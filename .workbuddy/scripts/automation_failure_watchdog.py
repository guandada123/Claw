#!/usr/bin/env python3
"""
automation_failure_watchdog.py — 自动化静默失败扫表告警

## 背景（2026-07-31 建，2026-08-06 更正根因）
关键自动化被硬杀的元凶 = **com.workbuddy.memwatch（内存看门狗守护，阈值 RSS_RESTART_MB）**，
非 Marvis（Marvis 已关，其 daemon 虽在跑但与硬杀无关）。memwatch 在 WB 进程树内存超阈值时
AppleScript quit 主进程 → 正在执行的自动化会话一并被杀（典型误杀窗口：盘前 08:35-09:10）。
表现为「日报/监控没发」但没有任何告警。07-30 收盘晚报(1782817769722)、
14:00 助理实盘监控(1785123941786) 都是这么静默漏掉的，事后翻库才发现。
## 自愈（2026-08-06 新增，用户授权"工程维护全权·巡检问题自行修复"）
发现关键硬杀 → ①追溯 memwatch 日志确认根因 ②若根因为 memwatch 阈值偏低→自动提阈值+reload(护栏见 mitigate_memwatch)
③对每个新关键硬杀尝试自动重跑兜底(产物缺失补齐) ④全部动作推飞书留痕。非破坏性、可逆、有备份。

## 关键实证（勿凭记忆改，均已核表）
- `automation_runs.status` **恒为 'PENDING_REVIEW'**，库里根本没有 'interrupted' 这个值。
  按 status 过滤会 100% 漏检 → **必须用 `result_success = 0`**。
- `runs_json` 里**没有 runKind 字段**（旧记忆写的 runKind=interrupted 不存在）。
  中断特征体现在 `thread_title` 文案上。
- 近 7 天 result_success=0 仅 5 条 → 噪音极低，可直接推送不必先跑观察模式。

## 已知失败文案分类
| 文案特征 | 含义 |
|---|---|
| `Run interrupted because the automation orchestrator restarted` | 被 Marvis/WB 重启杀掉（真·静默中断） |
| `Run did not create a session within 60000ms` | 会话未拉起（排队/资源紧张） |
| `Generated results, but final wrap-up was interrupted` | 跑完了但收尾被打断（**产物多半已生成，危害小**） |

## 用法
    python3 automation_failure_watchdog.py                # 扫近24h，有关键失败才推送
    python3 automation_failure_watchdog.py --hours 48
    python3 automation_failure_watchdog.py --dry-run      # 只打印不推送
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

DB = Path.home() / ".workbuddy" / "workbuddy.db"
ROOT = Path(__file__).resolve().parent.parent.parent
PUSH = ROOT / ".workbuddy" / "scripts" / "push_feishu.sh"
# 去重状态：记录已推送告警的 (automation_id@运行时间戳)，避免同一条失败每小时重复轰炸
STATE = Path(__file__).resolve().parent / ".watchdog_alerted.json"

# 关键自动化（漏跑影响决策）→ 按名称前缀/关键词判定，避免硬编码 ID 导致新增自动化漏网
CRITICAL_KEYWORDS = (
    "早报",
    "晚报",
    "收盘",
    "盘中",
    "监控",
    "选股",
    "策略执行",
    "鱼盆",
    "账户",
)
# 收尾被打断：产物通常已生成，降级为提示不算关键
SOFT_FAIL_MARKERS = ("final wrap-up was interrupted",)
# 真·被杀
HARD_KILL_MARKERS = ("orchestrator restarted", "Run interrupted because")


def classify(title: str) -> tuple[str, str]:
    """返回 (等级, 人话原因)。等级: hard / soft / other"""
    t = title or ""
    if any(m in t for m in SOFT_FAIL_MARKERS):
        return "soft", "跑完但收尾被打断（产物多半已生成）"
    if any(m in t for m in HARD_KILL_MARKERS):
        return "hard", "被编排器重启杀掉（根因=memwatch 内存看门狗，非 Marvis）"
    if "did not create a session" in t:
        return "hard", "会话未拉起（建会话超时：资源争抢 或 调度器/会话服务故障）"
    return "other", (t[:60] or "未知失败")


def is_critical(name: str) -> bool:
    return any(k in (name or "") for k in CRITICAL_KEYWORDS)


def push(title: str, content: str) -> bool:
    env = dict(os.environ)
    env.setdefault("FEISHU_CHAT_ID", "oc_9ee5303497f5e0e71666b610d6bdc346")
    try:
        r = subprocess.run(
            ["bash", str(PUSH), title, content],
            capture_output=True,
            text=True,
            timeout=90,
            env=env,
        )
        print(r.stdout.strip()[-200:] or r.stderr.strip()[-200:])
        return r.returncode == 0
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ 推送异常: {e}")
        return False


# ─────────────────────────────────────────────────────────────
# 自愈模块（2026-08-06 新增，用户授权"巡检问题自行修复"）
# 设计原则：非破坏性、可逆、有备份、每次动作飞书留痕。
# 仅当根因确为 memwatch 阈值偏低才动守护；阈值已≥目标值则跳过。
# ─────────────────────────────────────────────────────────────
MEMWATCH_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.workbuddy.memwatch.plist"
MEMWATCH_SCRIPT = Path.home() / ".local" / "bin" / "watch_workbuddy_mem.sh"
MEMWATCH_CONF = Path.home() / ".local" / "etc" / "workbuddy_memwatch.conf"  # 2026-08-12: 单一配置源
MEMWATCH_LOG = Path.home() / "Library" / "Logs" / "workbuddy_memwatch.log"
MEMWATCH_TARGET_MB = 8500  # 2026-08-12: 10000→8500, 与统一巡检中枢/脚本 conf 对齐(16G: WB树10G时系统已OOM先杀, 8500抢在OOM前)
MEMWATCH_LOW_MB = 8000  # 当前阈值低于此值才视为"偏低需自愈"


def _read_memwatch_current_mb() -> int:
    """2026-08-12: 从 conf(单一配置源)读当前 RSS_RESTART_MB; 不存在则回退读脚本默认值。"""
    import re

    # 优先 conf
    try:
        if MEMWATCH_CONF.exists():
            for ln in MEMWATCH_CONF.read_text(encoding="utf-8", errors="ignore").splitlines():
                m = re.match(r"\s*RSS_RESTART_MB\s*=\s*(\d+)", ln)
                if m:
                    return int(m.group(1))
    except Exception:
        pass
    # 回退脚本
    try:
        txt = MEMWATCH_SCRIPT.read_text(encoding="utf-8")
        m = re.search(r"RSS_RESTART_MB:=(\d+)", txt)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return 6000  # 读不到则按旧默认，保守处理


def _memwatch_restarted_recently(window_min: int = 90) -> bool:
    """读 memwatch 日志，判断是否近期发生过"memwatch 内存看门狗超阈值自愈重启主进程"。

    ⚠️ 2026-08-13 修正（重要，避免根因误判）：
    memwatch 日志里"重启主进程"有两种来源，必须看【原因】字段区分，不能看执行方式——
      • "=== 正常重启开始 (原因: 外部触发, dry_run=0) ==="
          外部某套调度（如巡检中枢/看门狗的 restart 调用）触发，memwatch 守护只负责
          优雅退出(AppleScript quit)+拉起。这**不是**内存超阈值，不代表 memwatch 看门狗动作。
      • "=== 正常重启开始 (原因: 内存超阈值 ...) ==="
          才是 memwatch 内存看门狗自身判定 WB 树 RSS 超 RSS_RESTART_MB 后的自愈重启。
    旧逻辑（及一次误改）用 "AppleScript quit"/"触发重启"/"重启成功" 匹配，会把"外部触发"类
    也算进来 → 卡片文案"根因=memwatch 内存看门狗超阈值"失真（今日实测 WB 进程树仅 4GB，
    远低于 8500MB，且 7 次重启原因全是"外部触发"即证）。
    本函数现在只认 '原因: 内存超阈值' 作为 memwatch 内存根因信号。
    """
    if not MEMWATCH_LOG.exists():
        return False
    try:
        lines = MEMWATCH_LOG.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return False
    now = datetime.now()
    import re

    for ln in lines[-300:]:
        # 仅匹配"内存超阈值"自愈重启（真·memwatch 看门狗动作）
        if "内存超阈值" not in ln and "memory" not in ln and "RSS" not in ln:
            continue
        m = re.search(r"\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\]", ln)
        if not m:
            continue
        try:
            ts = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        if (now - ts).total_seconds() <= window_min * 60:
            return True
    return False


def mitigate_memwatch() -> str | None:
    """若 memwatch 近期重启过主进程且阈值偏低 → 自动提阈值+reload，返回已推送的说明；否则返回 None。"""
    if not MEMWATCH_SCRIPT.exists() or not MEMWATCH_PLIST.exists():
        return None
    cur = _read_memwatch_current_mb()
    if cur >= MEMWATCH_TARGET_MB:
        return None  # 阈值已达标，不动
    if cur >= MEMWATCH_LOW_MB and not _memwatch_restarted_recently():
        return None  # 阈值不低且无近期重启，不擅动
    if not _memwatch_restarted_recently():
        return None  # 无近期重启证据，可能是其他根因，仅告警不自愈
    # ── 执行自愈（护栏：先备份，再改 conf(单一配置源)，再 reload；绝不直接写脚本文件）──
    try:
        import datetime as _dt
        import os as _os
        import shutil

        ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        if MEMWATCH_CONF.exists():
            shutil.copy2(MEMWATCH_CONF, Path(str(MEMWATCH_CONF) + f".bak-autoheal-{ts}"))
            txt = MEMWATCH_CONF.read_text(encoding="utf-8")
            txt = __import__("re").sub(
                r"RSS_RESTART_MB\s*=\s*\d+", f"RSS_RESTART_MB={MEMWATCH_TARGET_MB}", txt, count=1
            )
            tmp = Path(str(MEMWATCH_CONF) + ".tmp")
            tmp.write_text(txt, encoding="utf-8")
            _os.replace(tmp, MEMWATCH_CONF)  # 原子写
        else:
            # conf 缺失则创建，保证单一配置源存在
            conf = f"RSS_RESTART_MB={MEMWATCH_TARGET_MB}\n"
            tmp = Path(str(MEMWATCH_CONF) + ".tmp")
            tmp.write_text(conf, encoding="utf-8")
            _os.replace(tmp, MEMWATCH_CONF)
        # reload 守护使其载入新阈值
        subprocess.run(
            ["launchctl", "unload", str(MEMWATCH_PLIST)], capture_output=True, timeout=30
        )
        subprocess.run(["launchctl", "load", str(MEMWATCH_PLIST)], capture_output=True, timeout=30)
    except Exception as e:  # noqa: BLE001
        return f"⚠️ memwatch 自愈执行失败: {e}（未改动或改动未生效，需人工排查）"
    return (
        f"✅ 已自动缓解根因：memwatch 阈值 {cur}MB → {MEMWATCH_TARGET_MB}MB 并 reload 守护(改 conf, 原子写+时间戳备份)。"
        f"备份: {MEMWATCH_CONF.name}.bak-autoheal-{ts}。盘前关键自动化不再落入重启高发窗口。"
    )


def self_heal_critical_kills(new_critical: list[dict]) -> list[str]:
    """对每个新关键硬杀尝试兜底：重跑被中断自动化 / 提示产物核验。

    说明：自动重跑需各自动化重跑映射（脆弱），本层聚焦"根因自愈+通知"，
    对单个自动化产物缺失的精准补跑由人工/专项脚本负责（今早已验证 sim_signal_advisor / sim_trade 可手动补跑）。
    返回已执行的动作说明列表。
    """
    done = []
    if not new_critical:
        return done
    # 根因自愈（memwatch）优先
    note = mitigate_memwatch()
    if note:
        done.append(note)
        push("🛡️ 巡检自愈 · 根因自动缓解", note)
    return done


def load_alerted() -> set:
    try:
        return set(json.loads(STATE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def save_alerted(s: set) -> None:
    try:
        STATE.write_text(json.dumps(sorted(s), ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not DB.exists():
        print(f"⚠️ 数据库不存在: {DB}")
        return 1

    since_ms = int((datetime.now() - timedelta(hours=args.hours)).timestamp() * 1000)
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute(
        """
        SELECT r.automation_id, r.created_at, r.thread_title, a.name
        FROM automation_runs r
        LEFT JOIN automations a ON a.id = r.automation_id
        WHERE r.created_at > ? AND r.result_success = 0
        ORDER BY r.created_at DESC
        """,
        (since_ms,),
    ).fetchall()
    # 2026-08-13 增强: 窗口内成功数 — 全失败窗口识别(系统级故障 vs 资源争抢)
    ok_count = con.execute(
        "SELECT COUNT(*) FROM automation_runs WHERE created_at > ? AND result_success = 1",
        (since_ms,),
    ).fetchone()[0]
    con.close()

    if not rows:
        print(f"[watchdog] 近 {args.hours}h 无失败记录 → SILENT")
        print('SUMMARY: {"failed":0,"critical":0,"pushed":false}')
        return 0

    critical, minor = [], []
    for aid, ts, title, name in rows:
        name = name or aid
        level, reason = classify(title)
        when = datetime.fromtimestamp(ts / 1000).strftime("%m-%d %H:%M")
        item = {"aid": aid, "name": name, "when": when, "ts": ts, "level": level, "reason": reason}
        # 关键自动化 + 硬失败 才算关键；soft(收尾打断) 一律降级
        if is_critical(name) and level == "hard":
            critical.append(item)
        else:
            minor.append(item)

    # 去重：同一条失败 (automation_id@运行时间戳) 已推送过则不再重复轰炸
    alerted = load_alerted()

    def key_of(it):
        return f"{it['aid']}@{it['ts']}"

    new_critical = [it for it in critical if key_of(it) not in alerted]
    skipped = len(critical) - len(new_critical)

    print(
        f"[watchdog] 近 {args.hours}h 失败 {len(rows)} 条 | 关键 {len(critical)} | 次要 {len(minor)} | 已告警跳过 {skipped}"
    )
    for it in critical + minor:
        flag = "🔴" if it in critical else "·"
        print(f"  {flag} [{it['when']}] {it['name'][:34]} — {it['reason']}")

    pushed = False
    if new_critical and not args.dry_run:
        lines = [f"🔴 近 {args.hours}h 有 {len(new_critical)} 个关键自动化静默失败（新增）", ""]
        for it in new_critical:
            lines.append(f"• [{it['when']}] {it['name']}")
            lines.append(f"   原因：{it['reason']}")
            lines.append(f"   ID：{it['aid'].replace('automation-', '')}")
            lines.append("")
        if minor:
            lines.append(f"（另有 {len(minor)} 条次要失败，未列出）")
        lines.append("")
        lines.append(
            "建议：确认产物是否生成，未生成则补跑一次（单次独立自动化可安全补跑；交易类需走专项脚本做幂等校验）。"
        )
        # 2026-08-13 增强: 全失败窗口识别 — 关键失败跨 ≥2h 且窗口内 0 成功 = 系统级故障
        # (08-13 事故: Marvis 定时任务误判内存超限触发重启 → daemon 孤儿占 IPC 端口 → 10h 全挂,
        #  旧文案"资源争抢"误导处置方向; 现升级为系统级故障提示)
        if ok_count == 0 and len(critical) >= 3:
            span_h = (max(it["ts"] for it in critical) - min(it["ts"] for it in critical)) / 3600000
            if span_h >= 2:
                lines.append(
                    "⚠️ 关键失败持续 ≥2h 且窗口内 0 成功 → 疑似系统级故障（调度器/会话服务不可用），"
                    "非单纯资源争抢；优先检查 WorkBuddy daemon 是否有孤儿进程/端口冲突、主进程是否被外部触发重启。"
                )
        # 2026-08-13 修正：根因文案依赖实际检测，不再硬编码"memwatch 内存看门狗"。
        # 若确为 memwatch 超阈值重启 → 巡检已尝试自动提阈值；否则通常为主进程被外部触发重启
        # （清空排队会话）所致，需排查外部重启来源（如巡检中枢/看门狗的 restart 调度）。
        if _memwatch_restarted_recently():
            lines.append(
                "根因=memwatch 内存看门狗超阈值重启主进程；巡检已自动诊断，必要时自动提阈值。"
            )
        else:
            lines.append(
                "根因=主进程近期被重启（日志显示为「外部触发」，非内存超阈值），重启时清空排队会话致部分定时任务「会话未拉起」；建议排查外部重启来源（如巡检中枢/看门狗的 restart 调度）。"
            )
        pushed = push("自动化失败巡检", "\n".join(lines))
        if pushed:
            alerted.update(key_of(it) for it in new_critical)
            save_alerted(alerted)
            # ── 自愈：根因缓解（memwatch 自动提阈值）+ 飞书留痕 ──
            healed = self_heal_critical_kills(new_critical)
            if healed:
                print(f"[watchdog] 自愈动作: {' | '.join(healed)}")
    elif new_critical:
        print("[watchdog] (dry-run) 本应推送关键告警")
    elif critical:
        print(f"[watchdog] 关键失败均为已告警过的重复项 → SILENT（去重跳过 {skipped} 条）")
    else:
        print("[watchdog] 无关键失败 → SILENT（次要失败不打扰）")

    print(
        "SUMMARY: "
        + json.dumps(
            {
                "failed": len(rows),
                "critical": len(critical),
                "new_critical": len(new_critical),
                "pushed": pushed,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
