#!/usr/bin/env python3
"""
wx_report_validator.py — 微信早/晚报结构校验器（A+C 两部分）

## 背景（2026-07-31 建）
日报推送后无人复核结构，曾出现：段落跳号、行动清单缺失、`·五` 宏观段
漏写、持仓数跟 portfolio.json 对不上、市值/总资产口径偏差过大等瑕疵。
本脚本做轻量静态校验，默认**观察模式**（只记录不推送），避免告警疲劳。

## 校验项（SCHEMA，已核真实文件，勿凭记忆改）
- 早报(wx_*_morning.md): 风险段(🩺今日风险,无编号) + 二~八 + 七·五 = 9 段
- 晚报(wx_*_evening.md): 风险段(🩺收盘风险复盘,无编号) + 二~十 + 九·五 = 11 段
- 第1段为风险段（emoji 头,无中文编号）；主段编号自「二」起
- `·五` 白名单: 七·五 / 九·五（宏观景气段，允许中段插入）
- 段落顺序: 风险段→二→…严格递增，禁止跳号/重复
- 持仓数: 模拟盘标的计数 vs portfolio.json（仅告警，不硬失败）
- ⚠️ 行动清单段已于 2026-08 模板重构(vFinal)移除，不再强制校验

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
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
}
# 匹配标题中的中文编号: 允许前缀 emoji/空格, 核心 `七·五` / `一` 等
NUM_HEAD_RE = re.compile(r"(十?[一二三四五六七八九十]+)(?:·([一二三四五六七八九十]+))?")

# 第1段为风险段（🩺今日风险 / 🩺收盘风险复盘），无中文编号；
# 主段编号自「二」起（二~八 / 二~十），中段插 ·五 宏观景气段。
# 行动清单段已于 2026-08 模板重构（vFinal 合成版）移除，不再强制。
RISK_HEADERS = ("今日风险", "收盘风险复盘")  # 第1段风险头（无编号）
MORNING_RISK = "risk"
MORNING_NUMS = list(range(2, 9))  # 二~八
MORNING_SUB = "7·5"
EVENING_RISK = "risk"
EVENING_NUMS = list(range(2, 11))  # 二~十
EVENING_SUB = "9·5"


def parse_sections(text: str) -> list[tuple]:
    """返回 [(order_token, sub_or_none, raw_title), ...]
    order_token: 'risk'(第1段风险头) / int(主段编号) / 'x·y'(·五子段)
    """
    out = []
    for m in SECTION_RE.finditer(text):
        title = m.group(1).strip()
        # 风险段特判（第1段，无编号，emoji 风险头）
        if any(rh in title for rh in RISK_HEADERS):
            out.append(("risk", None, title))
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

    is_evening = "evening" in name
    risk_hdr = EVENING_RISK if is_evening else MORNING_RISK
    exp_nums = EVENING_NUMS if is_evening else MORNING_NUMS
    exp_subs = {EVENING_SUB if is_evening else MORNING_SUB}

    # 1) 风险段（第1段，无编号）必须存在且为首段
    if risk_hdr not in orders:
        errs.append(f"[{name}] 缺失风险段(第1段 🩺今日风险/收盘风险复盘)")
    elif orders[0] != risk_hdr:
        errs.append(f"[{name}] 风险段必须为首个主段落（实际非首段）")

    # 2) 重复主段编号
    seen = set()
    for o in orders:
        if isinstance(o, int):
            if o in seen:
                errs.append(f"[{name}] 段落编号重复: 第{o}段")
            seen.add(o)

    # 3) 跳号 / 多余（主段编号 二~八 / 二~十）
    actual_nums = [o for o in orders if isinstance(o, int)]
    for en in exp_nums:
        if en not in actual_nums:
            errs.append(f"[{name}] 缺失主段落: 第{en}段")
    for an in actual_nums:
        if an not in exp_nums:
            errs.append(f"[{name}] 多余主段落: 第{an}段（不在预期 schema）")

    # 4) 子段白名单（七·五 / 九·五）+ 缺失检测
    actual_subs = {o for o in orders if isinstance(o, str) and "·" in o}
    for s in actual_subs:
        if s not in exp_subs:
            errs.append(f"[{name}] 非白名单子段: {s}（仅允许 七·五/九·五）")
    for s in exp_subs:
        if s not in actual_subs:
            errs.append(f"[{name}] 缺失子段: {s}（宏观景气段）")

    # 5) 顺序校验（集合完整时）
    expected_seq = [risk_hdr] + exp_nums
    if is_evening:
        expected_seq = expected_seq[:9] + [EVENING_SUB] + expected_seq[9:]
    else:
        expected_seq = expected_seq[:7] + [MORNING_SUB] + expected_seq[7:]
    actual_seq = [o for o in orders if o in set(expected_seq)]
    if not errs and actual_seq != expected_seq:
        errs.append(f"[{name}] 段落顺序异常（集合完整但顺序错乱）")

    return errs


def check_holdings(name: str, text: str) -> list[str]:
    """持仓计数 vs portfolio.json（仅告警）

    消噪：同日盘中建仓的持仓（first_buy_date == 报告日）在早报生成时尚未买入，
    预期不出现于早报 → 跳过告警，消除 600031 类误报；其余持仓未出现仍告警。
    """
    warn = []
    try:
        sim = json.loads(SIM_PORTFOLIO.read_text())
        sim_pos = sim.get("positions", {})
        # 报告日期（文件名前8位 YYYYMMDD）
        m = re.match(r"(\d{8})", name)
        rdate = m.group(1) if m else None
        is_morning = "morning" in name
        for code, pos in sim_pos.items():
            if not isinstance(pos, dict):
                continue
            # 同日建仓 + 早报 → 预期不出现，跳过（消除盘中建仓误报）
            fbd = pos.get("first_buy_date")
            if fbd and rdate and is_morning:
                fbd8 = str(fbd).replace("-", "")[:8]
                if len(fbd8) == 8 and fbd8.isdigit() and fbd8 == rdate:
                    continue
            # 报告中模拟盘表格应含这些代码
            if code not in text:
                warn.append(f"[{name}] 模拟盘持仓 {code}({pos.get('name', '')}) 未在报告中出现")
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
        r = subprocess.run(
            ["bash", str(PUSH), title, content], capture_output=True, text=True, timeout=90, env=env
        )
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
        # 覆盖「当日 + 前一日」双日期前缀，修复跨日边界漏判（08-03 记录：
        # 21:00 运行时 since 落在前一日，原 glob 只匹配前一日前缀，漏掉当日报告）
        now = datetime.now()
        day_prefixes = {(now - timedelta(days=d)).strftime("%Y%m%d") for d in range(2)}
        files = [
            f
            for f in REPORT_DIR.glob("*.md")
            if f.name[:8].isdigit()
            and f.name[:8] in day_prefixes
            and ("morning" in f.name or "evening" in f.name)
        ]
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

    print(
        f"[validator] 校验 {len(results)} 份 | FAIL {len(failed)} | WARN {len(warned)} | OK {len(results) - len(failed) - len(warned)}"
    )
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

    print(
        "SUMMARY: "
        + json.dumps(
            {"checked": len(results), "fail": len(failed), "warn": len(warned)}, ensure_ascii=False
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
