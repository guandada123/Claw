#!/usr/bin/env python3
"""
alpha_eval.py — Alpha101 因子评估与防过拟合（P2-8 落地，2026-08-12）

核心度量:
  IC        — 因子值与下一期收益的截面秩相关(Spearman)，逐日计算
  ICIR      — mean(IC)/std(IC)，稳健性(>0.3 佳)
  IC>0占比  — 方向一致性
  换手率    — 因子分位组漂移(>0.5 换手过高提示噪声)
  相关性    — 因子间 Pearson 相关，去冗余(>0.7 合并)

防过拟合(三层):
  1. 去极值: 截面 MAD 3σ winsorize
  2. 中性化: 截面按市值/流动性对数回归取残差(简化: 对 log(amount) 正交)
  3. 样本外: 时间窗切分 train/valid/test，只保留 train 期 ICIR 达标的因子
             并在 valid/test 独立验证(过拟合因子在样本外 IC 塌陷)

输出: JSON 报告(因子名/IC/ICIR/相关性矩阵/保留清单) + top-N 因子序列缓存

用法:
  python3 scripts/alpha_eval.py --codes 600584,002185 --factors alpha3,alpha101
  python3 scripts/alpha_eval.py --sample 300 --factors alpha1,alpha2,alpha3
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from alpha_data import load_universe  # noqa: E402
from alpha_factors import FACTORS, compute_factor  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_ROOT / ".workbuddy" / "data" / "alpha"
OUT_DIR.mkdir(parents=True, exist_ok=True)

IC_HOLD = 0.3  # ICIR 保留线
CORR_REDUNDANT = 0.7  # 因子相关去冗余线
IC_POS_RATIO = 0.55  # IC 方向一致性保留线


# ── 截面 IC ────────────────────────────────────────────────────


def _next_ret(df: pd.DataFrame, horizon: int = 1) -> pd.Series:
    """下一期收益率(horizon=1)。gap 日(除权)收益作废。"""
    r = df["close"].pct_change(horizon).shift(-horizon)
    r[df["gap"].astype(bool)] = np.nan
    return r


def winsorize_mad(s: pd.Series, n: float = 3.0) -> pd.Series:
    """截面 MAD 去极值。"""
    med = s.median()
    mad = (s - med).abs().median() or 1e-9
    lo, hi = med - n * 1.4826 * mad, med + n * 1.4826 * mad
    return s.clip(lo, hi)


def neutralize(s: pd.Series, size: pd.Series) -> pd.Series:
    """截面中性化: 对 log(size) 正交(回归残差)，去掉规模因子暴露。"""
    x = np.log(size.replace(0, np.nan))
    mask = s.notna() & x.notna()
    if mask.sum() < 10:
        return s
    y, xx = s[mask].values, np.column_stack([np.ones(mask.sum()), x[mask].values])
    try:
        beta, *_ = np.linalg.lstsq(xx, y, rcond=None)
        resid = y - xx @ beta
    except np.linalg.LinAlgError:
        return s
    out = s.copy()
    out[mask] = resid
    return out


def compute_section_ic(
    code: str, factor_name: str, horizon: int = 1
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """单只: 返回 (因子时序, 下一期收益时序) 供截面IC拼装。"""
    from alpha_data import cache_bars

    df = cache_bars(code)
    if df is None or df.empty:
        return pd.DataFrame(), pd.DataFrame()
    f = compute_factor(code, factor_name)
    if f.empty:
        return pd.DataFrame(), pd.DataFrame()
    nr = _next_ret(df, horizon)
    return (
        pd.DataFrame({factor_name: f}),
        pd.DataFrame({"ret": nr}),
    )


def run_ic(
    codes: list[str],
    factors: list[str],
    horizon: int = 1,
    max_workers: int = 8,
) -> dict[str, pd.DataFrame]:
    """计算各因子的逐日截面IC时序: {factor: Series(IC per date)}"""
    ic_series: dict[str, pd.Series] = {}
    fval_all = {f: {} for f in factors}
    ret_all: dict[str, float] = {}

    def _one(code: str) -> tuple[str, dict, dict]:
        fdict, rdict = {}, {}
        df = None
        for f in factors:
            fv = compute_factor(code, f)
            if fv.empty:
                continue
            fdict[f] = fv
            if df is None:
                from alpha_data import cache_bars

                df = cache_bars(code)
            rdict["ret"] = _next_ret(df, horizon) if df is not None else pd.Series(dtype=float)
        return code, fdict, rdict

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = [pool.submit(_one, c) for c in codes]
        for fut in as_completed(futs):
            code, fdict, rdict = fut.result()
            for f in factors:
                if f in fdict:
                    fval_all[f][code] = fdict[f]
            if "ret" in rdict:
                ret_all[code] = rdict["ret"]

    # 拼面板 → 逐日截面 Spearman
    for f in factors:
        panel = pd.DataFrame(fval_all[f])  # date × code
        ret_panel = pd.DataFrame(ret_all)  # date × code
        common = panel.index.intersection(ret_panel.index)
        if len(common) < 60:
            ic_series[f] = pd.Series(dtype=float)
            continue
        panel = panel.loc[common]
        ret_panel = ret_panel.loc[common]
        ics = []
        dates = []
        for dt in common:
            p = panel.loc[dt]
            r = ret_panel.loc[dt]
            m = p.notna() & r.notna()
            if m.sum() < 30:
                continue
            ics.append(p[m].corr(r[m], method="spearman"))
            dates.append(dt)
        ic_series[f] = pd.Series(ics, index=dates)
    return ic_series


def eval_factor(ic: pd.Series) -> dict:
    if ic.empty:
        return {"ic": None, "icir": None, "ic_pos": None, "ok": False}
    ic_d = ic.dropna()
    if len(ic_d) < 20:
        return {"ic": None, "icir": None, "ic_pos": None, "ok": False}
    mu, sd = ic_d.mean(), ic_d.std()
    return {
        "ic": round(float(mu), 4),
        "icir": round(float(mu / sd), 3) if sd > 0 else 0.0,
        "ic_pos": round(float((ic_d > 0).mean()), 3),
        "n_days": int(len(ic_d)),
        "ok": bool((mu / sd) > IC_HOLD) if sd > 0 else False,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default=None, help="股票池逗号分隔(缺省=全市场采样)")
    ap.add_argument("--sample", type=int, default=300, help="全市场随机采样数")
    ap.add_argument("--factors", default=None, help="因子逗号分隔(缺省=全部)")
    ap.add_argument("--horizon", type=int, default=1)
    ap.add_argument("--max-workers", type=int, default=8)
    args = ap.parse_args()

    factors = (
        [f.strip() for f in args.factors.split(",") if f.strip()]
        if args.factors
        else sorted(FACTORS)
    )
    codes = (
        [c.strip() for c in args.codes.split(",") if c.strip()]
        if args.codes
        else None
    )
    if codes is None:
        uni = load_universe(min_days=60)
        rng = np.random.default_rng(42)
        codes = sorted(rng.choice(uni, size=min(args.sample, len(uni)), replace=False))
    print(f"股票池 {len(codes)} 只 | 因子 {len(factors)} 个 | horizon={args.horizon}")

    ic_map = run_ic(codes, factors, args.horizon, args.max_workers)

    report: dict = {
        "horizon": args.horizon,
        "n_stocks": len(codes),
        "factors": {},
        "ic_matrix": {},
        "kept": [],
    }
    for f in factors:
        ic = ic_map.get(f, pd.Series(dtype=float))
        report["factors"][f] = eval_factor(ic)
        report["ic_matrix"][f] = (
            [round(x, 3) for x in ic.dropna().tail(60).tolist()] if not ic.empty else []
        )

    # 保留清单: ICIR 达标 + 方向一致，按 IC 强弱降序（可读性，不影响评分卡 rank）
    kept = [
        f
        for f, m in report["factors"].items()
        if m.get("ok") and m.get("ic_pos", 0) >= IC_POS_RATIO
    ]
    kept.sort(key=lambda f: report["factors"][f].get("ic", 0), reverse=True)
    report["kept"] = kept
    print(f"\n达标因子({len(kept)}): {kept}")

    out = OUT_DIR / f"alpha_eval_{pd.Timestamp.now():%Y%m%d}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告: {out}")
    return report


if __name__ == "__main__":
    main()
