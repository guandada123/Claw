"""股债相关性链路测试（2026-10-09 新增）

覆盖 disc-20261009-06 修复：cron_monitor.py 的 10Y 中债收益率原为
无条件 `return []`（恒空且不报错），消费方无法区分「源没接上」与「当日无数据」。
本次接入真实数据源（中国债券信息网）+ 沪深300 改腾讯源，并以
(series, error) 二元组显式区分「空」与「失败」。

测试要点：
- HTML 解析（表头定位 / 曲线过滤 / 缺值跳过）
- 空值与失败可区分（(None, reason) vs (list, None)）
- 日变化 change 差分计算（单位 bp，首日无 change 不入列）
- check_stock_bond_correlation 三态分流
- 沪深300 腾讯 K 线格式解析
"""

from __future__ import annotations

import json

import cron_monitor as cm
import pytest


class _Resp:
    """最小可用的 urlopen 上下文管理器替身。"""

    def __init__(self, body: str, status: int = 200):
        self._body = body.encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *_exc) -> bool:
        return False


# 与 chinabond historyQuery 真实返回同构的 HTML（含一个前导表格 + 数据表格）
_HTML_OK = """
<table><tr><td>曲线名称</td><td>日期</td><td>10年</td></tr></table>
<table>
<tr><td>曲线名称</td><td>日期</td><td>3月</td><td>6月</td><td>1年</td><td>3年</td><td>5年</td><td>7年</td><td>10年</td><td>30年</td></tr>
<tr><td>中债商业银行普通债收益率曲线(AAA)</td><td>2026-10-09</td><td>1.41</td><td>1.44</td><td>1.47</td><td>1.54</td><td>1.57</td><td>1.70</td><td>1.85</td><td>2.28</td></tr>
<tr><td>中债国债收益率曲线</td><td>2026-10-07</td><td>1.17</td><td>1.19</td><td>1.21</td><td>1.28</td><td>1.40</td><td>1.51</td><td>1.70</td><td>2.09</td></tr>
<tr><td>中债国债收益率曲线</td><td>2026-10-08</td><td>1.17</td><td>1.19</td><td>1.21</td><td>1.28</td><td>1.40</td><td>1.51</td><td>1.68</td><td>2.09</td></tr>
<tr><td>中债国债收益率曲线</td><td>2026-10-09</td><td>1.17</td><td>1.19</td><td>1.21</td><td>1.28</td><td>1.40</td><td>1.51</td><td>1.6864</td><td>2.09</td></tr>
<tr><td>中债国债收益率曲线</td><td>2026-10-10</td><td>1.17</td><td>1.19</td><td>1.21</td><td>1.28</td><td>1.40</td><td>1.51</td><td></td><td>2.09</td></tr>
</table>
"""


# ── HTML 解析 ────────────────────────────────────────────────────────────


def test_parse_filters_target_curve_and_skips_missing_value():
    """只取『中债国债收益率曲线』，且 10 年缺值的日期被跳过。"""
    rows = cm._parse_bond_yield_html(_HTML_OK)
    # AAA 曲线被过滤；10-10 的 10 年为空 → 跳过；剩余按日期升序
    assert [r["date"] for r in rows] == ["2026-10-07", "2026-10-08", "2026-10-09"]
    assert rows[-1]["yield"] == pytest.approx(1.6864)


def test_parse_returns_empty_without_header():
    """表头缺失（页面改版）时返回空列表，不抛异常。"""
    assert cm._parse_bond_yield_html("<table><tr><td>x</td></tr></table>") == []
    assert cm._parse_bond_yield_html("") == []


# ── 取数：空值与失败可区分 ────────────────────────────────────────────────


def test_fetch_failure_returns_none_with_reason(monkeypatch):
    """网络异常 → (None, 原因)，而不是静默返回空列表。"""

    def _boom(*_a, **_k):
        raise OSError("connection refused")

    monkeypatch.setattr(cm.urllib.request, "urlopen", _boom)
    series, err = cm.fetch_bond_10y_yield()
    assert series is None
    assert err and "不可用" in err


def test_fetch_http_error_returns_none_with_reason(monkeypatch):
    """非 200 → (None, 原因)。"""
    monkeypatch.setattr(cm.urllib.request, "urlopen", lambda *_a, **_k: _Resp("", status=503))
    series, err = cm.fetch_bond_10y_yield()
    assert series is None
    assert err and "503" in err


