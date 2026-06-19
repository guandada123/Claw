#!/usr/bin/env python3
"""公众号信号溯源报告生成器"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SIGNALS_FILE = PROJECT_ROOT / ".workbuddy" / "data" / "article_signals.json"


def parse_chinese_date(s: str) -> datetime | None:
    """解析中文明细日期"""
    s = s.strip()
    # "2026年5月29日"
    m = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", s)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    # "2026-05-29"
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def main():
    signals = json.loads(SIGNALS_FILE.read_text())

    now = datetime.now()
    # 统计近60天
    cutoff_60 = now - timedelta(days=60)
    # 统计近30天
    cutoff_30 = now - timedelta(days=30)
    # 统计近7天
    cutoff_7 = now - timedelta(days=7)

    recent_60 = []
    recent_30 = []
    recent_7 = []

    for s in signals:
        dt = parse_chinese_date(s.get("recorded_at", ""))
        if dt:
            if dt >= cutoff_60:
                recent_60.append(s)
            if dt >= cutoff_30:
                recent_30.append(s)
            if dt >= cutoff_7:
                recent_7.append(s)

    def stats(period_signals, label):
        account_stats = {}
        for s in period_signals:
            acc = s["account"]
            if acc not in account_stats:
                account_stats[acc] = {
                    "total": 0,
                    "bullish": 0,
                    "bearish": 0,
                    "stocks": {},
                    "articles": set(),
                }
            stat = account_stats[acc]
            stat["total"] += 1
            stat[s["signal"]] += 1
            stock_key = f"{s['stock_name']}({s['stock_code']})"
            stat["stocks"][stock_key] = stat["stocks"].get(stock_key, 0) + 1
            stat["articles"].add(s["title"])

        return account_stats

    stats_60 = stats(recent_60, "60天")
    stats_30 = stats(recent_30, "30天")
    stats_7 = stats(recent_7, "7天")

    # 输出报告
    print("📊 公众号信号溯源报告")
    print(f"   生成时间: {now.strftime('%Y-%m-%d %H:%M')}")
    print(f"   信号总量: {len(signals)} 条")
    print(f"   来源公众号: {len({s['account'] for s in signals})} 个")
    stock_set = set()
    for s in signals:
        stock_set.add(f"{s['stock_name']}({s['stock_code']})")
    print(f"   涉及股票: {len(stock_set)} 只")
    print()

    for label, stats_dict, period_signals in [
        ("近7天", stats_7, recent_7),
        ("近30天", stats_30, recent_30),
        ("近60天", stats_60, recent_60),
    ]:
        print(f"{'=' * 60}")
        print(f"📅 {label}（{len(period_signals)} 条信号）")
        print(f"{'=' * 60}")

        if not stats_dict:
            print("  无信号")
            print()
            continue

        sorted_accs = sorted(stats_dict.items(), key=lambda x: -x[1]["total"])

        print(f"  {'公众号':<16} {'信号':<6} {'看多':<6} {'涉及股票'}")
        print(f"  {'-' * 50}")
        for acc, stat in sorted_accs:
            stocks_str = ", ".join(sorted(stat["stocks"].keys()))
            # 截断显示
            if len(stocks_str) > 40:
                stocks_str = stocks_str[:38] + "…"
            print(f"  {acc:<16} {stat['total']:<6} {stat['bullish']:<6} {stocks_str}")
        print()

    # 按公众号汇总
    print(f"{'=' * 60}")
    print("📋 公众号综合排名（近60天）")
    print(f"{'=' * 60}")
    print(f"  {'排名':<4} {'公众号':<16} {'信号':<6} {'看多':<6} {'文章':<6} {'涉及股票'}")
    print(f"  {'-' * 55}")

    all_accounts = {}
    for s in signals:
        acc = s["account"]
        dt = parse_chinese_date(s.get("recorded_at", ""))
        if dt and dt >= cutoff_60:
            if acc not in all_accounts:
                all_accounts[acc] = {"total": 0, "bullish": 0, "articles": set(), "stocks": set()}
            all_accounts[acc]["total"] += 1
            all_accounts[acc]["bullish"] += 1 if s["signal"] == "bullish" else 0
            all_accounts[acc]["articles"].add(s["title"])
            all_accounts[acc]["stocks"].add(f"{s['stock_name']}({s['stock_code']})")

    for i, (acc, info) in enumerate(sorted(all_accounts.items(), key=lambda x: -x[1]["total"]), 1):
        stocks_str = ", ".join(sorted(info["stocks"])[:4])
        if len(info["stocks"]) > 4:
            stocks_str += f"…(+{len(info['stocks']) - 4})"
        print(
            f"  #{i:<2} {acc:<16} {info['total']:<6} {info['bullish']:<6} {len(info['articles']):<6} {stocks_str}"
        )

    print()
    print(f"{'=' * 60}")
    print("⚠️ 说明：所有信号均为AI自动提取，尚未与真实行情验证命中率")
    print("  验证功能需对接行情API完成回测比对")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
