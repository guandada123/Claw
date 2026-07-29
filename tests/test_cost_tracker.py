"""test_cost_tracker.py — cost_tracker 核心函数测试。

覆盖：_match_model、log_call、get_monthly_spent、daily_report、_load_records。
用 unittest.mock 控制文件读写与外部状态，避免污染 ~/.ai_cost_log.jsonl。
"""

import json

import cost_tracker as ct
import pytest

# ──────────────────────────────────────────────
# _match_model
# ──────────────────────────────────────────────


def test_match_model_exact():
    assert ct._match_model("gpt-5") == "gpt-5"
    assert ct._match_model("deepseek-v4-pro") == "deepseek-v4-pro"


def test_match_model_substring():
    # "gpt-5-something" 包含 "gpt-5" → 匹配
    assert ct._match_model("gpt-5-2025-06-15") == "gpt-5"
    # 短 key "gpt-5" 被长字符串包含 → 匹配
    assert ct._match_model("claude-opus-4-20250514-special") == "claude-opus-4-20250514"


def test_match_model_unknown():
    assert ct._match_model("nonexistent-model-xyz") == "unknown"


# ──────────────────────────────────────────────
# log_call
# ──────────────────────────────────────────────


def test_log_call_writes_jsonl_and_returns_cost(tmp_path, monkeypatch):
    """log_call 写入临时 JSONL 并返回正确的成本。"""
    monkeypatch.setattr(ct, "LOG_FILE", tmp_path / "test_cost_log.jsonl")

    cost = ct.log_call("gpt-5", 10000, 5000, task="代码审查", project="Claw")
    # gpt-5: input=18.0/万, output=72.0/万
    # (10000*18 + 5000*72)/10000 = (180000+360000)/10000 = 54.0
    assert cost == pytest.approx(54.0)

    lines = (tmp_path / "test_cost_log.jsonl").read_text().strip().split("\n")
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["model"] == "gpt-5"
    assert rec["model_key"] == "gpt-5"
    assert rec["input"] == 10000
    assert rec["output"] == 5000
    assert rec["cost_cny"] == pytest.approx(54.0)
    assert rec["task"] == "代码审查"
    assert rec["project"] == "Claw"


def test_log_call_with_cache_tokens(tmp_path, monkeypatch):
    """log_call 带 prompt_cache 参数时写入独立缓存日志。"""
    monkeypatch.setattr(ct, "LOG_FILE", tmp_path / "test_cost_log.jsonl")
    monkeypatch.setattr(ct, "CACHE_LOG_FILE", tmp_path / "test_cache_log.jsonl")

    ct.log_call(
        "deepseek-v4-flash", 8000, 2000,
        prompt_cache_hit_tokens=6000,
        prompt_cache_miss_tokens=2000,
    )

    # 验证缓存日志存在
    assert (tmp_path / "test_cache_log.jsonl").exists()
    cache_lines = (tmp_path / "test_cache_log.jsonl").read_text().strip().split("\n")
    assert len(cache_lines) == 1
    crec = json.loads(cache_lines[0])
    assert crec["prompt_cache_hit_tokens"] == 6000
    assert crec["prompt_cache_miss_tokens"] == 2000
    assert crec["total_input_tokens"] == 8000
    assert crec["hit_rate"] == pytest.approx(75.0)


# ──────────────────────────────────────────────
# _load_records / get_monthly_spent
# ──────────────────────────────────────────────


