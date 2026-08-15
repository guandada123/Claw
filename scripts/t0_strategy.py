#!/usr/bin/env python3
"""
t0_strategy.py — 做T（T+0 日内交易）子策略引擎

落地: 2026-08-12，来源 = 小红书做T实战笔记(2026-08-11 18:25) + 系统化做T指南。
识别口径已经用户确认: T仓=底仓1/10 / 10:10节点 / 单次亏3点认错 / 20日线定正反T。

规则(按优先级):
  R1. T仓额度铁律   — T仓 = 底仓市值 × 10%；T仓绝不能超过底仓(block)
  R2. 趋势方向      — 价 > MA20 → 正T(先买后卖)；价 < MA20 → 反T(先卖后买)
  R3. 频率上限      — 日内做T ≤ 2 次(佣金+印花税侵蚀利润)
  R4. 单次止损      — 单次T浮亏 ≥ 3% 强制认错离场(不恋战/不摊平)
  R5. 正T安全垫     — 买入价需低于持仓成本 2% 以上(防追高)
  R6. 时间节点      — 10:10 分时窗口(卡洗盘节奏做补涨)
  R7. T+1 合规      — 正T=卖旧买新(需已有底仓)；反T=先卖后买(当日可回补)
  R8. 情绪修正      — 自60日低点反弹≥10%强反弹时，价格微低于MA20(≤2%)视为
                      回踩买点改判正T(2026-08-12 用户指出单均线在强趋势中钝化)
  R9. VWAP 分时位   — 盘中分时 VWAP(成交量加权均价)；现价>VWAP=当日偏强(正T加力)，
                      <VWAP=偏弱(反T加力)；方向与VWAP背离时提示谨慎/降T仓(2026-08-12)
  R10. Pivot 价位   — 用昨日 H/L/C 算 P/S1/S2/R1/R2，输出「S1≈¥xx.xx 低吸 /
                      R1≈¥xx.xx 高抛」具体挂单价，替代"回踩MA20附近"无价提示(2026-08-12)
  R11. ATR 动态止损 — 止损线随波动率浮动 = ATR14(占价格比)，夹在[1.5%, 6%]；
                      低波动票收紧(3%→1.5~2%)、高波动票放宽(3%→4~6%)，
                      替代固定3%一刀切(2026-08-12, 来源=aifa-quant)

用法:
  python3 scripts/t0_strategy.py evaluate --code 600584 --ma20 78.0
  python3 scripts/t0_strategy.py evaluate --holding '{"code":"600584","shares":300,"avg_cost":84.2}' --price 77.67 --t-count 1
  python3 scripts/t0_strategy.py evaluate --code 600584 --ma20 78.0 --vwap 78.4 --prev-high 80.0 --prev-low 77.0 --prev-close 78.5
  python3 scripts/t0_strategy.py evaluate --code 600584 --ma20 78.0 --atr14 1.8   # 外部传入ATR14(可选)

设计原则: 纯函数式规则，不修改外部状态；网络失败降级为 None 不阻断。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, time
from pathlib import Path
from typing import Any

# ── 规则阈值常量 ────────────────────────────────────────────────
T_POSITION_RATIO = 0.10  # T仓 = 底仓市值 10%（笔记: 1000万拿100万）
LOT_SIZE = 100  # A股最小交易单位=1手=100股，且必须为100整数倍（2026-08-14 整手约束修复）
MAX_T_PER_DAY = 2  # 日内做T ≤2 次（合并指南"日≤2次"与笔记"分5次"→ 取严）
T_STOP_LOSS_PCT = 0.03  # 单次T浮亏 ≥3% 强制止损（笔记: 日内T亏3个点认错离场）
T_COST_SAFETY_PCT = 0.02  # 正T买入价需低于持仓成本 2% 以上（指南安全垫）
T_NODE_TIME = "10:10"  # 分时节点（笔记: 10:10 打时间差做补涨）
T_NODE_WINDOW_MIN = 10  # 节点窗口 ±10 分钟
# R8 情绪修正（2026-08-12 用户指出: 不能只看指标要看市场情绪）
STRONG_RALLY_PCT = 10.0  # 自60日低点反弹 ≥10%（百分比数值）→ 强反弹/强情绪
MA20_GAP_REVERSE = 0.02  # 价格低于MA20 超过 2% 才判反T（强反弹下微回踩≠破位）
# R9 VWAP 分时位（2026-08-12 落地，来源=stock_t_analyzer: 现价>VWAP偏强/偏弱）
VWAP_BIAS_PCT = 0.005  # 现价相对VWAP偏差 ≥0.5% 才判偏强/偏弱(否则视为中性区)
# R10 Pivot Point（2026-08-12 落地，来源=stock_t_analyzer 具体价位化）
PIVOT_SMA = 0.0  # 占位(经典 Pivot 无平滑因子)
# R11 ATR 动态止损（2026-08-12 落地，来源=aifa-quant: 固定3%低波动过宽/高波动过窄）
ATR_PERIOD = 14  # ATR14
ATR_STOP_FLOOR = 0.015  # 动态止损下限 1.5%（再紧会被日内噪音扫掉）
ATR_STOP_CAP = 0.06  # 动态止损上限 6%（防过度放宽失去保护）
# ATR 数据不可用(网络失败/bar不足)时回退固定3%止损，不阻断

PROJECT_ROOT = Path(__file__).resolve().parent.parent
USER_PORTFOLIO = PROJECT_ROOT / ".workbuddy" / "data" / "user" / "portfolio.json"


class T0Strategy:
    """做T（T+0）子策略规则引擎 — 纯函数式，只读不写"""

    def __init__(
        self,
        ratio: float = T_POSITION_RATIO,
        max_per_day: int = MAX_T_PER_DAY,
        stop_loss_pct: float = T_STOP_LOSS_PCT,
        cost_safety_pct: float = T_COST_SAFETY_PCT,
        node_time: str = T_NODE_TIME,
        strong_rally_pct: float = STRONG_RALLY_PCT,
        ma20_gap_reverse: float = MA20_GAP_REVERSE,
        vwap_bias_pct: float = VWAP_BIAS_PCT,
    ):
        self.ratio = ratio
        self.max_per_day = max_per_day
        self.stop_loss_pct = stop_loss_pct
        self.cost_safety_pct = cost_safety_pct
        self.node_time = node_time
        self.strong_rally_pct = strong_rally_pct
        self.ma20_gap_reverse = ma20_gap_reverse
        self.vwap_bias_pct = vwap_bias_pct

    # ════════════════════════════════════════════════════════════
    # 主入口: 对单个持仓生成做T建议
    # ════════════════════════════════════════════════════════════
    def evaluate(
        self,
        holding: dict,
        price: float | None = None,
        ma20: float | None = None,
        t_count_today: int = 0,
        t_pnl_pct: float | None = None,
        rally_pct: float | None = None,
        vwap: float | None = None,
        prev_bar: dict | None = None,
        atr14: float | None = None,
        now: datetime | None = None,
    ) -> dict:
        """生成做T建议。

        holding 需含: code / shares / avg_cost（可附 current_price / name）
        price/ma20 优先外部传入（盘中监控已有行情时复用，避免重复请求）。
        rally_pct: 自近60日低点反弹幅度%（市场情绪维度，2026-08-12 新增 R8）——
            强反弹(≥10%)下价格微低于MA20(≤2%)时视为回踩买点，改判正T，
            避免单均线在强趋势行情中钝化导致方向反判。
            注: 用「自低点反弹」而非「近20日涨幅」，V型反转下20日窗口会失真。
        vwap: 盘中分时 VWAP（R9）。外部传入优先（盘中监控复用），None 时自动
            拉腾讯分时接口，失败降级 None（R9 不生效，不阻断）。
        prev_bar: 昨日 {high, low, close}（R10）。外部传入优先，None 时自动
            拉日K取昨日 H/L/C，失败降级 None（R10 不生效，不阻断）。
        atr14: 14日真实波幅（R11 动态止损）。外部传入优先，None 时自动
            拉日K计算，失败降级 None → 回退固定3%止损（不阻断）。
        """
        code = holding.get("code", "")
        name = holding.get("name", "")
        shares = int(holding.get("shares", 0) or 0)
        avg_cost = holding.get("avg_cost") or 0
        price = price if price is not None else holding.get("current_price")
        now = now or datetime.now()

        result: dict[str, Any] = {
            "code": code,
            "name": name,
            "t0": False,
            "direction": None,
            "base_value": None,
            "t_position_value": None,
            "t_position_shares": None,
            "buy_below": None,
            "vwap": None,
            "vwap_note": "",
            "pivot": None,
            "atr14": None,
            "atr_stop_pct": None,
            "stop_loss_note": "",
            "plan": None,
            "flags": [],
            "blocked": False,
            "summary": "",
        }

        # R7 前置: 无底仓 → 做T无意义（A股T+1，正T必须卖旧买新）
        if shares <= 0:
            result["flags"].append(
                {
                    "level": "info",
                    "rule": "R7",
                    "reason": "无底仓，做T需已有持仓（A股T+1正T=卖旧买新），当前无法执行",
                }
            )
            result["summary"] = "无底仓，暂不做T"
            return result

        # 底仓市值（优先实时价，无则按成本）
        base_value = shares * (price if price is not None else avg_cost)
        result["base_value"] = round(base_value, 2)

        # R1: T仓额度 + 铁律（T仓绝不能超过底仓）
        t_value = base_value * self.ratio
        if t_value > base_value:
            result["blocked"] = True
            result["flags"].append(
                {
                    "level": "block",
                    "rule": "R1",
                    "reason": "🚫 T仓额度超过底仓市值，违反铁律（T仓绝不能超底仓），停止做T",
                }
            )
            result["summary"] = "🚫 T仓超底仓，违反铁律，停止做T"
            return result
        result["t_position_value"] = round(t_value, 2)  # T仓额度(底仓市值×ratio)
        # ── A股整手约束（最小1手=100股，且须为100整数倍）── 2026-08-14 修复
        # 按额度算理想股数 → 整手取整；不足1手时至少取1手(否则无法成交)；不超底仓。
        result["t_position_shares"] = None
        result["t_lot_cost"] = None
        if price and price > 0:
            raw_shares = t_value / price
            lot = int(round(raw_shares / LOT_SIZE)) * LOT_SIZE
            lot = max(lot, LOT_SIZE)  # 至少1手，否则无法成交
            max_lot = (shares // LOT_SIZE) * LOT_SIZE  # 底仓向下取整到整手
            lot = min(lot, max_lot)
            if lot >= LOT_SIZE:
                result["t_position_shares"] = lot
                result["t_lot_cost"] = round(lot * price, 2)
                # 整手导致占比高于1/10纪律时提示（小底仓不可避免，仅告知不拦截）
                if shares > 0 and lot / shares > self.ratio + 1e-9:
                    result["flags"].append(
                        {
                            "level": "info",
                            "rule": "LOT",
                            "reason": (
                                f"整手限制: T仓{lot}股(占底仓{lot / shares * 100:.0f}%)"
                                f"高于1/10纪律({self.ratio * 100:.0f}%)，"
                                f"因A股最小1手=100股已整手取整"
                            ),
                        }
                    )
            else:
                result["flags"].append(
                    {
                        "level": "info",
                        "rule": "LOT",
                        "reason": "底仓不足1手(100股)，无法做整手T交易",
                    }
                )

        # R3: 频率上限（日≤2次，防佣金侵蚀利润）
        if t_count_today >= self.max_per_day:
            result["blocked"] = True
            result["flags"].append(
                {
                    "level": "block",
                    "rule": "R3",
                    "reason": f"🚫 今日已做T {t_count_today} 次，达上限 {self.max_per_day} 次/日，停止做T",
                }
            )
            result["summary"] = f"🚫 今日已做T {t_count_today} 次，达上限，停止"
            return result

        # R11: ATR 动态止损线（低波动收紧/高波动放宽，夹在[1.5%,6%]）
        # 外部传入优先；自动拉取失败降级 → 回退固定3%，不阻断。
        effective_stop = self.stop_loss_pct
        if atr14 is None:
            atr14 = self._fetch_atr14(self._prefix(code))
        if atr14 and atr14 > 0 and price and price > 0:
            atr_pct = atr14 / price
            dynamic = max(ATR_STOP_FLOOR, min(ATR_STOP_CAP, atr_pct))
            effective_stop = dynamic
            result["atr14"] = round(atr14, 3)
            result["atr_stop_pct"] = round(dynamic, 4)
            anchor = "低波动收紧" if dynamic < self.stop_loss_pct else (
                "高波动放宽" if dynamic > self.stop_loss_pct else "持平"
            )
            result["stop_loss_note"] = (
                f"ATR动态止损{anchor}: {dynamic * 100:.1f}% (ATR14={atr14:.2f}, "
                f"占价{atr_pct * 100:.1f}%, 区间[{ATR_STOP_FLOOR * 100:.1f}%, {ATR_STOP_CAP * 100:.0f}%])"
            )

        # R4: 单次止损（按动态止损线，认错离场）
        if t_pnl_pct is not None and t_pnl_pct <= -effective_stop:
            result["blocked"] = True
            result["flags"].append(
                {
                    "level": "block",
                    "rule": "R4",
                    "reason": f"🚨 单次T浮亏 {t_pnl_pct * 100:.1f}% 已破 {effective_stop * 100:.1f}% 止损线"
                    f" → 强制认错离场，不恋战不摊平",
                }
            )
            result["summary"] = (
                f"🚨 单次T亏 {t_pnl_pct * 100:.1f}% 破 {effective_stop * 100:.1f}% 线，强制止损"
            )
            return result

        # R2+R8: 方向（20日线为主 + 情绪修正，2026-08-12）
        direction = None
        trend_note = ""
        sentiment_note = ""
        if price is not None and ma20 is not None:
            gap = (price - ma20) / ma20
            if gap > 0:
                direction = "正T"
                trend_note = f"价¥{price:.2f} > MA20¥{ma20:.2f}"
            elif gap < -self.ma20_gap_reverse:
                # 显著破位(>2%) → 反T
                direction = "反T"
                trend_note = (
                    f"价¥{price:.2f} 低于 MA20¥{ma20:.2f} 超{self.ma20_gap_reverse * 100:.0f}%"
                )
            elif rally_pct is not None and rally_pct >= self.strong_rally_pct:
                # R8 情绪修正: 强反弹(自60日低点≥10%) + 微回踩MA20(≤2%) → 顺势正T
                direction = "正T"
                sentiment_note = f"R8情绪修正: 自低点+{rally_pct:.1f}%强反弹, 微回踩MA20视为买点"
                trend_note = f"价¥{price:.2f} 微低于 MA20¥{ma20:.2f} ({gap * 100:+.1f}%)"
            else:
                direction = "反T"
                trend_note = f"价¥{price:.2f} 低于 MA20¥{ma20:.2f} 且无强反弹支撑"
        elif price is not None and avg_cost:
            direction = "正T" if price > avg_cost else "反T"
            trend_note = (
                f"价¥{price:.2f} {'>' if price > avg_cost else '<'} 成本¥{avg_cost:.2f}(无MA20降级)"
            )
        if direction is None:
            result["flags"].append(
                {"level": "info", "rule": "R2", "reason": "缺价格/MA20，无法判断趋势方向，暂不做T"}
            )
            result["summary"] = "数据不足，暂不做T"
            return result
        result["direction"] = direction
        result["t0"] = True
        result["sentiment_note"] = sentiment_note

        # R9: VWAP 分时位（现价>VWAP=偏强正T加力 / <VWAP=偏弱反T加力；背离提示谨慎）
        vwap_note = ""
        if price is not None:
            if vwap is None:
                vwap = self._fetch_vwap(self._prefix(code))
            if vwap and vwap > 0:
                result["vwap"] = round(vwap, 2)
                gap = (price - vwap) / vwap
                if gap >= self.vwap_bias_pct:
                    vwap_note = f"现价¥{price:.2f} > VWAP¥{vwap:.2f}(+{gap * 100:.1f}%) 当日偏强"
                    if direction == "正T":
                        vwap_note += " → 正T加力"
                    else:
                        vwap_note += " → 与反T方向背离，反T需谨慎(冲高不追空)"
                elif gap <= -self.vwap_bias_pct:
                    vwap_note = f"现价¥{price:.2f} < VWAP¥{vwap:.2f}({gap * 100:.1f}%) 当日偏弱"
                    if direction == "反T":
                        vwap_note += " → 反T加力"
                    else:
                        vwap_note += " → 与正T方向背离，正T需谨慎(弱市不抄底)"
                else:
                    vwap_note = f"现价¥{price:.2f} ≈ VWAP¥{vwap:.2f}(±{gap * 100:+.1f}%) 中性区"
        result["vwap_note"] = vwap_note

        # R10: Pivot Point 具体价位（用昨日 H/L/C → P/S1/S2/R1/R2）
        pivot = None
        if prev_bar is None:
            prev_bar = self._fetch_prev_bar(self._prefix(code))
        if prev_bar and prev_bar.get("high") and prev_bar.get("low") and prev_bar.get("close"):
            pivot = self._calc_pivot(prev_bar["high"], prev_bar["low"], prev_bar["close"])
        result["pivot"] = pivot

        # R5: 正T安全垫（买入价 ≤ 持仓成本 × (1-2%)）
        buy_below = None
        if direction == "正T" and avg_cost:
            buy_below = round(avg_cost * (1 - self.cost_safety_pct), 2)

        # R6: 10:10 节点窗口提示
        node_hint = None
        if now and self._in_node_window(now):
            node_hint = f"⏰ 当前处于 {self.node_time} 分时节点窗口（±{T_NODE_WINDOW_MIN}分钟），卡洗盘节奏可做补涨"

        # 行动计划
        if direction == "正T":
            s1_text = f" / S1≈¥{pivot['S1']:.2f} 低吸" if pivot and pivot.get("S1") else ""
            entry_rule = (
                f"买入价需 ≤ 持仓成本×{1 - self.cost_safety_pct:.2f} 即 ≤¥{buy_below}（安全垫{self.cost_safety_pct * 100:.0f}%）"
                f"{s1_text}"
                if buy_below
                else "买入需低于持仓成本2%"
            )
            plan = {
                "action": "正T：先买后卖（T+1下买入的是新仓，卖出的是昨日底仓）",
                "entry_rule": entry_rule,
                "exit_rule": "反弹后卖出等量原底仓，T仓当日不做第二次"
                + (
                    f"；上方 R1≈¥{pivot['R1']:.2f} 压力位可高抛"
                    if pivot and pivot.get("R1")
                    else ""
                ),
            }
        else:
            r1_text = f" / R1≈¥{pivot['R1']:.2f} 高抛" if pivot and pivot.get("R1") else ""
            plan = {
                "action": "反T：先卖后买（卖的是已有底仓，当日可回补，合规）",
                "entry_rule": f"冲高遇阻（分时双顶）时卖出{r1_text}",
                "exit_rule": (
                    f"回落至支撑位回补同量，锁定差价；下方 S1≈¥{pivot['S1']:.2f} 可低吸回补"
                    if pivot and pivot.get("S1")
                    else "回落至支撑位回补同量，锁定差价"
                ),
            }
        result["plan"] = plan
        result["buy_below"] = buy_below

        # 汇总可读文本（供卡片）
        parts = [f"{name or code} {direction}"]
        if result.get("t_position_shares"):
            parts.append(
                f"T仓{result['t_position_shares']}股(¥{result['t_lot_cost']:.0f}, "
                f"占底仓{result['t_position_shares'] / shares * 100:.0f}%)"
            )
        else:
            parts.append(f"T仓额度¥{result['t_position_value']:.0f}(底仓{self.ratio * 100:.0f}%)")
        if buy_below:
            parts.append(f"买入≤¥{buy_below}")
        if vwap_note:
            parts.append(vwap_note)
        if result["stop_loss_note"]:
            parts.append(result["stop_loss_note"])
        if pivot:
            parts.append(f"Pivot: S1¥{pivot['S1']:.2f}/P¥{pivot['P']:.2f}/R1¥{pivot['R1']:.2f}")
        if node_hint:
            parts.append(node_hint)
        if sentiment_note:
            parts.append(sentiment_note)
        result["summary"] = " | ".join(parts)

        if not result["flags"]:
            result["flags"].append(
                {
                    "level": "info",
                    "rule": "G",
                    "reason": result["summary"],
                }
            )
        return result

    # ── 辅助 ────────────────────────────────────────────────────
    def _in_node_window(self, now: datetime) -> bool:
        """当前时刻是否处于 10:10 节点窗口（±10分钟）"""
        try:
            hh, mm = self.node_time.split(":")
            node = time(int(hh), int(mm))
        except (ValueError, AttributeError):
            return False
        cur = now.time().replace(microsecond=0)
        delta = (
            datetime.combine(now.date(), cur) - datetime.combine(now.date(), node)
        ).total_seconds()
        return abs(delta) <= T_NODE_WINDOW_MIN * 60

    @staticmethod
    def _prefix(code: str) -> str:
        """标准化为带市场前缀的代码（sh/sz）"""
        code = code.strip().lower()
        if code.startswith(("sh", "sz")):
            return code
        return f"sh{code}" if code.startswith("6") else f"sz{code}"

    def _fetch_vwap(self, code_prefixed: str) -> float | None:
        """盘中分时 VWAP（成交量加权均价）。腾讯分时接口(铁律)，失败降级 None。

        腾讯分时 data.data.data 每行为空格分隔字符串:
          "0930 77.10 7043 54301530.10" = [时间, 价格, 累计成交量(手), 累计成交额(元)]
        VWAP = 当前累计成交额 / 当前累计成交量(手×100股)。
        """
        try:
            url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code_prefixed}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310: https 硬编码
                d = json.loads(resp.read().decode("utf-8"))
            sub = d.get("data", {}).get(code_prefixed, {})
            rows = (sub.get("data") or {}).get("data", [])
            if not rows:
                return None
            # 末条为当日累计
            last = rows[-1]
            parts = last.split() if isinstance(last, str) else [str(x) for x in last]
            if len(parts) >= 4:
                amount = float(parts[3]) if parts[3] else 0.0
                volume_shares = float(parts[2]) * 100 if parts[2] else 0.0
                if amount > 0 and volume_shares > 0:
                    return amount / volume_shares
        except Exception:
            pass
        return None

    def _fetch_prev_bar(self, code_prefixed: str) -> dict | None:
        """昨日 K 线 {high, low, close}。腾讯 fqkline 优先，接口不可用时降级新浪。

        盘中需昨日 H/L/C 算 Pivot；当日 bar 未走完不能用，取倒数第2根。
        """
        # 1) 腾讯 fqkline 前复权
        try:
            url = (
                f"https://web.ifzq.gtimg.cn/appstuff/app/fqkline/get"
                f"?param={code_prefixed},day,,,10,qfq"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310: https 硬编码
                d = json.loads(resp.read().decode("utf-8"))
            sub = d.get("data", {})
            if isinstance(sub, dict) and d.get("code") != 11:
                kl = sub.get(code_prefixed, {}).get("qfqday", [])
                if len(kl) >= 2:
                    prev = kl[-2]
                    # qfqday 行: [日期, 开, 收, 高, 低, 量, ...]
                    if len(prev) >= 5:
                        return {
                            "high": float(prev[3]),
                            "low": float(prev[4]),
                            "close": float(prev[2]),
                        }
        except Exception:
            pass
        # 2) 新浪日K（fqkline 接口 2026-08-12 起返回 code=11 时降级）
        try:
            url = (
                f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php"
                f"/CN_MarketData.getKLineData?symbol={code_prefixed}&scale=240&ma=no&datalen=5"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310: https 硬编码
                arr = json.loads(resp.read().decode("utf-8"))
            if len(arr) >= 2:
                prev = arr[-2]
                if prev.get("high") and prev.get("low") and prev.get("close"):
                    return {
                        "high": float(prev["high"]),
                        "low": float(prev["low"]),
                        "close": float(prev["close"]),
                    }
        except Exception:
            pass
        return None

    def _fetch_atr14(self, code_prefixed: str) -> float | None:
        """拉日K计算 ATR14（R11 动态止损）。腾讯 fqkline 优先，失败降级新浪。

        至少需 ATR_PERIOD+1 根K线算 TR 序列；当日 bar 未走完不能用。
        """
        bars: list[tuple[float, float, float]] = []  # (high, low, close) 按时间升序
        # 1) 腾讯 fqkline 前复权
        try:
            url = (
                f"https://web.ifzq.gtimg.cn/appstuff/app/fqkline/get"
                f"?param={code_prefixed},day,,,{ATR_PERIOD + 5},qfq"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
                d = json.loads(resp.read().decode("utf-8"))
            sub = d.get("data", {})
            if isinstance(sub, dict) and d.get("code") != 11:
                kl = sub.get(code_prefixed, {}).get("qfqday", [])
                # qfqday 行: [日期, 开, 收, 高, 低, 量, ...]
                bars = [(float(b[3]), float(b[4]), float(b[2])) for b in kl if len(b) >= 5]
        except Exception:
            pass
        # 2) 新浪日K 降级
        if len(bars) < ATR_PERIOD + 1:
            try:
                url = (
                    f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php"
                    f"/CN_MarketData.getKLineData?symbol={code_prefixed}"
                    f"&scale=240&ma=no&datalen={ATR_PERIOD + 5}"
                )
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
                    arr = json.loads(resp.read().decode("utf-8"))
                bars = [
                    (float(b["high"]), float(b["low"]), float(b["close"]))
                    for b in arr
                    if b.get("high") and b.get("low") and b.get("close")
                ]
            except Exception:
                pass
        if len(bars) < ATR_PERIOD + 1:
            return None
        # 去掉当日未走完 bar，取最近 ATR_PERIOD+1 根
        bars = bars[-(ATR_PERIOD + 1):]
        return self._calc_atr14(bars)

    @staticmethod
    def _calc_atr14(bars: list[tuple[float, float, float]]) -> float | None:
        """ATR14 = TR 的14日简单平均。TR = max(H-L, |H-Cprev|, |L-Cprev|)。

        bars: [(high, low, close), ...] 按时间升序，需 ≥15 根。
        """
        if len(bars) < ATR_PERIOD + 1:
            return None
        trs: list[float] = []
        for i in range(1, len(bars)):
            h, l, c = bars[i]
            prev_c = bars[i - 1][2]
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            trs.append(tr)
        return round(sum(trs[-ATR_PERIOD:]) / ATR_PERIOD, 4)

    @staticmethod
    def _calc_pivot(high: float, low: float, close: float) -> dict:
        """经典 Pivot Point（R10）: P/S1/S2/R1/R2。

        P  = (H + L + C) / 3
        R1 = 2P - L ; S1 = 2P - H
        R2 = P + (H - L) ; S2 = P - (H - L)
        """
        p = (high + low + close) / 3
        r1 = 2 * p - low
        s1 = 2 * p - high
        r2 = p + (high - low)
        s2 = p - (high - low)
        return {
            "P": round(p, 2),
            "R1": round(r1, 2),
            "S1": round(s1, 2),
            "R2": round(r2, 2),
            "S2": round(s2, 2),
        }

    @staticmethod
    def load_portfolio(path: Path = USER_PORTFOLIO) -> dict:
        """读取持仓文件（只读，失败返回空）"""
        if not path.exists():
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)  # type: ignore[no-any-return]
        except (OSError, json.JSONDecodeError):
            return {}

    def find_holding(self, code: str, path: Path = USER_PORTFOLIO) -> dict | None:
        """按代码查持仓（供选股/盘中复用）"""
        data = self.load_portfolio(path)
        for h in data.get("holdings", []):
            if h.get("code") == code:
                return h
        return None


# ── CLI 入口 ────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="做T（T+0）子策略引擎")
    sub = parser.add_subparsers(dest="cmd")

    p_eval = sub.add_parser("evaluate", help="对持仓生成做T建议")
    p_eval.add_argument("--code", required=True, help="股票代码(6位)")
    p_eval.add_argument("--holding", default=None, help="持仓JSON字符串(可选，缺省读portfolio)")
    p_eval.add_argument("--portfolio", default=str(USER_PORTFOLIO), help="portfolio.json路径")
    p_eval.add_argument("--price", type=float, default=None, help="实时价(可选)")
    p_eval.add_argument("--ma20", type=float, default=None, help="MA20(可选)")
    p_eval.add_argument("--t-count", type=int, default=0, help="今日已做T次数")
    p_eval.add_argument("--t-pnl", type=float, default=None, help="当前单次T浮亏比例(如-0.02)")
    p_eval.add_argument("--vwap", type=float, default=None, help="盘中VWAP(可选,缺省自动拉)")
    p_eval.add_argument("--prev-high", type=float, default=None, help="昨日最高价(可选,缺省自动拉)")
    p_eval.add_argument("--prev-low", type=float, default=None, help="昨日最低价(可选,缺省自动拉)")
    p_eval.add_argument(
        "--prev-close", type=float, default=None, help="昨日收盘价(可选,缺省自动拉)"
    )
    p_eval.add_argument("--atr14", type=float, default=None, help="14日ATR(可选,缺省自动拉)")

    args = parser.parse_args()

    strategy = T0Strategy()
    if args.cmd == "evaluate":
        holding = None
        if args.holding:
            holding = json.loads(args.holding)
        else:
            holding = strategy.find_holding(args.code, Path(args.portfolio))
        if not holding:
            print(
                json.dumps(
                    {
                        "code": args.code,
                        "t0": False,
                        "blocked": False,
                        "flags": [
                            {"level": "info", "rule": "R7", "reason": "无此标的持仓，做T需已有底仓"}
                        ],
                        "summary": "无此标的持仓，无法做T",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        result = strategy.evaluate(
            holding,
            price=args.price,
            ma20=args.ma20,
            t_count_today=args.t_count,
            t_pnl_pct=args.t_pnl,
            vwap=args.vwap,
            prev_bar=(
                {
                    "high": args.prev_high,
                    "low": args.prev_low,
                    "close": args.prev_close,
                }
                if args.prev_high is not None
                or args.prev_low is not None
                or args.prev_close is not None
                else None
            ),
            atr14=args.atr14,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
