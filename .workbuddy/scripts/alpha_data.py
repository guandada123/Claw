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
import os
import sys
import time
from pathlib import Path

import pandas as pd


# qts_client 单源现位于 <Claw>/.workbuddy/scripts/（2026-09-23 双副本去重后 scripts/ 副本已退役）。
# 本模块仍留在 scripts/，故须显式把该目录加入 sys.path，否则 import 失败。
def _claw_root() -> Path:
    """位置无关地定位仓库根（向上找含 .workbuddy 的目录）。

    2026-10-12 P1-14：原写 `Path(__file__).resolve().parent.parent` ——
    该式**只在 scripts/ 下成立**；一旦搬到 .workbuddy/scripts/，parent.parent 会指到
    `.workbuddy/` 而算出 `.workbuddy/.workbuddy/…`（曾由此误建目录、被守卫抓到）。
    改为向上搜索后，两种位置都指向同一仓库根。
    """
    p = Path(__file__).resolve().parent
    for cand in (p, *p.parents):
        if (cand / ".workbuddy").is_dir():
            return cand
    raise RuntimeError("找不到含 .workbuddy 的仓库根（脚本被放在异常位置？）")


_HELPER_DIR = _claw_root() / ".workbuddy" / "scripts"
if str(_HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(_HELPER_DIR))

import qts_client  # noqa: E402 - 服务直连唯一入口(2026-08-13 打通)

# 除权跳变阈值: 单日 pct_change 绝对值超过此值(排除涨跌停极限)视为未复权跳空
GAP_PCT_THRESHOLD = 19.5
# 合规板块前缀(主板60/中小板00/创业板30), 排除科创688/北交8/4/920
ALLOWED_PREFIXES = ("60", "00", "30")
BLOCKED_PREFIXES = ("688", "689", "8", "4", "920")

# 本地缓存(因子引擎频繁读, 落盘避免每次全量 PG 往返)
CACHE_DIR = _claw_root() / ".workbuddy" / "data" / "alpha"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ── 缓存语义（2026-10-13 修复）────────────────────────────────────
# 原缺陷：cache_bars/universe 只要文件存在就无条件返回——**无 TTL**。
# 实测后果：bars_*.csv 停留在 2026-08-13/14，alpha_factors/alpha_eval 两个月的
# 因子计算与回测评估一直跑在两月前的 OHLCV 上（「缓存永不过期 → 下游静默
# 用旧数据」模式）。现改为：
#   · bars/universe 缓存带 TTL，过期先回源 PG 刷新，刷新失败才降级旧数据
#     **并打 WARNING**（降级可见，不静默）；
#   · sweep_cache 按保留期清扫超期 bars 与历史 eval 报告（进程内至多一次，
#     失败不影响主流程）——存量由后续运行自然消化，不做一次性手工删除。
BARS_CACHE_TTL_HOURS = float(os.environ.get("ALPHA_BARS_TTL_HOURS", "24"))
UNIVERSE_CACHE_TTL_HOURS = float(os.environ.get("ALPHA_UNIVERSE_TTL_HOURS", "168"))
BARS_KEEP_DAYS = float(os.environ.get("ALPHA_BARS_KEEP_DAYS", "14"))
EVAL_KEEP_DAYS = float(os.environ.get("ALPHA_EVAL_KEEP_DAYS", "90"))
_swept_once = False


def _read_sql(conn, sql: str, params: tuple | None = None) -> pd.DataFrame:
    """手动 cursor 读 PG（避免 pandas.read_sql 对 DBAPI2 的兼容警告）"""
    cur = conn.cursor()
    cur.execute(sql, params or ())
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    cur.close()
    return pd.DataFrame(rows, columns=cols)


def load_universe(min_days: int = 60) -> list[str]:
    """合规股票池 + 上市时长过滤。QTS 查询, 缓存 JSON（TTL 见 UNIVERSE_CACHE_TTL_HOURS）。"""
    cache = CACHE_DIR / "universe.json"
    if cache.exists():
        try:
            age_h = (time.time() - cache.stat().st_mtime) / 3600
        except OSError:
            age_h = float("inf")
        if age_h <= UNIVERSE_CACHE_TTL_HOURS:
            try:
                data = json.loads(cache.read_text(encoding="utf-8"))
                if data.get("min_days") == min_days:
                    return data["codes"]
            except (ValueError, KeyError):
                pass  # 缓存损坏 → 回源重建
    _sweep_cache_once()
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
        lambda r: (
            (r["close"] / r["pre_close"] - 1) * 100
            if pd.notna(r["pre_close"]) and r["pre_close"] > 0
            else 0.0
        ),
        axis=1,
    )
    df["gap"] = (df["pct"].abs() > GAP_PCT_THRESHOLD).astype(int)
    df["gap"] = df["gap"].where(df["pre_close"].notna(), 1)
    return df


def _read_bars_csv(csvf: Path) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(csvf, parse_dates=["date"], index_col="date")
        if len(df) > 0:
            return df
    except Exception:
        pass
    return None


