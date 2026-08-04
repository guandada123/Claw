#!/usr/bin/env python3
"""llm_insight_for_report.py — 为早报/晚报输出「公众号精读视角」数据段。

读取 read_wx_articles.py 产物：
  - .workbuddy/data/article_insights.json  (LLM 读后笔记)
  - .workbuddy/data/article_resonance.json (多篇共振推荐股)

输出纯文本区块（stdout），供自动化 prompt 的 bash 步骤捕获后注入报告「公众号汇总」段。
只展示近 lookback_days 天内有明确观点的文章 + 共振股，复盘类不展示。

用法:
  python3 llm_insight_for_report.py [--days 3]
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
INSIGHTS = ROOT / ".workbuddy" / "data" / "article_insights.json"
RESONANCE = ROOT / ".workbuddy" / "data" / "article_resonance.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3, help="回溯天数（默认3）")
    args = ap.parse_args()

    now = datetime.now()
    cutoff = now - timedelta(days=args.days)
    actionable = []

    if INSIGHTS.exists():
        try:
            insights = json.loads(INSIGHTS.read_text(encoding="utf-8"))
            for rec in insights:
                ins = rec.get("insight", {})
                if not ins.get("is_actionable"):
                    continue
                read_at = rec.get("read_at", "")
                try:
                    rd = datetime.strptime(read_at, "%Y-%m-%d %H:%M")
                except Exception:
                    rd = None
                if rd and rd < cutoff:
                    continue
                actionable.append(rec)
        except Exception:
            pass

    resonance = []
    if RESONANCE.exists():
        try:
            resonance = json.loads(RESONANCE.read_text(encoding="utf-8"))
        except Exception:
            resonance = []

    if not actionable and not resonance:
        print(f"（近 {args.days} 天无 LLM 精读产物，或文章均为行情复盘）")
        return

    print(f"### 📖 公众号精读视角（LLM 读懂精髓，近 {args.days} 天）")
    if actionable:
        print(f"有明确观点的文章 {len(actionable)} 篇：")
        for rec in actionable[:6]:
            ins = rec.get("insight", {})
            acc = rec.get("account", "")
            stance = {"bullish": "偏多", "bearish": "偏空", "neutral": "中性"}.get(
                ins.get("market_stance"), ""
            )
            conf = {"high": "高", "medium": "中", "low": "低", "recap": "复盘"}.get(
                ins.get("confidence"), "?"
            )
            print(f"- [{acc}] {stance}·置信{conf}：{ins.get('gist', '')}")
            views = ins.get("core_views") or []
            if views:
                print(f"  观点：{views[0][:60]}")
            recs = [s for s in ins.get("stocks", []) if s.get("attitude") == "recommend"]
            for s in recs[:3]:
                tw = f"·{s.get('time_window')}" if s.get("time_window") else ""
                print(f"  ★ {s.get('name', '')} — {s.get('reason', '')}{tw}")
    else:
        print("（近几天公众号文章多为行情复盘，无明确前瞻观点）")

    if resonance:
        items = "、".join(f"{r['stock_name']}(×{r['count']})" for r in resonance[:6])
        print(f"🔥 多篇共振推荐（≥2账号）：{items}")


if __name__ == "__main__":
    main()
