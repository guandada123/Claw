"""`check_data_freshness` 的判据回归测试（2026-09-28 修复「中秋误报」后建立）。

背景（本次卡片告警的复盘）：
  09-25 是**中秋节休市**（+ 09-26/27 周末）→ 09-24 收盘后到 09-28 05:00 之间没有任何交易日，
  产物 84h 未刷新**是正常的**；而旧判据 `mt.date() < today - 1day` 是固定窗口、**不认节假日**，
  于是报出「数据管线可能停摆」（实测管线 09-23~09-28 每天 success=1，一天没落）。

本测试把告警现场**原样回放**，并覆盖双向：正常的节假日空档不许报，真实的停摆必须报。
"""
import datetime
import importlib.util
import pathlib
import sys
import types

import pytest

# 相对定位（不要写死本机绝对路径 —— CI runner 上没有 /Users/guan，collection 直接报错）
UOC = pathlib.Path(__file__).resolve().parent.parent / ".workbuddy" / "scripts" / "unified_ops_center.py"
spec = importlib.util.spec_from_file_location("uoc_fresh", UOC)
uoc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uoc)

WHITELIST = ["qts_daily_signals.json", "qts_regime.json",
             "signal_consensus.json", "source_weights.json"]

# 固定休市日历（含 2026-09-25 中秋节）。
# 刻意**不读仓库里的 .workbuddy/data/astock_holidays.json**：该文件被 .gitignore 排除，
# CI / fresh clone 上没有 → 读它等于让测试只在开发机通过（2026-09-28 CI 实测 6 例全红）。
CAL = {"dates": {"2026-09-25"}, "names": {"2026-09-25": "中秋节"}}


