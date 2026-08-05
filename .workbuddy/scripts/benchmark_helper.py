#!/usr/bin/env python3
"""benchmark_helper.py — 持仓对标基准辅助脚本

落地来源: 全网调研 #1「无对标基准则数字无意义」。
功能: 计算每只持仓的**相对强弱**——其近 N 日区间收益 vs 沪深300(及所属行业ETF)
      区间收益，标注跑赢/跑输 N%，供早晚报模板「对标基准」列直接引用。

数据源: 腾讯 ifzq 前复权日K线（qt.gtimg.cn 同源优先级，与项目铁律一致）。
设计: 纯函数 + 网络降级；单只失败不影响其余；输出结构化 JSON 便于组装器消费。

用法:
  # 对持仓 JSON 计算对标基准（自动识别 positions dict / holdings array）
  python3 .workbuddy/scripts/benchmark_helper.py --portfolio .workbuddy/data/user/portfolio.json
  python3 .workbuddy/scripts/benchmark_helper.py --portfolio .workbuddy/data/simulation/portfolio.json

  # 对某只票单独看相对强弱（默认近 20 日）
  python3 .workbuddy/scripts/benchmark_helper.py --code 600584 --days 20

  # 作为模块 import
  from benchmark_helper import compute_holdings_benchmark, attach_benchmark
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
HS300_CODE = "sh000300"          # 沪深300 指数（对标基准核心）
DEFAULT_DAYS = 20

# ── 行业 → ETF 映射（用于「vs 行业」相对强弱）──────────────────────
# key=行业关键词(小写子串匹配)，value=ETF 代码(已带 sh/sz 前缀)
SECTOR_ETF_MAP: dict[str, str] = {
    "半导体": "sh512480", "芯片": "sh512760", "集成电路": "sh512760",
    "银行": "sh512800", "券商": "sh512000", "非银": "sh512070", "保险": "sh512070",
    "房地产": "sh512200", "地产": "sh512200",
    "医药": "sh512010", "医疗": "sh159828", "生物医药": "sh512290",
    "消费": "sh510150", "食品饮料": "sh515170", "白酒": "sh512690", "酒": "sh512690",
    "家电": "sh561120", "家用电器": "sh561120",
    "汽车": "sh516110", "新能源车": "sh515030", "新能源": "sh515030",
    "军工": "sh512660", "国防": "sh512660",
    "有色": "sh512400", "金属": "sh512400", "黄金": "sh518880",
    "煤炭": "sh515220", "钢铁": "sh515210", "化工": "sh516020",
    "电力": "sh561560", "光伏": "sh515790", "锂电": "sh515790",
    "传媒": "sh512980", "通信": "sh515880", "5g": "sh515880",
    "计算机": "sh159998", "软件": "sh515230", "信创": "sh515230",
    "电子": "sh159997", "农业": "sh159825", "建材": "sh516750",
    "机械": "sh516960", "工程机械": "sh516960", "建筑": "sh516970",
}


# ── 网络取数（腾讯 ifzq 前复权，与项目铁律一致）────────────────────
def _fetch_qfq_closes(code_prefixed: str, days: int) -> list[float] | None:
    """取近 days+5 根前复权日K线收盘，失败返回 None。"""
    try:
        url = (
            f"https://web.ifzq.gtimg.cn/appstuff/app/fqkline/get"
            f"?param={code_prefixed},day,,,{days + 5},qfq"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
            data = json.loads(resp.read().decode("utf-8"))
        sub = data.get("data", {})
        if not isinstance(sub, dict):
            return None
        kl = sub.get(code_prefixed, {}).get("qfqday", [])
        closes = [float(k[2]) for k in kl if len(k) > 2 and k[2]]
        return closes if len(closes) >= 2 else None
    except Exception:
        return None


def _interval_return(code_prefixed: str, days: int) -> float | None:
    """近 days 交易日区间收益(%)；不足数据返回 None。"""
    closes = _fetch_qfq_closes(code_prefixed, days)
    if not closes or len(closes) < 2:
        return None
    window = closes[-(days + 1):] if len(closes) > days else closes
    if len(window) < 2:
        return None
    return round((window[-1] - window[0]) / window[0] * 100, 2)


def _prefix(code: str) -> str:
    code = code.strip().lower()
    if code.startswith(("sh", "sz")):
        return code
    return f"sh{code}" if code.startswith("6") else f"sz{code}"


def _sector_to_etf(sector: str) -> str | None:
    if not sector:
        return None
    s = sector.lower()
    for key, etf in SECTOR_ETF_MAP.items():
        if key.lower() in s:
            return etf
    return None


# ── 核心计算 ────────────────────────────────────────────────────
def compute_holdings_benchmark(
    positions: dict[str, dict], days: int = DEFAULT_DAYS
) -> dict[str, dict]:
    """对持仓 dict(code -> {name, cost, current, sector}) 计算对标基准。

    返回: code -> {
        name, sector,
        holding_return_pct,        # 近 days 日该票区间收益
        hs300_return_pct,          # 沪深300 同期
        rel_vs_hs300,              # 相对强弱(票 - 指数)
        industry_etf, industry_return_pct, rel_vs_industry,
        benchmark_label,           # "跑赢沪深300 +x%" / "跑输..." 文本
    }
    任一项网络失败则为 None，不阻断其余。
    """
    hs300_ret = _interval_return(HS300_CODE, days)

    out: dict[str, dict] = {}
    for code, pos in positions.items():
        name = pos.get("name", code)
        sector = pos.get("sector", "未知")
        holding_ret = _interval_return(_prefix(code), days)

        rec: dict[str, Any] = {
            "code": code,
            "name": name,
            "sector": sector,
            "holding_return_pct": holding_ret,
            "hs300_return_pct": hs300_ret,
            "rel_vs_hs300": None,
            "industry_etf": None,
            "industry_return_pct": None,
            "rel_vs_industry": None,
            "benchmark_label": "数据缺失",
        }
        if holding_ret is not None and hs300_ret is not None:
            rel = round(holding_ret - hs300_ret, 2)
            rec["rel_vs_hs300"] = rel
            rec["benchmark_label"] = (
                f"跑赢沪深300 +{rel:.1f}%" if rel >= 0 else f"跑输沪深300 {rel:.1f}%"
            )

        # 行业 ETF 对标
        etf = _sector_to_etf(sector)
        if etf:
            rec["industry_etf"] = etf
            ind_ret = _interval_return(etf, days)
            if ind_ret is not None:
                rec["industry_return_pct"] = ind_ret
                if holding_ret is not None:
                    rel_i = round(holding_ret - ind_ret, 2)
                    rec["rel_vs_industry"] = rel_i
                    rec["benchmark_label"] += (
                        f" / 跑赢行业 +{rel_i:.1f}%"
                        if rel_i >= 0
                        else f" / 跑输行业 {rel_i:.1f}%"
                    )
        out[code] = rec
    return out


def _load_positions_from_portfolio(path: Path) -> dict[str, dict]:
    """从 portfolio JSON 提取 {code: {name, cost, current, sector}}。"""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

    positions: dict[str, dict] = {}
    # 模拟盘：positions dict
    sim = data.get("positions", {})
    if isinstance(sim, dict):
        for code, p in sim.items():
            positions[code] = {
                "name": p.get("name", code),
                "cost": p.get("avg_cost", 0),
                "current": p.get("current_price", p.get("avg_cost", 0)),
                "sector": p.get("sector", "未知"),
            }
    # 实盘：holdings array
    holdings = data.get("holdings", [])
    if isinstance(holdings, list):
        for h in holdings:
            if not isinstance(h, dict) or not h.get("code"):
                continue
            code = h["code"]
            positions[code] = {
                "name": h.get("name", code),
                "cost": h.get("avg_cost", 0),
                "current": h.get("current_price", h.get("avg_cost", 0)),
                "sector": h.get("sector", "未知"),
            }
    return positions


def main():
    ap = argparse.ArgumentParser(description="持仓对标基准辅助（vs 沪深300 / 行业ETF）")
    ap.add_argument("--portfolio", help="持仓 JSON 路径（positions dict 或 holdings array）")
    ap.add_argument("--code", help="单只股票代码（配合 --sector 看行业对标）")
    ap.add_argument("--sector", default="未知", help="单只股票所属行业（--code 时用）")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS, help="对标区间交易日数")
    args = ap.parse_args()

    if args.code:
        positions = {
            args.code: {"name": args.code, "cost": 0, "current": 0, "sector": args.sector}
        }
    elif args.portfolio:
        positions = _load_positions_from_portfolio(Path(args.portfolio))
    else:
        print("请提供 --portfolio 或 --code")
        sys.exit(1)

    if not positions:
        print("无持仓数据")
        return

    result = compute_holdings_benchmark(positions, days=args.days)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
