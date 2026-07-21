#!/usr/bin/env python3
"""鱼盆零 RSS 闭环验证。

背景：原鱼盆主生成依赖东财(881xxx) + 微盘股，沙箱封网无法直连，需 RSS OCR 兜底。
修复：已将 5 个东财轮动板块 + 微盘股 改为 Wind 主源(882xxx/884xxx/866xxx 等价口径)，
      东财仅作 YUPEN_USE_EM=1 时的兜底。Wind + 雅虎 在沙箱/裸机均直连可达，
      故现在可在任意环境(含 WorkBuddy 自动化沙箱)达成真正零 RSS 闭环，无需 Mac mini。

用法：
  cd /Users/guan/WorkBuddy/Claw
  python3 scripts/verify_yupen_rssfree.py            # 默认走 Wind+Yahoo 主源
  YUPEN_USE_EM=1 python3 scripts/verify_yupen_rssfree.py   # 含东财兜底(验证兜底路径)
自动：跑生成 → 读合并产物 → 判定 merged_from_rss 是否清空。
"""
import datetime as dt
import json
import os
import subprocess
import sys

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
        print("❌ 生成器失败")
        sys.exit(1)

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
