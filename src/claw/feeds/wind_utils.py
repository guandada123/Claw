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
# 2026-09-01 解 Wind 日限（用户要求）：
#   1) 上限由 180 抬至 200，对齐真实配额天花板（200 次简单查询≈1000 积分，无超额风险）。
#      2026-09-23 用户要求再抬至 1000（"计数器调到1000"）；env CLAW_WIND_DAILY_LIMIT 可继续覆盖。
#      2026-09-29 用户要求「取消 WIND 限制」→ **默认改为 0 = 不限制**（只计数、不再拦）。
#        背景：05:00 与 15:00 两条「信号溯源」自动化**共用同一份日计数**，05:00 跑完把 1000 用满
#        → 15:00 的 STEP1 恒被拦（日报连多日「Wind每日1000次上限达→验证报告未刷新」）。
#      ⚠️ 外部配额不由本闸控制：Wind 侧仍是「1000 积分/天」，真撞上时由 Wind 返回错误 →
#        日志会显示调用失败；**先排查是不是积分用尽，别误判成脚本 bug**。
#      需要恢复硬闸：`export CLAW_WIND_DAILY_LIMIT=<次数>`（>0 即生效，可不改代码回滚）。
#   2) 新增「同日内查询缓存」——相同 (server,tool,params) 直接返回缓存，不打网络、不计数，
#      真正释放有效吞吐（根治 signal_verify 逐股重复查 Wind 的浪费），而非单纯抬高数字。
#   3) 支持环境变量 CLAW_WIND_DAILY_LIMIT 覆盖（用户升级 Wind 套餐后可调高）。
_DAILY_QUERY_LIMIT = int(os.environ.get("CLAW_WIND_DAILY_LIMIT", "0"))  # 0 = 不限制（2026-09-29 用户要求取消）
_query_lock = threading.Lock()
_limit_warned = False  # 进程内去重：日限警告仅打印一次，避免 signal_verify 逐股循环刷屏（08-24 修复 25 天刷屏）

# ── 持久化计数器（跨进程累加，修复"日报永远0"测量bug）──
# ⚠️ DO NOT REVERT: 原 _daily_query_count 是纯内存变量，进程退出即归零，
# 导致 wind_quota_report.py 每次新进程读到0、日报失真。改为落盘 JSON 跨进程共享。
_WIND_COUNT_FILE = os.path.expanduser("~/.workbuddy/wind_query_count.json")

# ── 同日内查询缓存（2026-09-01 解日限核心）──
# 键 = 日期|server|tool|params_json；同进程内相同查询直接命中，绕过网络与计数器。
# 这是「解日限」的真正杠杆：把浪费在重复查询上的额度释放给真正的新查询。
_wind_cache: dict[str, Any] = {}
_wind_cache_date = ""


def _load_state() -> dict:
    """读取持久化计数状态 {date, count, fail}（文件损坏/缺失返回空态）

    `count` = 今日**尝试**次数（含失败）；`fail` = 其中失败次数（2026-09-29 加，见 _note_failure）。
    ⚠️ `count` 不是"用量"：它在调用前自增、失败也算，别拿它当 Wind 侧配额消耗。
    """
    try:
        with open(_WIND_COUNT_FILE) as f:
            d = json.load(f)
        return {
            "date": str(d.get("date", "")),
            "count": int(d.get("count", 0)),
            "fail": int(d.get("fail", 0)),
        }
    except (OSError, ValueError, json.JSONDecodeError):
        return {"date": "", "count": 0, "fail": 0}


def _save_state(state: dict) -> None:
    """原子写入持久化状态（先写临时文件再 rename，避免半写损坏）"""
    tmp = _WIND_COUNT_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, _WIND_COUNT_FILE)
    except OSError as e:
        logger.warning(f"Wind 计数器持久化失败(不影响本次调用): {e}")


_failure_warned: set[str] = set()  # 进程内去重：同类失败只 WARNING 一次，避免逐股刷屏


def _note_failure(code: str, message: str) -> None:
    """记录一次失败尝试，并把 **Wind 的真实原因**打到 WARNING（2026-09-29 加）

    背景（本次实测）：Wind CLI 用 `{"ok": false, "code": "backend_error",
    "message": "账户积分余额不足，无法完成当前操作…"}` 表达业务失败，而原实现只看
    `isError`/`content` → 真实原因被**静默成 None**，日报里只剩我们自己的"日限已达" →
    把根因误导成阈值/脚本问题（用户据此来要求取消日限，其实那天**账户积分已经用尽**）。
    """
    key = f"{time.strftime('%Y%m%d')}|{code}"
    if key not in _failure_warned:
        logger.warning(f"Wind 调用失败 [{code}]: {message[:200]}")
        _failure_warned.add(key)
    with _query_lock:
        st = _load_state()
        st["fail"] = st.get("fail", 0) + 1
        _save_state(st)


def _check_query_limit() -> bool:
    """检查是否超过每日查询上限（跨进程线程安全，落盘累加）

    已知局限（审计 🟡4）：load→incr→save 非跨进程原子，并发时偏差 ≤ N_concurrent-1。
    threading.Lock 仅进程内有效。Claw 自动化串行执行，实际偏差可忽略。
    """
    global _limit_warned
    with _query_lock:
        today = time.strftime("%Y%m%d")
        st = _load_state()
        if st["date"] != today:
            st = {"date": today, "count": 0, "fail": 0}
            _limit_warned = False  # 跨天重置去重标志
        # _DAILY_QUERY_LIMIT <= 0 表示「不限制」（2026-09-29 用户要求取消）→ 只计数、不拦
        if _DAILY_QUERY_LIMIT > 0 and st["count"] >= _DAILY_QUERY_LIMIT:
            if not _limit_warned:
                logger.warning(
                    f"Wind 每日查询上限已达 ({_DAILY_QUERY_LIMIT}次)，今日暂停"
                )
                _limit_warned = True
            return False
        st["count"] += 1
        _save_state(st)
        return True


