#!/usr/bin/env python3
"""
策略回测深度分析 — 全量跑 VWM / BBR / COMBO / ADX 四大策略
在 quant-strategy 容器内执行，结果写共享卷 → 容器外生成 HTML 报告

Usage:
  docker exec quant-strategy python /app/scripts/bt_deep_analysis.py
"""
import json
import os
import sys

sys.path.insert(0, "/app")
from pathlib import Path

from services.backtest_engine_v2 import BacktestConfig, EnhancedBacktestEngine

# ============================================================
# 股票池
# ============================================================
STOCK_NAMES = {
    "002049.SZ": "紫光国微", "600498.SH": "烽火通信", "000725.SZ": "京东方A",
    "600522.SH": "中天科技", "002601.SZ": "龙佰集团", "600206.SH": "有研新材",
    "000001.SZ": "平安银行", "000333.SZ": "美的集团", "002415.SZ": "海康威视",
    "600519.SH": "贵州茅台", "601318.SH": "中国平安", "000858.SZ": "五粮液",
    "600036.SH": "招商银行", "600276.SH": "恒瑞医药", "600887.SH": "伊利股份",
    "600570.SH": "恒生电子", "600585.SH": "海螺水泥", "600893.SH": "航发动力",
    "601899.SH": "紫金矿业", "002230.SZ": "科大讯飞",
    "300750.SZ": "宁德时代", "688981.SH": "中芯国际",
}

TS_CODES = list(STOCK_NAMES.keys())

# 按行业分类
INDUSTRY_MAP = {
    "科技/半导体": ["002049.SZ", "600206.SH", "688981.SH", "600570.SH"],
    "通信/电子": ["600498.SH", "000725.SZ", "600522.SH", "002415.SZ"],
    "金融": ["000001.SZ", "601318.SH", "600036.SH"],
    "消费": ["000333.SZ", "600519.SH", "000858.SZ", "600887.SH"],
    "医药": ["600276.SH"],
    "周期/制造": ["002601.SZ", "600585.SH", "600893.SH", "601899.SH", "300750.SZ"],
    "AI/科技": ["002230.SZ"],
}

def fmt(v):
    return round(v, 6)

def run_bt(ts_code, strategy, start="20250601", end="20260617"):
    """运行单策略回测"""
    c = BacktestConfig(
        ts_codes=[ts_code],
        strategies=[strategy],
        start_date=start,
        end_date=end,
        initial_cash=100000,
    )
    return EnhancedBacktestEngine(c).run()

def to_dict(r):
    """Convert BacktestResult to dict (safe for JSON)"""
    return {
        "total_return": fmt(r.total_return),
        "annual_return": fmt(r.annual_return),
        "sharpe_ratio": fmt(r.sharpe_ratio),
        "max_drawdown": fmt(r.max_drawdown),
        "win_rate": fmt(r.win_rate),
        "profit_factor": fmt(r.profit_factor),
        "calmar_ratio": fmt(r.calmar_ratio),
        "sortino_ratio": fmt(r.sortino_ratio),
        "volatility": fmt(r.volatility),
        "total_trades": r.total_trades,
        "winning_trades": r.winning_trades,
        "losing_trades": r.losing_trades,
        "avg_hold_days": fmt(r.avg_hold_days),
        "benchmark_return": fmt(r.benchmark_return),
        "excess_return": fmt(r.excess_return),
    }

def run_all():
    """Run all 4 strategies on all stocks, return nested dict"""
    results = {}
    strategies = ["vwm", "bollinger", "combo-vwm-bbr", "adx"]
    total = len(TS_CODES) * len(strategies)
    count = 0

    for ts_code in TS_CODES:
        name = STOCK_NAMES[ts_code]
        stock_data = {}
        for strat in strategies:
            count += 1
            label = f"[{count}/{total}] {name} ({ts_code}) - {strat}"
            print(f"  {label}...", flush=True)
            try:
                r = run_bt(ts_code, strat)
                stock_data[strat] = to_dict(r)
                # Also capture equity curve for NAV charts
                stock_data[f"{strat}_equity"] = [
                    {"x": e["date"], "y": e["value"]}
                    for e in getattr(r, "equity_curve", [])
                ]
                stock_data[f"{strat}_trades"] = [
                    {
                        "date": t.date,
                        "direction": t.direction,
                        "price": t.price,
                        "quantity": t.quantity,
                        "pnl": getattr(t, "pnl", 0) or 0,
                        "hold_days": getattr(t, "hold_days", 0),
                    }
                    for t in getattr(r, "trades", [])
                ]
            except Exception as e:
                print(f"    ✗ FAILED: {e}", flush=True)
                stock_data[strat] = {"error": str(e)}
        results[ts_code] = stock_data

    return results

