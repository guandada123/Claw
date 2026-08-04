"""
signal_verify.py — 公众号信号行情验证（v4 新增）

为 .workbuddy/data/article_signals.json 中每条信号补充实时行情验证：
  - realtime_chg_pct : 当日涨跌幅（Wind 优先，腾讯 gtimg 降级）
  - realtime_price   : 最新价
  - final_return_pct : 自信号发布日至今的累计收益率（Wind K线优先，akshare 降级）
  - verified         : 是否成功取得行情并可计算累计收益
  - hit              : 看多信号且累计收益>0 记为命中；看空则收益<0 命中

数据源说明：
  - 实时行情：Wind 万得（优先）→ 腾讯 gtimg（降级）
  - 历史收益：Wind K线（优先）→ 新浪日线 akshare stock_zh_a_daily（降级）

输出：
  - 增量写回 article_signals.json（保留原有字段，新增验证字段）
  - 生成 signal_verify_report.json（按公众号统计 + 总体胜率/均价；胜率采近 N 日滚动口径，样本不足回退累计）
"""
from __future__ import annotations

import datetime
import email.utils
import json
import os
import re
import sys
import time
from pathlib import Path

# 本环境存在不可达的代理，强制直连
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_k, None)

import urllib.request  # noqa: E402 (proxy must be cleared first)

try:
    import akshare as ak  # noqa: E402
except ImportError:
    ak = None  # type: ignore[assignment]

# Wind 万得（优先数据源）
try:
    from claw.feeds.wind_utils import (
        get_wind_kline,
        get_wind_realtime_price,
        plain_code_to_windcode,
        wind_available,
    )
except ImportError:
    def wind_available() -> bool:  # type: ignore[misc]
        return False

    def get_wind_realtime_price(code: str) -> None:  # type: ignore[misc]
        return None

    def get_wind_kline(code: str, days: int = 60) -> None:  # type: ignore[misc]
        return None

    def plain_code_to_windcode(code: str) -> str:  # type: ignore[misc]
        return code

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SIGNALS_FILE = PROJECT_ROOT / ".workbuddy" / "data" / "article_signals.json"
REPORT_FILE = PROJECT_ROOT / ".workbuddy" / "data" / "signal_verify_report.json"
HISTORY_FILE = PROJECT_ROOT / ".workbuddy" / "data" / "signal_verify_history.json"

_DATE_CN = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日?")
_DATE_ISO = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
# 截断 RFC822（历史入库被 pub_time[:10] 截断为 "Sat, 27 Ju"，无年/完整月份）
_DATE_RFC_TRUNC = re.compile(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun), (\d{1,2}) (\w+)")
_WEEKDAY = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
_MONTH_NAMES = [(1, "Jan"), (2, "Feb"), (3, "Mar"), (4, "Apr"), (5, "May"), (6, "Jun"),
                (7, "Jul"), (8, "Aug"), (9, "Sep"), (10, "Oct"), (11, "Nov"), (12, "Dec")]

# B @2026-07-18：胜率口径由「全量累计」改为「近 N 日滚动」。
# 原口径对所有历史信号累计算 win_rate，某号阶段性回撤会缓慢但不可逆地拉低胜率，
# 跌破质量门槛(当前 25%)即被踢出优质名单、信号流断崖。
# 滚动口径只看近 ROLLING_DAYS 天样本，更稳；窗口样本不足 MIN_ROLLING_SAMPLES 时
# 回退全量累计，避免近期少发的稀疏号被误踢。假设透明：报告含 win_rate_basis 字段。
ROLLING_DAYS = int(os.environ.get("SIGNAL_ROLLING_DAYS", "30"))
MIN_ROLLING_SAMPLES = 10
# 收益率异常阈值：<=-35% 疑为未复权(送转)/退市/重组标价，统计默认过滤（可关闭）
SUSPECT_RETURN_LE = -35.0
EXCLUDE_SUSPECT = os.environ.get("SIGNAL_EXCLUDE_SUSPECT_RETURN", "1") != "0"


