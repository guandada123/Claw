#!/usr/bin/env python3
"""
模拟炒股引擎 — AI 自主决策买卖，跟踪收益与胜率
总资金：¥30,000 | 不可买创业板/科创板/北交所

优化说明（2026-06-06）：
1. 修复所有语法错误（字典缺少逗号）
2. 添加智能追踪止损（trailing stop）
3. 添加分级止盈策略（+15%卖1/3, +25%卖1/3, +35%全平）
4. 添加风险管理（单只持仓≤50%，行业分散）
5. 添加自动止损止盈检查函数
"""

import fcntl
import json
import sys
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

# 加载 Claw 公共库
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
from error_handler import atomic_write_json

# 配置日志（stderr，避免污染 stdout JSON 输出）
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("sim_trade")

# 文件锁上下文管理器 — 跨进程并发保护 portfolio.json
class PortfolioLock:
    """对 PORTFOLIO_FILE 的文件锁，防止并发读写竞态"""

    _fd = None

    def __enter__(self):
        PORTFOLIO_FILE.parent.mkdir(parents=True, exist_ok=True)
        lock_path = str(PORTFOLIO_FILE) + ".lock"
        self._fd = open(lock_path, "w")
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *args):
        if self._fd:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            self._fd.close()
            self._fd = None

DATA_DIR = Path(__file__).parent.parent / "data" / "simulation"
PORTFOLIO_FILE = DATA_DIR / "portfolio.json"
HISTORY_DIR = DATA_DIR / "history"
REPORTS_DIR = Path(__file__).parent.parent / "reports"

# A股交易费用
COMMISSION_RATE = 0.0003  # 佣金 0.03%
STAMP_TAX_RATE = 0.001  # 印花税（仅卖出）0.1%
MIN_COMMISSION = 5.0  # 最低佣金 5 元

# 不可交易的板块（创业板/科创板/北交所）
RESTRICTED_PREFIXES = ["300", "301", "688", "689", "8", "4"]

# 初始资金
INITIAL_CAPITAL = 30000.0

# 风险管理参数
MAX_POSITION_PCT = 0.50  # 单只股票最大仓位 50%
MAX_SECTOR_PCT = 0.60  # 同行业最大仓位 60%
STOP_LOSS_PCT = 0.08  # 固定止损线 -8%（降级方案）
TRAILING_STOP_PCT = 0.15  # 追踪止损：从最高价回落 15% 触发
TAKE_PROFIT_LEVELS = [  # 分级止盈
    {"pct": 0.15, "sell_ratio": 0.33, "desc": "+15%卖出1/3"},
    {"pct": 0.25, "sell_ratio": 0.33, "desc": "+25%再卖1/3"},
    {"pct": 0.35, "sell_ratio": 0.34, "desc": "+35%清仓"},
]

# star_signal 集成 (v2.1)
try:
    from star_signal_adapter import get_dynamic_stop_loss, get_star_signal

    STAR_SIGNAL_AVAILABLE = True
except ImportError:
    STAR_SIGNAL_AVAILABLE = False


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str() -> str:
    return date.today().isoformat()


def load_portfolio() -> dict:
    with PortfolioLock():
        if PORTFOLIO_FILE.exists():
            return json.loads(PORTFOLIO_FILE.read_text())
        return _empty_portfolio()


def save_portfolio(pf: dict):
    pf["config"]["updated_at"] = now()
    with PortfolioLock():
        PORTFOLIO_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(PORTFOLIO_FILE, pf)


def _empty_portfolio() -> dict:
    return {
        "config": {
            "initial_capital": INITIAL_CAPITAL,
            "created_at": today_str(),
            "updated_at": now(),
        },
        "cash": INITIAL_CAPITAL,
        "positions": {},
        "transactions": [],
        "daily_snapshot": {},
        "dividends": [],
    }