def test_fetch_success_computes_change_in_bp(monkeypatch):
    """成功路径：change = 收益率日差 × 100（bp），首日无 change 不入列。"""
    monkeypatch.setattr(cm.urllib.request, "urlopen", lambda *_a, **_k: _Resp(_HTML_OK))
    series, err = cm.fetch_bond_10y_yield()
    assert err is None
    assert [r["date"] for r in series] == ["2026-10-08", "2026-10-09"]
    # 1.68 - 1.70 = -0.02 → -2.0 bp
    assert series[0]["change"] == pytest.approx(-2.0)
    # 1.6864 - 1.68 = 0.0064 → 0.64 bp
    assert series[1]["change"] == pytest.approx(0.64)


# ── 沪深300（腾讯源）─────────────────────────────────────────────────────


def test_fetch_csi300_parses_tencent_format(monkeypatch):
    """腾讯 K 线项 [日期, 开, 收, 高, 低, 量] → 日收益率序列。"""
    payload = json.dumps(
        {
            "code": 0,
            "data": {
                "sh000300": {
                    "day": [
                        ["2026-10-08", "4000", "4010", "4020", "3990", "1"],
                        ["2026-10-09", "4010", "4030", "4040", "4005", "1"],
                    ]
                }
            },
        }
    )
    monkeypatch.setattr(cm.urllib.request, "urlopen", lambda *_a, **_k: _Resp(payload))
    out = cm.fetch_csi300_daily()
    assert [r["date"] for r in out] == ["2026-10-08", "2026-10-09"]
    assert out[0]["return"] == 0.0
    assert out[1]["return"] == pytest.approx(round((4030 - 4010) / 4010, 6))


def test_fetch_csi300_empty_payload_returns_empty(monkeypatch):
    """腾讯接口未返回 K 线 → 空列表（不抛异常）。"""
    payload = json.dumps({"code": 0, "data": {"sh000300": {"day": []}}})
    monkeypatch.setattr(cm.urllib.request, "urlopen", lambda *_a, **_k: _Resp(payload))
    assert cm.fetch_csi300_daily() == []


# ── 三态分流 ─────────────────────────────────────────────────────────────


def _csi300_ok(n: int = 300) -> list[dict]:
    return [{"date": f"2026-{(i % 12) + 1:02d}-01", "return": 0.001 * ((-1) ** i)} for i in range(n)]


def test_correlation_reports_source_unavailable(monkeypatch):
    """源不可用 → status=bond_source_unavailable 且 source_ok=False（显式报错）。"""
    monkeypatch.setattr(cm, "fetch_csi300_daily", lambda: _csi300_ok())
    monkeypatch.setattr(cm, "fetch_bond_10y_yield", lambda: (None, "中债收益率源不可用 — 测试"))
    r = cm.check_stock_bond_correlation()
    assert r["status"] == "bond_source_unavailable"
    assert r["source_ok"] is False
    assert "不可用" in r["error"]


def test_correlation_reports_insufficient_bond_data(monkeypatch):
    """源可用但样本不足 → status=insufficient_bond_data 且 source_ok=True（与源故障区分）。"""
    monkeypatch.setattr(cm, "fetch_csi300_daily", lambda: _csi300_ok())
    short = [{"date": f"b{i}", "yield": 1.7, "change": 0.01} for i in range(10)]
    monkeypatch.setattr(cm, "fetch_bond_10y_yield", lambda: (short, None))
    r = cm.check_stock_bond_correlation()
    assert r["status"] == "insufficient_bond_data"
    assert r["source_ok"] is True
    assert r["bond_points"] == 10


def test_correlation_normal_path_exposes_source_ok(monkeypatch):
    """正常路径：返回 source_ok=True 与 bond_points，供消费方判定数据可信度。"""
    monkeypatch.setattr(cm, "fetch_csi300_daily", lambda: _csi300_ok())
    bond = [{"date": f"b{i}", "yield": 1.7, "change": 0.01 * ((-1) ** i)} for i in range(300)]
    monkeypatch.setattr(cm, "fetch_bond_10y_yield", lambda: (bond, None))
    r = cm.check_stock_bond_correlation()
    assert r["source_ok"] is True
    assert r["bond_points"] == 300
    assert r["status"] in ("normal", "caution", "warning")
    assert "corr_126d" in r and "corr_252d" in r
