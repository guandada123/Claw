"""test_price_sanity.py — 价格防错校验器回归测试（P0 防御）。

覆盖：
  - G1 实时快照偏差 >30% → FAIL
  - G2 52周区间越界 → FAIL
  - G3 MA20 偏离 >60% → FAIL
  - 真实价通过 → ok=True
  - 缺失实时价（降级标记）→ PASS_WITH_WARN 不误杀
  - advisor_rules.check_entry 集成 sanity（错误价 blocked + 用可信价）

网络依赖通过 monkeypatch 隔离，离线可跑，不依赖盘中时段。
"""
from __future__ import annotations

# 以脚本所在目录加入 sys.path（脚本在 Claw/scripts/）
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import price_sanity as ps  # noqa: E402

# ---- 受控数据源（替代网络）----
FAKE_SNAPSHOT = {"price": 77.75}          # 长电科技近似实时价
FAKE_META = {"low_52w": 33.59, "high_52w": 106.64, "ma20": 79.21}


@pytest.fixture
def patch_net(monkeypatch):
    """隔离全部网络取数，喂入受控数据。"""
    monkeypatch.setattr(ps, "_gtimg_snapshot", lambda c: FAKE_SNAPSHOT)
    monkeypatch.setattr(ps, "_gtimg_52w_and_ma20", lambda c: FAKE_META)


def test_real_price_passes(patch_net):
    """真实价 77.75 → 通过。"""
    r = ps.check("600584", 77.75)
    assert r["ok"] is True
    assert r["action"] == "PASS"
    assert r["verified_price"] == 77.75
    assert r["fail_reasons"] == []


def test_g1_deviation_blocks(patch_net):
    """偏差 >30% → G1 FAIL。"""
    r = ps.check("600584", 40.0)  # 与 77.75 偏差 ~49%
    assert r["ok"] is False
    assert any(f.startswith("G1") for f in r["fail_reasons"])
    assert r["verified_price"] == 77.75  # 自动改用可信价


def test_g2_outside_52w_blocks(patch_net):
    """超出 52周区间 → G2 FAIL。"""
    r = ps.check("600584", 5.0)  # 远低于 low_52w 33.59
    assert r["ok"] is False
    assert any(f.startswith("G2") for f in r["fail_reasons"])


def test_g3_ma20_deviation_blocks(patch_net):
    """偏离 MA20 >60% → G3 FAIL。"""
    r = ps.check("600584", 20.0)  # 与 MA20 79.21 偏离 ~75%
    assert r["ok"] is False
    assert any(f.startswith("G3") for f in r["fail_reasons"])


def test_missing_snapshot_passes_with_warn(patch_net):
    """实时价缺失（降级标记）→ 放行但标注，不误杀。"""
    monkeypatch_missing = FAKE_SNAPSHOT.copy()
    monkeypatch_missing["price"] = None

    # 用 monkeypatch 覆盖 fixture 内的 snapshot
    import price_sanity as _ps
    _ps._gtimg_snapshot = lambda c: {"price": None}
    r = _ps.check("600584", 77.75)
    # 无实时价 → 仅 G1 缺失警告，ok 应为 True（不误杀）
    assert r["ok"] is True
    assert r["action"] == "PASS_WITH_WARN"
    assert any("腾讯实时价缺失" in f for f in r["fail_reasons"])


def test_prefix_normalization():
    """代码前缀补齐逻辑 + 美股自动识别。"""
    assert ps._prefix("600584") == "sh600584"
    assert ps._prefix("002185") == "sz002185"
    assert ps._prefix("sh600584") == "sh600584"
    # 纯字母 ticker（美股）→ 原样返回，由 _detect_market 识别
    assert ps._prefix("AAPL") == "aapl"
    assert ps._detect_market("AAPL") == "us"
    assert ps._detect_market("600584") == "cn"


