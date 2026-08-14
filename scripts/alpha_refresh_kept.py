#!/usr/bin/env python3
"""
alpha_refresh_kept.py — 因子保留清单(kept)每日刷新（P2-8 遗留②，2026-08-14 落地）

逻辑:
  1. 读取最新 alpha_eval_YYYYMMDD.json（由 alpha_eval.py 生成）
  2. 提取 kept 达标因子 → 与 alpha_factors_kept.json 对比
  3. 有变化才写回（保留 generated/method/note 元数据，避免无谓抖动）
  4. 无最新报告 → 提示先跑 alpha_eval.py，不报错退出

用法:
  python3 scripts/alpha_refresh_kept.py                     # 自动用最新报告刷新
  python3 scripts/alpha_refresh_kept.py --report PATH       # 指定报告文件
  python3 scripts/alpha_refresh_kept.py --dry-run           # 只打印不写文件
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALPHA_DIR = PROJECT_ROOT / ".workbuddy" / "data" / "alpha"
KEPT_JSON = ALPHA_DIR / "alpha_factors_kept.json"

# kept 生成时写入的方法说明（与 alpha_eval.py 保留线保持一致）
METHOD = "ICIR>0.3 + IC>0占比>55% + 样本外70/30验证 valid_ICIR>0.2"


def latest_report() -> Path | None:
    """取最新 alpha_eval_YYYYMMDD.json，无则 None。"""
    reports = sorted(ALPHA_DIR.glob("alpha_eval_*.json"), reverse=True)
    return reports[0] if reports else None


def extract_kept(report: Path) -> tuple[list[str], dict]:
    """从报告 JSON 提取 (kept因子列表, 因子统计)，按 IC 强弱降序。"""
    data = json.loads(report.read_text(encoding="utf-8"))
    kept = [f for f in data.get("kept", []) if isinstance(f, str)]
    stats = {
        f: data["factors"][f]
        for f in kept
        if f in data.get("factors", {})
        and isinstance(data["factors"][f], dict)
        and data["factors"][f].get("ok")
    }
    kept.sort(key=lambda f: stats.get(f, {}).get("ic", 0), reverse=True)
    return kept, stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Alpha保留因子清单每日刷新")
    parser.add_argument("--report", default=None, help="指定评估报告JSON路径(缺省=最新)")
    parser.add_argument("--dry-run", action="store_true", help="只打印不写文件")
    args = parser.parse_args()

    report = Path(args.report) if args.report else latest_report()
    if report is None or not report.exists():
        print(f"⚠️ 无评估报告，请先运行 alpha_eval.py（{ALPHA_DIR}/alpha_eval_*.json）")
        return

    kept, stats = extract_kept(report)
    print(f"📄 报告: {report.name} | 达标因子 {len(kept)} 个: {kept}")

    if args.dry_run:
        print("🔍 dry-run: 不写文件")
        return

    # 与现有 kept 对比，有变化才写回
    old = []
    if KEPT_JSON.exists():
        try:
            old = json.loads(KEPT_JSON.read_text(encoding="utf-8")).get("kept", [])
        except (OSError, json.JSONDecodeError):
            old = []

    if sorted(old) == sorted(kept):
        print(f"✅ 无变化，保留现有清单: {kept}")
        return

    note_lines = []
    for f in kept:
        s = stats.get(f, {})
        note_lines.append(
            f"{f}(IC {s.get('ic', 0):.3f}/ICIR {s.get('icir', 0):.3f}/方向 {s.get('ic_pos', 0):.1%})"
        )
    note = "; ".join(note_lines) if note_lines else "无达标因子"

    payload = {
        "generated": datetime.now().strftime("%Y-%m-%d"),
        "method": METHOD,
        "kept": kept,
        "note": note,
        "source_report": report.name,
    }
    KEPT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"🔄 kept 已更新: {old} → {kept}")
    print(f"📝 {KEPT_JSON}")


if __name__ == "__main__":
    main()
