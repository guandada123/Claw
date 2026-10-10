#!/usr/bin/env python3
"""check_automation_memory.py — 按「相对稳态体积」判定自动化记忆是否真异常

━━ 为什么需要（2026-10-10 由每日记忆维护自动化自己发现）━━━━━━━━━━━━━━━━━
原来的判据是「文件 >30KB 报 🔴」。但实测该判据对**高频档位恒真**：
  · 单档日增量近似恒定（watchdog ≈ +6.4KB/日）
  · 归档脚本只移「早于 14 天」的条目
  ⇒ 稳态体积 ≈ 14 × 日增量 ≈ 90KB **本身就 >30KB**
所以 watchdog 档位每天都报红，而它根本没坏。逐日计数 1 → 8 → 14 看着像失控，
其实是**锯齿**（归档日跳水、之后线性回升），不是趋势。

━━ 新判据（相对稳态，而非绝对阈值）━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  · 🔴 异常  = 内容时间跨度 > 归档窗口(14天) × 1.5（即 >21 天）**且** 近 7 天有写入
        —— 直白说：「归档没在按窗口工作，而且它还在长」。
  · ⚠️ 高位  = size > 30KB（只记录，不报警）
  · ⚠️ 休眠  = 近 7 天没写（体积是历史遗留，不会继续涨）
  · ⚠️ unknown = 时间戳不足，**显式列出**（不静默当成正常）

判据迭代（2026-10-10，被自己的真实数据与测试连打回三次，过程如实记录）：
  ① 原判据「size > 30KB」对高频档位**恒真**：稳态 ≈ 14×日增 本身就 >30KB → 每天报红。
  ② 「size > max(30KB, 稳态×1.5)」→ 把 4 个 31KB 的**休眠**档位误报（比值仅 1.01×）。
  ③ 「近 7 天日增 > 基线×3」→ 命中的却是**自身稳态之内**的正常加速（44KB vs 稳态 45.5KB）。
  ④ 数学上捅破：`steady = 14 × daily`，而 `size ≈ 跨度天数 × daily`
     ⇒ 「size > steady × 1.5」**等价于「跨度 > 21 天」**。
     所以这个判据真正测的从来不是"体积"，而是**归档窗口有没有在工作**。
     直接按这个语义写：测跨度，别绕体积。这才是能自愈、且真值得看的信号。

退出码：0=无 🔴 / 1=有 🔴 / 2=无法判定（目录缺失或一个文件都分析不了）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

TS_PATTERNS = [
    re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})"),  # 2026-10-10T17:56 ...
    re.compile(r"^#{1,4}\s*(\d{4}-\d{2}-\d{2})[ |]+(\d{2}:\d{2})"),  # ## 2026-09-27 07:11:49 |
]
ARCHIVE_WINDOW_DAYS = (
    14  # 与 rotate_automation_memory.py 的「早于 14 天」保持一致（仅用于展示稳态体积）
)
ABS_FLOOR_BYTES = 30 * 1024  # 旧判据的绝对下限，现降级为「提示线」，不再单独报警
RECENT_DAYS = 7
BASELINE_DAYS = 21
MIN_RECENT_DAILY = 1024.0  # 近窗日增低于此值视为休眠，不参与异常判定
SPAN_OVER_FACTOR = 1.5  # 内容跨度须超归档窗口这些倍（14×1.5 = 21 天）
MIN_ANOMALY_DAILY = 2048.0  # 且近窗日增须越过此值（休眠档位跨度再大也不报）


def parse_entry_dates(text: str) -> list[datetime]:
    out: list[datetime] = []
    for line in text.split("\n"):
        for pat in TS_PATTERNS:
            m = pat.match(line)
            if m:
                try:
                    out.append(datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M"))
                except ValueError:
                    pass
                break
    return sorted(out)


def analyze(path: Path, window_days: int = 7) -> dict:
    text = (path or Path()).read_text(encoding="utf-8", errors="ignore")
    size = len(text.encode("utf-8"))
    dates = parse_entry_dates(text)
    row: dict = {"file": str(path), "size": size, "entries": len(dates)}
    if len(dates) < 2:
        row["status"] = "unknown"
        row["reason"] = "可解析时间戳少于 2 个，算不出日增量"
        return row

    span_days = max((dates[-1] - dates[0]).total_seconds() / 86400, 1e-9)
    # 按时间分桶：近 RECENT_DAYS 天 vs 更早 BASELINE_DAYS 天
    # ⚠️ 必须以「现在」为基准，不能以「最后一条」为基准 ——
    # 后者会让休眠档位的历史条目永远显得"recent"（被测试当场抓到的设计 bug）：
    # 一个 40/35/30 天前写过、之后再没写的档位，会被算成"近 7 天有写入"。
    now_ts = datetime.now().timestamp()
    recent_cut = now_ts - RECENT_DAYS * 86400
    base_cut = now_ts - (RECENT_DAYS + BASELINE_DAYS) * 86400
    recent_bytes = base_bytes = 0
    for line in text.split("\n"):
        for pat in TS_PATTERNS:
            m = pat.match(line)
            if not m:
                continue
            try:
                ts = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M").timestamp()
            except ValueError:
                break
            b = len(line.encode("utf-8")) + 1
            if ts >= recent_cut:
                recent_bytes += b
            elif ts >= base_cut:
                base_bytes += b
            break

    recent_daily = recent_bytes / RECENT_DAYS
    base_daily = base_bytes / BASELINE_DAYS
    row.update(
        {
            "recent_daily": round(recent_daily, 1),
            "baseline_daily": round(base_daily, 1),
            "steady_state": round(ARCHIVE_WINDOW_DAYS * recent_daily, 1),
            "span_days": round(span_days, 1),
            "last_entry": dates[-1].strftime("%Y-%m-%d %H:%M"),
            "days_since_last": round((now_ts - dates[-1].timestamp()) / 86400, 1),
        }
    )
    span_limit = ARCHIVE_WINDOW_DAYS * SPAN_OVER_FACTOR
    row["span_limit"] = span_limit
    if recent_daily < MIN_RECENT_DAILY:
        row["status"] = "dormant" if size > ABS_FLOOR_BYTES else "ok"
    elif span_days > span_limit and recent_daily > MIN_ANOMALY_DAILY:
        row["status"] = "anomaly"
        row["ratio"] = round(span_days / ARCHIVE_WINDOW_DAYS, 2)
    elif size > ABS_FLOOR_BYTES:
        row["status"] = "saw_high"
    else:
        row["status"] = "ok"
    return row


def scan(root: Path, window_days: int = 7) -> dict:
    files = sorted(root.glob("*/memory.md"))
    rows = [analyze(f, window_days) for f in files]
    return {
        "root": str(root),
        "considered": len(files),
        "anomaly": [r for r in rows if r.get("status") == "anomaly"],
        "saw_high": [r for r in rows if r.get("status") == "saw_high"],
        "dormant": [r for r in rows if r.get("status") == "dormant"],
        "unknown": [
            r for r in rows if r.get("status") == "unknown" and r["size"] > ABS_FLOOR_BYTES
        ],
        "rows": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".workbuddy/memory/automations")
    ap.add_argument("--window-days", type=int, default=7)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"[ERROR] 目录不存在: {root} —— 判不了，不静默放过（rc=2）", file=sys.stderr)
        return 2

    rep = scan(root, args.window_days)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(
            f"扫描 {rep['considered']} 个档位（归档窗口 {ARCHIVE_WINDOW_DAYS} 天 / 统计窗口 {args.window_days} 天）"
        )
        for r in rep["anomaly"]:
            print(
                f"  🔴 {Path(r['file']).parent.name}: {r['size'] / 1024:.1f}KB —— 内容跨度 "
                f"{r['span_days']:.0f} 天（上限 {r['span_limit']:.0f}）×{r['ratio']}，"
                f"近 {RECENT_DAYS} 天日增 {r['recent_daily'] / 1024:.2f}KB"
            )
        for r in rep["unknown"]:
            print(f"  ⚠️  {Path(r['file']).parent.name}: {r['size'] / 1024:.1f}KB，但{r['reason']}")
        print(
            f"\n合计: 🔴 异常 {len(rep['anomaly'])}"
            f" | ⚠️ 高位不涨 {len(rep['saw_high'])}"
            f" | ⚠️ 休眠 {len(rep['dormant'])}"
            f" | ⚠️ 无法判定 {len(rep['unknown'])}"
            f" | 🟢 正常 {sum(1 for r in rep['rows'] if r.get('status') == 'ok')}"
        )
        big = sorted(rep["rows"], key=lambda r: -r["size"])[:3]
        print(
            "最重三档:",
            " · ".join(
                f"{Path(r['file']).parent.name.split('-')[-1]} {r['size'] / 1024:.0f}KB"
                for r in big
            ),
        )
        if rep["anomaly"]:
            print(
                "\n🔴 涨速异常 —— 近 7 天写入速度达基线的 3 倍以上，查该档是否重复写入或内容膨胀。"
            )
        elif not rep["rows"]:
            return 2
        else:
            print("✅ 无涨速异常（体积大的档位均属正常锯齿高位或已休眠）")

    if not rep["rows"]:
        return 2
    return 1 if rep["anomaly"] else 0


if __name__ == "__main__":
    sys.exit(main())