def parse_date(s: str, src: str = ""):
    """解析信号日期：ISO / 中文(2026年6月1日) / 截断RFC822(Sat,27 Ju) / 完整RFC822。

    src 传入 source_file（形如 20260628_...json）用于截断日期歧义兜底。
    """
    if not s:
        return None
    s = s.strip()
    m = _DATE_CN.search(s)
    if m:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _DATE_ISO.search(s)
    if m:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    # 截断 RFC822：weekday 约束 + source_file 日期兜底，消解 Jun/Jul 歧义
    m = _DATE_RFC_TRUNC.match(s)
    if m:
        wd, day, mon_p = m.group(1), int(m.group(2)), m.group(3)
        cands = []
        for y in (datetime.date.today().year - 1, datetime.date.today().year):
            for month, monname in _MONTH_NAMES:
                if monname.startswith(mon_p):
                    try:
                        d = datetime.date(y, month, day)
                        if _WEEKDAY[wd] == d.weekday():
                            cands.append(d)
                    except ValueError:
                        pass
        if len(cands) == 1:
            return cands[0]
        if len(cands) > 1 and len(src) >= 8:
            try:
                sd = datetime.date(int(src[:4]), int(src[4:6]), int(src[6:8]))
                return min(cands, key=lambda d: abs((d - sd).days))
            except ValueError:
                pass
        if cands:
            return cands[0]
        return None
    # 完整 RFC822
    try:
        t = email.utils.parsedate_to_datetime(s)
        if t:
            return t.date()
    except Exception:
        pass
    return None


def gtimg_prefix(code: str) -> str:
    return "sh" if code.startswith(("60", "68", "90", "11", "5", "4")) else "sz"


def fetch_realtime(code: str) -> dict:
    """实时行情。Wind 优先，降级腾讯 gtimg。返回 price / chg_pct / ok"""
    # 1) Wind 万得
    if wind_available():
        try:
            r = get_wind_realtime_price(code)
            if r and r.get("price") is not None:
                return {"price": r["price"], "chg_pct": r.get("change_pct"), "ok": True}
        except Exception as _:  # noqa: S110 — 降级到腾讯
            pass
    # 2) 腾讯 gtimg
    url = f"http://qt.gtimg.cn/q={gtimg_prefix(code)}{code}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:  # nosec B310: qt.gtimg.cn
            raw = r.read().decode("gbk")
        body = raw.split('"', 1)[1].rstrip('";')
        f = body.split("~")
        return {"price": float(f[3]), "chg_pct": float(f[32]), "ok": True}
    except Exception as e:  # noqa: BLE001
        return {"price": None, "chg_pct": None, "ok": False, "err": str(e)[:80]}


def _tx_qfq_history(code: str, start: str, end: str) -> tuple[dict | None, float | None]:
    """腾讯K线前复权(qfqday)日线收盘价 — 🔴 08-04 优先源(符合腾讯优先铁律+根治Wind未复权)。"""
    # 腾讯前复权须走独立 fqkline 端点(param 末尾 ,qfq 返回 qfqday)；日期须 YYYY-MM-DD 格式
    def _fmt(d: str) -> str:
        d = d.replace("-", "")
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) >= 8 else d

    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
        f"param={gtimg_prefix(code)}{code},day,{_fmt(start)},{_fmt(end)},400,qfq"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
        raw = urllib.request.urlopen(req, timeout=10).read().decode("utf-8")
        node = json.loads(raw).get("data", {}).get(f"{gtimg_prefix(code)}{code}", {})
        days = node.get("qfqday") or node.get("day") or []
        if not days:
            return (None, None)
        m = {}
        for k in days:
            if len(k) < 3:
                continue
            dt = str(k[0])[:10]
            try:
                m[dt] = float(k[2])  # [日期, 开, 收, 高, 低, 量] → 收
            except (TypeError, ValueError):
                continue
        if not m:
            return (None, None)
        filtered = {k: v for k, v in m.items() if start[:8] <= k.replace("-", "")[:8] <= end[:8]}
        if not filtered:
            return (None, None)
        return (filtered, float(list(filtered.values())[-1]))
    except Exception:
        return (None, None)