def gen_html(results, out_path):
    """Generate comprehensive HTML report"""
    strategies = ["vwm", "bollinger", "combo-vwm-bbr", "adx"]
    strat_labels = {"vwm": "VWM趋势", "bollinger": "BBR均值回归",
                    "combo-vwm-bbr": "COMBO组合", "adx": "ADX趋势强度"}

    # Build per-stock comparison rows
    stock_rows = []
    nav_chart_data = {}
    trade_data = {}

    for ts_code, sdata in results.items():
        name = STOCK_NAMES[ts_code]
        # Skip if all strategies failed
        if all("error" in sdata.get(s, {}) for s in strategies):
            continue

        nav_datasets = []
        for strat in strategies:
            if strat in sdata and f"{strat}_equity" in sdata:
                ed = sdata[f"{strat}_equity"]
                if ed:
                    nav_datasets.append({"name": strat_labels[strat], "data": ed})
        if nav_datasets:
            nav_chart_data[ts_code] = {
                "name": name,
                "series": nav_datasets
            }

        trades_list = []
        for strat in strategies:
            tk = f"{strat}_trades"
            if tk in sdata and sdata[tk]:
                for t in sdata[tk]:
                    trades_list.append({**t, "strategy": strat_labels[strat]})
        if trades_list:
            trade_data[ts_code] = {"name": name, "trades": trades_list}

        # Build comparison table
        cells = []
        for strat in strategies:
            sd = sdata.get(strat, {})
            if "error" in sd:
                cells.append({"error": sd["error"]})
            else:
                cells.append({
                    "ret": sd.get("total_return", 0),
                    "sharpe": sd.get("sharpe_ratio", 0),
                    "mdd": sd.get("max_drawdown", 0),
                    "win": sd.get("win_rate", 0),
                    "trades": sd.get("total_trades", 0),
                    "calmar": sd.get("calmar_ratio", 0),
                    "sortino": sd.get("sortino_ratio", 0),
                    "hold": sd.get("avg_hold_days", 0),
                })

        stock_rows.append({"code": ts_code, "name": name, "cells": cells})

    # Compute summary averages
    summary = {}
    for strat in strategies:
        i = strategies.index(strat)
        vals = [s["cells"][i] for s in stock_rows
                if i < len(s["cells"]) and "error" not in s["cells"][i]]
        if vals:
            n = len(vals)
            summary[strat_labels[strat]] = {
                "count": n,
                "avg_return": sum(v["ret"] for v in vals) / n,
                "avg_sharpe": sum(v["sharpe"] for v in vals) / n,
                "avg_mdd": sum(v["mdd"] for v in vals) / n,
                "avg_win": sum(v["win"] for v in vals) / n,
                "total_trades": sum(v["trades"] for v in vals),
                "avg_calmar": sum(v["calmar"] for v in vals) / n,
                "avg_sortino": sum(v["sortino"] for v in vals) / n,
                "avg_hold": sum(v["hold"] for v in vals) / n,
            }

    # Industry analysis
    industry_analysis = []
    for ind, codes in INDUSTRY_MAP.items():
        row_data = []
        for strat in strategies:
            vals_in_ind = []
            for code in codes:
                if code in results:
                    sd = results[code].get(strat, {})
                    if "error" not in sd:
                        vals_in_ind.append(sd.get("total_return", 0))
            avg_ret = sum(vals_in_ind) / len(vals_in_ind) if vals_in_ind else 0
            row_data.append({"ret": avg_ret, "count": len(vals_in_ind)})
        industry_analysis.append({"name": ind, "codes": codes, "data": row_data})

    # Build per-stock NAV chart data JSON
    nav_json = json.dumps(nav_chart_data)
    trade_json = json.dumps(trade_data)
    summary_json = json.dumps(summary)
    industry_json = json.dumps(industry_analysis)
    stock_rows_json = json.dumps(stock_rows)

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>开八策略回测深度分析报告</title>
<script src="https://cdn.jsdelivr.net/npm/apexcharts@4"></script>
<style>
:root {{
  --bg: #0b0f17;
  --card: #131a27;
  --border: #1e2840;
  --text: #dce2f0;
  --text2: #7a8aaa;
  --accent: #6366f1;
  --accent2: #22c55e;
  --accent3: #ef4444;
  --accent4: #f59e0b;
}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;
  background:var(--bg);color:var(--text);padding:0;min-height:100vh}}
