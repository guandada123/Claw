"""`unified_ops_center.py` 核心决策逻辑的回归测试（2026-10-11 建立）。

该模块 1557 语句、此前仅 14% 覆盖，且它**挂着整个自愈链路** —— 一旦决策逻辑
静默失效，表现是「巡检看起来在跑、其实什么都没查/告警被吞/自愈变互害」，
而这恰恰是本项目最高频的一类事故（见 .learnings）。

本文件只测**确定性决策逻辑**（纯函数 + 文件状态），不测外部命令/网络：
  A. 告警去重  —— `_dedup_key` 归一化 / TTL override / `is_alert_duplicated`
  B. 熔断三态  —— `is_runbook_fused` / `audit_self_actions` / `_sink_failure_mode`
  C. 审计留痕  —— `append_heal_log` 的同类节流与 200 条环形上限
  D. 纯解析    —— `_extract_pick_codes` / `_parse_docker_ts` / `_utc_iso` / `_held_clause`
  E. 选股共识  —— `check_duplicate_picks`（含"假多源共识"回归）

隔离铁律：模块级 4 个路径常量（ALERT_DEDUP_STATE / SELF_HEAL_LOG / FUSE_STATE_FILE /
CROSS_STATE_PATH）以及 SCRIPT_DIR 全部改指 tmp_path —— 否则测试会**读写生产状态文件**。
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import pathlib

import pytest

SRC = (
    pathlib.Path(__file__).resolve().parent.parent
    / ".workbuddy"
    / "scripts"
    / "unified_ops_center.py"
)
_spec = importlib.util.spec_from_file_location("uoc_core", SRC)
uoc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(uoc)


@pytest.fixture(autouse=True)
def isolate_state(tmp_path, monkeypatch):
    """把所有模块级状态路径改指 tmp_path —— 防测试污染生产状态文件。"""
    monkeypatch.setattr(uoc, "ALERT_DEDUP_STATE", tmp_path / ".ops_alerted.json")
    monkeypatch.setattr(uoc, "SELF_HEAL_LOG", tmp_path / "unified_self_heal_log.json")
    monkeypatch.setattr(uoc, "FUSE_STATE_FILE", tmp_path / ".runbook_fuse.json")
    monkeypatch.setattr(uoc, "CROSS_STATE_PATH", tmp_path / "cross_project_state.json")
    monkeypatch.setattr(uoc, "SCRIPT_DIR", tmp_path / "scripts")
    monkeypatch.setattr(uoc, "_run_log", [])
    return tmp_path


def _now_minus(hours: float) -> str:
    return (datetime.datetime.now() - datetime.timedelta(hours=hours)).isoformat(timespec="seconds")


def _detect(target: str, reason: str, ts: str | None = None) -> dict:
    """构造一条 detect 留痕记录。**必须带 ts** —— 真实调用方传的是 log_action() 产物
    （含 ts）；无 ts 的裸 dict 会走 append_heal_log 的"时间戳不可解析"兜底分支，测不到真节流。"""
    return {
        "action": "detect",
        "target": target,
        "reason": reason,
        "result": "failed",
        "ts": ts or datetime.datetime.now().isoformat(timespec="seconds"),
    }


# ═══════════════════ A. 告警去重 ═══════════════════
def test_dedup_key_cuts_at_chinese_comma():
    assert uoc._dedup_key("chk", "登录过期，请扫码重登") == "chk@登录过期"


def test_dedup_key_cuts_at_fullwidth_paren():
    assert uoc._dedup_key("chk", "微信通道（登录过期）") == "chk@微信通道"


def test_dedup_key_keeps_ascii_paren():
    # 只切中文标点：ASCII 括注是身份的一部分，不能被切掉
    k = uoc._dedup_key("chk", "登录过期(isExpired=true)")
    assert k == "chk@登录过期(isExpired=true)"


def test_dedup_key_folds_digits():
    """同一根因的易变计数必须折叠 —— 这是 run#53「同一告警连推 3 次」的回归锚。"""
    a = uoc._dedup_key("chk", "鉴权失败 202 次/24h")
    b = uoc._dedup_key("chk", "鉴权失败 230 次/24h")
    assert a == b
    assert not any(ch.isdigit() for ch in a)


def test_dedup_key_strips_trailing_colon_and_space():
    assert uoc._dedup_key("chk", "调度活性异常：") == "chk@调度活性异常"
    assert uoc._dedup_key("chk", "  前后留白  ") == "chk@前后留白"


def test_dedup_key_truncates_to_maxlen():
    long = "问" * 200
    k = uoc._dedup_key("chk", long)
    assert k == f"chk@{'问' * uoc._DEDUP_KEY_MAXLEN}"
    assert len(k) == len("chk@") + uoc._DEDUP_KEY_MAXLEN


