"""credit_meter.py — 官方积分读取单源（2026-10-10 建）

## 为什么单独一个模块
积分是本工作区**唯一真实的消耗单位**（token 是量，¥ 都是估算），而它至少被三处需要：
预算守护 `budget_guard`、三源对齐 `credit_vs_token`、巡检中枢的成本维度。
按本仓「单一真值源」纪律：**解析 credit_json 的代码只写一次**，
任何人不得再手写第二份（否则又会出现两套口径）。

## 数据源与口径
`~/.workbuddy/workbuddy.db` → `session_usage.credit_json`（官方计费）。
- 只读打开（`mode=ro`），**绝不写平台库**；
- `credit_json` 形如 `{"<uuid>": 12.5, ...}`，取其中数值成员求和（非数值成员忽略）；
- 只覆盖**实际扣积分**的会话（实测约 27%），因此：
  - 它回答「花了多少积分」→ 可信；
  - 它**不**回答「一共用了多少 token」→ 那要看 token 侧（平台日志）。
- 归属月份/日期按该会话的 `updated_at`（会话最后一次活动）划入，同一会话只计一次。
  ⚠️ 因此**日粒度会尖峰**（一个跨三天的会话把三天积分全落在结束那天），
  `total_for_day()` 只能当参考，**不要拿它做「日增量」告警**——要日粒度走 token 侧
  （请求级时间戳，见 `.workbuddy/reports/token-dashboard.html`）。月粒度不受影响。

实测倍率（2026-10-10，全历史）：**2.68 积分 / 百万 token**（详见
`.workbuddy/reports/credit-vs-token.md` 与 `.workbuddy/docs/consumption-ledger.md`）。
"""

from __future__ import annotations

import datetime
import json
import sqlite3
from pathlib import Path

DEFAULT_DB = Path.home() / ".workbuddy" / "workbuddy.db"

# 实测倍率：把 token 粗略折成积分时用它（唯一来源，勿在别处再写一个数字）
CREDITS_PER_MILLION_TOKENS = 2.68


class CreditReadError(RuntimeError):
    """平台库读不到 / 结构变了 —— 调用方必须显式处置，不许静默当 0。"""


def load_rows(db_path: Path | str | None = None) -> list[dict]:
    """读全部扣积分会话 → [{session_id, credits, calls, ts_ms}, ...]。

    失败一律抛 `CreditReadError`（**不返回空列表**：空与读不到是两件事）。
    """
    path = Path(db_path) if db_path else DEFAULT_DB
    if not path.is_file():
        raise CreditReadError(f"平台库不存在：{path}")
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as e:  # pragma: no cover - 环境相关
        raise CreditReadError(f"平台库打不开：{e}") from e
    try:
        raw = con.execute(
            "SELECT session_id, credit_json, updated_at FROM session_usage "
            "WHERE credit_json IS NOT NULL AND credit_json != ''"
        ).fetchall()
    except sqlite3.Error as e:
        raise CreditReadError(f"读 session_usage 失败：{e}") from e
    finally:
        con.close()

    rows: dict[str, dict] = {}
    for sid, cj, ts in raw:
        try:
            d = json.loads(cj)
            credits = sum(float(v) for v in d.values() if isinstance(v, (int, float)))
        except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
            continue
        key = str(sid)
        rec = rows.setdefault(key, {"session_id": key, "credits": 0.0, "calls": 0, "ts_ms": 0})
        rec["credits"] += credits
        rec["calls"] += len(d) if isinstance(d, dict) else 0
        rec["ts_ms"] = max(rec["ts_ms"], ts or 0)
    return list(rows.values())


def _key_of(ts_ms: int, fmt: str) -> str | None:
    if not ts_ms:
        return None
    return datetime.datetime.fromtimestamp(ts_ms / 1000).strftime(fmt)


def totals_by_month(rows: list[dict] | None = None) -> dict[str, float]:
    """{YYYY-MM: 积分}，按会话 updated_at 归属。"""
    out: dict[str, float] = {}
    for r in rows if rows is not None else load_rows():
        k = _key_of(r["ts_ms"], "%Y-%m")
        if k:
            out[k] = out.get(k, 0.0) + r["credits"]
    return dict(sorted(out.items()))


def total_for_month(month: str | None = None, rows: list[dict] | None = None) -> float:
    """指定月（YYYY-MM，默认本月）的积分合计。"""
    month = month or datetime.date.today().strftime("%Y-%m")
    return round(totals_by_month(rows).get(month, 0.0), 2)


def total_for_day(day: str | None = None, rows: list[dict] | None = None) -> float:
    """指定日（YYYY-MM-DD，默认今天）的积分合计。"""
    day = day or datetime.date.today().isoformat()
    tot = 0.0
    for r in rows if rows is not None else load_rows():
        if _key_of(r["ts_ms"], "%Y-%m-%d") == day:
            tot += r["credits"]
    return round(tot, 2)


if __name__ == "__main__":  # 手工核查用
    import sys

    try:
        _rows = load_rows()
    except CreditReadError as e:
        print(f"❌ {e}")
        sys.exit(2)
    print(f"覆盖 {len(_rows)} 个扣积分会话")
    for _k, _v in totals_by_month(_rows).items():
        print(f"  {_k}  {_v:>10,.2f} 积分")
