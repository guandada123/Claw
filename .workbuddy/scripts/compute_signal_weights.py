#!/usr/bin/env python3
"""
compute_signal_weights.py — 从验证后的信号计算时间加权权重
用法: python3 compute_signal_weights.py
输入: article_signals.json
输出: signal_weights.json
"""

import json
import os
import re
from datetime import date

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
DATA_DIR = os.path.join(PROJECT_ROOT, ".workbuddy", "data")
SIGNALS_FILE = os.path.join(DATA_DIR, "article_signals.json")
WEIGHTS_FILE = os.path.join(DATA_DIR, "signal_weights.json")


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    m = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s[:10])
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def main():
    if not os.path.exists(SIGNALS_FILE):
        print("无信号数据，跳过")
        return

    signals = json.load(open(SIGNALS_FILE, encoding="utf-8"))

    # 读旧权重做对比
    old_weights = {}
    if os.path.exists(WEIGHTS_FILE):
        old_weights = json.load(open(WEIGHTS_FILE, encoding="utf-8")).get("accounts", {})

    today = date.today()

    # 按公众号汇总
    accounts = {}
    for s in signals:
        a = s.get("account", "?")
        acc = accounts.setdefault(
            a,
            {
                "total": 0,
                "verified": 0,
                "weighted_hits": 0.0,
                "weighted_total": 0.0,
                "raw_hits": 0,
                "ret_sum": 0.0,
                "ret_count": 0,
                "source": s.get("source", "早报"),
            },
        )
        acc["total"] += 1
        if s.get("verified"):
            acc["verified"] += 1
            ret = s.get("final_return_pct")
            if ret is not None:
                acc["ret_sum"] += ret
                acc["ret_count"] += 1
                # 时间衰减
                sd = parse_date(s.get("recorded_at", ""))
                if sd:
                    days = (today - sd).days
                    w = 1.0 if days <= 30 else 0.7 if days <= 90 else 0.4 if days <= 180 else 0.1
                else:
                    w = 1.0
                acc["weighted_total"] += w
                if s.get("hit"):
                    acc["raw_hits"] += 1
                    acc["weighted_hits"] += w

    # 生成权重（≥3 验证信号才有效）
    weights = {}
    for a, acc in accounts.items():
        if acc["verified"] >= 3 and acc["weighted_total"] > 0:
            wr = acc["weighted_hits"] / acc["weighted_total"] * 100
            ar = acc["ret_sum"] / acc["ret_count"] if acc["ret_count"] > 0 else 0.0
            mult = 3 if wr > 50 else 2 if wr > 30 else 1 if wr > 10 else 0.5
            weights[a] = {
                "weighted_hit_rate": round(wr, 1),
                "avg_return": round(ar, 2),
                "signals_verified": acc["verified"],
                "weight_multiplier": mult,
                "source": acc["source"],
            }

            # 升降级判定
            old = old_weights.get(a, {})
            old_wr = old.get("weighted_hit_rate", 0)
            if wr > 50 and acc["source"] != "早报":
                print(f"⭐ 建议加入RSS: {a} (命中{wr:.1f}%)")
            elif wr < 15 and acc["source"] == "早报" and acc["verified"] >= 5:
                print(f"⚠️ 建议移出RSS: {a} (命中{wr:.1f}%)")
            elif old_wr and abs(wr - old_wr) > 10:
                print(f"👀 持续监控: {a} ({old_wr:.1f}%→{wr:.1f}%)")

    with open(WEIGHTS_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"updated": today.strftime("%Y-%m-%d"), "accounts": weights},
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"✅ 权重表已更新 ({len(weights)} 个公众号 ≥3验证信号)")
    for a, w in sorted(weights.items(), key=lambda x: -x[1]["weighted_hit_rate"]):
        print(f"  {a}: 加权命中{w['weighted_hit_rate']}% 收益{w['avg_return']:+.1f}% ×{w['weight_multiplier']}")


if __name__ == "__main__":
    main()
