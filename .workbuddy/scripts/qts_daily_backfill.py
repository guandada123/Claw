#!/usr/bin/env python3
"""QTS daily_quote 全市场日线回填（腾讯K线源，根治50只限制）

背景（2026-07-23 诊断）：
  QTS 的 daily_data_refresh 定时任务调 sync_daily_data()，默认只同步
  stock_pool 表前 50 只（fetch_symbols(limit=50)），全市场 3521 只其余标的
  从未被日常任务覆盖 → 停在历史全量导入的 2026-06，造成选股扫描"有效29只"失真。

本脚本用腾讯 K 线接口（免费、无限频）批量回填全市场日线，根治该缺陷：
  - 数据源：web.ifzq.gtimg.cn/appstock/app/kline/kline（实测全市场3521只~8.5min）
  - 写入：127.0.0.1:15432 的 quant_trading.daily_quote（UPSERT，幂等）
  - 不依赖 docker / tushare token，本地直接跑

运行：
  python3 .workbuddy/scripts/qts_daily_backfill.py            # 全量回填近120日
  python3 .workbuddy/scripts/qts_daily_backfill.py --days 30  # 只回填近30日
  python3 .workbuddy/scripts/qts_daily_backfill.py --limit 100 # 调试前100只
  python3 .workbuddy/scripts/qts_daily_backfill.py --dry-run  # 不写库，只统计
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

# ── 数据源 ──
# ⚠️ 密码通过环境变量注入，不硬编码在仓库中（修复审计 🔴1: 硬编码凭证泄露风险）
DB_CFG = {
    "host": os.environ.get("QTS_DB_HOST", "127.0.0.1"),
    "port": int(os.environ.get("QTS_DB_PORT", "15432")),
    "dbname": os.environ.get("QTS_DB_NAME", "quant_trading"),
    "user": os.environ.get("QTS_DB_USER", "quant_user"),
    # 08-05 加固：环境变量缺失时回退 docker-compose 默认(本地dev)，防 KeyError 整链路失败
    # （08-04 16:30 自动化启动失败致 daily_quote 缺 08-04 一整日，补跑时发现此脆弱点）
    "password": os.environ.get("QTS_DB_PASS", "quant_pass"),
}
TX_KLINE = (
    "https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param={sym},day,{start},{end},{limit}"
)
POOL_PATH = os.path.join(os.path.dirname(__file__), "mainboard_scan_pool.json")

# 全市场股票列表也可从 DB stock_pool 取；主板池仅1076只，不够全。
# 优先用 stock_pool 全量（3521只），回退到主板池。
DB_POOL_QUERY = "SELECT ts_code FROM stock_pool ORDER BY ts_code"


def fetch_stock_pool_from_db():
    """从 quant-postgres stock_pool 取全量标的（3521只）。"""
    import psycopg2

    try:
        conn = psycopg2.connect(**DB_CFG, connect_timeout=8)
        cur = conn.cursor()
        cur.execute(DB_POOL_QUERY)
        codes = [r[0] for r in cur.fetchall()]
        conn.close()
        return codes
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] stock_pool 读取失败({e})，回退主板池")
        return []


def load_pool():
    """加载标的列表：优先 DB 全量，回退主板池 json。"""
    codes = fetch_stock_pool_from_db()
    if not codes:
        with open(POOL_PATH, encoding="utf-8") as f:
            codes = list(json.load(f).keys())
    return codes


def tx_symbol(ts_code: str) -> str:
    """ts_code → 腾讯K线 symbol（sh/sz/bj 前缀）。

    2026-09-02 run#66 修复：本函数原先对 .BJ 直接 return [] 跳过。
    原注释「北交所腾讯K线格式不同」方向对、结论错 —— 实测腾讯确实支持北交所，
    前缀用 `bj` 即可，sh/sz/bj 返回结构完全一致（data.<sym>.day）。

    但北交所**有两个真实限制**（实测，勿再想当然）：
      ① **只返回最新 1 根日K**：无论请求区间多长（实测 2026-06-01~09-02），
         920xxx 恒返回 1 行（最新交易日）。故本源只能做**每日增量维护**，
         不能用于历史回填 —— 缺口需另找源。
      ② **旧代码段完全不支持**：bj430047 / bj830799 / bj872925 均 code=0 但 data 为空。
         仅 920xxx 新代码段可用（本次 stock_pool 322 只 .BJ 全为 920xxx，均覆盖）。

    原跳过的代价：322 只北交所标的被排除在腾讯源之外，只能依赖 QTS 15:10 的
    Tushare 刷新（run#60 实测成功率约 10%），覆盖率自 08-20 起逐日衰减，
    到 **2026-09-01 已归零**（daily_quote 当日起无任何 920*.BJ 数据）。
    """
    num, _, exch = ts_code.partition(".")
    return f"{exch.lower()}{num}"


def fetch_tx_kline(ts_code: str, start: str, end: str, limit: int = 120) -> list[list]:
    """腾讯日K线。返回 [[日期,开,收,高,低,量(手)], ...]。"""
    sym = tx_symbol(ts_code)
    url = TX_KLINE.format(sym=sym, start=start, end=end, limit=limit)
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
        )
        raw = urllib.request.urlopen(req, timeout=8).read().decode("utf-8")
        d = json.loads(raw)
        node = d.get("data", {}).get(sym, {})
        return node.get("day") or node.get("qfqday") or []
    except Exception:  # noqa: BLE001
        return []


def kline_to_rows(ts_code: str, klines: list[list]) -> list[tuple]:
    """腾讯K线 → daily_quote 行 (ts_code, trade_date, open, high, low, close, volume, amount)。"""
    rows = []
    for k in klines:
        # k: [日期, 开, 收, 高, 低, 量(手)]
        if len(k) < 6:
            continue
        tdate = k[0]
        o, c, h, l = float(k[1]), float(k[2]), float(k[3]), float(k[4])
        vol_shares = int(float(k[5]) * 100)  # 手 → 股
        amount = round(c * vol_shares, 2)  # 腾讯K线不含额，用 收×量 估算
        rows.append((ts_code, tdate, o, h, l, c, vol_shares, amount))
    return rows


def upsert_rows(rows: list[tuple]) -> int:
    """批量 UPSERT 到 daily_quote。返回写入行数。

    2026-09-04 优化：原实现**逐行** cur.execute —— 每条记录一次 round-trip，
    40 万行时单是网络往返就要 8~12min。这正是灌库任务被自动化超时杀掉、
    连续两天只写进 44/5044 只的根因（见 .learnings/2026-09-04-max-trade-date-...）。
    改 execute_values 批量提交后降到 1min 量级，从根本上消除超时风险。
    """
    import psycopg2
    from psycopg2.extras import execute_values

    if not rows:
        return 0
    now = datetime.now()
    conn = psycopg2.connect(**DB_CFG, connect_timeout=8)
    try:
        cur = conn.cursor()
        execute_values(
            cur,
            """INSERT INTO daily_quote
               (ts_code, trade_date, open, high, low, close, volume, amount, created_at)
               VALUES %s
               ON CONFLICT (ts_code, trade_date) DO UPDATE SET
                 open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                 close=EXCLUDED.close, volume=EXCLUDED.volume, amount=EXCLUDED.amount""",
            [r + (now,) for r in rows],
            page_size=1000,
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def process_one(code, start, end, dry_run):
    """单只处理：拉取→转行→upsert。返回 (kline_count, written, failed_str_or_None)。"""
    klines = fetch_tx_kline(code, start, end)
    if not klines:
        return (0, 0, code)
    rows = kline_to_rows(code, klines)
    if dry_run:
        return (len(rows), len(rows), None)
    try:
        n = upsert_rows(rows)
        return (len(rows), n, None)
    except Exception as e:  # noqa: BLE001
        return (len(rows), 0, f"{code}:{e}")


def verify_coverage(codes, min_ratio):
    """回填自检：最新交易日的覆盖率是否达标。返回 (ok, trade_date, got, expected)。

    2026-09-04 加。此前本脚本被自动化超时杀掉时只写进 44/5044 只（0.9%），
    连续 3 天无人发现，原因是**校验口径全部失效**：
      - 调用方（automation-1784811393302）校验用
        `MAX(trade_date)` + `COUNT(DISTINCT ts_code) WHERE trade_date >= 两月前`
        —— 前者截断日照样是"最新"，后者统计两个月的并集，44 只残缺日完全淹没在里面
      - 脚本自己只在结尾打一行汇总，被杀时连这行都不会打印
    → 结论：**自检必须放在写数据的一方，且必须用「当天的覆盖数」判，不能用 MAX 也不能用区间并集。**
    """
    import psycopg2

    expected = len(codes)
    try:
        conn = psycopg2.connect(**DB_CFG, connect_timeout=8)
        cur = conn.cursor()
        cur.execute("SELECT MAX(trade_date) FROM daily_quote")
        row = cur.fetchone()
        if not row or not row[0]:
            return (False, None, 0, expected)
        td = row[0]
        cur.execute("SELECT COUNT(*) FROM daily_quote WHERE trade_date = %s", (td,))
        got = cur.fetchone()[0]
        conn.close()
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 覆盖率自检失败: {e}")
        return (False, None, 0, expected)
    return (got >= expected * min_ratio, td, got, expected)


def main():
    ap = argparse.ArgumentParser(description="QTS daily_quote 全市场日线回填(腾讯源)")
    # ⚠️ 默认值 2026-09-04 由 120 改为 10。
    # 120 日 ≈ 80 个交易日 × 5044 只 ≈ 40 万行，逐行 INSERT + 每只新建连接 → 8~12min，
    # 在自动化调度里必然撞超时被杀（09-02 / 09-03 连续两天如此，每次只写进 44 只就死）。
    # 每日维护只需补最近缺口：10 个自然日 ≈ 7 个交易日 ≈ 34s，
    # 且自带"前几天失败、今天跑一次就能补回"的自愈窗口。
    # 需要加深历史深度时显式指定：--days 120（建议单独跑，不要挂在每日常任务上）。
    ap.add_argument("--days", type=int, default=10, help="回填近 N 日 (默认10)")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 只(调试)")
    ap.add_argument("--workers", type=int, default=32, help="并发线程数(默认32)")
    ap.add_argument("--dry-run", action="store_true", help="不写库，只统计")
    ap.add_argument(
        "--min-coverage",
        type=float,
        default=0.9,
        help="最新交易日覆盖率下限(默认0.9)，低于此值 exit 1",
    )
    args = ap.parse_args()

    codes = load_pool()
    if args.limit:
        codes = codes[: args.limit]
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=args.days)).isoformat()
    print(
        f"[INFO] 标的 {len(codes)} 只; 区间 {start}~{end}; workers={args.workers}; dry_run={args.dry_run}"
    )

    t0 = time.time()
    total_written = 0
    total_klines = 0
    failed = []
    done = 0
    lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(process_one, code, start, end, args.dry_run): code for code in codes}
        for fut in as_completed(futures):
            kc, wn, fstr = fut.result()
            with lock:
                total_klines += kc
                total_written += wn
                done += 1
                if fstr:
                    failed.append(fstr)
                if done % 200 == 0:
                    print(
                        f"  [{done}/{len(codes)}] 已写 {total_written} 行, 失败 {len(failed)}, "
                        f"耗时 {time.time() - t0:.0f}s"
                    )

    elapsed = time.time() - t0
    print(
        f"\n[完成] 标的 {len(codes)} | K线 {total_klines} 根 | 写入 {total_written} 行 | "
        f"失败 {len(failed)} | 耗时 {elapsed:.0f}s ({elapsed / 60:.1f}min)"
    )
    if failed:
        print(f"[失败样例] {failed[:10]}")

    # 自检：被超时杀掉时这一步不会执行，但**没被杀却覆盖不足**的情况必须拦下来。
    # 这是唯一能抓住"写了但只写了一部分"的时机 —— 调用方的校验口径看不出截断（见函数注释）。
    if args.dry_run:
        print("[自检] dry-run 模式，跳过覆盖率校验")
        return

    ok, td, got, exp = verify_coverage(codes, args.min_coverage)
    ratio = got / max(exp, 1)
    if not ok:
        print(
            f"\n[❌ 覆盖率不足] 最新交易日 {td}: {got}/{exp} = {ratio:.1%} "
            f"< 阈值 {args.min_coverage:.0%}"
        )
        print("   → 下游回测会静默跑在残缺数据上（09-02~09-04 事故：44/5044 = 0.9%，连错 3 天）")
        print("   → 请勿消费当日 qts_daily_brief，先修复本脚本的执行环境再重跑")
        sys.exit(1)
    print(f"[✅ 覆盖率达标] 最新交易日 {td}: {got}/{exp} = {ratio:.1%}")


if __name__ == "__main__":
    main()