def fetch_history(code: str, start: str, end: str, retries: int = 5):
    """历史日线收盘价。🔴 腾讯qfq优先 → Wind降级 → akshare兜底（08-04 对齐腾讯优先铁律）。"""
    # 1) 腾讯前复权 K线（优先：符合铁律+根治 Wind 未复权导致的 -35% 虚低收益）
    m_tx, last_tx = _tx_qfq_history(code, start, end)
    if m_tx:
        return (m_tx, last_tx)
    # 2) Wind 万得 K线（降级，未复权风险已由腾讯源规避）
    if wind_available():
        try:
            klines = get_wind_kline(code, days=400)
            if klines:
                m = {}
                for k in klines:
                    dt = str(k.get("TIME", ""))[:10]
                    if dt:
                        close = k.get("MATCH") or k.get("match") or k.get("close") or k.get("收盘价")
                        if close is not None:
                            m[dt] = float(close)
                if m:
                    # 过滤 start~end 范围
                    filtered = {k: v for k, v in m.items() if start[:8] <= k.replace("-", "")[:8] <= end[:8]}
                    if filtered:
                        last_close = list(filtered.values())[-1]
                        return (filtered, last_close)
        except Exception as _:  # noqa: S110 — 降级到 akshare
            pass
    # 3) akshare 新浪日线 qfq（兜底）
    if ak is None:
        return (None, None)
    sym = gtimg_prefix(code) + code
    last = None
    for attempt in range(retries):
        try:
            df = ak.stock_zh_a_daily(symbol=sym, start_date=start, end_date=end, adjust="qfq")
            if df is not None and not df.empty:
                date_col = "date" if "date" in df.columns else df.columns[0]
                m = {str(row[date_col])[:10]: float(row["close"]) for _, row in df.iterrows()}
                last_close = float(df["close"].iloc[-1])
                return (m, last_close)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 + attempt * 0.5)
    return (None, None)