def get_query_stats() -> dict:
    """查询今日统计 {limit, used, fail, remaining, unlimited, date}（读落盘值）

    `limit <= 0` 时 `unlimited=True`、`remaining=None`（2026-09-29 取消日限后仍是**只读观测**：
    次数照样累计、照样上报，取消的是「拦截」不是「观测」）。
    `used` = 尝试次数（含失败），`fail` = 其中失败数 —— 两者一起看才知道"到底通没通"。
    """
    with _query_lock:
        today = time.strftime("%Y%m%d")
        st = _load_state()
        same_day = st["date"] == today
        used = st["count"] if same_day else 0
        fail = st["fail"] if same_day else 0
    unlimited = _DAILY_QUERY_LIMIT <= 0
    return {
        "limit": _DAILY_QUERY_LIMIT,
        "used": used,
        "fail": fail,
        "remaining": None if unlimited else _DAILY_QUERY_LIMIT - used,
        "unlimited": unlimited,
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

    2026-09-01 起：相同查询走同日内缓存，不打网络、不计入日限计数器。
    """
    today = time.strftime("%Y%m%d")
    params_json = json.dumps(params, ensure_ascii=False)

    # ── 同日内缓存命中：直接返回，不计网络不计计数器（解日限核心）──
    # 注：_wind_cache 仅做原地 .clear() 变更（不重新绑定），无需 global 声明；
    #     仅 _wind_cache_date 会被重新赋值，故只需声明它（避免 PLW0602）。
    global _wind_cache_date
    if _wind_cache_date != today:
        _wind_cache.clear()
        _wind_cache_date = today
    cache_key = f"{today}|{server_type}|{tool_name}|{params_json}"
    if cache_key in _wind_cache:
        return _wind_cache[cache_key]

    if not _check_query_limit():
        return None
    if not os.path.exists(WIND_CLI_PATH):
        logger.debug("Wind CLI 不可用: 未安装 wind-mcp-skill")
        return None

    try:
        result = subprocess.run(
            ["node", WIND_CLI_PATH, "call", server_type, tool_name, params_json],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=WIND_SKILL_DIR,
        )
        if result.returncode != 0:
            # ⚠️ 实测（2026-09-29）：Wind CLI 会把**业务错误**写在 stdout
            # （`{"ok": false, "code": "backend_error", "message": "账户积分余额不足…"}`）
            # 并以非 0 退出 → 只看 stderr 会得到空消息、把根因丢掉。故这里也解析 stdout。
            code = f"exit{result.returncode}"
            message = (result.stderr or "").strip()
            try:
                j = json.loads(result.stdout)
                if isinstance(j, dict) and j.get("ok") is False:
                    code = str(j.get("code") or code)
                    message = str(j.get("message") or message)
            except (ValueError, TypeError):
                pass
            logger.debug(
                f"Wind CLI[{server_type}.{tool_name}] 退出码 {result.returncode}"
            )
            _note_failure(code, message or (result.stdout or "")[:200])
            return None

        out = json.loads(result.stdout)
        # 2026-09-29：CLI 的业务失败形如 {"ok": false, "code": "backend_error",
        # "message": "账户积分余额不足…"} —— 必须把 code/message 带出来，否则根因被静默
        if out.get("ok") is False or out.get("isError"):
            _note_failure(
                str(out.get("code") or "isError"), str(out.get("message") or "")
            )
            return None

        text = out.get("content", [{}])[0].get("text", "")
        if not text:
            _note_failure("empty_content", json.dumps(out, ensure_ascii=False)[:200])
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
            ret = {"columns": [], "rows": flat_rows}
            _wind_cache[cache_key] = ret
            return ret

        # analytics_data 嵌套 data.data
        if "data" in inner and isinstance(inner["data"], list):
            inner = inner["data"][0] if inner["data"] else {}

        # 文档/新闻: {items: [...]}
        if "items" in inner:
            ret = {"columns": [], "rows": inner["items"]}
            _wind_cache[cache_key] = ret
            return ret

        # 标准表格: {columns, rows}
        ret = {"columns": [c["name"] for c in inner.get("columns", [])], "rows": inner.get("rows", [])}
        _wind_cache[cache_key] = ret
        return ret

    except json.JSONDecodeError as e:
        logger.warning(f"Wind CLI JSON 解析失败: {e}", exc_info=True)
        _note_failure("json_decode", str(e))
    except FileNotFoundError:
        logger.debug("Wind CLI 不可用: node 未找到")
        _note_failure("node_missing", WIND_CLI_PATH)
    except subprocess.TimeoutExpired:
        logger.debug(f"Wind CLI[{server_type}.{tool_name}] 超时")
        _note_failure("timeout", f"{server_type}.{tool_name} > {timeout}s")
    except Exception as e:
        logger.warning(f"Wind CLI[{server_type}.{tool_name}] 异常: {e}", exc_info=True)
        _note_failure(type(e).__name__, str(e))
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
