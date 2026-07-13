"""Wind 万得高级监控 — 利用 Wind 高级分析能力增强持仓监控

功能:
  1. 技术指标监控（MACD 形态、RSI 超买超卖）
  2. 财经新闻聚合（持仓股最新动态）
  3. 风险指标快照（Beta/波动率）
  4. 选股筛选（按条件发现机会）

依赖 Wind CLI 和 API Key（参见 docs/wind-integration.md）。

用法:
    python -m claw.monitoring.wind_monitor                # 默认：持仓技术+新闻
    python -m claw.monitoring.wind_monitor --holdings      # 仅持仓监控
    python -m claw.monitoring.wind_monitor --technical     # 仅技术指标
    python -m claw.monitoring.wind_monitor --news          # 仅新闻
    python -m claw.monitoring.wind_monitor --risk          # 仅风险指标
    python -m claw.monitoring.wind_monitor --screening     # 条件选股
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from claw.feeds.wind_analytics import WindAnalytics

PROJECT_ROOT = Path(os.environ.get(
    "CLAW_PROJECT_ROOT",
    str(Path(__file__).resolve().parent.parent.parent.parent)
))
PORTFOLIO_FILE = PROJECT_ROOT / ".workbuddy" / "data" / "portfolio.json"


def _load_holdings() -> dict[str, str]:
    """从统一持仓文件加载持仓列表"""
    if PORTFOLIO_FILE.exists():
        try:
            data = json.loads(PORTFOLIO_FILE.read_text())
            positions = data.get("positions", data.get("stocks", []))
            if positions:
                return {
                    p.get("code", p.get("symbol", "")): p.get("name", "") or p.get("code", "")
                    for p in positions
                    if p.get("code") or p.get("symbol")
                }
        except (json.JSONDecodeError, OSError):
            pass
    # 降级到硬编码（兜底）
    return {
        "600522": "中天科技",
        "600206": "有研新材",
        "000021": "深科技",
        "000636": "风华高科",
        "600584": "长电科技",
    }


# 持仓列表
HOLDINGS: dict[str, str] = _load_holdings()


def _fmt(v, decimals: int = 2) -> str:
    """格式化数值"""
    if v is None:
        return "N/A"
    return f"{v:.{decimals}f}"


def monitor_technical():
    """技术指标监控：MACD 趋势 + RSI（并行查询加速）"""

    codes = list(HOLDINGS.keys())

    def _fetch_one(wa, code):
        """每线程独立实例，避免 subprocess 竞争"""
        name = HOLDINGS[code]
        macd_data = wa.get_technicals(code, "近60日MACD走势")
        rsi_data = wa.get_technicals(code, "近60日RSI")

        macd_val, macd_trend = None, "?"
        if macd_data and len(macd_data) >= 2:
            lt, pv = macd_data[-1], macd_data[-2]
            macd_key = next((k for k in lt if "MACD" in k), None)
            prev_key = next((k for k in pv if "MACD" in k), None)
            if macd_key and prev_key:
                cur, prv = lt[macd_key], pv[prev_key]
                if isinstance(cur, (int, float)) and isinstance(prv, (int, float)):
                    macd_val, macd_trend = round(cur, 2), "↑" if cur > prv else "↓"

        rsi_val, rsi_signal = None, ""
        if rsi_data and len(rsi_data) >= 1:
            row = rsi_data[-1]
            for ck in ["近60日每日RSI_相对强弱指标", "60日RSI相对强弱指标", "RSI相对强弱指标"]:
                v = row.get(ck)
                if v is not None and isinstance(v, (int, float)):
                    rsi_val = v
                    if rsi_val > 70:
                        rsi_signal = "⚠️ 超买"
                    elif rsi_val < 30:
                        rsi_signal = "⚠️ 超卖"
                    else:
                        rsi_signal = "正常"
                    break

        return code, name, macd_val, macd_trend, rsi_val, rsi_signal

    with ThreadPoolExecutor(max_workers=min(5, len(codes))) as pool:
        futures = []
        for code in codes:
            wa = WindAnalytics()
            futures.append(pool.submit(_fetch_one, wa, code))
        for f in as_completed(futures):
            code, name, macd_val, macd_trend, rsi_val, rsi_signal = f.result()


def monitor_news(top_k: int = 3):
    """财经新闻监控：持仓股最新动态"""

    def _fetch_one(wa, code):
        name = HOLDINGS[code]
        news = wa.get_news(name, top_k=top_k)
        return code, name, news

    with ThreadPoolExecutor(max_workers=min(5, len(HOLDINGS))) as pool:
        futures = []
        for code, name in HOLDINGS.items():
            wa = WindAnalytics()
            futures.append(pool.submit(_fetch_one, wa, code))
        for f in as_completed(futures):
            code, name, news = f.result()
            if news:
                for n in news:
                    title = n.get("title", "?")[:55]
                    date = n.get("date", "?")
            else:
                pass


def monitor_risk():
    """风险指标快照"""

    def _fetch_one(wa, code):
        name = HOLDINGS[code]
        risk = wa.get_risk_metrics(code, "过去1年Beta和波动率")
        if risk and len(risk) >= 1:
            r = risk[0]
            beta = None
            for bk in ["过去1年BETA", "过去1年Beta", "过去1年年化Beta"]:
                beta = r.get(bk)
                if beta is not None:
                    break
            vol = None
            for vk in ["过去1年波动率", "过去1年年化波动率", "过去1年Volatility"]:
                vol = r.get(vk)
                if vol is not None:
                    break
            return code, name, beta, vol
        return code, name, None, None

    with ThreadPoolExecutor(max_workers=min(5, len(HOLDINGS))) as pool:
        futures = []
        for code in HOLDINGS:
            wa = WindAnalytics()
            futures.append(pool.submit(_fetch_one, wa, code))
        for f in as_completed(futures):
            code, name, beta, vol = f.result()
            if beta is not None:
                pass
            else:
                pass


def run_screening(wa: WindAnalytics):
    """条件选股：发现潜在机会"""

    conditions = [
        ("沪深市场市值超500亿且连续3日上涨", "大盘企稳"),
        ("沪深市场MACD金叉且市值超100亿", "技术突破"),
        ("沪深市场RSI低于30且成交放量", "超卖反弹"),
    ]

    for condition, label in conditions:
        stocks = wa.search_stocks(condition)
        if stocks:
            for s in stocks[:5]:
                code = s.get("Wind代码", s.get("代码", "?"))
        else:
            pass


def main():
    parser = argparse.ArgumentParser(description="Wind 万得高级监控")
    parser.add_argument("--holdings", action="store_true", help="仅持仓监控")
    parser.add_argument("--technical", action="store_true", help="仅技术指标")
    parser.add_argument("--news", action="store_true", help="仅新闻")
    parser.add_argument("--risk", action="store_true", help="仅风险指标")
    parser.add_argument("--screening", action="store_true", help="条件选股")
    args = parser.parse_args()

    wa = WindAnalytics()
    if not wa.available:
        sys.exit(1)

    # 默认全部
    run_all = not any([args.holdings, args.technical, args.news, args.risk, args.screening])

    if run_all or args.technical:
        monitor_technical()
    if run_all or args.news:
        monitor_news()
    if run_all or args.risk:
        monitor_risk()
    if args.screening:
        run_screening(wa)



if __name__ == "__main__":
    main()
