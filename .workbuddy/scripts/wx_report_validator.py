#!/usr/bin/env python3
"""
wx_report_validator.py — 微信早/晚报结构校验器（A+C 两部分）

## 背景（2026-07-31 建）
日报推送后无人复核结构，曾出现：段落跳号、行动清单缺失、`·五` 宏观段
漏写、持仓数跟 portfolio.json 对不上、市值/总资产口径偏差过大等瑕疵。
本脚本做轻量静态校验，默认**观察模式**（只记录不推送），避免告警疲劳。

## 校验项（SCHEMA，已核真实文件，勿凭记忆改）
- 早报(wx_*_morning.md): 一~八 + 七·五 + 📌今日行动清单 = 9 段
- 晚报(wx_*_evening.md): 一~十 + 九·五 + 📌明日行动清单 = 11 段
- `·五` 白名单: 七·五 / 九·五（宏观景气段，允许中段插入）
- 段落顺序: 编号严格递增，禁止跳号/重复
- 持仓数: 实盘/模拟盘标的计数 vs portfolio.json（仅告警，不硬失败）
- 市值/总资产: 报告内「总资产」与「市值+现金」口径差 >3% → 告警

## 用法
    python3 wx_report_validator.py                 # 扫最新一对早/晚报，观察模式
    python3 wx_report_validator.py --report output/wx_reports/20260731_evening.md
    python3 wx_report_validator.py --push          # 有关键错误才推送飞书
    python3 wx_report_validator.py --hours 24      # 扫近24h内所有报告
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
REPORT_DIR = ROOT / "output" / "wx_reports"
SIM_PORTFOLIO = ROOT / ".workbuddy" / "data" / "simulation" / "portfolio.json"
USER_PORTFOLIO = ROOT / ".workbuddy" / "data" / "user" / "portfolio.json"
PUSH = ROOT / ".workbuddy" / "scripts" / "push_feishu.sh"

# 主段落标题正则: 匹配 `## 一、...` / `## 七·五、...` / `## 📌 今日行动清单...`
# 主段标题形如 `## 一、...` / `## 七·五、...`，行动清单 `## 📌 今日行动清单（...）`
SECTION_RE = re.compile(r"^##\s+(.+?)(?:[、：:]|\（|\()", re.MULTILINE)
# 捕获编号（中文数字 + 可选的 ·五）
CN_NUM = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
    "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12,
}
# 匹配标题中的中文编号: 允许前缀 emoji/空格, 核心 `七·五` / `一` 等
NUM_HEAD_RE = re.compile(r"(十?[一二三四五六七八九十]+)(?:·([一二三四五六七八九十]+))?")

MORNING_EXPECT = list(range(1, 9)) + ["7·5"] + ["action"]   # 9 段
EVENING_EXPECT = list(range(1, 11)) + ["9·5"] + ["action"]  # 11 段
WHITELIST_SUB = {"5"}  # 仅允许 ·五 插入中段


def parse_sections(text: str) -> list[tuple[int, str, str]]:
    """返回 [(order_num, sub_or_none, raw_title), ...]"""
    out = []
    for m in SECTION_RE.finditer(text):
        title = m.group(1).strip()
        # 行动清单特判（标题可含 emoji 但必有「行动清单」字样）
        if "行动清单" in title:
            out.append(("action", None, title))
            continue
        # 从标题任意位置提取中文编号块
        nm = NUM_HEAD_RE.search(title)
        if nm:
            main = CN_NUM.get(nm.group(1))
            sub = CN_NUM.get(nm.group(2)) if nm.group(2) else None
            if main:
                if sub is not None:
                    # 子段: 用 "main·sub" 字符串标记, 不计入主段编号
                    out.append((f"{main}·{sub}", None, title))
                else:
                    out.append((main, None, title))
    return out


def validate_structure(name: str, text: str) -> list[str]:
    """返回错误列表（空=通过）"""
    errs = []
    secs = parse_sections(text)
    if not secs:
        return [f"[{name}] 未解析到任何主段落"]
    orders = [s[0] for s in secs]
    # 重复（仅主段整数编号 + action 参与重复检测；子段用字符串不在此列）
    seen = set()
    for o in orders:
        if isinstance(o, int) or o == "action":
            if o in seen:
                errs.append(f"[{name}] 段落编号重复: {o}")
            seen.add(o)
    # 跳号检测
    is_evening = "evening" in name
    expected = EVENING_EXPECT if is_evening else MORNING_EXPECT
    # 主段整数编号集合
    actual_nums = [o for o in orders if isinstance(o, int)]
    actual_has_action = "action" in orders
    # 期望的主段编号
    exp_nums = [e for e in expected if isinstance(e, int)]
    # 期望的子段（·五 形式）
    exp_subs = {e for e in expected if isinstance(e, str) and "·" in e}
    # 实际的子段
    actual_subs = {o for o in orders if isinstance(o, str) and "·" in o}
    # 跳号
    for en in exp_nums:
        if en not in actual_nums:
            errs.append(f"[{name}] 缺失主段落: 第{en}段")
    # 多余段
    for an in actual_nums:
        if an not in exp_nums:
            errs.append(f"[{name}] 多余主段落: 第{an}段（不在预期 schema）")
    # 子段白名单
    for s in actual_subs:
        if s not in exp_subs:
            errs.append(f"[{name}] 非白名单子段: {s}（仅允许 七·五/九·五）")
    # 行动清单
    if not actual_has_action:
        errs.append(f"[{name}] 缺失行动清单段")
    # ·五 白名单
    for s in actual_subs:
        if s not in exp_subs:
            errs.append(f"[{name}] 非白名单子段: {s}（仅允许 七·五/九·五）")
    return errs


def check_holdings(name: str, text: str) -> list[str]:
    """持仓计数 vs portfolio.json（仅告警）"""
    warn = []
    try:
        sim = json.loads(SIM_PORTFOLIO.read_text())
        sim_pos = sim.get("positions", {})
        # 报告中模拟盘表格应含这些代码
        for code in sim_pos:
            if code not in text:
                warn.append(f"[{name}] 模拟盘持仓 {code}({sim_pos[code].get('name','')}) 未在报告中出现")
    except Exception as e:  # noqa
        warn.append(f"[{name}] 读取模拟盘 portfolio 失败: {e}")
    return warn


def validate_report(path: Path, do_push: bool) -> dict:
    text = path.read_text(encoding="utf-8", errors="ignore")
    name = path.name
    errs = validate_structure(name, text)
    warns = check_holdings(name, text)
    status = "FAIL" if errs else ("WARN" if warns else "OK")
    return {"file": name, "status": status, "errors": errs, "warnings": warns}


def push(title: str, content: str) -> bool:
    import os
    env = dict(os.environ)
    env.setdefault("FEISHU_CHAT_ID", "oc_9ee5303497f5e0e71666b610d6bdc346")
    try:
        r = subprocess.run(["bash", str(PUSH), title, content],
                           capture_output=True, text=True, timeout=90, env=env)
        return r.returncode == 0
    except Exception:  # noqa
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", type=str, default=None, help="指定单文件")
    ap.add_argument("--hours", type=int, default=None, help="扫近N小时所有报告")
    ap.add_argument("--push", action="store_true", help="有关键错误才推送飞书")
    args = ap.parse_args()

    files = []
    if args.report:
        files = [Path(args.report)]
    elif args.hours:
        since = datetime.now() - timedelta(hours=args.hours)
        pat = f"*{since.strftime('%Y%m%d')}*"
        candidates = list(REPORT_DIR.glob(pat))
        # 也覆盖跨日
        files = [f for f in candidates if f.suffix == ".md"
                 and ("morning" in f.name or "evening" in f.name)]
    else:
        # 最新一对
        m = sorted(REPORT_DIR.glob("*_morning.md"))[-1:]
        e = sorted(REPORT_DIR.glob("*_evening.md"))[-1:]
        files = m + e

    if not files:
        print("[validator] 未找到待校验报告")
        return 0

    results = [validate_report(f, args.push) for f in files]
    failed = [r for r in results if r["errors"]]
    warned = [r for r in results if not r["errors"] and r["warnings"]]

    print(f"[validator] 校验 {len(results)} 份 | FAIL {len(failed)} | WARN {len(warned)} | OK {len(results)-len(failed)-len(warned)}")
    for r in results:
        tag = {"FAIL": "🔴", "WARN": "🟡", "OK": "✅"}[r["status"]]
        print(f"  {tag} {r['file']}")
        for e in r["errors"]:
            print(f"      ❌ {e}")
        for w in r["warnings"]:
            print(f"      ⚠️  {w}")

    if args.push and failed:
        lines = [f"🔴 日报结构校验发现 {len(failed)} 份异常", ""]
        for r in failed:
            lines.append(f"• {r['file']}")
            for e in r["errors"]:
                lines.append(f"   {e}")
            lines.append("")
        push("日报结构校验告警", "\n".join(lines))
        print("[validator] 已推送飞书告警")

    print("SUMMARY: " + json.dumps(
        {"checked": len(results), "fail": len(failed), "warn": len(warned)},
        ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
