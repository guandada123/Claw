#!/usr/bin/env python3
"""
knowledge_indexer.py - 知识库文章索引器
扫描 archive/articles 下的微信文章 markdown，提取元数据、生成摘要、打标签，
写入 .workbuddy/knowledge/index/ 下的 JSON + Markdown 索引，并更新 last_scan.txt。
支持增量：仅索引比 last_scan.txt 更晚修改的文章（首次运行则全量）。
"""
import datetime
import glob
import json
import os
import re
from collections import Counter

ARCHIVE_ARTICLES = "/Users/guan/WorkBuddy/Claw/archive/articles"
INDEX_DIR = "/Users/guan/WorkBuddy/Claw/.workbuddy/knowledge/index"
LAST_SCAN = os.path.join(INDEX_DIR, "last_scan.txt")
SUMMARY_LIMIT = 100

os.makedirs(INDEX_DIR, exist_ok=True)


def parse_meta(text):
    meta = {}
    for line in text.splitlines()[:40]:
        m = re.match(r'-\s*\*\*(.+?)\*\*:\s*(.+)', line)
        if m:
            meta[m.group(1).strip()] = m.group(2).strip()
    return meta


def extract_summary(text, limit=SUMMARY_LIMIT):
    parts = text.split('---', 2)
    body = parts[-1] if len(parts) >= 2 else text
    body = re.sub(r'!\[.*?\]\(.*?\)', '', body)          # 去图片
    paras = [p.strip() for p in body.split('\n')
             if p.strip() and not p.strip().startswith('#')]
    summary = ''
    for para in paras:
        cleaned = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', para)   # 去链接保留文字
        cleaned = re.sub(r'[#*`>]', '', cleaned)            # 去 markdown 符号
        if len(cleaned) < 10:
            continue
        summary = cleaned
        break
    if not summary:
        summary = re.sub(r'\s+', ' ', body)[:200]
    return summary[:limit].strip()


TAG_RULES = [
    ("AI", ["AI", "人工智能", "大模型", "GPT", "算力", "机器人", "智能体", "agent",
            "深度学习", "神经网络", "机器学习"]),
    ("投资", ["股", "涨", "跌", "仓", "策略", "金股", "龙头", "复盘", "券商", "盘",
              "估值", "业绩", "买入", "目标价", "仓位", "A股", "指数", "板块", "利好", "利空"]),
    ("技术", ["仓库", "代码", "github", "爬虫", "开源", "rust", "python", "开发",
              "架构", "api", "前端", "后端", "数据库", "算法"]),
    ("管理", ["管理", "组织", "中国式现代化", "科研", "学习语", "干部", "党建", "团队"]),
    ("效率", ["效率", "工具", "工作流", "自动化", "prompt", "提效", "方法论"]),
]


def tag(text, title):
    combined = (title + " " + text[:600]).lower()
    tags = []
    for tagname, kws in TAG_RULES:
        if any(kw.lower() in combined for kw in kws):
            tags.append(tagname)
    return tags or ["其他"]


def main():
    files = sorted(glob.glob(os.path.join(ARCHIVE_ARTICLES, "**", "*.md"), recursive=True))
    # 增量过滤：仅处理比 last_scan.txt 更新的文件
    ref_mtime = None
    if os.path.exists(LAST_SCAN):
        ref_mtime = os.path.getmtime(LAST_SCAN)
    new_files = []
    for f in files:
        if ref_mtime is None or os.path.getmtime(f) > ref_mtime:
            new_files.append(f)

    articles = []
    for f in new_files:
        rel = os.path.relpath(f, ARCHIVE_ARTICLES)
        parts = rel.split(os.sep)
        dir_date = parts[0] if re.match(r'\d{4}-\d{2}-\d{2}', parts[0]) else ''
        fname = os.path.splitext(parts[-1])[0]
        if '_' in fname:
            source, title = fname.split('_', 1)
        else:
            source, title = '未知', fname
        with open(f, encoding='utf-8', errors='ignore') as fh:
            text = fh.read()
        meta = parse_meta(text)
        pub = meta.get('发布时间', '')
        pub_date = dir_date
        m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', pub)
        if m:
            pub_date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        link = meta.get('原文链接', '')
        articles.append({
            "title": title,
            "source": source,
            "pub_date": pub_date,
            "archive_date": dir_date,
            "tags": tag(text, title),
            "summary": extract_summary(text),
            "link": link,
            "path": f,
        })

    articles.sort(key=lambda a: a['pub_date'], reverse=True)

    index = {
        "generated_at": datetime.datetime.now().isoformat(timespec='seconds'),
        "total": len(articles),
        "articles": articles,
    }
    with open(os.path.join(INDEX_DIR, "articles_index.json"), "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=2)

    md = f"# 知识库文章索引\n\n> 生成时间: {index['generated_at']} | 共 {len(articles)} 篇（本次新增）\n\n"
    for a in articles:
        md += f"## {a['title']}\n"
        md += (f"- **来源**: {a['source']} | **发布**: {a['pub_date']} "
               f"| **标签**: {', '.join(a['tags'])}\n")
        md += f"- **摘要**: {a['summary']}\n"
        if a['link']:
            md += f"- **原文**: {a['link']}\n"
        md += f"- **路径**: `{a['path']}`\n\n"
    with open(os.path.join(INDEX_DIR, "articles_index.md"), "w", encoding="utf-8") as fh:
        fh.write(md)

    now = datetime.datetime.now().isoformat(timespec='seconds')
    with open(LAST_SCAN, "w", encoding="utf-8") as fh:
        fh.write(f"{len(articles)} 篇新增 @ {now}\n")

    print(f"INDEXED {len(articles)} articles")
    cat = Counter()
    for a in articles:
        for t in a['tags']:
            cat[t] += 1
    print("Tags:", dict(cat.most_common()))
    src = Counter(a['source'] for a in articles)
    print("Sources:", dict(src.most_common()))


if __name__ == "__main__":
    main()
