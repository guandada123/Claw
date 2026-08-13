#!/usr/bin/env python3
"""
pull_qts_signals.py — 从 QTS 回测日报提取 WF 验证通过的股票信号
=================================================================
服务直连（2026-08-13 打通：废除 docker exec 容器注入，改 qts_client 只读 PG），
读取最新回测日报，提取 WF stability >= 50% 的策略-股票对。

输出：data/qts_daily_signals.json

用法:
  python3 scripts/pull_qts_signals.py
  python3 scripts/pull_qts_signals.py --min-stability 50 --top 10
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_OUTPUT = _PROJECT_ROOT / "data" / "qts_daily_signals.json"


def _connect() -> dict:
    """直连 QTS PG 读取最新回测日报（2026-08-13 打通：废除 docker exec 容器注入）。

    原实现: docker cp + docker exec 注入容器内 Python 脚本读 backtest_reports。
    现实现: qts_client.get_daily_report() 服务直连(只读PG 15432)，零容器依赖。
    """
    from qts_client import get_daily_report

    report = get_daily_report()
    if report is None:
        return {"error": "no_report_in_db", "hint": "回测日报尚未生成，等15:35或手动触发"}
    detail = report.get("detail") or {}
    wf = detail.get("wf_validated", {})
    top = (detail.get("top_strategies") or [])[:15]
    output_signals = []
    for s in top:
        ts_code = s.get("ts_code", "")
        wf_data = wf.get(ts_code, {})
        stability = wf_data.get("stability")
        output_signals.append({
            "ts_code": ts_code,
            "strategy": s.get("strategy", ""),
            "sharpe": s.get("sharpe", 0),
            "total_return": s.get("total_return", 0),
            "win_rate": s.get("win_rate", 0),
            "wf_stability": stability,
            "wf_overfit_ratio": wf_data.get("overfit_ratio"),
            "wf_passed": (stability is not None and stability >= 50),
        })
    return {
        "report_date": report.get("report_date"),
        "report_type": report.get("report_type"),
        "signals": output_signals,
        "total_wf_passed": sum(1 for o in output_signals if o.get("wf_passed")),
    }


def pull(min_stability: float = 50, top_n: int = 10) -> dict[str, Any]:
    """拉取 QTS 回测信号并过滤"""
    raw = _connect()

    if "error" in raw:
        return raw

    signals = raw.get("signals", [])

    # 过滤：仅保留 WF 稳定性 >= min_stability 的信号
    passed = []
    for s in signals:
        wf_pass = s.get("wf_passed")
        wf_stab = s.get("wf_stability")

        if wf_pass is True:
            passed.append(s)
        elif wf_pass is False:
            continue
        elif wf_stab is not None and wf_stab >= min_stability:
            s["wf_passed"] = True
            passed.append(s)
        elif wf_stab is None:
            s["wf_passed"] = False
            s["wf_note"] = "未经过 Walk-Forward 验证（历史数据）"

    # 去重：同一股票只取最优策略
    seen_codes: set[str] = set()
    deduped = []
    for s in passed:
        code = s["ts_code"].split(".")[0]
        if code not in seen_codes:
            deduped.append(s)
            seen_codes.add(code)

    # ── 质量闸门（A-side 护栏，2026-07-29 加）──
    # ⚠️ DO NOT REVERT: 当日回测日报 WF 全部未通过(或样本严重污染)时，
    # 写 quarantine 文件(ok:false)而非空"有效"文件，阻断下游 signal_consensus 消费。
    total = len(signals)
    wf_pass_rate = (len(passed) / total) if total else 0.0
    # ST 污染检测：原始信号里出现 ST/*ST 股票名
    st_hits = [s for s in signals if "ST" in str(s.get("name", "")).upper()]
    quarantine = False
    q_reason = ""
    if total > 0 and len(passed) == 0:
        quarantine = True
        q_reason = f"WF验证全部未通过({len(passed)}/{total})，回测不可信，已隔离"
    elif st_hits:
        quarantine = True
        q_reason = f"回测池含ST股票({len(st_hits)}只，如{st_hits[0].get('name','?')})，样本污染，已隔离"
    elif wf_pass_rate < 0.01 and total >= 100:
        # 万级以上样本却近乎0通过 → 策略库整体失效
        quarantine = True
        q_reason = f"WF通过率{wf_pass_rate:.1%}过低(样本{total})，策略库整体失效，已隔离"

    result = {
        "generated_at": datetime.now().isoformat(),
        "source": "QTS回测日报",
        "report_date": raw.get("report_date"),
        "report_type": raw.get("report_type"),
        "total_signals": total,
        "wf_passed_signals": len(passed),
        "deduped_signals": len(deduped),
        "min_stability": min_stability,
        "ok": not quarantine,
        "quarantine": quarantine,
        "quarantine_reason": q_reason,
        "signals": deduped[:top_n] if not quarantine else [],
    }

    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if quarantine:
        print(f"[QUARANTINE] {q_reason}")

    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="从 QTS 回测日报提取 WF 验证股票信号")
    parser.add_argument("--min-stability", type=float, default=50,
                        help="WF 稳定度最低阈值 (default: 50)")
    parser.add_argument("--top", type=int, default=10,
                        help="最多输出信号数 (default: 10)")
    args = parser.parse_args()

    result = pull(min_stability=args.min_stability, top_n=args.top)

    if "error" in result:
        print(f"❌ {result['error']}", file=sys.stderr)
        sys.exit(1)

    print(f"✅ QTS 信号拉取完成: {result['total_signals']} 总数, "
          f"{result['wf_passed_signals']} WF通过, "
          f"{result['deduped_signals']} 去重输出")

    for s in result["signals"][:5]:
        stab = s.get("wf_stability", "N/A")
        note = s.get("wf_note", f"WF稳{stab}%") if s.get("wf_note") else f"WF稳{stab}%"
        print(f"  {s['ts_code']:12s} {s['strategy']:10s} "
              f"夏普{s['sharpe']:.1f} 收益{s['total_return']:.1f}% {note}")
