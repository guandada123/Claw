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
    （降级≠静默：读不出 registry 时**退出码 2**，调用方必须当"今天没跑成"处理）

退出码：0=正常跑完（含"过闸 0 条"这种正常的 SILENT）；2=输入不可读/不可信（不等于无候选）
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

# v1.10：锚点与指标口径从**单一真源**取（防"新增一个项目名，五个脚本各判一次"）
sys.path.insert(0, str(SCRIPT.parent))
try:  # pragma: no cover - 降级路径仅为健壮性
    from discovery_schema import TARGET_PROJECTS as _ALLOWED_TARGETS
    from discovery_schema import has_metric as _has_metric
except Exception:  # noqa: BLE001
    _ALLOWED_TARGETS = ("Claw", "QTS", "StockInsight", "meta", "none")

    def _has_metric(c):
        return bool(str(c.get("success_metric") or "").strip()) or bool(
            str(c.get("check_cmd") or "").strip()
        )

DEFAULT_CFG = {
    "mode": "calibrate",
    "top_n": 3,
    "quality_gate": {
        "allow_status": ["pending", "approved"],
        "value_min": ["high", "medium-high"],
        "risk_max": ["low"],
        "require_host": True,
        "require_doc_target": True,
        # v1.10：没有目标锚点 / 没有可判定指标的候选 = 无法证真的「优化」→ 不进落地通道
        #   （旧口径的产物：57 条候选 100% 落在技能元层、落地即宣布成功、零收益回查）
        "require_target_project": True,
        "require_success_metric": True,
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
    """落地目标文件的**唯一真源**：显式字段优先，正则仅作兜底。

    病根（2026-10-09 实测）：目标文件原先只从 suggestion/applied_action 散文里正则猜，
    要求 `references/xxx.md` 斜杠与文件名连续。同一件事写成 `references/ 增补 xxx.md`
    （斜杠后带空格）就被静默判为「无文档落地目标」→ 候选被质量闸误拦 4 条。
    目标文件是可声明的结构化事实，不该靠散文解析。
    """
    for key in ("doc_target", "metric_target_file"):
        v = str(c.get(key) or "").strip()
        if not v:
            continue
        m = DOC_TARGET_RE.search(v)
        if m:
            return m.group(1)
        if v.endswith(".md"):
            # 允许写绝对路径/家目录缩写：只保留 skills/ 之后的部分
            tail = v.split("skills/", 1)[1] if "skills/" in v else v
            return tail.lstrip("/")
    blob = " ".join(str(c.get(k, "")) for k in ("suggestion", "applied_action"))
    m = DOC_TARGET_RE.search(blob)
    return m.group(1) if m else None


def metric_of(c: dict) -> str:
    return str(c.get("success_metric") or c.get("check_cmd") or "").strip()


def quality_gate(c: dict, cfg: dict, skills_dir: Path) -> tuple[bool, str, str]:
    """返回 (通过?, 理由, 理由码)。理由码供调用方分类统计（尤其「指标闸拦下」要能单独计数）。

    理由码：pass | status | value | risk | host | doc_target | target_project | success_metric
    """
    g = cfg.get("quality_gate", DEFAULT_CFG["quality_gate"])
    if c.get("status") not in g.get("allow_status", []):
        return False, f"status={c.get('status')} 不在允许集", "status"
    if c.get("value") not in g.get("value_min", []):
        return False, f"value={c.get('value')} 低于阈", "value"
    if c.get("risk") not in g.get("risk_max", []):
        return False, f"risk={c.get('risk')} 高于阈", "risk"
    skill = c.get("skill") or ""
    if g.get("require_host", True) and (not skill or not (skills_dir / skill).is_dir()):
        return False, f"承载方缺失: {skill or '(空)'}", "host"
    if g.get("require_doc_target", True) and not doc_target(c):
        return False, "无文档落地目标(references/README/CHANGELOG)", "doc_target"
    # v1.10 新增两道闸（顺序：先锚点后指标 —— 先问"优化谁"，再问"怎么算优化成功"）
    if g.get("require_target_project", True):
        tp = str(c.get("target_project") or "").strip()
        if tp not in _ALLOWED_TARGETS or tp == "none":
            return (
                False,
                f"无目标锚点(target_project={tp or '(空)'}) — 不知道优化谁",
                "target_project",
            )
    if g.get("require_success_metric", True) and not _has_metric(c):
        return False, "无可判定指标(success_metric) — 优化无法证真", "success_metric"
    return True, "pass", "pass"


def select(reg: dict, cfg: dict, skills_dir: Path) -> tuple[list[dict], list[dict]]:
    passed, dropped = [], []
    for c in reg.get("discovery_candidates", []) or []:
        try:
            ok, why, code = quality_gate(c, cfg, skills_dir)
        except Exception as e:  # noqa: BLE001
            ok, why, code = False, f"gate 异常: {e}", "error"
        if ok:
            passed.append(c)
        else:
            dropped.append({"id": c.get("id"), "why": why, "code": code})
    top_n = int(cfg.get("top_n", 3))
    sel = passed[:top_n]
    # v1.10.2：过闸但被 top_n 限流的，**逐条列出并保留 id**。
    #   旧写法把 N 条压成一条摘要 `(+N 条过闸但被 top_n 限流)` → dropped_by_code 只记「1 条」，
    #   读起来像「只挤掉 1 条」，实际挤掉 N 条 —— 典型的「聚合值抹平组成变化」。
    for c in passed[top_n:]:
        dropped.append(
            {"id": c.get("id"), "why": f"top_n 限流（本批配额 {top_n}）", "code": "top_n"}
        )
    return sel, dropped


def write_calibration_report(
    sel: list[dict], dropped: list[dict], mode: str, suffix: str = ""
) -> Path:
    # 探测（--mode 覆盖）报告写进 probe/ 子目录：calibration/ 是**证据目录**，
    # 观测行为不该在证据目录里留同名文件（三次审计 S6，与二次审计"试跑污染账本"同族）。
    d = CALIBRATION_DIR / "probe" if suffix else CALIBRATION_DIR
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"apply_dryrun_{today8()}{suffix}.md"
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
            f"   - 目标锚点：**{c.get('target_project') or '(空)'}** · 轨道={c.get('track') or '(空)'}",
            f"   - 目标文件：`{doc_target(c)}`",
            f"   - 成功指标：{metric_of(c) or '(无)'}",
            f"   - 来源：{c.get('url', '(无)')}",
            f"   - 依据：{c.get('suggestion', '(无)')}",
        ]
    blocked_metric = [d for d in dropped if d.get("code") == "success_metric"]
    blocked_anchor = [d for d in dropped if d.get("code") == "target_project"]
    lines += ["", "## 被滤除（质量闸）"]
    for d in dropped:
        lines.append(f"- `{d.get('id')}` — {d.get('why')}")
    lines += [
        "",
        "## v1.10 指标闸（新）",
        f"- 因**无目标锚点**被拦：{len(blocked_anchor)} 条",
        f"- 因**无可判定指标**被拦：{len(blocked_metric)} 条",
        "- 药方：在「🛡️ 统一发现-每日扫描」里给候选补 `target_project` + `success_metric`（可判定、可复核）后才进本通道；",
        "  补不出指标的 → 在周度排序里落到 `Assess`（先评估）或 `rejected`，**不再默认落地**。",
    ]
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
            {
                "id": c.get("id"),
                "skill": c.get("skill"),
                "target": doc_target(c),
                "value": c.get("value"),
                "risk": c.get("risk"),
                "url": c.get("url"),
                "target_project": c.get("target_project"),
                "track": c.get("track"),
                "success_metric": c.get("success_metric"),
            }
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
        # ⚠️ 三次审计 S5：原实现 return 0 —— 落地自动化只看退出码，会把"读不出 registry"
        #    当成"跑完了、没事"，于是**当天静默不落地且没人知道**。
        #    现在退出码 2（1 留给"有候选但被闸门全滤掉"这类正常业务结果）。
        out = {
            "ok": False,
            "error": reg["_error"],
            "mode": None,
            "selected": [],
            "silent": None,
            "note": "退出码 2 = 输入不可读/不可信，**不等于「无候选」**；不得据此静默",
        }
        if args.json:
            print(json.dumps(out, ensure_ascii=False))
        else:
            print(f"[apply_doc] 致命：{reg['_error']}", file=sys.stderr)
        return 2

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
        print(
            f"[apply_doc] 注意：--mode {mode} 仅本次运行生效 → 报告写 {report.name}（probe 后缀），"
            f"**不写权威账本、不回写 registry**（registry 现有 mode={reg.get('doc_apply', {}).get('mode')}）；"
            f"切 mode 请走 1c 自动化或人工显式改 registry",
            file=sys.stderr,
        )
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

    blocked_metric = sum(1 for d in dropped if d.get("code") == "success_metric")
    blocked_anchor = sum(1 for d in dropped if d.get("code") == "target_project")
    da0 = reg.get("doc_apply", {}) or {}
    alert_min = int(da0.get("blocked_metric_alert_min", 3))
    # v1.10.1 节流：存量被拦是「待清账」的稳态，不是每日事件。
    #   不加节流 → 每天推同一条提示（4 条存量固定被拦）→ 告警疲劳
    #   （铁律：告警频率须匹配可处置性）。规则：距上次推送不足 N 天 → suppress。
    throttle_days = int(da0.get("metric_alert_min_interval_days", 7))
    last_alert = str(da0.get("last_metric_alert_at") or "").strip()
    throttled = False
    if last_alert:
        try:
            d0 = datetime.date.fromisoformat(last_alert[:10])
            throttled = (datetime.date.today() - d0).days < throttle_days
        except ValueError:
            throttled = False
    raw_alert = blocked_metric >= alert_min
    metric_alert = raw_alert and not throttled
    out = {
        "ok": True,
        "mode": mode,
        "mode_source": "cli-override(不回写)" if override else "registry",
        "selected": [
            {
                "id": c.get("id"),
                "skill": c.get("skill"),
                "target": doc_target(c),
                "value": c.get("value"),
                "risk": c.get("risk"),
                "target_project": c.get("target_project"),
                "track": c.get("track"),
                "success_metric": c.get("success_metric"),
            }
            for c in sel
        ],
        "dropped": dropped,
        "dropped_by_code": {
            code: sum(1 for d in dropped if d.get("code") == code)
            for code in sorted({d.get("code") for d in dropped})
        },
        "blocked_metric": blocked_metric,
        "blocked_anchor": blocked_anchor,
        "metric_alert": metric_alert,
        "metric_alert_raw": raw_alert,
        "metric_alert_throttled": throttled,
        "metric_alert_last_at": last_alert or None,
        "metric_alert_min_interval_days": throttle_days,
        "metric_alert_min": alert_min,
        "report": str(report.relative_to(CLAW)),
        "silent": len(sel) == 0,
    }
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    elif not args.quiet:
        print(
            f"[apply_doc] mode={mode} 入选 {len(sel)} 条 / 滤除 {len(dropped)} 条 -> {out['report']}"
        )
        for c in out["selected"]:
            print(
                f"  · {c['id']} [{c['skill']}] -> {c['target']}"
                f"（锚点 {c['target_project']}｜指标 {c['success_metric'] or '(无)'}）"
            )
        if out["silent"]:
            print("[apply_doc] 无过闸候选 → SILENT")
        if blocked_metric:
            if metric_alert:
                flag = "⚠️ 达告警阈 → 今日应推一次"
            elif raw_alert and throttled:
                flag = f"（达阈但节流中：上次推送 {last_alert}，间隔<{throttle_days}天）"
            else:
                flag = "（未达阈）"
            print(
                f"[apply_doc] v1.10 指标闸拦下 {blocked_metric} 条（无 success_metric）"
                f"、锚点闸拦下 {blocked_anchor} 条 {flag}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
