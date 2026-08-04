#!/usr/bin/env python3
"""生成信号溯源审计 CSV（合并 source_weights + signal_consensus）。
用法: python3 gen_signal_audit_csv.py
输出: data/signal_audit_YYYY-MM-DD.csv
"""

import csv
import datetime
import json
import os

CLAW = os.path.expanduser("~/WorkBuddy/Claw")
TODAY = datetime.date.today().strftime("%Y-%m-%d")
WS = os.path.join(CLAW, "data", "source_weights.json")
CS = os.path.join(CLAW, "data", "signal_consensus.json")
OUT = os.path.join(CLAW, "data", f"signal_audit_{TODAY}.csv")

# 7/23 结算态（来自 automation memory 权威记录）用于横比 change 列
# 仅收录有明确记录的源；未记录的按"维持"处理（数据集近等价）
BASELINE = {
    "证券时报": ("NEW", "新增⭐推荐(≥3验证首次达66.7%)"),
    "TGB湖南人": (41.3, "✅正常维持"),
    "小李哥的投资逻辑": (22.2, "⚠️监控维持"),
    "好运侠客": (16.3, "⚠️监控维持(仍<40%)"),
    "红鼻子小丑": (0.0, "建议移出RSS(命中0%)"),
}
# 其余在 7/23 已记录为稳定者
STABLE = {
    "城市金融报财观新闻": "⭐推荐维持",
    "第一财经": "⭐推荐维持",
    "上海证券报": "✅正常维持",
    "君临木": "⚠️监控维持",
    "猫笔刀": "⚠️监控维持",
    "股海孙大圣": "极低维持",
    "盘口逻辑拆解": "极低维持",
    "鑫指量化": "低信源维持",
    "白泽投研": "极低维持",
    "盘面解盘室": "低信源维持",
    "恩哥箴言": "极低维持",
    "飞龙山侠": "极低维持",
    "搬砖小组": "极低维持",
    "猫笔叨的读后感专区": "低信源维持",
    "天财龙哥": "低信源维持",
    "炒家信条抄底大师": "低信源维持",
    "板神猫哥": "低信源维持",
    "财联社": "低信源维持",
}


def grade_of(wr):
    if wr is None:
        return "N/A"
    if wr >= 60:
        return "⭐推荐"
    if wr >= 40:
        return "✅正常"
    return "⚠️监控"


with open(WS, encoding="utf-8") as f:
    ws = json.load(f)
with open(CS, encoding="utf-8") as f:
    cs = json.load(f)

summary = cs.get("summary", {})

rows = []
for d in ws.get("details", []):
    acct = d["account"]
    wr = d["win_rate"]
    weight = d["weight"]
    if acct in BASELINE:
        base, note = BASELINE[acct]
        if base == "NEW":
            change = "🆕 新增⭐推荐"
        else:
            delta = round(wr - base, 1)
            change = f"{base}%→{wr}% ({'+' if delta >= 0 else ''}{delta}pt) {note}"
    elif acct in STABLE:
        change = STABLE[acct]
    else:
        change = "维持"
    rows.append(
        {
            "account": acct,
            "win_rate": wr,
            "signals": d["signals"],
            "avg_return": d["avg_return"],
            "weight": weight,
            "grade": grade_of(wr),
            "change_vs_0723": change,
        }
    )

# 按 win_rate 降序
rows.sort(key=lambda r: r["win_rate"] if r["win_rate"] is not None else -1, reverse=True)

with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["账号", "加权命中率%", "验证信号数", "平均收益%", "权重", "评级", "vs_7-23变化"])
    for r in rows:
        w.writerow(
            [
                r["account"],
                r["win_rate"],
                r["signals"],
                r["avg_return"],
                r["weight"],
                r["grade"],
                r["change_vs_0723"],
            ]
        )

print(f"✅ CSV 已生成: {OUT}")
print(
    f"   行数={len(rows)} | 共识: 总配对={summary.get('total_pairs')} 双源={summary.get('dual_source')} "
    f"强共识={summary.get('strong_consensus')} 分歧={summary.get('conflict')}"
)
