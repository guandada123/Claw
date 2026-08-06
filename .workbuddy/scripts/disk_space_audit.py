#!/usr/bin/env python3
"""
disk_space_audit.py — 跨项目磁盘/SSD 空间巡检（周日运维周报组件）

设计：
  复用统一巡检中枢的磁盘检查口径（致态SSD /Volumes/ZHITAI + 阈值 warn=85/crit=92）。
  巡检所有挂载点（不只数据盘），生成 markdown 报告到 output/reports/disk_space_<date>.md，
  供周日运维周报聚合或独立查阅。

  为何独立成脚本而非塞进 push_weekly_report 链：
  原周报生成链(push_weekly_report.py)只负责"读 md→推飞书"，内容由分散脚本写入，
  且最近一次生成停留在 07-24（链路可能已断裂）。独立脚本零耦合、可累积趋势、可单独排障。

用法：
  python3 disk_space_audit.py              # 巡检 + 生成 md 报告
  python3 disk_space_audit.py --json        # 仅输出 JSON（供其他脚本聚合）
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
OUT_DIR = SCRIPT_DIR.parent.parent / "output" / "reports"

WARN_PCT = 85
CRIT_PCT = 92


def _df_rows() -> list[dict]:
    """取真实数据盘挂载点使用率（df -P 可移植格式）。
    排除伪/系统卷：/dev(devfs)、/System/*(Apple 合成卷)、/private/var/vm(交换)、
    /net /home 等——与中枢 check_disk 口径一致，只关注用户可写数据盘(如 /Volumes/ZHITAI)。"""
    SKIP_PREFIXES = ("/dev", "/System/", "/private/var/vm", "/net", "/proc", "/run",
                     "/sys", "/Volumes/com.apple", "/Volumes/Preboot", "/Volumes/VM")
    rows = []
    try:
        r = subprocess.run(["df", "-P", "-k"], capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            return rows
        for line in r.stdout.strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) < 6:
                continue
            mount = parts[5]
            if mount in ("100%",) or any(mount.startswith(p) for p in SKIP_PREFIXES):
                continue
            try:
                use_pct = int(parts[4].rstrip("%"))
            except ValueError:
                continue
            total_k = int(parts[1]) if parts[1].isdigit() else 0
            avail_k = int(parts[3]) if parts[3].isdigit() else 0
            # 跳过总量为0的伪盘
            if total_k == 0:
                continue
            rows.append({
                "mount": mount,
                "use_pct": use_pct,
                "total_gb": round(total_k / 1024 / 1024, 1),
                "avail_gb": round(avail_k / 1024 / 1024, 1),
            })
    except Exception:
        pass
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="仅输出 JSON")
    args = ap.parse_args()

    rows = _df_rows()
    # 仅关注使用率较高的挂载点（>=warn 或 系统卷但异常高）
    flagged = [r for r in rows if r["use_pct"] >= WARN_PCT]
    status = "alert" if any(r["use_pct"] >= CRIT_PCT for r in flagged) else ("warn" if flagged else "ok")

    if args.json:
        print(json.dumps({"status": status, "rows": rows, "flagged": flagged,
                          "thresholds": {"warn": WARN_PCT, "crit": CRIT_PCT}},
                         ensure_ascii=False))
        return 0

    now = datetime.datetime.now()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# 磁盘/SSD 空间巡检（{now:%Y-%m-%d %H:%M}）",
        "",
        f"> 阈值：warn≥{WARN_PCT}% / crit≥{CRIT_PCT}%",
        f"> 状态：{'🔴 严重' if status == 'alert' else ('🟡 警告' if status == 'warn' else '🟢 正常')}",
        "",
        "## 挂载点使用率",
        "",
        "| 挂载点 | 使用率 | 总量(GB) | 可用(GB) |",
        "|--------|--------|----------|----------|",
    ]
    for r in sorted(rows, key=lambda x: -x["use_pct"]):
        mark = "🔴" if r["use_pct"] >= CRIT_PCT else ("🟡" if r["use_pct"] >= WARN_PCT else "")
        lines.append(f"| {r['mount']} | {mark}{r['use_pct']}% | {r['total_gb']} | {r['avail_gb']} |")
    lines += [
        "",
        "## 结论",
        "",
        ("⚠️ 存在挂载点超过严重阈值，建议清理 .backups/ 旧备份或 output/ 历史产物。" if status == "alert"
         else "🟡 部分挂载点接近警告阈值，持续监控。" if status == "warn"
         else "✅ 所有挂载点空间正常。"),
        "",
    ]
    out_path = OUT_DIR / f"disk_space_{now:%Y%m%d}.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[disk-audit] 状态={status} 巡检 {len(rows)} 个挂载点，报告: {out_path}")
    if flagged:
        for r in flagged:
            print(f"  ⚠️ {r['mount']} {r['use_pct']}% ( avail {r['avail_gb']}GB )")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
