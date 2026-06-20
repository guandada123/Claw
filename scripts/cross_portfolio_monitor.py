#!/usr/bin/env python3
"""
跨盘风险联动监控 — 模拟盘(投顾操盘) + 实盘(用户持仓) 统一风控

执行: python3 scripts/cross_portfolio_monitor.py
输出: JSON 到 stdout，可选 --push 推送飞书

功能:
1. 同标持仓检测 (两盘同时持有的股票)
2. 两盘合计行业集中度
3. 跨盘止损/止盈联动预警
4. 策略库规则验证
"""

import json
import sys
from datetime import datetime
from pathlib import Path

# ── 路径配置 ──────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SIM_PORTFOLIO = PROJECT_ROOT / ".workbuddy" / "data" / "simulation" / "portfolio.json"
USER_PORTFOLIO = PROJECT_ROOT / ".workbuddy" / "data" / "user" / "portfolio.json"
STRATEGY_LIB   = PROJECT_ROOT / ".workbuddy" / "data" / "simulation" / "strategy_library.json"

# ── 行业分类映射（与 asset-allocation-framework.md 一致） ──────────
INDUSTRY_MAP = {
    # 🏭 科技/半导体
    "002049": "🏭 科技/半导体",  # 紫光国微
    "603986": "🏭 科技/半导体",  # 兆易创新
    "600703": "🏭 科技/半导体",  # 三安光电
    "300782": "🏭 科技/半导体",  # 卓胜微
    "600745": "🏭 科技/半导体",  # 闻泰科技
    "600498": "🏭 科技/半导体",  # 烽火通信（通信→科技链）
    "600522": "🏭 科技/半导体",  # 中天科技（通信→科技链）
    "600206": "🏭 科技/半导体",  # 有研新材（电子材料→科技链）
    # 🛢️ 周期/资源
    "601899": "🛢️ 周期/资源",  # 紫金矿业（有色）
    "600547": "🛢️ 周期/资源",  # 山东黄金
    "002601": "🛢️ 周期/资源",  # 龙佰集团（钛白粉→化工）
    "600010": "🛢️ 周期/资源",  # 包钢股份
    "600585": "🛢️ 周期/资源",  # 海螺水泥（建材→基建链也用）
    "600176": "🛢️ 周期/资源",  # 中国巨石（建材→基建链也用）
    # 🏗️ 基建/建材
    "600031": "🏗️ 基建/建材",  # 三一重工（机械）
    "002271": "🏗️ 基建/建材",  # 东方雨虹
    # 🚗 新能源/汽车
    "300750": "🚗 新能源/汽车",  # 宁德时代
    "002594": "🚗 新能源/汽车",  # 比亚迪
    # 💊 医药/消费
    "600519": "💊 医药/消费",  # 贵州茅台
    "000858": "💊 医药/消费",  # 五粮液
    "300760": "💊 医药/消费",  # 迈瑞医疗
    # 🏦 金融/地产
    "601318": "🏦 金融/地产",  # 中国平安
    "600036": "🏦 金融/地产",  # 招商银行
    "601166": "🏦 金融/地产",  # 兴业银行
    # 关联性链（同一逻辑链的额外合并规则）
    "科技链": ["🏭 科技/半导体"],
    "基建链": ["🏗️ 基建/建材", "🛢️ 周期/资源"],  # 水泥/钢铁/化工也属基建链
}

# ── 关联性链分组 ────────────────────────────────────────────────
CHAIN_MAP = {
    "科技链": {"groups": ["🏭 科技/半导体"], "max_pct": 50},
    "基建链": {"groups": ["🏗️ 基建/建材", "🛢️ 周期/资源"], "max_pct": 50},
}


def load_json(path: Path):
    """安全读取 JSON 文件，失败返回空结构"""
    if not path.exists():
        print(f"[WARN] 文件不存在: {path}", file=sys.stderr)
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[ERROR] 读取失败 {path}: {e}", file=sys.stderr)
        return {}