@pytest.fixture(autouse=True)
def fake_calendar(monkeypatch):
    """注入日历来源：**只替换 load_holidays，交易日判定仍走真实现**（不测替身）。"""
    scripts = str(UOC.parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    real = importlib.import_module("is_trading_day")
    stub = types.ModuleType("is_trading_day")
    stub.load_holidays = lambda *a, **k: dict(CAL)
    stub.is_trading_day = real.is_trading_day
    monkeypatch.setitem(sys.modules, "is_trading_day", stub)
    return stub


def make_root(tmp_path, mtimes: dict[str, datetime.datetime]):
    root = tmp_path / "data"
    root.mkdir(parents=True)
    for fn in WHITELIST:
        p = root / fn
        p.write_text("{}", encoding="utf-8")
        mt = mtimes.get(fn)
        if mt is not None:
            ts = mt.timestamp()
            import os
            os.utime(p, (ts, ts))
    return root


def dt(*args) -> datetime.datetime:
    return datetime.datetime(*args)


# ── ① 回放本次告警：中秋+周末的正常空档**不许报** ─────────────────
def test_midautumn_gap_is_not_stale(tmp_path):
    """09-28 03:16（告警时刻）· 产物最后更新 = 09-24 15:13（上一个交易日）。

    这是本次卡片的原样回放：旧判据在这里报「停摆」，新判据应判**正常**。
    """
    root = make_root(tmp_path, {fn: dt(2026, 9, 24, 15, 13) for fn in WHITELIST})
    r = uoc.check_data_freshness(now=dt(2026, 9, 28, 3, 16), root=root)
    assert r["ok"] is True, r["note"]
    assert "2026-09-24 15:05" in r["note"]      # 依据可追溯


# ── ② 真停摆必须报（修复不能把检查器修成永远绿）──────────────────
def test_real_stall_still_reported(tmp_path):
    """09-28 16:30（当天 15:05 槽应已跑完）· 产物仍停在 09-24 → **必须报**。"""
    root = make_root(tmp_path, {fn: dt(2026, 9, 24, 15, 13) for fn in WHITELIST})
    r = uoc.check_data_freshness(now=dt(2026, 9, 28, 16, 30), root=root)
    assert r["ok"] is False
    assert "陈旧" in r["note"] and "停摆" in r["alerts"][0]


# ── ③ 交易日隔日同刻不报（防把日常节奏当故障）────────────────────
def test_previous_trading_day_is_fine(tmp_path):
    """09-29 03:16 · 产物 09-28 15:13 → 正常（当天 15:05 槽已跑完，次日 05:05 尚未到）。"""
    root = make_root(tmp_path, {fn: dt(2026, 9, 28, 15, 13) for fn in WHITELIST})
    assert uoc.check_data_freshness(now=dt(2026, 9, 29, 3, 16), root=root)["ok"] is True


# ── ④ 缺失文件照旧要报（别被新逻辑吞掉）──────────────────────────
def test_missing_file_reported(tmp_path):
    root = make_root(tmp_path, {})
    (root / "qts_regime.json").unlink()
    r = uoc.check_data_freshness(now=dt(2026, 9, 28, 16, 30), root=root)
    assert r["ok"] is False and "缺失" in r["note"]


# ── ⑤ 宽限期：槽位时刻 +90min 内，仍按「上一交易日 15:05」判 → 不报 ──────
def test_grace_period_respected(tmp_path):
    """09-28 05:30 时，今天 05:05 那个槽位刚过 25 分钟（管线要跑 ~9min + 编排抖动）
    → 期望时点仍停在 09-24 15:05，产物 09-24 15:13 判**正常**。"""
    root = make_root(tmp_path, {fn: dt(2026, 9, 24, 15, 13) for fn in WHITELIST})
    assert uoc.check_data_freshness(now=dt(2026, 9, 28, 5, 30), root=root)["ok"] is True


def test_after_grace_it_becomes_stale(tmp_path):
    """同一批陈旧产物：到 07:00（05:05 槽已过宽限）就该报 —— 宽限不是无限容忍。"""
    root = make_root(tmp_path, {fn: dt(2026, 9, 24, 15, 13) for fn in WHITELIST})
    assert uoc.check_data_freshness(now=dt(2026, 9, 28, 7, 0), root=root)["ok"] is False


# ── ⑥ 日历不可用：降级但**必须标出来**（取不到依据 ≠ 新鲜）────────
def test_calendar_unavailable_is_marked_degraded(tmp_path, monkeypatch):
    monkeypatch.setattr(uoc, "_last_expected_refresh", lambda now: (None, "calendar_unavailable(ImportError)"))
    root = make_root(tmp_path, {fn: dt(2026, 9, 24, 15, 13) for fn in WHITELIST})
    r = uoc.check_data_freshness(now=dt(2026, 9, 28, 3, 16), root=root)
    assert r["ok"] is False                       # 降级窗口下 3 天前 → 仍报
    assert "降级判断" in r["note"] and "日历不可用" in r["note"]

    # 降级时不能把「1 天前」误报（沿用旧窗口的容忍度）；
    # 但**必须仍在文案里标明这是降级判断** —— 取不到依据 ≠ 数据新鲜。
    root2 = make_root(tmp_path / "b", {fn: dt(2026, 9, 27, 15, 13) for fn in WHITELIST})
    r2 = uoc.check_data_freshness(now=dt(2026, 9, 28, 3, 16), root=root2)
    assert r2["ok"] is True
    assert "降级判断" in r2["note"], "降级必须留痕：读者要知道这次不是按交易日历判的"


# ── ⑦ 日历加载器 sys.exit(2) 时不许把检查器带走（CI 实测踩到）──────────
def test_calendar_loader_sys_exit_degrades_not_dies(tmp_path, fake_calendar):
    """`is_trading_day.load_holidays()` 在文件缺失/JSON 坏时**调 sys.exit(2)**。

    SystemExit 继承自 BaseException 而非 Exception → `except Exception` 接不住，
    会把整个中枢进程带走（2026-09-28 CI：fresh clone 无 astock_holidays.json）。
    正确行为：降级 + 在文案里标明降级，而不是炸掉调用方。
    """
    def boom(*a, **k):  # noqa: ARG001
        raise SystemExit(2)

    fake_calendar.load_holidays = boom
    root = make_root(tmp_path, {fn: dt(2026, 9, 24, 15, 13) for fn in WHITELIST})
    r = uoc.check_data_freshness(now=dt(2026, 9, 28, 3, 16), root=root)   # 不许抛 SystemExit
    assert r["ok"] is False
    assert "降级判断" in r["note"] and "calendar_unavailable(SystemExit)" in r["note"]


# ── ⑧ 日历文件缺失：给得出原因，好过让下游 sys.exit ────────────────────
def test_calendar_file_missing_names_the_reason(tmp_path, fake_calendar):
    fake_calendar.HOLIDAYS_FILE = str(tmp_path / "astock_holidays.json")   # 故意不存在
    root = make_root(tmp_path, {fn: dt(2026, 9, 24, 15, 13) for fn in WHITELIST})
    r = uoc.check_data_freshness(now=dt(2026, 9, 28, 3, 16), root=root)
    assert r["ok"] is False
    assert "calendar_file_missing(astock_holidays.json)" in r["note"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "--no-header", "-p", "no:cacheprovider"]))
