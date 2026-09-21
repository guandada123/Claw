#!/usr/bin/env python3
"""
股票池技术体检 — 合并双持仓 + 腾讯行情 + 价格防错 + 写入 pool_health.json
架构(L3护栏层/08-07五层落地)：
  - 池内标的：.workbuddy/data/stock_pool.json
  - 实盘持仓：.workbuddy/data/user/portfolio.json（国金）
  - 模拟持仓：.workbuddy/data/simulation/portfolio.json（投顾操盘）
  - 行情：腾讯 qt.gtimg.cn（铁律：实时价腾讯优先）
  - 价格防错：fetch价 与 portfolio 内已落库腾讯价 偏差>30% 则回退到落库价（防幻觉）
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta

DATA = os.path.join(os.path.dirname(__file__), "..", ".workbuddy", "data")
DATA = os.path.abspath(DATA)
POOL_PATH = os.path.join(DATA, "stock_pool.json")
USER_PF = os.path.join(DATA, "user", "portfolio.json")
SIM_PF = os.path.join(DATA, "simulation", "portfolio.json")
QUOTE_CACHE = os.path.join(DATA, "stock_pool_quotes_cache.json")
OUT = os.path.join(DATA, "pool_health.json")


def last_trading_friday():
    """动态取最近一个交易日(收盘口径)：周日→上周五；其余→当天或最近周五"""
    today = datetime.now()
    # 回退到最近一个周五（含今天）
    d = today
    while d.weekday() > 4:  # 5=Sat,6=Sun
        d -= timedelta(days=1)
    # 若今天晚于周五或就是周五，取最近周五；周末则已回退到上周五
    return d


def fetch_quote(code):
    """腾讯 qt.gtimg.cn 取单只行情，返回 dict 或 None"""
    prefix = "sh" if code.startswith("6") else "sz"
    url = f"http://qt.gtimg.cn/q={prefix}{code}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        data = resp.read().decode("gbk")
        parts = data.split("~")
        if len(parts) >= 40:
            price = float(parts[3]) if parts[3] else 0
            prev_close = float(parts[4]) if parts[4] else 0
            return {
                "code": code,
                "name": parts[1],
                "price": price,
                "prev_close": prev_close,
                "change_pct": round((price - prev_close) / prev_close * 100, 2) if prev_close else 0,
                "volume": int(parts[6]) if parts[6] else 0,
                "high": float(parts[33]) if parts[33] else 0,
                "low": float(parts[34]) if parts[34] else 0,
            }
    except Exception as e:
        sys.stderr.write(f"  [warn] fetch {code} failed: {e}\n")
    return None


def sanity_guard(code, fetched, ref_price):
    """价格防错：fetched 与 ref(落库腾讯价) 偏差>30% 则回退 ref"""
    if fetched is None or fetched.get("price") in (0, None):
        return None
    if ref_price and abs(fetched["price"] - ref_price) / ref_price > 0.30:
        sys.stderr.write(f"  [sanity] {code} 偏差>{30}% 回退落库价 {ref_price}\n")
        f = dict(fetched)
        f["price"] = ref_price
        f["change_pct"] = 0
        f["price_sanity_fail"] = True
        return f
    return fetched


def main():
    with open(POOL_PATH, encoding="utf-8") as f:
        pool = json.load(f)
    with open(USER_PF, encoding="utf-8") as f:
        user_pf = json.load(f)
    with open(SIM_PF, encoding="utf-8") as f:
        sim_pf = json.load(f)

    # 池内代码
    pool_codes = set()
    for sname, stocks in pool.get("sectors", {}).items():
        for s in stocks:
            pool_codes.add(s["code"])

    # 持仓（合并实盘+模拟）
    holdings = {}  # code -> dict(name, avg_cost, shares, ref_price, type)
    ref_prices = {}
    for h in user_pf.get("holdings", []):
        holdings[h["code"]] = {
            "code": h["code"], "name": h["name"],
            "avg_cost": h.get("avg_cost", 0), "shares": h.get("shares", 0),
            "type": "live",
        }
        ref_prices[h["code"]] = h.get("current_price")
    for code, p in sim_pf.get("positions", {}).items():
        if p.get("shares", 0) <= 0:
            continue
        holdings[code] = {
            "code": code, "name": p.get("name", code),
            "avg_cost": p.get("avg_cost", 0), "shares": p.get("shares", 0),
            "type": "sim",
        }
        if ref_prices.get(code) is None:
            ref_prices[code] = p.get("current_price")

    all_codes = list(pool_codes | set(holdings.keys()))

    # 批量取价
    quotes = {}
    for code in all_codes:
        q = fetch_quote(code)
        q = sanity_guard(code, q, ref_prices.get(code))
        if q:
            quotes[code] = q

    # 落缓存
    with open(QUOTE_CACHE, "w", encoding="utf-8") as f:
        json.dump(quotes, f, ensure_ascii=False, indent=2)
    print(f"✅ 已获取 {len(quotes)}/{len(all_codes)} 只标的行情（池{pool_codes and len(pool_codes)}∪持仓{len(holdings)}去重{len(all_codes)}）")

    # 行业统计
    sector_stats = []
    total = 0
    for sname, stocks in pool.get("sectors", {}).items():
        count = len(stocks)
        total += count
        codes_in = [s["code"] for s in stocks]
        changes = [quotes.get(c, {}).get("change_pct", 0) for c in codes_in if c in quotes]
        avg = round(sum(changes) / len(changes), 2) if changes else 0
        sector_stats.append({
            "name": sname, "count": count,
            "stocks": "/".join([s.get("name", "") for s in stocks]),
            "avg_change": avg,
        })

    # 涨跌排行（全池）
    all_changes = [(c, q.get("change_pct", 0), q.get("name", "")) for c, q in quotes.items() if c in pool_codes]
    all_changes.sort(key=lambda x: x[1], reverse=True)
    n_up = sum(1 for _, p, _ in all_changes if p > 0)
    n_down5 = sum(1 for _, p, _ in all_changes if p < -5)
    pool_avg = round(sum(p for _, p, _ in all_changes) / len(all_changes), 2) if all_changes else 0
    top_gainers = [{"code": c, "name": n, "change_pct": p} for c, p, n in all_changes[:5]]
    top_losers = [{"code": c, "name": n, "change_pct": p} for c, p, n in all_changes[-5:]]
    top_losers.reverse()

    # 持仓体检
    holdings_detail = {}
    for code, h in holdings.items():
        q = quotes.get(code)
        if not q:
            continue
        ac = h.get("avg_cost", 0)
        sl = round(ac * 0.92, 2) if ac else 0
        pnl = round((q["price"] - ac) / ac * 100, 2) if ac else 0
        in_pool = code in pool_codes
        if sl > 0 and q["price"] < sl:
            status = "⚠️ 已破止损"
        elif sl > 0:
            status = "安全"
        else:
            status = "观察"
        holdings_detail[code] = {
            "code": code, "name": q.get("name", h.get("name", code)),
            "price": q["price"], "change_pct": q["change_pct"],
            "pnl_pct": pnl, "avg_cost": ac, "stop_loss": sl,
            "in_pool": in_pool, "status": status,
            "type": h.get("type"),
        }

    # 集中度 HHI
    hhi = round(sum((s["count"] / total) ** 2 for s in sector_stats), 3) if total else 0
    top = max(sector_stats, key=lambda x: x["count"])
    missing = [f"{c}{holdings[c]['name']}" for c in holdings if c not in pool_codes]

    # 风险/防御
    risk_sectors = sorted([s for s in sector_stats if s["avg_change"] < -2], key=lambda x: x["avg_change"])
    defensive_sectors = sorted([s for s in sector_stats if s["avg_change"] > 0.5], key=lambda x: x["avg_change"], reverse=True)

    # 健康评分
    breach = sum(1 for h in holdings_detail.values() if h["status"] == "⚠️ 已破止损")
    health_score = max(0, min(10, 6 + (1 if pool_avg > 0 else 0) - breach))
    health_assessment = "健康" if health_score >= 8 else "中等" if health_score >= 5 else "偏弱"

    asof = last_trading_friday().strftime("%Y-%m-%d")

    health = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "data_as_of": f"{asof} 收盘 (周日无交易，行情反映最近交易日)",
        "pool_size": total,
        "sector_count": len(sector_stats),
        "holdings_total": len(holdings),
        "holdings_in_pool": sum(1 for h in holdings_detail.values() if h["in_pool"]),
        "holdings_detail": holdings_detail,
        "sectors": sector_stats,
        "concentration_hhi": hhi,
        "concentration_assessment": ("分散（HHI<0.1）" if hhi < 0.1 else "中等集中（HHI 0.1-0.15）" if hhi < 0.15 else "高度集中（HHI>0.15）"),
        "top_sector": top["name"],
        "top_sector_weight": f"{top['count']}/{total} = {round(top['count']/total*100,1)}%",
        "risk_sectors": [{"name": s["name"], "avg_change": s["avg_change"]} for s in risk_sectors],
        "defensive_sectors": [{"name": s["name"], "avg_change": s["avg_change"]} for s in defensive_sectors],
        "top_gainers": top_gainers,
        "top_losers": top_losers,
        "pool_avg_change": pool_avg,
        "n_up": n_up,
        "n_down5": n_down5,
        "missing_in_pool": missing,
        "summary": f"全池{total}只/{len(sector_stats)}行业，HHI={hhi}({('分散' if hhi<0.1 else '中等集中' if hhi<0.15 else '高度集中')})。最近交易日全池均值{pool_avg:+.2f}%，{n_up}只上涨/{total-n_up}只下跌，{n_down5}只单日跌超5%。持仓{len(holdings)}只体检：{sum(1 for h in holdings_detail.values() if h['in_pool'])}只在池、{len(missing)}只不在池。不在池：{'、'.join(missing) if missing else '无'}。",
        "health_score": health_score,
        "health_assessment": health_assessment,
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(health, f, ensure_ascii=False, indent=2)
    print(f"✅ pool_health.json 已写入（{total}只/{len(sector_stats)}行业，HHI={hhi}，评分{health_score}/10）")
    return health_score


if __name__ == "__main__":
    sys.exit(0 if main() is not None else 1)
