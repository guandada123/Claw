#!/usr/bin/env python3
"""
📊 投顾操盘 — 策略信号选股顾问 v1.0

读取 Quant 信号管线输出的 COMBO/VWM/BBR/ADX 策略信号，
结合当前持仓、风控约束、现金水位，输出结构化买入建议给 AI 自动化调用。

用法:
  python3 sim_signal_advisor.py                                 # 默认输出
  python3 sim_signal_advisor.py --debug                          # 详细日志
  python3 sim_signal_advisor.py --threshold 0.3                  # 调低信心阈值
  python3 sim_signal_advisor.py --push                           # 飞书推送建议
"""

import json
import logging
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
from error_handler import atomic_write_json

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("sim_signal_advisor")

DATA_DIR = Path(__file__).parent.parent / "data" / "simulation"
OUTPUT_DIR = Path(__file__).parent.parent.parent / "output"
PORTFOLIO_FILE = DATA_DIR / "portfolio.json"

# ============================================================
# 配置
# ============================================================

# 策略信号信心阈值（COMBO 综合得分，范围 -1~1）
BUY_THRESHOLD = 0.2      # 买入信号阈值
STRONG_BUY_THRESHOLD = 0.4  # 强烈买入阈值

# 风险参数
MAX_POSITIONS = 3         # 最大持仓数
MAX_SINGLE_PCT = 0.50     # 单只最大仓位50%
MAX_SECTOR_PCT = 0.40     # 同行业最大仓位40%
MIN_CASH_RESERVE = 3000   # 最低现金保留 ¥3,000
PORTFOLIO_TARGET = 39000  # 月度目标 30% = ¥39,000
TOTAL_CAPITAL = 30000     # 初始本金

# 行业映射（简化版）
INDUSTRY_MAP = {
    "002049": "科技/半导体", "600206": "科技/半导体", "688981": "科技/半导体",
    "600570": "科技/半导体", "600498": "通信/电子", "000725": "通信/电子",
    "600522": "通信/电子", "002415": "通信/电子", "002230": "AI/科技",
    "000001": "金融", "601318": "金融", "600036": "金融",
    "000333": "消费", "600519": "消费", "000858": "消费", "600887": "消费",
    "600276": "医药",
    "002601": "周期/资源", "600585": "建材", "600893": "军工/制造",
    "601899": "周期/资源", "300750": "新能源/制造",
}

# 股票名称映射
STOCK_NAMES = {
    "002049": "紫光国微", "600498": "烽火通信", "000725": "京东方A",
    "600522": "中天科技", "002601": "龙佰集团", "600206": "有研新材",
    "000001": "平安银行", "000333": "美的集团", "002415": "海康威视",
    "600519": "贵州茅台", "601318": "中国平安", "000858": "五粮液",
    "600036": "招商银行", "600276": "恒瑞医药", "600887": "伊利股份",
    "600570": "恒生电子", "600585": "海螺水泥", "600893": "航发动力",
    "601899": "紫金矿业", "002230": "科大讯飞",
    "300750": "宁德时代", "688981": "中芯国际",
}

# 受限板块前缀
RESTRICTED_PREFIXES = ["300", "301", "688", "689", "8", "4"]

# 策略库路径
STRATEGY_LIBRARY_FILE = DATA_DIR / "strategy_library.json"
DECISION_LOG_FILE = DATA_DIR / "decision_log.json"
CROSS_PORTFOLIO_MONITOR_OUTPUT = OUTPUT_DIR / "cross_portfolio_latest.json"


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str():
    return date.today().isoformat()


# ============================================================
# 策略库集成（v2.1 新增）
# ============================================================

def load_strategy_library() -> dict:
    """读取策略库"""
    if STRATEGY_LIBRARY_FILE.exists():
        return json.loads(STRATEGY_LIBRARY_FILE.read_text())
    return {"loss_patterns_to_avoid": [], "high_win_rate_patterns": [],
            "risk_control_rules": {}, "scoring_thresholds": {}}


def load_decision_log() -> list:
    """读取决策日志"""
    if DECISION_LOG_FILE.exists():
        return json.loads(DECISION_LOG_FILE.read_text())
    return []


