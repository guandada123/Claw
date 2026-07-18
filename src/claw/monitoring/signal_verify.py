"""
signal_verify.py — 公众号信号行情验证（v4 新增）

为 .workbuddy/data/article_signals.json 中每条信号补充实时行情验证：
  - realtime_chg_pct : 当日涨跌幅（腾讯 gtimg 实时快照，稳定可靠）
  - realtime_price   : 最新价
  - final_return_pct : 自信号发布日至今的累计收益率（新浪日线 qfq）
  - verified         : 是否成功取得行情并可计算累计收益
  - hit              : 看多信号且累计收益>0 记为命中；看空则收益<0 命中

数据源说明：
  - 实时行情走腾讯 gtimg（单只快照，极少失败）
  - 历史收益走新浪日线 stock_zh_a_daily（东财 kline 在本环境连接不稳定，已弃用）

输出：
  - 增量写回 article_signals.json（保留原有字段，新增验证字段）
  - 生成 signal_verify_report.json（按公众号统计 + 总体胜率/均价；胜率采近 N 日滚动口径，样本不足回退累计）
"""
from __future__ import annotations

import datetime
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

import akshare as ak  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SIGNALS_FILE = PROJECT_ROOT / ".workbuddy" / "data" / "article_signals.json"
REPORT_FILE = PROJECT_ROOT / ".workbuddy" / "data" / "signal_verify_report.json"

_DATE_CN = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日?")
_DATE_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

# B @2026-07-18：胜率口径由「全量累计」改为「近 N 日滚动」。
# 原口径对所有历史信号累计算 win_rate，某号阶段性回撤会缓慢但不可逆地拉低胜率，
# 跌破质量门槛(当前 25%)即被踢出优质名单、信号流断崖。
# 滚动口径只看近 ROLLING_DAYS 天样本，更稳；窗口样本不足 MIN_ROLLING_SAMPLES 时
# 回退全量累计，避免近期少发的稀疏号被误踢。假设透明：报告含 win_rate_basis 字段。
ROLLING_DAYS = int(os.environ.get("SIGNAL_ROLLING_DAYS", "30"))
MIN_ROLLING_SAMPLES = 10


def parse_date(s: str):
    if not s:
        return None
    s = s.strip()
    m = _DATE_CN.search(s)
    if m:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _DATE_ISO.search(s)
    if m:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def gtimg_prefix(code: str) -> str:
    return "sh" if code.startswith(("60", "68", "90", "11", "5", "4")) else "sz"


def fetch_realtime(code: str) -> dict:
    """腾讯 gtimg 单只实时快照。返回 price / chg_pct / ok"""
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


def fetch_history(code: str, start: str, end: str, retries: int = 5):
    """新浪日线 qfq，返回 DataFrame 或 None。proxy 已禁用。"""
    sym = gtimg_prefix(code) + code
    last = None
    for attempt in range(retries):
        try:
            df = ak.stock_zh_a_daily(symbol=sym, start_date=start, end_date=end, adjust="qfq")
            if df is not None and not df.empty:
                return df
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 + attempt * 0.5)
    return None


def verify_signals() -> dict:
    signals = json.loads(SIGNALS_FILE.read_text(encoding="utf-8"))
    today = datetime.date.today()

    codes = sorted({s["stock_code"] for s in signals})
    hist_cache: dict[str, tuple] = {}
    start_map: dict[str, str] = {}

    def _fetch_one(code: str, end: str) -> None:
        sdates = [parse_date(s["recorded_at"]) for s in signals if s["stock_code"] == code]
        recent = [d for d in sdates if d and (today - d).days <= 400]
        start = (min(recent) - datetime.timedelta(days=10)).strftime("%Y%m%d") if recent \
            else (today - datetime.timedelta(days=40)).strftime("%Y%m%d")
        start_map[code] = start
        df = fetch_history(code, start, end)
        if df is not None:
            date_col = "date" if "date" in df.columns else df.columns[0]
            m = {str(row[date_col])[:10]: float(row["close"]) for _, row in df.iterrows()}
            last_close = float(df["close"].iloc[-1])
            hist_cache[code] = (m, last_close)
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
    return report


def build_report(signals: list, today: datetime.date, rolling_days: int = ROLLING_DAYS) -> dict:
    cutoff = today - datetime.timedelta(days=rolling_days)
    accounts: dict = {}
    ov = {"total": 0, "verified": 0, "bullish": 0, "with_return": 0, "hits": 0, "ret_sum": 0.0}
    # 总体滚动窗口累计
    ov_win_samples = ov_win_hits = 0
    ov_win_ret_sum = 0.0
    for s in signals:
        a = s["account"]
        acc = accounts.setdefault(a, {
            "total": 0, "verified": 0, "bullish": 0, "bearish": 0,
            "with_return": 0, "hits": 0, "ret_sum": 0.0, "stocks": set(),
            "win_samples": 0, "win_hits": 0, "win_ret_sum": 0.0,  # 滚动窗口内累计
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
            acc["with_return"] += 1
            ov["with_return"] += 1
            acc["ret_sum"] += s["final_return_pct"]
            ov["ret_sum"] += s["final_return_pct"]
            # 滚动窗口：仅计入信号日在近 rolling_days 内的样本
            sdate = parse_date(s.get("recorded_at"))
            if sdate is not None and sdate >= cutoff:
                acc["win_samples"] += 1
                acc["win_ret_sum"] += s["final_return_pct"]
                if s.get("hit") is True:
                    acc["win_hits"] += 1
                ov_win_samples += 1
                ov_win_ret_sum += s["final_return_pct"]
                if s.get("hit") is True:
                    ov_win_hits += 1
        if s.get("hit") is True and s.get("signal") != "neutral":
            acc["hits"] += 1
            ov["hits"] += 1

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
    sys.exit(0 if main() else 1)