def test_dedup_key_empty_reason_still_stable():
    assert uoc._dedup_key("chk", "") == "chk@"
    assert uoc._dedup_key("chk", None) == "chk@"


def test_ttl_default_is_24h():
    assert uoc._ttl_hours_for("任意检查@随便什么问题") == 24


def test_ttl_override_hits_pure_manual_channel():
    key = "微信公众号通道@微信公众号通道@wechat-download-api 登录过期"
    assert uoc._ttl_hours_for(key) == 72


def test_is_alert_duplicated_first_false_then_true(isolate_state):
    assert uoc.is_alert_duplicated("chk", "磁盘将满") is False
    assert uoc.is_alert_duplicated("chk", "磁盘将满") is True
    # 状态已落盘
    saved = json.loads((isolate_state / ".ops_alerted.json").read_text(encoding="utf-8"))
    assert uoc._dedup_key("chk", "磁盘将满") in saved


def test_is_alert_duplicated_different_reason_not_dup():
    assert uoc.is_alert_duplicated("chk", "问题A") is False
    assert uoc.is_alert_duplicated("chk", "问题B") is False


def test_is_alert_duplicated_digits_folded_same_alert():
    """计数不同 = 同一告警，必须被判重（防每轮新键重推）。"""
    assert uoc.is_alert_duplicated("chk", "鉴权失败 202 次/24h") is False
    assert uoc.is_alert_duplicated("chk", "鉴权失败 999 次/24h") is True


def test_load_alerted_migrates_raw_keys(isolate_state):
    """历史"原始文案键"应收敛为稳定键，同组合并保留最新时间戳。"""
    raw = {
        "chk@登录过期，请扫码": 100.0,
        "chk@登录过期，换个说法": 200.0,  # 归一化后与上条同键
        "裸键": 50.0,  # 无 "@" → 原样保留
    }
    (isolate_state / ".ops_alerted.json").write_text(json.dumps(raw), encoding="utf-8")
    merged = uoc._load_alerted()
    assert merged["chk@登录过期"] == 200.0  # 保留最新
    assert merged["裸键"] == 50.0
    assert len(merged) == 2


def test_load_alerted_missing_file_returns_empty(isolate_state):
    assert uoc._load_alerted() == {}
    # 缺文件不应创建它
    assert not (isolate_state / ".ops_alerted.json").exists()


def test_load_alerted_skips_unparseable_timestamp(isolate_state):
    raw = {"chk@A": "not-a-number", "chk@B": 10.0}
    (isolate_state / ".ops_alerted.json").write_text(json.dumps(raw), encoding="utf-8")
    merged = uoc._load_alerted()
    assert merged == {"chk@B": 10.0}


def test_save_alerted_drops_expired_default_ttl(isolate_state):
    now = datetime.datetime.now().timestamp()
    uoc._save_alerted({"chk@旧": now - 25 * 3600, "chk@新": now - 1 * 3600})
    saved = json.loads((isolate_state / ".ops_alerted.json").read_text(encoding="utf-8"))
    assert "chk@旧" not in saved
    assert "chk@新" in saved


def test_save_alerted_keeps_override_ttl_entry(isolate_state):
    """72h 降频项在 25h 时仍应保留（默认 24h 早该清掉）。"""
    now = datetime.datetime.now().timestamp()
    key = "chk@微信公众号通道@wechat-download-api 登录过期"
    uoc._save_alerted({key: now - 25 * 3600})
    saved = json.loads((isolate_state / ".ops_alerted.json").read_text(encoding="utf-8"))
    assert key in saved


# ═══════════════════ B. 熔断三态 ═══════════════════
def test_fuse_state_missing_returns_empty(isolate_state):
    assert uoc._load_fuse_state() == {}


def test_fuse_state_roundtrip(isolate_state):
    uoc._save_fuse_state({"a": {"side_effects": 1, "fused": False}})
    assert uoc._load_fuse_state() == {"a": {"side_effects": 1, "fused": False}}


def test_is_runbook_fused_false_when_unfused():
    assert uoc.is_runbook_fused("memwatch_bump") is False


def test_is_runbook_fused_true_when_fused_recent():
    uoc._save_fuse_state({"memwatch_bump": {"fused": True, "fused_at": _now_minus(1)}})
    assert uoc.is_runbook_fused("memwatch_bump") is True


def test_is_runbook_fused_half_open_auto_recovers():
    """冷却期（6h）过后应自动解除熔断，允许重试探测。"""
    uoc._save_fuse_state({"memwatch_bump": {"fused": True, "fused_at": _now_minus(7)}})
    assert uoc.is_runbook_fused("memwatch_bump") is False


