#!/usr/bin/env python3
"""learnings_index.py — 由 `.learnings/*.md` **派生**出索引（`.learnings/INDEX.md`）。

━━ 为什么需要（2026-10-11 实测）━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
`.learnings/` 里有两个产物：
  · `YYYY-MM-DD-<slug>.md` —— 一次教训一篇（**一直在写**）
  · `LEARNINGS.md`         —— 手工浓缩条目（**早就停更**）

实测：Claw 索引最后条目 **08-31**、QTS **09-06**，而 dated 文件都到了 **10-11**
→ 索引落后 5~6 周。浏览索引的人会以为"最近的教训停在 8 月"。

根因不是"忘了更新"，而是**同一份信息存了两处、只维护一处**。
→ 处置：把索引改成**派生物**（本脚本生成 `INDEX.md`），手工那份标注为非权威。
   「同一语义的值必须单一真源」在文档层同样成立 —— **能派生就别手抄**。

用法：
  python3 learnings_index.py --dir <repo>/.learnings          # 生成 INDEX.md（stdout 摘要）
  python3 learnings_index.py --dir ... --check                # 只检查是否最新（rc 1 = 落后），不写
退出码：0 正常 / 1 `--check` 发现落后 / 2 目录不可读
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path

# 头部块：`# 标题` + `**日期**：…` / `**等级**：…` / `**类型**：…` / `**状态**：…`
HEAD_FIELDS = ("日期", "等级", "类型", "状态")
TITLE_RE = re.compile(r"^#\s+(.+?)\s*$")
FIELD_RE = {f: re.compile(rf"^\*\*{f}\*\*[：:]\s*(.+?)\s*$") for f in HEAD_FIELDS}
DATED_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)\.md$")


def parse_entry(p: Path) -> dict:
    """从一篇 dated 文件里抽出可机读的头部字段（缺就留空，**不编造**）。"""
    m = DATED_RE.match(p.name)
    rec = {
        "file": p.name,
        "date": m.group(1) if m else "",
        "slug": m.group(2) if m else p.stem,
        "title": "",
        "日期": "",
        "等级": "",
        "类型": "",
        "状态": "",
    }
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return rec
    for line in text.splitlines()[:40]:  # 头部块只在前若干行
        if not rec["title"]:
            t = TITLE_RE.match(line)
            if t:
                rec["title"] = t.group(1)
                continue
        for f, rx in FIELD_RE.items():
            if not rec[f]:
                hit = rx.match(line)
                if hit:
                    rec[f] = hit.group(1)
        if all(rec[k] for k in ("title", *HEAD_FIELDS)):
            break
    return rec


def collect(dirpath: Path) -> list[dict]:
    items = [parse_entry(p) for p in sorted(dirpath.glob("*.md")) if DATED_RE.match(p.name)]
    items.sort(key=lambda r: (r["date"], r["slug"]), reverse=True)
    return items


def render(items: list[dict], repo_name: str, now: datetime.datetime) -> str:
    by_type: dict[str, int] = {}
    by_level: dict[str, int] = {}
    for r in items:
        by_type[r["类型"].split("（")[0].strip() or "(未标)"] = (
            by_type.get(r["类型"].split("（")[0].strip() or "(未标)", 0) + 1
        )
        by_level[r["等级"].split("（")[0].strip() or "(未标)"] = (
            by_level.get(r["等级"].split("（")[0].strip() or "(未标)", 0) + 1
        )

    def _stat(d: dict[str, int]) -> str:
        """只列**已标注**的取值；未标注的另起一句（不混进主行，免得一屏"(未标)×66"）。"""
        named = {k: v for k, v in d.items() if k != "(未标)"}
        unlabeled = d.get("(未标)", 0)
        parts = (
            "　".join(f"{k}×{v}" for k, v in sorted(named.items(), key=lambda x: -x[1])) or "（无）"
        )
        return parts + (f"　（另有 {unlabeled} 条未标注该字段）" if unlabeled else "")

    out = [
        f"# Learnings INDEX — {repo_name}",
        "",
        "> ⚠️ **本文件由 `learnings_index.py` 自动生成，请勿手改**（改了会在下次生成时被覆盖）。",
        "> 权威内容是 `.learnings/*.md` 正文；本文件只是**派生视图**。",
        "> ⚠️ 手工维护的 `LEARNINGS.md` 里的**浓缩条目不在本表内**（它已停更，见其抬头说明）。",
        f"> 生成时间：{now:%Y-%m-%d %H:%M}　｜　条目数：**{len(items)}**",
        "",
        f"- **按类型**：{_stat(by_type)}",
        f"- **按等级**：{_stat(by_level)}",
        "",
        "| 日期 | 标题 | 等级 | 类型 | 状态 | 文件 |",
        "|---|---|---|---|---|---|",
    ]
    for r in items:
        title = (r["title"] or r["slug"]).replace("|", "/")
        out.append(
            f"| {r['date']} | {title} | {r['等级'] or '—'} | {r['类型'] or '—'} "
            f"| {r['状态'] or '—'} | `{r['file']}` |"
        )
    out.append("")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="仓库的 .learnings 目录")
    ap.add_argument("--out", default="INDEX.md", help="输出文件名（默认 INDEX.md）")
    ap.add_argument("--check", action="store_true", help="只比对是否最新（rc 1 = 落后），不写")
    args = ap.parse_args(argv[1:])

    # ⚠️ 先 resolve：相对路径（如 `--dir .learnings`）下 `d.parent.name` 会是空串，仓库名就丢了
    d = Path(args.dir).expanduser().resolve()
    if not d.is_dir():
        print(f"[learnings-index] 无法判定：{d} 不是目录", file=sys.stderr)
        return 2
    items = collect(d)
    if not items:
        print(f"[learnings-index] 无法判定：{d} 下没有 `YYYY-MM-DD-*.md`", file=sys.stderr)
        return 2

    repo_name = d.parent.name
    now = datetime.datetime.now()
    content = render(items, repo_name, now)
    target = d / args.out

    if args.check:
        old = target.read_text(encoding="utf-8", errors="ignore") if target.exists() else ""
        # 只比"条目行数"这一件可判定的事（生成时间每次都会变，不能拿来比）
        n_old = len([ln for ln in old.splitlines() if ln.startswith("| 20")])
        if n_old != len(items):
            print(f"[learnings-index] {repo_name}: INDEX 条目 {n_old} ≠ 实际 {len(items)} → 落后")
            return 1
        print(f"[learnings-index] {repo_name}: 最新（{len(items)} 条）")
        return 0

    target.write_text(content + "\n", encoding="utf-8")
    print(f"[learnings-index] {repo_name}: 已生成 {target.name}，{len(items)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
