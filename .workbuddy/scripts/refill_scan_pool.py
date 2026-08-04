#!/usr/bin/env python3
"""refill_scan_pool.py — 增量补全选股池（覆盖所有允许板块，不止创业板）

用法:
    python3 refill_scan_pool.py            # 增量补全并打印统计
    python3 refill_scan_pool.py --dry       # 只统计不写盘
    python3 refill_scan_pool.py --no-color  # 关闭进度条(自动化日志更干净)

背景:
    07-29 放开创业板后，原 refill_cyb_pool.py 仅枚举 300000-301999，
    主板新上市标的(如深市 003xxx、沪市 605xxx 后续段)无法自动进池 → 池逐渐过时。
    本脚本泛化：覆盖所有「允许板块」代码区间，对新上市标的增量补全。

允许板块(对齐 sim_trade.py / MEMORY.md 铁律):
    - 深市主板: 000/001/002/003 .SZ
    - 沪市主板: 600/601/603/605 .SH
    - 创业板:   300/301 .SZ  (07-29 放开)
禁买板块(不枚举，避免污染池):
    - 科创板 688/689
    - 北交所 8/4

逻辑:
    - 枚举上述允许板块的完整代码区间(非仅已上市段，含未来预留号段)
    - 批量请求腾讯行情(每批 50 只)，过滤 pv_none_match(未上市/退市)
    - 剔除 ST/*ST
    - 写入 {ts_code: {avg_amt(元), name}}，与现有池结构一致
    - avg_amt 用腾讯当日成交额(万元)*10000 近似(原池为元)
    - 已存在于池中的代码直接跳过(增量)，不覆盖原有 avg_amt
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

POOL = Path(__file__).resolve().parent / "mainboard_scan_pool.json"
QT = "https://qt.gtimg.cn/q="
HEAD = {"User-Agent": "Mozilla/5.0", "Referer": "http://quote.eastmoney.com/"}
BATCH = 50

# (prefix3, exchange_suffix) — 仅允许板块，禁买板块不在此列
ALLOWED = [
    ("000", "SZ"),
    ("001", "SZ"),
    ("002", "SZ"),
    ("003", "SZ"),
    ("600", "SH"),
    ("601", "SH"),
    ("603", "SH"),
    ("605", "SH"),
    ("300", "SZ"),
    ("301", "SZ"),
]


def _enum_codes() -> list[str]:
    """枚举所有允许板块的代码(腾讯 qt 前缀格式: sh/sz + 6位)。"""
    codes = []
    for prefix, ex in ALLOWED:
        # 每段 000-999 全枚举(预留未来号段，靠 pv_none_match 过滤未上市)
        for i in range(1000):
            num = f"{prefix}{i:03d}"
            codes.append(f"{ex.lower()}{num}")
    return codes


def _fetch(codes: list[str]) -> dict:
    """codes: ['sz300750', ...] -> {ts_code: {name, avg_amt}}"""
    url = QT + ",".join(codes)
    try:
        req = urllib.request.Request(url, headers=HEAD)
        txt = urllib.request.urlopen(req, timeout=20).read().decode("gbk", "replace")
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] batch fetch err: {e}", file=sys.stderr)
        return {}
    out = {}
    for line in txt.strip().split("\n"):
        if "=" not in line:
            continue
        v = line.split("=", 1)[1].strip().strip('"')
        if v.startswith("v_pv_none_match"):
            continue
        f = v.split("~")
        if len(f) < 38:
            continue
        name = f[1]
        code = f[2]
        if not code or len(code) != 6:
            continue
        if "ST" in name.upper():
            continue
        # 退市/零成交垃圾数据过滤: 名称含「退/PT」或当日成交额=0
        if "退" in name or name.upper().startswith("PT"):
            continue
        # ts_code 格式: 6位.交易所
        ex = "SH" if code.startswith(("6", "9")) else "SZ"
        ts_code = f"{code}.{ex}"
        try:
            amount_wan = float(f[37]) if f[37] else 0.0
        except ValueError:
            amount_wan = 0.0
        if amount_wan <= 0:
            continue  # 退市/暂停上市无成交，跳过
        out[ts_code] = {"name": name, "avg_amt": amount_wan * 10000.0}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只统计不写盘")
    ap.add_argument("--no-color", action="store_true", help="关闭进度条(自动化用)")
    args = ap.parse_args()

    pool = json.loads(POOL.read_text(encoding="utf-8"))
    before = len(pool)

    all_codes = _enum_codes()
    total = len(all_codes)
    print(f"枚举允许板块代码区间: {total} 只 (主板+创业板，不含科创/北交)")

    added = 0
    skipped_existing = 0
    skipped_invalid = 0
    for i in range(0, total, BATCH):
        batch = all_codes[i : i + BATCH]
        res = _fetch(batch)
        for ts_code, info in res.items():
            if ts_code in pool:
                skipped_existing += 1
                continue
            pool[ts_code] = info
            added += 1
        if not args.no_color:
            print(
                f"  batch {i // BATCH + 1}/{total // BATCH + 1}: +{len(res)} 有效",
                end="\r",
                file=sys.stderr,
            )

    print(
        f"\n增量补全完成: 新增 {added} 只, 已存在跳过 {skipped_existing} 只, "
        f"未上市/退市/ST 跳过 {skipped_invalid} 只"
    )
    print(f"池规模: {before} -> {len(pool)}")

    if not args.dry:
        POOL.write_text(json.dumps(pool, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写盘: {POOL}")
    else:
        print("[dry] 未写盘")


if __name__ == "__main__":
    main()