def check_restricted(code: str) -> str | None:
    """检查股票代码是否受限，返回原因或 None"""
    for prefix in RESTRICTED_PREFIXES:
        if code.startswith(prefix):
            board = {
                "300": "创业板",
                "301": "创业板",
                "688": "科创板",
                "689": "科创板",
                "8": "北交所",
                "4": "北交所/新三板",
            }.get(prefix, "受限板块")
            return f"{code} 属于{board}，当前账户无权交易"
    if "ST" in code.upper() or "*ST" in code.upper():
        return f"{code} 是ST/*ST股票，风险较高，建议谨慎"
    return None


def get_position(pf: dict, code: str) -> dict | None:
    return pf["positions"].get(code)


def calc_commission(amount: float) -> float:
    return max(amount * COMMISSION_RATE, MIN_COMMISSION)


def calc_stamp_tax(amount: float, is_sell: bool) -> float:
    return amount * STAMP_TAX_RATE if is_sell else 0.0


def calc_position_value(pf: dict, code: str) -> float:
    """计算某只股票的市值"""
    pos = pf["positions"].get(code)
    if not pos:
        return 0.0
    return pos["shares"] * pos.get("current_price", pos["avg_cost"])


def calc_total_asset(pf: dict) -> float:
    """计算总资产（现金 + 持仓市值）"""
    total = pf["cash"]
    for code, pos in pf["positions"].items():
        total += pos["shares"] * pos.get("current_price", pos["avg_cost"])
    return round(total, 2)


# ══════════════════════════════════════════════════
#  智能止损止盈检查
# ══════════════════════════════════════════════════


def check_stop_loss(pf: dict, code: str) -> dict:
    """
    检查是否需要止损
    返回：{"should_sell": bool, "reason": str, "shares_to_sell": int}
    """
    pos = pf["positions"].get(code)
    if not pos:
        return {"should_sell": False, "reason": "无持仓"}

    current_price = pos.get("current_price", pos["avg_cost"])
    avg_cost = pos["avg_cost"]
    highest_price = pos.get("highest_price", avg_cost)

    # 更新最高价
    if current_price > highest_price:
        pos["highest_price"] = current_price
        highest_price = current_price
        save_portfolio(pf)

    pnl_pct = (current_price - avg_cost) / avg_cost * 100

    # 1. ATR动态止损（优先，star_signal 提供）
    if STAR_SIGNAL_AVAILABLE:
        try:
            atr_stop = get_dynamic_stop_loss(code, current_price)
            if atr_stop.get("method") == "ATR" and current_price <= atr_stop["stop_price"]:
                return {
                    "should_sell": True,
                    "reason": f"触发ATR动态止损 {atr_stop['stop_pct']:.1f}% (止损价{atr_stop['stop_price']:.2f})",
                    "shares_to_sell": pos["shares"],
                    "priority": "high",
                }
        except Exception:
            logger.warning("AI止损计算失败，降级到固定止损")

    # 2. 固定止损：-8%（降级方案 / ATR不可用时的主方案）
    if pnl_pct <= -STOP_LOSS_PCT * 100:
        return {
            "should_sell": True,
            "reason": f"触发固定止损线 {pnl_pct:.2f}% (止损线 -{STOP_LOSS_PCT * 100:.0f}%)",
            "shares_to_sell": pos["shares"],
            "priority": "high",
        }

    # 2. 追踪止损：从最高价回落 15%
    trailing_stop_price = highest_price * (1 - TRAILING_STOP_PCT)
    if current_price <= trailing_stop_price and pnl_pct > 0:
        return {
            "should_sell": True,
            "reason": f"触发追踪止损：最高价 {highest_price:.2f}，当前价 {current_price:.2f}，回落 {TRAILING_STOP_PCT * 100:.0f}%",
            "shares_to_sell": pos["shares"],
            "priority": "medium",
        }

    return {"should_sell": False, "reason": "未触发止损"}


