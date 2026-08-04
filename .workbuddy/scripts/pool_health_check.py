#!/usr/bin/env python3
"""
股票池技术体检 v4 — 数据驱动
- 读取股票池 + 实盘(user/portfolio.json) + 模拟盘(simulation/portfolio.json)
- 批量获取腾讯行情(反映最近交易日收盘)
- 计算行业集中度(HHI)、涨跌排行、持仓体检
- 写入 pool_health.json 供周日前瞻消费

注意：自动化原 prompt 指向 $DATA/portfolio.json(已不存在)，真实数据按数据隔离架构拆分：
  · 国金实盘 -> .workbuddy/data/user/portfolio.json  (live)
  · 投顾模拟盘 -> .workbuddy/data/simulation/portfolio.json (sim)
本脚本据此合并，保持与下周前瞻消费端兼容的字段结构。
"""

import json
import urllib.request
from datetime import datetime

CLAW = "/Users/guan/WorkBuddy/Claw"
DATA = f"{CLAW}/.workbuddy/data"
STOCK_POOL = f"{DATA}/stock_pool.json"
USER_PF = f"{DATA}/user/portfolio.json"
SIM_PF = f"{DATA}/simulation/portfolio.json"
QUOTES_CACHE = f"{DATA}/stock_pool_quotes_cache.json"
HEALTH_OUT = f"{DATA}/pool_health.json"

# ----- 1. 读取股票池 -----
with open(STOCK_POOL, encoding="utf-8") as f:
    pool = json.load(f)

pool_codes = []
for sname, stocks in pool.get("sectors", {}).items():
    for s in stocks:
        pool_codes.append(s["code"])
pool_codes = list(set(pool_codes))

# ----- 2. 合并持仓（实盘 + 模拟盘） -----
with open(USER_PF, encoding="utf-8") as f:
    user_pf = json.load(f)
with open(SIM_PF, encoding="utf-8") as f:
    sim_pf = json.load(f)

live_holdings = []
for h in user_pf.get("holdings", []):
    live_holdings.append(
        {
            "code": h["code"],
            "name": h.get("name", ""),
            "shares": h.get("shares", 0),
            "avg_cost": h.get("avg_cost", 0),
            "current_price": h.get("current_price", 0),
        }
    )

sim_holdings = []
# 用 positions 字典(含 600036 招商银行)，比 holdings 数组更全
for code, pos in sim_pf.get("positions", {}).items():
    sim_holdings.append(
        {
            "code": code,
            "name": pos.get("name", ""),
            "shares": pos.get("shares", 0),
            "avg_cost": pos.get("avg_cost", 0),
            "current_price": pos.get("current_price", 0),
        }
    )

merged_portfolio = {"live": {"holdings": live_holdings}, "sim": {"holdings": sim_holdings}}

all_holding_codes = set([h["code"] for h in live_holdings] + [h["code"] for h in sim_holdings])

# 需获取行情的代码 = 池内 + 所有持仓
need_codes = list(set(pool_codes) | all_holding_codes)


# ----- 3. 批量获取行情（腾讯 gtimg） -----
def fetch_quote(code):
    prefix = "sh" if code.startswith("6") else "sz"
    url = f"http://qt.gtimg.cn/q={prefix}{code}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=10)
    data = resp.read().decode("gbk")
    parts = data.split("~")
    if len(parts) < 40:
        return None
    prev = float(parts[4]) if parts[4] else 0
    price = float(parts[3]) if parts[3] else 0
    chg = round((price - prev) / prev * 100, 2) if prev else 0
    return {
        "name": parts[1],
        "price": price,
        "prev_close": prev,
        "change_pct": chg,
        "volume": int(parts[6]) if parts[6] else 0,
        "high": float(parts[33]) if parts[33] else 0,
        "low": float(parts[34]) if parts[34] else 0,
    }


quotes = {}
for code in need_codes:
    try:
        q = fetch_quote(code)
        if q:
            quotes[code] = q
    except Exception:
        pass

with open(QUOTES_CACHE, "w", encoding="utf-8") as f:
    json.dump(quotes, f, ensure_ascii=False)
print(f"✅ 已获取 {len(quotes)}/{len(need_codes)} 只标的行情")

# ----- 4. 计算技术面体检指标 -----
sector_stats = []
total_stocks = 0
for sname, stocks in pool.get("sectors", {}).items():
    count = len(stocks)
    total_stocks += count
    codes_in = [s["code"] for s in stocks]
    changes = [quotes.get(c, {}).get("change_pct", 0) for c in codes_in if c in quotes]
    avg_chg = round(sum(changes) / len(changes), 2) if changes else 0
    sector_stats.append(
        {
            "name": sname,
            "count": count,
            "stocks": "/".join([s.get("name", "") for s in stocks]),
            "avg_change": avg_chg,
        }
    )

# 涨跌排行（全池 + 持仓）
all_changes = [
    (c, q.get("change_pct", 0), q.get("name", "")) for c, q in quotes.items() if c in pool_codes
]
all_changes.sort(key=lambda x: x[1], reverse=True)
top_gainers = [{"code": c, "name": n, "change_pct": p} for c, p, n in all_changes[:5]]
top_losers = [{"code": c, "name": n, "change_pct": p} for c, p, n in all_changes[-5:]]
top_losers.reverse()

