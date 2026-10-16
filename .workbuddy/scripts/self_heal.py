#!/usr/bin/env python3
"""
self_heal.py — 跨项目 Docker 自愈执行（bounded, fail-safe）
===========================================================
读取 cross_project_state.json → monitoring.surfaces[].self_heal_allowlist，
对允许的无状态容器做健康检查；仅当容器非 running(Exited / Restarting 卡死)时
才 `docker restart`，带冷却(30min) + 震荡防护(同目标 60min 内 >2 次重启→停手仅告警)；
Up 但不健康 → 仅告警不重启(防抖动)。stateful 容器(见各 surface.stateful)硬排除。

设计原则（对齐跨项目控制面）：
  - 观察≠transition：docker 重启是真实外部动作，须记录到 self_heal_log 且输出报告。
  - fail-safe：docker 不可用 / JSON 损坏 / 单容器异常 → 不崩溃，记 alert 继续。
  - 不重复造轮子：Docker 原生 restart:unless-stopped 已做即时拉起；本脚本是
    二次确认 + 崩溃循环捕获 + 飞书可见性层（Docker 静默重启无通知）。

输出：JSON 报告到 stdout，供巡检/看门狗汇总 + 飞书告警。
self_heal_log 回写 cross_project_state.json（原子写）。
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import time
from pathlib import Path

STATE_FILE = str(Path.home() / ".workbuddy" / "cross_project_state.json")
COOLDOWN_MIN = 30  # 同目标冷却
OSC_LIMIT = 2  # 60min 内 >2 次重启 → 震荡防护停手
OSC_WINDOW_MIN = 60
VERIFY_ATTEMPTS = 3  # 重启后复核次数（2026-10-16 加）
VERIFY_INTERVAL_SEC = 3.0  # 每次复核间隔秒；单容器最大观察窗 ≈9s


def now() -> datetime.datetime:
    return datetime.datetime.now()


def _parse_ts(ts: str) -> datetime.datetime:
    try:
        return datetime.datetime.fromisoformat(ts)
    except Exception:
        return datetime.datetime.min


def load_state() -> dict:
    with open(STATE_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def docker_inspect(container: str):
    """返回 (status, health) 或 None（容器不存在 / docker 不可用）。"""
    try:
        out = subprocess.run(
            [
                "docker",
                "inspect",
                "-f",
                "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}",
                container,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return None
        parts = out.stdout.strip().split("|")
        status = parts[0]
        health = parts[1] if len(parts) > 1 else "n/a"
        return status, health
    except Exception:
        return None


def docker_restart(container: str) -> bool:
    try:
        r = subprocess.run(
            ["docker", "restart", container], capture_output=True, text=True, timeout=60
        )
        return r.returncode == 0
    except Exception:
        return False


def verify_running(container: str):
    """重启后复核容器是否真的恢复（2026-10-16 加）。

    返回 (ok, status, health)。ok=True 仅当复核期内容器回到 running。
    为什么必须复核：`docker restart` 返回 0 只代表「重启动作被受理」，
    崩溃循环容器（如运行期依赖缺失 → 起来即退）会立刻再次退出。原实现据此
    计入 restarted，被上游当作 healed/success → 「已下发」被误报为「已恢复」
    （假成功）。实例：QTS quant-execution 因 PyJWT 缺失崩溃循环，
    2026-10-16 当日被"成功自愈"5 次（02:17/05:21/11:34/14:40/17:44）而服务实际
    全程不可用；振荡防护窗口 60min 而重启间隔约 3h → 也永不触发。
    判定口径：status == "running" 即视为恢复（healthcheck 的 starting 属重启后
    正常态；长期 unhealthy 由既有的 unhealthy-but-running 分支负责告警）。
    """
    status, health = None, None
    for _ in range(VERIFY_ATTEMPTS):
        time.sleep(VERIFY_INTERVAL_SEC)
        info = docker_inspect(container)
        if info is None:
            status, health = None, None
            continue
        status, health = info
        if status == "running":
            return True, status, health
    return False, status, health


def main() -> int:
    try:
        state = load_state()
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"state load: {e}"}, ensure_ascii=False))
        return 1

    surfaces = state.get("monitoring", {}).get("surfaces", {})
    log = state.get("monitoring", {}).get("self_heal_log", []) or []
    now_dt = now()
    cutoff = now_dt - datetime.timedelta(minutes=OSC_WINDOW_MIN)
    log = [e for e in log if _parse_ts(e.get("ts", "")) > cutoff]

    restarted, alerts, skipped, checked = [], [], [], []

    for proj, surf in surfaces.items():
        allow = surf.get("self_heal_allowlist") or []
        stateful = set(surf.get("stateful") or [])
        for c in allow:
            checked.append(c)
            if c in stateful:
                skipped.append({"container": c, "reason": "stateful-never-auto-restart"})
                continue
            info = docker_inspect(c)
            if info is None:
                alerts.append({"container": c, "issue": "inspect-failed/not-in-docker"})
                continue
            status, health = info
            if status == "running" and health in ("healthy", "n/a", ""):
                continue  # 健康，无需动作
            recent = [e for e in log if e.get("container") == c]
            if len(recent) >= OSC_LIMIT:
                skipped.append(
                    {
                        "container": c,
                        "reason": "oscillation-guard",
                        "status": status,
                        "health": health,
                    }
                )
                alerts.append(
                    {"container": c, "issue": "oscillation-guard-stopped", "status": status}
                )
                continue
            if recent:
                last = _parse_ts(recent[-1].get("ts", ""))
                if (now_dt - last).total_seconds() < COOLDOWN_MIN * 60:
                    skipped.append(
                        {"container": c, "reason": "cooldown", "status": status, "health": health}
                    )
                    continue
            if status != "running":
                ok = docker_restart(c)
                # 2026-10-16：重启后必须复核（见 verify_running 文档）。
                #   仅当复核确认 running 才计入 restarted；否则升级为 alert，
                #   让「重启已下发」与「服务已恢复」在报告里分开。
                vok, vstatus, vhealth = (False, None, None)
                if ok:
                    vok, vstatus, vhealth = verify_running(c)
                log.append(
                    {
                        "ts": now_dt.isoformat(),
                        "container": c,
                        "action": "restart",
                        "ok": ok,
                        "status": status,
                        "health": health,
                        "verified": vok,
                        "verify_status": vstatus,
                        "verify_health": vhealth,
                    }
                )
                if ok and vok:
                    restarted.append(c)
                elif ok:
                    alerts.append(
                        {
                            "container": c,
                            "issue": "restart-ineffective-still-down",
                            "status": vstatus,
                            "health": vhealth,
                            "pre_status": status,
                        }
                    )
                else:
                    alerts.append({"container": c, "issue": "restart-failed", "status": status})
            else:
                # running 但不健康 → 仅告警，不重启（防抖动）
                alerts.append(
                    {
                        "container": c,
                        "issue": "unhealthy-but-running",
                        "status": status,
                        "health": health,
                    }
                )

    state.setdefault("monitoring", {})["self_heal_log"] = log
    try:
        save_state(state)
    except Exception:
        pass  # 回写失败不影响本次报告

    report = {
        "ok": True,
        "checked": checked,
        "restarted": restarted,
        "skipped": skipped,
        "alerts": alerts,
        "ts": now_dt.isoformat(),
    }
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