def load_cross_portfolio_monitor() -> dict | None:
    """读取跨盘风控输出"""
    if CROSS_PORTFOLIO_MONITOR_OUTPUT.exists():
        try:
            return json.loads(CROSS_PORTFOLIO_MONITOR_OUTPUT.read_text())
        except Exception:
            pass
    return None


def check_strategy_library_patterns(code: str, industry: str, library: dict) -> list[str]:
    """
    检查策略库拦截规则
    返回拦截理由列表（空 = 通过）
    """
    blocks = []
    rules = library.get("loss_patterns_to_avoid", [])

    for rule in rules:
        rid = rule.get("id", "")
        desc = rule.get("description", "")

        if rid == "L001" and industry == "科技/半导体":
            # L001: 单行业高集中度 → 买入前确保不会超限
            blocks.append(f"策略库L001拦截：{industry}行业集中度风险")
        if rid == "L002":
            # L002: 同日密集建仓 → 检查当天是否已建仓
            blocks.append("策略库L002拦截：单日最多新开1只仓位")
        if rid == "L003" and industry in ("科技/半导体", "消费电子"):
            blocks.append(f"策略库L003拦截：{industry}可能处于下行趋势")

    return blocks


def check_cross_portfolio_concentration(code: str, industry: str, positions: dict) -> list[str]:
    """
    检查跨盘合并集中度
    读取 cross_portfolio_monitor 输出，如果合并后超限则拦截
    """
    blocks = []
    cp = load_cross_portfolio_monitor()
    if not cp:
        return blocks

    concentration = cp.get("industry_concentration", {})
    chain_risk = cp.get("chain_risk", {})

    # 检查该行业在合并后是否超限
    for ind_name, ind_data in concentration.items():
        if industry in ind_name or ind_name in industry:
            pct = ind_data.get("pct", 0)
            if pct > 40:
                blocks.append(f"🚨 跨盘合并后{ind_name}集中度{pct}%（上限40%）禁止新增")
            break

    return blocks


# ============================================================
# 决策反馈（v2.1 新增）
# ============================================================

def auto_feedback_decision(trade_type: str, code: str, name: str,
                           price: float, shares: int, reason: str,
                           signal_score: float = 0, strategy_tag: str = "combo_signal"):
    """
    自动记录决策到 decision_log.json
    供 AI 自动化在买入/卖出后调用
    """
    log = load_decision_log()
    entry = {
        "timestamp": now(),
        "date": today_str(),
        "trade_type": trade_type,  # BUY / SELL
        "code": code,
        "name": name,
        "price": price,
        "shares": shares,
        "reason": reason,
        "signal_score": signal_score,
        "strategy_tag": strategy_tag,  # combo_signal / event_driven / trend_follow / mean_reversion / rebalance
    }

    if trade_type == "BUY":
        entry["status"] = "open"
    elif trade_type == "SELL":
        # 找到对应买入记录，更新卖出信息
        for prev in log:
            if prev.get("code") == code and prev.get("trade_type") == "BUY" and prev.get("status") == "open":
                prev["status"] = "closed"
                prev["close_price"] = price
                prev["close_reason"] = reason
                prev["close_date"] = today_str()
                prev["hold_days"] = (date.today() - date.fromisoformat(prev.get("date", today_str()))).days if prev.get("date") else 0
                prev["realized_pnl"] = round((price - prev["price"]) * min(shares, prev["shares"]), 2)
                prev["realized_pnl_pct"] = round((price - prev["price"]) / prev["price"] * 100, 2)
                break

    log.append(entry)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # 只保留最近200条
    if len(log) > 200:
        log = log[-200:]
    atomic_write_json(DECISION_LOG_FILE, log)
    return entry