.container{{max-width:1400px;margin:0 auto;padding:24px}}
/* Header */
.header{{text-align:center;padding:32px 0 24px;position:relative}}
.header h1{{font-size:28px;font-weight:700;background:linear-gradient(135deg,#818cf8,#22d3ee,#a78bfa);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}}
.header .subtitle{{color:var(--text2);font-size:14px;margin-top:6px}}
.header .date-badge{{display:inline-block;background:var(--card);border:1px solid var(--border);
  border-radius:20px;padding:4px 16px;font-size:12px;color:var(--text2);margin-top:8px}}
/* Summary Cards */
.summary-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:28px}}
.summary-card{{background:var(--card);border:1px solid var(--border);border-radius:14px;
  padding:18px 20px;transition:all .2s}}
.summary-card:hover{{border-color:var(--accent);transform:translateY(-2px);box-shadow:0 8px 24px rgba(99,102,241,.12)}}
.summary-card .strat-name{{font-size:13px;font-weight:600;color:var(--text2);margin-bottom:10px;
  letter-spacing:.3px}}
.summary-card .metric-row{{display:flex;justify-content:space-between;padding:3px 0;font-size:13px}}
.summary-card .metric-label{{color:var(--text2)}}
.summary-card .metric-value{{font-weight:600;font-variant-numeric:tabular-nums}}
.pos{{color:var(--accent2)}}.neg{{color:var(--accent3)}}.neu{{color:var(--accent4)}}
/* Section */
.section-title{{font-size:18px;font-weight:700;margin-bottom:14px;display:flex;align-items:center;gap:8px}}
.section-title .badge{{background:var(--card);border:1px solid var(--border);border-radius:12px;
  padding:2px 10px;font-size:11px;color:var(--text2);font-weight:400}}
/* Charts Row */
.charts-row{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:24px}}
.chart-card{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:16px}}
.chart-card.full{{grid-column:1/-1}}
.chart-card h3{{font-size:13px;color:var(--text2);margin-bottom:10px;font-weight:500}}
/* Controls */
.controls{{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap;align-items:center}}
.controls select,.controls button{{background:var(--card);border:1px solid var(--border);
  border-radius:8px;color:var(--text);padding:6px 14px;font-size:13px;cursor:pointer;
  transition:all .15s}}
.controls select:hover,.controls button:hover{{border-color:var(--accent);background:#1a2540}}
.controls label{{font-size:12px;color:var(--text2);margin-right:4px}}
/* Table */
.data-table{{width:100%;border-collapse:separate;border-spacing:0;font-size:13px}}
.data-table th{{text-align:left;padding:8px 10px;border-bottom:2px solid var(--border);
  color:var(--text2);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.5px;
  position:sticky;top:0;background:var(--card);z-index:1}}
.data-table td{{padding:7px 10px;border-bottom:1px solid rgba(30,40,64,.5);
  font-variant-numeric:tabular-nums;white-space:nowrap}}
.data-table tr:hover td{{background:rgba(99,102,241,.04)}}
.data-table .stock-name{{display:flex;align-items:center;gap:6px}}
.data-table .stock-code{{color:var(--text2);font-size:11px}}
.stock-dot{{width:6px;height:6px;border-radius:50%;display:inline-block;flex-shrink:0}}
/* Trades Modal */
.modal-overlay{{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.6);
  backdrop-filter:blur(4px);z-index:100;display:none;align-items:center;justify-content:center}}
.modal-overlay.active{{display:flex}}
.modal{{background:var(--card);border:1px solid var(--border);border-radius:16px;
  max-width:900px;width:90%;max-height:80vh;overflow:hidden;display:flex;flex-direction:column}}
.modal-header{{display:flex;justify-content:space-between;align-items:center;
  padding:16px 20px;border-bottom:1px solid var(--border);flex-shrink:0}}
.modal-header h2{{font-size:16px;font-weight:600}}
.modal-close{{background:none;border:none;color:var(--text2);font-size:22px;cursor:pointer;
  padding:4px 8px;border-radius:6px;transition:all .15s}}
.modal-close:hover{{background:rgba(255,255,255,.06);color:var(--text)}}
.modal-body{{overflow-y:auto;padding:16px 20px;flex:1}}
/* Industry chart container */
.industry-section{{margin-bottom:24px}}
/* Win/loss bar colors */
.bar-win{{background:var(--accent2)}} .bar-loss{{background:var(--accent3)}}
/* Toggle row */
.toggle-row{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}}
.toggle-btn{{background:var(--card);border:1px solid var(--border);border-radius:20px;
  padding:4px 14px;font-size:12px;cursor:pointer;color:var(--text2);transition:all .15s}}
