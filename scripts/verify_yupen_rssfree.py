#!/usr/bin/env python3
"""鱼盆零 RSS 闭环验证（Mac mini 裸机终端运行，非 WorkBuddy 自动化沙箱）。

为什么需要裸机：
  WorkBuddy 自动化沙箱封了东财(push2his HTTP 000)，而鱼盆主生成自动化 1784605310235
  运行在沙箱内，故其 primary 产物只能 9/14+19/20，靠 RSS 兜底补 5 东财轮动+微盘股。
  本脚本需在 Mac mini 裸机终端跑（东财直连可达）：
      cd /Users/guan/WorkBuddy/Claw
      YUPEN_USE_EM=1 python3 scripts/verify_yupen_rssfree.py
  自动：跑生成(YUPEN_USE_EM=1 东财直连) → 读合并产物 → 判定 merged_from_rss 是否清空。
"""
import os
import sys
import json
import subprocess
import datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    os.environ["YUPEN_USE_EM"] = "1"
    today = dt.date.today().isoformat()
    print(f"[1/2] 生成 yupen 主表 (YUPEN_USE_EM=1, date={today}) ...")
    r = subprocess.run(
        [sys.executable, "scripts/build_yupen_from_market.py",
         "--date", today, "--no-selfcheck"],
        cwd=ROOT,
    )
    if r.returncode != 0:
        print("❌ 生成器失败"); sys.exit(1)

    print("[2/2] 读取合并产物，检查 RSS 兜底项 ...")
    out = json.loads(subprocess.run(
        [sys.executable, ".workbuddy/scripts/read_yupen_data.py"],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout)
    sr = out.get("sector_rotation") or {}
    tr = out.get("yupen_trend") or {}
    rss_s = sr.get("_merged_from_rss") or []
    rss_t = tr.get("_merged_from_rss") or []
    print(f"板块轮动: {len(sr.get('sectors', []))}/14  | RSS兜底: {rss_s or '无'}")
    print(f"鱼盆趋势: {len(tr.get('sectors', []))}/20  | RSS兜底: {rss_t or '无'}")
    if not rss_s and not rss_t:
        print("✅ 真正零 RSS 闭环达成（公众号发文不再影响鱼盆表）")
    else:
        print("⚠️ 仍依赖 RSS 兜底（东财/雅虎可能不可达，或检查网络）")


if __name__ == "__main__":
    main()
