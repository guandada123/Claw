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
  - calc_rsi.py (RSI14, 腾讯 ifzq 前复权)
  - 腾讯 qt.gtimg.cn (实时行情/MA20 用日K近似)
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

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CALC_RSI = PROJECT_ROOT / "scripts" / "calc_rsi.py"
USER_PORTFOLIO = PROJECT_ROOT / ".workbuddy" / "data" / "user" / "portfolio.json"

# ── 规则阈值常量 ────────────────────────────────────────────────
RSI_OVERBOUGHT = 70          # RSI(14) 超买线
DAY_GAIN_WARN = 5.0          # 当日涨幅预警线(%)
DOUBLE_ACCT_PCT = 0.33       # 双账户同标合计占比上限
REBUY_COOLING_DAYS = 30      # 30天内≥2次亏卖 → 冷却
REBUY_DCA_DAYS = 7           # 7天内≥3次买 → 摊薄标记
DCA_WARN_COUNT = 3
T3_LOCK_PROFIT_DAYS = 3      # 持仓≥3天且浮盈 → 锁利建议
T5_REVIEW_DAYS = 5            # 持仓≥5天 → 每日复盘
T7_STOPLOSS_DAYS = 7         # 持仓≥7天且回撤≥-8% → 紧急减仓
RISK_REWARD_MIN = 1.5        # 盈亏比下限

# 默认止损/止盈（与 portfolio.json rules 一致）
DEFAULT_STOP_LOSS = -0.08
DEFAULT_TAKE_PROFIT = 0.05    # +5% 作为短线目标