.toggle-btn.active{{border-color:var(--accent);color:var(--accent);background:rgba(99,102,241,.1)}}
/* Responsive */
@media(max-width:768px){{
  .summary-grid{{grid-template-columns:repeat(2,1fr)}}
  .charts-row{{grid-template-columns:1fr}}
  .container{{padding:12px}}
}}
</style></head>
<body>
<div class="container">
<div class="header">
  <h1>📊 量化策略回测深度分析</h1>
  <div class="subtitle">VWM趋势 · BBR均值回归 · COMBO组合 · ADX趋势强度 — 四大策略全量对比</div>
  <div class="date-badge">📅 2025-06-01 → 2026-06-17 · {len(stock_rows)} 只股票</div>
</div>

<div class="summary-grid" id="summaryGrid"></div>

<div class="section-title">📈 策略表现概览 <span class="badge">21只股票平均</span></div>
<div class="charts-row">
  <div class="chart-card"><h3>总收益率对比</h3><div id="chartReturn"></div></div>
  <div class="chart-card"><h3>夏普比率对比</h3><div id="chartSharpe"></div></div>
</div>

<div class="section-title">🏭 行业表现分析 <span class="badge">按行业分组</span></div>
<div class="charts-row">
  <div class="chart-card full"><h3>各行业在不同策略下的平均收益率</h3><div id="chartIndustry"></div></div>
</div>

<div class="section-title">📋 个股策略对比 <span class="badge">点击股票查看详情</span></div>
<div class="controls">
  <label>过滤行业:</label>
  <select id="industryFilter"><option value="all">全部行业</option></select>
  <label>排序:</label>
  <select id="sortBy">
    <option value="combo">COMBO收益</option>
    <option value="vwm">VWM收益</option>
    <option value="bollinger">BBR收益</option>
    <option value="adx">ADX收益</option>
  </select>
  <button onclick="refreshTable()">⟳ 刷新</button>
</div>
<div style="overflow-x:auto;border-radius:14px;background:var(--card);border:1px solid var(--border)">
<table class="data-table" id="stockTable">
<thead><tr>
<th>股票</th>
<th colspan="3" style="text-align:center;border-right:1px solid var(--border)">VWM趋势</th>
<th colspan="3" style="text-align:center;border-right:1px solid var(--border)">BBR均值回归</th>
<th colspan="3" style="text-align:center;border-right:1px solid var(--border)">COMBO组合</th>
<th colspan="3" style="text-align:center">ADX趋势强度</th>
</tr><tr>
<th></th>
<th style="border-right:1px solid var(--border)">收益率</th><th style="border-right:1px solid var(--border)">夏普</th><th style="border-right:1px solid var(--border)">回撤</th>
<th style="border-right:1px solid var(--border)">收益率</th><th style="border-right:1px solid var(--border)">夏普</th><th style="border-right:1px solid var(--border)">回撤</th>
<th style="border-right:1px solid var(--border)">收益率</th><th style="border-right:1px solid var(--border)">夏普</th><th style="border-right:1px solid var(--border)">回撤</th>
<th>收益率</th><th>夏普</th><th>回撤</th>
</tr></thead>
<tbody id="stockTbody"></tbody></table></div>

<div class="controls" style="margin-top:16px">
  <label>查看个股净值曲线:</label>
  <select id="navSelector">
    <option value="">— 选择股票 —</option>
  </select>
  <label style="margin-left:12px">策略:</label>
  <select id="navStratFilter">
    <option value="all">全部</option>
  </select>
</div>
<div class="chart-card full">
  <h3>📈 个股净值曲线对比</h3>
  <div id="chartNav"></div>
</div>

