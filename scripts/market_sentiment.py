#!/usr/bin/env python3
"""
market_sentiment.py — 市场情绪层（大盘环境 + 板块强度）

2026-08-12 落地。用户指出: "最近的推荐都没考虑板块和市场情绪,
只是机械地按照技术指标来操作, 完全不具备参考性"。

设计原则:
  - 纯 HTTP（自动化环境无 MCP 也可用）
  - 数据源: 新浪K线(指数) + 东财(个股行业) + 腾讯板块行情(优先, 铁律)
  - 网络失败逐级降级为 None，绝不阻断主流程（与 advisor_rules 同原则）

模块:
  - market_regime()    大盘环境评分(强/中/弱 + score 0-100)
  - sector_strength()  个股所属行业板块当日强度(强/中/弱)
  - sentiment_context() 综合上下文(供推荐/入场过滤/做T 消费)

用法:
  python3 scripts/market_sentiment.py            # 打印当前情绪上下文
  python3 scripts/market_sentiment.py --sector sh600584
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

# ── 行业 → 腾讯板块代码 映射表（westock data_sector search 核实, pt 代码与腾讯兼容）──
# 覆盖用户持仓行业 + 常见推荐行业；新行业可在交互环境用 westock 查后补充
SECTOR_CODE_MAP: dict[str, str] = {
    "半导体": "pt01801081",  # 申万二级
    "电子": "pt01801082",  # 其他电子Ⅱ(申万二级近似)
    "元件": "pt01801083",  # 申万二级
    "通信设备": "pt01801102",
    "计算机设备": "pt01801094",
    "光伏设备": "pt01801735",
    "电池": "pt01801117",
    "化学制药": "pt01801151",
    "医药商业": "pt01801154",
    "医疗器械": "pt01801157",
    "汽车零部件": "pt01801093",
    "证券": "pt01801193",
    "银行": "pt01801192",
    "房地产开发": "pt01801221",
    "白酒": "pt01801225",
    "食品加工": "pt01801020",
    "电网设备": "pt01801118",
    "军工电子": "pt01801084",
    "消费电子": "pt01801108",
    "游戏": "pt01801201",
    "钢铁": "pt01801050",
    "有色金属": "pt01801050",  # 申万一级（西游核实 08-12）
    "煤炭开采": "pt01801026",
    "保险": "pt01801191",
    "工程机械": "pt01801110",
    "公用事业": "pt01801160",  # 申万一级（westock 核实 08-12）
    "家用电器": "pt01801110",  # 申万一级（westock 核实 08-12）
}

# ── 大盘环境阈值 ──
REGIME_STRONG = 70  # score ≥70 → 强
REGIME_WEAK = 40  # score <40 → 弱
INDEX_SYMBOLS = [("sh000001", "上证指数"), ("sz399006", "创业板指")]
RALLY_WINDOW = 60  # 自低点反弹观察窗口(K线根数)

# 行业名缓存（东财 push2 被风控 2026-08-12 后，行业名以 MCP 预置缓存为准）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SECTOR_CACHE = PROJECT_ROOT / ".workbuddy" / "data" / "sector_cache.json"
_industry_cache: dict[str, str] | None = None


def _load_industry_cache() -> dict[str, str]:
    """读取行业缓存（code → 行业名），失败返回空"""
    global _industry_cache
    if _industry_cache is not None:
        return _industry_cache
    _industry_cache = {}
    if SECTOR_CACHE.exists():
        try:
            d = json.loads(SECTOR_CACHE.read_text(encoding="utf-8"))
            _industry_cache = {k: v for k, v in d.items() if k.isdigit() and isinstance(v, str)}
        except (OSError, json.JSONDecodeError):
            _industry_cache = {}
    return _industry_cache


def _fetch_kline(symbol: str, n: int = 60) -> list[tuple[str, float, float, float]] | None:
    """新浪日K: [(day, close, low, high)]，失败返回 None"""
    try:
        url = (
            f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php"
            f"/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={n}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310: https 硬编码
            arr = json.loads(resp.read().decode("utf-8"))
        return [
            (row["day"], float(row["close"]), float(row["low"]), float(row["high"]))
            for row in arr
            if row.get("close") is not None
        ]
    except Exception:
        return None


def _fetch_tencent_quote(code: str) -> dict | None:
    """腾讯实时行情: {name, price, change_pct}，失败返回 None"""
    try:
        url = f"https://qt.gtimg.cn/q={code}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        text = urllib.request.urlopen(req, timeout=8).read().decode("gbk", errors="replace")
        vals = text.split('"')[1].split("~")
        if len(vals) > 32 and vals[1] and vals[3]:
            return {
                "name": vals[1],
                "price": float(vals[3]),
                "change_pct": float(vals[32]) if vals[32] else 0.0,
            }
    except Exception:
        pass
    return None


def _fetch_industry_name(code: str) -> str | None:
    """个股行业名：优先本地缓存，未命中再试东财 f127，成功后回填缓存。

    东财 push2 2026-08-12 起被风控(RemoteDisconnected)，缓存为主要来源；
    新增标的可在交互会话用 westock data_profile 补充到 data/sector_cache.json。
    """
    cache = _load_industry_cache()
    if code in cache:
        return cache[code]
    secid = f"1.{code}" if code.startswith("6") else f"0.{code}"
    try:
        url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields=f127"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        d = json.loads(urllib.request.urlopen(req, timeout=6).read().decode("utf-8"))
        v = (d.get("data") or {}).get("f127")
        if v:
            cache[code] = str(v)
            _save_industry_cache(cache)
            return str(v)
    except Exception:
        pass
    return None


def _save_industry_cache(cache: dict[str, str]) -> None:
    """回写行业缓存（合并既有非行业字段如 source/note）"""
    try:
        old: dict = {}
        if SECTOR_CACHE.exists():
            old = json.loads(SECTOR_CACHE.read_text(encoding="utf-8"))
        old.update({k: v for k, v in cache.items() if k.isdigit()})
        SECTOR_CACHE.write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8")
    except (OSError, json.JSONDecodeError):
        pass


class MarketSentiment:
    """市场情绪层 — 只读纯函数，网络失败降级"""

    # ════════════════════════════════════════════════════════════
    # 大盘环境
    # ════════════════════════════════════════════════════════════
    def market_regime(self) -> dict:
        """大盘环境评分 → {regime, score, basis, indexes}

        维度: 自60日低点反弹(40%) / 站上MA20(30%) / 近5日动量(30%)
        """
        scores: list[float] = []
        basis: list[str] = []
        indexes: dict[str, Any] = {}
        for symbol, name in INDEX_SYMBOLS:
            kl = _fetch_kline(symbol, RALLY_WINDOW)
            if not kl or len(kl) < 25:
                continue
            closes = [k[1] for k in kl]
            lows = [k[2] for k in kl]
            cur = closes[-1]
            # 自低点反弹
            low = min(lows)
            rally = (cur / low - 1) * 100 if low > 0 else 0.0
            s_rally = min(rally / 8.0 * 40, 40.0)  # 反弹8%得满40分
            # 站上MA20
            ma20 = sum(closes[-20:]) / 20
            above = cur >= ma20
            s_ma = 30.0 if above else (0.0 if cur < ma20 * 0.98 else 15.0)
            # 近5日动量
            m5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else 0.0
            s_m5 = max(min(m5 / 2.0 * 30, 30.0), 0.0)
            total = s_rally + s_ma + s_m5
            scores.append(total)
            basis.append(
                f"{name} 自低点+{rally:.1f}% 现价{'站上' if above else '低于'}MA20({ma20:.0f}) "
                f"近5日{m5:+.1f}% → {total:.0f}分"
            )
            indexes[name] = {
                "current": round(cur, 2),
                "low": round(low, 2),
                "rally_pct": round(rally, 1),
                "ma20": round(ma20, 2),
                "above_ma20": above,
                "m5_pct": round(m5, 1),
            }
        if not scores:
            return {"regime": "未知", "score": None, "basis": ["数据源不可用"], "indexes": {}}
        score = sum(scores) / len(scores)
        regime = "强" if score >= REGIME_STRONG else ("弱" if score < REGIME_WEAK else "中")
        return {"regime": regime, "score": round(score, 1), "basis": basis, "indexes": indexes}

    # ════════════════════════════════════════════════════════════
    # 板块强度
    # ════════════════════════════════════════════════════════════
    def sector_strength(self, code: str) -> dict | None:
        """个股所属行业板块当日强度 → {sector, board_code, change_pct, strength}

        链路: 东财行业名 → 映射表板块代码 → 腾讯板块行情(优先腾讯, 铁律)
        行业名不在映射表 → 返回 None（不阻断，由调用方降级）
        """
        industry = _fetch_industry_name(code)
        if not industry:
            return None
        board_code = SECTOR_CODE_MAP.get(industry)
        if not board_code:
            return {
                "sector": industry,
                "board_code": None,
                "change_pct": None,
                "strength": None,
                "note": f"行业「{industry}」无板块代码映射，可在交互环境用自选股MCP补充",
            }
        q = _fetch_tencent_quote(board_code)
        if not q:
            return {
                "sector": industry,
                "board_code": board_code,
                "change_pct": None,
                "strength": None,
                "note": "板块行情获取失败",
            }
        zdf = q["change_pct"]
        strength = "强" if zdf >= 1.0 else ("中" if zdf >= 0 else "弱")
        return {
            "sector": industry,
            "board_code": board_code,
            "board_name": q["name"],
            "change_pct": round(zdf, 2),
            "strength": strength,
            "note": f"板块「{q['name']}」当日{zdf:+.2f}% → {strength}",
        }

    # ════════════════════════════════════════════════════════════
    # 综合上下文
    # ════════════════════════════════════════════════════════════
    def sentiment_context(self, code: str | None = None) -> dict:
        """综合情绪上下文（供推荐/入场过滤/做T 消费）"""
        ctx: dict[str, Any] = {"regime": self.market_regime()}
        if code:
            ctx["sector"] = self.sector_strength(code)
        return ctx


def main():
    parser = argparse.ArgumentParser(description="市场情绪层")
    parser.add_argument("--sector", default=None, help="查个股所属板块强度(6位代码)")
    args = parser.parse_args()

    ms = MarketSentiment()
    ctx = ms.sentiment_context(args.sector)
    out = {"regime": ctx["regime"]}
    if args.sector:
        out["sector"] = ctx["sector"]
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
