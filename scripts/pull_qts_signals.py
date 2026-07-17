#!/usr/bin/env python3
"""
pull_qts_signals.py — 从 QTS 回测日报提取 WF 验证通过的股票信号
=================================================================
通过 Docker exec 在 QTS 容器内执行 Python 脚本，读取最新回测日报，
提取 WF stability >= 50% 的策略-股票对。

输出：data/qts_daily_signals.json

用法:
  python3 scripts/pull_qts_signals.py
  python3 scripts/pull_qts_signals.py --min-stability 50 --top 10
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_OUTPUT = _PROJECT_ROOT / "data" / "qts_daily_signals.json"

_QTS_SCRIPT = r"""
import json, sys
try:
    from models.database import get_db_session
    from sqlalchemy import text
    with get_db_session() as db:
        rows = db.execute(text(
            "SELECT report_type, report_date, detail_content "
            "FROM backtest_reports "
            "ORDER BY created_at DESC LIMIT 1"
        )).fetchall()
        if not rows:
            print(json.dumps({"error": "no_report_in_db", "hint": "回测日报尚未生成，等15:35或手动触发"}))
            sys.exit(0)
        r = rows[0]
        data = r[2] if isinstance(r[2], dict) else json.loads(r[2])
        wf = data.get("wf_validated", {})
        top = data.get("top_strategies", [])[:15]
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
        result = {
            "report_date": str(r[1]) if r[1] else None,
            "report_type": r[0],
            "signals": output_signals,
            "total_wf_passed": sum(1 for o in output_signals if o.get("wf_passed")),
        }
        print(json.dumps(result, ensure_ascii=False))
except Exception as e:
    print(json.dumps({"error": str(e)}))
"""


def _connect() -> dict:
    """通过 Docker exec 在 QTS 容器内执行查询"""
    # 把脚本写入临时文件
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(_QTS_SCRIPT)
        tmp_path = f.name

    try:
        # 复制脚本到容器（用 /app 目录避免权限问题）
        subprocess.run(
            ["docker", "cp", tmp_path, "quant-strategy:/app/_pull_signals.py"],
            capture_output=True, text=True, timeout=10,
        )
        result = subprocess.run(
            ["docker", "exec", "quant-strategy",
             "python", "/app/_pull_signals.py"],
            capture_output=True, text=True, timeout=30,
        )
        
        if result.returncode != 0:
            return {"error": f"docker exec failed: {result.stderr[:200]}"}
        
        output = result.stdout.strip()
        # 提取最后一行 JSON
        for line in reversed(output.split("\n")):
            line = line.strip()
            if line.startswith("{"):
                return json.loads(line)
        
        return {"error": f"no JSON in output: {output[:200]}"}
    finally:
        os.unlink(tmp_path)


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

    result = {
        "generated_at": datetime.now().isoformat(),
        "source": "QTS回测日报",
        "report_date": raw.get("report_date"),
        "report_type": raw.get("report_type"),
        "total_signals": len(signals),
        "wf_passed_signals": len(passed),
        "deduped_signals": len(deduped),
        "min_stability": min_stability,
        "signals": deduped[:top_n],
    }

    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

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
