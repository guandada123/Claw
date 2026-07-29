"""
wind_quote.py — Wind 万得行情获取（共享模块）

腾讯实时优先 → Wind 降级（盘中监控防 Wind MATCH 滞后）。所有 .workbuddy/scripts/ 脚本通过此模块统一获取行情。

用法:
    from wind_quote import fetch_quotes, fetch_wind_kline, fetch_wind_price
    quotes = fetch_quotes(["600900", "000333"])
    # → {"600900": {...}, "000333": {...}, "_source": "wind"/"tencent"}
    price = fetch_wind_price("600519")
    # → {"price": 1308.0, "change_pct": -1.47, ...}
    df = fetch_wind_kline("600519", days=200)
    # → DataFrame[date, open, close, high, low, volume]

与 src/claw/feeds/wind_utils.py 的关系：
  - wind_quote.py 是独立的旧版共享模块，供 .workbuddy/scripts/ 脚本使用
  - wind_utils.py 是 claw 包的统一工具模块
  - 日限额优先共享 wind_utils 的计数器（两者走同一池），不可用时退本地计数器
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

WIND_CLI = Path.home() / ".agents" / "skills" / "wind-mcp-skill" / "scripts" / "cli.mjs"
QT_URL = "https://qt.gtimg.cn/q={}"

# 每日查询上限（保护积分，独立于 claw 包的 wind_utils）
_DAILY_LIMIT = 100


class _DailyQueryCounter:
    """线程安全日限额计数器（本地兜底，共享 claw 包计数器不可用时启用）"""

    def __init__(self, limit: int = _DAILY_LIMIT) -> None:
        self._lock = threading.Lock()
        self._limit = limit
        self._count = 0
        self._date = ""

    def check(self) -> bool:
        with self._lock:
            today = time.strftime("%Y%m%d")
            if self._date != today:
                self._count = 0
                self._date = today
            if self._count >= self._limit:
                return False
            self._count += 1
            return True

    def stats(self) -> dict:
        with self._lock:
            today = time.strftime("%Y%m%d")
            used = self._count if self._date == today else 0
        return {
            "limit": self._limit,
            "used": used,
            "remaining": self._limit - used,
            "date": today,
        }


_local_counter = _DailyQueryCounter()

# Wind 指数/市场代码别名（来自 normalization-rules.json）
INDEX_ALIASES: dict[str, str] = {
    "DJI": "DJI.GI", "SPX": "SPX.GI", "IXIC": "IXIC.GI",
    "NDX": "NDX.GI", "SOX": "SOX.GI", "HSI": "HSI.HI",
    "HSTECH": "HSTECH.HI",
}


# 每日查询上限 — 优先共享 claw 包的 wind_utils 计数器，不可用时退本地
try:
    from claw.feeds.wind_utils import _check_query_limit as _shared_limit
    _shared_counter = True
except ImportError:
    _shared_counter = False


def _check_limit() -> bool:
    """线程安全日限额检查（优先共享 claw 包计数器）"""
    if _shared_counter:
        return _shared_limit()
    return _local_counter.check()


def get_query_stats() -> dict:
    """查询今日统计 {limit, used, remaining, date}（线程安全）"""
    if _shared_counter:
        try:
            from claw.feeds.wind_utils import get_query_stats as _shared_qs
            return _shared_qs()
        except Exception:
            logger.warning("共享计数器查询失败，退本地计数器", exc_info=True)
    return _local_counter.stats()


def _wind_code(code: str, market: str = "cn") -> str:
    """归一化为 Wind 代码格式

    Args:
        code: 原始代码（6位数字 / 美股ticker / 指数代码）
        market: "cn"(A股) | "us"(美股) | "index"(指数)

    示例:
        _wind_code("600519")      → "600519.SH"
        _wind_code("000333")      → "000333.SZ"
        _wind_code("AAPL", "us") → "AAPL.O"
        _wind_code("DJI", "index") → "DJI.GI"
    """
    code = code.strip()
    if market == "index":
        return INDEX_ALIASES.get(code, code)
    if market == "cn":
        if code.startswith(("6", "5")):
            return f"{code}.SH"
        if code.startswith(("0", "3")):
            return f"{code}.SZ"
        return code
    if market == "us":
        if "." in code:  # 已有后缀（.O/.N/.A）直通
            return code
        return f"{code}.O"  # 默认纳斯达克
    return code


def _tencent_code(code: str) -> str:
    code = code.strip()
    if code.startswith(("6", "5")):
        return f"sh{code}"
    if code.startswith(("0", "3")):
        return f"sz{code}"
    return code


def _to_float(v) -> float | None:
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def wind_available() -> bool:
    """Wind CLI 可用且未超日限额"""
    return WIND_CLI.exists() and _check_limit()


def fetch_wind_quote(code: str) -> dict | None:
    """单只 via Wind CLI，失败返回 None。补涨跌幅 from get_stock_price_indicators。"""
    wcode = _wind_code(code)
    try:
        r = subprocess.run(
            ["node", str(WIND_CLI), "call", "stock_data", "get_stock_quote",
             json.dumps({"windcode": wcode}, ensure_ascii=False)],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            return None
        payload = json.loads(r.stdout)
        text_payload = payload.get("content", [{}])[0].get("text", "")
        if not text_payload:
            return None
        data = json.loads(text_payload)
        rows = data.get("data", {}).get("rows", [])
        if not rows:
            return None
        row = rows[0]
        # Wind 行情: TIME, OPEN, MATCH(=现价), HIGH, LOW, TURNOVER(=成交额), VOLUME(=成交量), CHANGEHANDRATE(=换手率), AVPRICE
        result: dict[str, Any] = {
            "name": "",          # 实时行情不返回名称
            "code": code,
            "price": _to_float(row[2]),
            "prev_close": None,
            "open": _to_float(row[1]),
            "change": None,
            "change_pct": None,
            "high": _to_float(row[3]) if len(row) > 3 else None,
            "low": _to_float(row[4]) if len(row) > 4 else None,
            "volume": int(row[6]) if len(row) > 6 and row[6] else None,
            "amount": _to_float(row[5]) if len(row) > 5 else None,
            "turnover": _to_float(row[7]) if len(row) > 7 else None,
        }

        # 补涨跌幅：get_stock_price_indicators
        pi = _fetch_wind_price_indicators(wcode)
        if pi and pi.get("change_pct") is not None:
            result["change_pct"] = pi["change_pct"]
        return result
    except Exception:
        return None


def _fetch_wind_price_indicators(wcode: str) -> dict | None:
    """内部：调 get_stock_price_indicators 获取涨跌幅（不占日限额，嵌入 fetch_wind_quote 共用一次检查）"""
    try:
        r = subprocess.run(
            ["node", str(WIND_CLI), "call", "stock_data", "get_stock_price_indicators",
             json.dumps({"windcode": wcode, "indexes": "最新成交价,涨跌幅"}, ensure_ascii=False)],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return None
        payload = json.loads(r.stdout)
        text_payload = payload.get("content", [{}])[0].get("text", "")
        if not text_payload:
            return None
        data = json.loads(text_payload)
        rows = data.get("data", {}).get("rows", [])
        if not rows or not rows[0]:
            return None
        row = rows[0]
        return {"price": _to_float(row[0]), "change_pct": _to_float(row[1])}
    except Exception:
        return None


def fetch_wind_price(code: str) -> dict | None:
    """获取含涨跌幅的行情：腾讯实时优先 → Wind 降级

    ⚠️ DO NOT REVERT: 原版仅走 Wind get_stock_price_indicators，盘中可能滞后
    （同 fetch_quotes 的 Wind 优先问题）。现改为腾讯 qt.gtimg.cn 实时优先，失败
    再退 Wind。返回字段保持兼容：{"price", "change_pct", "code"}。

    Args:
        code: 裸 6 位代码或 Wind 标准码

    Returns:
        {"price": float, "change_pct": float, "code": str} 或 None
    """
    # 腾讯实时优先
    tc = fetch_tencent_quote([code])
    if tc and code in tc:
        q = tc[code]
        if q.get("price") is not None:
            return {
                "code": code,
                "price": q["price"],
                "change_pct": q.get("change_pct"),
            }

    # Wind 降级
    wcode = _wind_code(code)
    MAX_RETRIES = 2
    for _attempt in range(MAX_RETRIES):
        try:
            r = subprocess.run(
                ["node", str(WIND_CLI), "call", "stock_data", "get_stock_price_indicators",
                 json.dumps({"windcode": wcode, "indexes": "最新成交价,涨跌幅"}, ensure_ascii=False)],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode != 0:
                return None
            payload = json.loads(r.stdout)
            text_payload = payload.get("content", [{}])[0].get("text", "")
            if not text_payload:
                return None
            data = json.loads(text_payload)
            rows = data.get("data", {}).get("rows", [])
            if not rows or not rows[0]:
                return None
            row = rows[0]
            return {
                "code": code,
                "price": _to_float(row[0]),
                "change_pct": _to_float(row[1]),
            }
        except Exception as e:
            logger.warning("Wind 价格查询第 %d 次失败: %s", _attempt + 1, e)
            continue
    return None


def _parse_tencent(line: str) -> dict | None:
    if "=" not in line or line.startswith("pv_none_match"):
        return None
    try:
        vals = line.split('"')[1].split("~")
    except IndexError:
        return None
    if len(vals) < 40:
        return None
    return {
        "name": vals[1],
        "code": vals[2],
        "price": _to_float(vals[3]),
        "prev_close": _to_float(vals[4]),
        "open": _to_float(vals[5]),
        "volume": int(vals[6]) if vals[6].isdigit() else None,
        "change": _to_float(vals[31]),
        "change_pct": _to_float(vals[32]),
        "high": _to_float(vals[33]),
        "low": _to_float(vals[34]),
        "amount": _to_float(vals[37]),
        "turnover": _to_float(vals[38]),
    }


def fetch_tencent_quote(codes: list[str]) -> dict[str, dict]:
    """批量 via 腾讯"""
    if not codes:
        return {}
    t_codes = [_tencent_code(c) for c in codes]
    url = QT_URL.format(",".join(t_codes))
    try:
        req = urllib.request.Request(url)  # noqa: S310 # 硬编码可信 HTTPS 端点(QT_URL)，code 经 _tencent_code 净化，无 scheme 注入风险
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 # nosec B310 硬编码可信HTTPS端点(QT_URL)，code经_tencent_code净化无scheme注入风险
            text = resp.read().decode("gbk", errors="replace")
    except Exception as e:
        return {c: {"error": str(e)} for c in codes}
    results: dict[str, Any] = {}
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        q = _parse_tencent(line)
        if q and q.get("code"):
            results[q["code"]] = q
    return results


def fetch_quotes(codes: list[str]) -> dict[str, Any]:
    """获取行情：腾讯实时优先 → Wind 降级

    ⚠️ DO NOT REVERT: 旧版为 "Wind 优先 → 腾讯补缺"，但 Wind 的 MATCH 字段盘中
    可能滞后（实测华天 17.41 vs 腾讯 16.22，差 5.8%），且无交叉验证，导致监控类
    采用滞后价。腾讯 qt.gtimg.cn 为交易所实时推送（最准），必须优先。Wind 仅在
    腾讯缺失时兜底。改回 Wind 优先必再次引入滞后偏差。

    返回 {code: quote_dict, ..., "_source": "tencent"/"wind"/"tencent|wind"}
    """
    if not codes:
        return {"_source": "none"}

    results: dict[str, Any] = {}
    used_tencent = False

    # 腾讯实时优先（全量）
    tc = fetch_tencent_quote(codes)
    if tc:
        results.update(tc)
        used_tencent = True

    # Wind 补缺（腾讯未覆盖的标的）
    missing = [c for c in codes if c not in results]
    used_wind = False
    if missing and wind_available():
        for code in missing:
            q = fetch_wind_quote(code)
            if q and q.get("code"):
                results[q["code"]] = q
        if any(c in results for c in missing):
            used_wind = True

    if used_tencent and used_wind:
        results["_source"] = "tencent|wind"
    elif used_tencent:
        results["_source"] = "tencent"
    elif used_wind:
        results["_source"] = "wind"
    else:
        results["_source"] = "none"

    return results
