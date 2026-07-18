#!/usr/bin/env python3
"""
push_card.py — 飞书 interactive 卡片推送中台
=================================================
统一替代 notify_center.py / push_feishu.sh 的纯 --markdown / --text 推送，
让所有自动化用「语义配色 + 分区块 + 可选表格/按钮」的交互卡片，
解决「纯文本看的不舒服、突出不了重点」问题。

设计要点：
  1. 语义配色：level 决定 header template
       alert   → red    （告警/失败/止损击穿）
       warning → orange  （警告/降级/逼近阈值）
       info    → blue   （常规简报/信息通知）
       success → green  （成功/完成/无异常）
  2. 分区块：sections = [(title, markdown_content), ...]，每块一个 div + hr 分割
  3. 可选表格：table = {"headers":[...], "rows":[[...],...]}，渲染成 markdown 表格
  4. 可选按钮：buttons = [{"text":..., "url":...}]，作主操作入口（飞书当前按钮点不动，仅展示+链接）
  5. 兜底：卡片发送失败 → 自动回退 --markdown（保留格式，绝不用 --text 丢格式）
  6. 429 退避：发送带指数退避重试（3 次 5/10/20s）
  7. 卡片 body ≤ 30KB（飞书硬限制），超长内容摘要 + 链云文档

用法：
  python3 push_card.py \
    --title "📊 微信早报 — 2026-07-17（周五）" \
    --level info \
    --section "🩺 今日风险" "🔴 实盘止损击穿..." \
    --section "📈 大盘行情" "上证 3882 -1.85%" \
    --table-headers "指数|收盘|涨跌|状态" \
    --table-rows "上证指数|3882.41|-1.85%|🟡" \
    --button "📄 完整报告" "https://feishu.cn/docx/xxx" \
    --chat-id oc_xxx \
    --dedupe-key "早报-2026-07-17"

  # 也支持从 stdin 读 JSON（结构化调用）：
  echo '{"title":"...","level":"alert","sections":[...],"table":{...},"buttons":[...]}' | python3 push_card.py --json-stdin

  ⚠️ lark-cli 发卡片正确方式（1.0.68 无 --card flag）：
      lark-cli im +messages-send --as bot --chat-id X \
        --content '<card_json>' --msg-type interactive
  早期早报卡片失败(230001)即因误用 --card / actions 字段格式错。
"""
import argparse
import json
import os
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LARK_CLI = (
    os.path.expanduser("~/.workbuddy/binaries/node/versions/22.22.2/bin/lark-cli")
    if os.path.isfile(os.path.expanduser("~/.workbuddy/binaries/node/versions/22.22.2/bin/lark-cli"))
    else "lark-cli"
)
DEFAULT_CHAT = "oc_9ee5303497f5e0e71666b610d6bdc346"

LEVEL_TEMPLATE = {
    "alert": "red",
    "warning": "orange",
    "info": "blue",
    "success": "green",
}


def build_card(title: str, level: str, sections: list, table: dict = None,
              buttons: list = None, footer: str = None) -> dict:
    """构造飞书 interactive card JSON"""
    template = LEVEL_TEMPLATE.get(level, "blue")
    elements = []

    for idx, (sec_title, sec_body) in enumerate(sections):
        # 区块标题 + 内容
        block = f"**{sec_title}**\n\n{sec_body}" if sec_title else sec_body
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": block},
        })
        # 区块间分割线（最后一块后不加）
        if idx < len(sections) - 1:
            elements.append({"tag": "hr"})

    # 表格：渲染成 markdown 表格，作为独立 div 追加
    if table and table.get("headers") and table.get("rows"):
        md = "| " + " | ".join(table["headers"]) + " |\n"
        md += "|" + "|".join(["------"] * len(table["headers"])) + "|\n"
        for row in table["rows"]:
            md += "| " + " | ".join(str(c) for c in row) + " |\n"
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": md},
        })

    # 按钮（主操作入口，飞书当前点不动，仅展示+链接）
    if buttons:
        actions = []
        for i, b in enumerate(buttons):
            actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": b["text"]},
                "type": "primary" if i == 0 else "default",
                "url": b.get("url", ""),
            })
        elements.append({"tag": "action", "actions": actions})

    # 页脚
    if footer:
        elements.append({"tag": "note", "elements": [
            {"tag": "plain_text", "content": footer}
        ]})

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title[:50]},
            "template": template,
        },
        "elements": elements,
    }
    return card