def test_is_runbook_fused_bad_timestamp_stays_fused():
    # 时间戳不可解析 → 不误判为 Half-Open（宁可继续熔断，不可放开互害）
    uoc._save_fuse_state({"memwatch_bump": {"fused": True, "fused_at": "garbage"}})
    assert uoc.is_runbook_fused("memwatch_bump") is True


def test_reset_runbook_fuse_clears(isolate_state):
    uoc._save_fuse_state({"a": {"fused": True}, "b": {"fused": True}})
    uoc._reset_runbook_fuse("a")
    left = uoc._load_fuse_state()
    assert "a" not in left and "b" in left


def test_audit_self_actions_fuses_and_sinks(monkeypatch, isolate_state):
    """效果连续未恢复 ×2 → 熔断降级 + 自动沉淀 F8 失败模式。"""
    monkeypatch.setattr(uoc, "_EFFECT_VERIFIERS", {"memwatch_bump": lambda: False})

    uoc._run_log = []
    uoc.log_action("memwatch_bump", "t", "r", "success")
    alerts1 = uoc.audit_self_actions()
    assert any("副作用累计 1" in a for a in alerts1)
    assert uoc._load_fuse_state()["memwatch_bump"]["fused"] is False

    uoc._run_log = []
    uoc.log_action("memwatch_bump", "t", "r", "success")
    alerts2 = uoc.audit_self_actions()
    assert any("已熔断降级" in a for a in alerts2)
    entry = uoc._load_fuse_state()["memwatch_bump"]
    assert entry["fused"] is True and entry["side_effects"] >= uoc.FUSE_TRIGGER

    # 自我升级第③环：熔断时沉淀失败模式
    cs = json.loads((isolate_state / "cross_project_state.json").read_text(encoding="utf-8"))
    km = cs["monitoring"]["known_failure_modes"]
    assert len(km) == 1 and km[0]["id"] == "F8" and km[0]["action"] == "memwatch_bump"


def test_audit_self_actions_decays_on_recovery(monkeypatch):
    """效果恢复正常 → 历史副作用计数减半（防一次旧失误永久锁死）。"""
    uoc._save_fuse_state({"memwatch_bump": {"side_effects": 4, "fused": False}})
    monkeypatch.setattr(uoc, "_EFFECT_VERIFIERS", {"memwatch_bump": lambda: True})
    uoc._run_log = []
    rec = uoc.log_action("memwatch_bump", "t", "r", "success")
    assert uoc.audit_self_actions() == []
    assert uoc._load_fuse_state()["memwatch_bump"]["side_effects"] == 2
    assert rec["self_audit"] == "effect_recovered"


def test_audit_self_actions_unverifiable_not_counted(monkeypatch):
    monkeypatch.setattr(uoc, "_EFFECT_VERIFIERS", {"memwatch_bump": lambda: None})
    uoc._run_log = []
    rec = uoc.log_action("memwatch_bump", "t", "r", "success")
    assert uoc.audit_self_actions() == []
    assert uoc._load_fuse_state().get("memwatch_bump", {}).get("side_effects", 0) == 0
    assert rec["self_audit"] == "effect_unverifiable"


def test_audit_self_actions_ignores_non_success(monkeypatch):
    """只有 result=="success" 的动作才做效果验证。"""
    monkeypatch.setattr(uoc, "_EFFECT_VERIFIERS", {"memwatch_bump": lambda: False})
    uoc._run_log = []
    uoc.log_action("memwatch_bump", "t", "r", "failed")
    assert uoc.audit_self_actions() == []


def test_audit_self_actions_ignores_unregistered_action(monkeypatch):
    monkeypatch.setattr(uoc, "_EFFECT_VERIFIERS", {"memwatch_bump": lambda: False})
    uoc._run_log = []
    uoc.log_action("dependabot_rebase", "t", "r", "success")
    assert uoc.audit_self_actions() == []


def test_sink_failure_mode_idempotent(isolate_state):
    uoc._sink_failure_mode("memwatch_bump")
    uoc._sink_failure_mode("memwatch_bump")
    cs = json.loads((isolate_state / "cross_project_state.json").read_text(encoding="utf-8"))
    assert len(cs["monitoring"]["known_failure_modes"]) == 1


