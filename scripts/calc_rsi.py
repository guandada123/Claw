#!/usr/bin/env python3
"""calc_rsi.py — 计算个股 RSI(14)

用法:
    python3 scripts/calc_rsi.py sh600522          # 单只，输出 RSI(14)
    python3 scripts/calc_rsi.py sh600522 sz000636  # 多只
    python3 scripts/calc_rsi.py 600522 000636      # 裸代码自动加前缀(6→sh, 其他→sz)

数据源: Wind 万得(优先) → 腾讯财经前复权日K (web.ifzq.gtimg.cn) → 新浪
降级: 网络失败/数据不足 → 输出 null + 原因，不抛异常（供自动化安全调用）
"""
from __future__ import annotations  # 兼容 3.9: X|Y 注解字符串化

import json
import sys
import urllib.request

try:
    import ssl
    _CTX = ssl.create_default_context()
    _CTX.check_hostname = False
    _CTX.verify_mode = ssl.CERT_NONE
except Exception:
    _CTX = None  # type: ignore[assignment]

# Wind 万得（可选依赖 — claw 包不存在也不阻断）
try:
    sys.path.insert(0, __file__ + "/../../src")
    from claw.feeds.wind_utils import get_wind_kline, wind_available
except ImportError:
    def wind_available() -> bool:  # type: ignore[misc]
        return False

    def get_wind_kline(code: str, days: int = 60, kline_type: str = "日K") -> None:  # type: ignore[misc]
        return None


def _prefix(code: str) -> str:
    code = code.strip().lower()
    if code.startswith(("sh", "sz")):
        return code
    return f"sh{code}" if code.startswith("6") else f"sz{code}"


def fetch_close(code_prefixed: str, n: int = 60) -> list[float]:
    """拉前复权日K收盘价序列（远端取 n+1 根，本地算 RSI）。

    Wind → 腾讯 ifzq → 新浪 K 线。
    """
    # 0) Wind 万得（优先）
    bare = code_prefixed[2:] if code_prefixed.startswith(("sh", "sz")) else code_prefixed
    if wind_available():
        try:
            klines = get_wind_kline(bare, days=n + 5)
            if klines and len(klines) >= n:
                closes = []
                for k in klines:
                    v = k.get("MATCH") or k.get("match") or k.get("close") or k.get("收盘价")
                    if v is not None:
                        closes.append(float(v))
                if len(closes) >= n:
                    return closes[-n:]
        except Exception:
            pass
    # 1) 腾讯 ifzq（前复权）
    try:
        url = (
            f"https://web.ifzq.gtimg.cn/appstuff/app/fqkline/get"
            f"?param={code_prefixed},day,,,{n+1},qfq"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=6, context=_CTX)  # nosec B310: ifzq.gtimg.cn
        raw = resp.read().decode("utf-8", errors="replace")
        d = json.loads(raw)
        kl = d.get("data", {}).get(code_prefixed, {}).get("qfqday") or []
        closes = [float(row[2]) for row in kl if len(row) > 2 and row[2]]
        if len(closes) >= n:
            return closes
    except Exception:
        pass

    # 2) 新浪回退
    sina_code = code_prefixed
    url = (
        f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php"
        f"/CN_MarketData.getKLineData?symbol={sina_code}&scale=240&ma=no&datalen={n+1}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=6)  # nosec B310: sina finance
    raw = resp.read().decode("utf-8", errors="replace")
    arr = json.loads(raw)
    closes = [float(row["close"]) for row in arr if row.get("close")]
    return closes


def rsi_wilder(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(rs / (1 + rs) * 100, 1)


def calc(code: str) -> dict:
    pref = _prefix(code)
    try:
        closes = fetch_close(pref, 60)
        rsi = rsi_wilder(closes, 14)
        if rsi is None:
            return {"code": pref, "rsi14": None, "reason": f"数据不足(仅{len(closes)}根)"}
        return {"code": pref, "rsi14": rsi, "bars": len(closes)}
    except Exception as e:  # noqa: BLE001
        return {"code": pref, "rsi14": None, "reason": str(e)[:80]}


def main():
    codes = sys.argv[1:]
    if not codes:
        print("用法: calc_rsi.py <code1> [code2 ...]  (支持 sh/sz 前缀或裸代码)")
        sys.exit(1)
    results = [calc(c) for c in codes]
    # 人类可读
    for r in results:
        if r["rsi14"] is None:
            print(f"{r['code']}: RSI(14)=N/A ({r.get('reason', '未知')})")
        else:
            print(f"{r['code']}: RSI(14)={r['rsi14']}")
    # 机器可读（最后一行 JSON，供自动化解析）
    print("JSON:" + json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
