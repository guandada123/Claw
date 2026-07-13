#!/usr/bin/env python3
"""
宏观数据采集脚本 — 基于 AKShare
覆盖指标: GDP / CPI / PMI / 货币供应 / Shibor / 社融 / LPR / 外汇储备
输出: data/macro_data.json（标准化格式）
Python: 系统 3.9（AKShare 已安装）
"""

import json
import sys
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")

import akshare as ak

# 输出目录
DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── AnySearch 回退源（双数据源：AKShare 优先 + AnySearch 降级）──
# helper 位于项目 scripts/ 目录（与 .workbuddy/scripts/ 同级）
_HELPER_PATH = Path(__file__).resolve().parent.parent / "scripts" / "anysearch_helper.py"
_anysearch_helper = None
if _HELPER_PATH.exists():
    sys.path.insert(0, str(_HELPER_PATH.parent))
    try:
        import anysearch_helper as _anysearch_helper
    except Exception:
        _anysearch_helper = None

# AKShare→AnySearch 类型映射（回退层覆盖的 5 类干净源）
_AKSHARE_TO_ANYSEARCH = {
    "gdp": "gdp",
    "cpi": "cpi",
    "money_supply": "money_supply",
    "lpr": "lpr",
    "shibor": "shibor",
}


def safe_fetch(name: str, fn, **kwargs) -> dict:
    """安全调用 AKShare 接口，统一错误处理"""
    try:
        df = fn(**kwargs)
        if df is None or df.empty:
            return {"status": "empty", "error": "no data returned"}
        # 取最近 12 条（AKShare 返回降序：最新在前，用 head）
        recent = df.head(12)
        return {
            "status": "ok",
            "total_rows": len(df),
            "latest_12": recent.to_dict(orient="records"),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _with_fallback(name: str, fn, as_type: str, mapper, **kwargs) -> dict:
    """AKShare 优先，失败/空时回退 AnySearch finance.macro。

    Args:
        name: 指标名（用于日志）
        fn: AKShare 取数函数
        as_type: 回退映射键（gdp/cpi/money_supply/lpr/shibor）
        mapper: 将 AnySearch 原始 dict 转成与 AKShare 分支一致的字段结构
    Returns:
        dict: 成功含业务字段 + '_source'（akshare/anysearch）；
              全部失败含 'error' + '_source': 'none'
    """
    ak_result = safe_fetch(name, fn, **kwargs)
    if ak_result["status"] == "ok":
        d = mapper(ak_result)
        d["_source"] = "akshare"
        return d
    # AKShare 失败 → 回退 AnySearch
    if _anysearch_helper is None or as_type not in _AKSHARE_TO_ANYSEARCH:
        return {"error": ak_result.get("error", "akshare_failed"),
                "_source": "none"}
    try:
        alt = _anysearch_helper.macro_indicator(_AKSHARE_TO_ANYSEARCH[as_type])
        if "error" in alt:
            return {"error": f"akshare_failed+anysearch_{alt['error']}",
                    "_source": "none"}
        d = mapper({"_raw": {"latest_12": [alt]}, "status": "ok"}, alt=alt)
        d["_source"] = "anysearch"
        return d
    except Exception as e:
        return {"error": f"akshare_failed+anysearch_exc({e})",
                "_source": "none"}


def fetch_gdp() -> dict:
    """GDP 数据（季度）— AKShare 优先 + AnySearch 降级"""
    def _map(ak_result, alt=None):
        if alt is not None:
            # AnySearch 回退：{gdp, gdp_yoy, pi, pi_yoy, quarter, si, si_yoy, ti, ti_yoy}
            return {
                "latest_quarter": alt.get("quarter", "N/A"),
                "gdp_absolute": alt.get("gdp", None),
                "gdp_yoy": alt.get("gdp_yoy", None),
                "primary_yoy": alt.get("pi_yoy", None),
                "secondary_yoy": alt.get("si_yoy", None),
                "tertiary_yoy": alt.get("ti_yoy", None),
                "history": [alt],
            }
        latest = ak_result["latest_12"][0] if ak_result["latest_12"] else {}
        return {
            "latest_quarter": latest.get("季度", "N/A"),
            "gdp_absolute": latest.get("国内生产总值-绝对值", None),
            "gdp_yoy": latest.get("国内生产总值-同比增长", None),
            "primary_yoy": latest.get("第一产业-同比增长", None),
            "secondary_yoy": latest.get("第二产业-同比增长", None),
            "tertiary_yoy": latest.get("第三产业-同比增长", None),
            "history": ak_result["latest_12"],
        }
    return _with_fallback("GDP", ak.macro_china_gdp, "gdp", _map)


def fetch_cpi() -> dict:
    """CPI 数据（月度）— AKShare 优先 + AnySearch 降级"""
    def _map(ak_result, alt=None):
        if alt is not None:
            # AnySearch: {month, cnt_yoy, cnt_mom, nt_yoy, nt_mom, town_yoy, town_mom}
            return {
                "latest_month": alt.get("month", "N/A"),
                "cpi_national_yoy": alt.get("nt_yoy", alt.get("cnt_yoy", None)),
                "cpi_national_mom": alt.get("nt_mom", alt.get("cnt_mom", None)),
                "cpi_city_yoy": alt.get("town_yoy", None),
                "cpi_rural_yoy": None,
                "history": [alt],
            }
        latest = ak_result["latest_12"][0] if ak_result["latest_12"] else {}
        return {
            "latest_month": latest.get("月份", "N/A"),
            "cpi_national_yoy": latest.get("全国-同比增长", None),
            "cpi_national_mom": latest.get("全国-环比增长", None),
            "cpi_city_yoy": latest.get("城市-同比增长", None),
            "cpi_rural_yoy": latest.get("农村-同比增长", None),
            "history": ak_result["latest_12"],
        }
    return _with_fallback("CPI", ak.macro_china_cpi, "cpi", _map)


def fetch_pmi() -> dict:
    """PMI 数据（月度）— AKShare 单源（AnySearch 无结构化 PMI）"""
    result = safe_fetch("PMI", ak.macro_china_pmi)
    if result["status"] == "ok":
        latest = result["latest_12"][0] if result["latest_12"] else {}
        return {
            "latest_month": latest.get("月份", "N/A"),
            "pmi_manufacturing": latest.get("制造业-指数", None),
            "pmi_manufacturing_yoy": latest.get("制造业-同比增长", None),
            "pmi_non_manufacturing": latest.get("非制造业-指数", None),
            "pmi_non_manufacturing_yoy": latest.get("非制造业-同比增长", None),
            "history": result["latest_12"],
            "_source": "akshare",
        }
    return {"error": result.get("error", "unknown"), "_source": "none"}


def fetch_money_supply() -> dict:
    """货币供应量 M0/M1/M2（月度）— AKShare 优先 + AnySearch 降级"""
    def _map(ak_result, alt=None):
        if alt is not None:
            # AnySearch: {month, m0, m0_yoy, m1, m1_yoy, m2, m2_yoy}
            return {
                "latest_month": alt.get("month", "N/A"),
                "m2": alt.get("m2", None),
                "m2_yoy": alt.get("m2_yoy", None),
                "m1": alt.get("m1", None),
                "m1_yoy": alt.get("m1_yoy", None),
                "m0": alt.get("m0", None),
                "m0_yoy": alt.get("m0_yoy", None),
                "history": [alt],
            }
        latest = ak_result["latest_12"][0] if ak_result["latest_12"] else {}
        return {
            "latest_month": latest.get("月份", "N/A"),
            "m2": latest.get("货币和准货币(M2)-数量(亿元)", None),
            "m2_yoy": latest.get("货币和准货币(M2)-同比增长", None),
            "m1": latest.get("货币(M1)-数量(亿元)", None),
            "m1_yoy": latest.get("货币(M1)-同比增长", None),
            "m0": latest.get("流通中的现金(M0)-数量(亿元)", None),
            "m0_yoy": latest.get("流通中的现金(M0)-同比增长", None),
            "history": ak_result["latest_12"],
        }
    return _with_fallback("MoneySupply", ak.macro_china_money_supply, "money_supply", _map)


def fetch_shibor() -> dict:
    """Shibor 利率（日度）— AKShare 优先 + AnySearch 降级"""
    def _map(ak_result, alt=None):
        if alt is not None:
            # AnySearch: {date, on, 1w, 1m, 2w, 3m, 6m, 9m, 1y}
            return {
                "latest_date": alt.get("date", "N/A"),
                "overnight": alt.get("on", None),
                "week_1": alt.get("1w", None),
                "month_1": alt.get("1m", None),
                "month_3": alt.get("3m", None),
                "month_6": alt.get("6m", None),
                "year_1": alt.get("1y", None),
                "total_days": 1,
            }
        try:
            df = ak.macro_china_shibor_all()
            if df is None or df.empty:
                return {"error": "no data", "_source": "none"}
            latest_date = df["日期"].max()
            latest_row = df[df["日期"] == latest_date]
            return {
                "latest_date": str(latest_date),
                "overnight": float(latest_row["O/N-定价"].values[0])
                if "O/N-定价" in df.columns else None,
                "week_1": float(latest_row["1W-定价"].values[0]) if "1W-定价" in df.columns else None,
                "month_1": float(latest_row["1M-定价"].values[0]) if "1M-定价" in df.columns else None,
                "month_3": float(latest_row["3M-定价"].values[0]) if "3M-定价" in df.columns else None,
                "month_6": float(latest_row["6M-定价"].values[0]) if "6M-定价" in df.columns else None,
                "year_1": float(latest_row["1Y-定价"].values[0]) if "1Y-定价" in df.columns else None,
                "total_days": len(df),
            }
        except Exception as e:
            return {"error": str(e), "_source": "none"}
    # shibor 原逻辑用 try/except 而非 safe_fetch，故单独处理回退
    ak_d = _map(None)
    if "error" not in ak_d:
        ak_d["_source"] = "akshare"
        return ak_d
    # AKShare 失败 → AnySearch
    if _anysearch_helper is not None:
        try:
            alt = _anysearch_helper.macro_indicator("shibor")
            if "error" not in alt:
                d = _map(None, alt=alt)
                d["_source"] = "anysearch"
                return d
        except Exception:
            pass
    return {"error": ak_d.get("error", "akshare_failed"), "_source": "none"}


def fetch_social_financing() -> dict:
    """社会融资规模（月度）"""
    try:
        df = ak.macro_china_shrzgm()
        if df is None or df.empty:
            return {"error": "no data"}
        recent = df.head(12)
        latest = recent.iloc[0] if len(recent) > 0 else None
        return {
            "latest_month": str(latest.name) if latest is not None else "N/A",
            "total_social_financing": float(latest.iloc[0]) if latest is not None else None,
            "history_12m": recent.to_dict(orient="records"),
            "total_rows": len(df),
        }
    except Exception as e:
        return {"error": str(e)}


def fetch_lpr() -> dict:
    """LPR 利率"""
    try:
        df = ak.macro_china_lpr()
        if df is None or df.empty:
            return {"error": "no data"}
        latest = df.iloc[0] if len(df) > 0 else None
        return {
            "latest_date": str(latest.get("TRADE_DATE", "N/A")) if latest is not None else "N/A",
            "lpr_1y": float(latest.get("LPR1Y", 0)) if latest is not None else None,
            "lpr_5y": float(latest.get("LPR5Y", 0)) if latest is not None else None,
            "history": df.head(12).to_dict(orient="records"),
        }
    except Exception as e:
        return {"error": str(e)}


def fetch_forex_reserves() -> dict:
    """外汇储备"""
    try:
        df = ak.macro_china_fx_gold()
        if df is None or df.empty:
            return {"error": "no data"}
        recent = df.head(12)
        latest = recent.iloc[0] if len(recent) > 0 else None
        return {
            "latest_month": str(latest.get("月份", "N/A")) if latest is not None else "N/A",
            "forex_reserves": float(latest.get("外汇储备", 0)) if latest is not None else None,
            "gold_reserves": float(latest.get("黄金储备", 0)) if latest is not None else None,
            "history": recent.to_dict(orient="records"),
        }
    except Exception as e:
        return {"error": str(e)}


def calculate_macro_score(data: dict) -> dict:
    """基于宏观数据计算综合评分（-100 ~ +100）"""
    score = 0
    signals = []

    # PMI 信号（权重最高）
    pmi = data.get("pmi", {})
    pmi_mfg = pmi.get("pmi_manufacturing")
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

    # CPI 信号
    cpi = data.get("cpi", {})
    cpi_yoy = cpi.get("cpi_national_yoy")
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

    # M2 信号
    ms = data.get("money_supply", {})
    m2_yoy = ms.get("m2_yoy")
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

    # GDP 信号
    gdp = data.get("gdp", {})
    gdp_yoy = gdp.get("gdp_yoy")
    if gdp_yoy is not None:
        if gdp_yoy > 5.5:
            score += 10
            signals.append(f"高增长(GDP+{gdp_yoy}%)")
        elif gdp_yoy > 4.5:
            score += 5

    # Shibor 信号
    shibor = data.get("shibor", {})
    if shibor.get("overnight") is not None:
        if shibor["overnight"] < 1.5:
            score += 10
            signals.append("流动性充裕")
        elif shibor["overnight"] > 3:
            score -= 10
            signals.append("流动性紧张")

    return {
        "score": max(-100, min(100, score)),
        "signals": signals,
        "interpretation": "偏多" if score > 15 else ("偏空" if score < -15 else "中性"),
    }


def main():
    print(f"[{datetime.now()}] 宏观数据采集开始...")

    data = {}

    # 核心指标
    print("  采集 GDP...")
    data["gdp"] = fetch_gdp()

    print("  采集 CPI...")
    data["cpi"] = fetch_cpi()

    print("  采集 PMI...")
    data["pmi"] = fetch_pmi()

    print("  采集 货币供应量...")
    data["money_supply"] = fetch_money_supply()

    print("  采集 Shibor...")
    data["shibor"] = fetch_shibor()

    # 扩展指标（可能失败，不阻断）
    print("  采集 社融...")
    data["social_financing"] = fetch_social_financing()

    print("  采集 LPR...")
    data["lpr"] = fetch_lpr()

    print("  采集 外汇储备...")
    data["forex_reserves"] = fetch_forex_reserves()

    # 评分的
    score = calculate_macro_score(data)
    data["macro_score"] = score

    # 元数据
    sources = [v.get("_source", "akshare") for v in data.values()
               if isinstance(v, dict) and "_source" in v]
    data["_meta"] = {
        "updated_at": datetime.now().isoformat(),
        "source": "AKShare+AnySearch(降级)",
        "akshare_only": all(s == "akshare" for s in sources) if sources else False,
        "anysearch_fallback": [k for k, v in data.items()
                               if isinstance(v, dict) and v.get("_source") == "anysearch"],
        "indicators_ok": sum(1 for v in data.values() if isinstance(v, dict) and "error" not in v),
        "indicators_total": 8,
    }

    # 写入文件
    output_path = DATA_DIR / "macro_data.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    print(f"[{datetime.now()}] 完成 → {output_path}")
    print(f"  宏观评分: {score['score']} ({score['interpretation']})")
    print(f"  信号: {' | '.join(score['signals']) if score['signals'] else '无强烈信号'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