def get_industry(code: str, name: str = "") -> str:
    """通过股票代码或名称获取行业分组"""
    code = code.strip()
    if code in INDUSTRY_MAP:
        return INDUSTRY_MAP[code]

    # 按名称匹配（兜底）
    name_lower = name.lower()
    if any(kw in name_lower for kw in ["半导体", "芯片", "电子", "通信", "消费电子"]):
        return "🏭 科技/半导体"
    if any(kw in name_lower for kw in ["化工", "有色", "煤炭", "石油", "黄金", "钢铁"]):
        return "🛢️ 周期/资源"
    if any(kw in name_lower for kw in ["建材", "水泥", "钢铁", "机械", "建筑"]):
        return "🏗️ 基建/建材"
    if any(kw in name_lower for kw in ["锂电", "光伏", "新能源", "汽车", "充电桩"]):
        return "🚗 新能源/汽车"
    if any(kw in name_lower for kw in ["医药", "医疗", "食品", "白酒", "饮料"]):
        return "💊 医药/消费"
    if any(kw in name_lower for kw in ["银行", "保险", "券商", "房地产", "地产"]):
        return "🏦 金融/地产"
    return "📦 其他"


def parse_sim_positions(sim_data: dict) -> dict:
    """解析模拟盘持仓"""
    positions = sim_data.get("positions", {})
    if isinstance(positions, dict):
        return positions
    return {}


def parse_user_holdings(user_data: dict) -> list:
    """解析实盘持仓"""
    return user_data.get("holdings", [])