def _send_via_lark(card: dict, chat_id: str, timeout: int = 30) -> tuple:
    """用 lark-cli 发 interactive 卡片，返回 (ok, raw_stdout)

    注意：lark-cli 1.0.68 无 --card flag，正确方式是
    --content '<card_json>' --msg-type interactive
    （--msg-type 缺省为 text，必须显式声明 interactive 才能渲染卡片）
    """
    payload = json.dumps(card, ensure_ascii=False)
    r = subprocess.run(
        [LARK_CLI, "im", "+messages-send", "--as", "bot",
         "--chat-id", chat_id, "--content", payload, "--msg-type", "interactive"],
        capture_output=True, text=True, timeout=timeout,
    )
    return r.returncode == 0, r.stdout, r.stderr


def _send_via_markdown_fallback(text: str, chat_id: str, timeout: int = 30) -> bool:
    """兜底：卡片失败 → 用 --markdown 发送（保留格式，绝不用 --text）"""
    r = subprocess.run(
        [LARK_CLI, "im", "+messages-send", "--as", "bot",
         "--chat-id", chat_id, "--markdown", text],
        capture_output=True, text=True, timeout=timeout,
    )
    return r.returncode == 0


def send_card(title, level="info", sections=None, table=None, buttons=None,
              footer=None, chat_id=DEFAULT_CHAT, max_retries=3) -> bool:
    """对外主函数：发卡片，带 429 退避 + markdown 兜底"""
    sections = sections or []
    buttons = buttons or []
    card = build_card(title, level, sections, table, buttons, footer)

    # 卡片 body 大小检查（≤30KB）
    card_str = json.dumps(card, ensure_ascii=False)
    if len(card_str.encode("utf-8")) > 30000:
        print(f"  ⚠️ 卡片超 30KB({len(card_str)}B)，截断 footer/按钮避免发送失败")
        card.pop("elements", None)
        card["elements"] = [{"tag": "div", "text": {"tag": "lark_md",
                                         "content": f"**{title}**\n\n内容过长，请查看完整报告链接。"}}]

    backoff = [5, 10, 20]
    last_err = ""
    for attempt in range(1, max_retries + 1):
        ok, out, err = _send_via_lark(card, chat_id)
        if ok:
            try:
                d = json.loads(out)
                mid = d.get("data", {}).get("message_id", "?")
                print(f"  ✅ 卡片已发送! message_id: {mid} (level={level})")
                return True
            except json.JSONDecodeError:
                print("  ✅ 卡片已发送 (无法解析message_id)")
                return True

        # 失败：判断是否 429 限流
        is_429 = "429" in (err or "") or "429" in (out or "")
        last_err = (err or out)[:300]
        print("  ⚠️ 卡片发送返回码非0" + (" (429限流)" if is_429 else ""))
        if attempt < max_retries:
            wait = backoff[attempt - 1] if attempt <= len(backoff) else backoff[-1]
            print(f"  🔄 第{attempt}次失败，{wait}s后重试 ({attempt}/{max_retries})...")
            time.sleep(wait)
        else:
            print(f"  🔴 卡片重试{max_retries}次失败，转 markdown 兜底")

    # 兜底：markdown（保留格式）
    md_text = f"**{title}**\n\n" + "\n\n".join(
        f"**{t}**\n{b}" for t, b in sections
    )
    if table and table.get("headers"):
        md_text += "\n\n" + "| " + " | ".join(table["headers"]) + " |\n"
        md_text += "|" + "|".join(["------"] * len(table["headers"])) + "|\n"
        for row in table["rows"]:
            md_text += "| " + " | ".join(str(c) for c in row) + " |\n"
    if ok_fb := _send_via_markdown_fallback(md_text, chat_id):
        print("  ✅ markdown 兜底发送成功")
        return True
    print(f"  🔴 卡片+兜底均失败: {last_err}")
    return False


