#!/usr/bin/env python3
"""
cost_dashboard.py — 成本追踪可视化仪表盘生成器
================================================
基于 cost_tracker.py 的成本数据，生成交互式 HTML 仪表盘。

用法：
  python3 cost_dashboard.py                           → 保存到默认路径
  python3 cost_dashboard.py ~/Desktop/cost-dash.html  → 指定输出路径
  python3 cost_dashboard.py --open                     → 生成后自动打开

输出：自包含 HTML 文件（Chart.js 通过 CDN 加载）
"""

import json
import sys
from datetime import datetime, date, timedelta
from pathlib import Path
from collections import defaultdict

# ============================================================
# 数据源
# ============================================================

SCRIPTS_DIR = Path(__file__).parent.resolve()

# 复用 cost_tracker 的常量
LOG_FILE = Path.home() / ".ai_cost_log.jsonl"
MONTHLY_BUDGET_CNY = 400.0
FLASH_LOCK_THRESHOLD = 350.0
DAILY_WARNING_CNY = 25.0

# 直接从 cost_tracker 导入估算配置
sys.path.insert(0, str(SCRIPTS_DIR))
from cost_tracker import AUTO_COST_ESTIMATES, MODEL_PRICES


# ============================================================
# 数据加载
# ============================================================

def load_cost_records() -> list:
    """加载所有实际成本记录"""
    if not LOG_FILE.exists():
        return []
    records = []
    for line in LOG_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            records.append(r)
        except json.JSONDecodeError:
            continue
    return records


def get_daily_trend(records: list, days: int = 30) -> list:
    """获取过去 N 天每日成本趋势"""
    end = date.today()
    start = end - timedelta(days=days - 1)
    daily = defaultdict(float)
    for r in records:
        d = r.get("date", "")
        if start.isoformat() <= d <= end.isoformat():
            daily[d] += r.get("cost_cny", 0)

    # 填充缺失日期（补 0）
    result = []
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        result.append({"date": d, "cost": round(daily.get(d, 0), 6)})
    return result


def get_model_distribution(records: list) -> list:
    """按模型聚合成本"""
    by_model = defaultdict(float)
    for r in records:
        m = r.get("model_key", r.get("model", "未知"))
        by_model[m] += r.get("cost_cny", 0)
    return sorted(
        [{"name": k, "cost": round(v, 6)} for k, v in by_model.items()],
        key=lambda x: -x["cost"]
    )


def get_project_distribution(records: list) -> list:
    """按项目聚合成本"""
    by_project = defaultdict(float)
    for r in records:
        p = r.get("project", "未知")
        by_project[p] += r.get("cost_cny", 0)
    return sorted(
        [{"name": k, "cost": round(v, 6)} for k, v in by_project.items()],
        key=lambda x: -x["cost"]
    )


def get_top_tasks(records: list, n: int = 10) -> list:
    """最烧钱的任务 TOP-N"""
    by_task = defaultdict(lambda: {"cost": 0.0, "count": 0})
    for r in records:
        t = r.get("task", "未知")
        by_task[t]["cost"] += r.get("cost_cny", 0)
        by_task[t]["count"] += 1
    sorted_tasks = sorted(by_task.items(), key=lambda x: -x[1]["cost"])[:n]
    return [
        {"name": name, "cost": round(data["cost"], 6), "count": data["count"]}
        for name, data in sorted_tasks
    ]


def get_month_summary(records: list) -> dict:
    """当月汇总"""
    month = date.today().strftime("%Y-%m")
    month_records = [r for r in records if r.get("date", "").startswith(month)]
    total = sum(r.get("cost_cny", 0) for r in month_records)
    today_day = date.today().day
    days_in_month = 30
    projection = total / today_day * days_in_month if today_day > 0 and total > 0 else 0
    return {
        "total": round(total, 6),
        "count": len(month_records),
        "projection": round(projection, 2),
        "remaining": round(MONTHLY_BUDGET_CNY - total, 6),
        "budget": MONTHLY_BUDGET_CNY,
    }


