#!/usr/bin/env python3
"""
sector_strength_layer.py — 板块强弱层（2026-08-14 落地，复盘「砍科技」后修复）

═══════════════════════════════════════════════════════════════════
背景 / 为什么需要它
───────────────────────────────────────────────────────────────────
2026-08 初系统在「大级别板块启动前(08-03 绝对底部)」砍/弃科技与半导体。
根因(market_sentiment.py:391)是板块强弱用「单日涨跌幅」判定(强≥1%/中≥0/弱<0)，
动量-only 在筑底区把最强 upcoming 板块判为最弱，且入场闸(advisor_rules:242)
对「弱板块」直接 block 推荐 → 砍在启动前。

本模块用 QTS PG daily_quote 的**多周期动量**重新定义板块强弱：
  - 5/10/20 日动量 + MA20 位置 + 跨板块 RPS 分位（取代单日涨跌幅，修复 R1）
  - 额外输出「早期转折」信号：弱势板块中动量拐头/站上 MA20 的候选
    （修复 R2：避免只追已涨高、错过刚启动——这是本层的核心交付）

输出（stdout JSON + 落库 data/sector_strength.json）：
  - sectors: 全部板块按综合强度排序，含 mom_5/mom_20/ma20_pos/rps/turning/label
  - top3: 当前最强板块 Top3（用户明确要求「每天输出当前最强板块 Top3」）
  - early_turning: 早期转折候选（弱势但动量拐头，= 即将启动）
  - scan_focus: 强势板块内扫的聚焦代码（仅允许板块：主板+创业板，禁科创/北交）

数据源：QTS PG（经 qts_client 只读，铁律：Claw 只消费 QTS，不写）
失败降级：PG 不可用时返回 None，调用方回退旧逻辑（绝不阻断主流程）。

用法：
  python3 scripts/sector_strength_layer.py              # 计算+打印+落库
  python3 scripts/sector_strength_layer.py --quiet      # 只落库不打印
  python3 scripts/sector_strength_layer.py --no-write   # 只打印不落库
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

# ── 路径 / 依赖 ───────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
import qts_client as qts  # 经授权服务层只读 PG（铁律：禁直连 QTS 代码，走 qts_client）

OUTPUT = PROJECT_ROOT / ".workbuddy" / "data" / "sector_strength.json"

# 允许板块（对齐 sim_trade RESTRICTED_PREFIXES：主板60/00 + 创业板30，禁 688/689/8/4）
ALLOWED_PREFIXES = ("60", "00", "30")
BLOCKED_PREFIXES = ("688", "689", "8", "4")


def _allowed(code: str) -> bool:
    return code.startswith(ALLOWED_PREFIXES) and not code.startswith(BLOCKED_PREFIXES)


# ── 板块 → 代表股（A 股各板块流动性龙头，用于动量探针）────────────
# 说明：代表股含部分科创板(688)用于准确刻画板块动量，但 scan_focus 导出时
#       按 _allowed() 过滤，仅保留可交易板块（主板+创业板）。
SECTORS: dict[str, list[str]] = {
    "半导体": ["688981", "002371", "603986", "603501", "600584", "002185", "688041"],
    "消费电子": ["002475", "002241", "601138", "688036", "300136"],
    "PCB/元件": ["002463", "600183", "002916", "300476"],
    "通信设备": ["300308", "300502", "000063", "600522"],
    "有色金属": ["601899", "600362", "603993", "600547"],
    "化工": ["600309", "600426", "002648", "600989"],
    "新能源/电池": ["300750", "002594", "300274", "688599"],
    "医药": ["600276", "603259", "300760", "000661"],
    "银行": ["600036", "601398", "601166", "600000"],
    "证券": ["300059", "600030", "601688", "600837"],
    "军工": ["600760", "600893", "000768", "002179"],
    "建筑/基建": ["601668", "601800", "601186", "601390"],
    "白酒/消费": ["600519", "000858", "000333", "600887"],
    "煤炭": ["601088", "600585", "601225", "600188"],
    "家电": ["000651", "600690", "000333"],
    "汽车": ["601633", "601238", "002920", "600104"],
    "软件/计算机": ["002230", "600570", "300033", "688111"],
}

# 动量权重（综合强度评分）
W_MOM20, W_MOM5, W_MA20POS = 0.5, 0.3, 0.2
LOOKBACK = 60  # 取最近 60 根日线
EARLY_TURN_MOM_SLOPE = 0.02  # 动量拐头阈值(5d-20d)
EARLY_TURN_MOM20_CAP = 0.05  # 早期转折要求 20d 动量尚未过热


# ── 单股动量指标 ───────────────────────────────────────────────
def _stock_metrics(code: str) -> dict | None:
    rows = qts.get_kline(code, LOOKBACK)
    if not rows or len(rows) < 22:
        return None
    # rows DESC → 反转为时间升序
    closes = [float(r["close"]) for r in reversed(rows)]
    n = len(closes)
    cur = closes[-1]
    mom20 = (cur / closes[-21] - 1) if n >= 21 else 0.0
    mom10 = (cur / closes[-11] - 1) if n >= 11 else 0.0
    mom5 = (cur / closes[-6] - 1) if n >= 6 else 0.0
    ma20 = sum(closes[-20:]) / 20
    ma20_pos = (cur / ma20 - 1) if ma20 > 0 else 0.0
    # 动量斜率：短期相对中期是否加速（拐头向上）
    mom_slope = mom5 - mom20
    return {
        "code": code,
        "mom5": mom5,
        "mom10": mom10,
        "mom20": mom20,
        "ma20_pos": ma20_pos,
        "mom_slope": mom_slope,
        "reclaimed_ma20": (cur > ma20) and (closes[-20] <= sum(closes[-40:-20]) / 20 if n >= 40 else False),
    }


# ── 板块聚合 ───────────────────────────────────────────────────
def _sector_agg(name: str, codes: list[str]) -> dict | None:
    ms = [_stock_metrics(c) for c in codes]
    ms = [m for m in ms if m]
    if not ms:
        return None
    import statistics

    def med(key):
        return statistics.median(m[key] for m in ms)

    mom5, mom20, ma20_pos = med("mom5"), med("mom20"), med("ma20_pos")
    mom_slope = med("mom_slope")
    reclaimed = sum(1 for m in ms if m["reclaimed_ma20"]) / len(ms)
    # 早期转折：动量拐头向上，但 20d 尚未过热（= 筑底反转 / 刚启动）
    early = (mom_slope > EARLY_TURN_MOM_SLOPE) and (mom20 < EARLY_TURN_MOM20_CAP)
    # 已站上 MA20 但 20d 仍为负 = 底部收复
    bottom_reclaim = (ma20_pos > 0) and (mom20 < 0)
    if mom20 >= 0.08 and mom5 >= 0:
        label = "强"
    elif early or bottom_reclaim:
        label = "早期转折"
    elif mom20 >= 0:
        label = "中"
    else:
        label = "弱"
    composite = mom20 * W_MOM20 + mom5 * W_MOM5 + ma20_pos * W_MA20POS
    return {
        "sector": name,
        "n_stocks": len(ms),
        "mom5": round(mom5, 4),
        "mom10": round(med("mom10"), 4),
        "mom20": round(mom20, 4),
        "ma20_pos": round(ma20_pos, 4),
        "mom_slope": round(mom_slope, 4),
        "reclaim_ratio": round(reclaimed, 2),
        "early": bool(early or bottom_reclaim),
        "label": label,
        "composite": round(composite, 4),
    }


def compute(date_str: str | None = None) -> dict | None:
    """计算全板块强弱。返回 dict 或 None(PG 失败)。"""
    sectors = []
    for name, codes in SECTORS.items():
        agg = _sector_agg(name, codes)
        if agg:
            sectors.append(agg)
    if not sectors:
        return None
    # 跨板块 RPS：按 composite 排名分位(0-100)
    sectors_sorted = sorted(sectors, key=lambda s: s["composite"])
    n = len(sectors_sorted)
    for i, s in enumerate(sectors_sorted):
        s["rps"] = round((i + 1) / n * 100, 1)
    # 重新按 composite 降序排（强→弱）
    sectors_sorted = sorted(sectors, key=lambda s: s["composite"], reverse=True)
    top3 = sectors_sorted[:3]
    early_turning = [s for s in sectors_sorted if s["early"] and s["label"] == "早期转折"]
    early_turning = sorted(early_turning, key=lambda s: s["mom_slope"], reverse=True)
    # 扫描聚焦：Top3 + 早期转折 板块的代表股（仅允许板块）
    focus_sectors = [s["sector"] for s in top3] + [s["sector"] for s in early_turning]
    focus_codes = []
    for sec in focus_sectors:
        for c in SECTORS.get(sec, []):
            if _allowed(c) and c not in focus_codes:
                focus_codes.append(c)
    return {
        "report_date": date_str or date.today().isoformat(),
        "source": "QTS PG daily_quote (via qts_client, readonly)",
        "sectors": sectors_sorted,
        "top3": [{"sector": s["sector"], "label": s["label"], "composite": s["composite"],
                  "mom20": s["mom20"], "mom5": s["mom5"], "ma20_pos": s["ma20_pos"], "rps": s["rps"]}
                 for s in top3],
        "early_turning": [{"sector": s["sector"], "mom_slope": s["mom_slope"],
                            "mom20": s["mom20"], "ma20_pos": s["ma20_pos"]} for s in early_turning],
        "scan_focus_sectors": focus_sectors,
        "scan_focus_codes": focus_codes,
    }


def get_sector_label(code: str) -> dict | None:
    """给定个股代码，返回其所属板块的强弱标签（供 advisor_rules 入场闸消费）。

    返回 {sector, label, early, rps, mom20} 或 None(层不可用/个股不在代表股中)。
    注意：若个股非代表股，回退由调用方处理（旧单日逻辑）。
    """
    # 先算全层（带缓存由调用方负责；本函数每次重算，调用方应低频调用）
    data = compute()
    if not data:
        return None
    for sec, codes in SECTORS.items():
        if code in codes:
            s = next((x for x in data["sectors"] if x["sector"] == sec), None)
            if s:
                return {"sector": sec, "label": s["label"], "early": s["early"],
                        "rps": s["rps"], "mom20": s["mom20"], "ma20_pos": s["ma20_pos"]}
    return None


def format_scan_focus_prompt(date_str: str | None = None) -> str:
    """生成粘贴到选股 prompt 的『强势板块内扫』上下文块。

    选股 prompt 应优先在 scan_focus_codes 内生成候选（禁科创/北交），
    并把 early_turning 板块视为布局窗口而非回避（修复复盘「砍科技」R2）。
    PG 不可用时返回空串（调用方回退全市场均匀扫）。
    """
    data = compute(date_str)
    if not data:
        return ""
    t = "、".join(f"{s['sector']}({s['label']})" for s in data["top3"])
    e = "、".join(f"{s['sector']}" for s in data["early_turning"]) or "无"
    codes = " ".join(data["scan_focus_codes"])
    return (
        "【板块强弱层·强势板块内扫】(源:data/sector_strength.json, QTS PG 多周期动量)\n"
        f"  当前最强板块 Top3: {t}\n"
        f"  早期转折候选(布局窗口,非回避): {e}\n"
        f"  扫描聚焦板块: {', '.join(data['scan_focus_sectors'])}\n"
        f"  聚焦候选代码(仅主板+创业板,禁科创/北交): {codes}\n"
        "  → 候选生成优先在上方聚焦代码内；早期转折板块可逢回调布局，不得按『弱板块』暂缓推荐。"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="板块强弱层")
    ap.add_argument("--quiet", action="store_true", help="只落库不打印")
    ap.add_argument("--no-write", action="store_true", help="只打印不落库")
    ap.add_argument("--date", default=None, help="报告日期 YYYY-MM-DD")
    args = ap.parse_args()

    data = compute(args.date)
    if data is None:
        print(json.dumps({"error": "PG 不可用，板块强弱层降级（调用方回退旧逻辑）"}, ensure_ascii=False))
        return 1

    if not args.no_write:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.quiet:
        t = data["top3"]
        e = data["early_turning"]
        print(f"📊 板块强弱层 | {data['report_date']} | 源: {data['source']}")
        print("─" * 56)
        print("【当前最强板块 Top3】")
        for i, s in enumerate(t, 1):
            print(f"  {i}. {s['sector']:10s} {s['label']:6s} 综合{s['composite']:+.3f} "
                  f"| 20d {s['mom20']:+.1%} 5d {s['mom5']:+.1%} MA20 {s['ma20_pos']:+.1%} RPS{s['rps']}")
        print("─" * 56)
        if e:
            print("【早期转折候选】（弱势但动量拐头，即将启动）")
            for s in e:
                print(f"  ⚠️ {s['sector']:10s} 斜率 {s['mom_slope']:+.1%} "
                      f"| 20d {s['mom20']:+.1%} MA20 {s['ma20_pos']:+.1%}")
        else:
            print("【早期转折候选】无")
        print("─" * 56)
        print(f"【扫描聚焦】板块={data['scan_focus_sectors']}")
        print(f"  聚焦代码({len(data['scan_focus_codes'])}只, 仅允许板块): {data['scan_focus_codes']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
