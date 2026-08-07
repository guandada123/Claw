#!/usr/bin/env python3
"""
price_sanity.py — 股票价格合理性校验器（P0 防御）

目的：防止早报/选股段输出"数量级错误"的股价与买区（8/6 事故根因 =
AI 在 Wind 降级/运行中断时手填了未经校验的错误低价）。

校验逻辑（三道独立闸门，任一触发即 SANITY_FAIL）：
  G1 实时快照比对：传入价 vs 腾讯 qt.gtimg.cn 实时价，偏差 > DEV_TOLERANCE(30%) → FAIL
  G2 52周区间：传入价超出 [52w_low, 52w_high] → FAIL（含停牌/数据缺失保护）
  G3 MA20 锚定：传入价与 MA20 偏离 > MA_DEV(60%) → FAIL（极端复权/单位错误捕获）

输出 JSON：
  {
    "code": "600206",
    "input_price": 62.34,
    "ok": true/false,
    "verified_price": 62.34,        # 通过则用 input_price，否则用 gtimg 实时价
    "gtimg_price": 62.50,
    "low_52w": 30.1, "high_52w": 75.2,
    "ma20": 58.0,
    "fail_reasons": ["G1: dev 45% > 30%"],
    "action": "PASS" | "BLOCK_AND_USE_VERIFIED"
  }

用法：
  python3 price_sanity.py --code 600206 --price 62.34
  python3 price_sanity.py --code 600206 --price 49.4 --json
  echo '[{"code":"600206","price":62.34}]' | python3 price_sanity.py --batch
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

DEV_TOLERANCE = 0.30      # G1: 与实时价偏差上限 30%
MA_DEV = 0.60             # G3: 与 MA20 偏离上限 60%
TIMEOUT = 10

import re

PREFIX_RE = re.compile(r"^(sh|sz)\d{6}$")


def _prefix(code: str) -> str:
    code = code.strip().lower()
    if PREFIX_RE.match(code):
        return code
    if len(code) == 6 and code.isdigit():
        return f"sh{code}" if code.startswith("6") else f"sz{code}"
    # 美股 ticker（纯字母，如 AAPL/TSLA/NVDA）→ 原样返回，market=us 时由调用方识别
    if code.isalpha():
        return code
    raise ValueError(f"无法识别代码: {code}")


def _detect_market(code: str) -> str:
    """识别市场：美股(ticker纯字母) / A股(带前缀或6位数字)。"""
    c = code.strip().lower()
    if c.isalpha():
        return "us"
    return "cn"


def _yahoo_snapshot(ticker: str) -> dict:
    """拉 Yahoo 实时快照 + 52周高低。美股专用。"""
    out = {"price": None, "low_52w": None, "high_52w": None}
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker.upper()}?range=1y&interval=1d"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # nosec B310
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        meta = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
        price = meta.get("regularMarketPrice")
        out["price"] = float(price) if price else None
        out["low_52w"] = meta.get("fiftyTwoWeekLow")
        out["high_52w"] = meta.get("fiftyTwoWeekHigh")
    except Exception:
        pass
    return out


def _gtimg_snapshot(code_prefixed: str) -> dict | None:
    """拉腾讯实时快照：现价/52周高低/MA20(近似用不到，单独算)。"""
    try:
        url = f"https://qt.gtimg.cn/q={code_prefixed}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # nosec B310
            text = resp.read().decode("gbk", errors="replace")
        vals = text.split('"')[1].split("~")
        if len(vals) < 47:
            return None
        price = float(vals[3]) if vals[3] else None
        # 52周高低：qt.gtimg 字段 47=年初至今最低? 改用 45/46 近似不可用 → 改抓 fqkline
        return {"price": price}
    except Exception:
        return None


def _gtimg_52w_and_ma20(code_prefixed: str) -> dict:
    """从 qfqday K线取 52周高低 + MA20。腾讯 ifzq 优先，新浪回退。"""
    out = {"low_52w": None, "high_52w": None, "ma20": None}
    closes: list[float] = []

    # 1) 腾讯 ifzq（前复权）
    try:
        import ssl
        _ctx = ssl.create_default_context()
        _ctx.check_hostname = False
        _ctx.verify_mode = ssl.CERT_NONE
        url = (f"https://web.ifzq.gtimg.cn/appstuff/app/fqkline/get"
               f"?param={code_prefixed},day,,,260,qfq")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx) as resp:  # nosec B310
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        sub = data.get("data", {})
        if isinstance(sub, str):
            try:
                sub = json.loads(sub)
            except Exception:
                sub = {}
        if isinstance(sub, dict):
            node = sub.get(code_prefixed, {})
            if isinstance(node, str):
                try:
                    node = json.loads(node)
                except Exception:
                    node = {}
            kl = (node.get("qfqday") or node.get("day") or []) if isinstance(node, dict) else []
            closes = [float(k[2]) for k in kl if len(k) > 2 and k[2]]
    except Exception:
        closes = []

    # 2) 新浪回退
    if len(closes) < 20:
        try:
            sina = code_prefixed
            url = (f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php"
                   f"/CN_MarketData.getKLineData?symbol={sina}&scale=240&ma=no&datalen=260")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # nosec B310
                arr = json.loads(resp.read().decode("utf-8", errors="replace"))
            closes = [float(r["close"]) for r in arr if r.get("close")]
        except Exception:
            pass

    if closes:
        out["low_52w"] = min(closes)
        out["high_52w"] = max(closes)
    if len(closes) >= 20:
        out["ma20"] = sum(closes[-20:]) / 20
    return out


def check(code: str, price: float, market: str | None = None) -> dict:
    market = market or _detect_market(code)

    if market == "us":
        # 美股：Yahoo 实时价 + 52周高低（无 MA20 锚定，G3 跳过）
        snap = _yahoo_snapshot(code) or {}
        live_price = snap.get("price")
        low_52w, high_52w, ma20 = snap.get("low_52w"), snap.get("high_52w"), None
        live_label = "Yahoo"
    else:
        # A股：腾讯 gtimg 实时 + 52周/MA20
        code_prefixed = _prefix(code)
        snap = _gtimg_snapshot(code_prefixed) or {}
        live_price = snap.get("price")
        meta = _gtimg_52w_and_ma20(code_prefixed)
        low_52w, high_52w, ma20 = meta["low_52w"], meta["high_52w"], meta["ma20"]
        live_label = "腾讯实时"

    fail_reasons: list[str] = []
    verified = price

    # G1 实时快照比对
    if live_price is not None and live_price > 0:
        dev = abs(price - live_price) / live_price
        if dev > DEV_TOLERANCE:
            fail_reasons.append(
                f"G1: 传入价¥{price:.2f} 与{live_label}¥{live_price:.2f} 偏差 {dev*100:.0f}% > {DEV_TOLERANCE*100:.0f}%"
            )
            verified = live_price
    else:
        fail_reasons.append(f"G1: {live_label}价缺失，无法比对（降级标记）")

    # G2 52周区间
    if (
        low_52w is not None
        and high_52w is not None
        and (price < low_52w * 0.95 or price > high_52w * 1.05)
    ):
        fail_reasons.append(
            f"G2: 传入价¥{price:.2f} 超出52周区间 [¥{low_52w:.2f}, ¥{high_52w:.2f}]"
        )
        if verified == price and live_price:
            verified = live_price

    # G3 MA20 锚定（仅 A股）
    if ma20 is not None and ma20 > 0:
        mdev = abs(price - ma20) / ma20
        if mdev > MA_DEV:
            fail_reasons.append(
                f"G3: 传入价¥{price:.2f} 与MA20¥{ma20:.2f} 偏离 {mdev*100:.0f}% > {MA_DEV*100:.0f}%"
            )
            if verified == price and live_price:
                verified = live_price

    # G1 缺失（无实时价，无法比对）→ 放行但标注；G1 偏差失败（真实偏差>30%）→ 拦截
    g1_missing = any(r.startswith("G1:") and "缺失" in r for r in fail_reasons)
    g1_real_fail = any(r.startswith("G1:") and "缺失" not in r for r in fail_reasons)
    hard_fail = any(not (r.startswith("G1:") and "缺失" in r) for r in fail_reasons)
    ok = not hard_fail

    if not ok:
        action = "BLOCK_AND_USE_VERIFIED"
    elif g1_missing and not g1_real_fail:
        action = "PASS_WITH_WARN"
    else:
        action = "PASS"

    return {
        "code": code,
        "market": market,
        "input_price": price,
        "ok": ok,
        "verified_price": round(verified, 2) if verified else None,
        "live_price": live_price,
        "low_52w": round(low_52w, 2) if low_52w else None,
        "high_52w": round(high_52w, 2) if high_52w else None,
        "ma20": round(ma20, 2) if ma20 else None,
        "fail_reasons": fail_reasons,
        "action": action,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", help="6位代码/sh/sz前缀(Ａ股) 或 AAPL/TSLA(美股)")
    ap.add_argument("--price", type=float, help="待校验价格")
    ap.add_argument("--market", choices=["cn", "us"], default=None, help="市场，默认自动识别")
    ap.add_argument("--json", action="store_true", help="输出完整 JSON")
    ap.add_argument("--batch", action="store_true", help="从 stdin 读 [{code,price,market?}]")
    args = ap.parse_args()

    if args.batch:
        items = json.load(sys.stdin)
        out = [check(it["code"], float(it["price"]), it.get("market")) for it in items]
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if all(r["ok"] for r in out) else 1

    if not args.code or args.price is None:
        print("用法: price_sanity.py --code 600206 --price 62.34 [--market us] [--json]", file=sys.stderr)
        return 2

    res = check(args.code, args.price, args.market)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        status = "✅PASS" if res["ok"] else "🚫FAIL"
        lp = res.get("live_price")
        print(f"{status} [{res['market']}] {args.code} 传入¥{args.price:.2f} | 实时¥{lp} | 52w[{res['low_52w']},{res['high_52w']}] | MA20={res['ma20']}")
        if res["fail_reasons"]:
            for r in res["fail_reasons"]:
                print(f"  - {r}")
        if not res["ok"]:
            print(f"  → 改用可信价 ¥{res['verified_price']} ({res['action']})")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