def verify_signals() -> dict:
    signals = json.loads(SIGNALS_FILE.read_text(encoding="utf-8"))
    today = datetime.date.today()

    # 只对有效 A 股 6 位代码取行情（跳过 MU 等美股代码与"未知（新股）"脏记录）
    codes = sorted({s["stock_code"] for s in signals
                    if isinstance(s.get("stock_code"), str) and s["stock_code"].isdigit()})
    hist_cache: dict[str, tuple] = {}
    start_map: dict[str, str] = {}

    def _fetch_one(code: str, end: str) -> None:
        sdates = [parse_date(s["recorded_at"]) for s in signals if s["stock_code"] == code]
        recent = [d for d in sdates if d and (today - d).days <= 400]
        start = (min(recent) - datetime.timedelta(days=10)).strftime("%Y%m%d") if recent \
            else (today - datetime.timedelta(days=40)).strftime("%Y%m%d")
        start_map[code] = start
        result = fetch_history(code, start, end)
        # fetch_history 返回 (m, last_close) 或 (None, None)
        if result and result[0] is not None:
            hist_cache[code] = result
        else:
            hist_cache[code] = (None, None)

    end = today.strftime("%Y%m%d")
    for code in codes:
        _fetch_one(code, end)
        time.sleep(0.35)

    # 二次补拉：首次失败的 code 长间隔重试（规避新浪限流）
    failed = [c for c in codes if hist_cache.get(c, (None, None))[0] is None]
    for code in failed:
        time.sleep(3.0)
        _fetch_one(code, end)
        time.sleep(1.0)

    for s in signals:
        code = s["stock_code"]
        if not isinstance(code, str) or not code.isdigit():
            # 非 A 股代码（美股/未解析）：跳过行情，标记不可验证
            s["realtime_chg_pct"] = None
            s["realtime_price"] = None
            s["verified"] = False
            s["final_return_pct"] = None
            s["hit"] = None
            s["verify_note"] = "非A股代码，跳过行情验证"
            s["verify_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            continue
        rt = fetch_realtime(code)
        s["realtime_chg_pct"] = rt["chg_pct"]
        s["realtime_price"] = rt["price"]
        notes = []
        verified = False
        final_ret = None
        hit = None
        m, last_close = hist_cache.get(code, (None, None))
        sdate = parse_date(s["recorded_at"])

        if rt["ok"]:
            if m and sdate and (today - sdate).days <= 400:
                cand = [d for d in m if d >= sdate.strftime("%Y-%m-%d")]
                if cand:
                    entry = m[min(cand)]
                    exit_px = last_close if last_close is not None else rt["price"]
                    final_ret = (exit_px / entry - 1.0) * 100.0
                    verified = True
                    if s["signal"] == "bullish":
                        hit = final_ret > 0
                    elif s["signal"] == "bearish":
                        hit = final_ret < 0
                    notes.append("累计收益已计算")
                else:
                    notes.append("信号日早于行情窗口")
            elif sdate and (today - sdate).days > 400:
                notes.append("信号过旧(>1年)跳过")
            else:
                notes.append("无有效信号日期/历史缺失")
        else:
            notes.append("实时行情获取失败")

        s["verified"] = verified
        s["final_return_pct"] = round(final_ret, 2) if final_ret is not None else None
        s["hit"] = hit
        s["verify_note"] = ";".join(notes)
        s["verify_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        time.sleep(0.12)

    SIGNALS_FILE.write_text(json.dumps(signals, ensure_ascii=False, indent=2), encoding="utf-8")
    report = build_report(signals, today)
    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # 历史快照累积：每次运行追加（同日期幂等覆盖），保留最近 60 个快照
    append_history_snapshot(report)
    return report


def append_history_snapshot(report: dict) -> dict:
    """把当日胜率快照追加到 signal_verify_history.json（同日期幂等覆盖，保留 60 个）。"""
    hist = {"snapshots": []}
    if HISTORY_FILE.exists():
        try:
            hist = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            hist = {"snapshots": []}
    snapshots = hist.setdefault("snapshots", [])
    today = report["trade_date"]
    snap = {
        "date": today,
        "overall_win": report["overall"]["win_rate"],
        "per_account": {x["account"]: x["win_rate"] for x in report.get("ranking", [])},
    }
    snapshots = [s for s in snapshots if s.get("date") != today]  # 同日期覆盖，不累积重复
    snapshots.append(snap)
    hist["snapshots"] = snapshots[-60:]
    HISTORY_FILE.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")
    return hist


def build_report(signals: list, today: datetime.date, rolling_days: int = ROLLING_DAYS) -> dict:
    cutoff = today - datetime.timedelta(days=rolling_days)
    accounts: dict = {}
    ov = {"total": 0, "verified": 0, "bullish": 0, "with_return": 0, "hits": 0, "ret_sum": 0.0}
    # 总体滚动窗口累计
    ov_win_samples = ov_win_hits = 0
    ov_win_ret_sum = 0.0
    # 数据质量计数：日期不可解析 / 收益率异常被过滤
    ov_date_parse_fail = 0
    ov_suspect_excluded = 0
    for s in signals:
        a = s["account"]
        acc = accounts.setdefault(a, {
            "total": 0, "verified": 0, "bullish": 0, "bearish": 0,
            "with_return": 0, "hits": 0, "ret_sum": 0.0, "stocks": set(),
            "win_samples": 0, "win_hits": 0, "win_ret_sum": 0.0,  # 滚动窗口内累计
            "date_parse_fail": 0, "suspect_excluded": 0,
        })
        acc["total"] += 1
        ov["total"] += 1
        acc["stocks"].add(s["stock_code"])
        if s.get("verified"):
            acc["verified"] += 1
            ov["verified"] += 1
        if s["signal"] == "bullish":
            acc["bullish"] += 1
            ov["bullish"] += 1
        elif s["signal"] == "bearish":
            acc["bearish"] += 1
        # 非 neutral 且有累计收益的样本
        if s.get("final_return_pct") is not None and s.get("signal") != "neutral":
            sdate = parse_date(s.get("recorded_at"), str(s.get("source_file", "")))
            if sdate is None:
                # 日期不可解析：不进任何口径（不静默回退累计），仅计数
                acc["date_parse_fail"] += 1
                ov_date_parse_fail += 1
            elif EXCLUDE_SUSPECT and s.get("return_suspect"):
                # 收益率异常（疑未复权/退市/重组）：过滤出统计口径，仅计数
                acc["suspect_excluded"] += 1
                ov_suspect_excluded += 1
            else:
                acc["with_return"] += 1
                ov["with_return"] += 1
                acc["ret_sum"] += s["final_return_pct"]
                ov["ret_sum"] += s["final_return_pct"]
                if s.get("hit") is True:
                    acc["hits"] += 1
                    ov["hits"] += 1
                # 滚动窗口：仅计入信号日在近 rolling_days 内的样本
                if sdate >= cutoff:
                    acc["win_samples"] += 1
                    acc["win_ret_sum"] += s["final_return_pct"]
                    if s.get("hit") is True:
                        acc["win_hits"] += 1
                    ov_win_samples += 1
                    ov_win_ret_sum += s["final_return_pct"]
                    if s.get("hit") is True:
                        ov_win_hits += 1

    rows = []
    for a, acc in accounts.items():
        # 滚动胜率优先；窗口样本不足回退全量累计（防稀疏号近期少发被误踢）
        if acc["win_samples"] >= MIN_ROLLING_SAMPLES:
            basis = "rolling"
            with_return = acc["win_samples"]
            hits = acc["win_hits"]
            win = (acc["win_hits"] / acc["win_samples"] * 100) if acc["win_samples"] else None
            avg = (acc["win_ret_sum"] / acc["win_samples"]) if acc["win_samples"] else None
        else:
            basis = "cumulative"
            with_return = acc["with_return"]
            hits = acc["hits"]
            win = (acc["hits"] / acc["with_return"] * 100) if acc["with_return"] else None
            avg = (acc["ret_sum"] / acc["with_return"]) if acc["with_return"] else None
        cov = (acc["verified"] / acc["total"] * 100) if acc["total"] else 0
        rows.append({
            "account": a,
            "total": acc["total"],
            "verified": acc["verified"],
            "verify_cov": round(cov, 1),
            "bullish": acc["bullish"],
            "with_return": with_return,
            "hits": hits,
            "win_rate": round(win, 1) if win is not None else None,
            "avg_return": round(avg, 2) if avg is not None else None,
            "stocks": len(acc["stocks"]),
            "win_rate_basis": basis,
            "rolling_window_days": rolling_days,
            "date_parse_fail": acc["date_parse_fail"],
            "suspect_excluded": acc["suspect_excluded"],
        })
    rows.sort(key=lambda x: -(x["win_rate"] if x["win_rate"] is not None else -1))

    # 总体胜率同采滚动口径（窗口样本不足回退累计）
    if ov_win_samples >= MIN_ROLLING_SAMPLES:
        ov_win = (ov_win_hits / ov_win_samples * 100) if ov_win_samples else None
        ov_avg = (ov_win_ret_sum / ov_win_samples) if ov_win_samples else None
    else:
        ov_win = (ov["hits"] / ov["with_return"] * 100) if ov["with_return"] else None
        ov_avg = (ov["ret_sum"] / ov["with_return"]) if ov["with_return"] else None
    return {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trade_date": today.strftime("%Y-%m-%d"),
        "win_rate_basis": "rolling" if ROLLING_DAYS else "cumulative",
        "rolling_window_days": rolling_days,
        "exclude_suspect_return": EXCLUDE_SUSPECT,
        "suspect_return_le": SUSPECT_RETURN_LE,
        "data_quality": {
            "date_parse_fail": ov_date_parse_fail,
            "suspect_return_excluded": ov_suspect_excluded,
        },
        "overall": {
            "total": ov["total"],
            "verified": ov["verified"],
            "verify_cov": round(ov["verified"] / ov["total"] * 100, 1) if ov["total"] else 0,
            "bullish": ov["bullish"],
            "with_return": ov["with_return"],
            "hits": ov["hits"],
            "win_rate": round(ov_win, 1) if ov_win is not None else None,
            "avg_return": round(ov_avg, 2) if ov_avg is not None else None,
        },
        "ranking": rows,
    }


def main():
    report = verify_signals()
    return report


if __name__ == "__main__":
    ok = main() is not None
    # 🔴 根治挂死(08-04)：akshare/Wind 残留非daemon线程致进程S态不退出
    # (连续8天报告落盘后挂10~17min被kill -9)。报告已落盘，强制os._exit绕过解释器线程等待
    os._exit(0 if ok else 1)
