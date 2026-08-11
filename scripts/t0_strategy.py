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

用法:
  python3 scripts/t0_strategy.py evaluate --code 600584 --ma20 78.0
  python3 scripts/t0_strategy.py evaluate --holding '{"code":"600584","shares":300,"avg_cost":84.2}' --price 77.67 --t-count 1

设计原则: 纯函数式规则，不修改外部状态；网络失败降级为 None 不阻断。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, time
from pathlib import Path
from typing import Any

# ── 规则阈值常量 ────────────────────────────────────────────────
T_POSITION_RATIO = 0.10  # T仓 = 底仓市值 10%（笔记: 1000万拿100万）
MAX_T_PER_DAY = 2  # 日内做T ≤2 次（合并指南"日≤2次"与笔记"分5次"→ 取严）
T_STOP_LOSS_PCT = 0.03  # 单次T浮亏 ≥3% 强制止损（笔记: 日内T亏3个点认错离场）
T_COST_SAFETY_PCT = 0.02  # 正T买入价需低于持仓成本 2% 以上（指南安全垫）
T_NODE_TIME = "10:10"  # 分时节点（笔记: 10:10 打时间差做补涨）
T_NODE_WINDOW_MIN = 10  # 节点窗口 ±10 分钟

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
    ):
        self.ratio = ratio
        self.max_per_day = max_per_day
        self.stop_loss_pct = stop_loss_pct
        self.cost_safety_pct = cost_safety_pct
        self.node_time = node_time

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
        now: datetime | None = None,
    ) -> dict:
        """生成做T建议。

        holding 需含: code / shares / avg_cost（可附 current_price / name）
        price/ma20 优先外部传入（盘中监控已有行情时复用，避免重复请求）。
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
        result["t_position_value"] = round(t_value, 2)
        result["t_position_shares"] = round(t_value / price) if price else None

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

        # R4: 单次止损（≥3% 强制认错离场）
        if t_pnl_pct is not None and t_pnl_pct <= -self.stop_loss_pct:
            result["blocked"] = True
            result["flags"].append(
                {
                    "level": "block",
                    "rule": "R4",
                    "reason": f"🚨 单次T浮亏 {t_pnl_pct * 100:.1f}% 已破 {self.stop_loss_pct * 100:.0f}% 止损线"
                    f" → 强制认错离场，不恋战不摊平",
                }
            )
            result["summary"] = (
                f"🚨 单次T亏 {t_pnl_pct * 100:.1f}% 破 {self.stop_loss_pct * 100:.0f}% 线，强制止损"
            )
            return result

        # R2: 方向（20日线优先，降级用持仓成本）
        direction = None
        trend_note = ""
        if price is not None and ma20 is not None:
            direction = "正T" if price > ma20 else "反T"
            trend_note = f"价¥{price:.2f} {'>' if price > ma20 else '<'} MA20¥{ma20:.2f}"
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
            plan = {
                "action": "正T：先买后卖（T+1下买入的是新仓，卖出的是昨日底仓）",
                "entry_rule": f"买入价需 ≤ 持仓成本×{1 - self.cost_safety_pct:.2f} 即 ≤¥{buy_below}（安全垫{self.cost_safety_pct * 100:.0f}%）"
                if buy_below
                else "买入需低于持仓成本2%",
                "exit_rule": "反弹后卖出等量原底仓，T仓当日不做第二次",
            }
        else:
            plan = {
                "action": "反T：先卖后买（卖的是已有底仓，当日可回补，合规）",
                "entry_rule": "冲高遇阻（分时双顶）时卖出",
                "exit_rule": "回落至支撑位回补同量，锁定差价",
            }
        result["plan"] = plan
        result["buy_below"] = buy_below

        # 汇总可读文本（供卡片）
        parts = [f"{name or code} {direction}"]
        parts.append(f"T仓额度¥{result['t_position_value']:.0f}(底仓{self.ratio * 100:.0f}%)")
        if buy_below:
            parts.append(f"买入≤¥{buy_below}")
        if node_hint:
            parts.append(node_hint)
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
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
