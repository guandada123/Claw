#!/usr/bin/env python3
"""
多项目（QTS / pmf / StockInsight / wechat）每日健康巡检守卫（助手接管层）

覆盖范围（不含 Claw 本体，Claw 由 watchdog 自管）：
  - CI 红扫描：QuantTradingSystem / project-monitor-fusion 最近 N 次 GitHub Actions 运行
  - 容器存活快照：docker 容器前缀 `quant-` / `project-monitor-fusion-` 的运行/健康态

行为：
  - 发现异常 → 经 push_feishu.sh 卡片化推送飞书主群（仅异常时）
  - 全部健康 → 静默（打印 ALL GREEN，不推送）
  - --dry-run → 仅打印将推送内容，不实际推送

设计原则（对齐自动化铁律）：
  - 只读探测（gh run list / docker ps），不写、不重启
  - 重启职责归 self_heal.py（Docker 原生 unless-stopped + 白名单），本脚本只"看+报"
  - 失败必须 fail-safe：gh/docker 不可达时记警告而非崩溃
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ---- 配置（如需扩展其他仓，改这里即可）----
GH_REPOS = [
    "QuantTradingSystem",
    "project-monitor-fusion",
    "StockInsight",
    "wechat-download-api",
]
CONTAINER_GROUPS = [
    ("QTS", "quant-"),
    ("pmf", "project-monitor-fusion-"),
    ("StockInsight", "stockinsight"),  # 容器名 stockinsight-api-1（无尾随连字符，用子串匹配）
    ("wechat", "wechat-download-api"),
]
FEISHU_GROUP = "oc_9ee5303497f5e0e71666b610d6bdc346"
SCRIPT_DIR = Path(__file__).resolve().parent
PUSH_SH = SCRIPT_DIR / "push_feishu.sh"


def run(cmd, timeout=60):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except Exception as e:  # noqa: BLE001
        return -1, "", f"exec error: {e}"


def scan_ci(repo, limit=5):
    """返回 (reds:list, warn:str)。reds 每项 {workflow, conclusion, url, createdAt}"""
    rc, out, err = run(
        [
            "gh",
            "run",
            "list",
            "--repo",
            f"guandada123/{repo}",
            "--limit",
            str(limit),
            "--json",
            "status,conclusion,workflowName,url,createdAt,headBranch",
        ]
    )
    if rc != 0:
        return [], f"gh 不可达({repo}): {err.strip()[:120]}"
    try:
        runs = json.loads(out)
    except Exception:  # noqa: BLE001
        return [], f"gh 输出解析失败({repo})"
    reds = []
    for r in runs:
        concl = (r.get("conclusion") or "").lower()
        status = (r.get("status") or "").lower()
        if concl in ("failure", "cancelled", "timed_out", "stale") or status == "failure":
            reds.append(
                {
                    "workflow": r.get("workflowName", "?"),
                    "conclusion": r.get("conclusion") or status,
                    "url": r.get("url", ""),
                    "createdAt": (r.get("createdAt") or "")[:10],
                    "branch": r.get("headBranch", ""),
                }
            )
    return reds, ""


def scan_containers(prefix):
    """返回 (total, running, unhealthy:list, warn)。"""
    rc, out, err = run(
        [
            "docker",
            "ps",
            "--filter",
            f"name={prefix}",
            "--format",
            "{{.Names}}\t{{.Status}}",
        ]
    )
    if rc != 0:
        return 0, 0, [], f"docker 不可达({prefix}): {err.strip()[:120]}"
    lines = [l for l in out.strip().splitlines() if l.strip()]
    total = len(lines)
    running = 0
    unhealthy = []
    for l in lines:
        parts = l.split("\t")
        name = parts[0]
        status = parts[1] if len(parts) > 1 else ""
        if status.startswith("Up"):
            running += 1
        if "unhealthy" in status or "Restarting" in status or not status.startswith("Up"):
            unhealthy.append({"name": name, "status": status})
    return total, running, unhealthy, ""


def build_report(ci_results, container_results):
    lines = ["**📡 多项目每日健康巡检（QTS / pmf / StockInsight / wechat）**", ""]
    # CI 段
    lines.append("### 🔧 CI 状态（GitHub Actions）")
    any_ci = False
    for repo, reds, warn in ci_results:
        if warn:
            lines.append(f"- `{repo}`: ⚠️ {warn}")
            continue
        if reds:
            any_ci = True
            for r in reds:
                lines.append(
                    f"- `{repo}` ❌ **{r['workflow']}** ({r['conclusion']}) "
                    f"branch={r['branch']} {r['createdAt']}"
                )
                if r["url"]:
                    lines.append(f"  ↳ {r['url']}")
        else:
            lines.append(f"- `{repo}`: ✅ 近 {5} 次运行无红灯")
    if not any_ci:
        lines.append("  （无 CI 红灯）")
    # 容器段
    lines.append("")
    lines.append("### 🐳 容器存活")
    any_c = False
    for grp, total, running, unhealthy, warn in container_results:
        if warn:
            lines.append(f"- `{grp}`: ⚠️ {warn}")
            continue
        if unhealthy:
            any_c = True
            for u in unhealthy:
                lines.append(f"- `{grp}` ❌ **{u['name']}** {u['status']}")
        else:
            lines.append(f"- `{grp}`: ✅ {running}/{total} 运行正常")
    if not any_c:
        lines.append("  （无异常容器）")
    lines.append("")
    lines.append(f"_生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印将推送内容，不实际推送")
    ap.add_argument("--ci-limit", type=int, default=5, help="每仓扫描最近 N 次 CI 运行")
    args = ap.parse_args()

    ci_results = []
    for repo in GH_REPOS:
        reds, warn = scan_ci(repo, args.ci_limit)
        ci_results.append((repo, reds, warn))

    container_results = []
    for grp, prefix in CONTAINER_GROUPS:
        total, running, unhealthy, warn = scan_containers(prefix)
        container_results.append((grp, total, running, unhealthy, warn))

    report = build_report(ci_results, container_results)

    # 判定是否有异常
    has_anomaly = any((reds or warn) for _, reds, warn in ci_results) or any(
        (unhealthy or warn) for _, _, _, unhealthy, warn in container_results
    )

    summary = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "ci_reds": sum(len(r) for _, r, _ in ci_results),
        "ci_warns": sum(1 for _, _, w in ci_results if w),
        "container_unhealthy": sum(len(u) for _, _, _, u, _ in container_results),
        "container_warns": sum(1 for _, _, _, _, w in container_results if w),
        "anomaly": has_anomaly,
    }
    print(json.dumps(summary, ensure_ascii=False))

    if not has_anomaly:
        print("ALL GREEN — 静默，不推送")
        return 0

    print("--- 将推送飞书内容 ---")
    print(report)
    if args.dry_run:
        print("[DRY-RUN] 不实际推送")
        return 0

    if not PUSH_SH.exists():
        print(f"[ERROR] push_feishu.sh 不存在: {PUSH_SH}", file=sys.stderr)
        return 2
    rc, out, err = run(["bash", str(PUSH_SH), "📡 多项目健康告警", report])
    if rc != 0:
        print(f"[ERROR] 飞书推送失败 rc={rc}: {err.strip()[:200]}", file=sys.stderr)
        return 2
    print("飞书告警已推送")
    return 0


if __name__ == "__main__":
    sys.exit(main())
