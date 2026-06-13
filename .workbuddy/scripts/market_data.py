#!/usr/bin/env python3
"""
市场数据增强层 — 基于东方财富公开API，纯标准库实现
零外部依赖，直接 HTTP + JSON 获取板块/资金流/北向资金/市场情绪数据

数据源：东方财富公开行情API（push2.eastmoney.com）
用途：为智能选股评分卡的资金面维度提供补充数据
"""

import json
import ssl
import sys
import urllib.error
import urllib.request
from datetime import date, datetime

API_BASE = "https://push2.eastmoney.com/api/qt/clist/get"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://data.eastmoney.com/",
}
_SSL_CONTEXT = ssl.create_default_context()

# Cache disabled — use stdout-based caching by calling script only when needed
# Data is fresh on each call (EastMoney API is fast enough)


def _fetch(url: str, timeout: int = 10) -> dict:
    """通用 HTTP GET with JSON 解析"""
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, context=_SSL_CONTEXT, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP error {e.code}: {e.reason}"}
    except urllib.error.URLError as e:
        return {"error": f"URL error: {e.reason}"}
    except json.JSONDecodeError as e:
        return {"error": f"JSON decode error: {e}"}
    except TimeoutError:
        return {"error": f"请求超时 ({timeout}s)"}


# ══════════════════════════════════════════════════
#  板块排行
# ══════════════════════════════════════════════════


def cmd_sector_performance():
    """查询行业板块涨跌幅排行（Top5/Bottom5）"""
    # 东方财富行业板块API
    url = (
        f"{API_BASE}?pn=1&pz=100&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281"
        f"&fltt=2&invt=2&fid=f3&fs=m:90+t:2&fields=f2,f3,f4,f12,f14,f128"
        f"&_={int(datetime.now().timestamp() * 1000)}"
    )
    data = _fetch(url)

    if isinstance(data, dict) and "data" in data:
        items = data["data"].get("diff", [])
        # f2=最新价, f3=涨跌幅, f12=板块代码, f14=板块名称, f128=领涨股
        sectors = []
        for item in items:
            sectors.append(
                {
                    "code": item.get("f12", ""),
                    "name": item.get("f14", ""),
                    "pct_chg": item.get("f3", 0),
                    "lead_stock": item.get("f128", ""),
                }
            )
        sectors.sort(key=lambda x: x["pct_chg"], reverse=True)

        up_count = sum(1 for s in sectors if s["pct_chg"] > 0)
        down_count = sum(1 for s in sectors if s["pct_chg"] < 0)

        return {
            "ok": True,
            "total_sectors": len(sectors),
            "up_sectors": up_count,
            "down_sectors": down_count,
            "top_5": sectors[:5],
            "bottom_5": sectors[-5:][::-1],
        }
    return {"ok": False, "error": "板块数据获取失败"}


# ══════════════════════════════════════════════════
#  北向资金
# ══════════════════════════════════════════════════


def cmd_north_flow():
    """查询近5日北向资金流向"""
    url = (
        f"{API_BASE}?pn=1&pz=5&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281"
        f"&fltt=2&invt=2&fid=f3&fs=m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2"
        f"&fields=f2,f3,f12,f14&_={int(datetime.now().timestamp() * 1000)}"
    )
    # 备用：直接用沪深港通资金流向API
    alt_url = (
        f"{API_BASE}?pn=1&pz=1&po=0&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281"
        f"&fltt=2&invt=2&fid=f62&fs=m:0+t:6+s:!2&fields=f2,f3,f12,f14,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87"
        f"&_={int(datetime.now().timestamp() * 1000)}"
    )

    # 尝试获取北向资金日数据
    north_url = (
        f"https://push2his.eastmoney.com/api/qt/kamt.kline/get?"
        f"fields1=f1,f2,f3,f4&fields2=f51,f52,f53,f54&"
        f"klt=101&lmt=5&ut=2887a9128e9d96a09a7f33fe1e6097c7&"
        f"secid=1.000300&_={int(datetime.now().timestamp() * 1000)}"
    )

    data = _fetch(north_url)
    if isinstance(data, dict) and "data" in data and data["data"]:
        klines = data["data"].get("klines", [])
        if klines:
            # 最近一行
            last = klines[-1].split(",")
            # f51=日期, f52=当日资金流入(亿), f53=当日余额(亿), f54=累计资金流入(亿)
            net = float(last[1])
            return {
                "ok": True,
                "latest": {
                    "date": last[0],
                    "net_flow": round(net, 2),
                },
                "trend": "流入" if net > 0 else "流出",
                "recent_5d": [
                    {"date": k.split(",")[0], "net_flow": round(float(k.split(",")[1]), 2)}
                    for k in klines
                ],
            }

    return {"ok": False, "error": "北向资金数据暂不可用"}


