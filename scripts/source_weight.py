#!/usr/bin/env python3
"""
source_weight.py — 信源权重动态调整引擎
==========================================
基于 signal_verify_report.json 的逐源命中率，动态计算权重：
  - 命中率 > 60% → 权重 1.0（高信源，重点采信）
  - 命中率 40-60% → 权重 0.8（中等，谨慎参考）
  - 命中率 < 40% → 权重 0.5（低信源，打折处理）
  - 信号数 < 3 → 权重 0.5（样本不足，默认保守）
  - QTS_BACKTEST 初始权重 0.5，需积累 WF 验证数据后上调

每隔 24h 自动更新权重（由 signal_verify.py 触发，仅读取）

输出：data/source_weights.json
  格式：{"好运侠客": 1.0, "君临木": 0.8, ...}

用法:
  python3 scripts/source_weight.py
  python3 scripts/source_weight.py --force  # 强制重新计算
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_VERIFY_REPORT = _PROJECT_ROOT / ".workbuddy" / "data" / "signal_verify_report.json"
_OUTPUT = _PROJECT_ROOT / "data" / "source_weights.json"


def _win_rate_to_weight(win_rate: float, signals: int) -> float:
    """将胜率 + 信号数映射为权重"""
    if signals < 3:
        return 0.5  # 样本不足
    
    if win_rate >= 60:
        return 1.0  # 高信源
    elif win_rate >= 40:
        return 0.8  # 中等
    elif win_rate >= 20:
        return 0.5  # 较低
    else:
        return 0.3  # 很低——几乎反向指标


def compute_weights() -> dict[str, Any]:
    """从 verify_report 计算各信源权重"""
    
    # 基础权重（未经验证时的默认值）
    base_weights: dict[str, float] = {
        "好运侠客": 1.0,
        "君临木": 0.8,
        "飞龙山侠": 0.7,
        "红鼻子小丑": 0.7,
        "恩哥箴言": 0.6,
        "城市金融报": 0.5,
        "QTS_COMBO": 0.6,
        "QTS_BACKTEST": 0.5,
        "_default": 0.5,
    }
    
    if not _VERIFY_REPORT.exists():
        result = {
            "generated_at": datetime.now().isoformat(),
            "source": "默认（无验证数据）",
            "weights": base_weights,
        }
        _write_output(result)
        return result
    
    data = json.loads(_VERIFY_REPORT.read_text(encoding="utf-8"))
    ranking = data.get("ranking", [])
    
    weights: dict[str, float] = {}
    details: list[dict] = []
    
    for row in ranking:
        account = row.get("account", "")
        win_rate = row.get("win_rate")
        total = row.get("total", 0)
        avg_return = row.get("avg_return", 0)
        
        if win_rate is not None:
            weight = _win_rate_to_weight(win_rate, total)
            weights[account] = weight
            details.append({
                "account": account,
                "win_rate": win_rate,
                "signals": total,
                "avg_return": round(avg_return, 2),
                "weight": weight,
                "rationale": (
                    f"胜率{win_rate}%/信号{total}条" +
                    (" → 高信源" if weight >= 1.0 else
                     " → 中等" if weight >= 0.8 else
                     " → 低信源" if weight >= 0.5 else
                     " → 极低（接近反向指标）")
                ),
            })
    
    # 合并：已验证的用动态权重，未验证的保留默认
    final_weights = dict(base_weights)
    final_weights.update(weights)
    final_weights.setdefault("_default", 0.5)
    
    result = {
        "generated_at": datetime.now().isoformat(),
        "source": f"signal_verify_report ({data.get('generated_at', 'N/A')})",
        "verified_accounts": len(weights),
        "weights": final_weights,
        "details": details,
    }
    
    _write_output(result)
    return result


def _write_output(data: dict):
    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    result = compute_weights()
    print(f"✅ 信源权重更新完成: {result['verified_accounts']} 个已验证账号")
    print(f"   数据源: {result['source']}")
    for d in result.get("details", [])[:8]:
        print(f"   {d['account']:15s} 胜率{d['win_rate']:.1f}% 信号{d['signals']}条 → 权重{d['weight']:.1f} {d['rationale']}")
    
    # 检查有无权重变化
    old_file = _PROJECT_ROOT / "data" / "source_weights.json"
    if old_file.exists():
        old_data = json.loads(old_file.read_text(encoding="utf-8"))
        old_weights = old_data.get("weights", {})
        new_weights = result["weights"]
        changes = [
            (k, old_weights[k], new_weights[k])
            for k in set(old_weights) & set(new_weights)
            if old_weights[k] != new_weights[k]
        ]
        if changes:
            print(f"\n⚠️ 权重变化: {len(changes)} 项")
            for k, old, new in changes:
                direction = "↑" if new > old else "↓"
                print(f"   {k}: {old:.1f} → {new:.1f} {direction}")