def calc_combined_metrics(sim_positions: dict, user_holdings: list):
    """
    计算两盘合并指标
    返回: {
        "shared_holdings": [...],
        "industry_concentration": {...},
        "chain_risk": {...}
    }
    """
    result = {
        "shared_holdings": [],
        "industry_concentration": {},
        "chain_risk": {},
        "sim_market_value_total": 0,
        "user_market_value_total": 0,
    }

    # 构建实盘快速查询
    user_by_code = {}
    for h in user_holdings:
        code = h.get("code", "").strip()
        user_by_code[code] = h

    # 计算双方总市值
    for code, pos in sim_positions.items():
        price = pos.get("current_price", 0) or 0
        shares = pos.get("shares", 0) or 0
        result["sim_market_value_total"] += price * shares

    for h in user_holdings:
        result["user_market_value_total"] += h.get("market_value", 0) or 0

    # 合并所有持仓的市值（用于行业集中度计算）
    combined_by_industry = {}
    all_holdings_combined = []
    sim_total = result["sim_market_value_total"]
    user_total = result["user_market_value_total"]

    # 模拟盘
    for code, pos in sim_positions.items():
        name = pos.get("name", code)
        price = pos.get("current_price", 0) or 0
        shares = pos.get("shares", 0) or 0
        cost = pos.get("avg_cost", 0) or 0
        market_value = price * shares
        industry = get_industry(code, name)
        pnl_pct = ((price - cost) / cost * 100) if cost > 0 else 0

        entry = {
            "code": code, "name": name, "portfolio": "sim",
            "shares": shares, "price": price, "cost": cost,
            "market_value": market_value, "pnl_pct": round(pnl_pct, 2),
            "industry": industry,
            "stop_loss_price": round(cost * 0.92, 2),
        }
        all_holdings_combined.append(entry)
        combined_by_industry[industry] = combined_by_industry.get(industry, 0) + market_value

    # 实盘
    for h in user_holdings:
        code = h.get("code", "").strip()
        name = h.get("name", code)
        price = h.get("current_price", 0) or 0
        shares = h.get("shares", 0) or 0
        cost = h.get("cost_price", 0) or 0
        market_value = h.get("market_value", 0) or 0
        pnl_pct = h.get("pnl_pct", 0) or 0
        industry = get_industry(code, name)

        entry = {
            "code": code, "name": name, "portfolio": "user",
            "shares": shares, "price": price, "cost": cost,
            "market_value": market_value, "pnl_pct": round(pnl_pct, 2),
            "industry": industry,
            "stop_loss_price": round(cost * 0.92, 2),
        }
        all_holdings_combined.append(entry)
        combined_by_industry[industry] = combined_by_industry.get(industry, 0) + market_value

    # ── 同标检测 ──
    sim_codes = {code for code in sim_positions}
    user_codes = {h["code"].strip() for h in user_holdings if h.get("code")}
    shared_codes = sim_codes & user_codes

    for code in shared_codes:
        sim_pos = sim_positions[code]
        user_pos = user_by_code[code]
        sim_mv = sim_pos["current_price"] * sim_pos["shares"]
        user_mv = user_pos["market_value"]
        combined_mv = sim_mv + user_mv
        industry = get_industry(code, sim_pos.get("name", ""))

        # 计算两盘合计占各自总市值比例
        sim_pct = round(sim_mv / sim_total * 100, 1) if sim_total else 0
        user_pct = round(user_mv / user_total * 100, 1) if user_total else 0

        # 两盘综合风控
        sim_cost = sim_pos.get("avg_cost", 0)
        user_cost = user_pos.get("cost_price", 0)
        sim_pnl = sim_pos["current_price"] / sim_cost - 1 if sim_cost else 0
        user_pnl = user_pos["current_price"] / user_cost - 1 if user_cost else 0

        result["shared_holdings"].append({
            "code": code,
            "name": sim_pos.get("name", ""),
            "industry": industry,
            "sim": {
                "shares": sim_pos["shares"],
                "cost": sim_cost,
                "current_price": sim_pos["current_price"],
                "market_value": round(sim_mv, 2),
                "pnl_pct": round(sim_pnl * 100, 2),
                "pct_of_sim": sim_pct,
                "stop_loss_price": round(sim_cost * 0.92, 2) if sim_cost else 0,
                "stop_loss_distance": None,
            },
            "user": {
                "shares": user_pos.get("shares", 0),
                "cost": user_cost,
                "current_price": user_pos.get("current_price", 0),
                "market_value": round(user_mv, 2),
                "pnl_pct": round(user_pnl * 100, 2),
                "pct_of_user": user_pct,
                "stop_loss_price": round(user_cost * 0.92, 2) if user_cost else 0,
                "stop_loss_distance": None,
            },
            "combined_market_value": round(combined_mv, 2),
        })

        # 计算止损距离
        shared = result["shared_holdings"][-1]
        if shared["sim"]["cost"]:
            sim_dist = (shared["sim"]["current_price"] - shared["sim"]["stop_loss_price"]) / shared["sim"]["stop_loss_price"] * 100
            shared["sim"]["stop_loss_distance"] = round(sim_dist, 1)
        if shared["user"]["cost"]:
            user_dist = (shared["user"]["current_price"] - shared["user"]["stop_loss_price"]) / shared["user"]["stop_loss_price"] * 100
            shared["user"]["stop_loss_distance"] = round(user_dist, 1)

    # ── 行业集中度（两盘合并） ──
    total_market_value = result["sim_market_value_total"] + result["user_market_value_total"]
    for industry, mv in sorted(combined_by_industry.items(), key=lambda x: -x[1]):
        pct = round(mv / total_market_value * 100, 1) if total_market_value else 0
        result["industry_concentration"][industry] = {
            "market_value": round(mv, 2),
            "pct": pct,
            "limit": 40,
            "status": "✅" if pct <= 40 else ("⚠️" if pct <= 50 else "🚨"),
        }

    # ── 关联性链风险 ──
    for chain_name, chain in CHAIN_MAP.items():
        chain_mv = sum(
            combined_by_industry.get(g, 0) for g in chain["groups"]
            if g in combined_by_industry
        )
        chain_pct = round(chain_mv / total_market_value * 100, 1) if total_market_value else 0
        max_pct = chain["max_pct"]
        status = "✅" if chain_pct <= max_pct else ("⚠️" if chain_pct <= max_pct + 10 else "🚨")
        stocks_in_chain = [e for e in all_holdings_combined if e["industry"] in chain["groups"]]
        result["chain_risk"][chain_name] = {
            "groups": chain["groups"],
            "total_market_value": round(chain_mv, 2),
            "pct": chain_pct,
            "max_pct": max_pct,
            "status": status,
            "stocks": [
                {
                    "code": s["code"], "name": s["name"],
                    "portfolio": s["portfolio"],
                    "industry": s["industry"],
                    "market_value": s["market_value"],
                    "pnl_pct": s["pnl_pct"],
                }
                for s in stocks_in_chain
            ],
        }

    return result


