#!/usr/bin/env python3
"""
push_morning_report.py — 微信早报统一推送脚本（卡片化）
=========================================================
替代每天一个的 _push_morning_YYYYMMDD.py 技术债，统一走 push_card.py 中台。

流程（方案 X：脚本内部自治）：
  1. 读 output/wx_reports/{today}_morning.md
  2. lark-cli docs +create 生成飞书文档（user→bot 降级），取真实 url
  3. 解析 md 前几段 → 卡片 sections（风险/信号/大盘/板块/操作/合规）
  4. push_card.py 发 interactive 卡片（红头 alert），按钮指向真实 docx url

依赖：push_card.py（同目录）、lark-cli
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LARK_CLI = (
    os.path.expanduser("~/.workbuddy/binaries/node/versions/22.22.2/bin/lark-cli")
    if os.path.isfile(
        os.path.expanduser("~/.workbuddy/binaries/node/versions/22.22.2/bin/lark-cli")
    )
    else "lark-cli"
)
DEFAULT_CHAT = "oc_9ee5303497f5e0e71666b610d6bdc346"
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _normalize_risk_heading(md: str) -> str:
    """防御 LLM 偶发对风险段加「一、」编号（2026-08-06 / 2026-08-17 已复现）。

    风险段标题在模板中本无编号（`## 🩺 今日风险（...）`），但模型偶发在首段
    前加「一、」→ SECTION_RE 截断为「🩺 一」→ 校验器报「缺失风险段 / 多余主段落」。
    此处剥离开头的中文数字编号 + 顿号，保留模板分隔符（如 `（`），确保标题仍含
    风险关键词。仅作用于 `## 🩺` 风险段，不动其他段落。
    """

    def repl(m: re.Match) -> str:
        rest = m.group(2)
        cleaned = re.sub(r"^[一二三四五六七八九十]+、", "", rest)
        return m.group(1) + cleaned

    return re.sub(r"^(##\s+🩺\s+)(.*)$", repl, md, flags=re.MULTILINE)


def _create_feishu_doc(title: str, content: str) -> str | None:
    """生成飞书文档（仅 user 身份，确保创建者即用户、文档可直接打开）。

    放弃 bot 兜底：bot 身份创建的文档因 app 缺 docs:permission.member:create scope
    无法授权给用户，链接打不开。user 失败时由调用方走群文件兜底。
    """
    identity = "user"
    args = [
        LARK_CLI,
        "docs",
        "+create",
        "--as",
        identity,
        "--doc-format",
        "markdown",
        "--title",
        title,
        "--content",
        content,
    ]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=120)
        out = r.stdout.strip()
        try:
            res = json.loads(out)
        except Exception:
            print(f"  [{identity}] raw: {out[:300]}", file=sys.stderr)
            return None
        if res.get("ok"):
            url = res["data"]["document"].get("url")
            print(f"  ✅ 文档已建 (user): {url}")
            return url
        print(
            f"  [{identity}] not ok: {json.dumps(res, ensure_ascii=False)[:200]}", file=sys.stderr
        )
    except Exception as e:
        print(f"  [{identity}] exc: {e}", file=sys.stderr)
    return None


def _send_full_report_file(md: str, chat_id: str, ymd: str) -> bool:
    """群文件兜底：完整 md 写 output/wx_reports/{ymd}_full.md 并作为群文件发送。

    用于 user 文档生成失败（如自动化环境 user 凭据不可用），确保完整报告始终可读。
    lark-cli --file 要求 cwd-relative 路径，故用相对 output/ 路径（cwd=Claw）。
    """
    rel = f"output/wx_reports/{ymd}_full.md"
    try:
        with open(rel, "w", encoding="utf-8") as f:
            f.write(md)
    except Exception as e:
        print(f"  ⚠️ 写群文件失败: {e}", file=sys.stderr)
        return False
    args = [
        LARK_CLI,
        "im",
        "+messages-send",
        "--as",
        "bot",
        "--chat-id",
        chat_id,
        "--file",
        rel,
        "--msg-type",
        "file",
    ]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=60)
        ok = ('"ok":true' in r.stdout) or ('"ok": true' in r.stdout)
        print(f"  {'✅' if ok else '🔴'} 群文件兜底 {'成功' if ok else '失败'}: {r.stdout[:120]}")
        return ok
    except Exception as e:
        print(f"  ⚠️ 群文件兜底异常: {e}", file=sys.stderr)
        return False


def _parse_md_to_sections(md: str) -> tuple:
    """粗略解析早报 md → (title, sections_list, level)

    按一级/二级标题切分区块；今日风险含 🔴 触发 → alert 红头。
    """
    lines = md.splitlines()
    title = "📊 微信早报"
    # 取首个含「早报」的行作标题
    for ln in lines[:15]:
        if "早报" in ln:
            title = ln.strip().lstrip("#").strip()
            break

    # 按 ## 或一级标题切分
    blocks = []
    cur_title, cur_body = None, []
    for ln in lines:
        m = re.match(r"^#{1,3}\s+(.*)", ln)
        if m and cur_title is not None:
            blocks.append((cur_title, "\n".join(cur_body).strip()))
            cur_title, cur_body = m.group(1).strip(), []
        elif m:
            cur_title = m.group(1).strip()
        else:
            cur_body.append(ln)
    if cur_title is not None:
        blocks.append((cur_title, "\n".join(cur_body).strip()))

    # 映射成卡片 sections（过滤空块、过滤「完整报告」段——它进按钮）
    sections = []
    level = "info"
    for bt, bb in blocks:
        if not bb:
            continue
        if "完整报告" in bt or "完整报告" in bb[:20]:
            continue
        # 风险段含 🔴 → 红头
        if ("风险" in bt or "止损" in bt) and "🔴" in bb:
            level = "alert"
        # 截断单块过长内容（卡片单 div 建议 < 2000 字）
        if len(bb) > 1800:
            bb = bb[:1800] + "\n…(详见完整报告)"
        sections.append((bt, bb))

    # 若没解析出区块，整篇当一段
    if not sections:
        sections = [("早报摘要", md[:1800])]

    return title, sections, level


def main():
    today = datetime.now()
    ymd = today.strftime("%Y%m%d")
    weekday = WEEKDAYS[today.weekday()]

    # 允许 --date 覆盖（调试/补推）
    if "--date" in sys.argv:
        idx = sys.argv.index("--date")
        ymd = sys.argv[idx + 1]
        # 推到周一..周五映射（简化）
        d = datetime.strptime(ymd, "%Y%m%d")
        weekday = WEEKDAYS[d.weekday()]

    md_path = f"/Users/guan/WorkBuddy/Claw/output/wx_reports/{ymd}_morning.md"
    if not os.path.exists(md_path):
        print(f"🔴 早报文件不存在: {md_path}")
        return 1

    with open(md_path, encoding="utf-8") as f:
        md = f.read()

    # 0) 规范化风险段标题（剥离开头「一、」编号），落盘 + 推送同源，避免校验告警
    md_norm = _normalize_risk_heading(md)
    if md_norm != md:
        print("🧹 风险段标题规范化（剥离误加编号），回写文件")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_norm)
        md = md_norm

    title = f"📊 微信早报 — {ymd[:4]}-{ymd[4:6]}-{ymd[6:]}（{weekday}）"

    # 1) 生成飞书文档（仅 user 身份，确保可打开）
    print(f"📄 生成飞书文档: {title}")
    doc_url = _create_feishu_doc(title, md)

    # 2) 解析 md → 卡片区块
    _, sections, level = _parse_md_to_sections(md)
    # 标题用文件首行
    first_line = md.splitlines()[0].strip().lstrip("#").strip()
    if first_line:
        title = first_line

    # 3) 按钮 + 失败兜底（doc 失败 → 群文件兜底，保证完整报告可读）
    buttons = []
    footer = "本报告仅供参考，不构成投资建议"
    if doc_url:
        buttons = [{"text": "📄 完整报告", "url": doc_url}]
    else:
        print("⚠️ 飞书文档生成失败 → 群文件兜底发送完整报告")
        if _send_full_report_file(md, DEFAULT_CHAT, ymd):
            footer = "完整报告已作为群文件发送（飞书文档生成失败，请查收群文件）"
        else:
            footer = "⚠️ 完整报告生成失败（文档+群文件均失败），请检查 lark-cli 权限"

    # 4) 发卡片
    sys.path.insert(0, SCRIPT_DIR)
    import push_card as pc

    print(f"📨 发送卡片 (level={level}, {len(sections)}区块)")
    ok = pc.send_card(
        title=title[:50],
        level=level,
        sections=sections,
        buttons=buttons,
        footer=footer,
        chat_id=DEFAULT_CHAT,
    )
    print("✅ 早报卡片推送完成" if ok else "🔴 早报推送失败")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
