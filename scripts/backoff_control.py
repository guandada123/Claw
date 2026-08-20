#!/usr/bin/env python3
"""
backoff_control.py — 跨项目共享退避控制器 (Claw / T3 落地)

设计依据: ~/.workbuddy/skills/long-running-agent-control-plane/SKILL.md 原则4
  - 依赖外部 API 的自动化设「同身份共享退避」
  - 连续失败递增: 15 → 30 → 60 → 120 min
  - 权威退避表 = ~/.workbuddy/cross_project_state.json -> backoff_state.shared_identities
  - todo/gate/evidence 任一变化即重置 cadence (由 record_backoff_success 体现)

用法 (两种):
  Python:  from backoff_control import check_backoff, record_backoff_fail, record_backoff_success
  CLI:     python3 scripts/backoff_control.py check   <identity> [--state PATH]
           python3 scripts/backoff_control.py fail    <identity> [--state PATH]
           python3 scripts/backoff_control.py success <identity> [--state PATH]
           python3 scripts/backoff_control.py --selftest

CLI 退出码:
  check:   0 = 允许执行(skip=False)  1 = 应跳过(skip=True)
  fail/success: 0 = 写回成功
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone

# —— 配置 ——
DEFAULT_STATE = os.path.expanduser("~/.workbuddy/cross_project_state.json")
LADDER_MIN = [15, 30, 60, 120]  # 递增退避阶梯 (分钟)
BASE_INTERVAL = LADDER_MIN[0]  # 首次失败后基准间隔
MAX_INTERVAL = LADDER_MIN[-1]
DEFAULT_MAX = MAX_INTERVAL

PREAMBLE_SNIPPET = """\
# 🔴 共享退避铁律 (stateful backoff)
在调用任何外部 API 前，先运行退避检查；失败/成功必须回写，禁止空转烧 token：
  OUT=$(python3 /Users/guan/WorkBuddy/Claw/scripts/backoff_control.py check {identity})
  SKIP=$(echo "$OUT" | python3 -c "import sys,json;print(json.load(sys.stdin)['skip'])")
  if [ "$SKIP" = "True" ]; then echo "BACKOFF: skip (wait $(echo "$OUT" | python3 -c 'import sys,json;print(json.load(sys.stdin)["wait_min"])')min)"; exit 0; fi
  # ... 执行外部 API 调用 ...
  # 调用失败: python3 /Users/guan/WorkBuddy/Claw/scripts/backoff_control.py fail {identity}
  # 调用成功: python3 /Users/guan/WorkBuddy/Claw/scripts/backoff_control.py success {identity}