# ══════════════════════════════════════════════════
#  个股资金流向
# ══════════════════════════════════════════════════


def _get_market_code(symbol: str) -> str:
    """根据股票代码返回市场代码: 1=沪市, 0=深市"""
    return "1" if symbol.startswith(("6", "68")) else "0"


def cmd_individual_fund_flow(symbol: str):
    """查询个股最近1日资金流向（主力/超大单/大单/中单/小单）"""
    market = _get_market_code(symbol)
    secid = f"{market}.{symbol}"

    url = (
        f"{API_BASE}?pn=1&pz=1&po=0&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281"
        f"&fltt=2&invt=2&fid=f62&fs=m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2"
        f"&fields=f2,f3,f12,f14,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87"
        f"&_={int(datetime.now().timestamp() * 1000)}"
    )

    # 更准确的：个股资金流向
    detail_url = (
        f"https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?"
        f"fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        f"&lmt=5&ut=2887a9128e9d96a09a7f33fe1e6097c7&"
        f"secid={secid}&_={int(datetime.now().timestamp() * 1000)}"
    )

    data = _fetch(detail_url)
    if isinstance(data, dict) and "data" in data and data["data"]:
        klines = data["data"].get("klines", [])
        if klines:
            # f51=日期, f52=主力净流入, f53=小单净流入, f54=中单净流入, f55=大单净流入, f56=超大单净流入
            today = klines[-1].split(",")
            total_5d = sum(float(k.split(",")[1]) for k in klines)

            return {
                "ok": True,
                "symbol": symbol,
                "today": {
                    "date": today[0],
                    "main_net_flow": float(today[1]),
                    "super_large_flow": float(today[5]) if len(today) > 5 else 0,
                    "large_flow": float(today[4]) if len(today) > 4 else 0,
                    "mid_flow": float(today[3]) if len(today) > 3 else 0,
                    "small_flow": float(today[2]),
                },
                "recent_5d_total_flow": round(total_5d, 2),
                "trend": "净流入" if total_5d > 0 else "净流出",
            }

    return {"ok": False, "error": f"个股 {symbol} 资金流数据暂不可用"}


# ══════════════════════════════════════════════════
#  市场情绪
# ══════════════════════════════════════════════════


def cmd_market_sentiment():
    """查询今日市场情绪（涨停/跌停数量）"""
    today = date.today().isoformat().replace("-", "")

    # 涨停板
    zt_url = (
        f"{API_BASE}?pn=1&pz=5&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281"
        f"&fltt=2&invt=2&fid=f3&fs=m:0+t:80+f:!4&fields=f2,f3,f12,f14"
        f"&_={int(datetime.now().timestamp() * 1000)}"
    )
    zt_data = _fetch(zt_url)

    total_zt = 0
    total_dt = 0

    if isinstance(zt_data, dict) and "data" in zt_data:
        total_zt = zt_data["data"].get("total", 0)

    # 跌停板
    dt_url = (
        f"{API_BASE}?pn=1&pz=5&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281"
        f"&fltt=2&invt=2&fid=f3&fs=m:0+t:80+f:!3&fields=f2,f3,f12,f14"
        f"&_={int(datetime.now().timestamp() * 1000)}"
    )
    dt_data = _fetch(dt_url)
    if isinstance(dt_data, dict) and "data" in dt_data:
        total_dt = dt_data["data"].get("total", 0)

    sentiment = (
        "极度亢奋"
        if total_zt > 100
        else "偏乐观"
        if total_zt > 50
        else "中性"
        if total_zt > 20
        else "偏悲观"
    )

    return {
        "ok": True,
        "limit_up_count": total_zt,
        "limit_down_count": total_dt,
        "sentiment": sentiment,
    }