def get_automation_estimates() -> list:
    """获取自动化成本估算数据"""
    estimates = []
    for name, cfg in sorted(AUTO_COST_ESTIMATES.items()):
        model = cfg.get("model", "?")
        inp = cfg.get("inp_est", 0)
        out = cfg.get("out_est", 0)
        freq = cfg.get("freq", "?")
        prices = MODEL_PRICES.get(model, {"input": 0, "output": 0})
        cost = round((inp * prices["input"] + out * prices["output"]) / 10000, 6)
        estimates.append({
            "name": name,
            "model": model,
            "inp_est": inp,
            "out_est": out,
            "freq": freq,
            "cost": cost,
        })
    return estimates


# ============================================================
# HTML 生成
# ============================================================

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 成本追踪仪表盘</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
/* ========== CSS Variables ========== */
:root {
    --bg: #0a0e1a;
    --bg-card: rgba(255,255,255,0.04);
    --bg-card-hover: rgba(255,255,255,0.07);
    --border: rgba(255,255,255,0.08);
    --text: #e8edf5;
    --text-secondary: #8892a4;
    --accent: #4f8cff;
    --accent-glow: rgba(79,140,255,0.3);
    --green: #34d399;
    --yellow: #fbbf24;
    --red: #f87171;
    --glass-bg: rgba(10,14,26,0.6);
    --glass-border: rgba(255,255,255,0.06);
}
[data-theme="light"] {
    --bg: #f0f2f6;
    --bg-card: rgba(255,255,255,0.7);
    --bg-card-hover: rgba(255,255,255,0.85);
    --border: rgba(0,0,0,0.08);
    --text: #1a1d2e;
    --text-secondary: #5a6070;
    --accent: #3b82f6;
    --accent-glow: rgba(59,130,246,0.2);
    --green: #10b981;
    --yellow: #d97706;
    --red: #ef4444;
    --glass-bg: rgba(255,255,255,0.5);
    --glass-border: rgba(0,0,0,0.06);
}

/* ========== Base ========== */
*, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    transition: background 0.3s, color 0.3s;
    line-height: 1.6;
}