def test_sink_failure_mode_allocates_next_id(isolate_state):
    """已有 F1-F7 → 新项应分到 F8；再入不同 action → F9。"""
    (isolate_state / "cross_project_state.json").write_text(
        json.dumps({"monitoring": {"known_failure_modes": [{"id": f"F{i}"} for i in range(1, 8)]}}),
        encoding="utf-8",
    )
    uoc._sink_failure_mode("act_a")
    uoc._sink_failure_mode("act_b")
    cs = json.loads((isolate_state / "cross_project_state.json").read_text(encoding="utf-8"))
    ids = [k["id"] for k in cs["monitoring"]["known_failure_modes"]]
    assert ids[-2:] == ["F8", "F9"]


# ═══════════════════ C. 审计留痕 ═══════════════════
def test_log_action_records_fields():
    uoc._run_log = []
    rec = uoc.log_action("detect", "目标", "原因", "success", "细节")
    assert rec == {
        "ts": rec["ts"],
        "action": "detect",
        "target": "目标",
        "reason": "原因",
        "result": "success",
        "detail": "细节",
    }
    assert uoc._run_log == [rec]


def test_append_heal_log_appends(isolate_state):
    uoc.append_heal_log({"action": "success", "target": "t", "reason": "r", "result": "success"})
    data = json.loads((isolate_state / "unified_self_heal_log.json").read_text(encoding="utf-8"))
    assert len(data) == 1 and data[0]["hits"] == 1


