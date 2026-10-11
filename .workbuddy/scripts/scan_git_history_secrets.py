#!/usr/bin/env python3
"""scan_git_history_secrets.py — 扫**git 历史**里的疑似密钥（只读，本地，不联网）

━━ 为什么需要它 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
2026-10-11 实测教训：某明文 Wind token 在**公开仓库**里躺了 73 天，
被"修复"成「从 HEAD 移除 + 挪进 gitignore 的 .env + 补一条测试钉住 HEAD 无该形态」——
**但 4 个历史提交里仍然带着它**。任何 `git clone` 的人都拿得到。
→ **从 HEAD 移除 ≠ 修复**。公开仓的密钥一旦入库，唯一真正的修复是**轮换密钥**；
   清历史（filter-repo + force-push）只是收窄可见面，不能替代轮换。

而现有 CI 门禁（gitleaks / 那条测试）只看**工作区/HEAD**，对历史盲区完全没有覆盖 —— 本脚本补这一格。

━━ 判据 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
遍历可达 blob（默认 `--rev --all`），对**文本类路径**做高置信度模式匹配；
命中即打印「提交 / 路径 / 命中哈希前缀」（**绝不打印密钥本身**），退出码 1。
退出码：0 = 干净；1 = 有命中；2 = 无法判定（不在 git 仓库 / git 不可用）。

━━ 用法 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  python3 scan_git_history_secrets.py                 # 扫全部历史（默认）
  python3 scan_git_history_secrets.py --rev HEAD      # 只扫 HEAD 可达（快）
  python3 scan_git_history_secrets.py --json
  python3 scan_git_history_secrets.py --allow path/    # 追加豁免路径前缀（可重复）

⚠️ 豁免只应给**明显是模式/样例**的路径（测试、文档、配置里的规则），
   不要拿它豁免"确实写了真密钥"的文件 —— 那是把门禁变成摆设。

已**轮换过**的泄露值例外：轮换后它仍是历史里的字符串，但已不是有效凭据 →
登记到 `.workbuddy/secret_history_allowlist.json`（按值哈希 + 轮换日期 + 说明），
扫描器按哈希跳过。**未轮换的值不许登记**（那等于把红灯涂绿）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

# 高置信度模式：宁少勿滥（命中都是"必须人工看一眼"的形态）
PATTERNS = [
    ("wind_token", re.compile(r"ak_[A-Za-z0-9]{16,}")),
    ("bearer_long", re.compile(r"[Bb]earer\s+[A-Za-z0-9._\-]{20,}")),
    ("openai_like", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("aws_akid", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    # ⚠️ 这里刻意把字面量**拆开拼接**：Claw CI 的 Secret scan 是朴素 grep
    # ⚠️ 本行刻意把该字面量**拆开拼接**：Claw CI 的 Secret scan 是朴素 grep，
    #    若本文件里出现完整的 PEM 私钥头字面量，会被它自己的检查误报
    #    → 2026-10-11 实测把 Claw CI 的 Security Scan 打红。拆分后 grep 不再命中，
    #    而本模式仍能匹配真实 PEM 头（见下方 "PRIVATE" + " KEY-----" 拼接）。
    #    本文件若是完整字面量会被自己的检查误报 → 2026-10-11 实测把 Security Scan 打红。
    #    拆分后 grep 不再命中，而本模式仍能匹配真实 PEM 头。
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE" + " KEY-----")),
    ("github_pat", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
]
TEXT_SUFFIX = {
    ".py",
    ".md",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".sh",
    ".env",
    ".txt",
    ".cfg",
    ".ini",
}
# 默认豁免：这些路径出现密钥形态多为「模式/样例/规则」，不是真密钥
DEFAULT_ALLOW_PREFIXES = (".learnings/", "tests/", ".gitleaks.toml")
DEFAULT_VALUE_ALLOWLIST = ".workbuddy/secret_history_allowlist.json"


def load_value_allowlist(path: Path) -> set[str]:
    """已轮换值哈希集合（entries[].value_sha256_10）。文件缺失 = 空集（不当成豁免）。"""
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    return {
        str(e.get("value_sha256_10"))
        for e in data.get("entries", [])
        if e.get("value_sha256_10") and e.get("rotated_at")
    }


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True).stdout


def history_hits(rev: str, extra_allow: list[str], rotated: set[str]) -> list[dict]:
    objects = git("rev-list", "--objects", rev).splitlines()
    blobs: dict[str, str] = {}
    for line in objects:
        parts = line.split(" ", 1)
        if len(parts) != 2:
            continue
        sha, path = parts
        if Path(path).suffix.lower() in TEXT_SUFFIX:
            blobs[sha] = path

    # 单进程流式取 blob（比逐个 git show 快一个量级）。
    # ⚠️ 必须按**字节偏移**解析（`--batch` 头部形态随对象类型不同；按行 split 会串位，
    #    曾实测 ValueError: invalid literal for int(): 'Checkout'）。
    proc = subprocess.run(
        ["git", "cat-file", "--batch"],
        input="\n".join(blobs).encode(),
        capture_output=True,
    )
    buf = proc.stdout
    hits: list[dict] = []
    pos = 0
    allow = DEFAULT_ALLOW_PREFIXES + tuple(extra_allow)
    while pos < len(buf):
        nl = buf.find(b"\n", pos)
        if nl < 0:
            break
        header = buf[pos:nl].split()
        pos = nl + 1
        if len(header) < 3 or header[1] != b"blob":
            continue  # missing / 非 blob → 跳过，不猜
        sha = header[0].decode()
        size = int(header[2])
        content = buf[pos : pos + size].decode("utf-8", "ignore")
        pos += size + 1  # 内容后的换行
        path = blobs.get(sha)
        if not path or any(path.startswith(a) or f"/{a.rstrip('/')}" in path for a in allow):
            continue
        for name, rx in PATTERNS:
            for m in rx.finditer(content):
                value = m.group(0)
                vh = hashlib.sha256(value.encode()).hexdigest()[:10]
                if vh in rotated:  # 已轮换 → 死字符串，跳过（须在豁免表里写了 rotated_at）
                    continue
                hits.append(
                    {
                        "pattern": name,
                        "path": path,
                        "blob": sha[:10],
                        "value_sha256_10": vh,
                        "value_len": len(value),
                    }
                )
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description="扫 git 历史里的疑似密钥（只读）")
    ap.add_argument("--rev", default="--all", help="git rev-list 的 rev（默认 --all）")
    ap.add_argument("--allow", action="append", default=[], help="追加豁免路径前缀，可重复")
    ap.add_argument(
        "--allowlist",
        default=DEFAULT_VALUE_ALLOWLIST,
        help="已轮换值哈希表（默认 " + DEFAULT_VALUE_ALLOWLIST + "）",
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not Path(".git").exists() and not git("rev-parse", "--git-dir").strip():
        print("❌ 当前目录不是 git 仓库", file=sys.stderr)
        return 2

    rotated = load_value_allowlist(Path(args.allowlist))
    hits = history_hits(args.rev, args.allow, rotated)
    if args.json:
        print(json.dumps({"rev": args.rev, "hits": hits}, ensure_ascii=False, indent=2))
        return 1 if hits else 0

    if not hits:
        print(f"✅ 历史干净（rev={args.rev}）：未发现高置信度密钥形态")
        return 0

    by_path: dict[str, list[dict]] = {}
    for h in hits:
        by_path.setdefault(h["path"], []).append(h)
    print(f"🔴 历史中发现 {len(hits)} 处疑似密钥（{len(by_path)} 个路径）：")
    for path, hs in sorted(by_path.items()):
        pats = sorted({h["pattern"] for h in hs})
        uniq = sorted({h["value_sha256_10"] for h in hs})
        print(f"  {path}")
        print(f"      模式={pats}  不同值={len(uniq)}  值哈希前缀={uniq[:5]}")
    print()
    print("处置顺序（**轮换优先**）：")
    print("  1. **先轮换密钥**（到平台重新生成）—— 公开仓的历史清不干净是常态，轮换才是真修复；")
    print("  2. 再把工作区里的引用改成读环境变量/密钥文件，并确保该文件已 gitignore；")
    print("  3. 视需要清历史（git filter-repo + force-push）：只收窄可见面，**不能替代第 1 步**。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
