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
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from claw.feeds.wind_analytics import WindAnalytics

PROJECT_ROOT = Path(os.environ.get(
    "CLAW_PROJECT_ROOT",
    str(Path(__file__).resolve().parent.parent.parent.parent)
))
# canonical 投顾/模拟盘持仓文件（2026-07-22 由 .workbuddy/data/portfolio.json 合并迁入）
PORTFOLIO_SIM = PROJECT_ROOT / ".workbuddy" / "data" / "simulation" / "portfolio.json"
# 实盘持仓（国金证券等，A股人民币账户）
PORTFOLIO_USER = PROJECT_ROOT / ".workbuddy" / "data" / "user" / "portfolio.json"

# 多账户聚合配置： (文件, 账户标签)
PORTFOLIO_SOURCES = [
    (PORTFOLIO_SIM, "模拟盘"),
    (PORTFOLIO_USER, "实盘"),
]


def _extract_holdings(items: list) -> dict[str, str]:
    """从持仓条目列表提取 {code: name} 映射，跳过非法项"""
    result: dict[str, str] = {}
    for p in items:
        if not isinstance(p, dict):
            continue
        code = p.get("code") or p.get("symbol")
        if not code:
            continue
        result[str(code)] = p.get("name") or str(code)
    return result


def _load_account_holdings(path: Path) -> dict[str, str]:
    """从单个持仓文件提取 {code: name}，兼容多种 schema。

    支持：
      - 顶层 ``holdings`` / ``stocks`` / ``positions``（列表，或 "code->明细" 字典）
      - 合并文件 ``{live, sim}``（取 sim 优先，其次 live）
    文件缺失或解析失败时返回空 dict（不抛异常，避免模块导入即崩溃）。
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    # 顶层列表型字段
    for key in ("holdings", "stocks", "positions"):
        raw = data.get(key)
        if isinstance(raw, list) and raw:
            return _extract_holdings(raw)
    # positions 为 "code -> 明细" 字典
    if isinstance(data.get("positions"), dict) and data["positions"]:
        return _extract_holdings(list(data["positions"].values()))
    # 兼容 {live, sim} 合并文件
    for sub in ("sim", "live"):
        blk = data.get(sub)
        if isinstance(blk, dict):
            for key in ("holdings", "stocks", "positions"):
                raw = blk.get(key)
                if isinstance(raw, list) and raw:
                    return _extract_holdings(raw)
            if isinstance(blk.get("positions"), dict) and blk["positions"]:
                return _extract_holdings(list(blk["positions"].values()))
    return {}


def _load_all_holdings() -> dict[str, dict]:
    """聚合多账户持仓，返回 {code: {"name", "account"}}

    同代码跨账户时，保留首个出现的账户标签（模拟盘优先于实盘）。
    全部文件缺失时降级到硬编码兜底。
    """
    merged: dict[str, dict] = {}
    for path, account in PORTFOLIO_SOURCES:
        for code, name in _load_account_holdings(path).items():
            if code not in merged:  # 模拟盘优先，不覆盖
                merged[code] = {"name": name, "account": account}
    if merged:
        return merged
    # 降级到硬编码（兜底）
    fallback = {
        "600522": "中天科技",
        "600206": "有研新材",
        "000021": "深科技",
        "000636": "风华高科",
        "600584": "长电科技",
    }
    return {c: {"name": n, "account": "兜底"} for c, n in fallback.items()}


# 持仓列表 {code: {name, account}}
HOLDINGS: dict[str, dict] = _load_all_holdings()


def _fmt(v, decimals: int = 2) -> str:
    """格式化数值"""
    if v is None:
        return "N/A"
    return f"{v:.{decimals}f}"


def monitor_technical() -> list[dict]:
    """技术指标监控：MACD 趋势 + RSI（并行查询加速）

    Returns:
        [{code, name, macd_val, macd_trend, rsi, rsi_signal}]
    """
    codes = list(HOLDINGS.keys())
    results: list[dict] = []

    def _fetch_one(wa, code):
        """每线程独立实例，避免 subprocess 竞争"""
        name = HOLDINGS[code]["name"]
        account = HOLDINGS[code]["account"]
        macd_data = wa.get_technicals(code, "近60日MACD走势")
        rsi_data = wa.get_technicals(code, "近60日RSI")

        macd_val, macd_trend = None, "?"
        if macd_data and len(macd_data) >= 2:
            # 鲁棒匹配：Wind MACD 列名不稳定（指数平滑移动平均 / MACD值 等）
            def _macd_of(row):
                k = next((kk for kk in row if "MACD" in kk and "时间" not in kk), None)
                if k and isinstance(row.get(k), (int, float)):
                    return float(row[k])
                return None
            # 从末尾向前找两根"值不同"的 MACD，判断真实趋势（末根可能为未收盘重复值）
            vals = [v for v in (_macd_of(r) for r in reversed(macd_data)) if v is not None]
            if len(vals) >= 2:
                cur, prv = vals[0], vals[1]
                macd_val = round(cur, 2)
                if cur > prv:
                    macd_trend = "↑"
                elif cur < prv:
                    macd_trend = "↓"
                else:
                    macd_trend = "→"

        rsi_val, rsi_signal = None, ""
        if rsi_data and len(rsi_data) >= 1:
            row = rsi_data[-1]
            # 鲁棒匹配：Wind RSI 列名不稳定（近60日RSI / 近60日每日RSI / 相对强弱指标 等）
            rsi_key = next((k for k in row if "RSI" in k or "相对强弱" in k), None)
            if rsi_key:
                v = row.get(rsi_key)
                if v is not None and isinstance(v, (int, float)):
                    rsi_val = v
                    if rsi_val > 70:
                        rsi_signal = "⚠️ 超买"
                    elif rsi_val < 30:
                        rsi_signal = "⚠️ 超卖"
                    else:
                        rsi_signal = "正常"

        return {
            "code": code,
            "name": name,
            "account": account,
            "macd_val": macd_val,
            "macd_trend": macd_trend,
            "rsi": round(rsi_val, 1) if rsi_val is not None else None,
            "rsi_signal": rsi_signal,
        }

    with ThreadPoolExecutor(max_workers=min(5, len(codes))) as pool:
        futures = [pool.submit(_fetch_one, WindAnalytics(), code) for code in codes]
        for f in as_completed(futures):
            results.append(f.result())
    results.sort(key=lambda x: x["code"])
    return results


def monitor_news(top_k: int = 3) -> list[dict]:
    """财经新闻监控：持仓股最新动态

    Returns:
        [{code, name, news: [{title, date}]}]
    """

    def _fetch_one(wa, code):
        name = HOLDINGS[code]["name"]
        account = HOLDINGS[code]["account"]
        news = wa.get_news(name, top_k=top_k)
        items = (
            [{"title": n.get("title", "?")[:55], "date": n.get("date", "?")}
             for n in news]
            if news else []
        )
        return {"code": code, "name": name, "account": account, "news": items}

    with ThreadPoolExecutor(max_workers=min(5, len(HOLDINGS))) as pool:
        futures = [pool.submit(_fetch_one, WindAnalytics(), code) for code in HOLDINGS]
        results = [f.result() for f in as_completed(futures)]
    results.sort(key=lambda x: x["code"])
    return results


def monitor_risk() -> list[dict]:
    """风险指标快照

    Returns:
        [{code, name, beta, volatility, beta_suspect, vol_suspect, risk_note}]
    """

    def _fetch_one(wa, code):
        name = HOLDINGS[code]["name"]
        account = HOLDINGS[code]["account"]
        risk = wa.get_risk_metrics(code, "过去1年Beta和波动率")
        if risk and len(risk) >= 1:
            r = risk[0]
            beta = None
            for bk in ["过去1年BETA", "过去1年Beta", "过去1年年化Beta"]:
                v = r.get(bk)
                if isinstance(v, (int, float)):
                    beta = round(float(v), 2)
                    break
            vol = None
            for vk in ["过去1年波动率", "过去1年年化波动率", "过去1年Volatility"]:
                v = r.get(vk)
                if isinstance(v, (int, float)):
                    vol = round(float(v), 2)
                    break
            return {
                "code": code,
                "name": name,
                "account": account,
                "beta": beta,
                "volatility": vol,
                "beta_suspect": bool(r.get("beta_suspect", False)),
                "vol_suspect": bool(r.get("vol_suspect", False)),
                "risk_note": r.get("risk_note", ""),
            }
        return {"code": code, "name": name, "account": account, "beta": None, "volatility": None,
                "beta_suspect": False, "vol_suspect": False, "risk_note": "无数据"}

    with ThreadPoolExecutor(max_workers=min(5, len(HOLDINGS))) as pool:
        futures = [pool.submit(_fetch_one, WindAnalytics(), code) for code in HOLDINGS]
        results = [f.result() for f in as_completed(futures)]
    results.sort(key=lambda x: x["code"])
    return results


def run_screening(wa: WindAnalytics) -> list[dict]:
    """条件选股：发现潜在机会

    Returns:
        [{label, stocks: [{code, name}]}]
    """
    conditions = [
        ("沪深市场市值超500亿且连续3日上涨", "大盘企稳"),
        ("沪深市场MACD金叉且市值超100亿", "技术突破"),
        ("沪深市场RSI低于30且成交放量", "超卖反弹"),
    ]
    out: list[dict] = []
    for condition, label in conditions:
        stocks = wa.search_stocks(condition)
        items = []
        if stocks:
            for s in stocks[:5]:
                items.append({
                    "code": s.get("Wind代码", s.get("代码", "?")),
                    "name": s.get("证券简称", s.get("名称", "?")),
                })
        out.append({"label": label, "stocks": items})
    return out


def _fmt(v, decimals: int = 2) -> str:
    """格式化数值"""
    if v is None:
        return "N/A"
    return f"{v:.{decimals}f}"


def _label(item: dict) -> str:
    """持仓标签：名称(代码)[账户]，账户为兜底时省略"""
    acct = item.get("account", "")
    acct_tag = f"[{acct}]" if acct and acct != "兜底" else ""
    return f"{item['name']}({item['code']}){acct_tag}"


def render_markdown(
    technical: list[dict] | None = None,
    news: list[dict] | None = None,
    risk: list[dict] | None = None,
) -> str:
    """将监控结果渲染为 Markdown 文本（供飞书卡片推送）"""
    lines: list[str] = []
    if technical is not None:
        lines.append("📊 技术面（MACD趋势 / RSI）")
        for t in technical:
            rsi = f"RSI={t['rsi']}({t['rsi_signal']})" if t["rsi"] is not None else "RSI=N/A"
            lines.append(f"· {_label(t)} MACD{t['macd_trend']} {rsi}")
        lines.append("")
    if news is not None:
        lines.append("📰 持仓新闻")
        any_news = False
        for n in news:
            if n["news"]:
                any_news = True
                for item in n["news"]:
                    lines.append(f"· {_label(n)} {item['date']} {item['title']}")
        if not any_news:
            lines.append("· 近3日无重大持仓新闻")
        lines.append("")
    if risk is not None:
        lines.append("⚠️ 风险指标（Wind口径，失真已标注）")
        for r in risk:
            flag = ""
            if r["beta_suspect"] or r["vol_suspect"]:
                flag = " ⚠️疑似失真"
            lines.append(
                f"· {_label(r)} Beta={_fmt(r['beta'])} "
                f"波动率={_fmt(r['volatility'])}{flag}"
            )
        lines.append("· 注：Beta/波动率如标⚠️疑似失真，勿用于仓位计算，仅供参考")
        lines.append("")
    return "\n".join(lines).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Wind 万得高级监控")
    parser.add_argument("--holdings", action="store_true", help="仅持仓监控")
    parser.add_argument("--technical", action="store_true", help="仅技术指标")
    parser.add_argument("--news", action="store_true", help="仅新闻")
    parser.add_argument("--risk", action="store_true", help="仅风险指标")
    parser.add_argument("--screening", action="store_true", help="条件选股")
    parser.add_argument("--markdown", action="store_true", help="输出 Markdown（供推送）")
    args = parser.parse_args()

    wa = WindAnalytics()
    if not wa.available:
        print("Wind 数据源不可用（CLI 未装或 Key 未配），跳过。")
        return 1

    # 默认全部
    run_all = not any([args.holdings, args.technical, args.news, args.risk, args.screening])

    technical = monitor_technical() if (run_all or args.technical or args.holdings) else None
    news = monitor_news() if (run_all or args.news or args.holdings) else None
    risk = monitor_risk() if (run_all or args.risk or args.holdings) else None

    if args.screening:
        screening = run_screening(wa)

    if args.markdown:
        md = render_markdown(technical, news, risk)
        print(md)
        return 0

    # 默认人类可读输出（调试用）
    if technical is not None:
        print("== 技术面 ==")
        for t in technical:
            print(t)
    if news is not None:
        print("== 新闻 ==")
        for n in news:
            print(n)
    if risk is not None:
        print("== 风险 ==")
        for r in risk:
            print(r)
    if args.screening:
        print("== 选股 ==")
        for s in screening:
            print(s)
    return 0



if __name__ == "__main__":
    main()