def test_advisor_rules_integration_blocked_on_bad_price(monkeypatch):
    """advisor_rules.check_entry 传入错误价 → blocked + sanity.ok=False。"""
    sys.path.insert(0, str(SCRIPTS))
    import advisor_rules as ar

    # 隔离全部网络：实时价=77.75，52周/MA20 受控
    monkeypatch.setattr(ps, "_gtimg_snapshot", lambda c: {"price": 77.75})
    monkeypatch.setattr(ps, "_gtimg_52w_and_ma20",
                        lambda c: {"low_52w": 33.59, "high_52w": 106.64, "ma20": 79.21})
    monkeypatch.setattr(ar.AdvisorRules, "_get_live_price",
                        lambda self, c: {"price": 77.75, "change_pct": -1.0})

    res = ar.AdvisorRules().check_entry("600584", price=7.77)  # 8/6 同类错误价
    assert res["blocked"] is True
    assert res["price_sanity"]["ok"] is False
    # 不输出离谱买区：价格已被可信价覆盖
    assert res["price_used"] == 77.75


def test_advisor_rules_integration_pass_on_real_price(monkeypatch):
    """advisor_rules.check_entry 真实价 → 正常输出买区。"""
    sys.path.insert(0, str(SCRIPTS))
    import advisor_rules as ar

    monkeypatch.setattr(ps, "_gtimg_snapshot", lambda c: {"price": 77.75})
    monkeypatch.setattr(ps, "_gtimg_52w_and_ma20",
                        lambda c: {"low_52w": 33.59, "high_52w": 106.64, "ma20": 79.21})
    monkeypatch.setattr(ar.AdvisorRules, "_get_live_price",
                        lambda self, c: {"price": 77.75, "change_pct": -1.0})
    # 隔离规则I(行业集中度): 该测试聚焦价格sanity，真实持仓(半导体100%)会误触发block
    monkeypatch.setattr(ar.AdvisorRules, "check_sector_block",
                        lambda self, *a, **k: None)

    res = ar.AdvisorRules().check_entry("600584", price=77.75)
    assert res["blocked"] is False
    # 外部价与实时价一致（偏差≤30%）→ 无需 sanity 强校验，sanity 保持 None（设计如此）
    assert res["price_sanity"] is None
    assert "¥" in res["suggested_buy_zone"]


def test_advisor_rules_integration_blocks_on_deviation_over_30pct(monkeypatch):
    """外部价与实时偏离>30% → 必拦截（G1 防御，不误放）。"""
    sys.path.insert(0, str(SCRIPTS))
    import advisor_rules as ar

    # 实时价=50，外部价=77.75（偏差>30%）→ 触发 sanity 强校验 → blocked
    monkeypatch.setattr(ps, "_gtimg_snapshot", lambda c: {"price": 50.0})
    monkeypatch.setattr(ps, "_gtimg_52w_and_ma20",
                        lambda c: {"low_52w": 33.59, "high_52w": 106.64, "ma20": 79.21})
    monkeypatch.setattr(ar.AdvisorRules, "_get_live_price",
                        lambda self, c: {"price": 50.0, "change_pct": 0.0})

    res = ar.AdvisorRules().check_entry("600584", price=77.75)
    assert res["blocked"] is True
    assert res["price_sanity"]["ok"] is False
    # 决策价回退到可信实时价，绝不输出偏离>30% 的外部价买区
    assert res["price_used"] == 50.0


# ---- 美股 sanity（market=us，Yahoo 数据源）----
def test_us_stock_real_price_passes(monkeypatch):
    """美股真实价 → 通过（Yahoo 实时比对）。"""
    monkeypatch.setattr(ps, "_yahoo_snapshot",
                        lambda t: {"price": 312.41, "low_52w": 216.58, "high_52w": 344.57})
    r = ps.check("AAPL", 312.41, market="us")
    assert r["market"] == "us"
    assert r["ok"] is True
    assert r["action"] == "PASS"


def test_us_stock_deviation_blocks(monkeypatch):
    """美股错误价（偏离实时>30% 且超52周）→ 拦截。"""
    monkeypatch.setattr(ps, "_yahoo_snapshot",
                        lambda t: {"price": 312.41, "low_52w": 216.58, "high_52w": 344.57})
    r = ps.check("AAPL", 200.0, market="us")
    assert r["ok"] is False
    assert any(f.startswith("G1") for f in r["fail_reasons"])
    assert any(f.startswith("G2") for f in r["fail_reasons"])
    assert r["verified_price"] == 312.41


def test_market_auto_detect():
    """纯字母 ticker 自动识别为美股。"""
    assert ps._detect_market("AAPL") == "us"
    assert ps._detect_market("600584") == "cn"