/* ========== Header ========== */
.header {
    position: sticky; top: 0; z-index: 100;
    backdrop-filter: blur(24px) saturate(180%);
    -webkit-backdrop-filter: blur(24px) saturate(180%);
    background: var(--glass-bg);
    border-bottom: 1px solid var(--glass-border);
    padding: 16px 32px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    transition: background 0.3s;
}
.header-left { display: flex; align-items: center; gap: 12px; }
.header h1 {
    font-size: 20px;
    font-weight: 600;
    letter-spacing: -0.3px;
    background: linear-gradient(135deg, var(--accent), #a78bfa);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}
.header-time {
    font-size: 13px;
    color: var(--text-secondary);
    padding: 4px 10px;
    background: var(--bg-card);
    border-radius: 8px;
    border: 1px solid var(--border);
}

/* ========== Theme Toggle ========== */
.theme-toggle {
    display: flex; align-items: center; gap: 8px;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 4px;
    cursor: pointer;
    transition: all 0.3s;
}
.theme-toggle:hover { background: var(--bg-card-hover); }
.theme-toggle .icon {
    padding: 6px 10px;
    border-radius: 8px;
    font-size: 16px;
    transition: all 0.3s;
    opacity: 0.5;
}
.theme-toggle .icon.active { opacity: 1; background: var(--accent-glow); }

/* ========== Layout ========== */
.container {
    max-width: 1400px;
    margin: 0 auto;
    padding: 24px 32px 48px;
}

/* ========== Summary Cards ========== */
.summary-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 16px;
    margin-bottom: 28px;
}
.summary-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 20px 24px;
    transition: all 0.3s cubic-bezier(0.16,1,0.3,1);
    cursor: default;
    position: relative;
    overflow: hidden;
}
.summary-card::before {
    content: '';
    position: absolute;
    top: 0; left: 0;
    width: 100%; height: 3px;
    border-radius: 16px 16px 0 0;
}
.summary-card:hover {
    transform: translateY(-2px);
    background: var(--bg-card-hover);
}
.summary-card .label {
    font-size: 13px;
    color: var(--text-secondary);
    margin-bottom: 6px;
    display: flex;
    align-items: center;
    gap: 6px;
}
.summary-card .value {
    font-size: 28px;
    font-weight: 700;
    letter-spacing: -0.5px;
}
.summary-card .sub {
    font-size: 12px;
    color: var(--text-secondary);
    margin-top: 4px;
}
.card-total::before { background: var(--accent); }
.card-total .value { color: var(--accent); }
.card-count::before { background: var(--green); }
.card-count .value { color: var(--green); }
.card-budget::before { background: var(--yellow); }
.card-budget .value { color: var(--yellow); }
.card-project::before { background: #a78bfa; }
.card-project .value { color: #a78bfa; }

/* ========== Chart Grid ========== */
.chart-grid {
    display: grid;
    grid-template-columns: 2fr 1fr;
    gap: 16px;
    margin-bottom: 28px;
}
.chart-grid-3 {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 16px;
    margin-bottom: 28px;
}
.chart-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 20px;
    transition: all 0.3s;
}
.chart-card:hover { background: var(--bg-card-hover); }
.chart-card.full { grid-column: 1 / -1; }
.chart-card h3 {
    font-size: 15px;
    font-weight: 600;
    margin-bottom: 16px;
    display: flex;
    align-items: center;
    gap: 8px;
}
.chart-card h3 .badge {
    font-size: 11px;
    font-weight: 500;
    padding: 2px 8px;
    border-radius: 6px;
    background: var(--accent-glow);
    color: var(--accent);
}
.chart-wrap { position: relative; height: 280px; }
.chart-wrap.tall { height: 360px; }
.chart-wrap canvas { max-height: 100%; max-width: 100%; }

/* ========== Estimation Table ========== */
.table-wrap {
    overflow-x: auto;
    margin-top: 8px;
}
table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
}
thead th {
    padding: 10px 14px;
    text-align: left;
    font-weight: 600;
    color: var(--text-secondary);
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
}
tbody td {
    padding: 10px 14px;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
}
tbody tr:hover { background: rgba(255,255,255,0.03); }
tbody tr:last-child td { border-bottom: none; }
.freq-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 6px;
    font-size: 11px;
    font-weight: 500;
}
.freq-日 { background: rgba(52,211,153,0.15); color: var(--green); }
.freq-周 { background: rgba(79,140,255,0.15); color: var(--accent); }
.freq-月 { background: rgba(167,139,250,0.15); color: #a78bfa; }
.freq-季 { background: rgba(251,191,36,0.15); color: var(--yellow); }
.freq-半年 { background: rgba(251,191,36,0.12); color: var(--yellow); }
.freq-年 { background: rgba(248,113,113,0.15); color: var(--red); }
.freq-高频 { background: rgba(248,113,113,0.15); color: var(--red); }
.freq-时 { background: rgba(79,140,255,0.12); color: var(--accent); }
.model-tag {
    display: inline-block;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 11px;
    font-family: 'SF Mono', 'Fira Code', monospace;
    background: var(--bg-card);
    border: 1px solid var(--border);
}

/* ========== Empty State ========== */
.empty-state {
    text-align: center;
    padding: 60px 20px;
    color: var(--text-secondary);
}
.empty-state .icon { font-size: 48px; margin-bottom: 16px; opacity: 0.5; }
.empty-state h3 { font-size: 18px; margin-bottom: 8px; color: var(--text); }
.empty-state p { font-size: 14px; max-width: 400px; margin: 0 auto; line-height: 1.8; }

/* ========== Footer ========== */
.footer {
    text-align: center;
    padding: 24px;
    color: var(--text-secondary);
    font-size: 12px;
    border-top: 1px solid var(--border);
    margin-top: 40px;
}

/* ========== Responsive ========== */
@media (max-width: 1024px) {
    .chart-grid-3 { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 768px) {
    .container { padding: 16px; }
    .header { padding: 12px 16px; }
    .header h1 { font-size: 16px; }
    .chart-grid { grid-template-columns: 1fr; }
    .chart-grid-3 { grid-template-columns: 1fr; }
    .summary-grid { grid-template-columns: 1fr 1fr; }
    .summary-card .value { font-size: 22px; }
}
@media (max-width: 480px) {
    .summary-grid { grid-template-columns: 1fr; }
}

/* ========== Animations ========== */
@keyframes fadeUp {
    from { opacity: 0; transform: translateY(16px); }
    to { opacity: 1; transform: translateY(0); }
}
.summary-card, .chart-card {
    animation: fadeUp 0.5s ease-out forwards;
    opacity: 0;
}
.summary-card:nth-child(1) { animation-delay: 0.05s; }
.summary-card:nth-child(2) { animation-delay: 0.1s; }
.summary-card:nth-child(3) { animation-delay: 0.15s; }
.summary-card:nth-child(4) { animation-delay: 0.2s; }
.chart-card { animation-delay: 0.25s; }
</style>
</head>
<body>

<!-- ========== Header ========== -->
<header class="header">
    <div class="header-left">
        <h1>◆ 成本追踪仪表盘</h1>
        <span class="header-time" id="headerTime"></span>
    </div>
    <div class="theme-toggle" onclick="toggleTheme()">
        <span class="icon active" data-theme-val="dark">🌙</span>
        <span class="icon" data-theme-val="light">☀️</span>
    </div>
</header>

<!-- ========== Container ========== -->
<div class="container" id="app">

    <!-- Summary Cards -->
    <div class="summary-grid" id="summaryCards"></div>

    <!-- Charts Row 1 -->
    <div class="chart-grid">
        <div class="chart-card">
            <h3>📈 每日成本趋势 <span class="badge">近30天</span></h3>
            <div class="chart-wrap"><canvas id="trendChart"></canvas></div>
        </div>
        <div class="chart-card">
            <h3>🧩 模型分布</h3>
            <div class="chart-wrap"><canvas id="modelChart"></canvas></div>
        </div>
    </div>

    <!-- Charts Row 2 -->
    <div class="chart-grid-3">
        <div class="chart-card">
            <h3>📦 项目分布</h3>
            <div class="chart-wrap"><canvas id="projectChart"></canvas></div>
        </div>
        <div class="chart-card">
            <h3>🔥 最烧钱任务 <span class="badge">TOP</span></h3>
            <div class="chart-wrap"><canvas id="taskChart"></canvas></div>
        </div>
        <div class="chart-card">
            <h3>💰 预算进度</h3>
            <div class="chart-wrap"><canvas id="budgetChart"></canvas></div>
        </div>
    </div>

    <!-- Automation Cost Estimates -->
    <div class="chart-card full">
        <h3>📋 自动化成本估算明细</h3>
        <div class="table-wrap">
            <table>
                <thead>
                    <tr>
                        <th>自动化任务</th>
                        <th>模型</th>
                        <th>输入 Token</th>
                        <th>输出 Token</th>
                        <th>频次</th>
                        <th>单次成本 (¥)</th>
                    </tr>
                </thead>
                <tbody id="estimateTableBody"></tbody>
            </table>
        </div>
    </div>
</div>

<!-- Footer -->
<div class="footer">
    Generated by cost_dashboard.py · Data from ~/.ai_cost_log.jsonl · Budget: ¥{MONTHLY_BUDGET_CNY}/月
</div>

<script>
// ============================================================
// DATA (embedded by Python)
// ============================================================
const DATA = __DATA_PLACEHOLDER__;

// ============================================================
// Theme
// ============================================================
function toggleTheme() {
    const html = document.documentElement;
    const current = html.getAttribute('data-theme');
    const next = current === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', next);
    localStorage.setItem('cost-dash-theme', next);
    document.querySelectorAll('.theme-toggle .icon').forEach(el => {
        el.classList.toggle('active', el.dataset.themeVal === next);
    });
}
document.addEventListener('DOMContentLoaded', () => {
    const saved = localStorage.getItem('cost-dash-theme') || 'dark';
    document.documentElement.setAttribute('data-theme', saved);
    document.querySelectorAll('.theme-toggle .icon').forEach(el => {
        el.classList.toggle('active', el.dataset.themeVal === saved);
    });
});

// ============================================================
// Helpers
// ============================================================
function fmt(n) { return '¥' + n.toFixed(4); }

function getColors(n) {
    const palette = [
        '#4f8cff', '#34d399', '#fbbf24', '#f87171', '#a78bfa',
        '#60a5fa', '#f472b6', '#34d399', '#fb923c', '#818cf8',
        '#2dd4bf', '#e879f9', '#fbbf24', '#64748b', '#94a3b8'
    ];
    return Array.from({length: n}, (_, i) => palette[i % palette.length]);
}

function getThemeColors() {
    const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
    return {
        grid: isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)',
        text: isDark ? '#8892a4' : '#5a6070',
    };
}

function chartDefaults() {
    const tc = getThemeColors();
    return {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: {
                labels: { color: tc.text, font: { size: 11 }, padding: 12 }
            },
            tooltip: {
                backgroundColor: 'rgba(10,14,26,0.9)',
                titleColor: '#e8edf5',
                bodyColor: '#8892a4',
                borderColor: 'rgba(255,255,255,0.1)',
                borderWidth: 1,
                cornerRadius: 8,
                padding: 10,
            }
        },
        scales: {
            x: { grid: { color: tc.grid }, ticks: { color: tc.text, font: { size: 10 } } },
            y: { grid: { color: tc.grid }, ticks: { color: tc.text, font: { size: 10 } } }
        }
    };
}

