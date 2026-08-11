#!/usr/bin/env python3
"""
advisor_rules.py — 炒股助理操作纪律规则引擎

落地来源: output/operation_review_2026-07-15.md（双账户实盘回溯报告）
根因诊断: 用户有赚钱能力，但国金账户"高位追涨 + 死扛不止损"导致亏损；
          广发账户"低位入场 + 及时止盈"则大赚(+31.98%)。
目标: 在选股推荐和盘中监控环节，用规则自动拦截"追高"、催促"止损/锁利"。

五条规则（按优先级）:
  E. 入场价过滤器(最高优先级) — 解决"买在山顶"根因
  A. T+3 强制决策引擎        — 解决"死扛不止损"
  C. 双账户总仓位警示        — 解决"同标风险×2"
  B. 禁止重复抄底闸门        — 解决"越跌越买摊薄"
  D. 盈亏比预演卡片          — 风险收益可视化
  F. 双情景预案              — 乐观/中性/悲观触发条件驱动
  G. 做T子策略(2026-08-12)   — T仓=底仓10%/日≤2次/亏3%止损/20日线定向/10:10节点

用法:
  # 选股推荐前过滤（规则E）
  python3 advisor_rules.py check-entry --code 600206 --price 62.34

  # 盘中持仓诊断（规则A/C/B/D）
  python3 advisor_rules.py diagnose --portfolio .workbuddy/data/user/portfolio.json

  # 作为模块 import
  from advisor_rules import AdvisorRules
  advisor = AdvisorRules()
  flags = advisor.check_entry(code="600206", price=62.34)
  diag = advisor.diagnose_holding(holding_dict, quotes_dict)

数据源:
  - Wind 万得 (优先: 实时行情/K线/技术指标)
  - calc_rsi.py (RSI14, 腾讯 ifzq 前复权, Wind 不可用时)
  - 腾讯 qt.gtimg.cn (实时行情/MA20, Wind 不可用时)
  - portfolio.json (持仓 + broker 标记)

设计原则: 纯函数式规则，不修改外部状态；网络失败降级为 null 不阻断主流程。
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any

# Wind 万得（优先数据源）
try:
    from claw.feeds.wind_analytics import WindAnalytics
    from claw.feeds.wind_utils import get_wind_ma, get_wind_realtime_price, wind_available
except ImportError:
    # 降级：没有 claw 包时所有 Wind 方法返回 None
    def wind_available() -> bool:  # type: ignore[misc]
        return False

    def get_wind_realtime_price(code: str) -> None:  # type: ignore[misc]
        return None

    def get_wind_ma(code: str, period: int = 20) -> None:  # type: ignore[misc]
        return None

    class WindAnalytics:  # type: ignore[no-redef]
        def get_technicals(self, code: str, period: str = "") -> None:
            return None

        @property
        def available(self) -> bool:
            return False


# 做T子策略引擎（2026-08-12 落地，来源=小红书做T笔记+系统化指南）
try:
    from t0_strategy import T0Strategy
except ImportError:
    T0Strategy = None  # type: ignore[assignment,misc]

# 市场情绪层（2026-08-12 落地，用户指出推荐必须考虑板块/大盘情绪）
try:
    from market_sentiment import MarketSentiment
except ImportError:
    MarketSentiment = None  # type: ignore[assignment,misc]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CALC_RSI = PROJECT_ROOT / "scripts" / "calc_rsi.py"
USER_PORTFOLIO = PROJECT_ROOT / ".workbuddy" / "data" / "user" / "portfolio.json"

# ── 规则阈值常量 ────────────────────────────────────────────────
RSI_OVERBOUGHT = 70  # RSI(14) 超买线
DAY_GAIN_WARN = 5.0  # 当日涨幅预警线(%)
DOUBLE_ACCT_PCT = 0.33  # 双账户同标合计占比上限
REBUY_COOLING_DAYS = 30  # 30天内≥2次亏卖 → 冷却
REBUY_DCA_DAYS = 7  # 7天内≥3次买 → 摊薄标记
DCA_WARN_COUNT = 3
T3_LOCK_PROFIT_DAYS = 3  # 持仓≥3天且浮盈 → 锁利建议
T5_REVIEW_DAYS = 5  # 持仓≥5天 → 每日复盘
T7_STOPLOSS_DAYS = 7  # 持仓≥7天且回撤≥-8% → 紧急减仓
RISK_REWARD_MIN = 1.5  # 盈亏比下限

# 默认止损/止盈（与 portfolio.json rules 一致）
DEFAULT_STOP_LOSS = -0.08
DEFAULT_TAKE_PROFIT = 0.05  # +5% 作为短线目标


class AdvisorRules:
    """炒股助理纪律规则引擎"""

    def __init__(
        self, stop_loss: float = DEFAULT_STOP_LOSS, take_profit: float = DEFAULT_TAKE_PROFIT
    ):
        self.stop_loss = stop_loss
        self.take_profit = take_profit

    # ════════════════════════════════════════════════════════════
    # 规则 E: 入场价过滤器（选股推荐前调用）
    # ════════════════════════════════════════════════════════════
    def check_entry(self, code: str, price: float | None = None) -> dict:
        """检查标的当前是否适合入场。

        🔴 价格防错铁律（2026-08-07 落地，根因=8/6早报选股价数量级错误）：
        - 不再信任外部传入 price 作为真实价，**强制优先脚本取价**（gtimg 实时快照）。
        - 若调用方传入 price（AI 建议价），必须过 price_sanity 校验；
          校验失败 → blocked=True + 用可信价重算买区，绝不输出离谱买区。

        Returns:
            {
              "code": "...",
              "blocked": bool,          # True=暂缓推荐
              "flags": [{"level": "warn|block", "reason": "..."}],
              "suggested_buy_zone": str, # 推荐等待的买区
              "rsi14": float|None,
              "ma20": float|None,
              "day_change_pct": float|None,
              "price_used": float|None,   # 实际用于计算的价格（已校验/已取价）
              "price_sanity": dict|None   # 合理性校验结果
            }
        """
        code_prefixed = self._prefix(code)

        # ── 价格获取与校验（P0 防御）──
        live = self._get_live_price(code_prefixed)
        live_price = live.get("price") if live else None
        # 决策价：优先实时快照；无实时价才退用外部传入价（仍需 sanity）
        decision_price = live_price if live_price else price
        sanity = None
        if (
            price is not None
            and live_price is not None
            and abs(price - live_price) / live_price > 0.30
        ):
            # 外部价与实时偏离>30% → 触发 sanity 强校验
            try:
                from price_sanity import check as _sanity_check

                sanity = _sanity_check(code, price)
                if not sanity["ok"]:
                    decision_price = sanity["verified_price"] or live_price
            except Exception:
                sanity = None
        elif price is not None and live_price is None:
            # 无实时价，外部价仍需 sanity（52周/MA20 闸门）
            try:
                from price_sanity import check as _sanity_check

                sanity = _sanity_check(code, price)
                if not sanity["ok"]:
                    decision_price = sanity["verified_price"] or price
            except Exception:
                sanity = None

        rsi = self._get_rsi(code_prefixed)
        day_change = self._get_day_change(code_prefixed)
        ma20 = self._get_ma20(code_prefixed)

        # 市场情绪层（2026-08-12）: 大盘环境 + 板块强度，调节拦截门槛
        sentiment = self._get_sentiment(code)

        flags = []
        blocked = False
        # 价格 sanity 失败 → 阻断推荐并告警
        if sanity and not sanity["ok"]:
            blocked = True
            flags.append(
                {
                    "level": "block",
                    "reason": f"🚫 价格合理性校验失败：传入价¥{price:.2f} 不可信（{'; '.join(sanity['fail_reasons'])}）；已改用可信价¥{decision_price:.2f}，请复核后重试",
                }
            )

        # 市场情绪上下文（info 级，先于拦截输出，供推荐说明引用）
        regime = (sentiment or {}).get("regime", {}).get("regime")
        sector = (sentiment or {}).get("sector")
        if regime:
            flags.append(
                {
                    "level": "info",
                    "rule": "S",
                    "reason": f"📊 市场情绪: 大盘{regime}"
                    + (f" | {sector['note']}" if sector and sector.get("note") else ""),
                }
            )
        # 弱市 + 弱板块 → 提高拦截（追高在弱市/弱板块中风险成倍放大）
        weak_market = regime == "弱"
        weak_sector = bool(sector) and sector.get("strength") == "弱"
        if weak_market and weak_sector:
            blocked = True
            flags.append(
                {
                    "level": "block",
                    "rule": "S",
                    "reason": f"🚫 大盘{regime} + 板块「{sector['sector']}」弱势 → 追高风险大，暂缓推荐"
                    + (
                        f"（板块当日{sector['change_pct']:+.2f}%）"
                        if sector.get("change_pct") is not None
                        else ""
                    ),
                }
            )

        # E1: RSI 超买（阈值随大盘情绪调节: 弱市收紧>60 / 强市放宽>80 防钝化）
        rsi_block_line = 80.0 if regime == "强" else (60.0 if weak_market else RSI_OVERBOUGHT)
        if rsi is not None and rsi > rsi_block_line:
            blocked = True
            flags.append(
                {
                    "level": "block",
                    "reason": f"⚠️ RSI(14)={rsi:.1f} 超买区(>{rsi_block_line:.0f}，大盘{regime or '中'}调节)，追高风险大，建议等回调",
                }
            )

        # E1b: 弱市当日涨幅收紧（弱市追涨=接盘）
        if weak_market and day_change is not None and day_change > 3.0:
            blocked = True
            flags.append(
                {
                    "level": "block",
                    "rule": "S",
                    "reason": f"🚫 大盘{regime}弱势，当日涨幅 {day_change:+.1f}% 已>3% → 弱市追涨风险大，暂缓",
                }
            )

        # E2: 高于 MA20
        if ma20 is not None and decision_price is not None and decision_price > ma20 * 1.02:
            flags.append(
                {
                    "level": "warn",
                    "reason": f"⚠️ 现价 ¥{decision_price:.2f} 高于 MA20 ¥{ma20:.2f}（+{(decision_price / ma20 - 1) * 100:.1f}%），无安全垫",
                }
            )
        elif ma20 is not None and decision_price is not None and decision_price > ma20:
            flags.append(
                {
                    "level": "warn",
                    "reason": f"⚠️ 现价 ¥{decision_price:.2f} 略高于 MA20 ¥{ma20:.2f}，注意追高",
                }
            )

        # E3: 当日涨幅过大
        if day_change is not None and day_change > DAY_GAIN_WARN:
            blocked = True
            flags.append(
                {
                    "level": "block",
                    "reason": f"⚠️ 当日涨幅 {day_change:+.1f}% 已超 {DAY_GAIN_WARN}%，暂缓推荐，等回落",
                }
            )

        # 推荐买区
        suggested = self._suggest_buy_zone(decision_price, ma20, rsi)

        # 做T子策略（规则G，2026-08-12）: 若该标的有底仓 → 附加做T建议（选股场景复用持仓做T）
        t0_suggestion = None
        if not blocked:
            try:
                holding = self._find_holding(code)
                if holding:
                    t0_suggestion = self.check_t0(holding)
            except Exception:
                t0_suggestion = None

        return {
            "code": code,
            "blocked": blocked,
            "flags": flags,
            "suggested_buy_zone": suggested,
            "rsi14": rsi,
            "ma20": ma20,
            "day_change_pct": day_change,
            "price_used": decision_price,
            "price_sanity": sanity,
            "sentiment": sentiment,
            "t0_suggestion": t0_suggestion,
        }

    def _suggest_buy_zone(self, price, ma20, rsi) -> str:
        """生成参考买区文本"""
        if price is None:
            return "价格未知，无法计算买区"
        if ma20 is not None:
            zone_low = min(price * 0.95, ma20)
            zone_high = ma20 * 1.02
            return f"参考买区 ¥{zone_low:.2f}~¥{zone_high:.2f}（MA20附近回调介入）"
        # 无 MA20 时退用 RSI 逻辑
        if rsi is not None and rsi > RSI_OVERBOUGHT:
            return f"建议等待 RSI 回落至 <60 且价格回踩 ¥{price * 0.95:.2f} 以下"
        return f"建议等待回调至 ¥{price * 0.95:.2f} 附近"

    # ════════════════════════════════════════════════════════════
    # 规则 A: T+3 强制决策（盘中持仓诊断）
    # ════════════════════════════════════════════════════════════
    def check_timing(self, holding: dict, today: date | None = None) -> list[dict]:
        """根据持仓天数 + 浮盈亏生成决策建议

        holding 需含: bought_date(ISO str) / avg_cost / current_price / shares
        """
        flags: list[dict] = []
        today = today or date.today()
        bought = holding.get("bought_date")
        if not bought:
            return flags

        try:
            bdate = datetime.strptime(bought, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return flags

        days = (today - bdate).days
        cost = holding.get("avg_cost", 0)
        price = holding.get("current_price")
        if not cost or price is None:
            return flags

        pnl_pct = (price - cost) / cost

        # A1: 持仓≥3天且浮盈 → 锁利
        if days >= T3_LOCK_PROFIT_DAYS and pnl_pct > 0:
            flags.append(
                {
                    "level": "warn",
                    "rule": "A",
                    "reason": f"📌 持仓 {days}天 浮盈 {pnl_pct * 100:+.1f}% → 建议锁利（T+3短线目标已达成）",
                }
            )

        # A2: 持仓≥5天 → 每日复盘
        if days >= T5_REVIEW_DAYS:
            flags.append(
                {
                    "level": "info",
                    "rule": "A",
                    "reason": f"📌 持仓 {days}天 超短线周期 → 每日到期复盘：达到预期？未达则减仓",
                }
            )

        # A3: 持仓≥7天且回撤≥-8% → 紧急减仓
        if days >= T7_STOPLOSS_DAYS and pnl_pct <= self.stop_loss:
            flags.append(
                {
                    "level": "block",
                    "rule": "A",
                    "reason": f"🚨 持仓 {days}天 回撤 {pnl_pct * 100:+.1f}% 破止损线(-8%) → 紧急减仓",
                }
            )

        return flags

    # ════════════════════════════════════════════════════════════
    # 规则 C: 双账户总仓位警示
    # ════════════════════════════════════════════════════════════
    def check_double_account(self, code: str, portfolio_path: Path = USER_PORTFOLIO) -> dict | None:
        """检查同代码是否两账户都有持仓，合并占比是否超限

        需要 portfolio.json 的 holdings 含 broker 字段（"GJ" / "GF"）
        """
        data = self._load_json(portfolio_path)
        holdings = data.get("holdings", [])
        matched = [h for h in holdings if h.get("code") == code]
        if not matched:
            return None

        brokers = {h.get("broker", "GJ") for h in matched}
        if len(brokers) < 2:
            return None  # 单账户持有，不触发

        # 计算合计占比（按市值估算）
        total_value = data.get("summary", {}).get("total_assets", 0)
        if not total_value:
            return {
                "double_account": True,
                "brokers": list(brokers),
                "warn": "⚠️ 双账户同持，但 total_assets 为0无法算占比",
                "over_limit": None,
            }

        combined_value = sum(h.get("shares", 0) * h.get("avg_cost", 0) for h in matched)
        pct = combined_value / total_value
        return {
            "double_account": True,
            "brokers": list(brokers),
            "combined_pct": round(pct, 4),
            "over_limit": pct > DOUBLE_ACCT_PCT,
            "warn": f"⚠️ 双账户合计占比 {pct * 100:.1f}% > {DOUBLE_ACCT_PCT * 100:.0f}% 上限"
            if pct > DOUBLE_ACCT_PCT
            else f"双账户同持 {list(brokers)}，占比 {pct * 100:.1f}%",
        }

    # ════════════════════════════════════════════════════════════
    # 规则 B: 禁止重复抄底闸门
    # ════════════════════════════════════════════════════════════
    def check_rebuy_gate(self, code: str, trade_log: list[dict], today: date | None = None) -> dict:
        """根据交易历史判断是否触发抄底闸门

        trade_log: [{"date": "2026-07-01", "side": "buy"|"sell", "pnl": float|None}]
        """
        today = today or date.today()
        recent = [
            t
            for t in trade_log
            if (today - datetime.strptime(t["date"], "%Y-%m-%d").date()).days
            <= max(REBUY_COOLING_DAYS, REBUY_DCA_DAYS)
        ]
        if not recent:
            return {"triggered": False, "reason": "近期无同标交易"}

        loss_sells = [t for t in recent if t.get("side") == "sell" and (t.get("pnl") or 0) < 0]
        buys = [t for t in recent if t.get("side") == "buy"]

        result: dict[str, Any] = {"triggered": False, "reasons": []}

        # B1: 30天内≥2次亏卖 → 冷却
        if len(loss_sells) >= 2:
            result["triggered"] = True
            result["reasons"].append(
                f"🚫 30天内 {len(loss_sells)} 次亏损卖出 → 冷却 {REBUY_COOLING_DAYS}天不推荐"
            )

        # B2: 7天内≥3次买入 → 摊薄标记
        if len(buys) >= DCA_WARN_COUNT:
            result["triggered"] = True
            result["reasons"].append(
                f"🚫 7天内 {len(buys)} 次买入 → 高风险摊薄，等反弹清仓不再加仓"
            )

        return result

    # ════════════════════════════════════════════════════════════
    # 规则 D: 盈亏比预演卡片
    # ════════════════════════════════════════════════════════════
    def _calibrate_confidence(
        self,
        entry_price: float,
        stop_loss_pct: float,
        take_profit_pct: float,
        rsi: float | None = None,
    ) -> dict:
        """置信度校准（呼应调研 #5：每条论断须可追溯 + 校准）。

        依据盈亏比 + 是否处于超买区，给出 高/中/低 三级与可读依据，
        避免 LLM「张口就给高置信」的无源结论。
        """
        rr = abs(take_profit_pct / stop_loss_pct) if stop_loss_pct != 0 else 0.0
        if rr >= RISK_REWARD_MIN and (rsi is None or rsi <= RSI_OVERBOUGHT):
            level, basis = (
                "高",
                (
                    f"盈亏比 {rr:.2f}:1 ≥ {RISK_REWARD_MIN}:1 且非超买区"
                    + (f"（RSI={rsi:.0f}）" if rsi is not None else "")
                ),
            )
        elif rr >= 1.0:
            level, basis = "中", (f"盈亏比 {rr:.2f}:1 中性，需结合量价/板块确认后再加仓")
        else:
            level, basis = (
                "低",
                (f"盈亏比 {rr:.2f}:1 < {RISK_REWARD_MIN}:1，风险收益不划算，慎参与"),
            )
        return {"level": level, "basis": basis}

    def risk_reward_card(
        self,
        entry_price: float,
        stop_loss_pct: float = DEFAULT_STOP_LOSS,
        take_profit_pct: float = DEFAULT_TAKE_PROFIT,
        rsi: float | None = None,
    ) -> dict:
        """生成盈亏比预演卡片（含置信度校准）"""
        stop_price = entry_price * (1 + stop_loss_pct)
        target_price = entry_price * (1 + take_profit_pct)
        rr = abs(take_profit_pct / stop_loss_pct) if stop_loss_pct != 0 else None

        card = {
            "entry": entry_price,
            "stop_loss_price": round(stop_price, 2),
            "take_profit_price": round(target_price, 2),
            "risk_reward_ratio": round(rr, 2) if rr else None,
            "verdict": "风险收益良好" if (rr and rr >= RISK_REWARD_MIN) else "风险收益不佳",
            "confidence": self._calibrate_confidence(
                entry_price, stop_loss_pct, take_profit_pct, rsi
            ),
        }
        if rr and rr < RISK_REWARD_MIN:
            card["warn"] = f"⚠️ 盈亏比 {rr:.2f}:1 < {RISK_REWARD_MIN}:1，风险收益不划算"
        return card

    # ════════════════════════════════════════════════════════════
    # 规则 F: 双情景预案（乐观/中性/悲观，触发条件驱动）
    # 呼应调研 #2/#3：盘后复盘→次日策略须含至少两套预案(触发条件驱动)
    # ════════════════════════════════════════════════════════════
    def scenario_plan(self, holding: dict, quotes: dict | None = None) -> dict:
        """基于持仓 + 规则生成乐观/中性/悲观三情景预案。

        每个情景含 触发条件(trigger) + 应对动作(action)，杜绝单线预判。
        呼应「盘中只执行不决策」——预案在盘前/盘后定，盘中照触发条件执行。
        """
        code = holding.get("code", "")
        cost = holding.get("avg_cost", 0) or 0
        q = (quotes or {}).get(code, {})
        price = holding.get("current_price") or q.get("price")
        if price is None and cost:
            price = cost
        day_change = holding.get("day_change_pct")
        if day_change is None:
            day_change = q.get("change_pct")
        day_change = day_change or 0.0

        pnl_pct = (price - cost) / cost if (cost and price is not None) else 0.0
        stop_price = cost * (1 + self.stop_loss) if cost else None
        tp_price = cost * (1 + self.take_profit) if cost else None

        optimistic = {
            "bias": "持有/加仓",
            "trigger": (
                f"放量突破 ¥{price * 1.03:.2f}（约 +3%）且所属板块续强、或大盘站上 MA20 转强"
            ),
            "action": (
                f"持有待涨；浮盈达 +{self.take_profit * 100:.0f}% 分批锁利，"
                f"回踩 ¥{price * 0.98:.2f} 可加仓 1/3（不超单只上限）"
            ),
        }
        neutral = {
            "bias": "持有",
            "trigger": "区间震荡，无明确方向信号（量能持平、板块无催化）",
            "action": "持有观察，设移动止损保护已得利润，不追涨不杀跌",
        }
        pessimistic = {
            "bias": "减仓/清仓",
            "trigger": (
                (
                    f"跌破 ¥{stop_price:.2f} 止损线（{self.stop_loss * 100:.0f}%）"
                    if stop_price
                    else "触发纪律止损"
                )
                + " 或板块证伪/外围大跌破位"
            ),
            "action": "触发纪律止损，减仓/清仓不犹豫；永不摊平亏损仓（利弗莫尔铁律）",
        }
        return {
            "code": code,
            "name": holding.get("name", ""),
            "current_price": price,
            "pnl_pct": round(pnl_pct, 4),
            "stop_loss_price": round(stop_price, 2) if stop_price else None,
            "take_profit_price": round(tp_price, 2) if tp_price else None,
            "optimistic": optimistic,
            "neutral": neutral,
            "pessimistic": pessimistic,
        }

    # ════════════════════════════════════════════════════════════
    # 规则 G: 做T子策略建议（2026-08-12 落地，来源=小红书做T笔记+系统化指南）
    # 识别口径: T仓=底仓10% / 日≤2次 / 单次亏3%止损 / 20日线定正反T / 10:10节点
    # ════════════════════════════════════════════════════════════
    def check_t0(
        self,
        holding: dict,
        quotes: dict | None = None,
        t_count_today: int = 0,
        now: datetime | None = None,
    ) -> dict | None:
        """对持仓生成做T建议（仅提示不阻断，做T需已有底仓）。

        quotes 复用盘中已有行情（price/ma20/rally_pct），避免重复请求；
        rally_pct(自60日低点反弹幅度%) 缺失时自动拉取，失败降级为 None（R8不生效）。
        返回 None 表示引擎不可用或标的无底仓。
        """
        if T0Strategy is None:
            return None
        code = holding.get("code", "")
        q = (quotes or {}).get(code, {})
        price = holding.get("current_price") or q.get("price")
        ma20 = q.get("ma20")
        if ma20 is None:
            ma20 = self._get_ma20(self._prefix(code))
        rally_pct = q.get("rally_pct")
        if rally_pct is None:
            rally_pct = self._get_rally_pct(self._prefix(code))
        try:
            return T0Strategy().evaluate(
                holding,
                price=price,
                ma20=ma20,
                t_count_today=t_count_today,
                rally_pct=rally_pct,
                now=now,
            )
        except Exception:
            return None

    def _find_holding(self, code: str, portfolio_path: Path = USER_PORTFOLIO) -> dict | None:
        """按代码查持仓（供 check_entry 做T建议复用）"""
        data = self._load_json(portfolio_path)
        for h in data.get("holdings", []):
            if h.get("code") == code:
                return h
        return None

    def _get_sentiment(self, code: str | None = None) -> dict | None:
        """市场情绪上下文（大盘环境 + 个股板块强度）。降级: 引擎缺失/失败 → None"""
        if MarketSentiment is None:
            return None
        try:
            ms = MarketSentiment()
            ctx: dict = {"regime": ms.market_regime()}
            if code:
                ctx["sector"] = ms.sector_strength(code)
            return ctx
        except Exception:
            return None

    # ════════════════════════════════════════════════════════════
    # 组合诊断（盘中监控主入口）
    # ════════════════════════════════════════════════════════════
    def diagnose_holding(
        self,
        holding: dict,
        quotes: dict | None = None,
        trade_log: list[dict] | None = None,
        today: date | None = None,
    ) -> dict:
        """对单个持仓执行 A + C + B + D 全规则诊断"""
        today = today or date.today()
        code = holding.get("code", "")
        quotes = quotes or {}
        q = quotes.get(code, {})

        # 注入实时价（优先外部传入，否则自动拉取）
        if q.get("price") is not None:
            if holding.get("current_price") is None:
                holding["current_price"] = q["price"]
            holding["day_change_pct"] = q.get("change_pct")
        elif holding.get("current_price") is None:
            # 自动拉取实时价（降级：失败则跳过A规则的价格判断）
            live = self._get_live_price(self._prefix(code))
            if live:
                holding["current_price"] = live.get("price")
                holding["day_change_pct"] = live.get("change_pct")

        flags = []
        # A: 时机纪律
        flags.extend(self.check_timing(holding, today))
        # C: 双账户
        dbl = self.check_double_account(code)
        if dbl and dbl.get("over_limit"):
            flags.append({"level": "block", "rule": "C", "reason": dbl["warn"]})
        elif dbl:
            flags.append({"level": "info", "rule": "C", "reason": dbl["warn"]})
        # B: 抄底闸门
        if trade_log:
            rb = self.check_rebuy_gate(code, trade_log, today)
            if rb.get("triggered"):
                for r in rb["reasons"]:
                    flags.append({"level": "block", "rule": "B", "reason": r})

        # D: 盈亏比（若有入场价）
        rr_card = None
        if holding.get("avg_cost"):
            rr_card = self.risk_reward_card(holding["avg_cost"])

        # F: 双情景预案（盘前/盘后定，盘中照触发条件执行）
        scenario = self.scenario_plan(holding, quotes)

        # G: 做T子策略（盘中做T窗口提示；仅提示不阻断）
        t0 = self.check_t0(holding, quotes)
        if t0 and t0.get("t0"):
            flags.append(
                {
                    "level": "info",
                    "rule": "G",
                    "reason": t0.get("summary", ""),
                }
            )

        return {
            "code": code,
            "name": holding.get("name", ""),
            "flags": flags,
            "risk_reward": rr_card,
            "scenario_plan": scenario,
            "t0_strategy": t0,
            "has_block": any(f["level"] == "block" for f in flags),
        }

    # ── 工具方法 ────────────────────────────────────────────────
    @staticmethod
    def _prefix(code: str) -> str:
        code = code.strip().lower()
        if code.startswith(("sh", "sz")):
            return code
        return f"sh{code}" if code.startswith("6") else f"sz{code}"

    def _get_rsi(self, code_prefixed: str) -> float | None:
        """RSI(14)。Wind 万得直接返回，降级 calc_rsi.py 子进程。"""
        bare = code_prefixed[2:] if code_prefixed.startswith(("sh", "sz")) else code_prefixed
        # 1) Wind 万得 — 直接获取 RSI 技术指标
        _wa = WindAnalytics()
        if _wa.available:
            try:
                rsi_data = _wa.get_technicals(bare, "近60日RSI")
                if rsi_data and len(rsi_data) >= 1:
                    row = rsi_data[-1]
                    # 搜索可能的 RSI 列名
                    rsi_key = next((k for k in row if ("RSI" in k or "相对强弱" in k)), None)
                    if rsi_key:
                        v = row[rsi_key]
                        if v is not None and isinstance(v, (int, float)):
                            return round(float(v), 1)
            except Exception:
                pass
        # 2) calc_rsi.py 子进程
        try:
            out = subprocess.run(
                [sys.executable, str(CALC_RSI), code_prefixed],
                capture_output=True,
                text=True,
                timeout=30,
            )
            for line in out.stdout.splitlines():
                if line.startswith("JSON:"):
                    data = json.loads(line[5:].strip())
                    if data and data[0].get("rsi14") is not None:
                        return float(data[0]["rsi14"])
            for line in out.stdout.splitlines():
                if "RSI(14)=" in line:
                    return float(line.split("=")[1].strip())
        except Exception:
            pass
        return None

    def _get_day_change(self, code_prefixed: str) -> float | None:
        """当日涨跌幅（%）。Wind 优先，降级腾讯 gtimg。"""
        # 1) Wind 万得
        bare = code_prefixed[2:] if code_prefixed.startswith(("sh", "sz")) else code_prefixed
        if wind_available():
            try:
                r = get_wind_realtime_price(bare)
                if r and r.get("change_pct") is not None:
                    return r["change_pct"]  # type: ignore[no-any-return]
            except Exception:
                pass
        # 2) 腾讯 gtimg
        try:
            url = f"https://qt.gtimg.cn/q={code_prefixed}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310: URL scheme is hardcoded https
                text = resp.read().decode("gbk", errors="replace")
            vals = text.split('"')[1].split("~")
            if len(vals) > 32:
                return float(vals[32])
        except Exception:
            pass
        return None

    def _get_live_price(self, code_prefixed: str) -> dict | None:
        """拉取实时价（price + change_pct）。Wind 优先，降级腾讯 gtimg。"""
        bare = code_prefixed[2:] if code_prefixed.startswith(("sh", "sz")) else code_prefixed
        # 1) Wind 万得
        if wind_available():
            try:
                r = get_wind_realtime_price(bare)
                if r:
                    return {"price": r.get("price"), "change_pct": r.get("change_pct")}
            except Exception:
                pass
        # 2) 腾讯 gtimg
        try:
            url = f"https://qt.gtimg.cn/q={code_prefixed}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310: URL scheme is hardcoded https
                text = resp.read().decode("gbk", errors="replace")
            vals = text.split('"')[1].split("~")
            if len(vals) > 32:
                return {
                    "price": float(vals[3]) if vals[3] else None,
                    "change_pct": float(vals[32]) if vals[32] else None,
                }
        except Exception:
            pass
        return None

    def _get_ma20(self, code_prefixed: str) -> float | None:
        """MA20。Wind 优先，降级腾讯 ifzq → 新浪。"""
        bare = code_prefixed[2:] if code_prefixed.startswith(("sh", "sz")) else code_prefixed
        # 1) Wind 万得
        if wind_available():
            try:
                ma = get_wind_ma(bare, period=20)
                if ma is not None:
                    return float(ma)
            except Exception:
                pass
        # 2) 腾讯 ifzq 前复权
        try:
            url = (
                f"https://web.ifzq.gtimg.cn/appstuff/app/fqkline/get"
                f"?param={code_prefixed},day,,,60,qfq"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310: URL scheme is hardcoded https
                data = json.loads(resp.read().decode("utf-8"))
            sub = data.get("data", {})
            if isinstance(sub, dict):
                kl = sub.get(code_prefixed, {}).get("qfqday", [])
                if len(kl) >= 20:
                    closes = [float(k[2]) for k in kl[-20:] if len(k) > 2 and k[2]]
                    if closes:
                        return sum(closes) / len(closes[-20:])
        except Exception:
            pass
        # 3) 新浪回退
        try:
            url = (
                f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php"
                f"/CN_MarketData.getKLineData?symbol={code_prefixed}&scale=240&ma=no&datalen=60"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310: URL scheme is hardcoded https
                arr = json.loads(resp.read().decode("utf-8"))
            closes = [float(row["close"]) for row in arr if row.get("close")]
            if len(closes) >= 20:
                return sum(closes[-20:]) / len(closes[-20:])
        except Exception:
            pass
        return None

    def _get_rally_pct(self, code_prefixed: str) -> float | None:
        """自近60日低点反弹幅度（%）。市场情绪维度(做T R8)，新浪K线，失败降级 None。

        用「自低点反弹」而非「近20日涨幅」——V型反转下20日窗口会把高位算进去导致失真。
        """
        try:
            url = (
                f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php"
                f"/CN_MarketData.getKLineData?symbol={code_prefixed}&scale=240&ma=no&datalen=60"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310: URL scheme is hardcoded https
                arr = json.loads(resp.read().decode("utf-8"))
            lows = [float(row["low"]) for row in arr if row.get("low")]
            closes = [float(row["close"]) for row in arr if row.get("close")]
            if not lows or not closes:
                return None
            low = min(lows)
            cur = closes[-1]
            if low <= 0:
                return None
            return round((cur / low - 1) * 100, 2)
        except Exception:
            pass
        return None

    @staticmethod
    def _load_json(path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)  # type: ignore[no-any-return]
        except (OSError, json.JSONDecodeError):
            return {}


# ── CLI 入口 ────────────────────────────────────────────────────
def main():
    import argparse

    parser = argparse.ArgumentParser(description="炒股助理纪律规则引擎")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="显示依赖库日志（默认静音，防止 2>&1 时 Wind 提示污染 JSON 输出）",
    )
    sub = parser.add_subparsers(dest="cmd")

    p_entry = sub.add_parser("check-entry", help="入场价过滤(规则E)")
    p_entry.add_argument("--code", required=True)
    p_entry.add_argument("--price", type=float, default=None)

    p_diag = sub.add_parser("diagnose", help="持仓全规则诊断(A/C/B/D)")
    p_diag.add_argument("--portfolio", default=str(USER_PORTFOLIO))
    p_diag.add_argument("--code", default=None, help="只诊断指定代码")

    args = parser.parse_args()

    # 默认静音第三方 logger（如 wind_utils 的"每日查询上限已达"warning），
    # 避免自动化里 2>&1 合流时污染 stdout 的 JSON。--verbose 可恢复。
    if not args.verbose:
        import logging as _logging

        for _name in ("claw", "wind"):
            _logging.getLogger(_name).setLevel(_logging.ERROR)
    advisor = AdvisorRules()

    if args.cmd == "check-entry":
        result = advisor.check_entry(args.code, args.price)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.cmd == "diagnose":
        data = advisor._load_json(Path(args.portfolio))
        holdings = data.get("holdings", [])
        if args.code:
            holdings = [h for h in holdings if h.get("code") == args.code]
        out = []
        for h in holdings:
            out.append(advisor.diagnose_holding(h))
        print(json.dumps(out, ensure_ascii=False, indent=2))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