"""


# —— 底层读写 (atomic) ——
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(state_path: str) -> dict:
    try:
        with open(state_path, encoding="utf-8") as f:
            return json.load(f)  # type: ignore[no-any-return]
    except FileNotFoundError:
        return {"backoff_state": {"shared_identities": {}}}
    except json.JSONDecodeError as e:
        raise RuntimeError(f"状态锚 JSON 解析失败: {state_path} -> {e}") from e


def _atomic_write(state_path: str, data: dict) -> None:
    d = os.path.dirname(state_path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, state_path)  # 原子替换，避免半写
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _ensure_state_shape(data: dict) -> dict:
    data.setdefault("backoff_state", {})
    data["backoff_state"].setdefault("shared_identities", {})
    data["backoff_state"].setdefault(
        "rule",
        "同identity下连续失败共享退避(15→30→60→120min)；todo/gate/evidence任一变化即重置cadence",
    )
    return data


def _ensure_identity(identities: dict, identity: str) -> dict:
    if identity not in identities:
        identities[identity] = {
            "consecutive_failures": 0,
            "current_interval_min": BASE_INTERVAL,
            "max_interval_min": DEFAULT_MAX,
            "last_fail": None,
        }
    return identities[identity]  # type: ignore[no-any-return]


# —— 核心 API ——
def check_backoff(identity: str, state_path: str = DEFAULT_STATE) -> dict:
    """返回是否应跳过本次执行 + 剩余等待分钟。
    自动登记未知 identity (consecutive_failures=0 -> 立即允许)。"""
    data = _ensure_state_shape(_load(state_path))
    ids = data["backoff_state"]["shared_identities"]
    entry = _ensure_identity(ids, identity)

    if entry["consecutive_failures"] == 0 or entry["last_fail"] is None:
        return {
            "identity": identity,
            "skip": False,
            "wait_min": 0,
            "interval_min": entry["current_interval_min"],
        }

    last = datetime.fromisoformat(entry["last_fail"])
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 60.0
    interval = entry["current_interval_min"]
    if elapsed >= interval:
        return {"identity": identity, "skip": False, "wait_min": 0, "interval_min": interval}
    return {
        "identity": identity,
        "skip": True,
        "wait_min": round(interval - elapsed, 1),
        "interval_min": interval,
    }


def record_backoff_fail(identity: str, state_path: str = DEFAULT_STATE) -> dict:
    """记录一次失败：consecutive+1，按阶梯提升当前间隔。"""
    data = _ensure_state_shape(_load(state_path))
    ids = data["backoff_state"]["shared_identities"]
    entry = _ensure_identity(ids, identity)

    entry["consecutive_failures"] = entry.get("consecutive_failures", 0) + 1
    # 阶梯: 第1次失败=BASE, 之后每多一次升一档 (封顶 MAX)
    step = min(entry["consecutive_failures"], len(LADDER_MIN)) - 1
    max_i = entry.get("max_interval_min", DEFAULT_MAX)
    entry["current_interval_min"] = min(LADDER_MIN[step], max_i)
    entry["last_fail"] = _now_iso()

    _atomic_write(state_path, data)
    return entry


def record_backoff_success(identity: str, state_path: str = DEFAULT_STATE) -> dict:
    """记录一次成功：清零连续失败 + 间隔回到基准 (重置 cadence)。"""
    data = _ensure_state_shape(_load(state_path))
    ids = data["backoff_state"]["shared_identities"]
    entry = _ensure_identity(ids, identity)

    entry["consecutive_failures"] = 0
    entry["current_interval_min"] = BASE_INTERVAL
    entry["last_fail"] = None

    _atomic_write(state_path, data)
    return entry


def register_identity(
    identity: str, max_interval: int = DEFAULT_MAX, state_path: str = DEFAULT_STATE
) -> dict:
    """显式登记一个 identity (指定 max 上限)。已存在则仅补 max_interval。"""
    data = _ensure_state_shape(_load(state_path))
    ids = data["backoff_state"]["shared_identities"]
    entry = _ensure_identity(ids, identity)
    entry["max_interval_min"] = max_interval
    _atomic_write(state_path, data)
    return entry


# —— CLI ——
def _cli() -> int:
    args = sys.argv[1:]
    if not args:
        print(
            "usage: backoff_control.py [check|fail|success] <identity> [--state PATH] | --selftest",
            file=sys.stderr,
        )
        return 2
    if args[0] == "--selftest":
        return _selftest()

    cmd = args[0]
    if cmd not in ("check", "fail", "success", "register"):
        print(f"unknown command: {cmd}", file=sys.stderr)
        return 2

    identity = None
    state_path = DEFAULT_STATE
    max_interval = DEFAULT_MAX
    rest = args[1:]
    for i, a in enumerate(rest):
        if a == "--state":
            state_path = rest[i + 1]
        elif a == "--max":
            max_interval = int(rest[i + 1])
        elif identity is None:
            identity = a

    identity = None
    state_path = DEFAULT_STATE
    rest = args[1:]
    for i, a in enumerate(rest):
        if a == "--state":
            state_path = rest[i + 1]
        elif identity is None:
            identity = a
    if not identity:
        print("missing <identity>", file=sys.stderr)
        return 2

    if cmd == "check":
        res = check_backoff(identity, state_path)
        print(json.dumps(res, ensure_ascii=False))
        return 0 if not res["skip"] else 1
    if cmd == "fail":
        entry = record_backoff_fail(identity, state_path)
        print(json.dumps(entry, ensure_ascii=False))
        return 0
    if cmd == "success":
        entry = record_backoff_success(identity, state_path)
        print(json.dumps(entry, ensure_ascii=False))
        return 0
    if cmd == "register":
        entry = register_identity(identity, max_interval, state_path)
        print(json.dumps(entry, ensure_ascii=False))
        return 0
    return 2


def _selftest() -> int:
    import shutil

    fd, tmp = tempfile.mkstemp(suffix=".json")  # 替代 mktemp(B306): 随机路径,消除可预测竞态
    os.close(fd)
    os.remove(tmp)  # 保留"未占用唯一路径"语义(selftest 专用临时文件)
    try:
        # 1) 未知 identity 首次 check -> 允许
        r = check_backoff("test_a", tmp)
        assert r["skip"] is False, "unknown identity should be allowed"

        # 2) 第一次 fail -> consecutive=1, interval=BASE(15)
        e = record_backoff_fail("test_a", tmp)
        assert e["consecutive_failures"] == 1 and e["current_interval_min"] == 15

        # 3) 立刻 check -> 应跳过 (wait>0)
        r = check_backoff("test_a", tmp)
        assert r["skip"] is True and r["wait_min"] > 0

        # 4) 连续 fail 升级: 2次=30, 3次=60, 4次=120
        for n, exp in ((2, 30), (3, 60), (4, 120)):
            e = record_backoff_fail("test_a", tmp)
            assert e["consecutive_failures"] == n and e["current_interval_min"] == exp, (
                f"fail#{n} expect interval {exp}, got {e['current_interval_min']}"
            )

        # 5) 封顶: 第5次仍 120
        e = record_backoff_fail("test_a", tmp)
        assert e["current_interval_min"] == 120

        # 6) success 重置
        e = record_backoff_success("test_a", tmp)
        assert e["consecutive_failures"] == 0 and e["current_interval_min"] == 15
        r = check_backoff("test_a", tmp)
        assert r["skip"] is False

        # 7) atomic 写后文件可解析且含 identity
        with open(tmp, encoding="utf-8") as f:
            d = json.load(f)
        assert "test_a" in d["backoff_state"]["shared_identities"]

        print("BACKOFF SELFTEST: PASS (7/7)")
        return 0
    except AssertionError as ex:
        print(f"BACKOFF SELFTEST: FAIL -> {ex}")
        return 1
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


if __name__ == "__main__":
    sys.exit(_cli())
