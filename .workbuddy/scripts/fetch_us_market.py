#!/usr/bin/env python3
"""
美股市场数据获取器 — 统一美股盘前/收盘/海外市场数据采集，带缓存。

用法:
  python3 fetch_us_market.py                    # 全量数据（缓存优先）
  python3 fetch_us_market.py --session premarket  # 盘前模式（期指+事件）
  python3 fetch_us_market.py --session closing    # 收盘模式（三大指数+龙头）
  python3 fetch_us_market.py --no-cache           # 跳过缓存，强制刷新
  python3 fetch_us_market.py --summary-only       # 仅输出摘要（供早报等嵌入使用）

缓存位置: /tmp/us_market_cache.json (1小时有效期)

输出 JSON:
{
  "session": "premarket" | "closing",
  "timestamp": "2026-07-12T09:00:00",
  "indices": {
    "dow": {"name": "道琼斯", "price": 44500.0, "change_pct": 0.5},
    "nasdaq": {"name": "纳斯达克", "price": 21000.0, "change_pct": 0.8},
    "sp500": {"name": "标普500", "price": 6100.0, "change_pct": 0.6}
  },
  "stocks": {
    "AAPL": {"name": "苹果", "price": 250.0, "change_pct": 1.2},
    "NVDA": {"name": "英伟达", "price": 180.0, "change_pct": 2.5},
    "TSLA": {"name": "特斯拉", "price": 380.0, "change_pct": -0.8},
    "MSFT": {"name": "微软", "price": 480.0, "change_pct": 0.3},
    "AMD": {"name": "AMD", "price": 160.0, "change_pct": 1.1}
  },
  "korea": {
    "kospi": {"price": 2800.0, "change_pct": 0.4},
    "kosdaq": {"price": 850.0, "change_pct": 0.2}
  },
  "environment": {
    "vix": {"name": "恐慌指数VIX", "price": 18.32, "change_pct": -2.1, "source": "Wind"},
    "gold": {"name": "黄金", "price": 4123.92, "change_pct": 1.17, "source": "腾讯期货"},
    "silver": {"name": "白银", "price": 59.76, "change_pct": 1.10, "source": "腾讯期货"},
    "oil": {"name": "原油WTI", "price": 87.19, "change_pct": 3.37, "source": "腾讯期货"},
    "dollar_index": {"price": null, "change_pct": null, "source": "[缺失]", "note": "无可靠免费源(Wind无DXY标的/eastmoney限流)"},
    "us10y": {"price": null, "change_pct": null, "source": "[缺失]", "note": "无可靠免费源(Wind无US10Y标的/eastmoney限流)"}
  },
  "a_share_map": "半导体:看多(中芯/长电) | 新能源:中性(宁德)",
  "summary": "道+0.5%/标+0.6%/纳+0.8%",
  "cached": true,
  "source": "腾讯行情API | WebSearch fallback"
}
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

CACHE_FILE = Path("/tmp/us_market_cache.json")
CACHE_TTL = 3600  # 1 hour

# 腾讯行情代码映射（美股需加 us 前缀，否则返回 v_pv_none_match）
QQ_CODES = {
    "dow": "usDJI",
    "nasdaq": "usIXIC",
    "sp500": "usINX",
    "kospi": "KOSPI",       # 腾讯无韩股行情,始终 none_match,保留原值
    "kosdaq": "KOSDAQ",
}

QQ_STOCKS = {
    "AAPL": "usAAPL",
    "MSFT": "usMSFT",
    "NVDA": "usNVDA",
    "TSLA": "usTSLA",
    "AMD": "usAMD",
}

# Wind 万得美股指数代码
WIND_INDICES = {
    "dow": "DJI.GI",
    "nasdaq": "IXIC.GI",
    "sp500": "SPX.GI",
}

# 美股环境字段代码映射
# 腾讯 hf 期货格式（v_hf_xxx="价,涨跌幅,..."，逗号分隔）：金银油（实时可靠）
QQ_ENV = {
    "gold": "hf_GC",       # 黄金期货
    "silver": "hf_SI",     # 白银期货
    "oil": "hf_CL",        # WTI 原油期货
}

# Wind 万得环境指数（权威源）。注：DXY/US10Y 经实测 Wind 无对应标的
# （MARKET_TARGET_NOT_FOUND），免费源(eastmoney限流/Yahoo封/Tencent无)亦不可靠，故标 [缺失]。
WIND_ENV = {
    "vix": "VIX.GI",       # 恐慌指数 CBOE VIX（Wind 实测可用）
}


def _fetch_wind_index(name: str, windcode: str) -> dict | None:
    """从 Wind 获取美股指数行情。"""
    WIND_CLI = Path.home() / ".agents" / "skills" / "wind-mcp-skill" / "scripts" / "cli.mjs"
    if not WIND_CLI.exists():
        return None
    try:
        params = json.dumps({"windcode": windcode, "indexes": "最新成交价,涨跌幅"}, ensure_ascii=False)
        r = subprocess.run(
            ["node", str(WIND_CLI), "call", "index_data", "get_index_price_indicators", params],
            capture_output=True, text=True, timeout=15,
            cwd=str(WIND_CLI.parent.parent),
        )
        if r.returncode != 0:
            return None
        payload = json.loads(r.stdout)
        text_payload = payload.get("content", [{}])[0].get("text", "")
        if not text_payload:
            return None
        data = json.loads(text_payload)
        rows = data.get("data", {}).get("rows", [])
        if not rows:
            return None
        row = rows[0]
        return {
            "name": name,
            "price": float(row[0]) if row[0] else 0,
            "change_pct": float(row[1]) if row[1] else 0,
        }
    except Exception:
        return None


def _fetch_tencent_qq(codes: list[str]) -> str:
    """Fetch data from Tencent stock API.

    注意: 腾讯行情返回 GBK 编码字节流，绝不能用 text=True (会按 UTF-8 解码报错),
    必须 bytes 模式读取后再显式 decode('gbk')。
    """
    code_str = ",".join(codes)
    url = f"https://qt.gtimg.cn/q={code_str}"
    try:
        result = subprocess.run(
            ["curl", "-s", url],
            capture_output=True, timeout=10,
        )
        raw = result.stdout  # bytes
        try:
            return raw.decode("gbk", errors="ignore")
        except Exception:
            return raw.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _parse_qq_line(line: str) -> dict | None:
    """Parse a single Tencent QQ quote line.

    Format: v_<code>="<name>~<price>~<change_pct>~..."
    """
    if "=" not in line:
        return None
    try:
        _, value = line.split("=", 1)
        value = value.strip('";\n')
        fields = value.split("~")
        if len(fields) < 4:
            return None
        return {
            "name": fields[1] if len(fields) > 1 else "",
            "price": float(fields[3]) if len(fields) > 3 and fields[3] else 0,
            "change_pct": float(fields[32]) if len(fields) > 32 and fields[32] else 0,
        }
    except Exception:
        return None


def _parse_hf_line(line: str) -> dict | None:
    """Parse Tencent 期货(hf_) quote line.

    Format: v_hf_GC="4125.75,1.21,4123.40,..." -> [0]=price [1]=change_pct
    """
    if "=" not in line:
        return None
    try:
        _, value = line.split("=", 1)
        value = value.strip('";\n')
        parts = value.split(",")
        if len(parts) < 2:
            return None
        return {
            "price": float(parts[0]) if parts[0] else 0.0,
            "change_pct": float(parts[1]) if parts[1] else 0.0,
        }
    except Exception:
        return None


def _fetch_environment() -> dict:
    """采集美股环境字段：恐慌(VIX)/金银油；DXY/US10Y 暂标 [缺失]。

    可用源（实测）：
      - 金银油：腾讯 hf 期货（实时可靠）
      - VIX：Wind VIX.GI（权威；Wind 不可用则 [缺失]）
      - DXY/US10Y：免费源(eastmoney限流/Yahoo封/Tencent无)均不可靠，
        Wind 亦无对应标的（MARKET_TARGET_NOT_FOUND）→ 统一标 [缺失]
    缺失不阻断报告（与晚报模板「不阻断报告」原则一致）。
    """
    env: dict = {}

    # 腾讯 hf 期货（金银油，实时可靠）
    raw = _fetch_tencent_qq(list(QQ_ENV.values()))
    for line in raw.split("\n"):
        if not line.startswith("v_hf_"):
            continue
        parsed = _parse_hf_line(line)
        if not parsed:
            continue
        for key, code in QQ_ENV.items():
            if code in line and key not in env:
                env[key] = {**parsed, "source": "腾讯期货"}
                break

    # Wind 环境指数（VIX）
    for key, wcode in WIND_ENV.items():
        w = _fetch_wind_index(key, wcode)
        if w:
            env[key] = {**w, "source": "Wind"}

    # 缺失项统一标注
    notes = {
        "vix": "Wind VIX.GI 调用未返回(CLI缺失/网络/点数)",
        "dollar_index": "无可靠免费源(Wind无DXY标的/eastmoney限流)",
        "us10y": "无可靠免费源(Wind无US10Y标的/eastmoney限流)",
    }
    for key in ("vix", "dollar_index", "us10y"):
        if key not in env:
            env[key] = {"price": None, "change_pct": None,
                        "source": "[缺失]", "note": notes[key]}

    return env


def load_cache() -> dict | None:
    """Load cached data if not expired."""
    if not CACHE_FILE.exists():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text())
        ts = data.get("timestamp", "")
        if ts:
            dt = datetime.fromisoformat(ts)
            if (datetime.now() - dt).total_seconds() < CACHE_TTL:
                return data
    except Exception:
        pass
    return None


def save_cache(data: dict):
    """Save data to cache."""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def fetch_market_data(session: str = "auto", no_cache: bool = False) -> dict:
    """Fetch US and Korea market data."""
    # Check cache
    if not no_cache:
        cached = load_cache()
        if cached:
            cached["cached"] = True
            return cached

    # Determine session
    now = datetime.now()
    if session == "auto":
        hour = now.hour
        session = "closing" if 4 <= hour < 9 else "premarket"

    result = {
        "session": session,
        "timestamp": now.isoformat(),
        "indices": {},
        "stocks": {},
        "korea": {},
        "environment": {},
        "a_share_map": "",
        "summary": "",
        "cached": False,
        "source": "腾讯行情API",
    }

    # Fetch indices — Wind 优先，腾讯降级
    result["indices"] = {}
    for key, wcode in WIND_INDICES.items():
        w = _fetch_wind_index(key, wcode)
        if w:
            result["indices"][key] = w
            result["source"] = "Wind|腾讯行情API"

    # 腾讯补充 Wind 未覆盖的指数（韩股等）
    idx_codes = list(QQ_CODES.values())
    raw = _fetch_tencent_qq(idx_codes)
    for line in raw.split("\n"):
        parsed = _parse_qq_line(line)
        if parsed:
            for key, code in QQ_CODES.items():
                if code in line and key not in result["indices"]:
                    result["indices"][key] = parsed
                    break

    # Fetch key stocks
    stock_codes = list(QQ_STOCKS.values())
    raw_stocks = _fetch_tencent_qq(stock_codes)
    for line in raw_stocks.split("\n"):
        parsed = _parse_qq_line(line)
        if parsed:
            for key, code in QQ_STOCKS.items():
                if code in line:
                    result["stocks"][key] = parsed
                    break

    # Fetch environment fields (VIX / 美元 / 美债 / 金银油)
    result["environment"] = _fetch_environment()

    # Build summary
    parts = []
    for key in ["dow", "sp500", "nasdaq"]:
        idx = result["indices"].get(key, {})
        if idx:
            chg = idx.get("change_pct", 0)
            sign = "+" if chg > 0 else ""
            parts.append(f"{'道' if key == 'dow' else '标' if key == 'sp500' else '纳'}{sign}{chg}%")
    result["summary"] = "/".join(parts) if parts else "数据获取中"

    save_cache(result)
    return result


def main():
    parser = argparse.ArgumentParser(description="美股市场数据获取器")
    parser.add_argument("--session", choices=["premarket", "closing", "auto"],
                        default="auto", help="数据模式")
    parser.add_argument("--no-cache", action="store_true",
                        help="跳过缓存，强制刷新")
    parser.add_argument("--summary-only", action="store_true",
                        help="仅输出摘要行")
    args = parser.parse_args()

    data = fetch_market_data(session=args.session, no_cache=args.no_cache)

    if args.summary_only:
        print(data.get("summary", "数据获取中"))
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
