#!/usr/bin/env python3
"""
knowledge_push.py - 将本次新增文章摘要推送至飞书主群（替代缺失的 notify_center.py）
仅当新增数 > 3 时推送，消息含总数/标签分布/来源/重点速览。
"""
import datetime
import json
import os
import subprocess
from collections import Counter

INDEX_JSON = "/Users/guan/WorkBuddy/Claw/.workbuddy/knowledge/index/articles_index.json"
CHAT_ID = "oc_9ee5303497f5e0e71666b610d6bdc346"
LARK = "/Users/guan/.workbuddy/binaries/node/versions/22.22.2/bin/lark-cli"


def main():
    d = json.load(open(INDEX_JSON, encoding="utf-8"))
    arts = d["articles"]
    n = len(arts)
    if n <= 3:
        print(f"新增 {n} 篇 <= 3，跳过推送")
        return

    tags = Counter()
    for a in arts:
        for t in a["tags"]:
            tags[t] += 1
    srcs = Counter(a["source"] for a in arts)
    tag_str = " · ".join(f"{k} {v}" for k, v in tags.most_common())
    src_str = " · ".join(f"{k}({v})" for k, v in srcs.most_common(6))
    if len(srcs) > 6:
        src_str += f" 等{len(srcs)}个公众号"

    # 重点速览：取标题中含“深度/金股/复盘/收评/业绩”或来自投研类来源的条目
    highlight_keys = ["深度", "金股", "复盘", "收评", "业绩", "券商", "策略", "前瞻"]
    picks = []
    for a in arts:
        if any(k in a["title"] for k in highlight_keys) and len(picks) < 6:
            picks.append(f"· {a['source']}《{a['title']}》")
    if not picks:
        picks = [f"· {a['source']}《{a['title']}》" for a in arts[:6]]

    today = datetime.date.today().isoformat()
    msg = (
        "📚【知识库】知识沉淀\n"
        "━━━━━━━━━━━━━\n"
        f"📖 本次新增 {n} 篇文章（首次全量索引）\n"
        f"📊 标签分布：{tag_str}\n"
        f"📰 来源：{src_str}\n\n"
        "📌 重点速览：\n"
        + "\n".join(picks)
        + "\n━━━━━━━━━━━━━"
    )

    # write message for record
    os.makedirs("/Users/guan/WorkBuddy/Claw/.workbuddy/knowledge/index", exist_ok=True)
    with open("/Users/guan/WorkBuddy/Claw/.workbuddy/knowledge/index/last_push.md", "w", encoding="utf-8") as fh:
        fh.write(msg + f"\n\n(dedupe-key: 知识库索引-{today})\n")

    try:
        r = subprocess.run(
            [LARK, "im", "+messages-send", "--as", "bot",
             "--chat-id", CHAT_ID, "--markdown", msg],
            capture_output=True, text=True, timeout=60,
        )
        print("PUSH exit:", r.returncode)
        print(r.stdout[-500:] if r.stdout else "")
        if r.stderr:
            print("STDERR:", r.stderr[-500:])
    except Exception as e:
        print("PUSH ERROR:", repr(e))


if __name__ == "__main__":
    main()