// ============================================================
// Summary Cards
// ============================================================
function renderSummary(data) {
    const cards = [
        { label: '📊 本月总花费', cls: 'card-total', value: fmt(data.month.total), sub: `${data.month.count} 次调用` },
        { label: '📈 预估月底', cls: 'card-project', value: fmt(data.month.projection), sub: data.month.projection > data.month.budget ? '⚠️ 可能超预算' : '✅ 预算内' },
        { label: '💎 剩余预算', cls: 'card-budget', value: fmt(data.month.remaining), sub: `预算 ¥${data.month.budget}/月` },
        { label: '📋 自动化估算', cls: 'card-count', value: fmt(data.estimateTotal), sub: `${data.estimates.length} 个自动化 × 单次` },
    ];
    document.getElementById('summaryCards').innerHTML = cards.map(c =>
        `<div class="summary-card ${c.cls}"><div class="label">${c.label}</div><div class="value">${c.value}</div><div class="sub">${c.sub}</div></div>`
    ).join('');
}

// ============================================================
// Charts
// ============================================================
function renderCharts(data) {
    const tc = getThemeColors();
    const def = chartDefaults();

    // 1. Daily Trend
    const trendCtx = document.getElementById('trendChart').getContext('2d');
    new Chart(trendCtx, {
        type: 'line',
        data: {
            labels: data.dailyTrend.map(d => d.date.slice(5)),
            datasets: [{
                label: '每日成本 (¥)',
                data: data.dailyTrend.map(d => d.cost),
                borderColor: '#4f8cff',
                backgroundColor: (ctx) => {
                    const g = ctx.chart.ctx.createLinearGradient(0, 0, 0, 280);
                    g.addColorStop(0, 'rgba(79,140,255,0.25)');
                    g.addColorStop(1, 'rgba(79,140,255,0)');
                    return g;
                },
                fill: true,
                tension: 0.35,
                pointRadius: 3,
                pointHoverRadius: 6,
                borderWidth: 2,
            }]
        },
        options: {
            ...def,
            plugins: {
                ...def.plugins,
                legend: { display: false }
            }
        }
    });

    // 2. Model Distribution (doughnut)
    const modelCtx = document.getElementById('modelChart').getContext('2d');
    const modelData = data.modelDistribution;
    new Chart(modelCtx, {
        type: 'doughnut',
        data: {
            labels: modelData.map(d => `${d.name} (${d.cost.toFixed(4)})`),
            datasets: [{
                data: modelData.map(d => d.cost),
                backgroundColor: getColors(modelData.length),
                borderWidth: 0,
            }]
        },
        options: {
            ...def,
            cutout: '65%',
            plugins: {
                ...def.plugins,
                legend: { position: 'bottom', labels: { ...def.plugins.legend.labels, padding: 8 } }
            }
        }
    });

    // 3. Project Distribution (doughnut)
    const projCtx = document.getElementById('projectChart').getContext('2d');
    const projData = data.projectDistribution;
    new Chart(projCtx, {
        type: 'doughnut',
        data: {
            labels: projData.map(d => `${d.name} (${d.cost.toFixed(4)})`),
            datasets: [{
                data: projData.map(d => d.cost),
                backgroundColor: getColors(projData.length),
                borderWidth: 0,
            }]
        },
        options: {
            ...def,
            cutout: '65%',
            plugins: {
                ...def.plugins,
                legend: { position: 'bottom', labels: { ...def.plugins.legend.labels, padding: 8 } }
            }
        }
    });

    // 4. Top Tasks (horizontal bar)
    const taskCtx = document.getElementById('taskChart').getContext('2d');
    const taskData = data.topTasks;
    new Chart(taskCtx, {
        type: 'bar',
        data: {
            labels: taskData.map(d => d.name.length > 10 ? d.name.slice(0,10)+'…' : d.name),
            datasets: [{
                label: '成本 (¥)',
                data: taskData.map(d => d.cost),
                backgroundColor: getColors(taskData.length),
                borderRadius: 4,
                borderSkipped: false,
            }]
        },
        options: {
            ...def,
            indexAxis: 'y',
            plugins: {
                ...def.plugins,
                legend: { display: false }
            }
        }
    });

    // 5. Budget Gauge
    const budgetCtx = document.getElementById('budgetChart').getContext('2d');
    const used = data.month.total;
    const budget = data.month.budget;
    const pct = Math.min(used / budget * 100, 100);
    const gaugeColor = pct < 50 ? '#34d399' : pct < 80 ? '#fbbf24' : '#f87171';
    new Chart(budgetCtx, {
        type: 'doughnut',
        data: {
            labels: ['已用', '剩余'],
            datasets: [{
                data: [pct, 100 - pct],
                backgroundColor: [gaugeColor, 'rgba(255,255,255,0.08)'],
                borderWidth: 0,
            }]
        },
        options: {
            ...def,
            cutout: '78%',
            plugins: {
                ...def.plugins,
                legend: { display: false },
                tooltip: {
                    ...def.plugins.tooltip,
                    callbacks: {
                        label: (ctx) => ctx.dataIndex === 0
                            ? `已用: ¥${used.toFixed(4)} (${pct.toFixed(1)}%)`
                            : `剩余: ¥${(budget - used).toFixed(4)}`
                    }
                }
            }
        },
        plugins: [{
            id: 'centerText',
            afterDraw(chart) {
                const { ctx, chartArea: { top, left, right, bottom } } = chart;
                const cx = (left + right) / 2;
                const cy = (top + bottom) / 2;
                ctx.save();
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.font = 'bold 28px system-ui, sans-serif';
                ctx.fillStyle = getComputedStyle(document.body).getPropertyValue('--text').trim();
                ctx.fillText(pct.toFixed(1) + '%', cx, cy - 8);
                ctx.font = '11px system-ui, sans-serif';
                ctx.fillStyle = getComputedStyle(document.body).getPropertyValue('--text-secondary').trim();
                ctx.fillText(`¥${used.toFixed(2)} / ¥${budget}`, cx, cy + 22);
                ctx.restore();
            }
        }]
    });
}