<div class="controls" style="margin-top:16px">
  <label>查看交易明细:</label>
  <select id="tradeSelector">
    <option value="">— 选择股票 —</option>
  </select>
</div>
<div class="chart-card full">
  <h3>📝 策略收益分布</h3>
  <div id="chartDistribution"></div>
</div>

</div><!-- /container -->

<!-- Trade Modal -->
<div class="modal-overlay" id="tradeModal">
<div class="modal">
<div class="modal-header"><h2 id="modalTitle">交易明细</h2>
<button class="modal-close" onclick="document.getElementById('tradeModal').classList.remove('active')">&times;</button></div>
<div class="modal-body" id="modalBody"></div></div></div>

<script>
// Data from Python
const STOCK_ROWS = {stock_rows_json};
const SUMMARY = {summary_json};
const NAV_DATA = {nav_json};
const TRADE_DATA = {trade_json};
const INDUSTRY_DATA = {industry_json};
const STRATEGIES = {{"vwm":"VWM趋势","bollinger":"BBR均值回归","combo-vwm-bbr":"COMBO组合","adx":"ADX趋势强度"}};
const STRAT_COLORS = {{"VWM趋势":"#22c55e","BBR均值回归":"#f59e0b","COMBO组合":"#818cf8","ADX趋势强度":"#f472b6"}};

function fmtPct(v) {{return (v*100).toFixed(2)+"%"}}
function fmtDec(v,d) {{return (v).toFixed(d||2)}}
function clsNum(v) {{return v>=0?"pos":"neg"}}

// Build summary cards
(function() {{
  const g = document.getElementById('summaryGrid');
  const sl = ['VWM趋势','BBR均值回归','COMBO组合','ADX趋势强度'];
  sl.forEach(s => {{
    const d = SUMMARY[s]||{{}};
    const ret = d.avg_return||0;
    g.innerHTML += '<div class="summary-card"><div class="strat-name" style="color:'+(STRAT_COLORS[s]||'#818cf8')+'">'+s+'</div>'+
      '<div class="metric-row"><span class="metric-label">平均收益</span><span class="metric-value '+clsNum(ret)+'">'+fmtPct(ret)+'</span></div>'+
      '<div class="metric-row"><span class="metric-label">平均夏普</span><span class="metric-value">'+fmtDec(d.avg_sharpe,3)+'</span></div>'+
      '<div class="metric-row"><span class="metric-label">平均回撤</span><span class="metric-value neg">'+fmtPct(d.avg_mdd)+'</span></div>'+
      '<div class="metric-row"><span class="metric-label">平均胜率</span><span class="metric-value">'+fmtPct(d.avg_win)+'</span></div>'+
      '<div class="metric-row"><span class="metric-label">总交易</span><span class="metric-value">'+(d.total_trades||0)+'</span></div>'+
      '<div class="metric-row"><span class="metric-label">Calmar</span><span class="metric-value">'+fmtDec(d.avg_calmar,2)+'</span></div>'+
      '<div class="metric-row"><span class="metric-label">平均持股(天)</span><span class="metric-value">'+fmtDec(d.avg_hold,1)+'</span></div>'+
    '</div>';
  }});
}})();

// Return comparison chart
(function() {{
  const sl = ['VWM趋势','BBR均值回归','COMBO组合','ADX趋势强度'];
  new ApexCharts(document.querySelector('#chartReturn'),{{
    series: sl.map(s => ({{name:s,data:[SUMMARY[s]?.avg_return||0]}})),
    chart:{{type:'bar',height:260,toolbar:{{show:false}},background:'transparent',foreColor:'#7a8aaa'}},
    plotOptions:{{bar:{{borderRadius:4,horizontal:false,columnWidth:'50%',
      colors:{{ranges:[sl.map((s,i)=>({{from:0,to:1,color:Object.values(STRAT_COLORS)[i]}}))]}}}}}},
    colors: Object.values(STRAT_COLORS),
    dataLabels:{{enabled:true,formatter:v=>fmtPct(v),style:{{colors:['#fff'],fontSize:'11px'}}}},
    xaxis:{{categories:sl,labels:{{style:{{colors:Object.values(STRAT_COLORS),fontSize:'12px',fontWeight:600}}}}}},
    yaxis:{{labels:{{formatter:v=>fmtPct(v)}}}},
    grid:{{borderColor:'#1e2840',strokeDashArray:3}},
    tooltip:{{theme:'dark',y:{{formatter:v=>fmtPct(v)}}}},
  }}).render();
}})();

