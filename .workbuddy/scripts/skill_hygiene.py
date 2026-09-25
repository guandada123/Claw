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

🛠️ 2026-09-23 审计后改进:
  - 排除噪声目录(.venv/__pycache__/node_modules/site-packages/.git/tests/dist/build 等),
    不再把虚拟环境/缓存/依赖/构建产物/测试桩误报为「未用脚本」或「重复文件」。
  - 重复分类:
      * mirror_duplicates — 同一文件跨两个合法 store 根(Claw store vs 全局 ~/.workbuddy store)
        的同路径同内容副本, 属设计镜像, 不可删(仅作信息展示)。
      * duplicates       — 真正同 store 内的冗余副本, 才可操作清理。
"""
import datetime
import hashlib
import json
import os
import re
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

# 噪声目录: 跳过, 不计入卫生统计(虚拟环境/缓存/依赖/构建产物/测试桩)
NOISE_DIRS = {
    ".venv", "venv", "__pycache__", "node_modules", "site-packages",
    ".git", "dist", "build", ".tox", ".eggs", ".mypy_cache", ".pytest_cache",
    ".idea", ".vscode", ".ruff_cache",
}
SCRIPT_EXTS = (".py", ".sh")


def _is_noise(path_str: str) -> bool:
    segs = path_str.split("/")
    return any(seg in NOISE_DIRS for seg in segs) or any(
        s.endswith(".egg-info") for s in segs
    )


def _prune(dirs):
    dirs[:] = [d for d in dirs if d not in NOISE_DIRS and not d.endswith(".egg-info")]


def sha256(p):
    h = hashlib.sha256()
    try:
        with open(p, "rb") as f:
            for b in iter(lambda: f.read(65536), b""):
                h.update(b)
        return h.hexdigest()
    except Exception:
        return None


def rel_under(p, roots):
    """返回 p 相对其所属 store 根的 (root_index, relpath)。"""
    sp = str(p)
    for i, r in enumerate(roots):
        try:
            rel = Path(sp).relative_to(r)
            return i, str(rel)
        except Exception:
            continue
    return None, None


def scan():
    report = {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "scanned_skills": 0,
        "duplicates": [],            # 同 store 内冗余(可操作)
        "mirror_duplicates": [],     # 跨合法 store 根的同路径镜像(设计如此, 不可删)
        "broken_symlinks": [],
        "unused_scripts": [],
        "injection_hits": [],
    }
    hash_map = {}  # sha -> list of (path, root_index, relpath)

    for sdir in SKILL_DIRS:
        if not sdir.exists():
            continue
        for skill in sdir.iterdir():
            if not skill.is_dir():
                continue
            report["scanned_skills"] += 1
            for root, dirs, files in os.walk(skill):
                _prune(dirs)
                for fn in files:
                    fp = Path(root) / fn
                    if fp.is_symlink() and not fp.exists():
                        report["broken_symlinks"].append(str(fp))
                        continue
                    try:
                        h = sha256(fp)
                        if h:
                            ri, rel = rel_under(fp, SKILL_DIRS)
                            hash_map.setdefault(h, []).append((str(fp), ri, rel))
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

    # 重复分类
    for h, entries in hash_map.items():
        if len(entries) < 2:
            continue
        paths = [e[0] for e in entries]
        rels = {e[2] for e in entries if e[2]}
        roots = {e[1] for e in entries if e[1] is not None}
        # 跨两个不同 store 根 + 同相对路径 => 镜像(设计如此)
        if len(roots) > 1 and len(rels) == 1:
            report["mirror_duplicates"].append(paths)
        else:
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
                if _is_noise(str(sc)):
                    continue
                if sc.name == "__init__.py":
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
        "mirror_duplicates": len(r["mirror_duplicates"]),
        "broken_symlinks": len(r["broken_symlinks"]),
        "unused_scripts": len(r["unused_scripts"]),
        "injection_hits": len(r["injection_hits"]),
        "report": str(out),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
