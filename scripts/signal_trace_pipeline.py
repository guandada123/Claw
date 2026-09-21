#!/usr/bin/env python3
"""信号溯源 + 升降级 + CSV 全流程编排脚本（2026-09-05 新增）。

背景（为什么要这个脚本）
------------------------
原「📊【知识库】信号溯源+升降级+CSV(05:00)」由 LLM agent 逐步执行 7 条 Bash 命令。
实测各环节**真实耗时**：

    sync_combo_signals.py        0 秒
    pull_qts_signals.py          1 秒
    export_qts_regime.py         1 秒
    signal_verify            338 秒  ← 98% 的时间都在这一个脚本（行情验证，网络密集）
    compute_signal_weights.py    1 秒
    source_weight.py             0 秒
    signal_consensus.py          0 秒
    ─────────────────────────────────
    脚本合计                  ≈ 5.7 分钟

而该自动化 2026-09-01 成功那次**总耗时 8.6 分钟** —— 即 LLM 逐步编排的开销约 3 分钟。

问题出现在系统拥堵时：调度器超发（在飞峰值 21→26，concurrency=3 未生效），
LLM 编排开销随队列膨胀近 30 倍（3 分钟 → 80+ 分钟），总时长撞上 90 分钟硬超时：

    09-02 05:05   36.6 分  interrupted
    09-03 05:05   90.0 分  Run timed out   ← 撞硬超时
    09-04 05:05   90.0 分  Run timed out   ← 撞硬超时
    09-05 05:05   82.8 分  terminated

后果：三个数据产物（qts_regime / signal_consensus / source_weights）从 09-02 起
停更 73 小时，选股与策略一直在消费三天前的陈旧数据。

修法
----
把纯脚本编排的部分从「LLM 逐步执行」改为「一个脚本串行执行」，agent 只需执行
1 条命令。编排开销 3 分钟 → 0，且**不再随 LLM 队列膨胀**，总时长稳定在 ~6 分钟，
远离 90 分钟硬超时。同时释放并发槽位、省下 reasoner 调用成本。

设计原则
--------
1. **单步失败不中断全流程**：某个脚本挂掉不应连坐后续步骤（原 agent 模式正是
   某步卡住导致整轮超时）。错误全部记录下来，最后汇总。
2. **每步计时并打印**：让耗时分布可见，下次定位不用再逐个手测。
3. **纯编排，不做业务判断**：推送与否仍由上层 agent 依据本脚本输出决定，
   业务语义不变，只是把「执行」从 LLM 手里拿走。

用法
----
    python3 scripts/signal_trace_pipeline.py          # 全流程
    python3 scripts/signal_trace_pipeline.py --quiet  # 只打印汇总
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

CLAW_ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

# 每一步：(步骤名, 命令列表, 是否关键)
# critical=True 的步骤失败会在退出码中体现，用于上层判断是否需要告警。
STEPS: list[tuple[str, list[str], bool]] = [
    (
        "STEP0-1 COMBO信号同步",
        [str(PY), str(CLAW_ROOT / "scripts" / "sync_combo_signals.py")],
        False,
    ),
    (
        "STEP0-2 QTS回测拉取",
        [
            str(PY),
            str(CLAW_ROOT / "scripts" / "pull_qts_signals.py"),
            "--min-stability",
            "50",
            "--top",
            "10",
        ],
        False,
    ),
    (
        "STEP0-3 市场状态导出",
        [str(PY), str(CLAW_ROOT / "scripts" / "export_qts_regime.py")],
        True,  # 产出 qts_regime.json，早报/晚报直接引用
    ),
    (
        "STEP1 行情验证(signal_verify)",
        [str(PY), "-m", "claw.monitoring.signal_verify"],
        True,
    ),
    (
        "STEP2-1 信源权重更新",
        [str(PY), str(CLAW_ROOT / ".workbuddy" / "scripts" / "compute_signal_weights.py")],
        False,
    ),
    (
        "STEP2-2 权重落盘",
        [str(PY), str(CLAW_ROOT / "scripts" / "source_weight.py")],
        True,  # 产出 source_weights.json
    ),
    (
        "STEP2-3 共识计算",
        [str(PY), str(CLAW_ROOT / "scripts" / "signal_consensus.py")],
        True,  # 产出 signal_consensus.json
    ),
]

# STEP1 依赖 claw 包（位于 src/），需显式注入 PYTHONPATH。
# 2026-08-31 该自动化曾因缺 PYTHONPATH 报 ModuleNotFoundError，此处固化修正。
# 2026-09-05 审计修正：原实现用 {"PYTHONPATH": src} **整体覆盖**，会丢掉
#   preamble 前置追加保留的其他路径（preamble 是 "$CLAW/src:$PYTHONPATH" 追加式，
#   覆盖式与之语义冲突）。改为**去重 + 前置**：保证 src 在首位，其余原样保留。
def _child_env() -> dict:
    import os

    src = str(CLAW_ROOT / "src")
    existing = os.environ.get("PYTHONPATH", "").strip()
    parts = [src]
    parts += [p for p in existing.split(":") if p and p not in parts]
    return {**os.environ, "PYTHONPATH": ":".join(parts)}

# 单步超时（秒）。signal_verify 实测 338 秒，给足余量但不放任其拖垮全流程：
# 全步加起来仍须远离 90 分钟硬超时。
STEP_TIMEOUT = 900


def run_step(name: str, cmd: list[str]) -> dict:
    """执行单个步骤，返回 {name, ok, seconds, tail}。异常不抛出。"""
    t0 = time.time()
    try:
        import os

        proc = subprocess.run(
            cmd,
            cwd=str(CLAW_ROOT),
            env=_child_env(),
            capture_output=True,
            text=True,
            timeout=STEP_TIMEOUT,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return {
            "name": name,
            "ok": proc.returncode == 0,
            "rc": proc.returncode,
            "seconds": round(time.time() - t0, 1),
            "tail": "\n".join(out.strip().splitlines()[-6:]),
        }
    except subprocess.TimeoutExpired:
        return {
            "name": name,
            "ok": False,
            "rc": None,
            "seconds": round(time.time() - t0, 1),
            "tail": f"⏱ 单步超时（>{STEP_TIMEOUT}s），已跳过",
        }
    except Exception as e:  # noqa: BLE001
        return {
            "name": name,
            "ok": False,
            "rc": None,
            "seconds": round(time.time() - t0, 1),
            "tail": f"❌ 执行异常: {e}",
        }


def main() -> int:
    ap = argparse.ArgumentParser(description="信号溯源全流程编排")
    ap.add_argument("--quiet", action="store_true", help="仅打印汇总，不打印每步输出")
    args = ap.parse_args()

    t_start = time.time()
    results = []
    for name, cmd, _critical in STEPS:
        r = run_step(name, cmd)
        results.append((r, _critical))
        flag = "✅" if r["ok"] else "❌"
        print(f"{flag} {name}  ({r['seconds']}s)", flush=True)
        if not args.quiet and r["tail"]:
            for line in r["tail"].splitlines():
                print(f"      {line}", flush=True)

    total = round(time.time() - t_start, 1)
    failed = [r for r, _ in results if not r["ok"]]
    crit_failed = [r for r, c in results if not r["ok"] and c]

    # 产物新鲜度自检：直接回读文件 mtime，验证「跑了」是否等于「写出了」
    products = []
    for rel in ("qts_regime.json", "signal_consensus.json", "source_weights.json"):
        p = CLAW_ROOT / "data" / rel
        if p.exists():
            import datetime as _dt

            mtime = _dt.datetime.fromtimestamp(p.stat().st_mtime)
            age_h = (_dt.datetime.now() - mtime).total_seconds() / 3600
            products.append(
                {
                    "file": rel,
                    "mtime": mtime.strftime("%Y-%m-%d %H:%M:%S"),
                    "age_hours": round(age_h, 1),
                    "fresh": age_h < 1,
                }
            )
        else:
            products.append({"file": rel, "mtime": None, "age_hours": None, "fresh": False})

    print()
    print("=" * 62)
    print(f"⏱ 全流程耗时: {total}s（{total / 60:.1f} 分钟）")
    print(f"📊 步骤: {len(results) - len(failed)}/{len(results)} 成功")
    print("📦 产物新鲜度:")
    for p in products:
        mark = "✅" if p["fresh"] else "⚠️"
        age = f"{p['age_hours']}h前" if p["age_hours"] is not None else "缺失"
        print(f"   {mark} {p['file']:26s} {p['mtime'] or '—'}  ({age})")

    if failed:
        print("\n❌ 失败步骤:")
        for r in failed:
            print(f"   · {r['name']}  rc={r['rc']}  {r['tail'][:120]}")

    print("=" * 62)

    # 退出码：关键步骤失败 → 1（上层可据此告警）；仅非关键步骤失败 → 0（不噪音）
    return 1 if crit_failed else 0


if __name__ == "__main__":
    sys.exit(main())
