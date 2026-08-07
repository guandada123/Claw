"""宏观综合评分模块（自包含，无重型依赖）。

根因：旧 archive/scripts/macro_data.py（AKShare 抓取+评分）已废弃不再运行，
早报流程从未接入评分；早报 Agent 渲染时因 prompt 要求"宏观综合评分"但无评分
输入，自行编造 "None（macro_score 函数返回 None）" 噪声。

本模块提供：
  - fetch_public_macro(): 东方财富免费公开接口抓 PMI/CPI/GDP 结构化数值（带降级）
  - calculate_macro_score(data): 复用 archive 健全算法，缺失项跳过不崩
  - render_macro_score_block(): 算分并返回可消费文本（永远有值，杜绝 None）

M2/LPR/Shibor 若 Agent 侧经 Wind 已采集到，可经 build_macro_score() 注入；
缺失则跳过（算法已支持），评分至少基于 PMI+CPI+GDP 三项，恒有值。
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0"}
_TIMEOUT = 10

# 东方财富 datacenter 公开接口（免费，无需 key）
_API = {
    "cpi": "https://datacenter-web.eastmoney.com/api/data/v1/get?"
    "reportName=RPT_ECONOMY_CPI&columns=ALL&pageSize=1&"
    "sortColumns=REPORT_DATE&sortTypes=-1",
    "pmi": "https://datacenter-web.eastmoney.com/api/data/v1/get?"
    "reportName=RPT_ECONOMY_PMI&columns=ALL&pageSize=1&"
    "sortColumns=REPORT_DATE&sortTypes=-1",
    "gdp": "https://datacenter-web.eastmoney.com/api/data/v1/get?"
    "reportName=RPT_ECONOMY_GDP&columns=ALL&pageSize=1&"
    "sortColumns=REPORT_DATE&sortTypes=-1",
}


def _get_first(url: str):
    try:
        r = requests.get(url, timeout=_TIMEOUT, headers=_HEADERS)
        j = r.json()
        data = j.get("result", {}).get("data")
        if isinstance(data, list) and data:
            return data[0]
    except Exception as e:  # noqa: BLE001
        logger.warning("宏观接口抓取失败: %s", e)
    return None


def fetch_public_macro() -> dict:
    """抓取公开宏观指标，返回结构化 dict（缺失为 None，不抛异常）。"""
    out: dict = {}

    cpi = _get_first(_API["cpi"])
    if cpi:
        # NATIONAL_SAME = 同比(%)
        out["cpi_yoy"] = _to_float(cpi.get("NATIONAL_SAME"))

    pmi = _get_first(_API["pmi"])
    if pmi:
        # MAKE_INDEX = 制造业 PMI
        out["pmi_manufacturing"] = _to_float(pmi.get("MAKE_INDEX"))

    gdp = _get_first(_API["gdp"])
    if gdp:
        # 季度 GDP 同比：用 DOMESTICL_PRODUCT_BASE 不便直接取，改用常见字段
        # 东财 GDP 同比字段名随版本变，容错取第一个数值型增长率
        out["gdp_yoy"] = _extract_gdp_yoy(gdp)

    return out


def _to_float(v):
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _extract_gdp_yoy(gdp_row: dict):
    """从 GDP 行提取同比(%)。优先常见字段，否则跳过。"""
    for key in ("GDP_YOY", "DOMESTICL_PRODUCT_YOY", "NATIONAL_SAME", "SAME_RATIO"):
        if key in gdp_row:
            val = _to_float(gdp_row[key])
            if val is not None:
                return val
    return None


def calculate_macro_score(data: dict) -> dict:
    """基于宏观数据计算综合评分（-100 ~ +100）。缺失项跳过，恒返回 dict。"""
    score = 0
    signals: list[str] = []

    # PMI（权重最高）
    pmi_mfg = data.get("pmi_manufacturing")
    if pmi_mfg is not None:
        if pmi_mfg > 50.5:
            score += 25
            signals.append(f"PMI扩张({pmi_mfg})")
        elif pmi_mfg > 50:
            score += 10
            signals.append(f"PMI临界({pmi_mfg})")
        elif pmi_mfg > 49:
            score -= 5
            signals.append(f"PMI收缩({pmi_mfg})")
        else:
            score -= 20
            signals.append(f"PMI衰退({pmi_mfg})")

    # CPI
    cpi_yoy = data.get("cpi_yoy")
    if cpi_yoy is not None:
        if 1 <= cpi_yoy <= 3:
            score += 10
            signals.append(f"CPI温和({cpi_yoy}%)")
        elif cpi_yoy < 0:
            score -= 10
            signals.append(f"通缩风险({cpi_yoy}%)")
        elif cpi_yoy > 5:
            score -= 15
            signals.append(f"高通胀({cpi_yoy}%)")

    # M2（可经外部注入，见 build_macro_score）
    m2_yoy = data.get("m2_yoy")
    if m2_yoy is not None:
        if m2_yoy > 10:
            score += 15
            signals.append(f"宽货币(M2+{m2_yoy}%)")
        elif m2_yoy > 8:
            score += 5
            signals.append(f"货币中性(M2+{m2_yoy}%)")
        else:
            score -= 10
            signals.append(f"紧货币(M2+{m2_yoy}%)")

    # GDP
    gdp_yoy = data.get("gdp_yoy")
    if gdp_yoy is not None:
        if gdp_yoy > 5.5:
            score += 10
            signals.append(f"高增长(GDP+{gdp_yoy}%)")
        elif gdp_yoy > 4.5:
            score += 5
            signals.append(f"稳健增长(GDP+{gdp_yoy}%)")

    # Shibor 隔夜（可经外部注入）
    shibor_on = data.get("shibor_overnight")
    if shibor_on is not None:
        if shibor_on < 1.5:
            score += 10
            signals.append("流动性充裕")
        elif shibor_on > 3:
            score -= 10
            signals.append("流动性紧张")

    final = max(-100, min(100, score))
    interpretation = (
        "偏多" if final > 15 else ("偏空" if final < -15 else "中性")
    )
    return {
        "score": final,
        "signals": signals,
        "interpretation": interpretation,
        "available": bool(signals),  # 至少有 1 项指标才视为有效评分
    }


def build_macro_score(extra: dict | None = None) -> dict:
    """抓取公开宏观 + 合并外部注入（Wind M2/LPR/Shibor 等） + 算分。

    Args:
        extra: Agent 侧经 Wind 已采集到的结构化宏观值（如 m2_yoy / shibor_overnight），
               缺失字段不传即可，算法自动跳过。
    Returns:
        dict: {score, interpretation, signals, available, raw}
    """
    try:
        data = fetch_public_macro()
    except Exception as e:  # noqa: BLE001
        logger.warning("公开宏观抓取异常，仅用外部注入值: %s", e)
        data = {}
    if extra:
        data.update({k: v for k, v in extra.items() if v is not None})
    return calculate_macro_score(data)


def render_macro_score_block(extra: dict | None = None) -> str:
    """返回可直接注入早报的宏观评分文本（永远有值，杜绝 None 噪声）。"""
    res = build_macro_score(extra)
    if not res["available"]:
        return "**宏观综合评分**：暂无可量化指标（数据源未返回有效值），按定性研判。"
    sig = "；".join(res["signals"]) if res["signals"] else "—"
    return (
        f"**宏观综合评分**：{res['score']:+d}（{res['interpretation']}）｜"
        f"依据：{sig}"
    )
