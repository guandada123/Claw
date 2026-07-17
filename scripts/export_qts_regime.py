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
import subprocess
import tempfile
import os
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_OUTPUT = _PROJECT_ROOT / "data" / "qts_regime.json"

_QTS_SCRIPT = r"""
import json
try:
    from services.market_regime import MarketRegimeFilter, Regime
    from models.database import get_db_session
    from sqlalchemy import text
    
    # 从 daily_quote 直接取沪深300收盘价（最近500日）
    with get_db_session() as db:
        rows = db.execute(text(
            "SELECT close, high, low FROM daily_quote "
            "WHERE ts_code = '000300.SH' "
            "ORDER BY trade_date DESC LIMIT 500"
        )).fetchall()
    
    if rows and len(rows) >= 50:
        rows_rev = list(reversed(rows))
        closes = [float(r[0]) for r in rows_rev]
        highs = [float(r[1]) for r in rows_rev]
        lows = [float(r[2]) for r in rows_rev]
        
        rf = MarketRegimeFilter()
        regime = rf.classify(closes, highs, lows)
        pos_mult = MarketRegimeFilter.get_position_mult(regime)
    else:
        regime = Regime.OSCILLATE
        pos_mult = 0.5
    
    desc = {
        "bull": "🟢 牛市 — 建议全仓(1.0x)",
        "oscillate": "🟡 震荡 — 建议半仓(0.5x)",
        "bear": "🔴 熊市 — 建议25%仓(0.25x)",
        "transition": "⚠️ 过渡态 — 建议40%仓(0.4x)，等方向明确",
    }
    
    result = {
        "regime": regime.value,
        "regime_label": desc.get(regime.value, str(regime)),
        "position_multiplier": pos_mult,
        "data_points": len(rows) if rows else 0,
    }
    print(json.dumps(result, ensure_ascii=False))
except Exception as e:
    print(json.dumps({"error": str(e), "regime": "oscillate", "position_multiplier": 0.5}))
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
            data = {"error": "no JSON output", "regime": "oscillate", "position_multiplier": 0.5,
                    "regime_label": "🟡 震荡（QTS无响应，默认保守）",
                    "generated_at": datetime.now().isoformat()}
    except Exception as e:
        data = {"error": str(e), "regime": "oscillate", "position_multiplier": 0.5,
                "regime_label": "🟡 震荡（连接失败，默认保守）",
                "generated_at": datetime.now().isoformat()}
    finally:
        os.unlink(tmp)

    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


if __name__ == "__main__":
    result = export()
    print(f"市场状态: {result.get('regime_label', result.get('regime', 'unknown'))}")
    print(f"仓位系数: {result.get('position_multiplier', 0.5)}x")
