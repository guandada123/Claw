"""Wind 万得工具模块 — 共享的 CLI 路径、可用性检查、代码转换、统一 CLI 调用

所有 Wind 相关模块（data_sources / wind_analytics / wind_monitor）
统一从此处 import，避免重复定义。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# ── 路径常量 ──

WIND_CLI_PATH = os.path.expanduser(
    "~/.agents/skills/wind-mcp-skill/scripts/cli.mjs"
)
WIND_SKILL_DIR = os.path.dirname(os.path.dirname(WIND_CLI_PATH))
WIND_CONFIG_PATHS = [
    os.path.expanduser("~/.wind-aifinmarket/config"),
    os.path.expanduser("~/.agents/skills/wind-mcp-skill/config.json"),
]


# ── 可用性检查 ──

def wind_available() -> bool:
    """检查 Wind 数据源是否可用（CLI 文件存在 + API Key 已配置）"""
    if not os.path.exists(WIND_CLI_PATH):
        return False
    for p in WIND_CONFIG_PATHS:
        if os.path.exists(p):
            try:
                with open(p) as f:
                    content = f.read()
                if "WIND_API_KEY" in content or "wind_api_key" in content:
                    return True
            except OSError:
                continue
    return False


# ── 代码转换 ──

def plain_code_to_windcode(code: str) -> str:
    """裸 6 位代码转 Wind 标准代码

    沪市主板 6xxxxx → 600519.SH
    北交所   8xxxxx → 8xxxxx.BJ
    三板     4xxxxx → 4xxxxx.BJ
    深市/中小板/创业板 → 000001.SZ
    """
    code = code.strip()
    if code.startswith("6"):
        return f"{code}.SH"
    elif code.startswith(("8", "4")):
        return f"{code}.BJ"
    else:
        return f"{code}.SZ"


# ── 统一 CLI 调用（合并自 data_sources._call_wind_cli + wind_analytics._call_cli）──

# 每日查询上限（保护积分，1000 免费积分/天 ≈ 200 次简单查询 或 20 次分析查询）
# 2026-08-12 由 100 调至 180：用户确认 AIFin Market 真实配额为 1000 积分/天，
# 100 过于保守（按注释换算仅用半数），180 贴近 200 次简单查询的 90% 安全线。
_DAILY_QUERY_LIMIT = 180
_query_lock = threading.Lock()
_limit_warned = False  # 进程内去重：日限警告仅打印一次，避免 signal_verify 逐股循环刷屏（08-24 修复 25 天刷屏）

# ── 持久化计数器（跨进程累加，修复"日报永远0"测量bug）──
# ⚠️ DO NOT REVERT: 原 _daily_query_count 是纯内存变量，进程退出即归零，
# 导致 wind_quota_report.py 每次新进程读到0、日报失真。改为落盘 JSON 跨进程共享。
_WIND_COUNT_FILE = os.path.expanduser("~/.workbuddy/wind_query_count.json")


def _load_count() -> tuple[str, int]:
    """读取持久化计数 (date, count)，文件损坏/缺失返回 ('', 0)"""
    try:
        with open(_WIND_COUNT_FILE) as f:
            d = json.load(f)
        return str(d.get("date", "")), int(d.get("count", 0))
    except (OSError, ValueError, json.JSONDecodeError):
        return "", 0


def _save_count(date: str, count: int) -> None:
    """原子写入持久化计数（先写临时文件再 rename，避免半写损坏）"""
    tmp = _WIND_COUNT_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump({"date": date, "count": count}, f)
        os.replace(tmp, _WIND_COUNT_FILE)
    except OSError as e:
        logger.warning(f"Wind 计数器持久化失败(不影响本次调用): {e}")


def _check_query_limit() -> bool:
    """检查是否超过每日查询上限（跨进程线程安全，落盘累加）

    已知局限（审计 🟡4）：load→incr→save 非跨进程原子，并发时偏差 ≤ N_concurrent-1。
    threading.Lock 仅进程内有效。Claw 自动化串行执行，实际偏差可忽略。
    """
    global _limit_warned
    with _query_lock:
        today = time.strftime("%Y%m%d")
        _daily_query_date, _daily_query_count = _load_count()
        if _daily_query_date != today:
            _daily_query_count = 0
            _daily_query_date = today
            _limit_warned = False  # 跨天重置去重标志
        if _daily_query_count >= _DAILY_QUERY_LIMIT:
            if not _limit_warned:
                logger.warning(
                    f"Wind 每日查询上限已达 ({_DAILY_QUERY_LIMIT}次)，今日暂停"
                )
                _limit_warned = True
            return False
        _daily_query_count += 1
        _save_count(_daily_query_date, _daily_query_count)
        return True


def get_query_stats() -> dict:
    """查询今日统计 {limit, used, remaining, date}（跨进程线程安全，读落盘值）"""
    with _query_lock:
        today = time.strftime("%Y%m%d")
        _daily_query_date, _daily_query_count = _load_count()
        used = _daily_query_count if _daily_query_date == today else 0
    return {
        "limit": _DAILY_QUERY_LIMIT,
        "used": used,
        "remaining": _DAILY_QUERY_LIMIT - used,
        "date": today,
    }


def call_wind_cli(
    server_type: str,
    tool_name: str,
    params: dict,
    timeout: int = 15,
) -> dict | None:
    """调用 Wind CLI 并返回统一格式的 {columns, rows} 或 None

    支持 4 种后端返回格式：
    - 标准表格 {columns, rows}
    - 文档/新闻   {items}
    - EDB 宏数据 {code, data: [{meta, date, value}]}
    - analytics   {data: [{columns, rows}]} 嵌套
    """
    if not _check_query_limit():
        return None
    if not os.path.exists(WIND_CLI_PATH):
        logger.debug("Wind CLI 不可用: 未安装 wind-mcp-skill")
        return None

    params_json = json.dumps(params, ensure_ascii=False)
    try:
        result = subprocess.run(
            ["node", WIND_CLI_PATH, "call", server_type, tool_name, params_json],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=WIND_SKILL_DIR,
        )
        if result.returncode != 0:
            logger.debug(
                f"Wind CLI[{server_type}.{tool_name}] 退出码 {result.returncode}"
            )
            return None

        out = json.loads(result.stdout)
        if out.get("isError"):
            return None

        text = out.get("content", [{}])[0].get("text", "")
        if not text:
            return None

        parsed = json.loads(text)
        raw = parsed.get("data")
        if not raw:
            return None

        inner = raw if isinstance(raw, dict) else {}

        # EDB 宏数据: {code: 0, data: [{meta, date, value}]}
        if "code" in inner and isinstance(inner.get("data"), list):
            series_list = inner["data"]
            if not series_list:
                return None
            flat_rows = []
            for series in series_list:
                meta = series.get("meta", {})
                name = meta.get("name", "?")
                unit = meta.get("unit", "")
                dates = series.get("date", [])
                vals = series.get("value", [])
                if len(dates) != len(vals):
                    logger.warning(
                        f"EDB date/value 长度不一致: {len(dates)} vs {len(vals)}"
                    )
                for dt, val in zip(dates, vals):
                    flat_rows.append({
                        "指标": name,
                        "单位": unit,
                        "日期": dt[:10],
                        "值": val,
                    })
            return {"columns": [], "rows": flat_rows}

        # analytics_data 嵌套 data.data
        if "data" in inner and isinstance(inner["data"], list):
            inner = inner["data"][0] if inner["data"] else {}

        # 文档/新闻: {items: [...]}
        if "items" in inner:
            return {"columns": [], "rows": inner["items"]}

        # 标准表格: {columns, rows}
        return {"columns": [c["name"] for c in inner.get("columns", [])], "rows": inner.get("rows", [])}

    except json.JSONDecodeError as e:
        logger.warning(f"Wind CLI JSON 解析失败: {e}", exc_info=True)
    except FileNotFoundError:
        logger.debug("Wind CLI 不可用: node 未找到")
    except subprocess.TimeoutExpired:
        logger.debug(f"Wind CLI[{server_type}.{tool_name}] 超时")
    except Exception as e:
        logger.warning(f"Wind CLI[{server_type}.{tool_name}] 异常: {e}", exc_info=True)
    return None


def call_wind_cli_as_rows(
    server_type: str,
    tool_name: str,
    params: dict,
    timeout: int = 15,
) -> list[dict[str, Any]] | None:
    """调用 Wind CLI 并返回 list[dict]（每行一个 dict，items 格式直接返回）"""
    data = call_wind_cli(server_type, tool_name, params, timeout)
    if not data:
        return None

    rows = data["rows"]
    columns = data["columns"]

    # items 格式下的 row 已经是 dict
    if rows and isinstance(rows[0], dict):
        return rows  # type: ignore[no-any-return]

    # columns + rows 格式：zip 成 dict
    if columns:
        return [dict(zip(columns, row)) for row in rows]
    return None


# ── 高频便捷函数 ──

def get_wind_realtime_price(code: str) -> dict | None:
    """获取指定股票的实时行情（价格 + 涨跌幅）

    Args:
        code: 裸 6 位代码（如 "600519"）

    Returns:
        {"price": 1308.0, "change_pct": -1.47, "windcode": "600519.SH"} 或 None
    """
    if not wind_available():
        return None
    windcode = plain_code_to_windcode(code)
    rows = call_wind_cli_as_rows(
        "stock_data",
        "get_stock_price_indicators",
        {"windcode": windcode, "indexes": "最新成交价,涨跌幅"},
        timeout=10,
    )
    if not rows or not rows[0]:
        return None
    row = rows[0]
    try:
        price = None
        change_pct = None
        for k in row:
            kl = k.lower()
            if "成交价" in kl or "price" in kl or "最新" in kl:
                price = float(row[k]) if row[k] is not None else None
            elif "涨跌" in kl or "change" in kl:
                change_pct = float(row[k]) if row[k] is not None else None
        if price is not None or change_pct is not None:
            return {"price": price, "change_pct": change_pct, "windcode": windcode}
    except (ValueError, TypeError):
        pass
    return None


def get_wind_kline(
    code: str,
    days: int = 60,
    kline_type: str = "日K",
) -> list[dict] | None:
    """获取指定股票的历史 K 线数据

    Kline 列名: TIME, OPEN, MATCH(=收盘), HIGH, LOW, AMOUNT, VOL, PCT_CHG, PRE_CLOSE

    Args:
        code: 裸 6 位代码
        days: 回溯天数
        kline_type: K 线类型（日K/周K/月K）

    Returns:
        list[dict] 每行代表一根 K 线，或 None
    """
    if not wind_available():
        return None
    windcode = plain_code_to_windcode(code)
    end = time.strftime("%Y%m%d")
    # 窗口 = days*1.5 确保覆盖；上限60天（审计 🟡6: 原400天浪费>95%带宽）
    window = min(max(days * 2, 20), 60)
    start_ts = time.time() - window * 86400
    begin = time.strftime("%Y%m%d", time.localtime(start_ts))
    return call_wind_cli_as_rows(
        "stock_data",
        "get_stock_kline",
        {
            "windcode": windcode,
            "kline": kline_type,
            "begin_date": begin,
            "end_date": end,
        },
        timeout=15,
    )


def get_wind_ma(code: str, period: int = 20) -> float | None:
    """计算指定股票 Wind K 线的移动平均线（MA）

    Args:
        code: 裸 6 位代码
        period: 均线周期（默认 20 = MA20）

    Returns:
        MA 值（float），或 None
    """
    # 拉足够数据（period*2 确保够，最小 40）
    klines = get_wind_kline(code, days=max(period * 2, 40))
    if not klines or len(klines) < period:
        return None
    try:
        closes = []
        for k in klines:
            # MATCH = 收盘价
            v = k.get("MATCH") or k.get("match") or k.get("close") or k.get("收盘价")
            if v is not None:
                closes.append(float(v))
        if len(closes) < period:
            return None
        return sum(closes[-period:]) / period
    except (ValueError, TypeError):
        return None
