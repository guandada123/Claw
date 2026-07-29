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
    "password": os.environ["QTS_DB_PASS"],  # 必填，未设置时 KeyError 提前失败
}
TX_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param={sym},day,{start},{end},{limit}"
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


def fetch_tx_kline(ts_code: str, start: str, end: str, limit: int = 120) -> list[list]:
    """腾讯日K线。返回 [[日期,开,收,高,低,量(手)], ...]。"""
    if ts_code.endswith(".BJ"):
        return []  # 北交所腾讯K线格式不同，跳过(选股只用主板/中小板)
    market = "sh" if ts_code.endswith(".SH") else "sz"
    num = ts_code.split(".", 1)[0]
    sym = f"{market}{num}"
    url = TX_KLINE.format(sym=sym, start=start, end=end, limit=limit)
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
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
        amount = round(c * vol_shares, 2)     # 腾讯K线不含额，用 收×量 估算
        rows.append((ts_code, tdate, o, h, l, c, vol_shares, amount))
    return rows


def upsert_rows(rows: list[tuple]) -> int:
    """批量 UPSERT 到 daily_quote。返回写入行数。"""
    import psycopg2
    if not rows:
        return 0
    conn = psycopg2.connect(**DB_CFG, connect_timeout=8)
    n = 0
    try:
        cur = conn.cursor()
        for r in rows:
            cur.execute(
                """INSERT INTO daily_quote
                   (ts_code, trade_date, open, high, low, close, volume, amount, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (ts_code, trade_date) DO UPDATE SET
                     open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                     close=EXCLUDED.close, volume=EXCLUDED.volume, amount=EXCLUDED.amount""",
                r + (datetime.now(),))
            n += 1
        conn.commit()
    finally:
        conn.close()
    return n


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


def main():
    ap = argparse.ArgumentParser(description="QTS daily_quote 全市场日线回填(腾讯源)")
    ap.add_argument("--days", type=int, default=120, help="回填近 N 日 (默认120)")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 只(调试)")
    ap.add_argument("--workers", type=int, default=32, help="并发线程数(默认32)")
    ap.add_argument("--dry-run", action="store_true", help="不写库，只统计")
    args = ap.parse_args()

    codes = load_pool()
    if args.limit:
        codes = codes[:args.limit]
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=args.days)).isoformat()
    print(f"[INFO] 标的 {len(codes)} 只; 区间 {start}~{end}; workers={args.workers}; dry_run={args.dry_run}")

    t0 = time.time()
    total_written = 0
    total_klines = 0
    failed = []
    done = 0
    lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(process_one, code, start, end, args.dry_run): code
                   for code in codes}
        for fut in as_completed(futures):
            kc, wn, fstr = fut.result()
            with lock:
                total_klines += kc
                total_written += wn
                done += 1
                if fstr:
                    failed.append(fstr)
                if done % 200 == 0:
                    print(f"  [{done}/{len(codes)}] 已写 {total_written} 行, 失败 {len(failed)}, "
                          f"耗时 {time.time()-t0:.0f}s")

    elapsed = time.time() - t0
    print(f"\n[完成] 标的 {len(codes)} | K线 {total_klines} 根 | 写入 {total_written} 行 | "
          f"失败 {len(failed)} | 耗时 {elapsed:.0f}s ({elapsed/60:.1f}min)")
    if failed:
        print(f"[失败样例] {failed[:10]}")


if __name__ == "__main__":
    main()
