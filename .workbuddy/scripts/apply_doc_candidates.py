#!/usr/bin/env python3
"""apply_doc_candidates.py — 统一巡检中枢 · 发现回路「落地」闸门 + 校准账本

Mode B（2026-09-24 定案）：每日发现 → 当日落地文档类 → 周度审核心类。
本脚本只做三件事（确定性、零 LLM）：
  1) 质量闸：从 registry.discovery_candidates 筛出「可当日落地」的文档类候选
     （承载方存在 + 价值≥阈 + 风险≤阈 + 文档可落地 + 状态允许）
  2) top_n 限流（默认 3）
  3) 账本/报告：calibrate 只产出「本会改什么」报告（零写入）；
     live 产出待执行计划 JSON（交自动化里的 agent 合成文档）

铁律：
  - 绝不直接改任何 skill 文档（文档合成是自动化里 agent 的职责）
  - calibrate 模式零副作用（只写 calibration/ 目录与 registry.doc_apply.*）
  - 不触碰禁区：automations/、scripts/*.db、.git、.venv、symlink 目标、memory 层
  - fail-safe：registry 缺失/损坏、单个候选异常 → 不崩溃，降级并注明
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve()
CLAW = SCRIPT.parents[2]  # .workbuddy/scripts/<this>.py -> CLAW
DEFAULT_REGISTRY = CLAW / ".workbuddy" / "inspection_hub" / "registry.json"
DEFAULT_SKILLS_DIR = Path(os.environ.get("HOME", "/Users/guan")) / ".workbuddy" / "skills"
CALIBRATION_DIR = CLAW / ".workbuddy" / "inspection_hub" / "calibration"

DEFAULT_CFG = {
    "mode": "calibrate",
    "top_n": 3,
    "quality_gate": {
        "allow_status": ["pending", "approved"],
        "value_min": ["high", "medium-high"],
        "risk_max": ["low"],
        "require_host": True,
        "require_doc_target": True,
    },
}

DOC_TARGET_RE = re.compile(r"(references/[A-Za-z0-9._/\-]+\.md|README\.md|CHANGELOG\.md)")


def now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def today8() -> str:
    return datetime.datetime.now().strftime("%Y%m%d")


def load_registry(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        return {"_error": f"registry 读取失败: {e}"}


def save_registry(path: Path, reg: dict) -> bool:
    try:
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(reg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[apply_doc] warn: registry 回写失败(不阻断): {e}", file=sys.stderr)
        return False


def doc_target(c: dict) -> str | None:
    blob = " ".join(str(c.get(k, "")) for k in ("suggestion", "applied_action"))
    m = DOC_TARGET_RE.search(blob)
    return m.group(1) if m else None


def quality_gate(c: dict, cfg: dict, skills_dir: Path) -> tuple[bool, str]:
    g = cfg.get("quality_gate", DEFAULT_CFG["quality_gate"])
    if c.get("status") not in g.get("allow_status", []):
        return False, f"status={c.get('status')} 不在允许集"
    if c.get("value") not in g.get("value_min", []):
        return False, f"value={c.get('value')} 低于阈"
    if c.get("risk") not in g.get("risk_max", []):
        return False, f"risk={c.get('risk')} 高于阈"
    skill = c.get("skill") or ""
    if g.get("require_host", True):
        if not skill or not (skills_dir / skill).is_dir():
            return False, f"承载方缺失: {skill or '(空)'}"
    if g.get("require_doc_target", True):
        if not doc_target(c):
            return False, "无文档落地目标(references/README/CHANGELOG)"
    return True, "pass"


def select(reg: dict, cfg: dict, skills_dir: Path) -> tuple[list[dict], list[dict]]:
    passed, dropped = [], []
    for c in reg.get("discovery_candidates", []) or []:
        try:
            ok, why = quality_gate(c, cfg, skills_dir)
        except Exception as e:  # noqa: BLE001
            ok, why = False, f"gate 异常: {e}"
        if ok:
            passed.append(c)
        else:
            dropped.append({"id": c.get("id"), "why": why})
    top_n = int(cfg.get("top_n", 3))
    sel = passed[:top_n]
    if len(passed) > top_n:
        dropped.append({"id": f"(+{len(passed) - top_n} 条过闸但被 top_n 限流)", "why": "top_n"})
    return sel, dropped


def write_calibration_report(sel: list[dict], dropped: list[dict], mode: str, suffix: str = "") -> Path:
    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    p = CALIBRATION_DIR / f"apply_dryrun_{today8()}{suffix}.md"
    lines = [
        f"# 文档类候选落地 · 校准报告 {datetime.datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"- 模式：**{mode}**（calibrate=只读零写入；live=产出待执行计划，由 agent 落地）",
        f"- 过闸并入选：{len(sel)} 条（top_n 限流后）",
        f"- 被滤除：{len(dropped)} 条",
        "",
        "## 本会改什么（入选）",
    ]
    if not sel:
        lines.append("- （空）无过闸候选 → SILENT")
    for i, c in enumerate(sel, 1):
        lines += [
            f"{i}. `{c.get('id')}` · {c.get('skill')} · 价值={c.get('value')} / 风险={c.get('risk')}",
            f"   - 目标：`{doc_target(c)}`",
            f"   - 来源：{c.get('url', '(无)')}",
            f"   - 依据：{c.get('suggestion', '(无)')}",
        ]
    lines += ["", "## 被滤除（质量闸）"]
    for d in dropped:
        lines.append(f"- `{d.get('id')}` — {d.get('why')}")
    lines += [
        "",
        "## 说明",
        "- 校准期（calibrate）本脚本**不写任何 skill 文档**，仅供对拍「本会改什么」。",
        "- 生效期（live）由「📥 每日·文档类落地」自动化里的 agent 合成文档，"
        "每批 = 1 commit + 1 行 CHANGELOG + 1 个回滚点，top≤3。",
        "- 核心类（SKILL.md 主流程 / 脚本 / 自动化 prompt）**永不**进本通道，恒走周度审。",
    ]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def write_parity_ledger(sel: list[dict], mode: str, report: Path) -> Path:
    """对拍账本（机器可读，供 doc_apply_parity.py 校验校准期是否"0 越界"）。

    按 <日期>:<模式> 幂等覆盖，避免同日重跑产生重复行。
    """
    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    lp = CALIBRATION_DIR / "parity.json"
    try:
        led = json.loads(lp.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        led = {}
    runs = led.setdefault("runs", {})
    runs[f"{today8()}:{mode}"] = {
        "date": datetime.datetime.now().strftime("%Y-%m-%d"),
        "ts": now_iso(),
        "mode": mode,
        "report": str(report.relative_to(CLAW)),
        "selected": [
            {"id": c.get("id"), "skill": c.get("skill"), "target": doc_target(c),
             "value": c.get("value"), "risk": c.get("risk"), "url": c.get("url")}
            for c in sel
        ],
    }
    led["updated_at"] = now_iso()
    try:
        lp.write_text(json.dumps(led, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"[apply_doc] warn: 对拍账本写入失败(不阻断): {e}", file=sys.stderr)
    return lp


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["calibrate", "live"], default=None)
    ap.add_argument("--top-n", type=int, default=None)
    ap.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    ap.add_argument("--skills-dir", default=str(DEFAULT_SKILLS_DIR))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    reg = load_registry(Path(args.registry))
    if "_error" in reg:
        out = {"ok": False, "error": reg["_error"]}
        print(json.dumps(out, ensure_ascii=False))
        return 0

    cfg = dict(DEFAULT_CFG)
    cfg.update(reg.get("doc_apply", {}) or {})
    if args.mode:
        cfg["mode"] = args.mode
    if args.top_n is not None:
        cfg["top_n"] = args.top_n
    mode = cfg.get("mode", "calibrate")

    sel, dropped = select(reg, cfg, Path(args.skills_dir))

    # ⚠️ 修正(2026-09-24 二次审计)：`--mode` 是**本次运行的观察口径**，绝不允许改写生产状态。
    #    原实现两处越界：
    #      ① 无条件 `da["mode"] = mode` → 一次 `--mode live` 试跑就把 registry 的 mode 永久翻成 live，
    #         等于**提前打开"会自动写 skill 文档"的开关且不留痕**；
    #      ② 账本/报告也照写 → 试跑会在**权威证据链**（parity.json）里留下一条伪造的 live 运行记录，
    #         还会覆盖当日的校准报告。观测行为污染证据 = 让 10-08 的切换依据失真。
    #    修正：显式 --mode 时 → 报告写 `_probe` 后缀、**不写权威账本、不回写 registry**。
    #    真值纪律：mode 只能由 registry 自己持有；切换只走「📥 每日·文档类落地」1c（有守卫）或人工显式改。
    override = args.mode is not None
    report = write_calibration_report(sel, dropped, mode, "_probe" if override else "")
    if override:
        print(f"[apply_doc] 注意：--mode {mode} 仅本次运行生效 → 报告写 {report.name}（probe 后缀），"
              f"**不写权威账本、不回写 registry**（registry 现有 mode={reg.get('doc_apply', {}).get('mode')}）；"
              f"切 mode 请走 1c 自动化或人工显式改 registry", file=sys.stderr)
    else:
        write_parity_ledger(sel, mode, report)
        da = reg.setdefault("doc_apply", {})
        key = "last_calibration" if mode == "calibrate" else "last_live"
        da[key] = {
            "ts": now_iso(),
            "mode": mode,
            "selected": [c.get("id") for c in sel],
            "report": str(report.relative_to(CLAW)),
        }
        da["mode"] = mode
        da[f"{mode}_runs"] = int(da.get(f"{mode}_runs", 0)) + 1
        if not da.get("calibrate_start"):
            da["calibrate_start"] = datetime.datetime.now().strftime("%Y-%m-%d")
        save_registry(Path(args.registry), reg)

    out = {
        "ok": True,
        "mode": mode,
        "mode_source": "cli-override(不回写)" if override else "registry",
        "selected": [
            {"id": c.get("id"), "skill": c.get("skill"), "target": doc_target(c),
             "value": c.get("value"), "risk": c.get("risk")}
            for c in sel
        ],
        "dropped": dropped,
        "report": str(report.relative_to(CLAW)),
        "silent": len(sel) == 0,
    }
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    elif not args.quiet:
        print(f"[apply_doc] mode={mode} 入选 {len(sel)} 条 / 滤除 {len(dropped)} 条 -> {out['report']}")
        for c in out["selected"]:
            print(f"  · {c['id']} [{c['skill']}] -> {c['target']}")
        if out["silent"]:
            print("[apply_doc] 无过闸候选 → SILENT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
