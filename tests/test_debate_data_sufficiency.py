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

    def test_delay_host_fallback(self):
        """08-21 二次修复：实时端 push2 不可达(空) → 延时端 push2delay 兜底成功。
        实测 push2delay 稳定可达，资金流不再 unavailable 降级。"""
        calls = []

        def fake_run(cmd, capture_output=False, text=False, timeout=None):
            url = cmd[2]  # cmd = ["curl", "-s", url]
            calls.append(url)
            if "push2.eastmoney.com" in url:
                resp = mock.Mock()
                resp.stdout = ""
                return resp
            resp = mock.Mock()
            resp.stdout = json.dumps(
                {"rc": 0, "data": {"diff": [{"f62": 157098016.0, "f184": 159}]}}
            )
            return resp

        with mock.patch("subprocess.run", side_effect=fake_run):
            mf = run_debate._fetch_money_flow("600584")
        assert mf["main_net"] == 157098016.0
        assert mf["main_pct"] == 1.59
        assert any("push2delay.eastmoney.com" in c for c in calls)

    def test_enrich_attaches_fund_flow(self):
        """集成：_enrich_stock_data 成功补全资金面 → data['fund_flow'] 非空，
        data_insufficient 不再恒标 fund_flow；键名对齐 prompt 消费键 main_net_inflow。
        08-24 修复：注入 anysearch_helper MagicMock 消除 CI 无包环境的 fundamental 兜底依赖。"""
        base = {"price": 33.5, "change_pct": 1.2, "sector": "半导体"}
        _mh = mock.MagicMock()
        _mh.a_stock_quote.return_value = {
            "pe_ttm": 20.0,
            "pb": 3.0,
            "total_mv": 20000000,
        }
        _mh.a_stock_indicator.return_value = {"roe": 10.0, "revenue_growth": 5.0}
        with (
            mock.patch.dict(sys.modules, {"anysearch_helper": _mh}),
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


class TestDebateFromCodesSuffix:
    """08-21 修复：扫描脚本产出 code 带 .SH/.SZ 后缀（如 "000025.SZ"），
    辩论入口须自动剥后缀取纯 6 位数字，否则腾讯行情按纯数字匹配被过滤
    → 价格=0 → 数据链异常 → LLM 模板漂移推送占位符"""

    _GT_QUOTE = 'v_sz000025="1~特力A~000025~15.48~15.20~15.30~100000~50000~50000~15.48";\n'

    def test_suffix_stripped(self):
        fake = mock.Mock()
        fake.stdout = self._GT_QUOTE.encode("gbk", errors="replace")
        with (
            mock.patch("subprocess.run", return_value=fake),
            mock.patch.object(run_debate, "_enrich_stock_data", side_effect=lambda c, b: dict(b)),
            mock.patch.object(run_debate, "batch_debate") as m_batch,
        ):
            run_debate.debate_from_codes("000025.SZ,603408.SH")
        args = m_batch.call_args[0][0]
        codes = [x["code"] for x in args]
        assert "000025" in codes and "603408" in codes

    def test_invalid_format_filtered(self):
        fake = mock.Mock()
        fake.stdout = ""
        with (
            mock.patch("subprocess.run", return_value=fake),
            mock.patch.object(run_debate, "batch_debate") as m_batch,
        ):
            run_debate.debate_from_codes("abc,123,600584.SH")
        args = m_batch.call_args[0][0] if m_batch.called else []
        codes = [x["code"] for x in args]
        assert codes == ["600584"]


class TestSentimentEnrichment:
    """08-21 sentiment 补齐：公众号信号聚合 + 热度榜，消除 design_gap=['sentiment']"""

    def test_classify_signal(self):
        assert run_debate._classify_signal("bullish") == 1
        assert run_debate._classify_signal("买入") == 1
        assert run_debate._classify_signal("bearish") == -1
        assert run_debate._classify_signal("止损🔴已触发·开盘清仓") == -1
        assert run_debate._classify_signal("证伪·回避(半导体#13区间-14.12%)") == -1
        assert run_debate._classify_signal("主线确认#1(RPS100)") == 1
        assert run_debate._classify_signal("观察") == 0
        assert run_debate._classify_signal("") == 0

    def test_fetch_sentiment_aggregates_wechat(self):
        """近7天聚合：2 多 1 空 → wechat_signals {bullish:2, bearish:1, net:1}"""
        recs = [
            {"stock_code": "600584", "signal": "bullish", "recorded_at": "2026-08-20 08:50"},
            {"stock_code": "600584", "signal": "买入", "recorded_at": "2026-08-19 08:50"},
            {"stock_code": "600584", "signal": "清仓(止损击穿)", "recorded_at": "2026-08-18 08:50"},
            {
                "stock_code": "600584",
                "signal": "观察",
                "recorded_at": "2026-08-10 08:50",
            },  # 超7天不计
            {
                "stock_code": "999999",
                "signal": "bullish",
                "recorded_at": "2026-08-20 08:50",
            },  # 非目标股
        ]
        sig_path = Path(__file__).parent.parent / ".workbuddy" / "data" / "article_signals.json"
        with (
            mock.patch.object(Path, "exists", return_value=True),
            mock.patch.object(Path, "read_text", return_value=json.dumps(recs, ensure_ascii=False)),
            mock.patch.object(run_debate, "_fetch_hot_rank", return_value=None),
        ):
            senti = run_debate._fetch_sentiment("600584")
        assert senti["wechat_signals"] == {"bullish": 2, "bearish": 1, "net": 1}

    def test_fetch_sentiment_hot_rank(self):
        with (
            mock.patch.object(Path, "exists", return_value=False),
            mock.patch.object(run_debate, "_fetch_hot_rank", return_value=7),
        ):
            senti = run_debate._fetch_sentiment("600584")
        assert senti.get("social_heat", {}).get("rank") == 7

    def test_sentiment_clears_design_gap(self):
        """sentiment 非空 → design_gap 消除（此前恒标 ['sentiment']）"""
        from claw.debate.debate_engine import _assess_data_sufficiency

        data = {
            "technical": {"rsi": 50},
            "fundamental": {"pe": 10},
            "fund_flow": {"main_net_inflow": 100},
            "sentiment": {"wechat_signals": {"bullish": 2, "bearish": 1, "net": 1}},
        }
        missing, gap = _assess_data_sufficiency(data)
        assert missing == []
        assert gap == []

    def test_enrich_attaches_sentiment(self):
        """08-24 修复 CI 红灯根因：该测试此前依赖外部 anysearch_helper/westock
        兜底补全 fundamental，但 CI 环境未装该包且无网 → fundamental 恒空 →
        _assess_data_sufficiency 报 missing=['fundamental'] → 断言失败。
        修复：注入 anysearch_helper MagicMock 使 fundamental 补全确定性，
        测试不再依赖外部网络/未声明依赖（本地有 westock 才 passed 属偶发）。"""
        base = {"price": 33.5, "change_pct": 1.2, "sector": "半导体"}
        _mh = mock.MagicMock()
        _mh.a_stock_quote.return_value = {
            "pe_ttm": 20.0,
            "pb": 3.0,
            "total_mv": 20000000,
        }
        _mh.a_stock_indicator.return_value = {"roe": 10.0, "revenue_growth": 5.0}
        with (
            mock.patch.dict(sys.modules, {"anysearch_helper": _mh}),
            mock.patch.object(run_debate, "_fetch_money_flow", return_value={"error": "down"}),
            mock.patch.object(run_debate, "_load_fundamental_cache", return_value={}),
            mock.patch.object(run_debate, "_write_fundamental_cache", return_value=None),
            mock.patch.object(run_debate, "_ema_series", side_effect=run_debate._ema_series),
            mock.patch.object(
                run_debate,
                "_fetch_sentiment",
                return_value={"wechat_signals": {"bullish": 1, "bearish": 0, "net": 1}},
            ),
        ):
            data = run_debate._enrich_stock_data("600584", base)
        assert data.get("sentiment", {}).get("wechat_signals", {}).get("net") == 1
        assert data.get("fundamental", {}).get("pe") == 20.0
        missing, gap = _assess_data_sufficiency(data)
        assert missing == [] and gap == []


class TestPushCardPlaceholderDefense:
    """08-21 防御：build_card 下沉占位符检测。LLM 模板漂移时即使绕过 CLI
    直接调 send() 也会被拦，避免 08-20 15:44「title/body」字面卡片事故"""

    import importlib.util as _ilu

    _spec = _ilu.spec_from_file_location(
        "push_card",
        Path(__file__).parent.parent / ".workbuddy" / "scripts" / "push_card.py",
    )
    push_card = _ilu.module_from_spec(_spec)  # type: ignore
    _spec.loader.exec_module(push_card)  # type: ignore

    def _try_build(self, sections):
        try:
            self.push_card.build_card("📊 测试", "info", sections)
        except ValueError as exc:
            return "占位符" in str(exc)
        return False

    def test_bare_title_body_intercepted(self):
        assert self._try_build([("title", "body")]) is True

    def test_placeholder_braces_intercepted(self):
        assert self._try_build([("今日", "{date}")]) is True

    def test_empty_body_intercepted(self):
        assert self._try_build([("今日", "")]) is True

    def test_real_content_passes(self):
        try:
            self.push_card.build_card(
                "📊 收盘晚报",
                "info",
                [("特力A", "现价 15.48 / COMBO 0.25 / 建议买入")],
            )
        except ValueError as exc:
            raise AssertionError("real content must not be intercepted") from exc