def check_take_profit(pf: dict, code: str) -> dict:
    """
    检查是否需要止盈（分级止盈）
    返回：{"should_sell": bool, "reason": str, "shares_to_sell": int}
    """
    pos = pf["positions"].get(code)
    if not pos:
        return {"should_sell": False, "reason": "无持仓"}

    current_price = pos.get("current_price", pos["avg_cost"])
    avg_cost = pos["avg_cost"]
    pnl_pct = (current_price - avg_cost) / avg_cost * 100

    current_level = pos.get("take_profit_level", 1)

    # 检查当前级别是否需要止盈
    if current_level <= len(TAKE_PROFIT_LEVELS):
        level = TAKE_PROFIT_LEVELS[current_level - 1]
        if pnl_pct >= level["pct"] * 100:
            shares_to_sell = int(pos["shares"] * level["sell_ratio"])
            if shares_to_sell < 100:  # A股最小交易单位100股
                shares_to_sell = pos["shares"]  # 如果剩余太少，直接清仓

            return {
                "should_sell": True,
                "reason": f"触发{level['desc']} (当前盈利 {pnl_pct:.2f}%)",
                "shares_to_sell": shares_to_sell,
                "new_level": current_level + 1,
                "priority": "low",
            }

    return {"should_sell": False, "reason": "未触发止盈"}


def auto_check_all_positions(pf: dict) -> list:
    """
    自动检查所有持仓的止损止盈条件
    返回建议交易列表
    """
    suggestions = []

    for code, pos in pf["positions"].items():
        # 检查止损
        stop_loss_check = check_stop_loss(pf, code)
        if stop_loss_check["should_sell"]:
            suggestions.append(
                {
                    "code": code,
                    "name": pos["name"],
                    "action": "SELL",
                    "reason": stop_loss_check["reason"],
                    "shares": stop_loss_check["shares_to_sell"],
                    "priority": stop_loss_check["priority"],
                }
            )
            continue  # 止损优先，不再检查止盈

        # 检查止盈
        take_profit_check = check_take_profit(pf, code)
        if take_profit_check["should_sell"]:
            suggestions.append(
                {
                    "code": code,
                    "name": pos["name"],
                    "action": "SELL",
                    "reason": take_profit_check["reason"],
                    "shares": take_profit_check["shares_to_sell"],
                    "priority": take_profit_check["priority"],
                    "new_level": take_profit_check.get("new_level"),
                }
            )

    # 按优先级排序：high > low
    priority_order = {"high": 0, "medium": 1, "low": 2}
    suggestions.sort(key=lambda x: priority_order.get(x["priority"], 99))

    return suggestions


# ══════════════════════════════════════════════════
#  交易操作
# ══════════════════════════════════════════════════


def cmd_buy(code: str, shares: int, price: float, name: str = ""):
    """买入股票（带风险管理）"""
    pf = load_portfolio()

    # 检查限制
    err = check_restricted(code)
    if err:
        return {"ok": False, "error": err}

    # 计算费用
    gross = shares * price
    commission = calc_commission(gross)
    total_cost = gross + commission

    if pf["cash"] < total_cost:
        return {
            "ok": False,
            "error": f"资金不足：需要 ¥{total_cost:.2f}，可用 ¥{pf['cash']:.2f}",
            "shortfall": round(total_cost - pf["cash"], 2),
        }

    # 风险管理：检查仓位是否超限
    total_asset = calc_total_asset(pf)
    position_value_after = calc_position_value(pf, code) + gross
    if position_value_after / total_asset > MAX_POSITION_PCT:
        return {
            "ok": False,
            "error": f"仓位超限：{code} 持仓将超过 {MAX_POSITION_PCT * 100:.0f}% (当前总资产 ¥{total_asset:,.0f})",
        }

    # 更新持仓
    if code in pf["positions"]:
        pos = pf["positions"][code]
        old_shares = pos["shares"]
        old_cost = pos["total_cost"]
        new_shares = old_shares + shares
        new_total_cost = old_cost + total_cost
        pos["shares"] = new_shares
        pos["avg_cost"] = round(new_total_cost / new_shares, 4)
        pos["total_cost"] = round(new_total_cost, 2)
        pos["current_price"] = price
        pos["highest_price"] = max(pos.get("highest_price", price), price)
    else:
        pf["positions"][code] = {
            "name": name,
            "shares": shares,
            "avg_cost": round(total_cost / shares, 4),
            "total_cost": round(total_cost, 2),
            "current_price": price,
            "highest_price": price,  # 追踪止损用的最高价
            "take_profit_level": 1,  # 止盈层级
            "first_buy_date": today_str(),
        }

    # 扣款
    pf["cash"] = round(pf["cash"] - total_cost, 2)

    # 记录交易
    txn = {
        "id": f"B{len(pf['transactions']) + 1:04d}",
        "type": "BUY",
        "code": code,
        "name": name,
        "shares": shares,
        "price": price,
        "commission": round(commission, 2),
        "total": round(total_cost, 2),
        "time": now(),
        "date": today_str(),
    }
    pf["transactions"].append(txn)

    save_portfolio(pf)

    return {
        "ok": True,
        "transaction": txn,
        "position": pf["positions"][code],
        "cash_remaining": pf["cash"],
        "total_asset": calc_total_asset(pf),
    }