def test_load_records_from_temp_file(tmp_path, monkeypatch):
    f = tmp_path / "cost_log.jsonl"
    f.write_text(
        json.dumps({"date": "2026-07-19", "cost_cny": 1.5}) + "\n"
        + json.dumps({"date": "2026-07-18", "cost_cny": 2.0}) + "\n"
        + json.dumps({"date": "2026-07-19", "cost_cny": 0.8}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ct, "LOG_FILE", f)

    # 全量
    assert len(ct._load_records()) == 3
    # 按日期过滤
    recs = ct._load_records("2026-07-19")
    assert len(recs) == 2
    assert sum(r["cost_cny"] for r in recs) == pytest.approx(2.3)


def test_get_monthly_spent(monkeypatch):
    monkeypatch.setattr(
        ct,
        "_load_records",
        lambda _date=None: [
            {"cost_cny": 10.0},
            {"cost_cny": 5.5},
            {"cost_cny": 0.0},
        ],
    )
    assert ct.get_monthly_spent() == pytest.approx(15.5)


# ──────────────────────────────────────────────
# daily_report
# ──────────────────────────────────────────────


def test_daily_report_empty_returns_zero():
    result = ct.daily_report(records=[])
    assert result["total"] == 0
    assert result["count"] == 0


def test_daily_report_with_mock_records():
    records = [
        {"model": "gpt-5", "model_key": "gpt-5", "cost_cny": 2.0, "project": "Claw", "task": "代码审查"},
        {"model": "deepseek-v4-flash", "model_key": "deepseek-v4-flash", "cost_cny": 0.5, "project": "QTS", "task": "回测"},
        {"model": "gpt-5", "model_key": "gpt-5", "cost_cny": 1.0, "project": "Claw", "task": "选股"},
    ]
    result = ct.daily_report(records=records)
    assert result["total"] == pytest.approx(3.5)
    assert result["count"] == 3
    assert "gpt-5" in result["by_model"]
    assert "Claw" in result["by_project"]
    assert "代码审查" in result["by_task"]


def test_daily_report_exceeds_warning_triggers(capsys):
    """日花费超过 DAILY_WARNING_CNY(=25) 应触发告警输出。"""
    records = [{"model": "gpt-5", "model_key": "gpt-5", "cost_cny": 30.0, "project": "X", "task": "X"}]
    ct.daily_report(records=records)
    captured = capsys.readouterr()
    assert "超出警告线" in captured.out


# ──────────────────────────────────────────────
# 常量与快照
# ──────────────────────────────────────────────


def test_model_prices_contains_required_models():
    assert "gpt-5" in ct.MODEL_PRICES
    assert "deepseek-v4-flash" in ct.MODEL_PRICES
    assert "deepseek-v4-pro" in ct.MODEL_PRICES
    assert "kimi-k2.6" in ct.MODEL_PRICES
    assert "ollama-local" in ct.MODEL_PRICES


def test_budget_constants_positive():
    assert ct.MONTHLY_BUDGET_CNY > 0
    assert ct.FLASH_LOCK_THRESHOLD > 0
    assert ct.DAILY_WARNING_CNY > 0


# ──────────────────────────────────────────────
# log_estimate
# ──────────────────────────────────────────────


def test_log_estimate_known_automation(tmp_path, monkeypatch):
    """log_estimate 对已知自动化名称写入估算日志。"""
    monkeypatch.setattr(ct, "LOG_FILE", tmp_path / "cost_log.jsonl")
    cost = ct.log_estimate("盘前分析", project="Claw")
    # 盘前分析: deepseek-v4-flash, inp=3000, out=800
    # (3000*0.5 + 800*1.5)/10000 = 0.27
    assert cost is not None
    assert cost == pytest.approx(0.27)
    assert (tmp_path / "cost_log.jsonl").exists()


def test_log_estimate_unknown_automation(tmp_path, monkeypatch):
    """log_estimate 对未知自动化名称返回 None。"""
    monkeypatch.setattr(ct, "LOG_FILE", tmp_path / "cost_log.jsonl")
    cost = ct.log_estimate("不存在的自动化名称XYZ")
    assert cost is None
    # 没有写入日志
    assert not (tmp_path / "cost_log.jsonl").exists()


def test_log_estimate_with_overrides(tmp_path, monkeypatch):
    """log_estimate 支持 override 模型与 Token 数。"""
    monkeypatch.setattr(ct, "LOG_FILE", tmp_path / "cost_log.jsonl")
    cost = ct.log_estimate(
        "盘前分析", project="QTS",
        override_model="deepseek-v4-pro",
        override_inp=5000,
        override_out=2000,
    )
    # deepseek-v4-pro: input=4.0, output=12.0
    # (5000*4 + 2000*12)/10000 = 4.4
    assert cost == pytest.approx(4.4)


# ──────────────────────────────────────────────
# monthly_report / top_expensive_tasks
# ──────────────────────────────────────────────


def test_monthly_report_with_mock_empty(monkeypatch):
    monkeypatch.setattr(ct, "_load_records", lambda _d=None: [])
    result = ct.monthly_report()
    assert result["total"] == 0
    assert result["count"] == 0


def test_monthly_report_with_mock_records(monkeypatch):
    monkeypatch.setattr(
        ct,
        "_load_records",
        lambda _d=None: [
            {"model": "gpt-5", "model_key": "gpt-5", "cost_cny": 50.0, "project": "Claw", "task": "选股"},
            {"model": "deepseek-v4-flash", "model_key": "deepseek-v4-flash", "cost_cny": 5.0, "project": "Claw", "task": "日报"},
        ],
    )
    result = ct.monthly_report()
    assert result["total"] == pytest.approx(55.0)
    assert result["count"] == 2
    assert "gpt-5" in result["by_model"]
    assert "Claw" in result["by_project"]


def test_top_expensive_tasks(monkeypatch):
    monkeypatch.setattr(
        ct,
        "_load_records",
        lambda _d=None: [
            {"model": "gpt-5", "model_key": "gpt-5", "cost_cny": 30.0, "task": "深度分析"},
            {"model": "gpt-5", "model_key": "gpt-5", "cost_cny": 10.0, "task": "简单查询"},
        ],
    )
    result = ct.top_expensive_tasks(3)
    assert len(result) >= 1
    assert result[0][0] == "深度分析"
    assert result[0][1] == 30.0


# ──────────────────────────────────────────────
# log_estimate_all_today
# ──────────────────────────────────────────────


def test_log_estimate_all_today_returns_total():
    """log_estimate_all_today 遍历 AUTO_COST_ESTIMATES 并返回总和。"""
    total = ct.log_estimate_all_today(project="Claw")
    assert total > 0
    assert isinstance(total, float)


# ──────────────────────────────────────────────
# cache_report
# ──────────────────────────────────────────────


def test_cache_report_no_file(tmp_path, monkeypatch):
    """CACHE_LOG_FILE 不存在时返回空字典。"""
    monkeypatch.setattr(ct, "CACHE_LOG_FILE", tmp_path / "nonexistent.jsonl")
    result = ct.cache_report()
    assert result == {}


def test_cache_report_with_data(tmp_path, monkeypatch):
    """缓存日志有数据时返回完整的命中率汇总。"""
    f = tmp_path / "cache_log.jsonl"
    f.write_text(
        json.dumps({
            "ts": "2026-07-19T10:00:00",
            "date": "2026-07-19",
            "model": "deepseek-v4-flash",
            "model_key": "deepseek-v4-flash",
            "prompt_cache_hit_tokens": 8000,
            "prompt_cache_miss_tokens": 2000,
            "task": "日报",
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ct, "CACHE_LOG_FILE", f)
    result = ct.cache_report(target_date="2026-07-19")
    assert result["total_calls"] == 1
    assert result["total_hit_tokens"] == 8000
    assert result["total_miss_tokens"] == 2000
    assert result["overall_hit_rate"] == pytest.approx(80.0)
    assert "deepseek-v4-flash" in result["by_model"]
    # 成本效益数值存在
    assert "actual_cost_cny" in result
    assert "savings_cny" in result
