#!/usr/bin/env python3
"""
bt_wf_sparsity_analysis.py — WF 0 通过根因专项分析（08-04）
================================================================
核心问题：QTS 回测日报 wf_passed 恒 0（连续 8 天），已修条件反转 Bug，
但深层根因疑为「WF 测试窗口(30天)信号极稀疏(0-2笔)→ stability 天然低」。

本脚本在 quant-strategy 容器内运行，对 22 只股票 × 7 策略做 Walk-Forward：
  1. 全样本回测的交易次数分布（trades 极少=虚高 sharpe 温床）
  2. WF 窗口信号数分布（有信号窗口占比=稀疏度）
  3. stability（盈利窗口占比）分布
  4. 阈值敏感性：不同 stability/overfit 阈值下 wf_passed 数量
  5. 信号稀疏度 vs 通过率的关系（验证「信号稀疏→stability 低」假说）

Usage:
  docker exec quant-strategy python /app/scripts/bt_wf_sparsity_analysis.py
输出: /app/output/bt_wf_sparsity.json + bt_wf_sparsity.html
"""

import json
import sys

sys.path.insert(0, "/app")
from collections import Counter
from pathlib import Path

from services.backtest_engine_v2 import BacktestConfig, EnhancedBacktestEngine
from services.param_grids import get_daily_param_grid

STOCK_NAMES = {
    "002049.SZ": "紫光国微", "600498.SH": "烽火通信", "000725.SZ": "京东方A",
    "600522.SH": "中天科技", "002601.SZ": "龙佰集团", "600206.SH": "有研新材",
    "000001.SZ": "平安银行", "000333.SZ": "美的集团", "002415.SZ": "海康威视",
    "600519.SH": "贵州茅台", "601318.SH": "中国平安", "000858.SZ": "五粮液",
    "600036.SH": "招商银行", "600276.SH": "恒瑞医药", "600887.SH": "伊利股份",
    "600570.SH": "恒生电子", "600585.SH": "海螺水泥", "600893.SH": "航发动力",
    "601899.SH": "紫金矿业", "002230.SZ": "科大讯飞", "300750.SZ": "宁德时代",
    "688981.SH": "中芯国际",
}
TS_CODES = list(STOCK_NAMES.keys())
# 对齐 DEFAULT_STRATEGIES 中的可回测策略（与 report_service 一致）
STRATEGIES = ["vwm", "bollinger", "combo-vwm-bbr", "adx", "kdj", "macd", "ma-cross"]

OUT_JSON = Path("/app/output/bt_wf_sparsity.json")
OUT_HTML = Path("/app/output/bt_wf_sparsity.html")