def cmd_decision_feedback() -> dict:
    """
    分析 decision_log.json 中的已平仓交易，
    按策略标签统计胜率和平均盈亏 → 写入 strategy_library 的 weekly_performance
    """
    log = load_decision_log()
    library = load_strategy_library()

    # 统计已平仓交易
    closed_trades = [t for t in log if t.get("status") == "closed"]
    if not closed_trades:
        return {"ok": True, "message": "无已平仓交易", "total_closed": 0}

    # 按策略标签分组
    by_tag = {}
    for t in closed_trades:
        tag = t.get("strategy_tag", "unknown")
        if tag not in by_tag:
            by_tag[tag] = {"trades": [], "wins": 0, "losses": 0, "total_pnl": 0}
        by_tag[tag]["trades"].append(t)
        pnl = t.get("realized_pnl", 0)
        by_tag[tag]["total_pnl"] += pnl
        if pnl > 0:
            by_tag[tag]["wins"] += 1
        else:
            by_tag[tag]["losses"] += 1

    # 构建性能摘要
    perf = {}
    for tag, data in by_tag.items():
        total = len(data["trades"])
        perf[tag] = {
            "total_trades": total,
            "wins": data["wins"],
            "losses": data["losses"],
            "win_rate": round(data["wins"] / total * 100, 1) if total > 0 else 0,
            "total_pnl": round(data["total_pnl"], 2),
            "avg_pnl_per_trade": round(data["total_pnl"] / total, 2) if total > 0 else 0,
        }

    # 识别高胜率模式
    high_win_patterns = []
    loss_patterns = []
    for tag, data in perf.items():
        if data["total_trades"] >= 2 and data["win_rate"] >= 60:
            high_win_patterns.append({
                "strategy_tag": tag,
                "win_rate": data["win_rate"],
                "total_trades": data["total_trades"],
                "avg_pnl": data["avg_pnl_per_trade"],
                "source": "decision_log_auto",
            })
        if data["total_trades"] >= 2 and data["win_rate"] <= 30:
            loss_patterns.append({
                "strategy_tag": tag,
                "win_rate": data["win_rate"],
                "total_trades": data["total_trades"],
                "avg_pnl": data["avg_pnl_per_trade"],
                "source": "decision_log_auto",
            })

    # 写入 strategy_library
    library["high_win_rate_patterns"] = high_win_patterns
    library["weekly_performance"] = library.get("weekly_performance", {})
    library["weekly_performance"][today_str()[:7]] = {
        "period": today_str(),
        "tag_performance": perf,
        "total_closed_trades": len(closed_trades),
        "overall_win_rate": round(
            sum(1 for t in closed_trades if t.get("realized_pnl", 0) > 0) / len(closed_trades) * 100, 1
        ) if closed_trades else 0,
    }
    library["updated_at"] = now()
    library["updated_by"] = "signal_advisor_auto"
    atomic_write_json(STRATEGY_LIBRARY_FILE, library)

    return {
        "ok": True,
        "total_closed": len(closed_trades),
        "tag_performance": perf,
        "new_patterns": len(high_win_patterns) + len(loss_patterns),
        "message": f"已沉淀 {len(closed_trades)} 笔交易到策略库",
    }


# ============================================================
# 1. 读取信号数据
# ============================================================