def test_append_heal_log_throttles_detect(isolate_state):
    """同类 detect 在 6h 窗口内不新增条目，只把 ts 刷新并累加 hits。"""
    uoc.append_heal_log(_detect("微信", "登录过期"))
    uoc.append_heal_log(_detect("微信", "登录过期"))
    uoc.append_heal_log(_detect("微信", "登录过期"))
    data = json.loads((isolate_state / "unified_self_heal_log.json").read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["hits"] == 3


def test_append_heal_log_detect_different_target_not_merged(isolate_state):
    uoc.append_heal_log(_detect("A", "x"))
    uoc.append_heal_log(_detect("B", "x"))
    data = json.loads((isolate_state / "unified_self_heal_log.json").read_text(encoding="utf-8"))
    assert len(data) == 2


def test_append_heal_log_non_detect_not_throttled(isolate_state):
    """自愈/修复类动作是处置留痕，一律不节流。"""
    for _ in range(3):
        uoc.append_heal_log(
            {"action": "success", "target": "t", "reason": "r", "result": "success"}
        )
    data = json.loads((isolate_state / "unified_self_heal_log.json").read_text(encoding="utf-8"))
    assert len(data) == 3


def test_append_heal_log_throttle_expires_after_window(isolate_state):
    """超过节流窗口的同项 → 落新条目（hits 不累加）。"""
    old = {
        "action": "detect",
        "target": "微信",
        "reason": "登录过期",
        "result": "failed",
        "ts": (datetime.datetime.now() - datetime.timedelta(hours=7)).isoformat(timespec="seconds"),
        "hits": 5,
    }
    (isolate_state / "unified_self_heal_log.json").write_text(json.dumps([old]), encoding="utf-8")
    uoc.append_heal_log(_detect("微信", "登录过期"))
    data = json.loads((isolate_state / "unified_self_heal_log.json").read_text(encoding="utf-8"))
    assert len(data) == 2
    assert data[0]["hits"] == 5  # 旧条目未被改写


def test_append_heal_log_caps_at_200(isolate_state):
    for i in range(205):
        uoc.append_heal_log({"action": "success", "target": f"t{i}", "reason": "r", "result": "ok"})
    data = json.loads((isolate_state / "unified_self_heal_log.json").read_text(encoding="utf-8"))
    assert len(data) == 200
    assert data[-1]["target"] == "t204"  # 保留最近 200


# ═══════════════════ D. 纯解析 ═══════════════════
def test_extract_pick_codes_from_variant_shapes():
    assert uoc._extract_pick_codes({"picks": [{"code": "600036"}]}) == ["600036"]
    assert uoc._extract_pick_codes({"candidates": [{"code": "000001"}]}) == ["000001"]
    assert uoc._extract_pick_codes([{"code": "600519"}, {"code": "000858"}]) == ["600519", "000858"]
    assert uoc._extract_pick_codes({"code": "601318"}) == ["601318"]
    assert uoc._extract_pick_codes({"code": 601318}) == []  # 非字符串不提取


def test_extract_pick_codes_from_text():
    assert uoc._extract_pick_codes("推荐 600036 与 000001") == ["600036", "000001"]
    assert uoc._extract_pick_codes("无代码") == []


def test_extract_pick_codes_returns_raw_duplicates():
    """原样返回（可能含重复） —— 去重是消费方的责任，见 check_duplicate_picks。"""
    assert uoc._extract_pick_codes({"picks": [{"code": "600036"}, {"code": "600036"}]}) == [
        "600036",
        "600036",
    ]


def test_parse_docker_ts_nanosecond_truncation():
    ts = uoc._parse_docker_ts("2026-08-31T06:44:58.213464901Z 日志正文")
    assert ts > 0


def test_parse_docker_ts_equal_across_fraction_digits():
    a = uoc._parse_docker_ts("2026-08-31T06:44:58.1Z x")
    b = uoc._parse_docker_ts("2026-08-31T06:44:58.100000000Z x")
    assert a == b > 0


def test_parse_docker_ts_no_fraction():
    assert uoc._parse_docker_ts("2026-08-31T06:44:58Z x") > 0


def test_parse_docker_ts_invalid_returns_zero():
    assert uoc._parse_docker_ts("2026-08-31 06:44:58 no-T") == 0.0
    assert uoc._parse_docker_ts("garbage") == 0.0
    assert uoc._parse_docker_ts("2026-13-45T99:99:99Z bad") == 0.0


def test_utc_iso_format():
    assert uoc._utc_iso(0) == "01-01 00:00Z"


def test_held_clause_empty_returns_blank():
    assert uoc._held_clause([]) == ""
    assert uoc._held_clause([(0.0, "id", None), (0.0, "id", 0)]) == ""


def test_held_clause_median_odd_and_even():
    odd = uoc._held_clause([(0.0, "a", 10.0), (0.0, "b", 20.0), (0.0, "c", 30.0)])
    assert "p50 20min" in odd and "最长 30min" in odd
    even = uoc._held_clause([(0.0, f"a{i}", h) for i, h in enumerate([10.0, 20.0, 30.0, 40.0])])
    assert "p50 25min" in even and "最长 40min" in even


def test_held_clause_filters_nonpositive():
    """负值只可能来自日志轮转后配错 start，必须滤掉（不编造）。"""
    out = uoc._held_clause([(0.0, "a", -5.0), (0.0, "b", 12.0)])
    assert "p50 12min" in out and "-5" not in out


# ═══════════════════ E. 选股共识（含假多源共识回归） ═══════════════════
def _write_pick(isolate_state, name: str, payload) -> None:
    sim = isolate_state / "data" / "simulation"
    exp = sim / "experiments"
    exp.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().strftime("%Y-%m-%d")
    if name == "智能选股":
        (exp / f"{today}.json").write_text(json.dumps(payload), encoding="utf-8")
    elif name == "午间选股":
        (exp / f"midday_pick_{today}.json").write_text(json.dumps(payload), encoding="utf-8")
    else:
        sim.mkdir(parents=True, exist_ok=True)
        (sim / "picks_history.json").write_text(json.dumps(payload), encoding="utf-8")


def test_check_duplicate_picks_consensus_across_sources(isolate_state):
    _write_pick(isolate_state, "智能选股", {"picks": [{"code": "600036"}]})
    _write_pick(isolate_state, "午间选股", {"picks": [{"code": "600036"}, {"code": "000001"}]})
    res = uoc.check_duplicate_picks()
    assert res["ok"] is None and res["alerts"] == []
    assert "多源共识 1 票" in res["note"]
    assert "600036(智能选股+午间选股)" in res["note"]


def test_check_duplicate_picks_no_false_consensus_within_one_source(isolate_state):
    """回归：同一源内同码重复不得被算成"多源共识"（此前会写成 600036(智能选股+智能选股)）。"""
    _write_pick(isolate_state, "智能选股", {"picks": [{"code": "600036"}, {"code": "600036"}]})
    res = uoc.check_duplicate_picks()
    assert "无多源共识" in res["note"]  # 真的没有共识票
    assert "600036(" not in res["note"]  # 不得列出共识明细


def test_check_duplicate_picks_no_data(isolate_state):
    res = uoc.check_duplicate_picks()
    assert res["ok"] is None
    assert "今日 0 票" in res["note"]


# ════════════════════ F. 仓库状态（2026-10-11 新增）════════════════════


class _FakeRun:
    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def _fake_cmd(branch: str, porcelain: str):
    """替身 run_cmd：按子命令返回预置输出（不真跑 git）。"""

    def _run(cmd, timeout=90, capture=True, env=None, cwd=None):
        if "rev-parse" in cmd:
            return _FakeRun(branch + "\n")
        if "status" in cmd:
            return _FakeRun(porcelain)
        return _FakeRun("")

    return _run


def _repo(tmp_path, name="r"):
    p = tmp_path / name
    (p / ".git").mkdir(parents=True, exist_ok=True)
    return p


def test_repo_state_flags_non_default_branch(tmp_path, monkeypatch):
    """硬判据：仓库被留在非默认分支（实测发生过 —— 负向验证做完没切回 main）。"""
    monkeypatch.setattr(uoc, "run_cmd", _fake_cmd("test/lock-gate-negative-20261011", ""))
    r = uoc.check_repo_state(targets=[("QTS", _repo(tmp_path), "main")])
    assert r["ok"] is False
    assert "分支" in r["alerts"][0] and "checkout main" in r["alerts"][0]


def test_repo_state_clean_repo_is_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(uoc, "run_cmd", _fake_cmd("main", ""))
    r = uoc.check_repo_state(targets=[("Claw", _repo(tmp_path), "main")])
    assert r["ok"] is True and r["alerts"] == []


def test_repo_state_flags_stale_dirty(tmp_path, monkeypatch):
    """软判据：改动滞留 ≥12h 才报（几分钟内的改动是自动化"边写边提交"的常态，不报）。"""
    import os
    import time

    repo = _repo(tmp_path)
    f = repo / "a.py"
    f.write_text("x", encoding="utf-8")
    old = time.time() - 20 * 3600
    os.utime(f, (old, old))
    monkeypatch.setattr(uoc, "run_cmd", _fake_cmd("main", " M a.py\n"))
    r = uoc.check_repo_state(targets=[("Claw", repo, "main")])
    assert r["ok"] is False
    assert "滞留" in r["alerts"][0] and "20" in r["alerts"][0]


def test_repo_state_fresh_dirty_is_not_an_alert(tmp_path, monkeypatch):
    """新鲜改动（<12h）不报 —— 否则每次巡检都撞上并发自动化的在写状态 = 恒真噪音。"""
    repo = _repo(tmp_path)
    (repo / "a.py").write_text("x", encoding="utf-8")
    monkeypatch.setattr(uoc, "run_cmd", _fake_cmd("main", " M a.py\n"))
    r = uoc.check_repo_state(targets=[("Claw", repo, "main")])
    assert r["ok"] is True and r["alerts"] == []


def test_repo_state_missing_repo_is_explicit_skip(tmp_path, monkeypatch):
    """取不到证 → ok=None 显式跳过（**不计入 ✅**，与中枢其它检查同口径）。"""
    monkeypatch.setattr(uoc, "run_cmd", _fake_cmd("main", ""))
    r = uoc.check_repo_state(targets=[("Nope", tmp_path / "no-such-repo", "main")])
    assert r["ok"] is None
    assert "取不到证" in r["note"]


def test_repo_state_git_error_is_skip(tmp_path, monkeypatch):
    """git 报错也算取不到证（不许当成通过）。"""
    monkeypatch.setattr(uoc, "run_cmd", lambda *a, **k: _FakeRun("", returncode=128))
    r = uoc.check_repo_state(targets=[("X", _repo(tmp_path), "main")])
    assert r["ok"] is None


def test_repo_state_registered_in_checks(tmp_path, monkeypatch):
    """检查项必须真的被注册 —— 「已实现但没人调」是本项目反复踩的坑。"""
    src = SRC.read_text(encoding="utf-8")
    assert '"仓库状态": check_repo_state()' in src


# ════════════════════ G. 静默桩检查（2026-10-11 新增）════════════════════


def _stub_env(tmp_path, monkeypatch):
    """让 check_silent_stubs 的前置条件成立：脚本存在 + 至少一个仓库根存在。"""
    sd = tmp_path / "scripts"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "silent_stub_scan.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(uoc, "SCRIPT_DIR", sd)
    monkeypatch.setattr(uoc, "_HOME_DIR", tmp_path)
    (tmp_path / "WorkBuddy" / "Claw").mkdir(parents=True, exist_ok=True)


def test_silent_stub_check_reports_reds(tmp_path, monkeypatch):
    """🔴 = 被调用却什么都不做 → 必须报，且把命中行带出来。"""
    _stub_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        uoc,
        "run_cmd",
        lambda *a, **k: _FakeRun("  🔴 a.py:1  f()  kind=bare_comment  calls=2\n", returncode=1),
    )
    r = uoc.check_silent_stubs()
    assert r["ok"] is False
    assert "静默桩" in r["alerts"][0] and "f()" in r["alerts"][0]


