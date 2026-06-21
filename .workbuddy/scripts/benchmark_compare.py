"""
benchmark_compare.py — 对比当前 benchmark 结果与基线
==================================================
用法：
    python benchmark_compare.py baseline.json current.json

逻辑：
    - 每个测试读取 mean 值（秒）
    - 如果当前比基线慢 >20%，输出警告
    - 如果当前比基线快 >10%，输出提示
    - 退出码 0 = 无回归，1 = 有回归（但 CI 里只 warning）
"""

import json
import sys
from pathlib import Path

THRESHOLD_SLOW = 1.20   # 慢 20% 以上算回归
THRESHOLD_FAST = 0.90   # 快 10% 以上算提升


def load_benchmarks(path: str) -> dict:
    with open(path) as f:
        data = json.load(f)
    # pytest-benchmark JSON 结构：data[key]["stats"]
    result = {}
    for item in data.get("benchmarks", []):
        name = item["name"]
        # mean 单位是秒
        result[name] = {
            "mean": item["stats"]["mean"],
            "ops": item["stats"]["ops"],
        }
    return result


def compare(baseline: dict, current: dict) -> list:
    warnings = []
    for name, base_stats in baseline.items():
        if name not in current:
            continue
        cur_stats = current[name]
        base_mean = base_stats["mean"]
        cur_mean = cur_stats["mean"]
        ratio = cur_mean / base_mean if base_mean > 0 else 1.0

        if ratio >= THRESHOLD_SLOW:
            warnings.append(
                f"[回归] {name}: {base_mean:.6f}s → {cur_mean:.6f}s "
                f"(慢 {ratio:.1%})"
            )
        elif ratio <= THRESHOLD_FAST:
            print(
                f"[提升] {name}: {base_mean:.6f}s → {cur_mean:.6f}s "
                f"(快 {(1-ratio):.1%})"
            )
    return warnings


def main():
    if len(sys.argv) != 3:
        print("用法: python benchmark_compare.py baseline.json current.json")
        sys.exit(1)

    baseline_path = sys.argv[1]
    current_path = sys.argv[2]

    if not Path(baseline_path).exists():
        print(f"基线文件不存在: {baseline_path}")
        sys.exit(0)  # 不阻塞

    baseline = load_benchmarks(baseline_path)
    current = load_benchmarks(current_path)

    warnings = compare(baseline, current)

    if warnings:
        print("\n⚠️  性能回归检测结果：")
        for w in warnings:
            print(f"  {w}")
        # 退出码 1 让 CI 知道有回归（但配合 || 只 warning）
        sys.exit(1)
    else:
        print("✅ 无性能回归")


if __name__ == "__main__":
    main()