// ============================================================
// Estimation Table
// ============================================================
function renderTable(data) {
    const tbody = document.getElementById('estimateTableBody');
    const total = data.estimates.reduce((s, e) => s + e.cost, 0);
    tbody.innerHTML = data.estimates.map(e =>
        `<tr>
            <td><strong>${e.name}</strong></td>
            <td><span class="model-tag">${e.model}</span></td>
            <td>${e.inp_est.toLocaleString()}</td>
            <td>${e.out_est.toLocaleString()}</td>
            <td><span class="freq-badge freq-${e.freq}">${e.freq}</span></td>
            <td>¥${e.cost.toFixed(6)}</td>
        </tr>`
    ).join('');
    // Add total row
    tbody.innerHTML += `<tr style="font-weight:700;border-top:2px solid var(--accent);">
        <td colspan="5">合计 (${data.estimates.length} 个自动化)</td>
        <td>¥${total.toFixed(4)}</td>
    </tr>`;
}

// ============================================================
// Init
// ============================================================
document.getElementById('headerTime').textContent = '更新: ' + DATA.generated;

renderSummary(DATA);
renderCharts(DATA);
renderTable(DATA);

// Re-render on theme change (recreate charts with new colors)
// Simpler approach: just update the page-level var and wait for next page open
</script>
</body>
</html>"""


# ============================================================
# 主逻辑
# ============================================================

def build_dashboard(output_path: str = None) -> str:
    """构建仪表盘 HTML 文件"""
    records = load_cost_records()

    data = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "recordCount": len(records),
        "dailyTrend": get_daily_trend(records),
        "modelDistribution": get_model_distribution(records),
        "projectDistribution": get_project_distribution(records),
        "topTasks": get_top_tasks(records),
        "month": get_month_summary(records),
        "estimates": get_automation_estimates(),
        "estimateTotal": round(sum(
            (cfg.get("inp_est", 0) * MODEL_PRICES.get(cfg.get("model", ""), {}).get("input", 0)
             + cfg.get("out_est", 0) * MODEL_PRICES.get(cfg.get("model", ""), {}).get("output", 0)) / 10000
            for cfg in AUTO_COST_ESTIMATES.values()
        ), 6),
    }

    html = HTML_TEMPLATE.replace("__DATA_PLACEHOLDER__", json.dumps(data, ensure_ascii=False))
    html = html.replace("{MONTHLY_BUDGET_CNY}", str(MONTHLY_BUDGET_CNY))

    if output_path is None:
        desktop = Path.home() / "Desktop"
        output_path = str(desktop / f"ai-cost-dashboard-{date.today().isoformat()}.html")

    Path(output_path).write_text(html, encoding="utf-8")
    return output_path


if __name__ == "__main__":
    args = sys.argv[1:]
    output = None
    should_open = False

    for arg in args:
        if arg == "--open":
            should_open = True
        elif not arg.startswith("--"):
            output = arg

    path = build_dashboard(output)
    print(f"✅ 仪表盘已生成: {path}")
    print(f"   记录数: {len(load_cost_records())} 条")
    print(f"   自动化估算: {len(AUTO_COST_ESTIMATES)} 项")

    if should_open:
        import webbrowser
        webbrowser.open(f"file://{path}")
        print("   已自动打开浏览器")
