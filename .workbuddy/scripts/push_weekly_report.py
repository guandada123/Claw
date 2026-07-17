#!/usr/bin/env python3
"""
push_weekly_report.py — 每周综合复盘统一推送脚本（卡片化 + 可点完整报告）
================================================================
复用 push_evening_report.py 的建文档+按钮逻辑，周报版：
  1. 读 output/wx_reports/{today}_weekly.md
  2. lark-cli docs +create 生成飞书文档（user→bot 降级），取真实 url
  3. 解析 md 标题层级 → 卡片 sections
  4. push_card.py 发 interactive 卡片，按钮指向真实 docx url

依赖：push_card.py（同目录）、lark-cli
支持 --date YYYYMMDD 覆盖（调试/补推）
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LARK_CLI = (
    os.path.expanduser("~/.workbuddy/binaries/node/versions/22.22.2/bin/lark-cli")
    if os.path.isfile(os.path.expanduser("~/.workbuddy/binaries/node/versions/22.22.2/bin/lark-cli"))
    else "lark-cli"
)
DEFAULT_CHAT = "oc_9ee5303497f5e0e71666b610d6bdc346"
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _create_feishu_doc(title: str, content: str) -> str | None:
    """生成飞书文档，返回 url（失败返回 None）"""
    for identity in ["user", "bot"]:
        args = [LARK_CLI, "docs", "+create", "--as", identity,
                 "--doc-format", "markdown", "--title", title, "--content", content]
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=120)
            out = r.stdout.strip()
            try:
                res = json.loads(out)
            except Exception:
                print(f"  [{identity}] raw: {out[:300]}", file=sys.stderr)
                continue
            if res.get("ok"):
                d = res["data"]["document"]
                url = d.get("url")
                print(f"  ✅ 文档已建 ({identity}): {url}")
                return url
            else:
                print(f"  [{identity}] not ok: {json.dumps(res, ensure_ascii=False)[:200]}", file=sys.stderr)
        except Exception as e:
            print(f"  [{identity}] exc: {e}", file=sys.stderr)
    return None


def _parse_md_to_sections(md: str) -> tuple:
    """解析周报 md → (title, sections_list, level)

    按 ## / ### 标题切分区块；运维异常/成功率低 → warning 橙头。
    """
    lines = md.splitlines()
    title = "📝 每周综合复盘"

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

    sections = []
    level = "info"
    for bt, bb in blocks:
        if not bb:
            continue
        # 数据来源/免责声明段不进卡片（太长且价值低）
        if bt.startswith("*数据") or "免责声明" in bt or "来源：" in bb[:30]:
            continue
        # 异常识别段含 <90% / PAUSED / 失败 → warning
        if ("异常" in bt or "成功率" in bt or "结论与行动" in bt) and ("<90%" in bb or "PAUSED" in bb or "失败" in bb or "异常" in bb):
            level = "warning"
        # 截断单块过长
        if len(bb) > 1800:
            bb = bb[:1800] + "\n…(详见完整报告)"
        sections.append((bt, bb))

    if not sections:
        sections = [("周报摘要", md[:1800])]

    return title, sections, level


def main():
    today = datetime.now()
    ymd = today.strftime("%Y%m%d")
    weekday = WEEKDAYS[today.weekday()]

    if "--date" in sys.argv:
        idx = sys.argv.index("--date")
        ymd = sys.argv[idx + 1]
        d = datetime.strptime(ymd, "%Y%m%d")
        weekday = WEEKDAYS[d.weekday()]

    md_path = f"/Users/guan/WorkBuddy/Claw/output/wx_reports/{ymd}_weekly.md"
    if not os.path.exists(md_path):
        # 兼容旧命名 weekly_review_YYYY-MM-DD.md
        alt_path = f"/Users/guan/WorkBuddy/Claw/output/weekly_review_{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}.md"
        if os.path.exists(alt_path):
            print(f"📄 使用旧命名路径: {alt_path}")
            md_path = alt_path
        else:
            print(f"🔴 周报文件不存在: {md_path}")
            return 1

    with open(md_path, encoding="utf-8") as f:
        md = f.read()

    title = f"📝 炒股助理·每周综合复盘 — {ymd[:4]}-{ymd[4:6]}-{ymd[6:]}（{weekday}）"

    # 1) 生成飞书文档
    print(f"📄 生成飞书文档: {title}")
    doc_url = _create_feishu_doc(title, md)
    if not doc_url:
        print("⚠️ 文档生成失败，卡片仍发（按钮降级为文字链接）")

    # 2) 解析 md → 卡片区块
    _, sections, level = _parse_md_to_sections(md)
    # 标题用文件首行（含「每周综合复盘」）
    first_line = md.splitlines()[0].strip().lstrip("#").strip()
    if first_line:
        title = first_line

    # 3) 按钮
    buttons = []
    if doc_url:
        buttons = [{"text": "📄 完整报告", "url": doc_url}]

    # 4) 发卡片
    sys.path.insert(0, SCRIPT_DIR)
    import push_card as pc
    print(f"📨 发送卡片 (level={level}, {len(sections)}区块)")
    ok = pc.send_card(
        title=title[:50],
        level=level,
        sections=sections,
        buttons=buttons,
        footer="本报告仅供参考，不构成投资建议",
        chat_id=DEFAULT_CHAT,
    )
    print("✅ 周报卡片推送完成" if ok else "🔴 周报推送失败")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