def get_latest_signals_from_quant() -> dict | None:
    """
    通过 docker exec 调用 Quant 管线获取最新策略信号
    如果 Docker 不可用，回退到读取本地缓存的信号文件
    """
    try:
        # 尝试在容器内运行管线，获取最新信号
        result = subprocess.run(
            [
                "docker", "exec", "quant-strategy", "python3",
                "/app/scripts/live_pipeline.py",
                "--output", "/tmp/live_signals_advisor.json",
            ],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            # 复制回来
            local_path = OUTPUT_DIR / "live_signals_advisor_latest.json"
            subprocess.run(
                ["docker", "cp", "quant-strategy:/tmp/live_signals_advisor.json",
                 str(local_path)],
                capture_output=True, timeout=10,
            )
            if local_path.exists():
                data = json.loads(local_path.read_text())
                logger.info(f"Quant 信号已获取: {data.get('summary', {})}")
                return data
    except Exception as e:
        logger.warning(f"Quant 管线调用失败: {e}")

    # 回退：读取本地最新信号缓存
    candidates = sorted(OUTPUT_DIR.glob("live_signals_*.json"))
    if candidates:
        latest = candidates[-1]
        logger.info(f"回退读取本地信号: {latest.name}")
        return json.loads(latest.read_text())

    logger.warning("无可用信号数据")
    return None


def run_quant_signal_for_stock(ts_code: str) -> dict | None:
    """对单只股票运行 Quant 信号分析"""
    try:
        result = subprocess.run(
            [
                "docker", "exec", "quant-strategy", "python3",
                "-c", f"""
import sys, json
sys.path.insert(0, '/app')
from services.backtest_engine_v2 import EnhancedBacktestEngine, BacktestConfig
from services.signals import generate_signals

ts_code = '{ts_code}'
c = BacktestConfig(ts_codes=[ts_code], strategies=['combo-vwm-bbr'],
                    start_date='20250601', end_date='20260617')
r = EnhancedBacktestEngine(c).run()

# 获取最近信号
signals = generate_signals(ts_code, 'combo-vwm-bbr')
print(json.dumps({{
    'ts_code': ts_code,
    'latest_signal': signals[-1] if signals else {{}},
    'total_return': r.total_return,
    'sharpe': r.sharpe_ratio,
    'max_dd': r.max_drawdown,
    'total_trades': r.total_trades,
    'win_rate': r.win_rate,
}}))
""",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return json.loads(result.stdout.strip())
    except Exception as e:
        logger.warning(f"Quant 单股信号失败 {ts_code}: {e}")
    return None


# ============================================================
# 2. 读取当前投顾持仓
# ============================================================

def load_portfolio() -> dict:
    """读取当前模拟盘持仓"""
    if PORTFOLIO_FILE.exists():
        return json.loads(PORTFOLIO_FILE.read_text())
    return {"cash": 30000, "positions": {}, "config": {"initial_capital": 30000}}


def get_industry_market_value(positions: dict) -> dict:
    """按行业统计总市值"""
    ind_val = {}
    for code, pos in positions.items():
        ind = INDUSTRY_MAP.get(code[:6], "其他")
        mv = pos["shares"] * pos.get("current_price", pos["avg_cost"])
        ind_val[ind] = ind_val.get(ind, 0) + mv
    return ind_val


def check_restricted(code: str) -> bool:
    """检查是否受限板块"""
    return any(code.startswith(prefix) for prefix in RESTRICTED_PREFIXES)


# ============================================================
# 3. 分析 & 建议
# ============================================================

def analyze_candidates(signals: dict, portfolio: dict) -> dict:
    """
    分析信号数据，结合持仓状态，输出建议
    """
    pf = portfolio
    positions = pf.get("positions", {})
    cash = pf.get("cash", 0)
    total_asset = cash + sum(
        pos["shares"] * pos.get("current_price", pos["avg_cost"])
        for pos in positions.values()
    )
    held_codes = set(positions.keys())
    position_count = len(positions)

    # 行业分布
    sector_values = get_industry_market_value(positions)

    # 解析信号
    buy_signals = signals.get("buy", [])
    sell_signals = signals.get("sell", [])
    hold_signals = signals.get("hold", [])
    all_signals = signals.get("all", [])

    # 候选买入池
    candidates = []
    for s in buy_signals:
        code_full = s.get("ts_code", "")
        code = code_full.split(".")[0]

        # 排除受限板块
        if check_restricted(code):
            continue

        # 排除已持仓
        if code in held_codes:
            continue

        # 获取信心分
        confidence = s.get("combo_confidence", s.get("signal_strength", 0))
        if isinstance(confidence, str):
            try:
                confidence = float(confidence)
            except (ValueError, TypeError):
                confidence = 0

        # 获取信号细分
        vwm_signal = s.get("vwm_signal", 0)
        bbr_signal = s.get("bbr_signal", 0)
        adx_signal = s.get("adx_signal", 0)

        candidates.append({
            "code": code,
            "ts_code": code_full,
            "name": STOCK_NAMES.get(code, code),
            "industry": INDUSTRY_MAP.get(code, "其他"),
            "combo_confidence": round(confidence, 3),
            "vwm_signal": round(vwm_signal, 3) if isinstance(vwm_signal, (int, float)) else vwm_signal,
            "bbr_signal": round(bbr_signal, 3) if isinstance(bbr_signal, (int, float)) else bbr_signal,
            "adx_signal": round(adx_signal, 3) if isinstance(adx_signal, (int, float)) else adx_signal,
            "current_price": s.get("current_price", 0),
            "current_price_change": s.get("current_price_change", 0),
            "volume_ratio": s.get("volume_ratio", 0),
            "signal_type": s.get("signal_type", "BUY"),
        })

    # 按 COMBO 信心排序（降序）
    candidates.sort(key=lambda x: x["combo_confidence"], reverse=True)

    # 过滤：只保留 >= BUY_THRESHOLD
    candidates = [c for c in candidates if c["combo_confidence"] >= BUY_THRESHOLD]

    # 行业分散检查 + 策略库检查（v2.1 新增）
    buyable = []
    sector_load = dict(sector_values)  # 当前各行业市值
    total_mv = sum(sector_load.values())

    # 加载策略库和跨盘数据
    library = load_strategy_library()
    cross_portfolio_cp = load_cross_portfolio_monitor()

    for c in candidates:
        if len(buyable) >= (MAX_POSITIONS - position_count):
            break

        # 检查行业集中度
        cur_sector_pct = sector_load.get(c["industry"], 0) / total_asset if total_asset > 0 else 0
        if cur_sector_pct >= MAX_SECTOR_PCT:
            logger.info(f"  ⛔ {c['name']} 行业 '{c['industry']}' 已达上限 {MAX_SECTOR_PCT*100:.0f}%")
            c["skip_reason"] = f"行业集中度上限 {MAX_SECTOR_PCT*100:.0f}%"
            continue

        # 策略库拦截检查（v2.1 新增）
        library_blocks = check_strategy_library_patterns(c["code"], c["industry"], library)
        if library_blocks:
            c["skip_reason"] = "; ".join(library_blocks)
            c["library_blocks"] = library_blocks
            logger.info(f"  ⛔ {c['name']} 策略库拦截: {library_blocks}")
            continue

        # 跨盘合并集中度检查（v2.1 新增）
        cross_blocks = check_cross_portfolio_concentration(c["code"], c["industry"], positions)
        if cross_blocks:
            c["skip_reason"] = "; ".join(cross_blocks)
            c["cross_portfolio_blocks"] = cross_blocks
            logger.info(f"  ⛔ {c['name']} 跨盘风控拦截: {cross_blocks}")
            continue

        # 计算建议仓位
        max_budget = total_asset * MAX_SINGLE_PCT
        available = cash - MIN_CASH_RESERVE
        suggested_amount = min(max_budget, available)

        # 至少能买一手（100股）
        if c["current_price"] and suggested_amount >= c["current_price"] * 100:
            suggested_shares = int(suggested_amount / c["current_price"] / 100) * 100
            suggested_shares = max(suggested_shares, 100)
            c["suggested_shares"] = suggested_shares
            c["suggested_amount"] = round(suggested_shares * c["current_price"], 2)
        else:
            c["skip_reason"] = "资金不足买一手"
            c["suggested_shares"] = 0
            c["suggested_amount"] = 0

        buyable.append(c)

    # 卖出信号分析
    sell_analysis = []
    for s in sell_signals:
        code_full = s.get("ts_code", "")
        code = code_full.split(".")[0]
        if code in held_codes:
            confidence = s.get("combo_confidence", s.get("signal_strength", 0))
            if isinstance(confidence, str):
                try:
                    confidence = float(confidence)
                except (ValueError, TypeError):
                    confidence = 0
            sell_analysis.append({
                "code": code,
                "name": STOCK_NAMES.get(code, code),
                "combo_signal": round(confidence, 3),
                "reason": s.get("signal_type", "SELL"),
                "current_price": s.get("current_price", 0),
            })

    # 集中度预警
    concentration_alerts = []
    for ind, mv in sector_values.items():
        pct = mv / total_asset * 100 if total_asset > 0 else 0
        if pct > MAX_SECTOR_PCT * 100:
            concentration_alerts.append({
                "industry": ind,
                "market_value": round(mv, 2),
                "pct": round(pct, 1),
                "limit": MAX_SECTOR_PCT * 100,
                "status": "🚨 超限",
            })

    return {
        "timestamp": now(),
        "trade_date": today_str(),
        "summary": {
            "total_asset": round(total_asset, 2),
            "cash": round(cash, 2),
            "position_count": position_count,
            "available_slots": MAX_POSITIONS - position_count,
            "available_cash_for_buy": round(max(cash - MIN_CASH_RESERVE, 0), 2),
            "candidates_found": len(candidates),
            "buyable_count": len(buyable),
            "sell_signals": len(sell_analysis),
        },
        "signals": {
            "raw_buy": len(buy_signals),
            "raw_sell": len(sell_signals),
            "raw_hold": len(hold_signals),
        },
        "concentration_alerts": concentration_alerts,
        "buy_recommendations": buyable,
        "sell_alerts": sell_analysis,
        "current_positions": [
            {
                "code": code,
                "name": pos["name"],
                "shares": pos["shares"],
                "cost": pos["avg_cost"],
                "current_price": pos.get("current_price", pos["avg_cost"]),
                "pnl_pct": round(
                    (pos.get("current_price", pos["avg_cost"]) - pos["avg_cost"]) / pos["avg_cost"] * 100, 2
                ),
                "industry": INDUSTRY_MAP.get(code, "其他"),
            }
            for code, pos in positions.items()
        ],
    }


# ============================================================
# 4. 单股深度扫描（智能选股用）
# ============================================================

def scan_candidate_pool(codes: list[str]) -> list[dict]:
    """
    批量扫描候选股票池，获取每只的 COMBO 信号和历史表现
    用于 智能选股 自动化读取
    """
    results = []
    for code in codes:
        # 跳过受限板块
        if check_restricted(code):
            continue

        ts_code = f"{code}.SZ" if code.startswith(("0", "3")) else f"{code}.SH"

        # 尝试从 Quant 获取信号
        signal_data = run_quant_signal_for_stock(ts_code)
        if signal_data:
            results.append({
                "code": code,
                "name": STOCK_NAMES.get(code, code),
                "industry": INDUSTRY_MAP.get(code, "其他"),
                "ts_code": ts_code,
                "combo_total_return": signal_data.get("total_return", 0),
                "combo_sharpe": signal_data.get("sharpe", 0),
                "combo_max_drawdown": signal_data.get("max_dd", 0),
                "combo_total_trades": signal_data.get("total_trades", 0),
                "combo_win_rate": signal_data.get("win_rate", 0),
                "latest_signal": signal_data.get("latest_signal", {}).get("signal", "HOLD"),
            })
        else:
            # 回退：无信号
            results.append({
                "code": code,
                "name": STOCK_NAMES.get(code, code),
                "industry": INDUSTRY_MAP.get(code, "其他"),
                "ts_code": ts_code,
                "combo_total_return": 0,
                "combo_sharpe": 0,
                "combo_max_drawdown": 0,
                "combo_total_trades": 0,
                "combo_win_rate": 0,
                "latest_signal": "N/A",
                "error": "Quant 信号不可用",
            })

    # 按 COMBO 收益率排序
    results.sort(key=lambda x: x.get("combo_total_return", 0), reverse=True)
    return results


# ============================================================
# 5. 自动再平衡
# ============================================================

# 再平衡目标行业分布
TARGET_SECTOR_ALLOCATION = {
    "科技/半导体": 0.25,     # 目标 25%
    "通信/电子": 0.15,
    "金融": 0.15,
    "消费": 0.15,
    "周期/资源": 0.15,
    "新能源/制造": 0.10,
    "其他": 0.05,
}


def cmd_rebalance() -> dict:
    """
    自动再平衡分析：
    检测行业集中度超限 → 推荐减仓标的 → 推荐替代标的 → 输出完整换仓方案
    """
    portfolio = load_portfolio()
    positions = portfolio.get("positions", {})
    cash = portfolio.get("cash", 0)
    total_asset = cash + sum(
        pos["shares"] * pos.get("current_price", pos["avg_cost"])
        for pos in positions.values()
    )

    # 1. 当前行业分布
    sector_values = get_industry_market_value(positions)
    sector_pcts = {
        ind: mv / total_asset * 100 if total_asset > 0 else 0
        for ind, mv in sector_values.items()
    }

    # 2. 需要减仓的行业
    sell_candidates = []
    for ind, pct in sector_pcts.items():
        target = TARGET_SECTOR_ALLOCATION.get(ind, 0.05) * 100
        if pct > target:
            # 该行业超配 → 找该行业持仓中表现最差的建议减仓
            ind_positions = [
                (code, pos) for code, pos in positions.items()
                if INDUSTRY_MAP.get(code, "其他") == ind
            ]
            # 按 PnL 排序（最差优先）
            ind_positions.sort(
                key=lambda x: (
                    x[1].get("current_price", x[1]["avg_cost"]) - x[1]["avg_cost"]
                ) / x[1]["avg_cost"]
            )

            excess_pct = pct - target
            excess_value = excess_pct / 100 * total_asset

            for code, pos in ind_positions:
                mv = pos["shares"] * pos.get("current_price", pos["avg_cost"])
                sell_pct = min(mv, excess_value)
                if sell_pct <= 0:
                    continue

                pnl_pct = (
                    (pos.get("current_price", pos["avg_cost"]) - pos["avg_cost"])
                    / pos["avg_cost"] * 100
                )
                sell_shares = min(
                    int(sell_pct / pos.get("current_price", pos["avg_cost"]) / 100) * 100,
                    pos["shares"],
                )
                if sell_shares >= 100:
                    sell_candidates.append({
                        "code": code,
                        "name": pos["name"],
                        "industry": ind,
                        "current_price": pos.get("current_price", pos["avg_cost"]),
                        "current_shares": pos["shares"],
                        "suggested_sell_shares": sell_shares,
                        "suggested_sell_value": round(sell_shares * pos.get("current_price", pos["avg_cost"]), 2),
                        "pnl_pct": round(pnl_pct, 2),
                        "reason": f"行业 {ind} 超配 {pct:.1f}% 目标 {target:.0f}%，建议减持 {ind} 头寸",
                    })
                    excess_value -= sell_pct
                if excess_value <= 0:
                    break

    # 3. 推荐替代标的（从信号中找）
    rebalance_buys = []
    if sell_candidates:
        signals = get_latest_signals_from_quant()
        if signals:
            freed_cash = sum(s["suggested_sell_value"] for s in sell_candidates)
            buy_signals = signals.get("buy", [])
            # 找买入信号中不属于超配行业的
            for s in buy_signals:
                code_full = s.get("ts_code", "")
                code = code_full.split(".")[0]
                if check_restricted(code):
                    continue
                if code in positions:
                    continue

                ind = INDUSTRY_MAP.get(code, "其他")
                # 不推荐再加仓超配行业
                if sector_pcts.get(ind, 0) >= TARGET_SECTOR_ALLOCATION.get(ind, 0.05) * 100:
                    continue

                confidence = s.get("combo_confidence", s.get("signal_strength", 0))
                if isinstance(confidence, str):
                    try:
                        confidence = float(confidence)
                    except (ValueError, TypeError):
                        confidence = 0

                if confidence >= BUY_THRESHOLD:
                    price = s.get("current_price", 0)
                    if price and freed_cash >= price * 100:
                        shares = int(freed_cash / price / 100) * 100
                        shares = max(shares, 100)

                        # 检查该行业目标仓位
                        cur_ind_value = sector_values.get(ind, 0)
                        new_ind_value = cur_ind_value + shares * price
                        new_ind_pct = new_ind_value / total_asset * 100
                        if new_ind_pct <= TARGET_SECTOR_ALLOCATION.get(ind, 0.05) * 100 + 10:
                            rebalance_buys.append({
                                "code": code,
                                "name": STOCK_NAMES.get(code, code),
                                "industry": ind,
                                "combo_confidence": round(confidence, 3),
                                "suggested_buy_shares": shares,
                                "suggested_buy_value": round(shares * price, 2),
                                "current_price": price,
                            })
                            freed_cash -= shares * price
                            if freed_cash < price * 100:
                                break

    # 4. 汇总评分
    score = 100
    issues = []
    for ind, pct in sector_pcts.items():
        target = TARGET_SECTOR_ALLOCATION.get(ind, 0.05) * 100
        if pct > target * 1.5:
            score -= 20
            issues.append(f"⚡ {ind} {pct:.0f}%（目标{target:.0f}%）严重超配")
        elif pct > target:
            score -= 10
            issues.append(f"⚠️ {ind} {pct:.0f}%（目标{target:.0f}%）超配")

    return {
        "timestamp": now(),
        "trade_date": today_str(),
        "portfolio_health_score": max(score, 0),
        "issues": issues,
        "total_asset": round(total_asset, 2),
        "cash": round(cash, 2),
        "current_sector_allocation": {
            ind: {"pct": round(pct, 1), "target": TARGET_SECTOR_ALLOCATION.get(ind, 0.05) * 100}
            for ind, pct in sorted(sector_pcts.items(), key=lambda x: -x[1])
        },
        "sell_recommendations": sell_candidates,
        "buy_recommendations": rebalance_buys,
        "freed_cash_after_sells": round(
            cash + sum(s["suggested_sell_value"] for s in sell_candidates), 2
        ),
    }


# ============================================================
# 6. CLI
# ============================================================

def push_to_feishu(advice: dict):
    """推送选股建议到飞书"""
    summary = advice["summary"]
    msg = f"📈 [投顾操盘] 信号选股建议 {today_str()}\n"
    msg += f"总资产 ¥{summary['total_asset']:,.0f} | 现金 ¥{summary['cash']:,.0f}\n"
    msg += f"可用仓位 {summary['available_slots']} 个 | 可买资金 ¥{summary['available_cash_for_buy']:,.0f}\n"

    if advice["concentration_alerts"]:
        msg += "\n⚠️ 集中度预警:\n"
        for a in advice["concentration_alerts"]:
            msg += f"  {a['status']} {a['industry']} {a['pct']}%\n"

    if advice["buy_recommendations"]:
        msg += "\n🟢 买入建议:\n"
        for c in advice["buy_recommendations"]:
            msg += f"  {c['name']}({c['code']}) 信心{c['combo_confidence']:.2f} "
            msg += f"建议{c['suggested_shares']}股 ≈¥{c['suggested_amount']:,.0f}\n"

    if advice["sell_alerts"]:
        msg += "\n🔴 卖出预警:\n"
        for s in advice["sell_alerts"]:
            msg += f"  {s['name']}({s['code']}) {s['reason']}\n"

    msg += "\n---\n信号来源：COMBO组合策略"

    try:
        subprocess.run(
            ["lark-cli", "im", "message", "send",
             "--chat-id", "oc_9ee5303497f5e0e71666b610d6bdc346",
             "--content", msg,
             "--msg-type", "text", "--as", "bot"],
            capture_output=True, timeout=15,
        )
        logger.info("✅ 飞书推送完成")
    except Exception as e:
        logger.warning(f"飞书推送失败: {e}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="投顾操盘信号选股顾问")
    parser.add_argument("--debug", action="store_true", help="详细日志")
    parser.add_argument("--threshold", type=float, default=BUY_THRESHOLD, help="买入信心阈值")
    parser.add_argument("--push", action="store_true", help="飞书推送建议")
    parser.add_argument("--scan", nargs="+", help="扫描指定候选池，例如 --scan 002049 002601")
    parser.add_argument("--rebalance", action="store_true", help="自动再平衡分析")
    parser.add_argument("--feedback", action="store_true", help="决策日志→策略库自动沉淀")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.feedback:
        # 决策反馈模式
        result = cmd_decision_feedback()
        output_path = OUTPUT_DIR / "decision_feedback_latest.json"
        atomic_write_json(output_path, result)
        logger.info(f"决策反馈已保存: {output_path}")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.rebalance:
        # 再平衡模式
        result = cmd_rebalance()
        output_path = OUTPUT_DIR / f"rebalance_{today_str()}.json"
        atomic_write_json(output_path, result)
        logger.info(f"再平衡建议已保存: {output_path}")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.scan:
        # 扫描候选池模式
        results = scan_candidate_pool(args.scan)
        output = {"mode": "scan", "timestamp": now(), "candidates": results}
        output_path = OUTPUT_DIR / "signal_advisor_scan.json"
        atomic_write_json(output_path, output)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    # 标准模式：读取信号 → 分析持仓 → 输出建议
    signals = get_latest_signals_from_quant()
    if not signals:
        print(json.dumps({"ok": False, "error": "无法获取策略信号"}, ensure_ascii=False))
        sys.exit(1)

    portfolio = load_portfolio()
    advice = analyze_candidates(signals, portfolio)

    # 保存建议
    output_path = OUTPUT_DIR / f"signal_advisor_{today_str()}.json"
    atomic_write_json(output_path, advice)
    logger.info(f"建议已保存: {output_path}")

    if args.push:
        push_to_feishu(advice)

    # 输出 JSON（供自动化调用）
    print(json.dumps(advice, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