def test_silent_stub_check_clean_repo_is_ok(tmp_path, monkeypatch):
    """🟠/🟡 不算 —— 只报 🔴（否则空对象 close / 模板方法基类会变成恒真噪音）。"""
    _stub_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        uoc,
        "run_cmd",
        lambda *a, **k: _FakeRun("  🟠 cache.py:130  close()  kind=pass  calls=18\n", returncode=0),
    )
    assert uoc.check_silent_stubs()["ok"] is True


def test_silent_stub_check_rc2_is_skip(tmp_path, monkeypatch):
    """rc=2 = 路径不可读 → ok=None，**不计入 ✅**（判不了就不许当通过）。"""
    _stub_env(tmp_path, monkeypatch)
    monkeypatch.setattr(uoc, "run_cmd", lambda *a, **k: _FakeRun("", returncode=2))
    r = uoc.check_silent_stubs()
    assert r["ok"] is None and "取不到证" in r["note"]


def test_silent_stub_check_missing_script_is_skip(tmp_path, monkeypatch):
    monkeypatch.setattr(uoc, "SCRIPT_DIR", tmp_path / "no-scripts")
    r = uoc.check_silent_stubs()
    assert r["ok"] is None and "取不到证" in r["note"]


def test_silent_stub_check_registered(tmp_path):
    """检查项必须真被注册 —— 「已实现但没人调」是本项目反复踩的坑。"""
    src = SRC.read_text(encoding="utf-8")
    assert '"静默桩": check_silent_stubs()' in src


