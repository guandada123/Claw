#!/usr/bin/env python3
"""scan_repo_credentials.py — 产物型 git 仓库的凭据泄漏守卫

━━ 为什么需要（2026-10-09 实证）━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
给技能库（`~/.workbuddy/skills`）建独立 git 时，`.gitignore` 只排了构建/缓存/备份三类，
**漏了凭据** → 基线快照把 `.neodata_token`（`{"token":"<35B>","saved_at":…}`）
连同其 blob 一并纳入版本控制。属「建立版本控制」这个动作自身引入的次级缺口。

危险点在于它**不报错、不告警**：仓库照样 commit、照样 clean，
只有把仓库 clone / push / bundle 到别处时才泄漏。所以必须由机器定期扫。

━━ 检查三件事 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  L1 索引/工作区：`git ls-files` 里有没有凭据类文件
  L2 对象库（含历史）：`git rev-list --objects --all` 里有没有凭据路径
     —— 只看 L1 会漏「已从索引移除但仍在历史里」的情形（本次事故正是如此）
  L3 .gitignore 覆盖：凭据模式是否已被显式忽略（防复发）

━━ 用法 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  python3 scan_repo_credentials.py                      # 扫默认仓库
  python3 scan_repo_credentials.py <repo> [<repo> ...]  # 扫指定仓库
  python3 scan_repo_credentials.py --json               # 机器可读

退出码：0=干净 / 1=有发现 / 2=参数错或不是 git 仓库
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_REPOS = [
    Path.home() / ".workbuddy" / "skills",
]

# 凭据类文件名（按 basename 匹配，忽略大小写）
CRED_NAME_RE = re.compile(
    r"""^(?:
        \.env(?:\..+)?            # .env / .env.local …
      | \.neodata_token
      | .*_token(?:\..+)?         # foo_token / foo_token.json
      | .*[-_]?(?:secret|credential)s?(?:\..+)?    # secret / credentials.json
      | .*\.(?:pem|key|p12|pfx|jks|keystore)
      | id_(?:rsa|dsa|ecdsa|ed25519)(?:\.pub)?
      | \.npmrc|\.pypirc|\.netrc|\.git-credentials
    )$""",
    re.IGNORECASE | re.VERBOSE,
)

# 这些后缀是模板/占位，不构成泄漏
SAFE_SUFFIX_RE = re.compile(r"\.(?:example|sample|template|dist|md|mdx|txt)$", re.IGNORECASE)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _is_credential_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    if SAFE_SUFFIX_RE.search(name):
        return False
    return bool(CRED_NAME_RE.match(name))


def scan(repo: Path) -> dict:
    out: dict = {"repo": str(repo), "ok": True, "findings": [], "ignored_patterns": []}
    if not (repo / ".git").exists():
        r = _git(repo, "rev-parse", "--git-dir")
        if r.returncode != 0:
            out["ok"] = False
            out["error"] = "不是 git 仓库"
            return out

    # L1 索引 / 工作区已跟踪文件
    r = _git(repo, "ls-files")
    tracked = [ln for ln in r.stdout.splitlines() if ln.strip()]
    for p in tracked:
        if _is_credential_path(p):
            out["findings"].append(
                {"layer": "L1-index", "path": p, "why": "凭据类文件已被 git 跟踪"}
            )

    # L2 对象库（含全部历史 —— 只看 L1 会漏「已移出索引但仍在历史」）
    r = _git(repo, "rev-list", "--objects", "--all")
    seen_paths: set[str] = set()
    for ln in r.stdout.splitlines():
        parts = ln.split(" ", 1)
        if len(parts) < 2:
            continue
        p = parts[1].strip()
        if not p or p in seen_paths:
            continue
        seen_paths.add(p)
        if _is_credential_path(p):
            out["findings"].append(
                {"layer": "L2-objectdb", "path": p, "why": "路径存在于对象库（含历史提交）"}
            )

    # L3 .gitignore 覆盖（只看回显的凭据名是否被覆盖）
    gi = repo / ".gitignore"
    gi_text = gi.read_text(encoding="utf-8", errors="ignore") if gi.is_file() else ""
    for pat in (".neodata_token", ".env", "*.pem", "*.key", "*_token"):
        if pat in gi_text:
            out["ignored_patterns"].append(pat)

    out["counts"] = {
        "tracked_files": len(tracked),
        "object_paths": len(seen_paths),
        "findings": len(out["findings"]),
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repos", nargs="*", type=Path)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    repos = args.repos or DEFAULT_REPOS
    missing = [str(r) for r in repos if not r.exists()]
    if missing:
        print(f"[ERROR] 路径不存在: {missing}", file=sys.stderr)
        return 2

    report = [scan(r) for r in repos]
    total = sum(len(x.get("findings", [])) for x in report)

    if args.json:
        print(json.dumps({"repos": report, "total_findings": total}, ensure_ascii=False, indent=2))
    else:
        for x in report:
            if not x.get("ok"):
                print(f"⚠️ {x['repo']} → {x.get('error')}")
                continue
            mark = "🔴" if x["findings"] else "✅"
            print(
                f"{mark} {x['repo']}  (跟踪 {x['counts']['tracked_files']} 文件 / "
                f"对象库 {x['counts']['object_paths']} 路径)"
            )
            for f in x["findings"]:
                print(f"     [{f['layer']}] {f['path']} — {f['why']}")
            print(f"     .gitignore 覆盖: {x['ignored_patterns'] or '无'}")
        print(f"\n合计发现: {total}")

    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
