"""宏观评分模块回归测试（离线，mock 网络）。

根因回归：archive 废弃后早报无评分 → Agent 编造 "None（macro_score 函数返回 None）"。
本测试确保 macro_score.build_macro_score / render_macro_score_block 永远返回有效值。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


from claw.feeds import macro_score as ms


# ── calculate_macro_score 算法 ──────────────────────────────
def test_score_pmi_expansion():
    r = ms.calculate_macro_score({"pmi_manufacturing": 51.0})
    assert r["score"] == 25
    assert r["interpretation"] == "偏多"
    assert r["available"] is True


def test_score_pmi_contraction():
    # 49.0 <= 49 → 衰退分支（-20）
    r = ms.calculate_macro_score({"pmi_manufacturing": 49.0})
    assert r["score"] == -20
    assert r["interpretation"] == "偏空"


def test_score_missing_all_yields_unavailable_not_none():
    r = ms.calculate_macro_score({})
    assert r["score"] == 0
    assert r["available"] is False  # 无指标，不算有效评分


def test_score_mixed_signals():
    r = ms.calculate_macro_score(
        {"pmi_manufacturing": 49.2, "cpi_yoy": 1.0, "m2_yoy": 8.5, "shibor_overnight": 1.353}
    )
    # -5(PMI收缩) +10(CPI温和) +5(M2中性) +10(流动性充裕) = +20
    assert r["score"] == 20
    assert r["interpretation"] == "偏多"


def test_score_clamped():
    r = ms.calculate_macro_score(
        {"pmi_manufacturing": 52, "cpi_yoy": 2, "m2_yoy": 15, "gdp_yoy": 8, "shibor_overnight": 0.5}
    )
    assert r["score"] <= 100


# ── render_macro_score_block 永远有值（根除 None 噪声）──
def test_render_block_with_data():
    block = ms.render_macro_score_block(extra={"pmi_manufacturing": 49.2, "cpi_yoy": 1.0})
    assert "宏观综合评分" in block
    assert "None" not in block
    assert "macro_score 函数" not in block
    assert "中性" in block or "偏多" in block or "偏空" in block


def test_render_block_no_data_qualitative_not_none(monkeypatch):
    # 离线场景：公开接口失败 + 无 extra → 走定性研判分支（不编造 None）
    monkeypatch.setattr(ms, "fetch_public_macro", dict)
    block = ms.render_macro_score_block(extra={})
    assert "宏观综合评分" in block
    assert "None" not in block
    assert "macro_score 函数" not in block
    assert "定性研判" in block  # 缺失时明确定性，不编造 None


def test_render_block_merges_extra():
    block = ms.render_macro_score_block(
        extra={"pmi_manufacturing": 49.2, "cpi_yoy": 1.0, "m2_yoy": 8.5, "shibor_overnight": 1.353}
    )
    assert "+20" in block  # 与手动算分一致


# ── build_macro_score 合并外部注入 ─────────────────────────
def test_build_merges_extra(monkeypatch):
    # 让 fetch_public_macro 返回空（模拟接口失败），仅靠 extra
    monkeypatch.setattr(ms, "fetch_public_macro", dict)
    r = ms.build_macro_score(extra={"pmi_manufacturing": 51.0})
    assert r["score"] == 25
    assert r["available"] is True


def test_build_handles_fetch_exception(monkeypatch):
    def boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(ms, "fetch_public_macro", boom)
    # 即便抓取异常，extra 仍应算分，不崩
    r = ms.build_macro_score(extra={"cpi_yoy": 2.0})
    assert r["score"] == 10
    assert "None" not in ms.render_macro_score_block(extra={"cpi_yoy": 2.0})