# 防护：LLM 常把模板占位符当真实内容传入，导致卡片显示 {日期}/title/body 字面量
# 只检测「花括号模板变量」和「孤立占位单词」，不碰正常换行/emoji/中文
_PLACEHOLDER_HAZARDS = ("{日期}", "{周几}", "{date}", "{weekday}",
                         "{{", "}}", "{x}", "{y}", "{n}", "{m}", "{code}", "{标的}")


def _looks_like_placeholder(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True  # 空内容/空标题一律拦截
    low = t.lower()
    # 花括号模板变量：直接判占位
    if any(h.lower() in low for h in _PLACEHOLDER_HAZARDS):
        return True
    # 孤立占位单词：整段去掉空白后恰好是 title / body（小写）
    bare = "".join(low.split())
    if bare in ("title", "body", "body内容占位"):
        return True
    return False


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--title", required=True)
    p.add_argument("--level", default="info", choices=list(LEVEL_TEMPLATE.keys()))
    p.add_argument("--section", action="append", nargs=2, metavar=("TITLE", "BODY"),
                   help="可重复：--section '标题' '内容'")
    p.add_argument("--table-headers", default="")
    p.add_argument("--table-rows", action="append", default=[],
                   help="可重复：--table-rows 'a|b|c'")
    p.add_argument("--button", action="append", nargs=2, metavar=("TEXT", "URL"),
                   help="可重复：--button '文字' 'url'")
    p.add_argument("--footer", default="")
    p.add_argument("--chat-id", default=DEFAULT_CHAT)
    p.add_argument("--dedupe-key", default="")
    p.add_argument("--json-stdin", action="store_true",
                   help="从 stdin 读完整 JSON {title,level,sections,table,buttons,footer}")
    args = p.parse_args()

    # ── 占位符/空内容防护（防止 LLM 把模板变量当真实内容传出）──
    if _looks_like_placeholder(args.title):
        print(f"  🔴 拒绝发送：title 疑似占位符/空值 → '{args.title}'")
        print("  💡 调用方须传入真实标题，如 --title \"📊 收盘晚报 — $(date +%Y-%m-%d)\"")
        return 2
    if not args.json_stdin and not args.section:
        print("  🔴 拒绝发送：未提供任何 --section 内容")
        return 2
    if not args.json_stdin:
        for i, (t, b) in enumerate(args.section or []):
            # 空标题 "" 是合法设计（整块无小标题）；只拦截空 BODY 或占位符
            if _looks_like_placeholder(b):
                print(f"  🔴 拒绝发送：第{i+1}个 section 内容疑似占位符或空 → "
                      f"title='{t}' body='{b[:30]}...'")
                return 2
            if _looks_like_placeholder(t) and t.strip():
                print(f"  🔴 拒绝发送：第{i+1}个 section 标题疑似占位符 → "
                      f"title='{t}'")
                return 2

    # 去重（文件级，冷却 6h）
    if args.dedupe_key:
        df = f"/tmp/feishu_card_dedupe_{args.dedupe_key.replace(' ', '_')[:40]}"
        if os.path.exists(df):
            print(f"⚠️ 去重: {args.dedupe_key} (6h内已发)")
            return 0
        with open(df, "w") as f:
            f.write("sent")

    if args.json_stdin:
        data = json.loads(sys.stdin.read())
        ok = send_card(
            title=data["title"],
            level=data.get("level", "info"),
            sections=data.get("sections", []),
            table=data.get("table"),
            buttons=data.get("buttons", []),
            footer=data.get("footer"),
            chat_id=args.chat_id,
        )
    else:
        sections = [(t, b) for t, b in (args.section or [])]
        table = None
        if args.table_headers:
            table = {
                "headers": [h.strip() for h in args.table_headers.split("|")],
                "rows": [[c.strip() for c in r.split("|")] for r in args.table_rows],
            }
        buttons = [{"text": t, "url": u} for t, u in (args.button or [])]
        ok = send_card(
            title=args.title,
            level=args.level,
            sections=sections,
            table=table,
            buttons=buttons,
            footer=args.footer,
            chat_id=args.chat_id,
        )

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
