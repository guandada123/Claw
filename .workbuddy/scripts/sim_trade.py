#!/usr/bin/env python3
"""
模拟炒股引擎 — AI 自主决策买卖，跟踪收益与胜率
总资金：¥50,000（07-14 从 30,000 放宽）| 不可买科创板/北交所（创业板已于 07-29 放开）

优化说明（2026-06-06）：
1. 修复所有语法错误（字典缺少逗号）
2. 添加智能追踪止损（trailing stop）
3. 添加分级止盈策略（双模式：冲刺期5/10/15%、正常期15/25/35%，由日期自动判定）
4. 添加风险管理（单只持仓≤50%，行业分散）
5. 添加自动止损止盈检查函数
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import sys
import time
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

# A股整手约束（2026-08-14 整手审计整改）：最小交易单位=1手=100股，
# 买入及部分卖出数量必须为100整数倍且≥100；全仓卖出允许零股尾仓。
LOT_SIZE = 100

# 不可交易的板块（科创板/北交所/ST）。创业板(300/301)已于 07-29 放开。
RESTRICTED_PREFIXES = ["688", "689", "8", "4"]

# 初始资金
INITIAL_CAPITAL = 50000.0


def get_effective_capital(pf: dict) -> float:
    """实际投入本金 = 初始本金 + 历次加仓合计。

    收益百分比分母用此值，避免硬编码 INITIAL_CAPITAL 与 portfolio.json
    的 capital_additions 脱节。无 capital_additions 时退化为 INITIAL_CAPITAL。
    """
    cfg = pf.get("config", {})
    base = cfg.get("initial_capital", INITIAL_CAPITAL)
    adds = sum(float(a.get("amount", 0)) for a in cfg.get("capital_additions", []))
    return round(base + adds, 2)


# 风险管理参数
MAX_POSITION_PCT = 0.50  # 单只股票最大仓位 50%
MAX_SECTOR_PCT = 0.60  # 同行业最大仓位 60%
STOP_LOSS_PCT = 0.08  # 固定止损线 -8%（主板/中小板，降级方案）
# 创业板(300/301)单独更宽止损：20%涨跌停，单日波动大，-8%易被穿透。07-29放开创业板后增设。
CYB_STOP_LOSS_PCT = 0.15  # 创业板固定止损线 -15%
TRAILING_STOP_PCT = 0.15  # 追踪止损：从最高价回落 15% 触发


# 分级止盈（双模式：冲刺期/正常期，由日期自动判定）
#   冲刺期（每月20号后 或 6月14号后）：5%/10%/15% 快速轮动锁定利润
#   正常期（其余日期）：15%/25%/35% 耐心持有追求高收益
#   B3 市场状态驱动（08-05 新增）：正常期 + 大盘强修复（上证站上MA20且最近3日连涨）→ 第一档 +15% 上浮 +18%
# 与 trading-dual-mode-seamless skill 对齐；check_take_profit 运行时调用，
# 模式切换时自动重置 take_profit_level（见 check_take_profit）。
def _is_sprint_period() -> bool:
    """判断当前是否处于冲刺期（每月20号后 或 6月14号后）。"""
    today = date.today()
    return (today.month == 6 and today.day >= 14) or (today.day >= 20)


_MARKET_STRONG_CACHE = {"ts": 0.0, "strong": False}  # 5 分钟缓存，避免每持仓检查都拉网络
MARKET_INDEX_KLINE_URL = (
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000001,day,,,30,qfq"
)


def _market_strong_recovery() -> bool:
    """大盘强修复判定：上证收盘站上 MA20 且最近 3 个交易日连涨（B3, 08-05）。失败回退 False 不阻断。"""
    now = time.time()
    if now - _MARKET_STRONG_CACHE["ts"] < 300:
        return _MARKET_STRONG_CACHE["strong"]
    strong = False
    try:
        import urllib.request

        req = urllib.request.Request(MARKET_INDEX_KLINE_URL, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=8).read().decode("utf-8", "ignore")
        d = json.loads(raw)
        days = (
            d.get("data", {}).get("sh000001", {}).get("qfqday")
            or d.get("data", {}).get("sh000001", {}).get("day")
            or []
        )
        if len(days) >= 24:
            closes = [float(x[2]) for x in days]
            ma20 = sum(closes[-20:]) / 20
            last3 = closes[-3:]
            strong = closes[-1] >= ma20 and all(last3[i] > last3[i - 1] for i in range(1, 3))
    except Exception:
        pass
    _MARKET_STRONG_CACHE["ts"] = now
    _MARKET_STRONG_CACHE["strong"] = strong
    return strong


def _get_take_profit_levels() -> list:
    """返回当前模式的止盈分级（运行时判定，非模块级静态）。"""
    if _is_sprint_period():
        return [
            {"pct": 0.05, "sell_ratio": 0.33, "desc": "+5%卖出1/3(冲刺)"},
            {"pct": 0.10, "sell_ratio": 0.33, "desc": "+10%再卖1/3(冲刺)"},
            {"pct": 0.15, "sell_ratio": 0.34, "desc": "+15%清仓(冲刺)"},
        ]
    if _market_strong_recovery():
        return [
            {"pct": 0.18, "sell_ratio": 0.33, "desc": "+18%卖出1/3(大盘强修复上浮)"},
            {"pct": 0.25, "sell_ratio": 0.33, "desc": "+25%再卖1/3"},
            {"pct": 0.35, "sell_ratio": 0.34, "desc": "+35%清仓"},
        ]
    return [
        {"pct": 0.15, "sell_ratio": 0.33, "desc": "+15%卖出1/3"},
        {"pct": 0.25, "sell_ratio": 0.33, "desc": "+25%再卖1/3"},
        {"pct": 0.35, "sell_ratio": 0.34, "desc": "+35%清仓"},
    ]


# 现金不足主动减仓规则（07/31 讨论，08/02 落地引擎）
CASH_CRITICAL_PCT = 0.15  # 现金<总资产15% → 触发主动减仓
CASH_TARGET_PCT = 0.25  # 减仓后目标现金占比 ≥25%
CASH_SELL_MAX_RATIO = 0.50  # 单次最多减仓原持仓的50%

# star_signal 集成 (v2.1)
try:
    from star_signal_adapter import get_dynamic_stop_loss, get_star_signal  # noqa: F401

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
    # 回写展示元字段，避免顶层 total_assets/initial_capital 长期为 None
    # （引擎计算用 get_effective_capital 读 config，此处仅补展示层，与之一致）
    pf["config"]["updated_at"] = now()
    pf["initial_capital"] = get_effective_capital(pf)
    mkt = sum(float(v.get("market_value", 0)) for v in pf.get("positions", {}).values())
    pf["total_assets"] = round(mkt + float(pf.get("cash", 0)), 2)
    with PortfolioLock():
        PORTFOLIO_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(PORTFOLIO_FILE, pf)


MEMORY_FILE = DATA_DIR / "trading_memory.json"


def record_trade_memory(txn: dict) -> None:
    """交易完成后自动写入交易记忆（memory_type=trade），去重防重复写入"""
    memory_records = []
    if MEMORY_FILE.exists():
        try:
            memory_records = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            memory_records = []

    # 用 txn id + date 生成唯一指纹
    raw = f"{txn.get('id', '')}:{txn.get('date', '')}:{txn.get('code', '')}"
    fingerprint = hashlib.sha256(raw.encode()).hexdigest()[:16]
    if any(r.get("fingerprint") == fingerprint for r in memory_records):
        return  # 已存在，跳过

    txn_type = txn.get("type", "TRADE")
    code = txn.get("code", "")
    name = txn.get("name", "")
    action = "买入" if txn_type == "BUY" else "卖出"

    memory_entry = {
        "id": f"mem_{txn.get('date', 'unknown')}_{txn.get('id', '').lower()}",
        "fingerprint": fingerprint,
        "created_at": now(),
        "updated_at": now(),
        "memory_type": "trade",
        "title": f"{action} {name}({code})",
        "summary": (
            f"{action} {shares}股 @¥{price:.2f} | "
            f"总金额 ¥{txn.get('total', txn.get('net_proceeds', 0)):.2f}"
            if (shares := txn.get("shares", 0)) and (price := txn.get("price", 0))
            else f"{action} {code}"
        ),
        "lesson": txn.get("reason", ""),
        "symbols": [code],
        "authors": ["北辰_自动交易"],
        "strategies": [],
        "experts": [],
        "market_regime": "unknown",
        "positive_signals": [],
        "negative_signals": [],
        "applicable_conditions": ["交易闭环自动记录"],
        "avoid_conditions": [],
        "source_decision_ids": [],
        "evidence": {},
        "confidence": 1.0,
        "status": "active",
    }
    memory_records.append(memory_entry)
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(MEMORY_FILE, memory_records)
    logger.info("交易记忆已写入: %s", memory_entry["title"])


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
    """检查股票代码是否受限，返回原因或 None。创业板(300/301)已放开(07-29)。"""
    for prefix in RESTRICTED_PREFIXES:
        if code.startswith(prefix):
            board = {
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

    # 更新最高价（仅更新内存，统一由 auto_check_all_positions 调用方保存）
    if current_price > highest_price:
        pos["highest_price"] = current_price
        highest_price = current_price

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

    # 2. 固定止损（降级方案 / ATR不可用时的主方案）
    #    创业板(300/301)用更宽止损线 CYB_STOP_LOSS_PCT，其余用 STOP_LOSS_PCT
    is_cyb = code.startswith(("300", "301"))
    stop_pct = CYB_STOP_LOSS_PCT if is_cyb else STOP_LOSS_PCT
    if pnl_pct <= -stop_pct * 100:
        return {
            "should_sell": True,
            "reason": f"触发固定止损线 {pnl_pct:.2f}% (止损线 -{stop_pct * 100:.0f}%{' 创业板' if is_cyb else ''})",
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
    检查是否需要止盈（分级止盈，双模式）
    返回：{"should_sell": bool, "reason": str, "shares_to_sell": int}
    """
    pos = pf["positions"].get(code)
    if not pos:
        return {"should_sell": False, "reason": "无持仓"}

    # 检测止盈模式切换（冲刺期 ↔ 正常期），自动重置止盈层级
    current_mode = "sprint" if _is_sprint_period() else "normal"
    last_mode = pos.get("mode")
    if last_mode and last_mode != current_mode:
        logger.info(
            "%s 止盈模式切换: %s → %s，重置 take_profit_level 为 1",
            code,
            last_mode,
            current_mode,
        )
        pos["take_profit_level"] = 1
    pos["mode"] = current_mode

    current_price = pos.get("current_price", pos["avg_cost"])
    avg_cost = pos["avg_cost"]
    pnl_pct = (current_price - avg_cost) / avg_cost * 100

    current_level = pos.get("take_profit_level", 1)
    levels = _get_take_profit_levels()

    # 检查当前级别是否需要止盈
    if current_level <= len(levels):
        level = levels[current_level - 1]
        if pnl_pct >= level["pct"] * 100:
            shares_to_sell = int(pos["shares"] * level["sell_ratio"])
            # 整手约束（2026-08-14 审计整改）：部分卖出须为100整数倍
            shares_to_sell = (shares_to_sell // LOT_SIZE) * LOT_SIZE
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


def check_cash_insufficient(pf: dict) -> dict | None:
    """
    检查现金是否不足，若是则选出最弱持仓建议减仓。

    规则（与午间选股/策略执行 prompt 对齐）：
    - 触发条件：现金占比 < CASH_CRITICAL_PCT（15%）且有持仓
    - 减仓优先级：亏损最多(距止损最近) > 盈利最薄 > 市值最小
    - 减仓量：释放至现金≥25%总资产；单次仅减1只、不超原仓50%
    - 返回 None 表示无需操作

    优先级：低于止损/止盈强制线 — 调用方应仅在无强制卖出时启用。
    """
    total = calc_total_asset(pf)
    cash_ratio = pf["cash"] / total if total > 0 else 1.0

    if cash_ratio >= CASH_CRITICAL_PCT or not pf["positions"]:
        return None

    # 计算需要释放的现金量（目标：现金≥25%总资产）
    target_cash = total * CASH_TARGET_PCT
    need_release = target_cash - pf["cash"]

    # 评估每只持仓的「弱势分」— 分数越低越该减
    weakest = None
    weakest_score = float("inf")

    for code, pos in pf["positions"].items():
        cur_price = pos.get("current_price", pos["avg_cost"])
        pnl_pct = (cur_price - pos["avg_cost"]) / pos["avg_cost"]
        mkt_value = pos["shares"] * cur_price

        # 弱势分 = 亏损幅度(负值越大越弱) + 小市值权重(越小越可弃)
        # 亏损每1%计-1分，市值每低于¥5000计-0.5分
        score = pnl_pct * 100  # 亏损为负分
        # 市值惩罚：市值<¥3000额外-2分，<¥5000额外-1分
        if mkt_value < 3000:
            score -= 2.0
        elif mkt_value < 5000:
            score -= 1.0

        if score < weakest_score:
            weakest_score = score
            weakest = (code, pos, cur_price, pnl_pct, mkt_value)

    if weakest is None:
        return None

    code, pos, cur_price, pnl_pct, mkt_value = weakest

    # 计算减仓量：释放 need_release 现金，但不超过原仓50%
    max_sell_shares = int(pos["shares"] * CASH_SELL_MAX_RATIO)
    # 向下取整到100的整数倍
    max_sell_shares = (max_sell_shares // 100) * 100
    if max_sell_shares < 100:
        max_sell_shares = pos["shares"]  # 持仓太少，全卖

    # 需要卖出的股数（按当前价算，取整百）
    shares_needed = int(need_release / cur_price)
    shares_needed = ((shares_needed // 100) + 1) * 100  # 向上取整百
    shares_to_sell = min(shares_needed, max_sell_shares, pos["shares"])
    shares_to_sell = (shares_to_sell // 100) * 100  # 确保整百

    if shares_to_sell < 100:
        return None  # 连一手都卖不了

    expected_release = shares_to_sell * cur_price * 0.998  # 扣约0.2%税费
    new_cash_ratio = (pf["cash"] + expected_release) / total

    return {
        "code": code,
        "name": pos["name"],
        "action": "SELL",
        "reason": (
            f"现金不足主动调仓释放（当前现金占比 {cash_ratio:.1%}<{CASH_CRITICAL_PCT:.0%}，"
            f"卖出{pos['name']}({code}) {shares_to_sell}股，"
            f"盈亏{pnl_pct:.1%}，释放约¥{expected_release:.0f}，"
            f"预计现金占比→{new_cash_ratio:.1%}）"
        ),
        "shares": shares_to_sell,
        "priority": "cash_rebalance",
    }


def auto_check_all_positions(pf: dict) -> list:
    """
    自动检查所有持仓的止损止盈条件
    返回建议交易列表
    """
    suggestions = []

    for code, pos in pf["positions"].items():
        # 🔴 冗余加固（2026-08-07 落地，根因=8/6早报选股价数量级错误）
        # 判定前先确认 current_price 已过 sanity，防止错误价穿透 update 入口守卫触发误卖
        current_price = pos.get("current_price")
        g = _sanity_check_price(code, current_price)
        if not g["ok"]:
            logger.error(
                f"🚫 止损/止盈判定跳过 {code}：现价¥{current_price} 未过sanity校验"
                f"（{g['reason']}），避免错误价触发误卖"
            )
            continue

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

    # 按优先级排序：high > medium > low > cash_rebalance
    priority_order = {"high": 0, "medium": 1, "low": 2, "cash_rebalance": 3}
    suggestions.sort(key=lambda x: priority_order.get(x["priority"], 99))

    # 现金不足检查（仅无强制止损止盈时启用，优先级最低）
    if not suggestions:
        cash_check = check_cash_insufficient(pf)
        if cash_check:
            suggestions.append(cash_check)

    return suggestions


# ══════════════════════════════════════════════════
#  交易操作
# ══════════════════════════════════════════════════


def cmd_buy(code: str, shares: int, price: float, name: str = ""):
    """买入股票（带风险管理 + 价格防错校验）"""
    pf = load_portfolio()

    # 价格防错（2026-08-07 落地，根因=8/6早报选股价数量级错误）
    g = _sanity_check_price(code, price)
    if not g["ok"]:
        logger.error(f"🚫 买入价校验失败 {code} ¥{price}: {g['reason']}（拒绝买入）")
        return {
            "ok": False,
            "error": f"价格校验失败：¥{price} 不可信（{g['reason']}），已拒绝买入",
            "sanity_failed": True,
            "reliable_price": g["reliable_price"],
        }

    # 检查限制
    err = check_restricted(code)
    if err:
        return {"ok": False, "error": err}

    # 整手约束门禁（2026-08-14 审计整改）：A股买入须为100整数倍且≥100，
    # 非整手直接拒绝（失败闭合，避免落库不可成交的零股仓位）
    if not isinstance(shares, int) or shares < LOT_SIZE or shares % LOT_SIZE != 0:
        return {
            "ok": False,
            "error": f"股数必须为100整数倍且≥100，收到 {shares} 股（A股最小1手=100股）",
        }

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
        "id": f"B{int(datetime.now().timestamp() * 1000)}",
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

    # 记录交易记忆（审计 🟢6: 异常不阻断交易结果返回）
    try:
        record_trade_memory(txn)
    except Exception as e:
        logger.warning("交易记忆写入失败（不影响交易结果）: %s", e)

    return {
        "cash_remaining": pf["cash"],
        "total_asset": calc_total_asset(pf),
    }


def cmd_sell(code: str, shares: int, price: float, reason: str = ""):
    """卖出股票（带价格防错校验）"""
    pf = load_portfolio()

    # 价格防错（2026-08-07 落地，根因=8/6早报选股价数量级错误）
    g = _sanity_check_price(code, price)
    if not g["ok"]:
        logger.error(f"🚫 卖出价校验失败 {code} ¥{price}: {g['reason']}（拒绝卖出）")
        return {
            "ok": False,
            "error": f"价格校验失败：¥{price} 不可信（{g['reason']}），已拒绝卖出",
            "sanity_failed": True,
            "reliable_price": g["reliable_price"],
        }

    pos = get_position(pf, code)
    if not pos:
        return {"ok": False, "error": f"不持有 {code}，无法卖出"}

    if shares > pos["shares"]:
        return {"ok": False, "error": f"持仓不足：需要 {shares} 股，持有 {pos['shares']} 股"}

    if shares <= 0:
        shares = pos["shares"]  # 全部卖出（允许零股尾仓）

    # 整手约束（2026-08-14 审计整改）：部分卖出须为100整数倍；
    # 全仓卖出(pos["shares"]) 允许零股尾仓，不强制整手
    if 0 < shares < pos["shares"]:
        lot = (shares // LOT_SIZE) * LOT_SIZE
        if lot < LOT_SIZE:
            return {
                "ok": False,
                "error": f"部分卖出股数须为100整数倍且≥100，收到 {shares} 股",
            }
        shares = lot

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
        "id": f"S{int(datetime.now().timestamp() * 1000)}",
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

    # 记录交易记忆（审计 🟢6: 异常不阻断交易结果返回）
    try:
        record_trade_memory(txn)
    except Exception as e:
        logger.warning("交易记忆写入失败（不影响交易结果）: %s", e)

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
    save_portfolio(pf)  # 持久化 check_stop_loss 中的 highest_price 更新

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
    cap = get_effective_capital(pf)
    pnl = round(total - cap, 2)
    pnl_pct = round(pnl / cap * 100, 2)

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
    cap = get_effective_capital(pf)

    pf["daily_snapshot"][d] = {
        "total_asset": total,
        "cash": pf["cash"],
        "pnl": round(total - cap, 2),
        "pnl_pct": round((total - cap) / cap * 100, 2),
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


def _sanity_check_price(code: str, price: float) -> dict:
    """价格防错校验（2026-08-07 落地，根因=8/6早报选股价数量级错误）。

    对写入持仓的现价做合理性校验，防止错误价污染止损/止盈判定。
    返回: {"ok": bool, "reliable_price": float|None, "reason": str}
    """
    if not price or price <= 0:
        return {"ok": False, "reliable_price": None, "reason": "价格非正或缺失"}
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
        from price_sanity import check as _ps_check

        res = _ps_check(code, float(price))
        if not res["ok"]:
            return {
                "ok": False,
                "reliable_price": res.get("verified_price"),
                "reason": "; ".join(res.get("fail_reasons", [])),
            }
    except Exception as e:
        logger.warning(f"sanity 校验异常({code}): {e}，放行但不信任")
        return {"ok": True, "reliable_price": price, "reason": ""}
    return {"ok": True, "reliable_price": price, "reason": ""}


def cmd_update_price(code: str, price: float):
    """更新持仓股票当前价格（带价格防错校验）"""
    pf = load_portfolio()
    if code in pf["positions"]:
        g = _sanity_check_price(code, price)
        if not g["ok"]:
            # 拒绝写入错误价，保留旧价，记录告警
            logger.error(f"🚫 价格校验失败 {code} ¥{price}: {g['reason']}（保留旧价，未刷新）")
            return {
                "ok": False,
                "code": code,
                "sanity_failed": True,
                "reason": g["reason"],
                "reliable_price": g["reliable_price"],
            }
        price = g["reliable_price"] or price
        pf["positions"][code]["current_price"] = price
        # 更新最高价
        if price > pf["positions"][code].get("highest_price", price):
            pf["positions"][code]["highest_price"] = price
        save_portfolio(pf)
        return {"ok": True, "code": code, "price": price}
    return {"ok": False, "error": f"不持有 {code}"}


def cmd_update_all_prices(prices: dict):
    """批量更新价格 {code: price}（带价格防错校验）"""
    pf = load_portfolio()
    updated = []
    sanity_failed = []
    for code, price in prices.items():
        if code in pf["positions"]:
            g = _sanity_check_price(code, price)
            if not g["ok"]:
                sanity_failed.append(
                    {
                        "code": code,
                        "price": price,
                        "reason": g["reason"],
                        "reliable_price": g["reliable_price"],
                    }
                )
                logger.error(f"🚫 批量价格校验失败 {code} ¥{price}: {g['reason']}（跳过刷新）")
                continue  # 拒绝写入错误价，保留旧价
            price = g["reliable_price"] or price
            pf["positions"][code]["current_price"] = price
            # 更新最高价
            if price > pf["positions"][code].get("highest_price", price):
                pf["positions"][code]["highest_price"] = price
            updated.append(code)
    if updated:
        save_portfolio(pf)
    return {"ok": True, "updated": updated, "sanity_failed": sanity_failed}


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
    cap = get_effective_capital(pf)

    for i, dt in enumerate(sorted_dates):
        snap = snapshots[dt]

        # 当日收益率（相对前一日）
        if i == 0:
            daily_ret = round((snap["total_asset"] - cap) / cap * 100, 2)
        else:
            prev_val = snapshots[sorted_dates[i - 1]]["total_asset"]
            daily_ret = (
                round((snap["total_asset"] - prev_val) / prev_val * 100, 2) if prev_val > 0 else 0
            )

        # 累计收益
        cum_pnl = round(snap["total_asset"] - cap, 2)
        cum_pnl_pct = round(cum_pnl / cap * 100, 2)

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
