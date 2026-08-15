#!/usr/bin/env python3
"""
alpha_data.py — Alpha101 数据访问层（复用 QTS PostgreSQL，不重复造轮子）

2026-08-13 打通: 统一走 qts_client 服务直连（唯一连接入口），
废除本模块独立 psycopg2 连接（单一配置源 QTS_PG_*）。

QTS daily_quote(quant-postgres:15432/quant_trading):
  ts_code / trade_date / open / high / low / close / pre_close /
  change / pct_change / volume / amount / turnover_ratio / pe/pb/ps_ratio
  历史回填后覆盖 2023-01 至今全市场合规池(60/00/30, 排除688/8/4/920)

除权处理: 用 QTS 自带 pre_close → pct_change 突跳(>19.5%)标记 gap 日,
因子层剔除该日收益, 避免未复权数据的假信号。
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import qts_client  # noqa: E402 - 服务直连唯一入口(2026-08-13 打通)

# 除权跳变阈值: 单日 pct_change 绝对值超过此值(排除涨跌停极限)视为未复权跳空
GAP_PCT_THRESHOLD = 19.5
# 合规板块前缀(主板60/中小板00/创业板30), 排除科创688/北交8/4/920
ALLOWED_PREFIXES = ("60", "00", "30")
BLOCKED_PREFIXES = ("688", "689", "8", "4", "920")

# 本地缓存(因子引擎频繁读, 落盘避免每次全量 PG 往返)
CACHE_DIR = Path(__file__).resolve().parent.parent / ".workbuddy" / "data" / "alpha"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _read_sql(conn, sql: str, params: tuple | None = None) -> pd.DataFrame:
    """手动 cursor 读 PG（避免 pandas.read_sql 对 DBAPI2 的兼容警告）"""
    cur = conn.cursor()
    cur.execute(sql, params or ())
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    cur.close()
    return pd.DataFrame(rows, columns=cols)


def load_universe(min_days: int = 60) -> list[str]:
    """合规股票池 + 上市时长过滤。QTS 查询, 缓存 JSON。"""
    cache = CACHE_DIR / "universe.json"
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("min_days") == min_days:
            return data["codes"]
    with qts_client._pg() as conn:
        df = _read_sql(
            conn,
            "SELECT ts_code, COUNT(*) AS n FROM daily_quote GROUP BY ts_code",
        )
    codes = []
    for ts_code, n in df.itertuples(index=False):
        num = ts_code.split(".")[0]
        if num.startswith(BLOCKED_PREFIXES):
            continue
        if not num.startswith(ALLOWED_PREFIXES):
            continue
        if int(n) < min_days:
            continue
        codes.append(num)
    codes.sort()
    cache.write_text(
        json.dumps({"min_days": min_days, "codes": codes}, ensure_ascii=False),
        encoding="utf-8",
    )
    return codes


def load_bars(code: str) -> pd.DataFrame | None:
    """单只日线(OHLCV+amount+pre_close), 计算 gap 标记。返回 None 若无数据。

    列: date(索引) / open / high / low / close / volume / amount / gap
    """
    ts_code = f"{code}.SH" if code.startswith("6") else f"{code}.SZ"
    try:
        with qts_client._pg() as conn:
            df = _read_sql(
                conn,
                """
                SELECT trade_date, open, high, low, close, pre_close,
                       volume, amount
                FROM daily_quote
                WHERE ts_code = %s
                ORDER BY trade_date
                """,
                (ts_code,),
            )
    except Exception:  # noqa: BLE001 - QTS 不可用降级
        return None
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["trade_date"])
    df = df.set_index("date").sort_index()
    df = df[["open", "high", "low", "close", "pre_close", "volume", "amount"]]
    # pre_close 缺失(QTS 增量管道当日写入时常为 NULL) → 用前一日 close 回填
    df["pre_close"] = df["pre_close"].fillna(df["close"].shift(1))
    # gap: 涨跌幅超阈(>19.5% 排除涨跌停极限) → 未复权跳空；首日/无 pre_close 标 gap
    df["pct"] = df.apply(
        lambda r: (r["close"] / r["pre_close"] - 1) * 100
        if pd.notna(r["pre_close"]) and r["pre_close"] > 0
        else 0.0,
        axis=1,
    )
    df["gap"] = (df["pct"].abs() > GAP_PCT_THRESHOLD).astype(int)
    df["gap"] = df["gap"].where(df["pre_close"].notna(), 1)
    return df


def cache_bars(code: str) -> pd.DataFrame | None:
    """带本地 CSV 缓存(最近N根), 避免重复 PG 查询。"""
    csvf = CACHE_DIR / f"bars_{code}.csv"
    if csvf.exists():
        try:
            df = pd.read_csv(csvf, parse_dates=["date"], index_col="date")
            if len(df) > 0:
                return df
        except Exception:
            pass
    df = load_bars(code)
    if df is not None and not df.empty:
        df.to_csv(csvf)
    return df


if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "universe"
    if cmd == "universe":
        u = load_universe()
        print(f"合规池: {len(u)} 只 | 样例: {u[:5]} ... {u[-3:]}")
    elif cmd == "bars":
        code = sys.argv[2] if len(sys.argv) > 2 else "600584"
        df = cache_bars(code)
        if df is None:
            print(f"{code}: 无数据")
        else:
            print(f"{code}: {len(df)} 根 | {df.index[0].date()}~{df.index[-1].date()}")
            print("gap 日:", int(df["gap"].sum()))
            print(df.tail(3)[["close", "volume", "amount", "gap"]].to_string())