// Sharpe chart
(function() {{
  const sl = ['VWM趋势','BBR均值回归','COMBO组合','ADX趋势强度'];
  new ApexCharts(document.querySelector('#chartSharpe'),{{
    series: sl.map(s => ({{name:s,data:[SUMMARY[s]?.avg_sharpe||0]}})),
    chart:{{type:'bar',height:260,toolbar:{{show:false}},background:'transparent',foreColor:'#7a8aaa'}},
    plotOptions:{{bar:{{borderRadius:4,horizontal:false,columnWidth:'50%'}}}},
    colors: Object.values(STRAT_COLORS),
    dataLabels:{{enabled:true,formatter:v=>fmtDec(v,3),style:{{colors:['#fff'],fontSize:'11px'}}}},
    xaxis:{{categories:sl,labels:{{style:{{colors:Object.values(STRAT_COLORS),fontSize:'12px',fontWeight:600}}}}}},
    yaxis:{{labels:{{formatter:v=>fmtDec(v,2)}}}},
    grid:{{borderColor:'#1e2840',strokeDashArray:3}},
    tooltip:{{theme:'dark'}},
  }}).render();
}})();

// Industry heatmap chart
(function() {{
  const cats = ['VWM趋势','BBR均值回归','COMBO组合','ADX趋势强度'];
  const industries = INDUSTRY_DATA.map(d => d.name);
  const series = cats.map((s,i) => ({{
    name: s,
    data: INDUSTRY_DATA.map(d => {{ const v = d.data[i]; return {{x:d.name,y:(v?.ret||0)*100}} }})
  }}));
  new ApexCharts(document.querySelector('#chartIndustry'),{{
    series: series,
    chart:{{type:'bar',height:320,toolbar:{{show:false}},background:'transparent',foreColor:'#7a8aaa',
      stacked:false}},
    plotOptions:{{bar:{{borderRadius:2,horizontal:false,columnWidth:'70%',
      dataLabels:{{position:'top'}}}}}},
    colors: Object.values(STRAT_COLORS),
    dataLabels:{{enabled:true,formatter:v=>v.toFixed(1)+'%',offsetY:-18,
      style:{{fontSize:'10px',colors:['#7a8aaa']}}}},
    xaxis:{{categories:industries,labels:{{style:{{fontSize:'11px'}}}}}},
    yaxis:{{title:{{text:'平均收益率(%)',style:{{color:'#7a8aaa',fontSize:'11px'}}}},
      labels:{{formatter:v=>v.toFixed(1)+'%'}}}},
    grid:{{borderColor:'#1e2840',strokeDashArray:3}},
    legend:{{position:'top',labels:{{colors:'#7a8aaa'}}}},
    tooltip:{{theme:'dark',y:{{formatter:v=>v.toFixed(2)+'%'}}}},
  }}).render();
}})();

// Stock table
function renderTable() {{
  const filter = document.getElementById('industryFilter').value;
  const sort = document.getElementById('sortBy').value;
  let rows = [...STOCK_ROWS];

  if(filter !== 'all') {{
    const indCodes = INDUSTRY_DATA.find(d=>d.name===filter)?.codes||[];
    rows = rows.filter(r => indCodes.includes(r.code));
  }}

  const sortIdx = ['vwm','bollinger','combo-vwm-bbr','adx'].indexOf(sort);
  rows.sort((a,b) => {{
    const va = a.cells[sortIdx]?.ret||0;
    const vb = b.cells[sortIdx]?.ret||0;
    return vb - va;
  }});

  const tbody = document.getElementById('stockTbody');
  tbody.innerHTML = rows.map(r => {{
    const getCell = (idx,f) => {{
      const c = r.cells[idx];
      if(!c||c.error) return '<td class="neu">-</td>';
      const v = c[f];
      if(f==='ret'||f==='mdd') return '<td class="'+clsNum(v)+'">'+fmtPct(v)+'</td>';
      return '<td>'+fmtDec(v,3)+'</td>';
    }};
    // Color dot by industry
    let dotColor = '#6366f1';
    for(const [ind,codes] of Object.entries({{}}) || INDUSTRY_DATA) {{
      // skip, do it inline
    }}
    return '<tr style="cursor:pointer" onclick="showTradeModal(\''+r.code+'\')">'+
      '<td><div class="stock-name"><span class="stock-dot" style="background:'+getIndColor(r.code)+'"></span>'+
      r.name+' <span class="stock-code">'+r.code+'</span></div></td>'+
      getCell(0,'ret')+getCell(0,'sharpe')+'<td style="border-right:1px solid var(--border)">'+getCell(0,'mdd')+
      getCell(1,'ret')+getCell(1,'sharpe')+'<td style="border-right:1px solid var(--border)">'+getCell(1,'mdd')+
      getCell(2,'ret')+getCell(2,'sharpe')+'<td style="border-right:1px solid var(--border)">'+getCell(2,'mdd')+
      getCell(3,'ret')+getCell(3,'sharpe')+getCell(3,'mdd')+
    '</tr>';
  }}).join('');
}}

