#!/usr/bin/env python3
"""
qts_client.py — Claw ↔ QTS 服务层统一客户端（2026-08-13 打通）

背景: 用户决策「Claw 与 QTS 打通，不再区分」——废除预生成 JSON 桥接
(/tmp/qts_daily_brief.json) 与 docker exec 容器注入(pull_qts_signals)，
统一走服务直连。铁律升级: 禁互相 import 代码，允许服务直连/只读 PG。

能力（覆盖 QTS 全部能力载体）:
  1. PG 只读(15432) — daily_quote全市场日线 / backtest_reports回测日报 /
     trading_signal信号 / stock_pool股票池 / positions持仓 / orders订单
  2. HTTP API(8000) — /api/v1/stocks|signals|backtest|account|execution
     （AUTH_ENABLED=false 时免认证；配 API_KEYS 时走 X-API-Key）
  3. 健康检查 + 降级: QTS 不可用时各方法返回 None/空，调用方降级腾讯/新浪

设计:
  - 单一连接入口, 全局懒连接, 失败自动重连
  - 只读: 所有查询不写 PG（铁律: Claw 只消费, 不写 QTS 数据）
  - 超时/重试: PG 8s 超时, API 5s 超时, 指数退避重试 2 次

用法:
  python3 scripts/qts_client.py health              # QTS 健康检查
  python3 scripts/qts_client.py kline 600584       # 日线(最近N根)
  python3 scripts/qts_client.py signals            # 最新交易信号
  python3 scripts/qts_client.py report             # 最新回测日报
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any

import psycopg2

# ── .env 加载（与 secrets.py 同模式; 环境变量优先）────────────
_ENV: dict[str, str] = {}
_env_path = Path(__file__).resolve().parent.parent / ".env"
try:
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        _ENV[_k.strip()] = _v.strip().strip('"').strip("'")
except OSError:
    pass


def _cfg(key: str, default: str) -> str:
    return os.environ.get(key) or _ENV.get(key, default)


# ── QTS 连接配置（.env / 环境变量覆盖）────────────────────────
QTS_PG_HOST = _cfg("QTS_PG_HOST", "127.0.0.1")
QTS_PG_PORT = int(_cfg("QTS_PG_PORT", "15432"))
QTS_PG_USER = _cfg("QTS_PG_USER", "quant_user")
QTS_PG_PASS = _cfg("QTS_PG_PASS", "quant_pass")
QTS_PG_DB = _cfg("QTS_PG_DB", "quant_trading")
QTS_API_HOST = _cfg("QTS_API_HOST", "127.0.0.1")
QTS_API_PORT = int(_cfg("QTS_API_PORT", "8000"))
QTS_API_KEY = _cfg("QTS_API_KEY", "")  # 配了则带 X-API-Key

PG_TIMEOUT = 8
API_TIMEOUT = 5
RETRY = 2

_conn: Any = None


# ── PG 只读 ───────────────────────────────────────────────────


def _pg():
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg2.connect(
            host=QTS_PG_HOST, port=QTS_PG_PORT, user=QTS_PG_USER,
            password=QTS_PG_PASS, dbname=QTS_PG_DB, connect_timeout=PG_TIMEOUT,
        )
    return _conn


def _query(sql: str, params: tuple | None = None, max_rows: int | None = None):
    """只读查询, 失败重试后返回 None。禁写。"""
    for attempt in range(RETRY + 1):
        try:
            cur = _pg().cursor()
            cur.execute(sql, params or ())
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchall()
            if max_rows:
                rows = rows[:max_rows]
            cur.close()
            return [dict(zip(cols, r)) for r in rows]
        except Exception:  # noqa: BLE001
            global _conn
            _conn = None
            if attempt < RETRY:
                time.sleep(0.5 * (attempt + 1))
                continue
            return None


def health_pg() -> dict:
    t0 = time.time()
    try:
        cur = _pg().cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
        return {"pg": True, "latency_ms": round((time.time() - t0) * 1000, 1)}
    except Exception:  # noqa: BLE001
        return {"pg": False, "latency_ms": None}


def get_kline(code: str, limit: int = 200) -> list[dict] | None:
    """全市场日线(2023-01至今, QTS daily_quote)。code 为 6 位数字。"""
    ts_code = f"{code}.SH" if code.startswith("6") else f"{code}.SZ"
    return _query(
        """
        SELECT trade_date, open, high, low, close, pre_close, volume, amount
        FROM daily_quote WHERE ts_code = %s
        ORDER BY trade_date DESC LIMIT %s
        """,
        (ts_code, limit),
    )


def get_daily_report() -> dict | None:
    """最新回测日报(backtest_reports 末行, detail_content 已解析)。"""
    rows = _query(
        """
        SELECT report_type, report_date, detail_content
        FROM backtest_reports ORDER BY created_at DESC LIMIT 1
        """
    )
    if not rows:
        return None
    r = rows[0]
    detail = r.get("detail_content")
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except json.JSONDecodeError:
            pass
    return {
        "report_type": r.get("report_type"),
        "report_date": str(r.get("report_date")) if r.get("report_date") else None,
        "detail": detail,
    }


def get_daily_brief() -> dict | None:
    """最新日评摘要(qts_daily_brief 表, 2026-08-13 打通: QTS落库→Claw只读)。

    返回 {report_date, brief(dict)}；无数据返回 None。
    """
    rows = _query(
        """
        SELECT report_date, brief FROM qts_daily_brief
        ORDER BY created_at DESC LIMIT 1
        """
    )
    if not rows:
        return None
    r = rows[0]
    brief = r.get("brief")
    if isinstance(brief, str):
        try:
            brief = json.loads(brief)
        except json.JSONDecodeError:
            pass
    return {
        "report_date": str(r.get("report_date")) if r.get("report_date") else None,
        "brief": brief,
    }


def get_signals(limit: int = 50) -> list[dict] | None:
    """交易信号(trading_signal 表)。"""
    return _query(
        """
        SELECT ts_code, signal_type, direction, confidence, price, created_at
        FROM trading_signal ORDER BY created_at DESC LIMIT %s
        """,
        (limit,),
    )


def get_positions() -> list[dict] | None:
    """持仓(positions 表, 模拟盘)。"""
    return _query("SELECT * FROM positions ORDER BY created_at DESC LIMIT 100")


def get_stock_pool(limit: int = 5000) -> list[dict] | None:
    """股票池(stock_pool)。"""
    return _query(
        "SELECT ts_code, name, industry, list_date FROM stock_pool LIMIT %s", (limit,)
    )


# ── HTTP API（认证可用时）─────────────────────────────────────


def _api(path: str, timeout: int = API_TIMEOUT):
    url = f"http://{QTS_API_HOST}:{QTS_API_PORT}{path}"
    headers = {"User-Agent": "Claw-QTS-Client/1.0"}
    if QTS_API_KEY:
        headers["X-API-Key"] = QTS_API_KEY
    for attempt in range(RETRY + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
                return json.loads(resp.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            if attempt < RETRY:
                time.sleep(0.5 * (attempt + 1))
                continue
            return None


def health_api() -> dict:
    d = _api("/health")
    if d is None:
        return {"api": False}
    return {"api": True, "detail": d}


def api_stock_realtime(ts_code: str) -> dict | None:
    return _api(f"/api/v1/stocks/realtime/{ts_code}")


def api_stock_kline(ts_code: str, limit: int = 50) -> dict | None:
    return _api(f"/api/v1/stocks/{ts_code}/kline?limit={limit}")


def api_signals(limit: int = 50) -> dict | None:
    return _api(f"/api/v1/signals/?limit={limit}")


def api_backtest_status() -> dict | None:
    d = _api("/api/v1/backtest/status")
    if d is None:
        return None
    # QTS 返回 {success, data} 结构
    if "data" in d:
        return d["data"]
    return d


def api_account_summary() -> dict | None:
    return _api("/api/v1/account/summary")


# ── 健康总览 ──────────────────────────────────────────────────


def health() -> dict:
    return {"pg": health_pg(), "api": health_api()}


def main():
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "health"
    if cmd == "health":
        print(json.dumps(health(), ensure_ascii=False, indent=2))
    elif cmd == "kline":
        code = sys.argv[2] if len(sys.argv) > 2 else "600584"
        rows = get_kline(code, 5)
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
    elif cmd == "report":
        r = get_daily_report()
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str)[:2000])
    elif cmd == "brief":
        r = get_daily_brief()
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str)[:2000])
    elif cmd == "signals":
        rows = get_signals(10)
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
    elif cmd == "positions":
        rows = get_positions()
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str)[:1500])
    elif cmd == "pool":
        rows = get_stock_pool(5)
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