class AdvisorRules:
    """炒股助理纪律规则引擎"""

    def __init__(self, stop_loss: float = DEFAULT_STOP_LOSS,
                 take_profit: float = DEFAULT_TAKE_PROFIT):
        self.stop_loss = stop_loss
        self.take_profit = take_profit

    # ════════════════════════════════════════════════════════════
    # 规则 E: 入场价过滤器（选股推荐前调用）
    # ════════════════════════════════════════════════════════════
    def check_entry(self, code: str, price: float | None = None) -> dict:
        """检查标的当前是否适合入场。

        Returns:
            {
              "code": "...",
              "blocked": bool,          # True=暂缓推荐
              "flags": [{"level": "warn|block", "reason": "..."}],
              "suggested_buy_zone": str, # 推荐等待的买区
              "rsi14": float|None,
              "ma20": float|None,
              "day_change_pct": float|None
            }
        """
        code_prefixed = self._prefix(code)
        rsi = self._get_rsi(code_prefixed)
        day_change = self._get_day_change(code_prefixed)
        ma20 = self._get_ma20(code_prefixed)

        flags = []
        blocked = False

        # E1: RSI 超买
        if rsi is not None and rsi > RSI_OVERBOUGHT:
            blocked = True
            flags.append({
                "level": "block",
                "reason": f"⚠️ RSI(14)={rsi:.1f} 超买区(>70)，追高风险大，建议等回调"
            })

        # E2: 高于 MA20
        if ma20 is not None and price is not None and price > ma20 * 1.02:
            flags.append({
                "level": "warn",
                "reason": f"⚠️ 现价 ¥{price:.2f} 高于 MA20 ¥{ma20:.2f}（+{(price/ma20-1)*100:.1f}%），无安全垫"
            })
        elif ma20 is not None and price is not None and price > ma20:
            flags.append({
                "level": "warn",
                "reason": f"⚠️ 现价 ¥{price:.2f} 略高于 MA20 ¥{ma20:.2f}，注意追高"
            })

        # E3: 当日涨幅过大
        if day_change is not None and day_change > DAY_GAIN_WARN:
            blocked = True
            flags.append({
                "level": "block",
                "reason": f"⚠️ 当日涨幅 {day_change:+.1f}% 已超 {DAY_GAIN_WARN}%，暂缓推荐，等回落"
            })

        # 推荐买区
        suggested = self._suggest_buy_zone(price, ma20, rsi)

        return {
            "code": code,
            "blocked": blocked,
            "flags": flags,
            "suggested_buy_zone": suggested,
            "rsi14": rsi,
            "ma20": ma20,
            "day_change_pct": day_change,
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
            return f"建议等待 RSI 回落至 <60 且价格回踩 ¥{price*0.95:.2f} 以下"
        return f"建议等待回调至 ¥{price*0.95:.2f} 附近"

    # ════════════════════════════════════════════════════════════
    # 规则 A: T+3 强制决策（盘中持仓诊断）
    # ════════════════════════════════════════════════════════════
    def check_timing(self, holding: dict, today: date | None = None) -> list[dict]:
        """根据持仓天数 + 浮盈亏生成决策建议

        holding 需含: bought_date(ISO str) / avg_cost / current_price / shares
        """
        flags = []
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
            flags.append({
                "level": "warn",
                "rule": "A",
                "reason": f"📌 持仓 {days}天 浮盈 {pnl_pct*100:+.1f}% → 建议锁利（T+3短线目标已达成）"
            })

        # A2: 持仓≥5天 → 每日复盘
        if days >= T5_REVIEW_DAYS:
            flags.append({
                "level": "info",
                "rule": "A",
                "reason": f"📌 持仓 {days}天 超短线周期 → 每日到期复盘：达到预期？未达则减仓"
            })

        # A3: 持仓≥7天且回撤≥-8% → 紧急减仓
        if days >= T7_STOPLOSS_DAYS and pnl_pct <= self.stop_loss:
            flags.append({
                "level": "block",
                "rule": "A",
                "reason": f"🚨 持仓 {days}天 回撤 {pnl_pct*100:+.1f}% 破止损线(-8%) → 紧急减仓"
            })

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

        combined_value = sum(
            h.get("shares", 0) * h.get("avg_cost", 0) for h in matched
        )
        pct = combined_value / total_value
        return {
            "double_account": True,
            "brokers": list(brokers),
            "combined_pct": round(pct, 4),
            "over_limit": pct > DOUBLE_ACCT_PCT,
            "warn": f"⚠️ 双账户合计占比 {pct*100:.1f}% > {DOUBLE_ACCT_PCT*100:.0f}% 上限"
                    if pct > DOUBLE_ACCT_PCT else
                    f"双账户同持 {list(brokers)}，占比 {pct*100:.1f}%",
        }

    # ════════════════════════════════════════════════════════════
    # 规则 B: 禁止重复抄底闸门
    # ════════════════════════════════════════════════════════════
    def check_rebuy_gate(self, code: str, trade_log: list[dict],
                         today: date | None = None) -> dict:
        """根据交易历史判断是否触发抄底闸门

        trade_log: [{"date": "2026-07-01", "side": "buy"|"sell", "pnl": float|None}]
        """
        today = today or date.today()
        recent = [
            t for t in trade_log
            if (today - datetime.strptime(t["date"], "%Y-%m-%d").date()).days
            <= max(REBUY_COOLING_DAYS, REBUY_DCA_DAYS)
        ]
        if not recent:
            return {"triggered": False, "reason": "近期无同标交易"}

        loss_sells = [t for t in recent if t.get("side") == "sell" and (t.get("pnl") or 0) < 0]
        buys = [t for t in recent if t.get("side") == "buy"]

        result = {"triggered": False, "reasons": []}

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
    def risk_reward_card(self, entry_price: float, stop_loss_pct: float = DEFAULT_STOP_LOSS,
                         take_profit_pct: float = DEFAULT_TAKE_PROFIT) -> dict:
        """生成盈亏比预演卡片"""
        stop_price = entry_price * (1 + stop_loss_pct)
        target_price = entry_price * (1 + take_profit_pct)
        rr = abs(take_profit_pct / stop_loss_pct) if stop_loss_pct != 0 else None

        card = {
            "entry": entry_price,
            "stop_loss_price": round(stop_price, 2),
            "take_profit_price": round(target_price, 2),
            "risk_reward_ratio": round(rr, 2) if rr else None,
            "verdict": "风险收益良好" if (rr and rr >= RISK_REWARD_MIN) else "风险收益不佳",
        }
        if rr and rr < RISK_REWARD_MIN:
            card["warn"] = f"⚠️ 盈亏比 {rr:.2f}:1 < {RISK_REWARD_MIN}:1，风险收益不划算"
        return card

    # ════════════════════════════════════════════════════════════
    # 组合诊断（盘中监控主入口）
    # ════════════════════════════════════════════════════════════
    def diagnose_holding(self, holding: dict, quotes: dict | None = None,
                         trade_log: list[dict] | None = None,
                         today: date | None = None) -> dict:
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

        return {
            "code": code,
            "name": holding.get("name", ""),
            "flags": flags,
            "risk_reward": rr_card,
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
        try:
            out = subprocess.run(
                [sys.executable, str(CALC_RSI), code_prefixed],
                capture_output=True, text=True, timeout=30
            )
            # 解析 JSON 行
            for line in out.stdout.splitlines():
                if line.startswith("JSON:"):
                    data = json.loads(line[5:].strip())
                    if data and data[0].get("rsi14") is not None:
                        return float(data[0]["rsi14"])
            # 退化解析: sh600522: RSI(14)=37.4
            for line in out.stdout.splitlines():
                if "RSI(14)=" in line:
                    return float(line.split("=")[1].strip())
        except Exception:
            pass
        return None

    def _get_day_change(self, code_prefixed: str) -> float | None:
        try:
            url = f"https://qt.gtimg.cn/q={code_prefixed}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310: URL scheme is hardcoded https
                text = resp.read().decode("gbk", errors="replace")
            # 字段 [32] = 涨跌幅%
            vals = text.split('"')[1].split("~")
            if len(vals) > 32:
                return float(vals[32])
        except Exception:
            pass
        return None

    def _get_live_price(self, code_prefixed: str) -> dict | None:
        """拉取实时价（price + change_pct），供 diagnose 自动注入"""
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
        """拉日K收盘价算 MA20（优先腾讯ifzq前复权，失败回退新浪）"""
        # 1) 腾讯 ifzq 前复权
        try:
            url = (f"https://web.ifzq.gtimg.cn/appstuff/app/fqkline/get"
                   f"?param={code_prefixed},day,,,60,qfq")
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
        # 2) 新浪回退
        try:
            url = (f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php"
                   f"/CN_MarketData.getKLineData?symbol={code_prefixed}&scale=240&ma=no&datalen=60")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310: URL scheme is hardcoded https
                arr = json.loads(resp.read().decode("utf-8"))
            closes = [float(row["close"]) for row in arr if row.get("close")]
            if len(closes) >= 20:
                return sum(closes[-20:]) / len(closes[-20:])
        except Exception:
            pass
        return None

    @staticmethod
    def _load_json(path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}


# ── CLI 入口 ────────────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(description="炒股助理纪律规则引擎")
    sub = parser.add_subparsers(dest="cmd")

    p_entry = sub.add_parser("check-entry", help="入场价过滤(规则E)")
    p_entry.add_argument("--code", required=True)
    p_entry.add_argument("--price", type=float, default=None)

    p_diag = sub.add_parser("diagnose", help="持仓全规则诊断(A/C/B/D)")
    p_diag.add_argument("--portfolio", default=str(USER_PORTFOLIO))
    p_diag.add_argument("--code", default=None, help="只诊断指定代码")

    args = parser.parse_args()
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