# ════════════ H. 工程质量·测试子信号不得静默降级（2026-10-11）════════════


def _qc_with(monkeypatch, pytest_stdout: str, rc: int = 0):
    """只替换 pytest 那一次调用，其余子信号返回空成功。"""

    def _run(cmd, **k):
        if "pytest" in " ".join(map(str, cmd)):
            return _FakeRun(pytest_stdout, returncode=rc)
        return _FakeRun("", returncode=0)

    monkeypatch.setattr(uoc, "run_cmd", _run)
    return uoc.check_code_quality()


def test_quality_note_reports_count_when_parseable(monkeypatch):
    r = _qc_with(monkeypatch, "..... 889 passed in 57.58s\n")
    assert "测试: 889 passed" in r["note"]


def test_quality_note_does_not_render_unknown_as_pass(monkeypatch):
    """回归：拿不到统计行时原写法落成 "?"，渲染出来是 `✅ … 测试: ? passed` —— 看着像通过。

    真实触发：用**没有 pytest 的解释器**跑本脚本（sys.executable 决定），
    pytest 只回 `No module named pytest`，没有任何统计行。
    """
    r = _qc_with(monkeypatch, "python3: No module named pytest\n", rc=1)
    assert "无法判定" in r["note"], r["note"]
    assert "? passed" not in r["note"], "不许把「判不了」写成「通过了」"
    assert "不等于通过" in r["note"]


def test_quality_note_reports_failures(monkeypatch):
    r = _qc_with(monkeypatch, "3 failed, 10 passed in 1.0s\n")
    assert "10 passed / 3 failed" in r["note"]


# ════════════ I. 上游 RSS 实况必须"连调度一起报"（2026-10-11）════════════


