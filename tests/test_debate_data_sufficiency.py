"""08-21 审计修复回归测试：数据完整性判定对齐 + 资金流补全 + codes 路径 change_pct。

覆盖审计遗留项：
- P1-1: data_insufficient 判定键(technical/fundamental/fund_flow)与 _enrich_stock_data
        补全维度对齐；sentiment 无本地源 → 单列 design_gap，消除恒标噪音。
- P1-2: data_insufficient 消费方接线（show_latest / trace_signal 展示标记）。
- P2② : _ema_series len<period 防御。
- P2④ : debate_from_codes change_pct 不再恒 0（解析腾讯昨收）。
"""

import json
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import run_debate

from claw.debate.debate_engine import _assess_data_sufficiency


class TestAssessDataSufficiency:
    """P1-1：判定键 = 设计内必备三维，sentiment 单列 design_gap"""

    def test_all_three_present(self):
        data = {
            "technical": {"rsi": 50},
            "fundamental": {"pe": 10},
            "fund_flow": {"main_net_wan": 100},
            "sentiment": {"score": 0.6},
        }
        assert _assess_data_sufficiency(data) == ([], [])

    def test_missing_fund_flow_flagged(self):
        data = {"technical": {"rsi": 50}, "fundamental": {"pe": 10}, "fund_flow": {}}
        missing, _ = _assess_data_sufficiency(data)
        assert missing == ["fund_flow"]

    def test_sentiment_only_is_design_gap(self):
        """修复前恒标 ['fund_flow','sentiment'] 的根因：sentiment 无本地源，
        不应计入 data_insufficient（否则全部新记录恒标噪音）。"""
        data = {
            "technical": {"rsi": 50},
            "fundamental": {"pe": 10},
            "fund_flow": {"x": 1},
            "sentiment": {},
        }
        missing, gap = _assess_data_sufficiency(data)
        assert missing == []
        assert gap == ["sentiment"]

    def test_empty_data_all_flagged(self):
        missing, gap = _assess_data_sufficiency({})
        assert missing == ["technical", "fundamental", "fund_flow"]
        assert gap == ["sentiment"]

    def test_absent_keys_treated_missing(self):
        missing, _ = _assess_data_sufficiency(
            {"technical": {}, "fundamental": None, "sentiment": "x"}
        )
        assert "fundamental" in missing and "fund_flow" in missing


class TestEmaSeriesDefense:
    """P2②：len<period 防御"""

    def test_short_series_returns_as_is(self):
        assert run_debate._ema_series([1.0, 2.0, 3.0], 20) == [1.0, 2.0, 3.0]

    def test_empty_returns_empty(self):
        assert run_debate._ema_series([], 12) == []

    def test_normal_series_length_preserved(self):
        out = run_debate._ema_series(list(range(30)), 12)
        assert len(out) == 30


