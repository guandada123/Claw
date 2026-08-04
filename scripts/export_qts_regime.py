#!/usr/bin/env python3
"""
export_qts_regime.py — 从 QTS 导出当前市场状态（牛/熊/震荡/过渡）
============================================================
在 QTS 容器内执行，通过 Docker exec 调用 MarketRegimeFilter.classify_fast()，
输出市场状态 + 建议仓位到 data/qts_regime.json。

用法:
  python3 scripts/export_qts_regime.py
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_OUTPUT = _PROJECT_ROOT / "data" / "qts_regime.json"

_QTS_SCRIPT = r"""
import json
try:
    from services.market_regime import MarketRegimeFilter
    from models.database import get_db_session
    from sqlalchemy import text

    # 从 daily_quote 直接取沪深300收盘价（最近500日）
    rows_rev = []
    with get_db_session() as db:
        rows = db.execute(text(
            "SELECT close, high, low FROM daily_quote "
            "WHERE ts_code = '000300.SH' "
            "ORDER BY trade_date ASC LIMIT 500"
        )).fetchall()
        # rows: 升序 (close, high, low)
        rows_rev = [(float(r[0]), float(r[1]), float(r[2])) for r in rows]

    if len(rows_rev) < 50:
        # 🔴 治本(08-04)：daily_quote 无指数数据时，腾讯源直拉沪深300兜底，不落库不污染回测池
        import urllib.request
        url = ("https://web.ifzq.gtimg.cn/appstock/app/kline/kline?"
               "param=sh000300,day,2025-01-01,2026-08-04,500")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=10).read().decode("utf-8")
        node = json.loads(raw).get("data", {}).get("sh000300", {})
        days = node.get("day") or node.get("qfqday") or []
        # 腾讯指数K线: [日期, 开, 收, 高, 低, 量] → (close, low, high) 升序
        rows_rev = [(float(k[2]), float(k[4]), float(k[3]))
                    for k in sorted(days, key=lambda x: x[0]) if len(k) >= 5]

    if len(rows_rev) >= 50:
        closes = [r[0] for r in rows_rev]
        highs = [r[2] for r in rows_rev]
        lows = [r[1] for r in rows_rev]

        rf = MarketRegimeFilter()
        regime = rf.classify(closes, highs, lows)
        pos_mult = MarketRegimeFilter.get_position_mult(regime)
        regime_val = regime.value
    else:
        # 🔴 数据不足时禁止硬编码震荡(曾致连续多日误报震荡0.5x)：诚实输出 unknown
        regime_val = "unknown"
        pos_mult = 0.5

    desc = {
        "bull": "🟢 牛市 — 建议全仓(1.0x)",
        "oscillate": "🟡 震荡 — 建议半仓(0.5x)",
        "bear": "🔴 熊市 — 建议25%仓(0.25x)",
        "transition": "⚠️ 过渡态 — 建议40%仓(0.4x)，等方向明确",
        "unknown": "⚪ 数据不足 — 无法判定，保守半仓(0.5x)",
    }

    result = {
        "regime": regime_val,
        "regime_label": desc.get(regime_val, str(regime_val)),
        "position_multiplier": pos_mult,
        "data_points": len(rows_rev) if rows_rev else 0,
    }
    print(json.dumps(result, ensure_ascii=False))
except Exception as e:
    print(json.dumps({"error": str(e), "regime": "unknown", "regime_label": "⚪ QTS异常 — 无法判定（保守半仓）", "position_multiplier": 0.5}))
"""


def export() -> dict:
    """导出 QTS 市场状态"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(_QTS_SCRIPT)
        tmp = f.name

    try:
        subprocess.run(
            ["docker", "cp", tmp, "quant-strategy:/app/_export_regime.py"],
            capture_output=True, timeout=10,
        )
        result = subprocess.run(
            ["docker", "exec", "quant-strategy", "python", "/app/_export_regime.py"],
            capture_output=True, text=True, timeout=30,
        )

        for line in reversed(result.stdout.strip().split("\n")):
            if line.startswith("{"):
                data = json.loads(line)
                data["generated_at"] = datetime.now().isoformat()
                break
        else:
            data = {"error": "no JSON output", "regime": "unknown", "position_multiplier": 0.5,
                    "regime_label": "⚪ QTS无响应 — 无法判定（保守半仓）",
                    "generated_at": datetime.now().isoformat()}
    except Exception as e:
        data = {"error": str(e), "regime": "unknown", "position_multiplier": 0.5,
                "regime_label": "⚪ 连接失败 — 无法判定（保守半仓）",
                "generated_at": datetime.now().isoformat()}
    finally:
        os.unlink(tmp)

    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data  # type: ignore[no-any-return]


if __name__ == "__main__":
    result = export()
    print(f"市场状态: {result.get('regime_label', result.get('regime', 'unknown'))}")
    print(f"仓位系数: {result.get('position_multiplier', 0.5)}x")