def cache_bars(code: str) -> pd.DataFrame | None:
    """带本地 CSV 缓存(最近N根), 避免重复 PG 查询。

    2026-10-13 修复：原实现「文件存在即返回」无 TTL —— 缓存永远新鲜，
    实测 bars 停更在 08-13/14 两月无人知。现语义：
      1) 缓存存在且未过 TTL → 直接返回；
      2) 过期/损坏 → 回源 PG 刷新并覆写；
      3) PG 刷新失败 → 降级返回旧缓存，但**打 WARNING**（降级可见）。
    """
    csvf = CACHE_DIR / f"bars_{code}.csv"
    if csvf.exists():
        try:
            age_h = (time.time() - csvf.stat().st_mtime) / 3600
        except OSError:
            age_h = float("inf")
        if age_h <= BARS_CACHE_TTL_HOURS:
            df = _read_bars_csv(csvf)
            if df is not None:
                return df
    df = load_bars(code)
    if df is not None and not df.empty:
        df.to_csv(csvf)
        return df
    # PG 不可用/无数据 → 有旧缓存则降级（显式告警，不静默用旧数据）
    if csvf.exists():
        df = _read_bars_csv(csvf)
        if df is not None:
            print(
                f"[alpha_data] WARNING {code}: PG 刷新失败，降级使用 {age_h:.0f}h 前的旧缓存",
                file=sys.stderr,
            )
            return df
    return None


def _sweep_cache_once() -> None:
    """进程内至多执行一次保留期清扫（失败不影响主流程）。"""
    global _swept_once
    if _swept_once:
        return
    _swept_once = True
    try:
        r = sweep_cache()
        # 可观测性（学习 2026-10-13-bulk-delete-trips-safe-delete-guard 教训 #2）：
        # 正常路径也必须输出汇总，否则「删了 0 个」与「被护栏拦下」无法区分。
        print(
            f"[alpha_data] 缓存清扫: bars 删 {r['removed_bars']} / 失败 {r['failed']} "
            f"/ eval 删 {r['removed_eval']} / 释放 {r['freed_bytes'] / 1048576:.1f} MB"
            + (f" / 超期未清 {r['deferred']} 个（分片限流，下次运行继续）" if r["deferred"] else "")
        )
    except Exception as e:  # noqa: BLE001 — 清扫失败不该让调用方失败
        print(f"[alpha_data] 缓存清扫跳过: {e}", file=sys.stderr)


def sweep_cache(
    bars_keep_days: float | None = None,
    eval_keep_days: float | None = None,
    batch_size: int = 25,
) -> dict[str, int]:
    """按保留期清扫 alpha 缓存目录（2026-10-13 新增；同日晚按护栏约束改分片）。

    - bars_*.csv：mtime 超过 bars_keep_days（默认 14 天）→ 删。
      被 TTL 刷新过的文件 mtime 会更新，故「超期」= 长期未被访问，
      删除后按需回源重建，不丢活跃数据。
    - alpha_eval_*.json：超过 eval_keep_days（默认 90 天）→ 删（历史评估报告，
      保留足够做趋势分析）。
    其余文件（universe.json / alpha_factors_kept 等）一律不动。

    ⚠️ 分片限流（本仓 safe-delete 护栏：一次超 50 个文件删除会被拦并中断整个
    进程，见 .learnings/2026-10-13-bulk-delete-trips-safe-delete-guard）：
    单次调用**总删除量封顶 batch_size（默认 25，护栏阈值的 1/2）**，超出部分
    计入 deferred 由后续运行分次消化——宁可慢，不可整链中断。
    返回 {removed_bars, removed_eval, freed_bytes, failed, deferred}。
    """
    bkd = BARS_KEEP_DAYS if bars_keep_days is None else bars_keep_days
    ekd = EVAL_KEEP_DAYS if eval_keep_days is None else eval_keep_days
    now = time.time()
    removed_bars = removed_eval = freed = failed = deferred = 0
    budget = max(int(batch_size), 0)

    for p in CACHE_DIR.glob("bars_*.csv"):
        try:
            if (now - p.stat().st_mtime) / 86400 <= bkd:
                continue
        except OSError:
            continue
        if budget <= 0:
            deferred += 1
            continue
        try:
            size = p.stat().st_size
            p.unlink()
            freed += size
            removed_bars += 1
            budget -= 1
        except OSError:
            failed += 1
    for p in CACHE_DIR.glob("alpha_eval_*.json"):
        try:
            if (now - p.stat().st_mtime) / 86400 <= ekd:
                continue
        except OSError:
            continue
        if budget <= 0:
            deferred += 1
            continue
        try:
            size = p.stat().st_size
            p.unlink()
            freed += size
            removed_eval += 1
            budget -= 1
        except OSError:
            failed += 1
    return {
        "removed_bars": removed_bars,
        "removed_eval": removed_eval,
        "freed_bytes": freed,
        "failed": failed,
        "deferred": deferred,
    }


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
    elif cmd == "sweep":
        # 手动执行保留期清扫（自动路径：load_universe 每进程至多触发一次）
        # 可选参数 = batch_size（默认 25，受 safe-delete 护栏约束）：`alpha_data.py sweep 100`
        bs = int(sys.argv[2]) if len(sys.argv) > 2 else 25
        r = sweep_cache(batch_size=bs)
        print(
            f"清扫完成: bars 删 {r['removed_bars']} 个 / eval 删 {r['removed_eval']} 个 "
            f"/ 失败 {r['failed']} / 释放 {r['freed_bytes'] / 1048576:.1f} MB"
            + (f" / 超期未清 {r['deferred']} 个（下次继续）" if r["deferred"] else "")
        )
