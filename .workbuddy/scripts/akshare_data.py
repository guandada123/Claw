#!/usr/bin/env python3
"""
AkShare 数据增强层 — 提供通达信/腾讯财经之外的补充数据
集成到智能选股和复盘的评分卡中，主要提供资金面数据

数据源：AkShare（免费开源 A 股金融数据接口）
安装：pip install akshare
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

try:
    import akshare as ak
except ImportError:
    print(json.dumps({"ok": False, "error": "akshare not installed. Run: pip install akshare"}))
    sys.exit(1)

CACHE_DIR = Path(__file__).parent.parent / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def cmd_margin_trading(date_str: str = None):
    """
    查询融资融券数据（两融余额，反映市场风险偏好）
    """
    try:
        if date_str is None:
            date_str = date.today().isoformat().replace("-", "")

        # 获取近30日两融数据
        df = ak.stock_margin_detail_sse(
            start_date=(date.today() - timedelta(days=30)).isoformat().replace("-", ""),
            end_date=date_str,
        )

        if df is not None and not df.empty:
            latest = df.iloc[-1]
            result = {
                "ok": True,
                "date": str(date_str),
                "margin_balance": float(latest.get("rzye", 0)),  # 融资余额
                "short_balance": float(latest.get("rqye", 0)),  # 融券余额
                "total_balance": float(latest.get("rzrqye", 0)),  # 两融总余额
            }
            return result
    except Exception as e:
        return {"ok": False, "error": f"AkShare两融数据获取失败: {e}"}

    return {"ok": False, "error": "暂无两融数据"}


def cmd_north_flow(date_str: str = None):
    """
    查询北向资金流向
    """
    try:
        # 北向资金日数据
        df = ak.stock_hsgt_hist_em(symbol="北向资金")

        if df is not None and not df.empty:
            df = df.sort_values("日期", ascending=False)
            latest = df.iloc[0]
            recent = df.head(5).to_dict(orient="records")

            result = {
                "ok": True,
                "latest": {
                    "date": str(latest.get("日期", "")),
                    "net_flow": float(latest.get("当日成交净买额", 0)),  # 亿元
                    "buy_amount": float(latest.get("买入成交额", 0)),
                    "sell_amount": float(latest.get("卖出成交额", 0)),
                },
                "trend": "流入" if float(latest.get("当日成交净买额", 0)) > 0 else "流出",
                "recent_5d": [
                    {
                        "date": str(r.get("日期", "")),
                        "net_flow": float(r.get("当日成交净买额", 0)),
                    }
                    for r in recent
                ],
            }
            return result
    except Exception:
        pass  # 尝试备用方法

    # 备用：使用沪股通个股数据估算
    try:
        df = ak.stock_hsgt_north_net_flow_in_em(symbol="北上")
        if df is not None and not df.empty:
            df = df.sort_values("日期", ascending=False)
            latest = df.iloc[0]
            result = {
                "ok": True,
                "latest": {
                    "date": str(latest.get("日期", "")),
                    "net_flow": float(latest.get("当日净流入", 0)),
                },
                "trend": "流入" if float(latest.get("当日净流入", 0)) > 0 else "流出",
            }
            return result
    except Exception as e:
        return {"ok": False, "error": f"AkShare北向资金获取失败: {e}"}

    return {"ok": False, "error": "暂无北向资金数据"}


def cmd_sector_performance():
    """
    查询行业板块涨跌幅排行
    """
    try:
        df = ak.stock_board_industry_name_em()

        if df is not None and not df.empty:
            # 按涨跌幅排序
            df = df.sort_values("涨跌幅", ascending=False)

            top_5 = df.head(5)[["板块名称", "涨跌幅", "领涨股票", "领涨股票-涨跌幅"]].to_dict(
                orient="records"
            )
            bottom_5 = df.tail(5)[["板块名称", "涨跌幅", "领涨股票", "领涨股票-涨跌幅"]].to_dict(
                orient="records"
            )

            return {
                "ok": True,
                "total_sectors": len(df),
                "up_sectors": int((df["涨跌幅"] > 0).sum()),
                "down_sectors": int((df["涨跌幅"] < 0).sum()),
                "top_5": top_5,
                "bottom_5": bottom_5,
            }
    except Exception as e:
        return {"ok": False, "error": f"AkShare板块数据获取失败: {e}"}


def cmd_individual_fund_flow(symbol: str):
    """
    查询个股资金流向（主力净流入/流出）
    参数: symbol - 6位股票代码
    """
    try:
        df = ak.stock_individual_fund_flow(
            stock=symbol, market="sh" if symbol.startswith("6") else "sz"
        )

        if df is not None and not df.empty:
            latest = df.iloc[-1]
            # 计算近5日资金流向趋势
            recent_5 = df.tail(5)
            total_flow = sum(float(r.get("主力净流入-净额", 0)) for _, r in recent_5.iterrows())

            result = {
                "ok": True,
                "symbol": symbol,
                "today": {
                    "date": str(latest.get("日期", "")),
                    "main_net_flow": float(latest.get("主力净流入-净额", 0)),  # 万元
                    "super_large_flow": float(latest.get("超大单净流入-净额", 0)),
                    "large_flow": float(latest.get("大单净流入-净额", 0)),
                    "mid_flow": float(latest.get("中单净流入-净额", 0)),
                    "small_flow": float(latest.get("小单净流入-净额", 0)),
                },
                "recent_5d_total_flow": round(total_flow, 2),
                "trend": "净流入" if total_flow > 0 else "净流出",
            }
            return result
    except Exception as e:
        return {"ok": False, "error": f"AkShare个股资金流获取失败: {e}"}


def cmd_financial_data(symbol: str):
    """
    查询个股最新财报关键指标
    参数: symbol - 6位股票代码
    """
    try:
        # 获取最新财报数据
        df = ak.stock_financial_abstract_ths(symbol=symbol, indicator="按报告期")

        if df is not None and not df.empty:
            latest = df.iloc[-1]

            result = {
                "ok": True,
                "symbol": symbol,
                "report_date": str(latest.get("报告期", "")),
                "indicators": {
                    "eps": float(latest.get("基本每股收益", 0) or 0),
                    "roe": float(latest.get("净资产收益率", 0) or 0),
                    "revenue_growth": float(latest.get("营业收入同比增长", 0) or 0),
                    "net_profit_growth": float(latest.get("归属净利润同比增长", 0) or 0),
                    "pe": float(latest.get("市盈率-动态", 0) or 0),
                    "pb": float(latest.get("市净率", 0) or 0),
                },
            }
            return result
    except Exception as e:
        return {"ok": False, "error": f"AkShare财报数据获取失败: {e}"}


def cmd_market_sentiment():
    """
    查询市场情绪指标（涨停板数量、跌停板数量、涨跌比）
    """
    try:
        # 涨跌停统计
        limit_up_df = ak.stock_zt_pool_em(date=date.today().isoformat().replace("-", ""))
        limit_down_df = ak.stock_zt_pool_dtgc_em(date=date.today().isoformat().replace("-", ""))

        # 全市场涨跌统计
        market_activity = ak.stock_market_activity_legu()

        total_up = int(limit_up_df["涨停统计"].count()) if limit_up_df is not None else 0
        total_down = int(limit_down_df["跌停统计"].count()) if limit_down_df is not None else 0

        result = {
            "ok": True,
            "limit_up_count": total_up,
            "limit_down_count": total_down,
            "sentiment": "极度亢奋"
            if total_up > 100
            else ("偏乐观" if total_up > 50 else ("中性" if total_up > 20 else "偏悲观")),
        }
        return result
    except Exception:
        # 备用方法
        try:
            df = ak.stock_zt_pool_em(date=date.today().isoformat().replace("-", ""))
            total_up = len(df) if df is not None else 0
            return {
                "ok": True,
                "limit_up_count": total_up,
                "limit_down_count": 0,
                "sentiment": "极度亢奋"
                if total_up > 100
                else "偏乐观"
                if total_up > 50
                else "中性",
            }
        except Exception as e:
            return {"ok": False, "error": f"市场情绪数据获取失败: {e}"}


# ══════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "用法: akshare_data.py <命令> [参数]"}))
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "margin":
        date_str = sys.argv[2] if len(sys.argv) > 2 else None
        result = cmd_margin_trading(date_str)
    elif cmd == "north-flow":
        date_str = sys.argv[2] if len(sys.argv) > 2 else None
        result = cmd_north_flow(date_str)
    elif cmd == "sector":
        result = cmd_sector_performance()
    elif cmd == "fund-flow":
        if len(sys.argv) < 3:
            result = {"ok": False, "error": "用法: akshare_data.py fund-flow <股票代码>"}
        else:
            result = cmd_individual_fund_flow(sys.argv[2])
    elif cmd == "financial":
        if len(sys.argv) < 3:
            result = {"ok": False, "error": "用法: akshare_data.py financial <股票代码>"}
        else:
            result = cmd_financial_data(sys.argv[2])
    elif cmd == "sentiment":
        result = cmd_market_sentiment()
    elif cmd == "all-flow":
        # 批量查询资金流向（从 stdin 读取股票列表）
        symbols = json.loads(sys.stdin.read())
        results = []
        for s in symbols:
            r = cmd_individual_fund_flow(s)
            results.append(r)
        result = {"ok": True, "symbols": symbols, "results": results}
    else:
        result = {"ok": False, "error": f"未知命令: {cmd}"}

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