def test_rss_upstream_facts_returns_schedule_and_last_run(tmp_path, monkeypatch):
    """回归：原文案写死「仍在**每日**调度」，而它是**工作日**调度 →
    周日读到「产出停在周五」就像停摆（实测害我白追一轮）。
    现在改为现查 DB：调度与最近运行必须一起给出，取不到就明说。
    """
    import sqlite3 as _sq

    (tmp_path / ".workbuddy").mkdir(exist_ok=True)
    db = tmp_path / ".workbuddy" / "workbuddy.db"   # 与 _rss_upstream_facts 的 Path.home()/".workbuddy"/… 对齐
    conn = _sq.connect(db)
    conn.execute("CREATE TABLE automations (id TEXT PRIMARY KEY, rrule TEXT, deleted_at INTEGER)")
    conn.execute("CREATE TABLE automation_runs (automation_id TEXT, created_at INTEGER)")
    conn.execute(
        "INSERT INTO automations VALUES (?,?,NULL)",
        (uoc.WECHAT_RSS_SYNC_ID, "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=7;BYMINUTE=30"),
    )
    # ⚠️ created_at 是**毫秒**（2026-10-09 07:30 +08:00 = 1791502200000 附近）
    conn.execute(
        "INSERT INTO automation_runs VALUES (?,?)",
        (uoc.WECHAT_RSS_SYNC_ID, 1791502200 * 1000),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(uoc.Path, "home", staticmethod(lambda: tmp_path))
    sched, last = uoc._rss_upstream_facts()
    assert "BYDAY=MO,TU,WE,TH,FR" in sched, sched
    # ⚠️ **不许写死日期字符串**：CI runner 是 UTC、本机是 +08 ——
    #    写死 "2026-10-09" 的断言会「本地绿、CI 恒红」（本提交实测踩到，Unit Tests 判红）。
    #    改为：按**与生产同一套本地时区换算**重建期望值再比；这同时钉住"毫秒口径"
    #    （若误把毫秒当秒解，会得到公元 5.8 万年那种值，比不过）。
    import datetime as _dt

    want = _dt.datetime.fromtimestamp(1791502200).strftime("%Y-%m-%d %H:%M")
    assert last == want, (last, want)
    assert last.startswith("2026"), last  # 粗略但与时区无关：钉住"不是秒口径解出来的远古日期"


def test_rss_upstream_facts_never_invents_when_unreadable(tmp_path, monkeypatch):
    """取不到时必须明说"取不到"，**不许用旧文字充数**（那正是腐烂的来源）。"""
    monkeypatch.setattr(uoc.Path, "home", staticmethod(lambda: tmp_path))
    sched, last = uoc._rss_upstream_facts()
    assert "取不到" in sched and "取不到" in last


# ════════ J. 结构性守卫：全命令失败时，依赖命令取证的检查不得报 ✅（2026-10-11）════════


# 白名单：**按设计**允许在"命令全失败"时仍 ok=True 的检查（每条必须写明理由）
_ALLOW_OK_ON_TOTAL_FAILURE = {
    # docstring 写明：它只做"趋势可见性"（记录到状态锚），**绝不返回会触发中枢推送的 alert** ——
    # 否则关键失败会被 watchdog(1785506975961) 与中枢整体报告双发（两套去重键不同，首轮必双推）。
    "check_automation_failures",
}


def _checks_using_run_cmd() -> list[str]:
    r"""用 AST 找出源码里调用过 run_cmd 的 check_\* —— 这样将来新加的检查会被自动纳入守卫。"""
    import ast
    import re as _re

    src = SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines()
    names = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("check_"):
            body = "\n".join(lines[node.lineno - 1 : node.end_lineno])
            if _re.search(r"\brun_cmd\(", body):
                names.append(node.name)
    return sorted(names)


def test_no_check_claims_pass_when_every_command_fails(monkeypatch):
    """把所有外部命令都弄失败 → 靠命令取证的检查**不得报 ok=True**。

    实测（2026-10-11）抓到 4 个同款缺陷，均已修：
      · `check_disk`                 —— df 失败 / **路径不存在** / 输出不可解析 → 报"磁盘正常"
      · `check_qts_api_auth`         —— docker logs 失败 → 报"没找到 401" = 通过
      · `check_qts_job_write_health` —— 同上，且「无日志输出」原**显式**写 ok=True
      · `check_code_quality`         —— bandit 没跑起来 → 安全检查压根没验，却仍 ✅
    这些都是"取不到证却渲染成通过"，与 `测试: ? passed` 同族。
    """

    class _Fail:
        returncode = 127
        stdout = ""
        stderr = "command not found"

    monkeypatch.setattr(uoc, "run_cmd", lambda *a, **k: _Fail())

    def _call_or_exc(name: str) -> dict:
        """抛异常 = 响亮失败，不属于"静默报通过" → 用哨兵值表示，不当作 offender。"""
        try:
            return getattr(uoc, name)()
        except Exception as e:  # noqa: BLE001
            return {"ok": "EXC", "alerts": [str(e)[:60]]}

    offenders = [
        name
        for name in _checks_using_run_cmd()
        if name not in _ALLOW_OK_ON_TOTAL_FAILURE and _call_or_exc(name).get("ok") is True
    ]

    assert not offenders, (
        "以下检查在「所有外部命令都失败」时仍报 ok=True —— 属「取不到证却渲染成通过」：\n"
        + "\n".join(f"  - {n}" for n in offenders)
        + "\n修法：拿不到证据时返回 ok=None（显式跳过，不计入 ✅），或在白名单里写明按设计的理由。"
    )


def test_guard_would_catch_a_regression(monkeypatch):
    """自证：把 check_disk 改回"df 失败就静默通过"，守卫必须抓到 —— 否则它是"看着在跑其实没查"。"""
    import re as _re

    real_src = SRC.read_text(encoding="utf-8")
    # 直接构造一个同形态的坏检查，验证判定逻辑（不动真文件）
    def bad_check() -> dict:
        alerts: list = []
        r = uoc.run_cmd(["df", "-P", "/nonexistent"], timeout=5)
        if r.returncode == 0:
            alerts.append("x")
        return {"ok": len(alerts) == 0, "alerts": alerts}

    class _Fail:
        returncode = 127
        stdout = ""
        stderr = ""

    monkeypatch.setattr(uoc, "run_cmd", lambda *a, **k: _Fail())
    assert bad_check()["ok"] is True, "坏检查在命令失败时报通过（这就是守卫要抓的形态）"
    assert _re.search(r"\brun_cmd\(", "def check_disk():\n    r = run_cmd([])") is not None
    assert "check_disk" in real_src  # 真文件里存在该检查