def run():
    cfg = BacktestConfig(
        ts_codes=TS_CODES, strategies=STRATEGIES,
        start_date="2025-01-01", end_date="2026-08-03", initial_cash=100000,
    )
    engine = EnhancedBacktestEngine(cfg)

    # ① 全样本回测 trades 分布（与 report_service 一致的逐对回测）
    full_trades: Counter = Counter()
    strat_trades: dict[str, list] = {s: [] for s in STRATEGIES}
    per_stock: dict[str, dict] = {}
    all_results = []

    for code in TS_CODES:
        data = engine.fetch_market_data(code, "2025-01-01", "2026-08-03")
        if not data or len(data) < 60:
            continue
        per_stock[code] = {}
        for strat in STRATEGIES:
            try:
                r = engine.run_single_stock(code, strat, data)
                trades = getattr(r, "total_trades", 0) or 0
                sharpe = getattr(r, "sharpe_ratio", 0) or 0
                ret = getattr(r, "total_return", 0) or 0
                full_trades[trades] += 1
                strat_trades[strat].append(trades)
                per_stock[code][strat] = {
                    "trades": trades, "sharpe": round(sharpe, 3), "ret": round(ret * 100, 2),
                }
                all_results.append({"ts_code": code, "strategy": strat, "trades": trades})
            except Exception as e:
                per_stock[code][strat] = {"err": str(e)[:60]}

    # ② WF 窗口信号稀疏度（重点）
    wf_sig_dist: Counter = Counter()      # 每样本「有信号窗口数」分布
    wf_stability_dist: list[float] = []
    wf_samples: list[dict] = []

    for code in TS_CODES:
        for strat in STRATEGIES:
            try:
                wf = engine.walk_forward(
                    code, strat,
                    train_days=120, test_days=30, step_days=40,
                    param_grid=get_daily_param_grid(strat),
                )
                if wf.get("error") or not wf.get("windows"):
                    continue
                windows = wf["windows"]
                if len(windows) < 2:
                    continue
                # 窗口是否有交易：test_return 非 0
                active = [1 if abs(w.get("test_return", 0)) > 1e-9 else 0 for w in windows]
                profitable = sum(1 for w in windows if w["test_return"] > 0)
                stability = profitable / len(windows) * 100
                ratios = [w["test_sharpe"] / w["train_sharpe"] for w in windows if w.get("train_sharpe")]
                overfit = sum(ratios) / len(ratios) if ratios else 0.0
                wf_stability_dist.append(stability)
                wf_sig_dist[sum(active)] += 1
                wf_samples.append({
                    "code": code, "name": STOCK_NAMES[code], "strategy": strat,
                    "windows": len(windows),
                    "active_windows": sum(active),
                    "sparsity": round((len(windows) - sum(active)) / len(windows) * 100, 1),
                    "stability": round(stability, 1),
                    "overfit": round(overfit, 3),
                    "passed": stability >= 50 and overfit <= 0.2,
                    "wf_return": round(wf.get("overall_test_return", 0) * 100, 2),
                })
            except Exception:
                continue

    # ③ 阈值敏感性
    sens = []
    for st_th in (30, 40, 50, 60):
        for of_th in (0.2, 0.3, 0.5):
            n = sum(1 for s in wf_samples if s["stability"] >= st_th and s["overfit"] <= of_th)
            sens.append({"stability": st_th, "overfit": of_th, "passed": n})

    # ④ 信号稀疏 vs 通过率
    sparse_vs_pass = []
    for bucket, lo, hi in [("0-20%", 0, 20), ("20-50%", 20, 50), ("50-80%", 50, 80), ("80-100%", 80, 101)]:
        grp = [s for s in wf_samples if lo <= s["sparsity"] < hi]
        sparse_vs_pass.append({
            "bucket": bucket, "n": len(grp),
            "avg_stability": round(sum(s["stability"] for s in grp) / len(grp), 1) if grp else 0,
            "passed": sum(1 for s in grp if s["passed"]),
        })

    result = {
        "generated_at": "2026-08-04",
        "summary": {
            "stocks": len(TS_CODES),
            "strategies": STRATEGIES,
            "full_backtests": len(all_results),
            "full_trades_dist": dict(sorted(full_trades.items())),
            "wf_samples": len(wf_samples),
            "wf_passed_current_threshold": sum(1 for s in wf_samples if s["passed"]),
            "wf_signal_dist": dict(sorted(wf_sig_dist.items())),
            "avg_stability": round(sum(wf_stability_dist) / len(wf_stability_dist), 1) if wf_stability_dist else 0,
            "avg_sparsity": round(sum(s["sparsity"] for s in wf_samples) / len(wf_samples), 1) if wf_samples else 0,
        },
        "threshold_sensitivity": sens,
        "sparsity_vs_pass": sparse_vs_pass,
        "wf_samples": sorted(wf_samples, key=lambda s: -s["stability"])[:60],
        "per_stock": per_stock,
    }
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def gen_html(r: dict):
    wf = r["wf_samples"]
    rows = "".join(
        f"""<tr class="{ 'pass' if s['passed'] else '' }">
        <td>{s['name']}</td><td>{s['code']}</td><td>{s['strategy'].upper()}</td>
        <td>{s['windows']}</td><td>{s['active_windows']}</td><td>{s['sparsity']}%</td>
        <td>{s['stability']}%</td><td>{s['overfit']}</td><td>{'✅' if s['passed'] else '❌'}</td>
        <td>{s['wf_return']}%</td></tr>"""
        for s in wf
    )
    sens_rows = "".join(
        f"<tr><td>{x['stability']}%</td><td>{x['overfit']}</td><td class='{'pass' if x['passed'] else ''}'>{x['passed']}</td></tr>"
        for x in r["threshold_sensitivity"]
    )
    spv_rows = "".join(
        f"<tr><td>{x['bucket']}</td><td>{x['n']}</td><td>{x['avg_stability']}%</td><td>{x['passed']}</td></tr>"
        for x in r["sparsity_vs_pass"]
    )
    html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>QTS WF 信号稀疏深度分析</title>
