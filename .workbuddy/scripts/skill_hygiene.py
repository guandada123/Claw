#!/usr/bin/env python3
"""skill_hygiene.py — 技能卫生审计(重建版, 非破坏性)。

扫描 skills 目录, 检测四类问题:
  1) 重复文件      — 按内容 sha256 哈希找副本
  2) 坏链          — 指向不存在目标的符号链接
  3) 未用脚本      — 本 skill 内未被任何 *.md 引用的 .py/.sh(启发式, 可能误报)
  4) prompt 注入   — 匹配常见注入模式的文本文件

输出: JSON 报告到 stdout, 并写 <data>/skill_hygiene_report.json。
铁律: 不修改/不删除任何文件(禁区不碰, 非破坏性)。

⚠️ 重建版: 检测规则为通用启发式, 报告仅供人工复核, 不直接执行清理。
"""
import datetime
import hashlib
import json
import os
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
SKILL_DIRS = [
    BASE / ".workbuddy" / "skills",
    Path.home() / ".workbuddy" / "skills",
]
INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.I),
    re.compile(r"忽略\s*(之前|先前|以上|前面)\s*(的)?\s*指令", re.I),
    re.compile(r"disregard\s+(the\s+)?(above|previous)", re.I),
    re.compile(r"system\s*:\s*you\s+are", re.I),
]


def sha256(p):
    h = hashlib.sha256()
    try:
        with open(p, "rb") as f:
            for b in iter(lambda: f.read(65536), b""):
                h.update(b)
        return h.hexdigest()
    except Exception:
        return None


def scan():
    report = {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "scanned_skills": 0,
        "duplicates": [],
        "broken_symlinks": [],
        "unused_scripts": [],
        "injection_hits": [],
    }
    hash_map = {}

    for sdir in SKILL_DIRS:
        if not sdir.exists():
            continue
        for skill in sdir.iterdir():
            if not skill.is_dir():
                continue
            report["scanned_skills"] += 1
            for root, _, files in os.walk(skill):
                for fn in files:
                    fp = Path(root) / fn
                    if fp.is_symlink() and not fp.exists():
                        report["broken_symlinks"].append(str(fp))
                        continue
                    try:
                        h = sha256(fp)
                        if h:
                            hash_map.setdefault(h, []).append(str(fp))
                    except Exception:
                        pass
                    if fn.endswith((".md", ".py", ".sh", ".txt", ".json")):
                        try:
                            txt = fp.read_text(errors="ignore")
                            for pat in INJECTION_PATTERNS:
                                if pat.search(txt):
                                    report["injection_hits"].append(
                                        {"file": str(fp), "pattern": pat.pattern}
                                    )
                                    break
                        except Exception:
                            pass

    for h, paths in hash_map.items():
        if len(paths) > 1:
            report["duplicates"].append(paths)

    # 未用脚本(启发式): skill 内 *.py/*.sh 未在其任一 *.md 文本中出现文件名
    for sdir in SKILL_DIRS:
        if not sdir.exists():
            continue
        for skill in sdir.iterdir():
            if not skill.is_dir():
                continue
            mds = [str(p) for p in skill.rglob("*.md")]
            refs = " ".join(Path(m).read_text(errors="ignore") for m in mds)
            for sc in list(skill.rglob("*.py")) + list(skill.rglob("*.sh")):
                if sc.name in ("__init__.py",):
                    continue
                if sc.name not in refs:
                    report["unused_scripts"].append(str(sc))

    return report


def main():
    r = scan()
    out = BASE / ".workbuddy" / "data" / "skill_hygiene_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(r, ensure_ascii=False, indent=2))
    print(json.dumps({
        "scanned_skills": r["scanned_skills"],
        "duplicates": len(r["duplicates"]),
        "broken_symlinks": len(r["broken_symlinks"]),
        "unused_scripts": len(r["unused_scripts"]),
        "injection_hits": len(r["injection_hits"]),
        "report": str(out),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
