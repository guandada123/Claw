#!/usr/bin/env python3
"""project_signal_collect.py — 统一巡检中枢 v1.10 · 双轨发现的「项目自身痛点」轨（Track 2）

病根：旧发现面只在外网搜「有什么新玩具」（趋势驱动），从不看**自己项目的痛**。
     业界技能进化框架（AutoSkill / Memento-Skills）都从**执行经验与失败**提炼，而非只搜趋势。

本脚本采集真实项目的痛点信号（只读、有界、可复现）：
  1) `.learnings/*.md`  近 N 天的**事故/教训**（Claw/QTS/StockInsight 三项目均有此目录）
  2) TODO/FIXME/XXX/HACK 标记（有界扫描：限量文件数 + 限量结果）
  3) （可选 `--with-tests`）测试失败摘要（跑 anchor.tests_cmd，带硬超时）

输出：signals[]（每条的 target_project 必为真实项目）→ 交给「🛡️ 统一发现-每日扫描」转成候选
      候选必须补 `success_metric`（"该痛点消失"要能被复核），否则进不了落地通道。

用法：
  python3 project_signal_collect.py --json                  # 默认：learnings + todo
  python3 project_signal_collect.py --json --with-tests      # 额外跑测试（慢，默认关）
  python3 project_signal_collect.py --json --days 30 --max-signals 30

退出码：0=跑完（含"无信号"）；2=registry 不可读（不得当成"项目无痛"）
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from discovery_schema import claw_root, expand, load_registry, now_iso, today_iso  # noqa: E402

SCRIPT = Path(__file__).resolve()
CLAW = claw_root(__file__)
DEFAULT_REGISTRY = CLAW / ".workbuddy" / "inspection_hub" / "registry.json"

TODO_RE = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b[:：]?\s*(.{0,120})")
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
TITLE_RE = re.compile(r"^#\s+(.+)$", re.M)
GRADE_RE = re.compile(r"\*\*等级\*\*[:：]\s*([Pp][0-9])")
MAX_BYTES_PER_FILE = 200_000


def _recent_dated_files(dirs: list[Path], days: int, cap: int = 40) -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    cutoff = datetime.date.today() - datetime.timedelta(days=days)
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md"), reverse=True):
            m = DATE_RE.search(f.name)
            if not m:
                continue
            try:
                fd = datetime.date.fromisoformat(m.group(1))
            except Exception:  # noqa: BLE001
                continue
            if fd >= cutoff:
                out.append((f, m.group(1)))
            if len(out) >= cap:
                return out
    return out


def collect_learnings(anchor: dict, days: int) -> list[dict]:
    dirs = [expand(p) for p in (anchor.get("learnings_dirs") or [])]
    sig = []
    for f, d in _recent_dated_files(dirs, days):
        try:
            txt = f.read_text(encoding="utf-8", errors="replace")[:20_000]
        except Exception as e:  # noqa: BLE001
            sig.append({"kind": "learning", "severity": None, "title": f"(读取失败) {f.name}",
                        "detail": str(e), "file": str(f), "date": d})
            continue
        t = TITLE_RE.search(txt)
        g = GRADE_RE.search(txt)
        title = (t.group(1).strip() if t else f.stem)[:160]
        # 取「事实」段前若干行做摘要（供人/agent 判是否成候选）
        body = re.sub(r"\s+", " ", txt)
        sig.append(
            {
                "kind": "learning",
                "severity": g.group(1).upper() if g else None,
                "title": title,
                "detail": body[200:640],
                "file": str(f),
                "date": d,
            }
        )
    return sig


def _resolve_glob(pat: str) -> tuple[Path | None, str]:
    """把 '<根>/a/**/*.py' 这类模式拆成 (存在的根目录, 相对模式)。

    取"最长存在前缀"为根 —— 直接 base.parent 会在 `src/**/*.py` 上得到不存在的 `src/**`，
    于是整条 glob 被静默跳过（看着在查、其实一条都没扫）。
    """
    p = expand(pat)
    parts = list(p.parts)
    # 从右往左找第一个真实存在的目录
    for i in range(len(parts), 0, -1):
        cand = Path(*parts[:i])
        if cand.is_dir():
            rel = "/".join(parts[i:])
            return (cand, rel) if rel else (cand, "*")
    return None, ""


def collect_todos(anchor: dict, max_files: int, max_hits: int) -> list[dict]:
    hits, scanned = [], 0
    for pat in anchor.get("todo_globs") or []:
        root, rel = _resolve_glob(pat)
        if root is None:
            continue
        try:
            files = sorted(root.glob(rel)) if rel else []
        except Exception:  # noqa: BLE001
            files = []
        for f in files:
            if scanned >= max_files or len(hits) >= max_hits:
                break
            if not f.is_file() or f.stat().st_size > MAX_BYTES_PER_FILE:
                continue
            scanned += 1
            try:
                txt = f.read_text(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                continue
            for m in list(TODO_RE.finditer(txt))[:3]:
                line_no = txt[: m.start()].count("\n") + 1
                hits.append(
                    {
                        "kind": "todo",
                        "severity": None,
                        "title": f"{m.group(1)}: {m.group(2).strip()[:140]}",
                        "detail": f"{f}:{line_no}",
                        "file": f"{f}:{line_no}",
                        "date": None,
                    }
                )
        if scanned >= max_files or len(hits) >= max_hits:
            break
    return hits


def run_tests(anchor: dict, timeout: int) -> list[dict]:
    cmd = anchor.get("tests_cmd")
    if not cmd:
        return []
    try:
        p = subprocess.run(  # noqa: S602  # nosec B602 - 命令来自本机 registry 声明，非外部输入
            str(cmd), shell=True, capture_output=True, text=True, timeout=timeout
        )
        raw = ((p.stdout or "") + (p.stderr or "")).strip()
        tail = "\n".join(raw.splitlines()[-12:])
        if p.returncode == 0:
            return []
        return [
            {
                "kind": "test",
                "severity": "P1",
                "title": f"测试失败 rc={p.returncode}",
                "detail": tail[:800],
                "file": None,
                "date": today_iso(),
            }
        ]
    except subprocess.TimeoutExpired:
        return [{"kind": "test", "severity": "P2", "title": f"测试超时({timeout}s)",
                 "detail": "(timeout)", "file": None, "date": today_iso()}]
    except Exception as e:  # noqa: BLE001
        return [{"kind": "test", "severity": "P2", "title": f"测试执行异常: {e}",
                 "detail": "(error)", "file": None, "date": today_iso()}]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    ap.add_argument("--days", type=int, default=14, help="learnings 回看天数")
    ap.add_argument("--max-files", type=int, default=120)
    ap.add_argument("--max-hits", type=int, default=20)
    ap.add_argument("--max-signals", type=int, default=40)
    ap.add_argument("--with-tests", action="store_true", help="额外跑 anchor.tests_cmd（慢）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    reg = load_registry(Path(args.registry))
    if "_error" in reg:
        print(json.dumps({"ok": False, "error": reg["_error"],
                          "note": "退出码 2 = 输入不可读，**不等于「项目无痛」**"}, ensure_ascii=False))
        return 2

    anchors = ((reg.get("discovery_policy", {}) or {}).get("project_watch", {}) or {}).get("anchors", [])
    signals, per_project = [], {}
    for a in anchors:
        proj = a.get("project")
        root = expand(a.get("root", ""))
        if not root.is_dir():
            per_project[proj] = {"error": "root 不存在", "root": str(root), "signals": 0}
            continue
        sig = []
        sig += collect_learnings(a, args.days)
        sig += collect_todos(a, args.max_files, args.max_hits)
        if args.with_tests:
            sig += run_tests(a, 180)
        for s in sig:
            s["target_project"] = proj
            s["track"] = "project_signal"
            s["source_url"] = f"file://{s.get('file')}" if s.get("file") and s["file"].startswith("/") else None
            s["suggestion_hint"] = (
                "把该痛点转成候选时必须补 success_metric（该痛点消失要能被复核），否则进不了落地通道"
            )
            s["found_at"] = today_iso()
        signals += sig
        per_project[proj] = {
            "root": str(root),
            "signals": len(sig),
            "by_kind": {k: sum(1 for s in sig if s["kind"] == k) for k in sorted({s["kind"] for s in sig})},
        }

    # 排序：先按严重度（P1>P2>无），再按日期新→旧
    sev_rank = {"P1": 0, "P2": 1, None: 2}
    signals.sort(key=lambda s: (sev_rank.get(s.get("severity"), 2), str(s.get("date") or "")), reverse=False)
    signals = signals[: args.max_signals]

    out = {
        "ok": True,
        "ts": now_iso(),
        "days": args.days,
        "with_tests": bool(args.with_tests),
        "anchors": len(anchors),
        "per_project": per_project,
        "signal_count": len(signals),
        "signals": signals,
    }
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"[signal] 锚点 {len(anchors)}｜信号 {len(signals)}")
        for p, v in per_project.items():
            print(f"  · {p}: {v.get('signals', 0)} 条 {v.get('by_kind', v.get('error', ''))}")
        for s in signals[:12]:
            print(f"  - [{s['target_project']}/{s['kind']}/{s.get('severity') or '-'}] {s['title'][:90]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
