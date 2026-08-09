#!/usr/bin/env python3
"""wind_deep_report.py — Wind 宏观/财报/估值深度研报（周日定时）

数据骨干（全部走 Wind AIFin，经 wind-mcp-skill CLI）:
  - 宏观: economic_data.natural_language_get_edb_data (GDP/CPI/PMI/LPR)
  - 财报+估值: stock_data.get_stock_fundamentals (ROE/毛利/净利/营收/净利 + PE/PB/市值)
  - 技术: stock_data.get_stock_technicals (多周期涨跌幅)
  - 风险: stock_data.get_risk_metrics (Beta/Sharpe/波动率/回撤)
覆盖标的: 模拟盘持仓(投顾) + 实盘持仓(国金)。

用法:
  python3 wind_deep_report.py            # 默认 dry-run（仅打印，不推送）
  python3 wind_deep_report.py --push    # 实际推送飞书群（自动化调用）
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # .workbuddy
DATA = ROOT / "data"
SCRIPTS = ROOT / "scripts"
CLI = Path.home() / ".agents" / "skills" / "wind-mcp-skill" / "scripts" / "cli.mjs"

# 展示时跳过的元数据列
META_COLS = {"Wind代码", "证券简称", "交易币种", "交易时间", "日期", "记账本位币", "币种"}


def clean_label(k: str) -> str:
    """把 Wind 原始列名归一化为易读中文标签（仅影响展示，不改数据）。"""
    k = re.sub(r"最新总市值\d*", "总市值", k)
    k = k.replace("最新市净率PB_LF", "PB").replace("最新市盈率PE_TTM", "PE")
    k = re.sub(r"最新市净率", "PB", k).replace("最新市盈率", "PE")
    k = re.sub(r"近(\d+)交易日涨跌幅", r"近\1日涨跌幅", k)
    k = k.replace("最新涨跌幅", "近1日涨跌幅")
    k = re.sub(r"(20\d{2})年年报", r"\1年报", k)
    k = k.replace("去年年报", "年报")
    return k


def wind_call(server: str, tool: str, params: dict, timeout: int = 25) -> list | None:
    """调用 Wind CLI，返回 rows 映射为 dict 的列表；失败返回 None。"""
    try:
        p = subprocess.run(
            ["node", str(CLI), "call", server, tool, json.dumps(params, ensure_ascii=False)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if p.returncode != 0:
            return None
        d = json.loads(p.stdout)
        content = d.get("content")
        if not content:
            return None
        payload = json.loads(content[0]["text"])
        blocks = payload.get("data", {}).get("data", [])
        if not blocks:
            return None
        block = blocks[0]
        cols = [c["name"] for c in block.get("columns", [])]
        rows = block.get("rows", [])
        return [dict(zip(cols, r)) for r in rows]
    except Exception:  # noqa: BLE001
        return None


def wind_call_timeseries(server: str, tool: str, params: dict, timeout: int = 30) -> list | None:
    """economic_data 返回时间序列 {meta.name, date[], value[]}，与 stock_data 的
    columns/rows 形状不同，需单独解析。返回 [(name, date, value), ...]（取每个序列最新非空点）。"""
    try:
        p = subprocess.run(
            ["node", str(CLI), "call", server, tool, json.dumps(params, ensure_ascii=False)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if p.returncode != 0:
            return None
        d = json.loads(p.stdout)
        content = d.get("content")
        if not content:
            return None
        payload = json.loads(content[0]["text"])
        blocks = payload.get("data", {}).get("data", [])
        out = []
        for blk in blocks:
            name = blk.get("meta", {}).get("name", "")
            dates = blk.get("date", [])
            values = blk.get("value", [])
            latest = None
            for dt, val in zip(dates, values):
                if val is not None and val != "":
                    latest = (dt, val)
            if latest:
                out.append((name, latest[0], latest[1]))
        return out
    except Exception:  # noqa: BLE001
        return None


def load_json(p: Path) -> dict:
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def get_targets() -> list:
    """返回 [(code, name, bucket)]，bucket=实盘/模拟盘。"""
    out = []
    sim = load_json(DATA / "simulation" / "portfolio.json").get("positions", {})
    for code, pos in sim.items():
        out.append((code, pos.get("name", code), "模拟盘"))
    real = load_json(DATA / "user" / "portfolio.json").get("holdings", [])
    for h in real:
        out.append((h["code"], h.get("name", h["code"]), "实盘"))
    return out


def render_stock(code: str, name: str, bucket: str) -> list:
    q = f"{name}{code}"
    lines = [f"#### {name}({code}) · {bucket}"]
    # 财报 + 估值（一问覆盖）
    fin = wind_call(
        "stock_data",
        "get_stock_fundamentals",
        {
            "question": f"{q}最近一期年报的ROE、销售毛利率、销售净利率、营业收入、归母净利润，"
            f"以及当前的PE(TTM)、PB、总市值"
        },
    )
    if fin:
        row = fin[0]
        kv = [
            f"{clean_label(k)}={v}"
            for k, v in row.items()
            if k not in META_COLS and v is not None and v != "None"
        ]
        lines.append("- 财报/估值: " + " ｜ ".join(kv))
    else:
        lines.append("- 财报/估值: （获取失败）")
    # 技术 多周期涨跌
    tech = wind_call(
        "stock_data",
        "get_stock_technicals",
        {"question": f"{q}近1日、5日、20日、60日、年初至今的涨跌幅"},
    )
    if tech:
        row = tech[0]
        kv = [f"{clean_label(k)}={v}%" for k, v in row.items() if "涨跌幅" in k]
        lines.append("- 技术: " + " ｜ ".join(kv))
    else:
        lines.append("- 技术: （获取失败）")
    # 风险
    risk = wind_call(
        "stock_data",
        "get_risk_metrics",
        {"question": f"{q}的Beta、Sharpe比率、年化波动率、最大回撤"},
    )
    if risk:
        row = risk[0]
        kv = [
            f"{clean_label(k)}={v}"
            for k, v in row.items()
            if k not in META_COLS and v is not None and v != "None"
        ]
        lines.append("- 风险: " + " ｜ ".join(kv))
    else:
        lines.append("- 风险: （获取失败）")
    return lines


# 宏观四项指标：include 命中即候选，exclude 命中即剔除（过滤一致预测/年度/累计等噪声序列）
MACRO_TARGETS = [
    ("PMI", ["制造业PMI"], ["一致预测", "综合", "大型", "中型", "小型"]),
    ("GDP", ["不变价:当季同比"], ["累计", "全年", "IMF", "WorldBank", "预测", "环比"]),
    ("LPR", ["贷款市场报价利率(LPR):1年"], ["5年", "5年期"]),
    ("CPI", ["CPI:同比"], ["IMF", "WorldBank", "预测", "环比"]),
]


def _pick_indicator(series: list, inc: list, exc: list) -> tuple | None:
    """在 time-series 列表中按 include/exclude 过滤，并取日期最新者（同日期取首个）。"""
    cands = []
    for name, dt, val in series:
        if any(k in name for k in inc) and not any(k in name for k in exc):
            cands.append((name, dt, val))
    if not cands:
        return None
    # 按日期降序，最新优先
    cands.sort(key=lambda x: x[1], reverse=True)
    return cands[0]


def render_macro() -> list:
    lines = ["### 🌐 宏观快照（Wind EDB）"]
    series = wind_call_timeseries(
        "economic_data",
        "natural_language_get_edb_data",
        {
            "executionMode": "searchFetch",
            "question": "中国GDP同比、CPI同比、制造业PMI、LPR一年期",
            "observation": "6",
        },
    )
    if not series:
        lines.append("- （宏观数据获取失败，详见早/晚报）")
        return lines
    picked = []
    for label, inc, exc in MACRO_TARGETS:
        hit = _pick_indicator(series, inc, exc)
        if hit:
            picked.append((label, hit))
    if not picked:
        lines.append("- （宏观数据为空，详见早/晚报）")
        return lines
    for label, (name, dt, val) in picked:
        lines.append(f"- **{label}**：{val}（{dt}，{name}）")
    return lines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true", help="实际推送飞书（默认 dry-run）")
    args = ap.parse_args()

    today = datetime.date.today().strftime("%Y-%m-%d")
    targets = get_targets()

    body = [f"📑 **Wind 深度研报 · {today}（周日）**", ""]
    body += render_macro()
    body += [""]
    body += ["### 🏢 持仓深度（财报/估值/技术/风险）"]
    for code, name, bucket in targets:
        body += render_stock(code, name, bucket)
        body += [""]
    body += [
        "---",
        "📌 数据：Wind AIFin（财报/估值/宏观）｜ 覆盖模拟盘+实盘持仓 ｜ 每周日 20:00 自动生成",
        "⚠️ 本报告为数据聚合，非投资建议；ROE/毛利等为最近披露期，PE/PB 为查询时点。",
    ]

    text = "\n".join(body)
    title = f"Wind深度研报 {today}"

    if args.push:
        r = subprocess.run(
            ["bash", str(SCRIPTS / "push_feishu.sh"), title, text, "--level", "info"],
            capture_output=True,
            text=True,
        )
        print(r.stdout)
        if r.stderr:
            print(r.stderr, file=sys.stderr)
        print("PUSH exit:", r.returncode)
    else:
        print("=== DRY-RUN（不加 --push 不推送）===")
        print(title)
        print(text)


if __name__ == "__main__":
    main()
