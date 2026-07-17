#!/usr/bin/env python3
"""
signal_consensus.py — QTS × 公众号 双源信号共识引擎
=====================================================
对同日同股票的多源信号计算共识评分：
  - 双源同向（都看多/都看空）→ 强共识 +2 权重
  - 双源反向（一看多一看空）→ 分歧 -1 权重
  - 单源独有 → 中性 +1
  - 按信源历史命中率加权

输出：data/signal_consensus.json
  格式：{"date": "2026-07-16", "pairs": [{code, name, qts_signal, gzh_signals, consensus, weight}, ...]}

用法:
  python3 scripts/signal_consensus.py
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 输入文件
_ARTICLE_SIGNALS = _PROJECT_ROOT / ".workbuddy" / "data" / "article_signals.json"
_QTS_SIGNALS = _PROJECT_ROOT / "data" / "qts_daily_signals.json"
_VERIFY_REPORT = _PROJECT_ROOT / ".workbuddy" / "data" / "signal_verify_report.json"
_SOURCE_WEIGHTS = _PROJECT_ROOT / "data" / "source_weights.json"

# 输出文件
_OUTPUT = _PROJECT_ROOT / "data" / "signal_consensus.json"


def _load_article_signals() -> list[dict]:
    """加载公众号信号库"""
    if not _ARTICLE_SIGNALS.exists():
        return []
    try:
        return json.loads(_ARTICLE_SIGNALS.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _load_qts_signals() -> dict:
    """加载 QTS 回测信号"""
    if not _QTS_SIGNALS.exists():
        return {"signals": [], "note": "qts_daily_signals.json 未生成（回测日报尚未运行）"}
    try:
        data = json.loads(_QTS_SIGNALS.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"signals": [], "note": "qts_daily_signals.json 解析失败"}
    if "error" in data:
        return {"signals": [], "note": f"QTS拉取失败: {data['error']}"}
    return data


def _load_source_weights() -> dict[str, float]:
    """加载各信源历史权重"""
    default_weights = {
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
    if not _SOURCE_WEIGHTS.exists():
        return default_weights
    try:
        data = json.loads(_SOURCE_WEIGHTS.read_text(encoding="utf-8"))
        weights = data.get("weights", data)
        weights.setdefault("_default", 0.5)
        return weights
    except (json.JSONDecodeError, OSError):
        return default_weights


def _load_verify_weights() -> dict[str, float]:
    """从 signal_verify_report 读取已验证的公众号权重"""
    if not _VERIFY_REPORT.exists():
        return {}
    try:
        data = json.loads(_VERIFY_REPORT.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    weights = {}
    for row in data.get("ranking", []):
        wr = row.get("win_rate")
        if wr is not None and row.get("total", 0) >= 3:
            # 转换胜率 → 权重: >60%→1.0, 40-60%→0.8, <40%→0.5
            if wr >= 60:
                w = 1.0
            elif wr >= 40:
                w = 0.8
            else:
                w = 0.5
            weights[row["account"]] = w
    return weights


def compute_consensus(today_str: str | None = None) -> dict[str, Any]:
    """计算双源信号共识"""
    today_str = today_str or datetime.now().strftime("%Y年%m月%d日")
    
    article_signals = _load_article_signals()
    qts_data = _load_qts_signals()
    source_weights = _load_source_weights()
    verify_weights = _load_verify_weights()
    
    # 合并权重：已验证的公众号用实际命中率转换的权重
    effective_weights = dict(source_weights)
    effective_weights.update(verify_weights)
    
    # 按股票代码分组公众号信号（同一只股票可能有多个公众号提及）
    gzh_by_code: dict[str, list[dict]] = defaultdict(list)
    for s in article_signals:
        if s.get("source") in ("QTS_COMBO",):
            continue  # QTS COMBO 信号单独处理
        code = s.get("stock_code", "")
        if code:
            gzh_by_code[code].append(s)
    
    # QTS 信号按代码索引
    qts_signals = qts_data.get("signals", [])
    qts_by_code: dict[str, dict] = {}
    for s in qts_signals:
        code = s["ts_code"].split(".")[0]  # "603823.SH" → "603823"
        qts_by_code[code] = s
    
    # 匹配 + 共识计算
    all_codes = set(list(gzh_by_code.keys()) + list(qts_by_code.keys()))
    pairs: list[dict] = []
    
    for code in sorted(all_codes):
        gzh_list = gzh_by_code.get(code, [])
        qts = qts_by_code.get(code)
        
        if not gzh_list and not qts:
            continue
        
        # 公众号信号聚合
        gzh_names = list({s.get("stock_name", "") for s in gzh_list})
        gzh_accounts = list({s.get("account", "") for s in gzh_list})
        
        # 公众号方向（取多数）
        gzh_directions = [s.get("signal", "neutral") for s in gzh_list]
        bullish = gzh_directions.count("bullish")
        bearish = gzh_directions.count("bearish")
        gzh_direction = "bullish" if bullish > bearish else ("bearish" if bearish > bullish else "neutral")
        
        # QTS 方向（从 strategy 推断：breakout/ma-cross 偏看多）
        qts_direction = None
        qts_strategy = None
        if qts:
            qts_strategy = qts.get("strategy", "")
            qts_direction = "bullish"  # 回测 Top 都是正收益策略 → 看多
        
        # 共识计算
        consensus_score = 0
        consensus_label = ""
        detail_parts = []
        
        if gzh_list and qts:
            # 双源覆盖
            if qts_direction == gzh_direction and gzh_direction != "neutral":
                consensus_score = +2
                consensus_label = "🟢 强共识"
                detail_parts.append("QTS×公众号同向，可信度+2")
            elif qts_direction and gzh_direction and qts_direction != gzh_direction:
                consensus_score = -1
                consensus_label = "🔴 分歧"
                detail_parts.append(f"QTS看多 vs 公众号{gzh_direction}，方向冲突")
            else:
                consensus_score = +1
                consensus_label = "🟡 弱信号"
                detail_parts.append("方向中性或单一源")
        elif gzh_list:
            consensus_score = +1
            consensus_label = "🟡 仅公众号"
            detail_parts.append(f"仅公众号覆盖（{len(gzh_list)}个号）")
        elif qts:
            consensus_score = +1
            consensus_label = "🟡 仅QTS"
            detail_parts.append(f"仅QTS覆盖（{qts_strategy}）")
        
        # 权重计算：信源加权 × 共识系数
        account_weights = [
            effective_weights.get(a, effective_weights["_default"])
            for a in gzh_accounts
        ]
        gzh_avg_weight = sum(account_weights) / max(len(account_weights), 1)
        
        qts_weight = effective_weights.get("QTS_BACKTEST", 0.5)
        if qts and qts.get("wf_passed"):
            qts_weight *= 1.3  # WF 验证通过的信源额外加 30%
        
        combined_weight = (gzh_avg_weight + qts_weight) / 2 if (gzh_list and qts) else (gzh_avg_weight if gzh_list else qts_weight)
        final_weight = round(combined_weight * (1 + 0.15 * consensus_score), 2)
        
        pairs.append({
            "code": code,
            "name": (gzh_names[0] if gzh_names else qts.get("ts_code", "")),
            "qts_signal": {
                "strategy": qts_strategy,
                "sharpe": qts.get("sharpe") if qts else None,
                "wf_stability": qts.get("wf_stability") if qts else None,
                "wf_passed": qts.get("wf_passed") if qts else None,
                "direction": qts_direction,
                "weight": round(qts_weight, 2),
            } if qts else None,
            "gzh_signals": [{
                "account": s.get("account", ""),
                "direction": s.get("signal", "neutral"),
                "confidence": s.get("confidence", 0),
                "weight": effective_weights.get(s.get("account", ""), effective_weights["_default"]),
            } for s in gzh_list],
            "gzh_direction": gzh_direction,
            "gzh_accounts": gzh_accounts,
            "consensus_score": consensus_score,
            "consensus_label": consensus_label,
            "consensus_detail": " | ".join(detail_parts),
            "combined_weight": final_weight,
        })
    
    # 按最终权重排序
    pairs.sort(key=lambda x: -x["combined_weight"])
    
    # 统计
    strong = sum(1 for p in pairs if p["consensus_score"] >= 2)
    weak = sum(1 for p in pairs if p["consensus_score"] == 1)
    conflict = sum(1 for p in pairs if p["consensus_score"] < 0)
    
    result = {
        "generated_at": datetime.now().isoformat(),
        "date": today_str,
        "summary": {
            "total_pairs": len(pairs),
            "dual_source": sum(1 for p in pairs if p["qts_signal"] and p["gzh_signals"]),
            "gzh_only": sum(1 for p in pairs if not p["qts_signal"] and p["gzh_signals"]),
            "qts_only": sum(1 for p in pairs if p["qts_signal"] and not p["gzh_signals"]),
            "strong_consensus": strong,
            "weak_signal": weak,
            "conflict": conflict,
        },
        "pairs": pairs,
    }
    
    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    
    return result


if __name__ == "__main__":
    result = compute_consensus()
    s = result["summary"]
    print(f"✅ 信号共识计算完成")
    print(f"   总配对: {s['total_pairs']} | 双源: {s['dual_source']} | "
          f"强共识: {s['strong_consensus']} | 分歧: {s['conflict']}")
    print()
    for p in result["pairs"][:10]:
        print(f"  {p['consensus_label']:8s} {p['code']:8s} {p.get('name',''):10s} "
              f"权重={p['combined_weight']:.2f} | {p['consensus_detail']}")
