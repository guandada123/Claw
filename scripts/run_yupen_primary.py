#!/usr/bin/env python3
"""run_yupen_primary.py — 鱼盆每日主生成器（替代 RSS OCR 链路）

流程:
  1. 调用 build_yupen_from_market.py 生成两张表(板块轮动+鱼盆趋势)，写入 output/yupen/
  2. 校验产物可被 read_yupen_data 读取
  3. 打印结构化摘要（供自动化 prompt / 日志消费）

设计: 产出 yupen_primary_<today>_* 文件（当日日期，primary_ 前缀隔离，避免被 RSS 同名文件覆盖）。
read_yupen_data 优先采用 primary 文件；Wind 未覆盖的缺口板块（如东财行业指数/台韩金银）
自动从 RSS 产物(08:50 兜底)合并补全。仅当 primary 完全缺失时，才纯用 RSS 产物。

退出码: 0（即使部分板块缺失也成功，由下游决定是否告警）
"""
import subprocess, sys, os, json
from datetime import date

CLAW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.environ.get("PYTHON", "/Users/guan/.workbuddy/binaries/python/envs/default/bin/python")
GEN = os.path.join(CLAW, "scripts", "build_yupen_from_market.py")
READ = os.path.join(CLAW, ".workbuddy", "scripts", "read_yupen_data.py")
TODAY = date.today().isoformat()


def main():
    print(f"=== 鱼盆主生成器 {TODAY} ===")
    r = subprocess.run([PY, GEN, "--date", TODAY, "--no-selfcheck"], cwd=CLAW)
    if r.returncode != 0:
        print(f"[WARN] generator 返回非零 {r.returncode}，可能 Wind 不可用")
    # 校验
    try:
        out = subprocess.run([PY, READ], cwd=CLAW, capture_output=True,
                              text=True, timeout=30)
        data = json.loads(out.stdout)
        sr = data.get("sector_rotation") or {}
        tr = data.get("yupen_trend") or {}
        n_sr = len(sr.get("sectors", [])) if isinstance(sr, dict) else 0
        n_tr = len(tr.get("sectors", [])) if isinstance(tr, dict) else 0
        print(f"[校验] sector_rotation={n_sr}板 | yupen_trend={n_tr}指数 | "
              f"freshness={data.get('freshness')} data_date={data.get('data_date')}")
        if n_sr == 0:
            print("[FAIL] 板块轮动为空，需 RSS 兜底")
            return 1
        print("[OK] 鱼盆主数据已就绪，下游(早报/推送)可消费")
        return 0
    except Exception as e:
        print(f"[ERR] 校验失败: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
