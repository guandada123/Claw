#!/usr/bin/env python3
"""
sync_lhb_signals.py — 龙虎榜信号接入信号仓库（公众号-independent 信号源）

背景（2026-09-01）:
  公众号 RSS 上游(wechatrss.waytomaster.com)自 08-19 起 HTTP 401 失效(套餐到期/token 吊销),
  验证层信号饥饿。龙虎榜是纯市场数据(无外部 auth 依赖), 作为新的独立信号源补验证层,
  直接"扩大选股池"的信号广度。

用法:
  python3 sync_lhb_signals.py                         # 读默认 /tmp/lhb_<今日>.json 并入库
  python3 sync_lhb_signals.py --file /tmp/lhb_2026-09-01.json
  python3 sync_lhb_signals.py --fetch-eastmoney       # 自取东方财富龙虎榜(无 MCP 依赖, 供每日自动化)
  python3 sync_lhb_signals.py --dry                   # 只统计不写盘
  python3 sync_lhb_signals.py --date 2026-09-01

入库规范(对齐 article_signals.json schema):
  - account / source = "龙虎榜"
  - stock_code = 6位字符串(去 sh/sz 前缀)
  - signal = "bullish" (仅取买方侧 jg/gslmr, 净买为正)
  - confidence = 1~10 由净买强度映射
  - verified = false (新信号, 待后续回测验证; compute_signal_weights 仅给≥3验证账户算权重, 故龙虎榜暂用 _default=0.5)
  - article_id = md5("LHB|code|date|tab") 按 股+tab 去重(1日/3日同股同tab只留首条)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent          # .workbuddy/scripts
PROJECT_ROOT = SCRIPT_DIR.parent.parent               # /Users/guan/WorkBuddy/Claw
SIGNALS_FILE = PROJECT_ROOT / ".workbuddy" / "data" / "article_signals.json"
SOURCE_WEIGHTS = PROJECT_ROOT / "data" / "source_weights.json"
DATA_DIR = PROJECT_ROOT / "data"

ACCOUNT = "龙虎榜"
WEIGHT_DEFAULT = 0.6  # 龙虎榜作为机构/游资共识信号, 给一个高于 _default(0.5) 的起始权重


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def strip_code(raw: str) -> str:
    """sz002886 / sh600519 -> 002886 / 600519"""
    raw = (raw or "").lower()
    for p in ("sh", "sz", "hk"):
        if raw.startswith(p) and len(raw) > 2:
            return raw[2:]
    return raw


def conf_jg(net_buy_rate: float) -> int:
    """机构净买率(%) -> 置信度 2~10"""
    return int(clamp(round((net_buy_rate or 0) / 4), 2, 10))


def conf_gslmr(win_num, up_rate) -> int:
    """游资净买: 席位净买家数 + 涨停加成 -> 置信度 2~10"""
    w = int(win_num or 0)
    c = 3 + w
    if (up_rate or 0) >= 9.5:
        c += 1
    return int(clamp(c, 2, 10))


def normalize(lhb: dict, trade_date: str) -> list[dict]:
    """将 westock data_lhb 结构( jg / gslmr 买方侧 ) 转为 article_signals schema 列表。"""
    out = []
    seen = set()  # (code, tab) 去重

    def add(code_raw, name, tab, signal_kind, conf, summary):
        code = strip_code(code_raw)
        key = (code, tab)
        if key in seen:
            return
        seen.add(key)
        art_id = hashlib.md5(
            f"LHB|{code}|{trade_date}|{tab}".encode(), usedforsecurity=False
        ).hexdigest()[:12]
        out.append({
            "article_id": art_id,
            "account": ACCOUNT,
            "title": f"龙虎榜·{signal_kind} {name} {summary}",
            "stock_code": code,
            "stock_name": name,
            "signal": "bullish",
            "target_price": None,
            "confidence": conf,
            "recorded_at": trade_date,
            "verified": False,
            "hit_target": None,
            "hit_stop": None,
            "final_return_pct": None,
            "source_file": None,
            "realtime_chg_pct": None,
            "realtime_price": None,
            "hit": None,
            "verify_note": None,
            "verify_at": None,
            "source": ACCOUNT,
            "code_status": "ok",
            "_lhb_tab": tab,  # 保留来源 tab, 便于后续审计(不影响消费层)
        })

    # 机构榜 (净买为正 = 机构净买入, bullish)
    for r in lhb.get("jg", []) or []:
        net = float(r.get("netBuyAmt") or 0)
        if net <= 0:
            continue
        add(r.get("code"), r.get("name"), "jg",
            "机构", conf_jg(r.get("netBuyRate")),
            f"净买率{r.get('netBuyRate')}%")

    # 敢死队/游资买入榜 (净买为正 = bullish)
    for r in lhb.get("gslmr", []) or []:
        net = float(r.get("netAmt") or 0)
        if net <= 0:
            continue
        add(r.get("code"), r.get("name"), "gslmr",
            "游资", conf_gslmr(r.get("winNum"), r.get("upRate")),
            f"净买+{r.get('upRate')}%")

    return out


def fetch_eastmoney(trade_date: str) -> dict:
    """自取东方财富龙虎榜明细(无 MCP 依赖), 转 westock 近似结构。

    东方财富 RPT_DAILYBILLBOARD_DETAILS 字段:
      SECURITY_CODE, SECURITY_NAME_ABBR, TRADE_DATE, EXPLAIN, CLOSE_PRICE,
      CHANGE_RATE, BILLBOARD_BUY_AMT, BILLBOARD_SELL_AMT
    - 净买 = BILLBOARD_BUY_AMT - BILLBOARD_SELL_AMT
    - EXPLAIN 携带席位类型: "机构买入" / "游资买入" / "敢死队买入" / "普通席位买入"
    """
    import urllib.request
    cols = ("SECURITY_CODE,SECURITY_NAME_ABBR,TRADE_DATE,EXPLAIN,CLOSE_PRICE,"
            "CHANGE_RATE,BILLBOARD_BUY_AMT,BILLBOARD_SELL_AMT")
    url = (
        "https://datacenter-web.eastmoney.com/api/data/v1/get"
        f"?reportName=RPT_DAILYBILLBOARD_DETAILS&columns={cols}"
        f"&filter=(TRADE_DATE%3D%27{trade_date}%27)"
        "&pageSize=400&sortColumns=BILLBOARD_BUY_AMT&sortTypes=-1"
        "&source=WEB&client=WEB&p=1"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw = json.loads(urllib.request.urlopen(req, timeout=25).read().decode("utf-8"))
    if not raw.get("success"):
        raise RuntimeError(f"eastmoney LHB 失败: {raw.get('message')}")
    rows = (raw.get("result") or {}).get("data") or []
    jg, gslmr = [], []
    for r in rows:
        code = str(r.get("SECURITY_CODE", ""))
        name = r.get("SECURITY_NAME_ABBR", "")
        buy = float(r.get("BILLBOARD_BUY_AMT") or 0)
        sell = float(r.get("BILLBOARD_SELL_AMT") or 0)
        net = buy - sell
        up = float(r.get("CHANGE_RATE") or 0)
        explain = str(r.get("EXPLAIN", ""))
        if net <= 0:
            continue
        rec = {"code": code, "name": name, "tdDays": 1, "netBuyAmt": net,
               "netAmt": net, "upRate": up, "winNum": 1, "instBuyAmt": net}
        if "机构" in explain:
            rec["netBuyRate"] = round(net / buy * 100, 2) if buy else 0.0
            jg.append(rec)
        elif ("游资" in explain) or ("敢死队" in explain):
            gslmr.append(rec)
    return {"date": trade_date, "jg": jg, "gslmr": gslmr}


def add_weight():
    """在 source_weights.json 的 weights 里登记龙虎榜起始权重(幂等, 不覆盖已有)。"""
    if not SOURCE_WEIGHTS.exists():
        return False
    try:
        d = json.loads(SOURCE_WEIGHTS.read_text(encoding="utf-8"))
    except Exception:
        return False
    w = d.setdefault("weights", {})
    if ACCOUNT not in w:
        w[ACCOUNT] = WEIGHT_DEFAULT
        SOURCE_WEIGHTS.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="LHB JSON 路径(westock data_lhb 结构)")
    ap.add_argument("--date", default=date.today().isoformat(), help="交易日 YYYY-MM-DD")
    ap.add_argument("--fetch-eastmoney", action="store_true", help="自取东方财富龙虎榜(无 MCP)")
    ap.add_argument("--dry", action="store_true", help="只统计不写盘")
    args = ap.parse_args()

    # 1) 取 LHB 数据 (优先级: --fetch-eastmoney 强制 > --file > 默认 /tmp/lhb_日期.json > 东方财富自取)
    if args.fetch_eastmoney:
        print(f"[fetch] 东方财富自取 {args.date} ...")
        lhb = fetch_eastmoney(args.date)
    elif args.file:
        lhb = json.loads(Path(args.file).read_text(encoding="utf-8"))
        if "data" in lhb:
            lhb = lhb["data"]
    else:
        default = DATA_DIR / f"lhb_{args.date}.json"
        if default.exists():
            lhb = json.loads(default.read_text(encoding="utf-8"))
            if "data" in lhb:
                lhb = lhb["data"]
        else:
            print(f"[fetch] 无本地 LHB 文件, 东方财富自取 {args.date} ...")
            lhb = fetch_eastmoney(args.date)

    trade_date = lhb.get("date") or args.date
    signals = normalize(lhb, trade_date)
    print(f"[normalize] {trade_date}: 买方侧(jg+gslmr) 净买为正 -> {len(signals)} 条候选信号")

    # 2) 读取现有仓库 + 去重
    existing = json.loads(SIGNALS_FILE.read_text(encoding="utf-8")) if SIGNALS_FILE.exists() else []
    existing_ids = {s.get("article_id") for s in existing}
    before = len(existing)
    new = [s for s in signals if s["article_id"] not in existing_ids]
    dup = len(signals) - len(new)
    print(f"[dedup] 仓库现有 {before} 条; 本次新增 {len(new)} 条 (跳过重复 {dup} 条)")

    if args.dry:
        print("[dry] 未写盘")
        return

    # 3) 写盘: 追加到 article_signals.json (剥离内部 _lhb_tab 后写)
    merged = existing + [{k: v for k, v in s.items() if not k.startswith("_")} for s in new]
    SIGNALS_FILE.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[write] article_signals.json: {before} -> {len(merged)}")

    # 4) 归档 dated 文件 (merge, 防同日多次运行覆盖)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    archive = DATA_DIR / f"lhb_signals_{trade_date}.json"
    arch_signals = []
    if archive.exists():
        try:
            arch_signals = json.loads(archive.read_text(encoding="utf-8")).get("signals", [])
        except Exception:
            arch_signals = []
    seen_a = {s.get("article_id") for s in arch_signals}
    merged = list(arch_signals) + [s for s in signals if s["article_id"] not in seen_a]
    archive.write_text(
        json.dumps({"date": trade_date, "count": len(merged),
                    "signals": merged}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[archive] {archive} (merge -> {len(merged)} 条)")

    # 5) 登记权重(幂等)
    if add_weight():
        print(f"[weight] 已在 source_weights.json 登记 {ACCOUNT}={WEIGHT_DEFAULT} (起始权重)")
    else:
        print(f"[weight] {ACCOUNT} 权重已存在或跳过")


if __name__ == "__main__":
    main()