function getIndColor(code) {{
  const colors = ['#6366f1','#22c55e','#f59e0b','#f472b6','#38bdf8','#a78bfa','#fb923c'];
  for(let i=0;i<INDUSTRY_DATA.length;i++) {{
    if(INDUSTRY_DATA[i].codes.includes(code)) return colors[i%colors.length];
  }}
  return '#6366f1';
}}

// Populate filters
(function() {{
  const f = document.getElementById('industryFilter');
  INDUSTRY_DATA.forEach(d => {{ f.innerHTML += '<option value="'+d.name+'">'+d.name+'</option>' }});
  const ns = document.getElementById('navSelector');
  const ts = document.getElementById('tradeSelector');
  Object.keys(NAV_DATA).forEach(k => {{
    ns.innerHTML += '<option value="'+k+'">'+NAV_DATA[k].name+'</option>';
    ts.innerHTML += '<option value="'+k+'">'+(TRADE_DATA[k]?.name||NAV_DATA[k].name)+'</option>';
  }});
  const sf = document.getElementById('navStratFilter');
  Object.values(STRATEGIES).forEach(s => {{ sf.innerHTML += '<option value="'+s+'">'+s+'</option>' }});

  renderTable();
}})();

function refreshTable() {{ renderTable(); }}

// NAV chart
let navChart = null;
document.getElementById('navSelector').addEventListener('change', function() {{
  const code = this.value;
  const stratFilter = document.getElementById('navStratFilter').value;
  if(!code || !NAV_DATA[code]) return;
  const d = NAV_DATA[code];
  let series = d.series;
  if(stratFilter !== 'all') series = series.filter(s => s.name === stratFilter);
  if(!series.length) return;
  if(navChart) navChart.destroy();
  navChart = new ApexCharts(document.querySelector('#chartNav'),{{
    series: series,
    chart:{{type:'line',height:320,toolbar:{{show:true}},background:'transparent',foreColor:'#7a8aaa'}},
    stroke:{{curve:'smooth',width:2}},
    colors: Object.values(STRAT_COLORS),
    xaxis:{{type:'datetime',labels:{{format:'MM-dd'}},tickAmount:10}},
    yaxis:{{labels:{{formatter:v=>'¥'+v.toLocaleString()}}}},
    grid:{{borderColor:'#1e2840',strokeDashArray:3}},
    legend:{{position:'top',labels:{{colors:'#7a8aaa'}}}},
    tooltip:{{theme:'dark',x:{{format:'yyyy-MM-dd'}}}},
  }}).render();
}});

document.getElementById('navStratFilter').addEventListener('change', function() {{
  document.getElementById('navSelector').dispatchEvent(new Event('change'));
}});

