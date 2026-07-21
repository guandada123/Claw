#!/usr/bin/env python3
"""build_yupen_from_market.py — 用行情数据自建鱼盆两张表（脱离微信RSS）

数据源(主): Wind API (index_data.get_index_kline) 覆盖 中证/国证/港股/美股/全球指数
            及 A股行业/概念(882xxx/884xxx/866xxx)，含原东财 881xxx 板块的 Wind 等价口径；
            雅虎财经 v8 API 覆盖外盘/商品(日经/台湾/韩国/金/银，Mac mini/沙箱均直连可达)。
兜底:      东财行业指数(881xxx)+微盘股 仅作 YUPEN_USE_EM=1 时的兜底(猫哥原生口径)，
            正常情况 Wind 主源已覆盖全部 34 项，无需东财、无需 RSS。
RSS OCR:   仅当 Wind 与东财均不可达时的最后兜底(merge 回填缺口)，正常零 RSS 闭环。

产出（写入 output/yupen/，与 RSS OCR 产物 schema 兼容；primary_ 前缀隔离，避免被 RSS 同名文件覆盖）:
  - yupen_primary_<date>_sector_rotation.json  板块轮动(14)
  - yupen_primary_<date>_yupen_trend.json      鱼盆趋势(20全球指数)

增强字段: ma20_slope(趋势拐头) / rps(池内相对强度) / overheat_warning(高波动三重确认) / src

用法:
  python scripts/build_yupen_from_market.py [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import urllib.parse

CLAW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIND_CLI = os.path.expanduser("~/.agents/skills/wind-mcp-skill/scripts/cli.mjs")
WIND_CWD = os.path.dirname(os.path.dirname(WIND_CLI))

# ── 板块轮动(14) ── src: wind=中证/国证/Wind二级行业系; em=东财行业指数(881xxx,仅兜底)
# 走势交叉验证(2026-07-21, vs 申万/聚源同名板块逐日涨跌方向):
#   光伏866020.WI 7/7吻合✅  半导体882121.WI 6/6吻合✅  电网882122/细分化工882202=Wind标准二级行业
#   商业航天884110.WI=泛航天概念近似(Wind无专属口径,详见该行注释)
SECTORS = [
    {"name": "中证消费", "code": "1B0932", "src": "wind", "wind": "中证消费"},
    {"name": "CS创新药", "code": "931152", "src": "wind", "wind": "931152.CSI"},
    {"name": "中证红利", "code": "000922", "src": "wind", "wind": "中证红利"},
    {"name": "中证煤炭", "code": "399998", "src": "wind", "wind": "中证煤炭"},
    {"name": "证券公司", "code": "399975", "src": "wind", "wind": "399975.SZ"},
    {"name": "房地产",   "code": "931775", "src": "wind", "wind": "931775.CSI"},
    {"name": "电网设备", "code": "881278", "src": "wind", "wind": "882122.WI", "em": "1.881278"},
    {"name": "有色金属", "code": "1B0819", "src": "wind", "wind": "中证有色金属", "wind2": "1B0819"},
    {"name": "细分化工", "code": "000813", "src": "wind", "wind": "882202.WI", "em": "0.000813"},
    {"name": "机器人",   "code": "H30590", "src": "wind", "wind": "H30590.CSI"},
    {"name": "光伏设备", "code": "881279", "src": "wind", "wind": "866020.WI", "em": "1.881279"},
    # 商业航天: Wind 通用 index_data 无专属"商业航天"口径(886078 取不到; 884110/884168/884142 同值,
    # 均映射到同一泛航天/军工概念线)。走势与聚源商业航天概念 corr+0.66(方向4/6)，作近似轮动观察项，标 approx。
    {"name": "商业航天", "code": "886078", "src": "wind", "wind": "884110.WI", "em": "1.886078",
     "approx": "泛航天概念近似(Wind无专属商业航天口径)"},
    {"name": "新能源",   "code": "000941", "src": "wind", "wind": "000941.CSI"},
    {"name": "半导体",   "code": "881121", "src": "wind", "wind": "882121.WI", "em": "1.881121"},
]

# ── 鱼盆趋势(20全球指数) ──
# wind: 港股/美股/中证/国证/沪深主要指数(Wind 覆盖)
# yf:   外盘/商品(雅虎财经 v8 API, Mac mini 直连可达；本环境封网则回落RSS)
#       Wind index_data 对台/韩/金/银 MARKET_TARGET_NOT_FOUND，且日经/微盘股 kline 仅~3天不够MA20
# rss:  微盘股(A股, 走东财1.883418, 需 YUPEN_USE_EM=1；否则RSS兜底)
TREND = [
    {"name": "恒生指数", "code": "HSI",   "src": "wind", "wind": "HSI.HI"},
    {"name": "国企指数", "code": "HSCEI", "src": "wind", "wind": "HSCEI.HI"},
    {"name": "恒生科技", "code": "HS2083","src": "wind", "wind": "HSTECH.HI"},
    {"name": "标普500",  "code": "SPY",   "src": "wind", "wind": "SPX.GI"},
    {"name": "黄金现价", "code": "GC=F",  "src": "yf", "yf": "GC=F", "approx": "期货近似"},
    {"name": "纳指100",  "code": "QQQ",   "src": "wind", "wind": "NDX.GI"},
    {"name": "微盘股",   "code": "884143", "src": "wind", "wind": "884143.WI"},
    {"name": "上证50",   "code": "1B0016", "src": "wind", "wind": "上证50"},
    {"name": "白银现价", "code": "SI=F",  "src": "yf", "yf": "SI=F", "approx": "期货近似"},
    {"name": "沪深300",  "code": "399300", "src": "wind", "wind": "沪深300"},
    {"name": "日经225",  "code": "N225",  "src": "yf", "yf": "^N225"},
    {"name": "台湾加权", "code": "TWII",  "src": "yf", "yf": "^TWII"},
    {"name": "中证A500", "code": "000510", "src": "wind", "wind": "中证A500"},
    {"name": "北证50",   "code": "899050", "src": "wind", "wind": "北证50"},
    {"name": "中证500",  "code": "399905", "src": "wind", "wind": "中证500"},
    {"name": "韩国综合", "code": "KS11",  "src": "yf", "yf": "^KS11"},
    {"name": "中证1000", "code": "1B0852", "src": "wind", "wind": "中证1000"},
    {"name": "中证2000", "code": "932000", "src": "wind", "wind": "中证2000"},
    {"name": "科创50",   "code": "1B0688", "src": "wind", "wind": "科创50"},
    {"name": "创业板指", "code": "399006", "src": "wind", "wind": "创业板指"},
]

CAT_REF = os.path.join(CLAW, "output/yupen/yupen_2026-07-17_sector_rotation.json")
CAT_REF_TREND = os.path.join(CLAW, "output/yupen/yupen_2026-07-17_yupen_trend.json")

# ── 东财兜底 secid 映射（仅 Mac mini 等可直连东财的环境启用 YUPEN_USE_EM=1）──
# 5 个轮动板块 = 东方财富行业指数(881xxx)，猫哥原生口径，确定可靠。
# 微盘股 = 东财微盘股指数(1.883418)，确定。
# 注: 外盘/商品(日经/台湾/韩国/黄金/白银)已改走 yf(雅虎)，不再用东财全球 secid。
EM_SECID = {
    "电网设备": "1.881278", "细分化工": "0.000813", "光伏设备": "1.881279",
    "商业航天": "1.886078", "半导体": "1.881121",   # 东财行业指数(确定)
    "微盘股":   "1.883418",                          # 东财微盘股指数(确定)
}
USE_EM = os.environ.get("YUPEN_USE_EM") == "1"


def _norm_date(s):
    """Wind TIME 字段偶发返回 20260721(无横线)或 2026-07-21(有横线)，统一为 YYYY-MM-DD，
    避免与 target 字符串比较时因格式不一致导致 upto 全被过滤(<20日误判)。"""
    s = s[:10]
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def wind_kline(query, beg, end):
    params = json.dumps({"windcode": query, "begin_date": beg, "end_date": end},
                        ensure_ascii=False)
    for _ in range(3):
        try:
            r = subprocess.run(
                ["node", WIND_CLI, "call", "index_data", "get_index_kline", params],
                capture_output=True, text=True, timeout=30, cwd=WIND_CWD,
            )
            out = json.loads(r.stdout)
            if out.get("isError"):
                continue
            text = out.get("content", [{}])[0].get("text", "")
            data = json.loads(text).get("data")
            if not data or not data.get("rows"):
                continue
            cols = [c["name"] for c in data["columns"]]
            ti, oi, ci, hi, li, vi = (cols.index("TIME"), cols.index("OPEN"),
                                      cols.index("MATCH"), cols.index("HIGH"),
                                      cols.index("LOW"), cols.index("VOLUME"))
            rows = [{"date": _norm_date(r[ti]), "open": float(r[oi]), "close": float(r[ci]),
                     "high": float(r[hi]), "low": float(r[li]), "vol": float(r[vi])}
                    for r in data["rows"]]
            rows.sort(key=lambda x: x["date"])
            return rows
        except Exception:
            continue
    return None


def em_kline(secid, beg, end):
    import requests
    for clear in (False, True):
        if clear:
            for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                os.environ.pop(k, None)
        try:
            r = requests.get(
                "https://push2his.eastmoney.com/api/qt/stock/kline/get",
                params={"secid": secid, "klt": "101", "fqt": "0", "beg": beg,
                        "end": end, "fields1": "f1,f2",
                        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58", "lmt": "120",
                        "ut": "fa5fd1943c7b386f172d6893dbfba10b"},
                headers={"User-Agent": "Mozilla/5.0",
                          "Referer": "http://quote.eastmoney.com/"},
                timeout=20, proxies={"http": None, "https": None} if clear else None)
            d = r.json().get("data")
            if not d or not d.get("klines"):
                continue
            rows = [{"date": f[0][:10], "open": float(f[1]), "close": float(f[2]),
                     "high": float(f[3]), "low": float(f[4]), "vol": float(f[5])}
                    for f in (k.split(",") for k in d["klines"])]
            rows.sort(key=lambda x: x["date"])
            return rows
        except Exception:
            continue
    return None


def yf_kline(ticker, beg, end):
    """雅虎财经 v8 chart API 拉日线（纯 requests，免 yfinance 依赖）。
    Mac mini 直连可达；本环境封网时返回 None 由 RSS 兜底。"""
    try:
        import requests
    except ImportError:
        return None
    try:
        p1 = int(dt.datetime(int(beg[:4]), int(beg[4:6]), int(beg[6:8])).timestamp())
        ed = dt.datetime(int(end[:4]), int(end[4:6]), int(end[6:8])) + dt.timedelta(days=1)
        p2 = int(ed.timestamp())
        url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
               f"{urllib.parse.quote(ticker)}?period1={p1}&period2={p2}&interval=1d")
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20,
                         proxies={"http": None, "https": None})
        d = r.json().get("chart", {}).get("result")
        if not d:
            return None
        res = d[0]
        ts = res.get("timestamp", [])
        q = res.get("indicators", {}).get("quote", [{}])[0]
        closes, opens, highs, lows, vols = (q.get(k, []) for k in
                                            ("close", "open", "high", "low", "volume"))
        rows = []
        for i, t in enumerate(ts):
            c = closes[i]
            if c is None:
                continue
            dstr = dt.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d")
            rows.append({"date": dstr, "open": float(opens[i] or c),
                         "close": float(c), "high": float(highs[i] or c),
                         "low": float(lows[i] or c), "vol": float(vols[i] or 0)})
        rows.sort(key=lambda x: x["date"])
        return rows or None
    except Exception:
        return None


def fetch(sector, beg, end):
    s = sector["src"]
    if s == "wind":
        rows = wind_kline(sector["wind"], beg, end)
        if not rows and sector.get("wind2"):
            rows = wind_kline(sector["wind2"], beg, end)
        # 东财兜底(仅 YUPEN_USE_EM=1 且 Wind 不可达时)：保留猫哥原生 881xxx 口径
        if not rows and USE_EM and sector["name"] in EM_SECID:
            rows = em_kline(EM_SECID[sector["name"]], beg, end)
            return rows, "em"
        return rows, "wind"
    if s == "yf":
        return yf_kline(sector["yf"], beg, end), "yf"
    if s == "em" and USE_EM and sector["name"] in EM_SECID:
        return em_kline(EM_SECID[sector["name"]], beg, end), "em"
    return None, "none"


def ma20(rows):
    return sum(r["close"] for r in rows) / len(rows)


def slope_dir(rows20):
    if len(rows20) < 6:
        return "flat"
    d = rows20[-1]["close"] - rows20[-6]["close"]
    return "up" if d > 0 else "down" if d < 0 else "flat"


def compute(rows, target):
    upto = [r for r in rows if r["date"] <= target]
    if len(upto) < 20:
        return None
    last20 = upto[-20:]
    close = upto[-1]["close"]
    m = ma20(last20)
    dev = (close - m) / m * 100
    chg = (upto[-1]["close"] - upto[-2]["close"]) / upto[-2]["close"] * 100 if len(upto) >= 2 else 0
    vol5 = sum(r["vol"] for r in upto[-5:]) / 5
    vol20 = sum(r["vol"] for r in last20) / 20
    vr = vol5 / vol20 if vol20 else 1.0
    interval = (close - last20[0]["close"]) / last20[0]["close"] * 100
    return {"close": round(close, 2), "ma20": round(m, 2), "dev": round(dev, 2),
            "chg": round(chg, 2), "vr": round(vr, 2),
            "interval": round(interval, 2), "slope": slope_dir(upto)}


def build_table(cfg_list, target, beg, end, ref_path, ref_key, no_selfcheck=False):
    results, fails = [], []
    for s in cfg_list:
        if s.get("src") == "rss" and not (USE_EM and s["name"] in EM_SECID):
            fails.append(s["name"] + "(Wind未覆盖→RSS兜底)")
            continue
        rows, src = fetch(s, beg, end)
        if not rows:
            fails.append(s["name"])
            continue
        c = compute(rows, target)
        if not c:
            fails.append(s["name"] + "(<20日)")
            continue
        results.append({"name": s["name"], "code": s["code"], "ok": True, "src": src,
                        **c, "dev_color": "red" if c["dev"] > 0 else "green",
                        "approx": s.get("approx", "")})
        print(f"  ✓ {s['name']:8s}[{src}] {c['close']:.1f} ma20={c['ma20']:.1f} "
              f"dev={c['dev']:+.2f}% slope={c['slope']}", file=sys.stderr)
    ok = [r for r in results if r.get("ok")]
    ok.sort(key=lambda x: -x["dev"])
    for i, r in enumerate(ok, 1):
        r["rank"] = i
    n = len(ok)
    for r in ok:
        r["rps"] = round((n - r["rank"]) / (n - 1) * 100, 1) if n > 1 else 100.0
        r["overheat_warning"] = (r["dev"] > 15 and r["vr"] < 1.0)
    # 自检（仅回溯验证历史日时启用；每日实跑禁止，避免今天vs历史表的时序漂移误报）
    selfcheck, max_diff = None, 0.0
    if os.path.exists(ref_path) and not no_selfcheck:
        with open(ref_path) as f:
            ref = json.load(f)
        rm = {x["name"]: x for x in ref.get(ref_key, [])}
        selfcheck = []
        for r in ok:
            if r["name"] in rm and r["src"] == "wind":
                rd = float(rm[r["name"]]["deviation_pct"].rstrip("%"))
                diff = abs(r["dev"] - rd)
                selfcheck.append({"name": r["name"], "self": r["dev"],
                                  "cat": rm[r["name"]]["deviation_pct"],
                                  "diff_pp": round(diff, 2), "risk": diff > 1.0})
        max_diff = max((x["diff_pp"] for x in selfcheck), default=0.0)
    return ok, fails, selfcheck, max_diff


def write_table(out_path, date, data_type, ok, fails, selfcheck, max_diff, trend=False):
    out = {
        "date": date, "source": "自建·Wind+雅虎+东财(脱离微信RSS OCR)",
        "data_type": data_type, "article_title": "(自建生成，无原文)",
        "article_id": "", "fetch_time": dt.datetime.now(dt.timezone.utc).isoformat(),
        "sectors": [
            {"rank": r["rank"], "code": r["code"], "name": r["name"],
             "change_pct": f"{r['chg']:+.2f}%", "price": r["close"], "ma20": r["ma20"],
             "deviation_pct": f"{r['dev']:+.2f}%", "deviation_color": r["dev_color"],
             "volume_ratio": r["vr"], "state_date": "",
             "interval_change_pct": f"{r['interval']:+.2f}%", "rank_change": "",
             "ma20_slope": r["slope"], "rps": r["rps"],
             "overheat_warning": r["overheat_warning"], "src": r["src"],
             **({"approx": r["approx"]} if r.get("approx") else {})}
            for r in ok],
        "missing": fails,
        "selfcheck": selfcheck, "selfcheck_max_diff_pp": round(max_diff, 2),
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--out-dir", default=os.path.join(CLAW, "output/yupen"))
    ap.add_argument("--no-selfcheck", action="store_true",
                    help="每日实跑时禁用自检(避免今天vs历史表的时序漂移误报)")
    args = ap.parse_args()
    target, beg = args.date, (dt.date.fromisoformat(args.date)
                              - dt.timedelta(days=45)).strftime("%Y%m%d")
    end = target.replace("-", "")

    print(f"[板块轮动] {target}", file=sys.stderr)
    sr, sr_fail, sr_sc, sr_md = build_table(SECTORS, target, beg, end, CAT_REF, "sectors", args.no_selfcheck)
    srp = write_table(os.path.join(args.out_dir, f"yupen_primary_{target}_sector_rotation.json"),
                      target, "板块轮动·行情MA20偏离(自建)", sr, sr_fail, sr_sc, sr_md)

    print(f"[鱼盆趋势] {target}", file=sys.stderr)
    tr, tr_fail, tr_sc, tr_md = build_table(TREND, target, beg, end, CAT_REF_TREND, "sectors", args.no_selfcheck)
    trp = write_table(os.path.join(args.out_dir, f"yupen_primary_{target}_yupen_trend.json"),
                      target, "鱼盆趋势·全球指数MA20偏离(自建)", tr, tr_fail, tr_sc, tr_md)

    print(f"\n板块轮动: {len(sr)}/14 缺失={sr_fail or '无'} 自检={sr_md:.2f}pp", file=sys.stderr)
    print(f"鱼盆趋势: {len(tr)}/20 缺失={tr_fail or '无'} 自检={tr_md:.2f}pp", file=sys.stderr)
    print(f"输出:\n  {srp}\n  {trp}", file=sys.stderr)


if __name__ == "__main__":
    main()
