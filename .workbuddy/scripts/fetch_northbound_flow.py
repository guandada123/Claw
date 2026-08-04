#!/usr/bin/env python3
"""
fetch_northbound_flow.py — 获取北向资金（沪深股通北上净买额），支持多日历史

⚠️ 已降级(2024-05起): 北向实时净买额停止披露，东财 kamt 接口恒为0，数据源失效。
   本脚本现直接返回 {"ok": false, "discontinued": true}，不再请求死源，下游监控跳过该维度。

数据源(已失效，保留仅供历史参考): 东方财富
  当日实时:  push2.eastmoney.com/api/qt/kamt/get
    hk2sh.dayNetAmtIn = 北向(沪股通)当日净买额(元)
    hk2sz.dayNetAmtIn = 北向(深股通)当日净买额(元)
    北向合计 = (hk2sh + hk2sz) / 1e8 → 亿元
  历史多日:  push2his.eastmoney.com/api/qt/kamt.kline/get
    hk2sh / hk2sz = ["YYYY-MM-DD,净额", ...]
    单位: 元(与 get.dayNetAmtIn 同源，经沙盒验证 get 接口 dayNetAmtIn 单位为元:
          sh2hk=4200000.0=420万)，kline 同字段体系推断为元 → ÷1e8 转亿元。
    ⚠️ 沙盒环境北向(hk2sh/hk2sz)恒为0无法实测校准; 若真实环境发现
       单日量级异常(如数千亿)，说明 kline 实为亿元，去掉下方 ÷1e8 即可。
  注: sh2hk/sz2hk 是港股通(内资买港股)方向，非北向，不计入。

用法:
    python3 fetch_northbound_flow.py             → 当日 + 近5日历史 + 连续同向累计
    python3 fetch_northbound_flow.py --days 10   → 指定历史天数

输出 JSON（供「助理实盘监控」PHASE 4 消费）:
  {
    "ok": true,
    "date": "2026-07-20",
    "net_flow": 12.34,            # 当日北向合计(亿) 正=流入
    "sh_north": 5.67, "sz_north": 6.67,
    "recent": [                   # 近N日北向合计(亿)，时间升序
       {"date": "2026-07-16", "net": -8.1},
       {"date": "2026-07-17", "net": -25.3},
       {"date": "2026-07-20", "net": 12.34}
    ],
    "consecutive_days": 1,        # 当前连续同向天数(含当日)
    "consecutive_sum": 12.34,     # 连续同向累计净额(亿)
    "consecutive_dir": "inflow",  # inflow(流入)/outflow(流出)
    "source": "eastmoney"
  }
  数据不可达/当日为0 → {"ok": false, "net_flow": null, ...}（历史仍尽力返回）

PHASE 4 触发判定（调用方据此推送）:
  - 单日: abs(net_flow) > 30
  - 连续: consecutive_days >= 3 且 abs(consecutive_sum) > 80
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import date

KAMT_URL = (
    "https://push2.eastmoney.com/api/qt/kamt/get"
    "?fields1=f1,f2,f3,f4&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
    "&ut=b2884a393a59ad64002292a3e90d46a5"
)
KLINE_URL = (
    "https://push2his.eastmoney.com/api/qt/kamt.kline/get"
    "?fields1=f1,f3,f5&fields2=f51,f52&klt=101&lmt={lmt}"
    "&ut=b2884a393a59ad64002292a3e90d46a5"
)

_TIMEOUT = 8

# ⚠️ 北向实时净买额自 2024-05 起停止披露（沪深交易所不再公布沪深股通北上实时净买额），
#    东财 kamt 接口虽存活但 dayNetAmtIn 恒为 0。数据源已失效 → 降级：直接返回 discontinued，
#    不再请求死源（省一次无效网络调用，也避免把恒0误读为"当日净流出0亿"）。
NORTHBOUND_DISCONTINUED = True


def _http_get(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        return None


def _fetch_today() -> tuple[float | None, float | None]:
    """返回当日 (沪股通北向亿, 深股通北向亿)，失败返回 (None, None)。"""
    raw = _http_get(KAMT_URL)
    if not raw:
        return None, None
    try:
        data = (json.loads(raw).get("data") or {})
    except (json.JSONDecodeError, ValueError):
        return None, None
    sh = data.get("hk2sh", {}).get("dayNetAmtIn")
    sz = data.get("hk2sz", {}).get("dayNetAmtIn")
    if sh is None and sz is None:
        return None, None
    return round(float(sh or 0.0) / 1e8, 2), round(float(sz or 0.0) / 1e8, 2)


def _fetch_recent(days: int) -> list[dict]:
    """拉近 days 个交易日北向合计(亿)，时间升序。单位: 东财 kline 亿元口径。"""
    raw = _http_get(KLINE_URL.format(lmt=max(days, 3)))
    if not raw:
        return []
    try:
        data = (json.loads(raw).get("data") or {})
    except (json.JSONDecodeError, ValueError):
        return []

    def _parse(series: list) -> dict[str, float]:
        out = {}
        for item in series or []:
            parts = item.split(",")
            if len(parts) >= 2:
                try:
                    # kline f52 与 get.dayNetAmtIn 同源, 单位为元 → ÷1e8 转亿元
                    out[parts[0]] = float(parts[1]) / 1e8
                except ValueError:
                    pass
        return out

    sh_map = _parse(data.get("hk2sh"))
    sz_map = _parse(data.get("hk2sz"))
    all_dates = sorted(set(sh_map) | set(sz_map))
    recent = [
        {"date": d, "net": round(sh_map.get(d, 0.0) + sz_map.get(d, 0.0), 2)}
        for d in all_dates
    ]
    return recent[-days:]


def _consecutive(recent: list[dict]) -> tuple[int, float, str | None]:
    """从最近一日往前，统计连续同向(同号且非0)的天数与累计净额。"""
    if not recent:
        return 0, 0.0, None
    # 找最近一个非零日确定方向
    last = None
    for r in reversed(recent):
        if r["net"] != 0.0:
            last = r["net"]
            break
    if last is None:
        return 0, 0.0, None
    sign = 1 if last > 0 else -1
    total = 0.0
    count = 0
    for r in reversed(recent):
        if r["net"] == 0.0:
            break
        if (r["net"] > 0) == (sign > 0):
            total += r["net"]
            count += 1
        else:
            break
    direction = "inflow" if sign > 0 else "outflow"
    return count, round(total, 2), direction


def main(days: int = 5) -> dict:
    today = date.today().isoformat()
    if NORTHBOUND_DISCONTINUED:
        # 数据源已失效（2024-05 起停止披露），直接降级返回，不再请求死源
        return {
            "ok": False,
            "net_flow": None,
            "discontinued": True,
            "source": "discontinued",
            "date": today,
            "error": ("北向资金(沪深股通北上净买额)实时披露自 2024-05 起已停止，"
                      "东财 kamt 接口数据恒为0，数据源失效；监控跳过该维度。"),
            "recent": [],
            "consecutive_days": 0,
            "consecutive_sum": 0.0,
            "consecutive_dir": None,
        }
    sh_north, sz_north = _fetch_today()
    recent = _fetch_recent(days)
    cons_days, cons_sum, cons_dir = _consecutive(recent)

    base = {
        "recent": recent,
        "consecutive_days": cons_days,
        "consecutive_sum": cons_sum,
        "consecutive_dir": cons_dir,
        "source": "eastmoney",
        "date": today,
    }

    if sh_north is None and sz_north is None:
        base.update({"ok": False, "net_flow": None,
                     "error": "北向当日数据缺失(可能非交易日或接口未更新)"})
        return base

    net = round((sh_north or 0.0) + (sz_north or 0.0), 2)
    if net == 0.0:
        base.update({"ok": False, "net_flow": None,
                     "error": "北向资金当日为0(可能休市或接口未更新)"})
        return base

    base.update({"ok": True, "net_flow": net,
                 "sh_north": sh_north, "sz_north": sz_north})
    return base


if __name__ == "__main__":
    n = 5
    if "--days" in sys.argv:
        try:
            n = int(sys.argv[sys.argv.index("--days") + 1])
        except (IndexError, ValueError):
            n = 5
    print(json.dumps(main(n), ensure_ascii=False))
    sys.exit(0)
