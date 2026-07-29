"""Wind 万得策略辅助模块

基于 Wind 高级分析工具的持仓监控和交易辅助。

使用方式：
    from claw.strategies.wind_strategy import WindStrategy
    ws = WindStrategy("600522")

    # 技术面
    signals = ws.technical_signals()

    # 顶底风险判断
    from claw.strategies.wind_strategy import check_overbought_oversold
    alerts = check_overbought_oversold(["600522", "000021"])
"""

from __future__ import annotations

from typing import Any

from claw.feeds.wind_analytics import WindAnalytics


class WindStrategy:
    """个股 Wind 策略分析器"""

    def __init__(self, code: str, name: str = ""):
        self.code = code
        self.name = name or code
        self._wa = WindAnalytics()

    def technical_signals(self) -> dict[str, Any]:
        """技术信号汇总

        Returns:
            {macd_trend: "↑"/"↓", rsi, rsi_signal, summary}
        """
        result: dict[str, Any] = {
            "code": self.code,
            "name": self.name,
            "macd_trend": "?",
            "rsi": None,
            "rsi_signal": "",
            "summary": "",
        }

        if not self._wa.available:
            result["summary"] = "Wind 不可用"
            return result

        # MACD
        macd_data = self._wa.get_technicals(self.code, "近60日MACD走势")
        if not macd_data:
            macd_data = self._wa.get_technicals(self.code, "近60日MACD")
        if macd_data and len(macd_data) >= 2:
            def _macd_of(row):
                k = next((kk for kk in row if "MACD" in kk and "时间" not in kk), None)
                if k and isinstance(row.get(k), (int, float)):
                    return float(row[k])
                return None
            vals = [v for v in (_macd_of(r) for r in reversed(macd_data)) if v is not None]
            if len(vals) >= 2:
                cur, prv = vals[0], vals[1]
                result["macd_val"] = round(cur, 2)
                if cur > prv:
                    result["macd_trend"] = "↑"
                elif cur < prv:
                    result["macd_trend"] = "↓"
                else:
                    result["macd_trend"] = "→"

        # RSI
        rsi_data = self._wa.get_technicals(self.code, "近60日RSI")
        if not rsi_data:
            rsi_data = self._wa.get_technicals(self.code, "近60日每日RSI")
        if rsi_data:
            rsi_row = rsi_data[-1]
            rsi_key = next((k for k in rsi_row if ("RSI" in k or "相对强弱" in k)), None)
            if rsi_key:
                rsi_v = rsi_row[rsi_key]
                if isinstance(rsi_v, (int, float)):
                    result["rsi"] = round(rsi_v, 1)
                    if rsi_v > 70:
                        result["rsi_signal"] = "🔴 超买"
                    elif rsi_v < 30:
                        result["rsi_signal"] = "🟢 超卖"
                    else:
                        result["rsi_signal"] = "正常"

        # 汇总
        parts = []
        parts.append(f"MACD{result['macd_trend']}")
        if result["rsi"]:
            parts.append(f"RSI={result['rsi']}({result['rsi_signal']})")
        result["summary"] = " | ".join(parts)
        return result

    def news_brief(self, top_k: int = 3) -> list[dict[str, str]]:
        """获取个股最新新闻摘要"""
        if not self._wa.available:
            return []
        news = self._wa.get_news(self.name if self.name != self.code else self.code, top_k=top_k)
        if not news:
            return []
        return [
            {"title": n.get("title", "?")[:60], "date": n.get("date", "?")}
            for n in news
        ]

    def risk_snapshot(self) -> dict[str, Any]:
        """风险快照

        注意：Wind 风险指标口径不稳定（Beta 偶发负值、波动率失真），
        当 ``beta_suspect`` / ``vol_suspect`` 为 True 时，对应值仅供参考，
        勿直接用于仓位/风险预算。
        """
        result: dict[str, Any] = {
            "beta": None,
            "volatility": None,
            "beta_suspect": False,
            "vol_suspect": False,
            "risk_note": "",
        }
        if not self._wa.available:
            return result
        risk = self._wa.get_risk_metrics(self.code, "过去1年Beta和波动率")
        if risk:
            r = risk[0]
            for bk in ["过去1年BETA", "过去1年Beta"]:
                v = r.get(bk)
                if isinstance(v, (int, float)):
                    result["beta"] = round(float(v), 2)
                    break
            for vk in ["过去1年波动率", "过去1年年化波动率"]:
                v = r.get(vk)
                if isinstance(v, (int, float)):
                    result["volatility"] = round(float(v), 2)
                    break
            result["beta_suspect"] = bool(r.get("beta_suspect", False))
            result["vol_suspect"] = bool(r.get("vol_suspect", False))
            result["risk_note"] = r.get("risk_note", "")
        return result


def check_overbought_oversold(
    codes: list[str],
    names: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """批量检查多只股票的 RSI 超买超卖状态

    Args:
        codes: 股票代码列表
        names: 可选 {code: name} 映射

    Returns:
        [{code, name, rsi, signal}]
    """
    wa = WindAnalytics()
    if not wa.available:
        return [{"code": c, "name": c, "rsi": None, "signal": "Wind不可用"} for c in codes]

    results = []
    for code in codes:
        name = (names or {}).get(code, code)
        rsi_data = wa.get_technicals(code, "近60日RSI")
        rsi_v = None
        if rsi_data and rsi_data[-1]:
            row = rsi_data[-1]
            key = next((k for k in row if ("RSI" in k or "相对强弱" in k)), None)
            if key:
                rsi_v = row[key]

        signal = ""
        if rsi_v is not None and isinstance(rsi_v, (int, float)):
            rsi_v = round(float(rsi_v), 1)
            if rsi_v > 70:
                signal = "🔴 超买"
            elif rsi_v < 30:
                signal = "🟢 超卖"
            else:
                signal = "正常"

        results.append({
            "code": code,
            "name": name,
            "rsi": rsi_v,
            "signal": signal,
        })

    return results


# ── 独立使用示例 ──
if __name__ == "__main__":
    holdings = {"600522": "中天科技", "600206": "有研新材", "000021": "深科技",
                "000636": "风华高科", "600584": "长电科技"}


    for code, name in holdings.items():
        ws = WindStrategy(code, name)
        sig = ws.technical_signals()
        news = ws.news_brief(top_k=1)
        risk = ws.risk_snapshot()
        if risk["beta"]:
            pass
        if news:
            pass

    for r in check_overbought_oversold(list(holdings.keys()), holdings):
        pass