<style>
body{{font-family:-apple-system,'PingFang SC',sans-serif;background:#f7f8fa;color:#1f2329;margin:0;padding:24px}}
.card{{background:#fff;border-radius:12px;padding:20px;margin-bottom:20px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:16px;margin:0 0 12px;color:#4e5969}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{padding:6px 10px;border-bottom:1px solid #e5e6eb;text-align:left}}
th{{background:#f2f3f5;position:sticky;top:0}}
.pass{{background:#e8f5e9}} tr.pass td{{background:#e8f5e9}}
.metric{{display:inline-block;margin-right:32px}} .metric b{{font-size:28px;display:block;color:#165dff}}
.warn{{color:#d93026;font-weight:600}}
</style></head><body>
<h1>QTS 回测 WF 信号稀疏深度分析</h1>
<p>生成: {r['generated_at']} ｜ {r['summary']['stocks']} 股票 × {len(r['summary']['strategies'])} 策略 ｜ 当前阈值(stability≥50 且 overfit≤0.2)</p>
<div class="card">
<h2>总览</h2>
<div class="metric"><b>{r['summary']['wf_samples']}</b>WF样本</div>
<div class="metric"><b class="{'warn' if r['summary']['wf_passed_current_threshold']==0 else ''}">{r['summary']['wf_passed_current_threshold']}</b>当前阈值通过</div>
<div class="metric"><b>{r['summary']['avg_stability']}%</b>平均stability</div>
<div class="metric"><b>{r['summary']['avg_sparsity']}%</b>平均窗口稀疏度</div>
</div>
<div class="card"><h2>全样本回测交易次数分布（1-2笔=虚高sharpe温床）</h2>
<pre>{json.dumps(r['summary']['full_trades_dist'], ensure_ascii=False, indent=1)}</pre></div>
<div class="card"><h2>WF 窗口信号分布（key=有信号窗口数）</h2>
<pre>{json.dumps(r['summary']['wf_signal_dist'], ensure_ascii=False, indent=1)}</pre>
<p>大量「0 有信号窗口」= 测试期完全无信号 → stability=0 无意义</p></div>
<div class="card"><h2>信号稀疏度 vs 通过率（验证假说）</h2>
<table><tr><th>稀疏度区间</th><th>样本数</th><th>平均stability</th><th>通过数</th></tr>{spv_rows}</table></div>
<div class="card"><h2>阈值敏感性（stability / overfit 双阈值）</h2>
<table><tr><th>stability≥</th><th>overfit≤</th><th>通过数</th></tr>{sens_rows}</table></div>
<div class="card"><h2>Top 60 WF 样本（按 stability 排序）</h2>
<table><tr><th>股票</th><th>代码</th><th>策略</th><th>窗口数</th><th>有信号窗口</th><th>稀疏度</th><th>stability</th><th>overfit</th><th>通过</th><th>WF收益</th></tr>{rows}</table></div>
</body></html>"""
    OUT_HTML.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    res = run()
    gen_html(res)
    print(json.dumps(res["summary"], ensure_ascii=False, indent=1))
    print(f"\n✅ 输出: {OUT_JSON} / {OUT_HTML}")
