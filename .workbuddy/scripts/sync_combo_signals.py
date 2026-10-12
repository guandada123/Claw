#!/usr/bin/env python3
"""
sync_combo_signals.py — 将 QTS COMBO / 投顾策略信号同步到 article_signals.json

读取 live_signals_advisor_latest.json，将 COMBO 买卖建议增量写入信号仓库。
信号类型：buy→bullish, sell→bearish, 置信度=COMBO得分
按 code+date 去重（同日同股同方向不重复写入）

用法:
  python3 scripts/sync_combo_signals.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path


# 项目根定位
def _claw_root() -> Path:
    """位置无关地定位仓库根（向上找含 .workbuddy 的目录）。

    2026-10-12 P1-14：原写 `Path(__file__).resolve().parent.parent` ——
    该式**只在 scripts/ 下成立**；一旦搬到 .workbuddy/scripts/，parent.parent 会指到
    `.workbuddy/` 而算出 `.workbuddy/.workbuddy/…`（曾由此误建目录、被守卫抓到）。
    改为向上搜索后，两种位置都指向同一仓库根。
    """
    p = Path(__file__).resolve().parent
    for cand in (p, *p.parents):
        if (cand / ".workbuddy").is_dir():
            return cand
    raise RuntimeError("找不到含 .workbuddy 的仓库根（脚本被放在异常位置？）")


_PROJECT_ROOT = _claw_root()
SIGNALS_FILE = _PROJECT_ROOT / ".workbuddy" / "data" / "article_signals.json"
SIGNALS_INPUT = _PROJECT_ROOT / "output" / "live_signals_advisor_latest.json"


def load_existing_signals():
    if SIGNALS_FILE.exists():
        try:
            return json.loads(SIGNALS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return []


def sync_combo() -> int:
    if not SIGNALS_INPUT.exists():
        return 0

    raw = json.loads(SIGNALS_INPUT.read_text(encoding="utf-8"))
    # 兼容 all/buy 字段的 list/dict 双格式
    all_raw = raw.get("all", [])
    if isinstance(all_raw, list):
        all_items = all_raw + raw.get("sell", [])
    else:
        all_items = raw.get("buy", []) + raw.get("sell", [])
    if not all_items:
        return 0

    existing = load_existing_signals()
    existing_ids = {s.get("article_id") for s in existing}  # O(1) 查重
    now = datetime.now()
    today_ymd = now.strftime("%Y年%m月%d日")
    new_count = 0

    for item in all_items:
        code = item.get("code", "")
        name = item.get("name", "")
        direction = item.get("action", item.get("direction", ""))
        combo = item.get("combo_score", item.get("combo", 0))
        adx = item.get("adx", 0)
        rsi = item.get("rsi", 50)

        if not code or not direction:
            continue

        signal = "bullish" if direction in ("buy", "bullish") else "bearish"
        article_id = hashlib.md5(
            f"COMBO|{code}|{today_ymd}".encode(), usedforsecurity=False
        ).hexdigest()[:12]  # noqa: S324

        if article_id in existing_ids:
            continue

        existing.append(
            {
                "article_id": article_id,
                "account": "QTS_COMBO",
                "title": f"COMBO信号 {code}",
                "stock_code": code,
                "stock_name": name,
                "signal": signal,
                "target_price": None,
                "confidence": int(clamp(combo * 10, 1, 10)),
                "recorded_at": today_ymd,
                "adx": adx,
                "rsi": rsi,
                "combo_raw": combo,
                "source": "QTS_COMBO",
            }
        )
        existing_ids.add(article_id)
        new_count += 1

    if new_count > 0:
        SIGNALS_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(suffix=".json", dir=str(SIGNALS_FILE.parent))
        try:
            os.write(fd, json.dumps(existing, ensure_ascii=False, indent=2).encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, str(SIGNALS_FILE))
    return new_count


def clamp(val, lo, hi):
    return max(lo, min(hi, val))


if __name__ == "__main__":
    n = sync_combo()
    print(f"COMBO信号同步: +{n}条" if n else "COMBO信号同步: 无新信号")
    sys.exit(0)