// Distribution chart
let distChart = null;
(function() {{
  const sl = ['VWM趋势','BBR均值回归','COMBO组合','ADX趋势强度'];
  const series = sl.map(s => {{
    const data = STOCK_ROWS.map(r => {{
      const i = ['vwm','bollinger','combo-vwm-bbr','adx'].indexOf(s==='VWM趋势'?'vwm':s==='BBR均值回归'?'bollinger':s==='COMBO组合'?'combo-vwm-bbr':'adx');
      return (r.cells[i]?.ret||0)*100;
    }});
    return {{name:s,type:'boxPlot',data:[{{q1:0,median:0,q3:0,min:-20,max:40}}]}};
  }});
  // Use a simpler distribution: scatter chart
  const distData = sl.map(s => {{
    const idx = ['vwm','bollinger','combo-vwm-bbr','adx'].indexOf(s==='VWM趋势'?'vwm':s==='BBR均值回归'?'bollinger':s==='COMBO组合'?'combo-vwm-bbr':'adx');
    const pts = STOCK_ROWS.map((r,i) => ({{x:i,y:(r.cells[idx]?.ret||0)*100}}));
    return {{name:s,data:pts}};
  }});
  distChart = new ApexCharts(document.querySelector('#chartDistribution'),{{
    series: distData,
    chart:{{type:'scatter',height:300,toolbar:{{show:false}},background:'transparent',foreColor:'#7a8aaa'}},
    colors: Object.values(STRAT_COLORS),
    xaxis:{{categories:STOCK_ROWS.map(r=>r.name),labels:{{rotate:-45,fontSize:'10px'}}}},
    yaxis:{{title:{{text:'收益率(%)',style:{{color:'#7a8aaa',fontSize:'11px'}}}},
      labels:{{formatter:v=>v.toFixed(1)+'%'}}}},
    grid:{{borderColor:'#1e2840',strokeDashArray:3}},
    legend:{{position:'top',labels:{{colors:'#7a8aaa'}}}},
    tooltip:{{theme:'dark',y:{{formatter:v=>v.toFixed(2)+'%'}}}},
    markers:{{size:5}},
  }}).render();
}})();

// Trade Modal
function showTradeModal(code) {{
  const d = TRADE_DATA[code];
  if(!d||!d.trades.length) return;
  const title = document.getElementById('modalTitle');
  title.textContent = '📋 '+d.name+' ('+code+') — 交易明细';
  const body = document.getElementById('modalBody');
  const trades = d.trades.sort((a,b) => a.date.localeCompare(b.date));
  let html = '<table class="data-table"><thead><tr><th>日期</th><th>策略</th><th>方向</th><th>价格</th><th>数量</th><th>盈亏</th><th>持股天数</th></tr></thead><tbody>';
  let totalPnl = 0;
  trades.forEach(t => {{
    totalPnl += t.pnl;
    html += '<tr><td>'+t.date+'</td><td style="color:'+(STRAT_COLORS[t.strategy]||'#818cf8')+'">'+t.strategy+'</td>'+
      '<td class="'+(t.direction==='BUY'?'pos':'neg')+'">'+t.direction+'</td>'+
      '<td>¥'+t.price.toFixed(2)+'</td><td>'+t.quantity+'</td>'+
      '<td class="'+(t.pnl>=0?'pos':'neg')+'">¥'+(t.pnl>0?'+':'')+t.pnl.toFixed(0)+'</td>'+
      '<td>'+t.hold_days+'天</td></tr>';
  }});
  html += '</tbody></table>';
  html += '<div style="margin-top:12px;padding:12px;background:rgba(99,102,241,.06);border-radius:8px;border:1px solid var(--border)">'+
    '<span style="color:var(--text2)">累计盈亏: </span><span class="'+(totalPnl>=0?'pos':'neg')+'" style="font-size:18px;font-weight:700">¥'+(totalPnl>0?'+':'')+totalPnl.toFixed(0)+'</span>'+
    '<span style="color:var(--text2);margin-left:8px">('+(totalPnl/100000*100).toFixed(2)+'%)</span></div>';
  body.innerHTML = html;
  document.getElementById('tradeModal').classList.add('active');
}}

// Close modal on overlay click
document.getElementById('tradeModal').addEventListener('click', function(e) {{
  if(e.target === this) this.classList.remove('active');
}});

</script>
</body></html>'''
    Path(out_path).write_text(html, encoding="utf-8")
    print(f"✅ Write: {out_path}  ({os.path.getsize(out_path)/1024:.0f} KB)", flush=True)

if __name__ == "__main__":
    print("=" * 60)
    print("📊 量化策略回测深度分析 — 全量跑")
    print("   VWM / BBR / COMBO / ADX × 21只股票")
    print("=" * 60, flush=True)
    results = run_all()

    # Save raw data
    raw_path = "/app/output/bt_deep_analysis_raw.json"
    Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
    Path(raw_path).write_text(json.dumps(results, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\n✅ Raw data: {raw_path}", flush=True)

    # Generate HTML
    html_path = "/app/output/bt_deep_analysis.html"
    gen_html(results, html_path)
    print(f"\n✅ HTML report: {html_path}", flush=True)