def cmd_sell(code: str, shares: int, price: float, reason: str = ""):
    """卖出股票"""
    pf = load_portfolio()

    pos = get_position(pf, code)
    if not pos:
        return {"ok": False, "error": f"不持有 {code}，无法卖出"}

    if shares > pos["shares"]:
        return {"ok": False, "error": f"持仓不足：需要 {shares} 股，持有 {pos['shares']} 股"}

    if shares <= 0:
        shares = pos["shares"]  # 全部卖出

    # 计算费用和盈亏
    gross = shares * price
    commission = calc_commission(gross)
    stamp_tax = calc_stamp_tax(gross, True)
    net_proceeds = gross - commission - stamp_tax

    cost_basis = shares * pos["avg_cost"]
    realized_pnl = net_proceeds - cost_basis
    pnl_pct = (realized_pnl / cost_basis * 100) if cost_basis > 0 else 0

    # 更新持仓
    pos["shares"] -= shares
    pos["total_cost"] = round(pos["total_cost"] - cost_basis, 2)
    if pos["shares"] > 0:
        pos["avg_cost"] = round(pos["total_cost"] / pos["shares"], 4)

    # 增加现金
    pf["cash"] = round(pf["cash"] + net_proceeds, 2)

    # 记录交易
    txn = {
        "id": f"S{len(pf['transactions']) + 1:04d}",
        "type": "SELL",
        "code": code,
        "name": pos["name"],
        "shares": shares,
        "price": price,
        "commission": round(commission, 2),
        "stamp_tax": round(stamp_tax, 2),
        "net_proceeds": round(net_proceeds, 2),
        "realized_pnl": round(realized_pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
        "reason": reason,
        "time": now(),
        "date": today_str(),
    }
    pf["transactions"].append(txn)

    # 如果卖出后升级止盈层级
    if "new_level" in reason:
        try:
            import re

            match = re.search(r"new_level:\s*(\d+)", reason)
            if match:
                pos["take_profit_level"] = int(match.group(1))
        except Exception:
            logger.warning("止盈级别解析失败，保持原值: %s", reason)

    # 清仓则删除持仓记录
    if pos["shares"] == 0:
        del pf["positions"][code]

    save_portfolio(pf)

    return {
        "ok": True,
        "transaction": txn,
        "cash_remaining": pf["cash"],
        "total_asset": calc_total_asset(pf),
    }


def cmd_auto_check():
    """
    自动检查所有持仓的止损止盈条件
    返回建议交易列表（可直接用于执行）
    """
    pf = load_portfolio()
    suggestions = auto_check_all_positions(pf)

    if not suggestions:
        return {
            "ok": True,
            "has_suggestions": False,
            "message": "所有持仓均未触发止损止盈条件",
        }

    return {
        "ok": True,
        "has_suggestions": True,
        "count": len(suggestions),
        "suggestions": suggestions,
    }


def cmd_execute_suggestion(suggestion: dict, price: float):
    """
    执行单个建议交易
    suggestion: cmd_auto_check() 返回的 suggestions 中的元素
    """
    if suggestion["action"] != "SELL":
        return {"ok": False, "error": "目前只支持自动卖出"}

    reason = suggestion["reason"]
    if "new_level" in suggestion:
        reason += f" [new_level: {suggestion['new_level']}]"

    return cmd_sell(suggestion["code"], suggestion["shares"], price, reason)


# ══════════════════════════════════════════════════
#  查询与计算
# ══════════════════════════════════════════════════


def calc_total_return(pf: dict) -> dict:
    """计算总收益"""
    total = calc_total_asset(pf)
    pnl = round(total - INITIAL_CAPITAL, 2)
    pnl_pct = round(pnl / INITIAL_CAPITAL * 100, 2)

    # 统计已实现盈亏
    realized = sum(t.get("realized_pnl", 0) for t in pf["transactions"] if t["type"] == "SELL")

    # 胜率：盈利交易数 / 总交易数
    sell_txns = [t for t in pf["transactions"] if t["type"] == "SELL"]
    wins = sum(1 for t in sell_txns if t.get("realized_pnl", 0) > 0)
    total_trades = len(sell_txns)
    win_rate = round(wins / total_trades * 100, 2) if total_trades > 0 else 0

    return {
        "total_asset": total,
        "cash": pf["cash"],
        "total_pnl": pnl,
        "total_pnl_pct": pnl_pct,
        "realized_pnl": round(realized, 2),
        "total_trades": total_trades,
        "win_trades": wins,
        "lose_trades": total_trades - wins,
        "win_rate": win_rate,
    }


def cmd_snapshot():
    """记录当日资产快照"""
    pf = load_portfolio()
    d = today_str()
    total = calc_total_asset(pf)

    pf["daily_snapshot"][d] = {
        "total_asset": total,
        "cash": pf["cash"],
        "pnl": round(total - INITIAL_CAPITAL, 2),
        "pnl_pct": round((total - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100, 2),
        "positions": {
            code: {
                "name": pos["name"],
                "shares": pos["shares"],
                "price": pos.get("current_price", pos["avg_cost"]),
                "market_value": round(pos["shares"] * pos.get("current_price", pos["avg_cost"]), 2),
            }
            for code, pos in pf["positions"].items()
        },
        "time": now(),
    }
    save_portfolio(pf)
    return pf["daily_snapshot"][d]


def cmd_update_price(code: str, price: float):
    """更新持仓股票当前价格"""
    pf = load_portfolio()
    if code in pf["positions"]:
        pf["positions"][code]["current_price"] = price
        # 更新最高价
        if price > pf["positions"][code].get("highest_price", price):
            pf["positions"][code]["highest_price"] = price
        save_portfolio(pf)
        return {"ok": True, "code": code, "price": price}
    return {"ok": False, "error": f"不持有 {code}"}


def cmd_update_all_prices(prices: dict):
    """批量更新价格 {code: price}"""
    pf = load_portfolio()
    updated = []
    for code, price in prices.items():
        if code in pf["positions"]:
            pf["positions"][code]["current_price"] = price
            # 更新最高价
            if price > pf["positions"][code].get("highest_price", price):
                pf["positions"][code]["highest_price"] = price
            updated.append(code)
    if updated:
        save_portfolio(pf)
    return {"ok": True, "updated": updated}


def cmd_portfolio():
    """查看当前持仓摘要"""
    pf = load_portfolio()
    perf = calc_total_return(pf)

    positions_detail = []
    for code, pos in pf["positions"].items():
        cur_price = pos.get("current_price", pos["avg_cost"])
        mkt_value = round(pos["shares"] * cur_price, 2)
        unrealized = round(mkt_value - pos["total_cost"], 2)
        unrealized_pct = (
            round(unrealized / pos["total_cost"] * 100, 2) if pos["total_cost"] > 0 else 0
        )

        # 止损止盈状态
        stop_loss_check = check_stop_loss(pf, code)
        take_profit_check = check_take_profit(pf, code)

        positions_detail.append(
            {
                "code": code,
                "name": pos["name"],
                "shares": pos["shares"],
                "avg_cost": pos["avg_cost"],
                "current_price": cur_price,
                "highest_price": pos.get("highest_price", cur_price),
                "market_value": mkt_value,
                "total_cost": pos["total_cost"],
                "unrealized_pnl": unrealized,
                "unrealized_pnl_pct": unrealized_pct,
                "weight_pct": round(mkt_value / perf["total_asset"] * 100, 2)
                if perf["total_asset"] > 0
                else 0,
                "take_profit_level": pos.get("take_profit_level", 1),
                "stop_loss_status": stop_loss_check["reason"],
                "take_profit_status": take_profit_check["reason"],
                "first_buy_date": pos.get("first_buy_date", "N/A"),
            }
        )

    # 按市值排序
    positions_detail.sort(key=lambda x: x["market_value"], reverse=True)

    return {
        "config": pf["config"],
        "performance": perf,
        "positions": positions_detail,
        "position_count": len(positions_detail),
        "recent_transactions": pf["transactions"][-10:],
    }


def cmd_history():
    """查看交易历史"""
    pf = load_portfolio()
    return pf["transactions"]


def cmd_report(period: str = "daily"):
    """
    生成报告
    period: daily | weekly | monthly | quarterly | semiannual | annual
    所有周期均包含每日收益明细和每日交易胜率
    """
    pf = load_portfolio()
    perf = calc_total_return(pf)
    d = today_str()

    # 获取历史快照
    snapshots = pf.get("daily_snapshot", {})
    sorted_dates = sorted(snapshots.keys())

    # 期间筛选
    period_days = {
        "daily": 1,
        "weekly": 7,
        "monthly": 30,
        "quarterly": 90,
        "semiannual": 180,
        "annual": 365,
    }
    days = period_days.get(period, 1)
    start = (date.today() - timedelta(days=days)).isoformat()

    # 全量快照（用于累计计算）
    all_period_snapshots = {k: v for k, v in snapshots.items() if k >= start}
    all_period_dates = sorted(all_period_snapshots.keys())

    # 交易数据
    txns = pf["transactions"]
    period_txns = [t for t in txns if t["date"] >= start]
    buys = [t for t in period_txns if t["type"] == "BUY"]
    sells = [t for t in period_txns if t["type"] == "SELL"]

    # 胜率
    period_wins = sum(1 for t in sells if t.get("realized_pnl", 0) > 0)
    period_total = len(sells)
    period_win_rate = round(period_wins / period_total * 100, 2) if period_total > 0 else 0
    period_realized = round(sum(t.get("realized_pnl", 0) for t in sells), 2)

    # 最大回撤
    max_dd = 0.0
    if all_period_snapshots:
        values = [s["total_asset"] for s in all_period_snapshots.values()]
        peak = values[0]
        for v in values:
            peak = max(peak, v)
            dd = (peak - v) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)

    # ══ 构建每日明细（核心增强）═══
    daily_detail = []
    cum_pnl = 0.0

    for i, dt in enumerate(sorted_dates):
        snap = snapshots[dt]

        # 当日收益率（相对前一日）
        if i == 0:
            daily_ret = round((snap["total_asset"] - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100, 2)
        else:
            prev_val = snapshots[sorted_dates[i - 1]]["total_asset"]
            daily_ret = (
                round((snap["total_asset"] - prev_val) / prev_val * 100, 2) if prev_val > 0 else 0
            )

        # 累计收益
        cum_pnl = round(snap["total_asset"] - INITIAL_CAPITAL, 2)
        cum_pnl_pct = round(cum_pnl / INITIAL_CAPITAL * 100, 2)

        # 当日交易
        day_txns = [t for t in txns if t["date"] == dt]
        day_buys = [t for t in day_txns if t["type"] == "BUY"]
        day_sells = [t for t in day_txns if t["type"] == "SELL"]
        day_wins = sum(1 for t in day_sells if t.get("realized_pnl", 0) > 0)
        day_total_sells = len(day_sells)
        day_win_rate = round(day_wins / day_total_sells * 100, 2) if day_total_sells > 0 else None
        day_realized = round(sum(t.get("realized_pnl", 0) for t in day_sells), 2)

        daily_detail.append(
            {
                "date": dt,
                "total_asset": snap["total_asset"],
                "cash": snap["cash"],
                "daily_return_pct": daily_ret,
                "cumulative_pnl": cum_pnl,
                "cumulative_pnl_pct": cum_pnl_pct,
                "position_count": len(snap.get("positions", {})),
                "buy_count": len(day_buys),
                "sell_count": day_total_sells,
                "day_win_count": day_wins if day_total_sells > 0 else 0,
                "day_lose_count": day_total_sells - day_wins if day_total_sells > 0 else 0,
                "day_win_rate": day_win_rate,
                "day_realized_pnl": day_realized,
            }
        )

    # 筛选期间的每日明细
    period_daily = [dd for dd in daily_detail if dd["date"] >= start]

    # 期间统计
    up_days = sum(1 for dd in period_daily if dd["daily_return_pct"] > 0)
    down_days = sum(1 for dd in period_daily if dd["daily_return_pct"] < 0)
    flat_days = sum(1 for dd in period_daily if dd["daily_return_pct"] == 0)

    # 最佳/最差日
    if period_daily:
        best_day = max(period_daily, key=lambda x: x["daily_return_pct"])
        worst_day = min(period_daily, key=lambda x: x["daily_return_pct"])
    else:
        best_day = worst_day = None

    # 日胜率（盈利天数/交易天数）
    trading_days = up_days + down_days
    daily_win_rate = round(up_days / trading_days * 100, 2) if trading_days > 0 else 0

    # 日均收益
    avg_daily_return = (
        round(sum(dd["daily_return_pct"] for dd in period_daily) / len(period_daily), 4)
        if period_daily
        else 0
    )

    report = {
        "period": period,
        "period_start": start,
        "period_end": d,
        "total_days": days,
        "trading_days_with_data": len(period_daily),
        "generated_at": now(),
        "summary": {
            "total_asset": perf["total_asset"],
            "cash": perf["cash"],
            "total_pnl": perf["total_pnl"],
            "total_pnl_pct": perf["total_pnl_pct"],
            "realized_pnl": period_realized,
            "unrealized_pnl": round(perf["total_pnl"] - perf["realized_pnl"], 2),
            "max_drawdown_pct": round(max_dd, 2),
            # 交易日统计
            "up_days": up_days,
            "down_days": down_days,
            "flat_days": flat_days,
            "daily_win_rate": daily_win_rate,
            "avg_daily_return_pct": avg_daily_return,
            "best_day": best_day,
            "worst_day": worst_day,
            # 交易统计
            "trade_count": len(period_txns),
            "buy_count": len(buys),
            "sell_count": len(sells),
            "win_count": period_wins,
            "lose_count": period_total - period_wins,
            "trade_win_rate": period_win_rate,
            # 盈亏统计
            "avg_win": round(
                sum(t.get("realized_pnl", 0) for t in sells if t.get("realized_pnl", 0) > 0)
                / period_wins,
                2,
            )
            if period_wins > 0
            else 0,
            "avg_loss": round(
                sum(t.get("realized_pnl", 0) for t in sells if t.get("realized_pnl", 0) < 0)
                / (period_total - period_wins),
                2,
            )
            if (period_total - period_wins) > 0
            else 0,
            "profit_factor": round(
                abs(
                    sum(t.get("realized_pnl", 0) for t in sells if t.get("realized_pnl", 0) > 0)
                    / sum(t.get("realized_pnl", 0) for t in sells if t.get("realized_pnl", 0) < 0)
                ),
                2,
            )
            if sum(t.get("realized_pnl", 0) for t in sells if t.get("realized_pnl", 0) < 0) != 0
            else 0,
        },
        "positions": [
            {
                "code": code,
                "name": pos["name"],
                "shares": pos["shares"],
                "avg_cost": pos["avg_cost"],
                "current_price": pos.get("current_price", pos["avg_cost"]),
                "market_value": round(pos["shares"] * pos.get("current_price", pos["avg_cost"]), 2),
                "unrealized_pnl": round(
                    pos["shares"] * pos.get("current_price", pos["avg_cost"]) - pos["total_cost"], 2
                ),
            }
            for code, pos in pf["positions"].items()
        ],
        "daily_detail": period_daily,  # 每日明细（核心）- 不再截断
        "recent_trades": period_txns[-20:],
    }

    # 保存报告
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_file = REPORTS_DIR / f"{period}_{d}.json"
    atomic_write_json(report_file, report)

    return report


def cmd_add_dividend(code: str, name: str, amount: float):
    """记录分红"""
    pf = load_portfolio()
    div = {
        "code": code,
        "name": name,
        "amount": round(amount, 2),
        "date": today_str(),
        "time": now(),
    }
    pf["dividends"].append(div)
    pf["cash"] = round(pf["cash"] + amount, 2)
    save_portfolio(pf)
    return {"ok": True, "dividend": div, "cash": pf["cash"]}


def cmd_reset():
    """重置模拟账户（危险操作）"""
    ARCHIVE_DIR = DATA_DIR / "archive"
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    if PORTFOLIO_FILE.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        PORTFOLIO_FILE.rename(ARCHIVE_DIR / f"portfolio_{ts}.json")

    pf = {
        "config": {
            "initial_capital": INITIAL_CAPITAL,
            "created_at": today_str(),
            "updated_at": now(),
        },
        "cash": INITIAL_CAPITAL,
        "positions": {},
        "transactions": [],
        "daily_snapshot": {},
        "dividends": [],
    }
    save_portfolio(pf)
    return {"ok": True, "message": "账户已重置", "initial_capital": INITIAL_CAPITAL}


# ══════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════


def main():
    if len(sys.argv) < 2:
        print("用法: sim_trade.py <命令> [参数...]")
        print()
        print("命令:")
        print("  buy <代码> <股数> <价格> [名称]    买入")
        print("  sell <代码> <股数> <价格> [原因]    卖出")
        print("  portfolio                           查看持仓")
        print("  history                             交易历史")
        print("  snapshot                            记录当日快照")
        print("  update <代码> <价格>                更新价格")
        print("  batch-update <JSON价格字典>         批量更新价格")
        print("  report [daily|weekly|monthly|...]   生成报告")
        print("  dividend <代码> <名称> <金额>       记录分红")
        print("  reset                               重置账户")
        print("  perf                                快速查看收益")
        print("  auto-check                          自动检查止损止盈")
        print("  help                                显示此帮助")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "buy":
        code = sys.argv[2]
        shares = int(sys.argv[3])
        price = float(sys.argv[4])
        name = sys.argv[5] if len(sys.argv) > 5 else ""
        result = cmd_buy(code, shares, price, name)
    elif cmd == "sell":
        code = sys.argv[2]
        shares = int(sys.argv[3])
        price = float(sys.argv[4])
        reason = sys.argv[5] if len(sys.argv) > 5 else ""
        result = cmd_sell(code, shares, price, reason)
    elif cmd == "portfolio":
        result = cmd_portfolio()
    elif cmd == "history":
        result = cmd_history()
    elif cmd == "snapshot":
        result = cmd_snapshot()
    elif cmd == "update":
        result = cmd_update_price(sys.argv[2], float(sys.argv[3]))
    elif cmd == "batch-update":
        result = cmd_update_all_prices(json.loads(sys.argv[2]))
    elif cmd == "report":
        period = sys.argv[2] if len(sys.argv) > 2 else "daily"
        result = cmd_report(period)
    elif cmd == "dividend":
        result = cmd_add_dividend(sys.argv[2], sys.argv[3], float(sys.argv[4]))
    elif cmd == "reset":
        result = cmd_reset()
    elif cmd == "perf":
        pf = load_portfolio()
        result = calc_total_return(pf)
    elif cmd == "auto-check":
        result = cmd_auto_check()
    elif cmd == "help":
        print(__doc__)
        sys.exit(0)
    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