# 持仓体检
holdings_in_pool = []
for code in all_holding_codes:
    q = quotes.get(code)
    # 找该持仓的成本/现价
    h = {}
    for hh in live_holdings:
        if hh["code"] == code:
            h = hh
            break
    if not h:
        for hh in sim_holdings:
            if hh["code"] == code:
                h = hh
                break
    avg_cost = h.get("avg_cost", 0)
    price = q["price"] if q else h.get("current_price", 0)
    sl = avg_cost * 0.92 if avg_cost else 0
    pnl = round((price - avg_cost) / avg_cost * 100, 2) if avg_cost else 0
    chg = q["change_pct"] if q else 0
    status = "安全" if (price and price >= sl) else ("⚠️ 已破止损" if sl > 0 else "观察")
    in_pool = code in pool_codes
    holdings_in_pool.append(
        {
            "code": code,
            "name": (q["name"] if q else h.get("name", "")),
            "price": price,
            "change_pct": chg,
            "pnl_pct": pnl,
            "avg_cost": avg_cost,
            "stop_loss": round(sl, 2),
            "in_pool": in_pool,
            "status": status,
        }
    )

# HHI 集中度
total = total_stocks or 1
hhi = round(sum((s["count"] / total) ** 2 for s in sector_stats), 3)
top_sector = max(sector_stats, key=lambda x: x["count"])
top_sector_weight = (
    f"{top_sector['count']}/{total} = {round(top_sector['count'] / total * 100, 1)}%"
)

# 风险/防御分类
risk_sectors = sorted(
    [s for s in sector_stats if s["avg_change"] < -2], key=lambda x: x["avg_change"]
)
defensive_sectors = sorted(
    [s for s in sector_stats if s["avg_change"] > 0.5], key=lambda x: x["avg_change"], reverse=True
)

# 全池均值
all_pool_chg = [quotes[c]["change_pct"] for c in pool_codes if c in quotes]
pool_avg = round(sum(all_pool_chg) / len(all_pool_chg), 2) if all_pool_chg else 0
n_down5 = sum(1 for v in all_pool_chg if v < -5)
n_up = sum(1 for v in all_pool_chg if v > 0)

concentration_assessment = (
    "分散（HHI<0.1）"
    if hhi < 0.1
    else "中等集中（HHI 0.1-0.15）"
    if hhi < 0.15
    else "高度集中（HHI>0.15）"
)

# 健康评分（0-10）
# 基准: 全池均值越负越扣分; 持仓破止损扣分; 集中度适中加分
score = 6
if pool_avg < -5:
    score -= 3
elif pool_avg < -2:
    score -= 2
elif pool_avg < 0:
    score -= 1
elif pool_avg > 0:
    score += 1
broken = [h for h in holdings_in_pool if h["status"].startswith("⚠️")]
score -= len(broken) * 1
if 0.1 <= hhi <= 0.15:
    score += 1
score = max(0, min(10, score))

health_assessment = "健康" if score >= 8 else "中等" if score >= 5 else "偏弱"

# 持仓不在池的提示
missing_in_pool = [h["code"] + h["name"] for h in holdings_in_pool if not h["in_pool"]]
inpool_holdings = [h for h in holdings_in_pool if h["in_pool"]]

summary = (
    f"全池{total_stocks}只/{len(sector_stats)}行业，HHI={hhi}({concentration_assessment})。"
    f"最近交易日全池均值{('+' if pool_avg >= 0 else '')}{pool_avg}%，{n_up}只上涨/{len(all_pool_chg) - n_up}只下跌，{n_down5}只单日跌超5%。"
    f"持仓{len(holdings_in_pool)}只在池体检：{len(inpool_holdings)}只在池、{len(missing_in_pool)}只不在池。"
    + (f"不在池：{'、'.join(missing_in_pool)}。" if missing_in_pool else "全部持仓均纳入股票池。")
)

health = {
    "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    "data_as_of": "2026-07-24 收盘 (周日无交易，行情反映最近交易日)",
    "pool_size": total_stocks,
    "sector_count": len(sector_stats),
    "holdings_total": len(holdings_in_pool),
    "holdings_in_pool": len(inpool_holdings),
    "holdings_detail": {h["code"]: h for h in holdings_in_pool},
    "sectors": sector_stats,
    "concentration_hhi": hhi,
    "concentration_assessment": concentration_assessment,
    "top_sector": top_sector["name"],
    "top_sector_weight": top_sector_weight,
    "risk_sectors": [{"name": s["name"], "avg_change": s["avg_change"]} for s in risk_sectors],
    "defensive_sectors": [
        {"name": s["name"], "avg_change": s["avg_change"]} for s in defensive_sectors
    ],
    "top_gainers": top_gainers,
    "top_losers": top_losers,
    "pool_avg_change": pool_avg,
    "n_up": n_up,
    "n_down5": n_down5,
    "missing_in_pool": missing_in_pool,
    "summary": summary,
    "health_score": score,
    "health_assessment": health_assessment,
}

with open(HEALTH_OUT, "w", encoding="utf-8") as f:
    json.dump(health, f, ensure_ascii=False, indent=2)
print(
    f"✅ pool_health.json 已写入（{total_stocks}只/{len(sector_stats)}行业，HHI={hhi}，健康分={score}/{10}）"
)
