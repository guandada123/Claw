#!/usr/bin/env python3
"""美股隔夜环境摘要打印器 — 供晚报自动化 PHASE 1 调用。

直接运行 fetch_us_market.py（强制刷新），解析其 indices + environment 输出，
打印 LLM 可直接填入晚报模板「🌐 美股隔夜环境」段的简洁行。
各源失败时优雅降级为「暂缺（数据源受限）」，绝不臆造。
"""
import json
import subprocess
import sys

SCRIPT = "/Users/guan/WorkBuddy/Claw/.workbuddy/scripts/fetch_us_market.py"


def _run() -> dict | None:
    try:
        r = subprocess.run(
            ["python3", SCRIPT, "--no-cache"],
            capture_output=True, text=True, timeout=40,
        )
        return json.loads(r.stdout)
    except Exception as e:
        print(f"美股环境采集失败: {e}")
        return None


def main() -> None:
    d = _run()
    if not d:
        print("美股三大指数: 暂缺")
        print("VIX = 暂缺")
        print("黄金 暂缺 | 白银 暂缺 | 原油 暂缺")
        print("美元指数 DXY = 暂缺 | 美债10Y = 暂缺")
        return

    idx = d.get("indices", {})

    def pct(k: str) -> str:
        v = idx.get(k, {}).get("change_pct")
        return f"{v:+}%" if isinstance(v, (int, float)) else "暂缺"

    env = d.get("environment", {})

    def ev(k: str) -> str:
        v = env.get(k, {})
        if v.get("price") is not None:
            return f"{v['change_pct']:+}% (源={v.get('source')})"
        return "暂缺（数据源受限）"

    print(f"美股三大指数: 道 {pct('dow')} | 标 {pct('sp500')} | 纳 {pct('nasdaq')}")
    print(f"VIX = {env.get('vix', {}).get('price')} {ev('vix')}")
    print(f"黄金 {ev('gold')} | 白银 {ev('silver')} | 原油 {ev('oil')}")
    print(f"美元指数 DXY = {ev('dollar_index')} | 美债10Y = {ev('us10y')}")


if __name__ == "__main__":
    main()
