#!/usr/bin/env python3
"""北辰多智能体辩论 CLI

用法:
    # 单只股票辩论（数据内嵌）
    python3 scripts/run_debate.py --code 000333 --name "美的集团" --price 85.92 --change 1.6 \
        --pe 14.5 --pb 3.2 --roe 22 --rsi 58 --macd bullish --sector 家用电器

    # 从 scan 候选 JSON 批量
    python3 scripts/run_debate.py --from-scan output/scan_candidates.json

    # 对当前持仓辩论
    python3 scripts/run_debate.py --from-holdings .workbuddy/data/simulation/portfolio.json

    # 查看最近辩论结果
    python3 scripts/run_debate.py --latest
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger("run_debate")

# 确保 src/claw 在 path 中
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from claw.debate import batch_debate, run_debate

RESULT_FILE = Path(__file__).parent.parent / ".workbuddy" / "data" / "debate" / "debate_result.json"


def parse_args():
    p = argparse.ArgumentParser(description="北辰多智能体辩论")
    p.add_argument("--code", help="股票代码（6位）")
    p.add_argument("--name", help="股票名称")
    p.add_argument("--price", type=float, help="最新价")
    p.add_argument("--change", type=float, default=0, help="涨跌幅%")
    p.add_argument("--pe", type=float, help="市盈率")
    p.add_argument("--pb", type=float, help="市净率")
    p.add_argument("--roe", type=float, help="ROE(%)")
    p.add_argument("--rsi", type=float, help="RSI")
    p.add_argument("--macd", help="MACD信号(bullish/bearish/neutral)")
    p.add_argument("--sector", default="未知", help="所属行业")
    p.add_argument("--mcap", help="市值")
    p.add_argument("--from-scan", help="从 scan 候选 JSON 批量")
    p.add_argument("--from-holdings", help="从持仓 JSON 批量")
    p.add_argument("--codes", help="逗号分隔的股票代码列表，自动拉行情后批量辩论")
    p.add_argument("--latest", action="store_true", help="查看最近辩论结果")
    p.add_argument("--dry-run", action="store_true", help="仅打印参数，不调 LLM")
    p.add_argument(
        "--learn",
        action="store_true",
        help="辩论后将「风险约束+高可靠因子」沉淀进 debate_memory.json，"
        "供后续辩论作为约束搜索锚点注入",
    )
    return p.parse_args()


def build_data(args) -> dict:
    """从 CLI 参数构建 data dict"""
    data = {
        "price": args.price,
        "change_pct": args.change,
        "sector": args.sector,
        "market_cap": args.mcap or "N/A",
        "technical": {},
        "fundamental": {},
        "fund_flow": {},
        "sentiment": {},
    }
    if any([args.rsi, args.macd]):
        tech = {}
        if args.rsi is not None:
            tech["rsi"] = args.rsi
        if args.macd:
            tech["macd"] = args.macd
        data["technical"] = tech
    if any([args.pe, args.pb, args.roe]):
        fund = {}
        if args.pe is not None:
            fund["pe"] = args.pe
        if args.pb is not None:
            fund["pb"] = args.pb
        if args.roe is not None:
            fund["roe"] = f"{args.roe}%"
        data["fundamental"] = fund
    return data


def debate_from_scan(path: str, learn: bool = False):
    """从 scan 候选 JSON 批量辩论"""
    scan_file = Path(path)
    if not scan_file.exists():
        print(f"错误：{path} 不存在")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        scan_data = json.load(f)

    stocks = []
    for item in scan_data if isinstance(scan_data, list) else scan_data.get("candidates", []):
        code = item.get("code", "")
        # 08-21 审计修复：scan 路径同样走数据补全（与 codes/holdings 对齐），
        # 避免原始 scan data 只有价格 → 专家数据缺失 → 盲猜降级。
        base = dict(item.get("data", {})) or {
            "price": item.get("price", 0),
            "change_pct": 0,
            "sector": item.get("sector", "未知"),
        }
        stocks.append(
            {
                "code": code,
                "name": item.get("name", ""),
                "data": _enrich_stock_data(code, base),
            }
        )

    if not stocks:
        print("无候选股需要辩论")
        return

    print(f"开始对 {len(stocks)} 只候选股进行多智能体辩论...\n")
    results = batch_debate(stocks, learn=learn)
    _print_summary(results)


def debate_from_codes(codes_str: str, learn: bool = False):
    """从逗号分隔的代码列表自动拉行情后批量辩论"""
    import re
    import subprocess

    codes = [c.strip() for c in codes_str.split(",") if c.strip()]
    if not codes:
        print("无股票代码")
        return

    # 自动拉取行情
    market_codes = ",".join(f"sh{c}" if c.startswith("6") else f"sz{c}" for c in codes)
    quotes = {}
    try:
        raw = subprocess.run(
            ["curl", "-s", f"http://qt.gtimg.cn/q={market_codes}"],
            capture_output=True,
            timeout=10,
        )
        text = raw.stdout.decode("gbk", errors="replace")
        for line in text.split("\n"):
            pre = re.match(r'v_(sh|sz)(\d+)="', line)
            if not pre or pre.group(2) not in codes:
                continue
            inner = line.split('="', 1)[1].rstrip('"')
            parts = inner.split("~")
            if len(parts) < 5:
                continue
            try:
                qt_price = float(parts[3])
            except ValueError:
                qt_price = 0
            try:
                prev_close = float(parts[4])
            except ValueError:
                prev_close = 0
            # 08-21 修复：解析昨收算涨跌幅（此前 change_pct 恒 0 → 专家无法判断日内强弱）
            change_pct = (
                round((qt_price - prev_close) / prev_close * 100, 2) if prev_close > 0 else 0
            )
            quotes[pre.group(2)] = {"name": parts[1], "price": qt_price, "change_pct": change_pct}
    except Exception as exc:
        logger.debug("行情拉取失败(%s): %s", codes_str, exc)

    stocks = []
    for c in codes:
        q = quotes.get(c, {})
        base = {"price": q.get("price", 0), "change_pct": q.get("change_pct", 0), "sector": "未知"}
        # 🔴 08-21 修复：codes 路径同样走数据补全（此前只喂价格+涨跌幅 →
        #   候选股辩论数据缺失 → 专家盲猜 → 置信度偏低），技术面本地化不耗 Wind 配额。
        enriched = _enrich_stock_data(c, base)
        stocks.append(
            {
                "code": c,
                "name": q.get("name", c),
                "data": enriched,
            }
        )

    print(f"开始对 {len(stocks)} 只股票进行多智能体辩论...\n")
    results = batch_debate(stocks, learn=learn)
    _print_summary(results)


def _load_fundamental_cache() -> dict:
    """读取持仓基本面快照缓存（Wind 财务数据，离线 JSON）。

    缓存来源：wind-finance 连接器抓取，存于 .workbuddy/data/debate/fundamental_cache.json。
    更新方式：持仓变动或定期(周)重抓 wind-finance 覆盖此文件（脚本不直接调 MCP）。
    任一字段缺失不影响主流程，fundamental 留空由专家基于价格+技术判断。
    """
    cache_path = (
        Path(__file__).parent.parent / ".workbuddy" / "data" / "debate" / "fundamental_cache.json"
    )
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        return raw
    except Exception:
        return {}


def _enrich_stock_data(code: str, base: dict) -> dict:
    """补全辩论所需的技术/基本面/资金面数据，让 7 专家有料可辩。

    🔴 08-21 修复（Wind 配额耗尽致辩论降级）：技术面全部本地化，辩论链路零 Wind 配额消耗——
      - RSI(14)：calc_rsi.py 子进程（腾讯 ifzq 前复权），不再走 AdvisorRules 的 Wind 分支
      - MA20/MACD/量比：qts_client.get_kline 本地 K 线计算（腾讯源）
      基本面（PE/PB/ROE/营收增速/市值）：fundamental_cache.json 优先（wind-finance 快照）；
      缓存缺失的股票（候选股等）用 anysearch_helper.a_stock_quote/a_stock_indicator 兜底
      （westock CLI 优先 → AnySearch 降级，均不耗 Wind 配额），并回写缓存。

    返回 enriched data dict（在 base 基础上追加 technical / fundamental 等字段）。
    """
    import subprocess as _subprocess

    data = dict(base)
    _scripts_dir = str(Path(__file__).parent)
    if _scripts_dir not in sys.path:
        sys.path.insert(
            0, _scripts_dir
        )  # ── 1) 技术面：全本地（腾讯 K 线 + calc_rsi.py），零 Wind ──
    tech = {}
    try:
        from qts_client import get_kline

        kline = get_kline(code, limit=60)
        if kline and len(kline) >= 26:
            closes = [float(k["close"]) for k in reversed(kline)]  # K线 DESC → 时间正序
            # MA20
            if len(closes) >= 20:
                tech["ma20"] = round(sum(closes[-20:]) / 20, 2)
            # MACD（EMA12/26/9）
            ema12_series = _ema_series(closes, 12)
            ema26_series = _ema_series(closes, 26)
            dif_series = [a - b for a, b in zip(ema12_series, ema26_series)]
            dea_series = _ema_series(dif_series, 9)
            dif = dif_series[-1]
            dea = dea_series[-1]
            tech["macd"] = "金叉" if dif > dea else ("死叉" if dif < dea else "neutral")
            tech["macd_hist"] = round((dif - dea) * 2, 3)
            # 量比（最新量 / 5日均量）
            vol = float(kline[0]["volume"])
            vol_ma5 = sum(float(k["volume"]) for k in kline[:5]) / min(5, len(kline))
            if vol_ma5 > 0:
                tech["volume_ratio"] = round(vol / vol_ma5, 2)
    except Exception as exc:
        logger.debug("技术面 K线补全失败(%s): %s", code, exc)
    # RSI：calc_rsi.py 子进程（腾讯 ifzq，独立于 Wind 配额）
    if "rsi" not in tech:
        try:
            prefixed = ("sh" if code.startswith("6") else "sz") + code
            out = _subprocess.run(
                [sys.executable, str(Path(__file__).parent / "calc_rsi.py"), prefixed],
                capture_output=True,
                text=True,
                timeout=30,
            )
            for line in out.stdout.splitlines():
                if line.startswith("JSON:"):
                    d = json.loads(line[5:].strip())
                    if d and d[0].get("rsi14") is not None:
                        tech["rsi"] = round(float(d[0]["rsi14"]), 1)
                        break
        except Exception as exc:
            logger.debug("RSI 子进程失败(%s): %s", code, exc)
    if tech:
        data["technical"] = tech

    # ── 2) 基本面：缓存优先 → westock/AnySearch 兜底 ──
    fund = {}
    try:
        _fcache = _load_fundamental_cache()
        rec = _fcache.get(code)
        if isinstance(rec, dict):
            if rec.get("pe_ttm") is not None:
                fund["pe"] = rec["pe_ttm"]
            if rec.get("pb") is not None:
                fund["pb"] = rec["pb"]
            if rec.get("roe") is not None:
                fund["roe"] = rec["roe"]
            if rec.get("revenue_growth") is not None:
                fund["revenue_growth"] = rec["revenue_growth"]
            if rec.get("total_market_cap") is not None:
                fund["market_cap"] = f"{rec['total_market_cap']}亿"
    except Exception as exc:
        logger.debug("基本面缓存读取失败(%s): %s", code, exc)
    # 缓存缺失 → anysearch_helper 兜底（不耗 Wind 配额）
    if not fund:
        try:
            from anysearch_helper import a_stock_indicator, a_stock_quote

            cn = f"{code}.{'SH' if code.startswith('6') else 'SZ'}"
            q = a_stock_quote(cn)
            if isinstance(q, dict) and "error" not in q:
                if q.get("pe_ttm") is not None:
                    fund["pe"] = round(float(q["pe_ttm"]), 2)
                if q.get("pb") is not None:
                    fund["pb"] = round(float(q["pb"]), 2)
                if q.get("total_mv") is not None:
                    fund["market_cap"] = (
                        f"{round(float(q['total_mv']) / 1e4, 1)}亿"  # westock total_mv 单位:万元
                    )
            ind = a_stock_indicator(cn)
            if isinstance(ind, dict) and "error" not in ind:
                if ind.get("roe") is not None:
                    fund["roe"] = ind["roe"]
                if ind.get("revenue_growth") is not None:
                    fund["revenue_growth"] = ind["revenue_growth"]
        except Exception as exc:
            logger.debug("基本面兜底失败(%s): %s", code, exc)
        # 兜底成功后回写缓存（次日复用，避免重复 CLI 调用）
        if fund:
            try:
                _write_fundamental_cache(code, fund)
            except Exception as exc:
                logger.debug("基本面缓存回写失败(%s): %s", code, exc)
    if fund:
        data["fundamental"] = fund

    # ── 3) 资金面：东财 ulist.np 资金流（零 Wind，见 _fetch_money_flow）──
    # 08-21 审计二次修正：
    #   a) 键名对齐 expert_prompts 消费键 main_net_inflow（此前写 main_net_wan → prompt 显示全 "?"）
    #   b) 数据源整体不可达（东财 push2 断连）→ 写 {"_source": "unavailable"} 显式标记，
    #      避免 fund_flow 恒标 data_insufficient 噪音（环境问题 ≠ 个股数据不足，须先修数据源）
    try:
        mf = _fetch_money_flow(code)
        if mf and "error" not in mf:
            data["fund_flow"] = {
                "main_net_inflow": round(float(mf.get("main_net", 0)) / 1e4, 1),  # 元→万元
                "main_pct": round(float(mf.get("main_pct", 0)), 2),
            }
        else:
            data["fund_flow"] = {"_source": "unavailable"}
            logger.debug("资金流数据源不可达(%s): %s", code, (mf or {}).get("error"))
    except Exception as exc:
        data["fund_flow"] = {"_source": "unavailable"}
        logger.debug("资金流补全异常(%s): %s", code, exc)
    return data


def _fetch_money_flow(code: str) -> dict:
    """东方财富个股资金流（主力净流入额+占比，单请求）。

    数据源：东财 ulist.np 接口（f62=主力净流入额元 / f184=主力净流入占比 0.01%）。
    08-21 实测：push2（实时端）间歇性不可达返回空，**push2delay（延时端）稳定可达** →
    双域名顺序降级（实时优先，空/失败 → 延时兜底），每域名重试 2 次。
    东财免费接口无 Wind 式 180 次/日配额限制，本地 curl 直取，不耗 Wind 配额。
    失败返回 {"error": ...}。
    """
    import subprocess as _subprocess
    import time as _time

    secid = ("1." if code.startswith(("6", "9")) else "0.") + code
    hosts = ("push2.eastmoney.com", "push2delay.eastmoney.com")  # 实时 → 延时兜底
    last_err = "no_money_flow_data"
    for host in hosts:
        url = (
            f"http://{host}/api/qt/ulist.np/get"
            f"?secids={secid}&fields=f62,f184&ut=fa5fd1943c7b386f172d6893dbfba10b"
        )
        for _attempt in range(2):
            try:
                raw = _subprocess.run(
                    ["curl", "-s", url], capture_output=True, text=True, timeout=10
                )
                data = json.loads(raw.stdout or "{}")
                diff = (data.get("data") or {}).get("diff") or []
                if data.get("rc") == 0 and diff:
                    row = diff[0]
                    main_net = row.get("f62")
                    main_pct_bp = row.get("f184")
                    return {
                        "main_net": float(main_net) if main_net is not None else 0.0,  # 元
                        "main_pct": round(float(main_pct_bp) / 100, 2)
                        if main_pct_bp is not None
                        else 0.0,  # bp→%
                    }
                last_err = "empty_or_rc!=0"
            except Exception as exc:
                last_err = str(exc)
            _time.sleep(0.5)
    return {"error": last_err}


def _write_fundamental_cache(code: str, fund: dict) -> None:
    """把兜底抓到的基本面回写 fundamental_cache.json（保留 _meta，文件锁防并发）。"""
    import fcntl as _fcntl

    cache_path = (
        Path(__file__).parent.parent / ".workbuddy" / "data" / "debate" / "fundamental_cache.json"
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = str(cache_path) + ".lock"
    with open(lock_path, "w") as lock_fd:
        _fcntl.flock(lock_fd, _fcntl.LOCK_EX)
        try:
            cache = {}
            if cache_path.exists():
                try:
                    cache = json.loads(cache_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    cache = {}
            if not isinstance(cache, dict):
                cache = {}
            meta = cache.get("_meta", {})
            meta["updated"] = "2026-08-21"
            meta.setdefault("source", "wind-finance + westock/AnySearch 兜底")
            meta.setdefault("note", "缓存缺失股票由 anysearch_helper 兜底后回写（08-21 起）")
            cache["_meta"] = meta
            cache[code] = {
                "name": "",
                "pe_ttm": fund.get("pe"),
                "pb": fund.get("pb"),
                "roe": fund.get("roe"),
                "revenue_growth": fund.get("revenue_growth"),
                "total_market_cap": float(fund.get("market_cap", "0").rstrip("亿"))
                if isinstance(fund.get("market_cap"), str) and fund["market_cap"].endswith("亿")
                else None,
            }
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            _fcntl.flock(lock_fd, _fcntl.LOCK_UN)


def _ema(values: list[float], period: int) -> float:
    """指数移动平均（末值）"""
    if not values:
        return 0.0
    k = 2 / (period + 1)
    ema = values[0]
    for v in values[1:]:
        ema = v * k + ema * (1 - k)
    return ema


def _ema_series(values: list[float], period: int) -> list[float]:
    """返回完整 EMA 序列（与输入等长，前 period-1 点用 SMA 种子）"""
    if not values:
        return []
    if len(values) < period:  # 防御：样本不足时无完整 SMA 种子，原样返回避免误解
        return list(values)
    k = 2 / (period + 1)
    out = []
    ema = sum(values[:period]) / period  # SMA 种子
    for i, v in enumerate(values):
        if i < period - 1:
            out.append(ema)  # 种子期占位
        elif i == period - 1:
            out.append(ema)
        else:
            ema = v * k + ema * (1 - k)
            out.append(ema)
    return out


def debate_from_holdings(path: str, learn: bool = False):
    """从持仓 JSON 批量辩论，自动拉取实时行情和基本面数据"""
    pf_file = Path(path)
    if not pf_file.exists():
        print(f"错误：{path} 不存在")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        pf = json.load(f)

    positions = pf.get("positions", {})
    if not positions:
        # 兼容实盘结构：holdings 为 array（user/portfolio.json）
        holdings = pf.get("holdings", [])
        if isinstance(holdings, list) and holdings:
            positions = {h["code"]: h for h in holdings if isinstance(h, dict) and h.get("code")}
    if not positions:
        print("无持仓")
        return

    # 自动拉取行情
    codes = list(positions.keys())
    market_codes = ",".join(f"sh{c}" if c.startswith("6") else f"sz{c}" for c in codes)
    quotes = {}
    try:
        import re
        import subprocess

        raw = subprocess.run(
            ["curl", "-s", f"http://qt.gtimg.cn/q={market_codes}"],
            capture_output=True,
            timeout=10,
        )
        # gtimg 真实格式: v_sh601668="1~名称~代码~当前价~昨收~开盘~..."
        #   field[1]=名称  field[2]=代码  field[3]=当前价  field[4]=昨收
        text = raw.stdout.decode("gbk", errors="replace")
        for line in text.split("\n"):
            if not line.strip():
                continue
            # 先取代码前缀
            pre = re.match(r'v_(sh|sz)(\d+)="', line)
            if not pre:
                continue
            code = pre.group(2)
            if code not in codes:
                continue
            inner = line.split('="', 1)[1].rstrip('"')
            parts = inner.split("~")
            if len(parts) < 5:
                continue
            name = parts[1]
            try:
                price = float(parts[3])
            except ValueError:
                price = 0
            try:
                prev_close = float(parts[4])
            except ValueError:
                prev_close = 0
            quotes[code] = {"price": price, "prev_close": prev_close, "name": name}
    except Exception as exc:
        print(f"  ⚠️ 行情拉取失败: {exc}，将回退至持仓成本价")

    stocks = []
    for code, pos in positions.items():
        q = quotes.get(code)
        # 优先用实时价；缺失时回退 current_price；再缺失才用 avg_cost（避免 change_pct 恒为 0 误导）
        price = (
            q["price"] if q and q["price"] > 0 else pos.get("current_price", pos.get("avg_cost", 0))
        )
        # 昨收优先用实时返回的 prev_close，否则用 avg_cost 近似
        prev_close = q["prev_close"] if q and q["prev_close"] > 0 else pos.get("avg_cost", price)
        change_pct = (
            round((price - prev_close) / prev_close * 100, 2)
            if prev_close and prev_close > 0
            else 0
        )
        base_data = {
            "price": price,
            "change_pct": change_pct,
            "sector": pos.get("sector", "未知"),
        }
        # 补全技术/资金面，避免专家饿成"数据缺失→一律观望"
        enriched = _enrich_stock_data(code, base_data)
        stocks.append(
            {
                "code": code,
                "name": (q.get("name") if q else None) or pos.get("name", code),
                "data": enriched,
            }
        )

    print(f"开始对 {len(stocks)} 只持仓进行多智能体辩论...\n")
    results = batch_debate(stocks, learn=learn)
    _print_summary(results)


def _print_summary(results: list[dict]):
    for r in results:
        v = r.get("verdict", {})
        buys = sum(1 for s in r.get("stances", []) if s["stance"] == "BUY")
        holds = sum(1 for s in r.get("stances", []) if s["stance"] == "HOLD")
        sells = sum(1 for s in r.get("stances", []) if s["stance"] == "SELL")
        sl = v.get("stop_loss_pct", -8.0)
        fs = v.get("factor_scores", {})
        factor_str = (
            f"V{fs.get('value', 50)}/Q{fs.get('quality', 50)}"
            f"/G{fs.get('growth', 50)}/M{fs.get('momentum', 50)}"
        )
        print(
            f"  {r['name']}({r['code']}): {v.get('consensus', '?')} "
            f"[{buys}B/{holds}H/{sells}S] "
            f"conf={v.get('confidence', 0):.0%} "
            f"止损{sl:+.0f}% "
            f"因子[{factor_str}] "
            f"({v.get('summary', '')[:60]})"
        )


def show_latest():
    """查看最近辩论结果"""
    if not RESULT_FILE.exists():
        print("暂无辩论结果")
        return
    with open(RESULT_FILE, encoding="utf-8") as f:
        records = json.load(f)
    for r in records[-5:]:
        v = r.get("verdict", {})
        fs = v.get("factor_scores", {})
        factor_str = (
            (
                f"V{fs.get('value', 50)}/Q{fs.get('quality', 50)}"
                f"/G{fs.get('growth', 50)}/M{fs.get('momentum', 50)}"
            )
            if fs
            else ""
        )
        # 08-21 接线：展示数据完整性标记（审计 P1-2 —— 此前字段无消费方）
        insuff = v.get("data_insufficient")
        insuff_str = f" ⚠️数据不足:[{','.join(insuff)}]" if insuff else ""
        gap_str = f" 设计缺口:[{','.join(v.get('design_gap', []))}]" if v.get("design_gap") else ""
        print(
            f"[{r.get('timestamp', '')[:16]}] {r.get('name', '')}({r.get('code', '')}): "
            f"{v.get('consensus', '?')} | conf={v.get('confidence', 0):.0%}{insuff_str}{gap_str} | "
            f"止损{v.get('stop_loss_pct', -8.0):+.0f}% | {factor_str} | "
            f"{v.get('summary', '')[:80]}"
        )


def main():
    args = parse_args()

    if args.latest:
        show_latest()
        return

    if args.from_scan:
        debate_from_scan(args.from_scan, learn=args.learn)
        return

    if args.from_holdings:
        debate_from_holdings(args.from_holdings, learn=args.learn)
        return

    if args.codes:
        debate_from_codes(args.codes, learn=args.learn)
        return

    if not args.code:
        print("请提供 --code 或 --from-scan/--from-holdings/--codes/--latest")
        sys.exit(1)

    data = build_data(args)

    if args.dry_run:
        print(f"DRY RUN: {args.name}({args.code}), data={json.dumps(data, ensure_ascii=False)}")
        return

    result = run_debate(args.code, args.name or args.code, data, learn=args.learn)
    _print_summary([result])


if __name__ == "__main__":
    main()