# ══════════════════════════════════════════════════
#  大类资产：国债收益率
# ══════════════════════════════════════════════════


def cmd_bond_yield():
    """查询中国国债收益率曲线（东方财富实时）"""
    # EMM code mapping (confirmed via Zhihu + East Money docs)
    # EMM00588704 = 2年期, EMM00166462 = 5年期
    # EMM00166466 = 10年期, EMM00166469 = 30年期
    # EMM01276014 = 10Y-2Y利差
    # EMG00001306 = 美国2年期, EMG00001308 = 美国10年期
    EMM_MAP = {
        "EMM00588704": "2Y",
        "EMM00166462": "5Y",
        "EMM00166466": "10Y",
        "EMM00166469": "30Y",
        "EMM01276014": "10Y-2Y利差",
    }
    EMG_MAP = {
        "EMG00001306": "美国2Y",
        "EMG00001308": "美国10Y",
        "EMG00001310": "美国30Y",
        "EMG00001312": "美国5Y",
    }

    try:
        url = (
            f"https://datacenter-web.eastmoney.com/api/data/v1/get?"
            f"reportName=RPTA_WEB_TREASURYYIELD&columns=ALL&"
            f"pageSize=5&pageNumber=1&"
            f"source=WEB&client=WEB&"
            f"_={int(datetime.now().timestamp() * 1000)}"
        )
        data = _fetch(url)
        if isinstance(data, dict) and data.get("success") and data.get("result", {}).get("data"):
            rows = data["result"]["data"]
            if not rows:
                return {"ok": False, "error": "国债收益率数据为空"}

            cn_yields = {}
            us_yields = {}
            latest = rows[0]
            latest_date = str(latest.get("SOLAR_DATE", "N/A"))

            for code, label in EMM_MAP.items():
                val = latest.get(code)
                if val is not None:
                    cn_yields[label] = round(float(val), 4)

            for code, label in EMG_MAP.items():
                val = latest.get(code)
                if val is not None:
                    us_yields[label] = round(float(val), 4)

            # History (5 days)
            history = []
            for row in rows:
                history.append(
                    {
                        "date": str(row.get("SOLAR_DATE", "")),
                        "cn_10y": float(row.get("EMM00166466", 0))
                        if row.get("EMM00166466")
                        else None,
                        "cn_2y": float(row.get("EMM00588704", 0))
                        if row.get("EMM00588704")
                        else None,
                        "us_10y": float(row.get("EMG00001308", 0))
                        if row.get("EMG00001308")
                        else None,
                    }
                )

            return {
                "ok": True,
                "source": "东方财富(实时)",
                "latest_date": latest_date,
                "cn_yields": cn_yields,
                "us_yields": us_yields,
                "history_5d": history,
            }

        return {"ok": False, "error": "国债收益率API返回异常"}
    except Exception as e:
        return {"ok": False, "error": f"国债收益率获取失败: {e}"}


# ══════════════════════════════════════════════════
#  大类资产：商品指数
# ══════════════════════════════════════════════════


def cmd_commodity():
    """查询南华商品指数及其他大宗商品价格"""
    result = {}

    # 南华商品指数 (东方财富期货)
    try:
        url = (
            f"https://push2.eastmoney.com/api/qt/ulist.np/get?"
            f"fields=f2,f3,f4,f12,f14&fltt=2&"
            f"secids=113.NHCI,113.020283,113.020284,113.NHAI,113.NHMI,113.NHII&"
            f"ut=bd1d9ddb04089700cf9c27f6f7426281&"
            f"_={int(datetime.now().timestamp() * 1000)}"
        )
        data = _fetch(url)
        if isinstance(data, dict) and data.get("data", {}).get("diff"):
            items = data["data"]["diff"]
            result["commodity_index"] = {
                item.get("f14", "?"): {
                    "price": item.get("f2"),
                    "pct_chg": item.get("f3"),
                }
                for item in items
            }
    except Exception:
        result["commodity_index"] = {"error": "获取失败"}

    # 黄金价格 (上海金交所)
    try:
        gold_url = (
            f"https://push2.eastmoney.com/api/qt/ulist.np/get?"
            f"fields=f2,f3,f12,f14&fltt=2&"
            f"secids=113.AU9999,113.AGTD&"
            f"ut=bd1d9ddb04089700cf9c27f6f7426281&"
            f"_={int(datetime.now().timestamp() * 1000)}"
        )
        gold_data = _fetch(gold_url)
        if isinstance(gold_data, dict) and gold_data.get("data", {}).get("diff"):
            result["precious_metals"] = {
                item.get("f14", "?"): {
                    "price": item.get("f2"),
                    "pct_chg": item.get("f3"),
                }
                for item in gold_data["data"]["diff"]
            }
    except Exception:
        result["precious_metals"] = {"error": "获取失败"}

    result["ok"] = bool(result)
    return result