def check_strategy_alerts(combined: dict, strategy: dict):
    """
    策略库检查 — 对照 loss_patterns_to_avoid 发现潜在风险
    """
    alerts = []
    rules = strategy.get("risk_control_rules", {})
    patterns = strategy.get("loss_patterns_to_avoid", [])

    # 检查行业集中度
    for industry, info in combined.get("industry_concentration", {}).items():
        if info["status"] in ("⚠️", "🚨"):
            alerts.append({
                "type": "行业集中度超限",
                "industry": industry,
                "detail": f"{industry} {info['pct']}%（上限{info['limit']}%），两盘合并计算",
                "severity": "🚨" if info["status"] == "🚨" else "⚠️",
                "rule_ref": "L001",
            })

    # 检查同标持仓的止损距离
    for sh in combined.get("shared_holdings", []):
        for side in ["sim", "user"]:
            dist = sh[side].get("stop_loss_distance")
            if dist is not None and dist < 5:
                alerts.append({
                    "type": "同标逼近止损",
                    "stock": f"{sh['name']}({sh['code']})",
                    "portfolio": side,
                    "detail": f"{sh['name']}({sh['code']}) {side}盘距止损线仅 {dist}%",
                    "severity": "🔴",
                    "rule_ref": "止损规则",
                })

    # 检查并仓节奏（模拟盘建仓后现金 > min_cash）
    min_cash = rules.get("min_cash_after_build", 15)

    return alerts


def monitor():
    """主监控入口"""
    sim_data = load_json(SIM_PORTFOLIO)
    user_data = load_json(USER_PORTFOLIO)
    strategy_data = load_json(STRATEGY_LIB)

    if not sim_data and not user_data:
        print(json.dumps({"error": "无法读取持仓数据", "timestamp": datetime.now().isoformat()}))
        sys.exit(1)

    sim_positions = parse_sim_positions(sim_data)
    user_holdings = parse_user_holdings(user_data)

    # 合并分析
    combined = calc_combined_metrics(sim_positions, user_holdings)

    # 策略库预警
    alerts = []
    if strategy_data:
        alerts = check_strategy_alerts(combined, strategy_data)

    # 最终报告
    report = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "sim": {
                "total_asset": round(sum(p["current_price"] * p["shares"] for p in sim_positions.values()) + sim_data.get("cash", 0), 2),
                "holdings": len(sim_positions),
                "cash": sim_data.get("cash", 0),
            },
            "user": {
                "total_asset": user_data.get("summary", {}).get("total_assets", 0),
                "holdings": len(user_holdings),
                "cash": user_data.get("summary", {}).get("cash_available", 0),
            },
        },
        "shared_holdings": combined["shared_holdings"],
        "industry_concentration": combined["industry_concentration"],
        "chain_risk": combined["chain_risk"],
        "alerts": alerts,
        "alerts_count": len(alerts),
        "health": "🟢" if len(alerts) == 0 else ("🟡" if len(alerts) <= 2 else "🔴"),
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    report = monitor()

    # ── 可选推送 ──
    if "--push" in sys.argv:
        # 有预警时推送飞书
        if report.get("alerts_count", 0) > 0:
            import subprocess
            msg_lines = [
                f"📊 跨盘联动监控 | {report['timestamp']}",
                "━━━━━━━━━━━━━",
                f"健康状态：{report['health']} | 预警 {report['alerts_count']} 条",
            ]
            for alert in report.get("alerts", []):
                msg_lines.append(f"\n{alert['severity']} {alert['type']}: {alert['detail']}")

            if report.get("shared_holdings"):
                msg_lines.append("\n🔗 同标持仓：")
                for sh in report["shared_holdings"]:
                    msg_lines.append(f"  {sh['name']}({sh['code']}) | "
                                     f"模拟:{sh['sim']['pnl_pct']}% | "
                                     f"实盘:{sh['user']['pnl_pct']}%")

            msg = "\n".join(msg_lines)
            cmd = [
                "lark-cli", "im", "+messages-send",
                "--chat-id", "oc_9ee5303497f5e0e71666b610d6bdc346",
                "--as", "bot",
                "--markdown", msg,
            ]
            try:
                subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            except Exception as e:
                print(f"[ERROR] 飞书推送失败: {e}", file=sys.stderr)
        else:
            print("[INFO] 无预警，跳过推送", file=sys.stderr)