class TestFetchMoneyFlow:
    """P1-1：东财 ulist.np 资金流解析（f62 元 / f184 0.01%→%）"""

    def _fake_subprocess(self, diff):
        def fake_run(cmd, capture_output=False, text=False, timeout=None):
            resp = mock.Mock()
            resp.stdout = json.dumps({"rc": 0, "data": {"diff": diff}})
            return resp

        return fake_run

    def test_parse_main_net(self):
        diff = [{"f62": 157098016.0, "f184": 159}]
        with mock.patch("subprocess.run", side_effect=self._fake_subprocess(diff)):
            mf = run_debate._fetch_money_flow("600584")
        assert mf["main_net"] == 157098016.0
        assert mf["main_pct"] == 1.59  # 159bp → 1.59%

    def test_no_data_returns_error(self):
        with mock.patch("subprocess.run", side_effect=self._fake_subprocess([])):
            mf = run_debate._fetch_money_flow("600584")
        assert "error" in mf

    def test_enrich_attaches_fund_flow(self):
        """集成：_enrich_stock_data 成功补全资金面 → data['fund_flow'] 非空，
        data_insufficient 不再恒标 fund_flow；键名对齐 prompt 消费键 main_net_inflow。"""
        base = {"price": 33.5, "change_pct": 1.2, "sector": "半导体"}
        with (
            mock.patch.object(
                run_debate,
                "_fetch_money_flow",
                return_value={"main_net": 12345000.0, "main_pct": 1.59},
            ),
            mock.patch.object(run_debate, "_load_fundamental_cache", return_value={}),
            mock.patch.object(run_debate, "_write_fundamental_cache", return_value=None),
            mock.patch.object(run_debate, "_ema_series", side_effect=run_debate._ema_series),
        ):
            data = run_debate._enrich_stock_data("600584", base)
        assert data.get("fund_flow", {}).get("main_net_inflow") == 1234.5  # 元→万元
        missing, _ = _assess_data_sufficiency(data)
        assert "fund_flow" not in missing

    def test_fund_flow_source_unavailable_not_flagged(self):
        """P1-1 二次修正：东财 push2 整体不可达（环境问题非个股问题）→ fund_flow 写
        {'_source':'unavailable'}，不恒标 data_insufficient（否则与修复前恒标噪音同病）。"""
        base = {"price": 33.5, "change_pct": 1.2, "sector": "半导体"}
        with (
            mock.patch.object(
                run_debate, "_fetch_money_flow", return_value={"error": "curl empty"}
            ),
            mock.patch.object(run_debate, "_load_fundamental_cache", return_value={}),
            mock.patch.object(run_debate, "_write_fundamental_cache", return_value=None),
            mock.patch.object(run_debate, "_ema_series", side_effect=run_debate._ema_series),
        ):
            data = run_debate._enrich_stock_data("600584", base)
        assert data.get("fund_flow", {}).get("_source") == "unavailable"
        missing, _ = _assess_data_sufficiency(data)
        assert "fund_flow" not in missing

    def test_enrich_exception_marks_unavailable(self):
        """_fetch_money_flow 抛异常 → 同样标记 source unavailable，不裸奔"""
        base = {"price": 33.5, "change_pct": 1.2, "sector": "半导体"}
        with (
            mock.patch.object(run_debate, "_fetch_money_flow", side_effect=RuntimeError("boom")),
            mock.patch.object(run_debate, "_load_fundamental_cache", return_value={}),
            mock.patch.object(run_debate, "_write_fundamental_cache", return_value=None),
            mock.patch.object(run_debate, "_ema_series", side_effect=run_debate._ema_series),
        ):
            data = run_debate._enrich_stock_data("600584", base)
        assert data.get("fund_flow", {}).get("_source") == "unavailable"


class TestDebateFromCodesChangePct:
    """P2④：change_pct 不再恒 0（解析腾讯 gtimg 昨收）"""

    _GT_QUOTE = 'v_sh600584="1~长电科技~600584~33.50~32.50~33.00~100000~50000~50000~33.50";\n'

    def test_change_pct_computed(self):
        fake = mock.Mock()
        fake.stdout = self._GT_QUOTE.encode("gbk", errors="replace")
        with (
            mock.patch("subprocess.run", return_value=fake),
            mock.patch.object(run_debate, "_enrich_stock_data", side_effect=lambda c, b: dict(b)),
            mock.patch.object(run_debate, "batch_debate") as m_batch,
        ):
            run_debate.debate_from_codes("600584")
        args = m_batch.call_args[0][0]
        assert args[0]["code"] == "600584"
        assert args[0]["name"] == "长电科技"
        assert args[0]["data"]["change_pct"] == round((33.50 - 32.50) / 32.50 * 100, 2)


class TestBuildUserPromptFundFlow:
    """P1-1 二次修正：prompt 资金面键对齐 + 数据源不可达显式告知（此前读
    northbound_change/margin_change 无数据源 → 恒显示 "?"）"""

    def _prompt(self, data):
        from claw.debate.expert_prompts import build_user_prompt

        return build_user_prompt("600584", "长电科技", data)

    def test_normal_flow_shows_inflow(self):
        p = self._prompt(
            {
                "price": 33.5,
                "change_pct": 1.2,
                "sector": "半导体",
                "fund_flow": {"main_net_inflow": 1234.5, "main_pct": 1.59},
            }
        )
        assert "主力净流入=1234.5万元" in p
        assert "主力净流入占比=1.59%" in p
        assert "?" not in p.split("【资金面】")[-1]

    def test_unavailable_source_explicit(self):
        p = self._prompt(
            {
                "price": 33.5,
                "change_pct": 1.2,
                "sector": "半导体",
                "fund_flow": {"_source": "unavailable"},
            }
        )
        assert "数据源不可用" in p
        assert "不得据此强判" in p

    def test_empty_flow_no_section(self):
        p = self._prompt({"price": 33.5, "change_pct": 1.2})
        assert "【资金面】" not in p
