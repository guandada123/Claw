"""test_alpha_factors.py — Alpha101 因子引擎纯函数测试（P2-8）

覆盖: 算子原语(delay/delta/ts_rank/ts_argmax/decay_linear) / 因子输出有效性 /
gap 日收益作废 / 无数据降级 / 注册表完整性 / IC 计算骨架。
"""


import alpha_factors as af
import numpy as np
import pandas as pd
import pytest
from alpha_eval import winsorize_mad


def _bars(n: int = 120, seed: int = 7, gaps: list[int] | None = None) -> pd.DataFrame:
    """构造合成日线: 趋势 + 噪声, 可选 gap 日索引。"""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2025-01-02", periods=n)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, n)))
    open_ = close * (1 + rng.normal(0, 0.005, n))
    high = np.maximum(open_, close) * (1 + abs(rng.normal(0, 0.01, n)))
    low = np.minimum(open_, close) * (1 - abs(rng.normal(0, 0.01, n)))
    vol = rng.integers(1_000_000, 5_000_000, n).astype(float)
    amt = close * vol * 10
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close,
         "volume": vol, "amount": amt, "pre_close": pd.Series(close).shift(1),
         "pct": 0.0, "gap": 0},
        index=idx,
    )
    df["gap"] = 0
    if gaps:
        for g in gaps:
            df.loc[df.index[g], "gap"] = 1
    df["pct"] = df["close"].pct_change().fillna(0) * 100
    return df


# ── 算子原语 ──


def test_delay_delta():
    s = pd.Series([1.0, 2.0, 3.0, 4.0])
    assert af.delay(s, 1).iloc[-1] == 3.0
    assert af.delta(s, 1).iloc[-1] == 1.0


def test_ts_rank_middle_value():
    s = pd.Series([1.0, 5.0, 3.0])
    r = af.ts_rank(s, 3)
    assert r.iloc[-1] == pytest.approx(2 / 3)  # 3在[1,5,3]中 ≤3 的有2个


def test_ts_argmax_newest_is_zero():
    s = pd.Series([1.0, 2.0, 9.0, 3.0])
    r = af.ts_argmax(s, 4)
    assert r.iloc[-1] == 1  # 最大值9在倒数第2位置(索引1)


def test_decay_linear_weights():
    s = pd.Series([1.0, 1.0, 3.0])
    r = af.decay_linear(s, 3)
    # 权重 1,2,3 → (1*1+2*1+3*3)/6 = 12/6 = 2
    assert r.iloc[-1] == pytest.approx(2.0)


def test_sign():
    s = pd.Series([-2.0, 0.0, 3.0])
    assert af.sign(s).tolist() == [-1.0, 0.0, 1.0]


def test_vwap_and_adv():
    df = _bars()
    v = af.vwap(df)
    assert v.notna().all()
    a = af.adv_n(df, 5)
    assert a.iloc[-1] > 0


# ── 因子输出有效性 ──


@pytest.mark.parametrize("name", ["alpha1", "alpha3", "alpha12", "alpha101", "alpha8"])
def test_factor_output_finite(name):
    df = _bars()
    s = af.FACTORS[name](df)
    assert isinstance(s, pd.Series)
    vals = s.dropna()
    assert len(vals) > 0
    assert np.isfinite(vals).all()


def test_all_factors_registered():
    assert len(af.FACTORS) >= 20
    for name in ["alpha1", "alpha101"]:
        assert name in af.FACTORS


def test_gap_day_return_is_nan():
    df = _bars(gaps=[50])
    r = af.returns(df)
    assert np.isnan(r.iloc[50])


# ── compute_factor 集成 ──


def test_compute_factor_unknown_name(monkeypatch):
    from unittest.mock import MagicMock

    monkeypatch.setattr(af, "cache_bars", MagicMock(return_value=_bars()))
    with pytest.raises(KeyError):
        af.compute_factor("600584", "alpha_unknown")


def test_compute_factor_empty_data(monkeypatch):
    from unittest.mock import MagicMock

    monkeypatch.setattr(af, "cache_bars", MagicMock(return_value=None))
    s = af.compute_factor("000000", "alpha3")
    assert s.empty


# ── 评估骨架 ──


def test_winsorize_mad_clips_outliers():
    s = pd.Series([1.0] * 100 + [100.0])
    out = winsorize_mad(s, n=3.0)
    assert out.max() < 50
    assert out.iloc[100] < out.iloc[99] * 3 + 1  # 被收敛


def test_eval_factor_empty():
    from alpha_eval import eval_factor

    m = eval_factor(pd.Series(dtype=float))
    assert m["ok"] is False


def test_eval_factor_good_ic():
    from alpha_eval import eval_factor

    ic = pd.Series([0.05] * 50)  # 恒正低波动 → ICIR 高
    m = eval_factor(ic)
    assert bool(m["ok"]) is True
    assert m["ic"] == pytest.approx(0.05, abs=1e-6)
