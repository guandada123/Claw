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
_DAILY_QUERY_LIMIT = 100
_query_lock = threading.Lock()
_daily_query_count = 0
_daily_query_date = ""


def _check_query_limit() -> bool:
    """检查是否超过每日查询上限（线程安全）"""
    global _daily_query_count, _daily_query_date
    with _query_lock:
        today = time.strftime("%Y%m%d")
        if _daily_query_date != today:
            _daily_query_count = 0
            _daily_query_date = today
        if _daily_query_count >= _DAILY_QUERY_LIMIT:
            logger.warning(
                f"Wind 每日查询上限已达 ({_DAILY_QUERY_LIMIT}次)，今日暂停"
            )
            return False
        _daily_query_count += 1
        return True


def get_query_stats() -> dict:
    """查询今日统计 {limit, used, remaining, date}（线程安全）"""
    with _query_lock:
        today = time.strftime("%Y%m%d")
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
        return rows

    # columns + rows 格式：zip 成 dict
    if columns:
        return [dict(zip(columns, row)) for row in rows]
    return None
