#!/usr/bin/env python3
"""
security_scanner.py — 工程安全扫描薄壳（封装 bandit）

供「工程质量报告」自动化直接调用（替代历史直跑降级）。
扫描范围固化: 项目源码 + 自动化脚本 + 业务库, 覆盖此前漏扫的 .workbuddy/scripts。

设计原则:
  - 安全类问题(bandit B3xx/B5xx 等)只报告、不自动修复（需人工审核）
  - 退出码: 0=完成扫描(无论有无问题); 非零=工具自身异常(便于调度判断)
  - 输出: 简洁的 severity 汇总 + 高危明细, 便于卡片化推送

用法:
  python3 security_scanner.py                 # 扫描默认范围
  python3 security_scanner.py --quiet         # 仅输出汇总行
  python3 security_scanner.py --paths a.py b.py
"""
import argparse
import subprocess
import sys
from pathlib import Path

# 默认扫描范围（显式列出，避免 `bandit -r .` 误扫 archive/ 大目录）
DEFAULT_SCAN_PATHS = [
    "scripts",
    "src",
    ".workbuddy/scripts",
    "src/claw",
]


def run_bandit(paths: list[str]) -> tuple[int, str]:
    """运行 bandit 并返回 (返回码, 原始输出)。"""
    cmd = [
        sys.executable,
        "-m",
        "bandit",
        "-r",
        *paths,
        "-f",
        "txt",
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=Path(__file__).resolve().parent.parent.parent,  # 项目根目录
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as e:
        return 127, f"bandit 未安装或不可达: {e}"
    return proc.returncode, proc.stdout + proc.stderr


def parse_summary(text: str) -> dict:
    """从 bandit 输出解析『by severity』汇总块（忽略后续 by confidence 块）。

    bandit 输出含两段计数:
      Total issues (by severity):   <-- 我们要的
        Undefined / Low / Medium / High
      Total issues (by confidence):  <-- 干扰段, 跳过
        Undefined / Low / Medium / High
    两者同用 'High:' 等前缀, 故仅在进入 severity 块后解析, 遇到 confidence
    块标记即停止。
    """
    summary = {"High": 0, "Medium": 0, "Low": 0, "Undefined": 0}
    in_severity_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if "Total issues (by severity):" in stripped:
            in_severity_block = True
            continue
        if "Total issues (by confidence):" in stripped:
            # severity 块结束, 停止解析
            break
        if in_severity_block:
            for sev in summary:
                if stripped.startswith(sev + ":"):
                    try:
                        summary[sev] = int(stripped.split(":", 1)[1].strip())
                    except ValueError:
                        pass
    return summary


def high_details(text: str) -> list[str]:
    """提取所有 HIGH 级别 issue 的标题与位置。"""
    details = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # 普通 issue 行 + 其后隔一行是 Severity: High → 判定为高危
        if (
            line.startswith(">> Issue:")
            and "High" not in line
            and i + 2 < len(lines)
            and "Severity: High" in lines[i + 2]
        ):
            title = line.replace(">> Issue:", "").strip()
            # 找 Location 行
            loc = ""
            for j in range(i, min(i + 8, len(lines))):
                if lines[j].strip().startswith("Location:"):
                    loc = lines[j].strip().replace("Location:", "").strip()
                    break
            details.append(f"  • {title} @ {loc}")
        i += 1
    # 去重
    seen = set()
    uniq = []
    for d in details:
        if d not in seen:
            seen.add(d)
            uniq.append(d)
    return uniq


def main() -> int:
    parser = argparse.ArgumentParser(description="工程安全扫描薄壳 (bandit)")
    parser.add_argument("--quiet", action="store_true", help="仅输出汇总行")
    parser.add_argument(
        "--paths", nargs="*", default=None, help="自定义扫描路径(覆盖默认)"
    )
    args = parser.parse_args()

    paths = args.paths if args.paths else DEFAULT_SCAN_PATHS
    rc, out = run_bandit(paths)
    if rc == 127:
        print(out)
        return rc

    summary = parse_summary(out)
    highs = high_details(out)

    print(f"扫描范围: {', '.join(paths)}")
    print(
        f"bandit: 高危 {summary['High']} | 中危 {summary['Medium']} | 低危 {summary['Low']}"
    )
    if highs:
        print(f"\n🚨 高危明细 ({summary['High']}):")
        for d in highs:
            print(d)
    elif not args.quiet:
        print("✅ 无高危安全问题")

    return 0


if __name__ == "__main__":
    sys.exit(main())
