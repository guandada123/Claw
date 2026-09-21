#!/usr/bin/env python3
"""
自动化健康检查脚本
扫描所有自动化任务，检查运行状态、产出时间、异常情况
输出: JSON 摘要 + 可选飞书告警

用法:
    python3 automation_health.py           # 终端输出 + 写 JSON
    python3 automation_health.py --json    # JSON only
    python3 automation_health.py --alert   # 异常时飞书告警
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

DB_PATH = Path.home() / ".workbuddy" / "workbuddy.db"
DATA_DIR = Path(__file__).parent.parent / "data"
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK", "")


def get_automations() -> list:
    """从 SQLite 读取所有活跃自动化"""
    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}", file=sys.stderr)
        return []

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 获取所有自动化
    cur.execute("""
        SELECT a.id, a.name, a.status, a.schedule_type, a.rrule,
               a.last_run_at, a.next_run_at, a.model_id, a.model_is_thinking
        FROM automations a
        WHERE a.deleted_at IS NULL
        ORDER BY a.name
    """)
    automations = [dict(r) for r in cur.fetchall()]

    for auto in automations:
        # 最近运行记录
        # 2026-09-02：补取 result_success / metadata_json。
        # 判「运行被中断」必须靠 metadata 里的 interrupted / resultState ——
        # 平台把被中断的 run 仍记为 status=ACCEPTED，只看 status 永远抓不到。
        cur.execute(
            """
            SELECT thread_id, status, created_at, result_success, metadata_json
            FROM automation_runs
            WHERE automation_id = ?
            ORDER BY created_at DESC LIMIT 5
        """,
            (auto["id"],),
        )
        auto["recent_runs"] = [dict(r) for r in cur.fetchall()]

        # 运行时状态
        cur.execute(
            """
            SELECT last_error, running, running_started_at
            FROM automation_runtime_state
            WHERE automation_id = ?
        """,
            (auto["id"],),
        )
        state = cur.fetchone()
        if state:
            auto["last_error"] = state["last_error"]
            auto["running"] = bool(state["running"])

    conn.close()
    return automations


def categorize(automations: list) -> dict:
    """按业务分类"""
    cats = {
        "投顾操盘": [],
        "炒股助理": [],
        "美股监控": [],
        "系统维护": [],
        "桥接监控": [],
        "其他": [],
    }

    for a in automations:
        name = a.get("name", "")
        if any(k in name for k in ["投顾", "模拟", "选股"]):
            cats["投顾操盘"].append(a)
        elif any(
            k in name for k in ["助理", "盘前", "收盘", "持仓", "财报", "早报", "公众号", "投资"]
        ):
            cats["炒股助理"].append(a)
        elif "美股" in name:
            cats["美股监控"].append(a)
        elif any(k in name for k in ["健康", "清理", "索引", "审计", "记忆"]):
            cats["系统维护"].append(a)
        elif any(k in name.lower() for k in ["marvis", "bridge", "桥接"]):
            cats["桥接监控"].append(a)
        else:
            cats["其他"].append(a)

    return cats


def _parse_unix(ts) -> datetime | None:
    """解析 Unix 时间戳（毫秒）"""
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts) / 1000)
    except Exception:
        return None


def _extract_byday(rrule: str) -> set:
    """从 RRULE 中提取 BYDAY 工作日集合，如 'MO,TU,WE' -> {'MO','TU','WE'}"""
    if not rrule:
        return set()
    m = re.search(r"BYDAY=([^;]+)", rrule)
    if not m:
        return set()
    return {d.strip().upper() for d in m.group(1).split(",") if d.strip()}


def check_health(auto: dict) -> dict:
    """检查单个自动化健康状态"""
    name = auto.get("name", "Unknown")
    status = auto.get("status", "UNKNOWN")
    runs = auto.get("recent_runs", [])
    last_error = auto.get("last_error", "")
    running = auto.get("running", False)

    # 最近运行时间取「平台回写的 last_run_at」与「automation_runs 表最新派发记录」的较大值。
    # 实测 2026-08-31：59 个 ACTIVE 自动化中 52 个的 last_run_at 滞后（滞后量恰为各自调度
    # 周期的整数倍），且这些自动化最近一次运行的状态均为 ACCEPTED —— 平台只在 run 走到
    # PENDING_REVIEW 等终态时才回写 last_run_at，托管自动执行（ACCEPTED）不回写，于是该字段
    # 集体冻结在若干周期前。automation_runs 每次派发都会落记录，是权威运行时间，故以它为准。
    # 若只用 last_run_at，静默失败判定的 gap 基线整体偏大，会把正常运行的任务误报为 🔴。
    def _to_ms(v) -> int:
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0

    platform_last_ts = _to_ms(auto.get("last_run_at"))
    newest_run_ts = _to_ms(runs[0].get("created_at")) if runs else 0
    field_lag_h = (
        (newest_run_ts - platform_last_ts) / 3600000
        if newest_run_ts and platform_last_ts
        else 0.0
    )
    last_run_ts = max(platform_last_ts, newest_run_ts)

    health = "🟢"
    issues = []

    # 状态检查
    if status == "PAUSED":
        health = "🟡"
        issues.append("已暂停")
    elif status != "ACTIVE":
        health = "🔴"
        issues.append(f"状态异常: {status}")

    # 运行记录检查
    last_run_interrupted = False
    if runs:
        last_status = runs[0].get("status", "")
        if last_status == "failed":
            health = "🔴"
            issues.append("最后一次运行失败")
        elif last_status == "timeout":
            health = "🟡"
            issues.append("超时")

        # ── 运行被中断检测（2026-09-02 新增，原为漏检盲区）──
        # 背景：平台把「跑到一半被宿主中断」的 run 记为 status=ACCEPTED 而非 failed，
        #   于是上面的 last_status 分支抓不到，只能落到下方模糊的「历史错误」，
        #   与早已修复的历史残留同级，真故障被噪音淹没。
        # 实证（2026-09-01/02）：5 个自动化最近一次 run 的 metadata 为
        #   interrupted=True / resultState=partial_delivered / resultEvidence=none，
        #   且共享同一 conversationId、finishedAt 时间戳完全相同 —— 宿主级批量中断，
        #   任务白跑、产物未落盘。这是**真故障**，须按 🔴 显式报出。
        # 判据取 metadata 而非 status：status 是平台终态口径，对此类中断不敏感。
        md = runs[0].get("metadata_json") or ""
        interrupted = False
        result_state = ""
        try:
            m = json.loads(md) if isinstance(md, (str, bytes)) else (md or {})
            if isinstance(m, dict):
                interrupted = str(m.get("interrupted", "")).lower() == "true"
                result_state = str(m.get("resultState", "") or "")
        except Exception:  # noqa: BLE001
            pass
        if interrupted or result_state == "partial_delivered":
            last_run_interrupted = True
            health = "🔴"
            issues.append(
                f"最近运行被中断({result_state or 'interrupted'}，产物未落盘)"
            )
        elif runs[0].get("result_success") in (0, "0", False):
            # 非中断但平台标记未成功
            health = "🔴" if health == "🟢" else health
            issues.append("最近运行未成功(result_success=0)")

    # last_run_at 字段滞后：真实运行时间已用 runs 表校正，该字段不参与健康判定。
    # 2026-09-02 初版曾「仅当滞后 > 7 天才提示」，但实测 last_run_at 在平台侧对所有
    #   ACCEPTED 托管运行集体冻结（~24 天前），552–576h 滞后成为常态而非异常，
    #   导致 60+ 个**正常运行**的自动化被塞入「滞后552h」噪音，与真故障同级展示、掩盖信号。
    # 2026-09-21 修复：该滞后属平台字段同步特性（非任务故障），彻底移出 issues；
    #   健康判定完全交给 runs 表校正后的真实运行时间，field_lag_h 仅留 JSON 供排查。

    # 静默失败检测（根据调度频率调整阈值）
    last_run = _parse_unix(last_run_ts)
    if last_run:
        hours_ago = (datetime.now() - last_run).total_seconds() / 3600
        schedule = auto.get("schedule_type", "recurring")
        rrule = auto.get("rrule", "") or ""

        # 根据调度频率设定不同阈值（防月/周度任务误报 stale）
        if "MONTHLY" in rrule or "每月" in name:
            threshold = 744  # 31天（最大月窗口+1天缓冲）
        elif "WEEKLY" in rrule or "每周" in name:
            threshold = 192  # 8天（1周+1天缓冲）
        elif "YEARLY" in rrule or "每年" in name:
            threshold = 8760  # 365天（年度任务）
        elif schedule == "once":
            threshold = 99999  # 一次性任务不报
        else:
            threshold = 48  # 默认48h

        # BYDAY 仅工作日(MO-FR)时，周末天然不调度，最长间隔含整个周末(~49h)
        # 若不放大阈值会把周末间隔误判为 critical（如 FREQ=HOURLY;BYDAY=MO-FR）
        byday = _extract_byday(rrule)
        WEEKDAYS = {"MO", "TU", "WE", "TH", "FR"}
        if byday and byday.issubset(WEEKDAYS):
            threshold = max(threshold, 72)  # 放宽到 72h 覆盖周五→周一

        # 若平台已算出未来的 next_run_at，说明调度正常（尊重 BYDAY/节假日），非静默失败
        # 这是最权威的判定：跨周末/节假日间隔不误报；仅当 next_run_at 缺失/过期才走 gap 回退
        next_run = _parse_unix(auto.get("next_run_at", 0))
        if next_run and next_run > datetime.now():
            stale = False  # 已正确排期，跳过静默失败判定
        else:
            stale = hours_ago > threshold or hours_ago > threshold * 0.6

        if stale:
            if hours_ago > threshold:
                health = "🔴" if health == "🟢" else health
            else:
                health = "🟡" if health == "🟢" else health
            issues.append(f"{int(hours_ago)}h未运行")

    # 运行中
    if running:
        health = "🔵" if health == "🟢" else health
        issues.append("运行中")

    # 错误信息
    # 2026-09-02：若最近一次运行已判定为「中断」，runtime.last_error 与之同源
    # （内容就是该次中断信息），不再重复计一条「历史错误」，避免同一根因双计刷屏。
    if last_error:
        if last_run_interrupted and "interrupt" in last_error.lower():
            pass  # 同源，已由「最近运行被中断」覆盖
        else:
            health = "🟡" if health == "🟢" else health
            issues.append(f"历史错误: {last_error[:40]}")

    # 计算24h运行次数
    run_24h = 0
    for r in runs:
        rt = _parse_unix(r.get("created_at"))
        if rt and (datetime.now() - rt).total_seconds() < 86400:
            run_24h += 1

    return {
        "name": name,
        "status": status,
        "health": health,
        "last_run": last_run.isoformat() if last_run else None,
        "issues": issues,
        "run_count_24h": run_24h,
        "running": running,
        # 诊断字段（2026-09-02）：滞后量移出告警文案但仍留痕，供排查字段同步问题
        "field_lag_h": round(field_lag_h, 1),
        "last_run_interrupted": last_run_interrupted,
    }


def generate_report(summary: dict) -> str:
    """生成汇总报告文本"""
    lines = []
    lines.append(f"⚙️ 自动化健康 | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("━━━━━━━━━━━━━━━━━━━━")

    total = summary["total_count"]
    h = summary["healthy_count"]
    w = summary["warning_count"]
    c = summary["critical_count"]

    lines.append(f"总计 {total} | 🟢{h} 🟡{w} 🔴{c}")
    lines.append("")

    for cat, items in summary["by_category"].items():
        if not items:
            continue
        cat_issues = [i for i in items if i["health"] in ("🟡", "🔴")]
        lines.append(f"📂 {cat} ({len(items)}个, {len(cat_issues)}异常)")
        for item in items:
            if item["health"] in ("🟡", "🔴"):
                lines.append(f"  {item['health']} {item['name']}: {', '.join(item['issues'])}")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def send_feishu_alert(summary: dict) -> bool:
    """发送飞书告警消息"""
    if not FEISHU_WEBHOOK:
        print("⚠️ FEISHU_WEBHOOK 未配置，跳过告警", file=sys.stderr)
        return False

    total = summary["total_count"]
    h = summary["healthy_count"]
    w = summary["warning_count"]
    c = summary["critical_count"]

    # 仅异常时发送
    if w == 0 and c == 0:
        print("✅ 所有自动化健康，跳过告警")
        return True

    # 构造飞书富文本消息卡片
    issue_lines = []
    for cat, items in summary["by_category"].items():
        bad = [i for i in items if i["health"] in ("🟡", "🔴")]
        if not bad:
            continue
        for item in bad:
            tag = "🔴" if item["health"] == "🔴" else "🟡"
            issue_lines.append(f"{tag} **{item['name']}**\n　{', '.join(item['issues'])}")

    issues_text = "\n".join(issue_lines[:15])  # 最多 15 条
    if len(issue_lines) > 15:
        issues_text += f"\n... 及其他 {len(issue_lines) - 15} 条异常"

    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": "⚙️ Claw 自动化健康告警"},
                "template": "red" if c > 0 else "yellow",
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        f"**扫描时间**: {summary.get('generated_at', 'N/A')}\n"
                        f"**总计**: {total} | 🟢{h} 🟡{w} 🔴{c}"
                    ),
                },
                {"tag": "hr"},
                {
                    "tag": "markdown",
                    "content": issues_text if issues_text else "无异常",
                },
                {
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "plain_text",
                            "content": "Claw automation_health.py 自动巡检",
                        }
                    ],
                },
            ],
        },
    }

    try:
        req = urllib.request.Request(
            FEISHU_WEBHOOK,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("code") == 0:
                print("✅ 飞书告警已发送")
                return True
            else:
                print(f"❌ 飞书返回错误: {result}", file=sys.stderr)
                return False
    except Exception as e:
        print(f"❌ 飞书请求失败: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Claw 自动化健康检查")
    parser.add_argument("--json", action="store_true", help="仅输出 JSON")
    parser.add_argument("--alert", action="store_true", help="异常时飞书告警")
    args = parser.parse_args()

    if not args.json:
        print(f"[{datetime.now()}] 自动化健康扫描...")

    automations = get_automations()
    if not automations:
        print("无法读取自动化数据", file=sys.stderr)
        sys.exit(1)

    by_cat = categorize(automations)

    healthy = warning = critical = 0
    cat_results = {}

    for cat, items in by_cat.items():
        checked = []
        for auto in items:
            h = check_health(auto)
            checked.append(h)
            if h["health"] == "🟢":
                healthy += 1
            elif h["health"] == "🟡":
                warning += 1
            elif h["health"] == "🔴":
                critical += 1
            else:
                healthy += 1  # 🔵 running = OK
        cat_results[cat] = checked

    summary = {
        "total_count": len(automations),
        "healthy_count": healthy,
        "warning_count": warning,
        "critical_count": critical,
        "by_category": cat_results,
        "generated_at": datetime.now().isoformat(),
    }

    # 写入 JSON
    output_path = DATA_DIR / "automation_health.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    else:
        report = generate_report(summary)
        print(report)
        print(f"\n详细数据 → {output_path}")

    # 飞书告警
    if args.alert:
        send_feishu_alert(summary)

    return 0 if critical == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
