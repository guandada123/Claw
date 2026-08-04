#!/usr/bin/env python3
"""
compute_signal_weights.py — 从验证后的信号计算时间加权权重
用法: python3 compute_signal_weights.py
输入: article_signals.json
输出: signal_weights.json
08-03 修复：parse_date 兼容中文/ISO/RFC822(含截断)；过滤 return_suspect 异常收益；
输出单票主导占比 top_stock_pct（主导≥50% 时权重倍数封顶为 1）。
"""

import collections
import email.utils
import json
import os
import re
from datetime import date, datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
DATA_DIR = os.path.join(PROJECT_ROOT, ".workbuddy", "data")
SIGNALS_FILE = os.path.join(DATA_DIR, "article_signals.json")
WEIGHTS_FILE = os.path.join(DATA_DIR, "signal_weights.json")

_WEEKDAY = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
_MONTH_NAMES = [(1, "Jan"), (2, "Feb"), (3, "Mar"), (4, "Apr"), (5, "May"), (6, "Jun"),
                (7, "Jul"), (8, "Aug"), (9, "Sep"), (10, "Oct"), (11, "Nov"), (12, "Dec")]
# 与 signal_verify.py 保持一致：收益率异常默认过滤
EXCLUDE_SUSPECT = os.environ.get("SIGNAL_EXCLUDE_SUSPECT_RETURN", "1") != "0"


def parse_date(s, src=""):
    if not s:
        return None
    s = str(s).strip()
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日?", s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    # 截断 RFC822（"Sat, 27 Ju"）：weekday 约束 + source_file 兜底
    m = re.match(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun), (\d{1,2}) (\w+)", s)
    if m:
        wd, day, mon_p = m.group(1), int(m.group(2)), m.group(3)
        cands = []
        for y in (datetime.now().year - 1, datetime.now().year):
            for month, monname in _MONTH_NAMES:
                if monname.startswith(mon_p):
                    try:
                        d = date(y, month, day)
                        if _WEEKDAY[wd] == d.weekday():
                            cands.append(d)
                    except ValueError:
                        pass
        if len(cands) == 1:
            return cands[0]
        if len(cands) > 1 and len(src) >= 8:
            sd = date(int(src[:4]), int(src[4:6]), int(src[6:8]))
            return min(cands, key=lambda d: abs((d - sd).days))
        if cands:
            return cands[0]
        return None
    try:
        t = email.utils.parsedate_to_datetime(s)
        if t:
            return t.date()
    except Exception:
        pass
    return None


def main():
    if not os.path.exists(SIGNALS_FILE):
        print("无信号数据，跳过")
        return

    with open(SIGNALS_FILE, encoding="utf-8") as f:
        signals = json.load(f)

    # 读旧权重做对比
    old_weights = {}
    if os.path.exists(WEIGHTS_FILE):
        with open(WEIGHTS_FILE, encoding="utf-8") as f:
            old_weights = json.load(f).get("accounts", {})

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
                "top_stock": None,
                "top_stock_pct": 0.0,
                "stock_counts": collections.Counter(),
                "suspect_excluded": 0,
            },
        )
        acc["total"] += 1
        # 单票统计（用于主导度检测，先于 return 过滤，统计全部信号）
        cd = str(s.get("stock_code", "") or "")
        if cd:
            acc["stock_counts"][cd] += 1
        if s.get("verified"):
            acc["verified"] += 1
            ret = s.get("final_return_pct")
            if ret is not None:
                if EXCLUDE_SUSPECT and s.get("return_suspect"):
                    acc["suspect_excluded"] += 1
                    continue  # 收益率异常（疑未复权/退市/重组）：与报告口径一致，过滤
                acc["ret_sum"] += ret
                acc["ret_count"] += 1
                # 时间衰减
                sd = parse_date(s.get("recorded_at", ""), str(s.get("source_file", "")))
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
            # 单票主导降权：同一标的≥50%验证样本时，权重倍数封顶为 1（✅正常）
            top_stock, top_cnt = acc["stock_counts"].most_common(1)[0] if acc["stock_counts"] else ("", 0)
            top_pct = round(top_cnt / acc["total"] * 100, 1) if acc["total"] else 0.0
            if top_pct >= 50:
                mult = min(mult, 1)
                print(f"👀 单票主导降权: {a} {top_stock} 占 {top_pct}% (≥50%)，权重封顶 ×1")
            weights[a] = {
                "weighted_hit_rate": round(wr, 1),
                "avg_return": round(ar, 2),
                "signals_verified": acc["verified"],
                "weight_multiplier": mult,
                "source": acc["source"],
                "top_stock": top_stock,
                "top_stock_pct": top_pct,
                "suspect_return_excluded": acc["suspect_excluded"],
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