# ══════════════════════════════════════════════════
#  大类资产：外汇汇率
# ══════════════════════════════════════════════════


def cmd_forex():
    """查询主要汇率：USD/CNY, EUR/CNY, JPY/CNY"""
    try:
        import akshare as ak

        df = ak.fx_spot_quote()
        if df is not None and not df.empty:
            rates = {}
            for _, row in df.iterrows():
                pair = str(row.get("货币对", ""))
                rates[pair] = {
                    "bid": row.get("买报价"),
                    "ask": row.get("卖报价"),
                }
            return {
                "ok": True,
                "source": "AKShare",
                "rates": rates,
                "count": len(rates),
                "updated": datetime.now().isoformat(),
            }
    except Exception as e:
        return {"ok": False, "error": f"外汇数据获取失败: {e}"}


# ══════════════════════════════════════════════════
#  大类资产：综合快照（一键获取所有）
# ══════════════════════════════════════════════════


def cmd_macro_snapshot():
    """大类资产综合快照：国债+商品+汇率一次获取"""
    return {
        "ok": True,
        "updated": datetime.now().isoformat(),
        "bond_yield": cmd_bond_yield(),
        "commodity": cmd_commodity(),
        "forex": cmd_forex(),
        "sentiment": cmd_market_sentiment(),
        "north_flow": cmd_north_flow(),
    }


# ══════════════════════════════════════════════════
#  批量查询：持仓股票资金面扫描
# ══════════════════════════════════════════════════


def cmd_batch_fund_scan():
    """从 stdin 读取股票代码列表，批量查询资金流向并返回汇总"""
    try:
        symbols = json.loads(sys.stdin.read())
    except Exception:
        return {"ok": False, "error": "请输入股票代码 JSON 数组"}

    results = []
    for sym in symbols:
        r = cmd_individual_fund_flow(sym)
        results.append(r)

    # 汇总
    inflow_count = sum(1 for r in results if r.get("ok") and r.get("trend") == "净流入")
    return {
        "ok": True,
        "total": len(symbols),
        "inflow_count": inflow_count,
        "outflow_count": len(symbols) - inflow_count,
        "details": results,
    }


# ══════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════


def main():
    if len(sys.argv) < 2:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "用法: market_data.py <命令>",
                    "commands": [
                        "sector",
                        "north-flow",
                        "fund-flow <代码>",
                        "sentiment",
                        "batch",
                        "bond-yield",
                        "commodity",
                        "forex",
                        "macro-snapshot",
                    ],
                },
                ensure_ascii=False,
            )
        )
        sys.exit(1)

    cmd = sys.argv[1]

    handlers = {
        "sector": lambda: cmd_sector_performance(),
        "north-flow": lambda: cmd_north_flow(),
        "sentiment": lambda: cmd_market_sentiment(),
        "fund-flow": lambda: (
            cmd_individual_fund_flow(sys.argv[2])
            if len(sys.argv) > 2
            else {"ok": False, "error": "用法: fund-flow <股票代码>"}
        ),
        "batch": lambda: cmd_batch_fund_scan(),
        "bond-yield": lambda: cmd_bond_yield(),
        "commodity": lambda: cmd_commodity(),
        "forex": lambda: cmd_forex(),
        "macro-snapshot": lambda: cmd_macro_snapshot(),
    }

    handler = handlers.get(cmd)
    result = handler() if handler else {"ok": False, "error": f"未知命令: {cmd}"}

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
