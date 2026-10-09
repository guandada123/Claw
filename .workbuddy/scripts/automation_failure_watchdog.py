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
    # 2026-09-25 补：**确定性失败**也要算 hard —— 修好前每次必失败，不是"偶尔没跑成"。
    # 实测 failure_code=automation-workspace-unavailable（cwds 双重 JSON 编码 → 目录不存在）
    # 被归入 other → 即使名字命中了关键白名单也还是次要，等于永不告警。
    if "automation-workspace-unavailable" in t:
        return "hard", "工作目录不可用（配置错 → 修好前每次必失败）"
    return "other", (t[:60] or "未知失败")


# 2026-09-25 补：**hub registry 已声明的自动化一律算关键**。
# 原来只按 9 个业务关键词判定 → 新增的治理类（看门狗/卫生/演进）**结构性漏网**：
# 💓中枢存活看门狗 连跑 48 次 0 成功（cwds 双重编码 → 工作目录不存在），
# 本脚本每小时都读到了它，却因「名字不含 早报/晚报/…」被归入 minor → 静默。
# → 白名单写在脚本里、而清单在另一个文件 = 「同一事实多副本必腐烂」，
#   只不过这次漏在了我自己的核对范围之外。改为**以 registry 的声明为单一事实源**：
#   新类型的自动化只要被 hub 纳管，就自动受本看门狗监控，不再需要人往白名单里加词。
REGISTRY = ROOT / ".workbuddy" / "inspection_hub" / "registry.json"


def _declared_critical() -> tuple[set[str], set[str]]:
    """从 hub registry 读「已纳管」的自动化 id 与名字（声明即"须被监控"）。

    读不到就返回空集 → 退化为原来的关键词判定（**不静默跳过整段**，
    与"输入缺失要自己报出来"同一条纪律：这里至少不能因此少监控）。
    """
    try:
        import json as _json

        data = _json.loads(REGISTRY.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return set(), set()
    ids, names = set(), set()
    for a in data.get("automations", []) or []:
        if isinstance(a, dict):
            if a.get("id"):
                ids.add(str(a["id"]))
            if a.get("name"):
                names.add(str(a["name"]))
    return ids, names


_ID_CRITICAL, _NAME_CRITICAL = _declared_critical()


def is_critical(name: str, aid: str | None = None) -> bool:
    # ① hub 声明的治理类（单一事实源）→ 一律关键
    if aid and aid in _ID_CRITICAL:
        return True
    if name and name in _NAME_CRITICAL:
        return True
    # ② 业务类关键词（保留为补充）
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


DISPATCH_AUDIT = ROOT / ".workbuddy" / "scripts" / "automation_dispatch_audit.py"


def dispatch_audit(hours: int) -> dict:
    """调用「派发对账」，返回其 JSON 结果；任何异常都不阻断主流程。

    设计要点（2026-10-09）：
      · **子进程调用**而非 import —— 避免两脚本耦合、避免 sys.path 污染；
      · 脚本不存在/执行失败 → 返回 {} 并打日志，**绝不 raise**：
        一个补位检查不该有能力把主 watchdog 拖垮；
      · 窗口按"天"给，且下限 14 天：小时级窗口对日/周跑自动化无意义
        （gap 阈值须按 FREQ 分级，分级逻辑在 audit 脚本内部）。
    """
    if not DISPATCH_AUDIT.is_file():
        print(f"[watchdog] ⚠️ 派发对账脚本缺失，跳过: {DISPATCH_AUDIT}")
        return {}
    days = max(14, (hours + 23) // 24)
    try:
        p = subprocess.run(
            [sys.executable, str(DISPATCH_AUDIT), "--json", "--days", str(days)],
            capture_output=True, text=True, timeout=180,
        )
        if p.returncode not in (0, 1):        # 0=干净 1=有发现，其余=异常
            print(f"[watchdog] ⚠️ 派发对账返回码 {p.returncode}: {p.stderr[:200]}")
            return {}
        return json.loads(p.stdout)
    except Exception as e:                     # noqa: BLE001 — 有意宽catch，见 docstring
        print(f"[watchdog] ⚠️ 派发对账执行失败（不阻断）: {e.__class__.__name__}: {e}")
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not DB.exists():
        print(f"⚠️ 数据库不存在: {DB}")
        return 1

    # ── 2026-10-09 新增：派发对账（补一个真实盲区）───────────────────────
    # 本脚本原只扫 `result_success=0` 的运行记录。而**从不派发**的自动化产生
    # **0 条运行记录** → 完全不可见。
    # ⚠️ 口径注意：对账脚本按 `status='ACTIVE' AND deleted_at IS NULL` 取"活跃"。
    #    只看 status 会把 20 行**已软删除**（deleted_at 非空）误当活跃 → 虚高到 91。
    #    本机 automations 表是软删除语义：**deleted_at 非空 = 已删除**，status 列不随之改写。
    # 注意：本步必须放在"无失败即早退"之前，否则最常见的路径（0 失败）根本不会跑到它，
    #       而它的价值恰恰在于**其它检查全绿时**。
    alerted = load_alerted()
    disp = dispatch_audit(args.hours)
    d_findings = disp.get("zombies", []) + disp.get("once_uncleaned", [])
    d_new = [it for it in d_findings if f"dispatch:{it['id']}" not in alerted]
    d_lines = [
        f"🔴 从不派发/长期静默：{it['name'][:34]}（{it.get('reason', '')}）"
        for it in sorted(d_findings, key=lambda x: x.get("last_dispatch") or "")
    ]

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
        print(f"[watchdog] 近 {args.hours}h 无失败记录")
        pushed_d = False
        if d_findings:
            print(f"[watchdog] 派发对账发现 {len(d_findings)} 条（新的 {len(d_new)}）")
            for ln in d_lines:
                print(f"  {ln}")
            if d_new and not args.dry_run:
                pushed_d = push("自动化派发对账", "\n".join(
                    [f"发现 {len(d_findings)} 条 ACTIVE 但从不派发的自动化：", *d_lines,
                     "", "说明：调度器只把 next_run_at 在未来的纳入排程；落在过去即永久不派发，"
                     "且不会产生运行记录，故此前所有健康检查都看不见它。"]))
                if pushed_d:
                    alerted.update(f"dispatch:{it['id']}" for it in d_new)
                    save_alerted(alerted)
            elif args.dry_run:
                print("[watchdog] (dry-run) 派发对账本应告警")
        else:
            print("[watchdog] 派发对账无发现 → SILENT")
        print("SUMMARY: " + json.dumps(
            {"failed": 0, "critical": 0, "pushed": pushed_d,
             "dispatch_findings": len(d_findings), "dispatch_new": len(d_new)},
            ensure_ascii=False))
        return 0

    critical, minor = [], []
    for aid, ts, title, name in rows:
        name = name or aid
        level, reason = classify(title)
        when = datetime.fromtimestamp(ts / 1000).strftime("%m-%d %H:%M")
        item = {"aid": aid, "name": name, "when": when, "ts": ts, "level": level, "reason": reason}
        # 关键自动化 + 硬失败 才算关键；soft(收尾打断) 一律降级
        if is_critical(name, aid) and level == "hard":
            critical.append(item)
        else:
            minor.append(item)

    # 去重：同一条失败 (automation_id@运行时间戳) 已推送过则不再重复轰炸
    # （alerted 已在 main 开头载入，此处不再重复读取）

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
        # 2026-09-25：按 (自动化, 原因) 聚合 —— 一条根因一行。
        # 实测：一条配置错（cwds 双重编码 → 工作目录不可用）在 48h 内产生 22 条失败记录，
        # 逐条列会让卡片变成 22 行重复，读的人还得自己做聚合。
        # **报告粒度要对齐人的决策粒度**（与 D9「一个键报一条」同一条纪律）。
        _groups: dict[tuple[str, str], list] = {}
        for it in new_critical:
            _groups.setdefault((it["name"], it["reason"]), []).append(it)
        lines = [
            f"🔴 近 {args.hours}h 关键失败 {len(new_critical)} 条（{len(_groups)} 个不同问题，新增）",
            "",
        ]
        for (_nm, _reason), _items in _groups.items():
            _times = "、".join(i["when"] for i in _items[:4])
            if len(_items) > 4:
                _times += f" …共 {len(_items)} 次"
            lines.append(f"• {_nm}（{len(_items)} 次）")
            lines.append(f"   原因：{_reason}")
            lines.append(f"   最近：{_times}")
            lines.append(f"   ID：{_items[0]['aid'].replace('automation-', '')}")
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
        # 2026-09-25 修正：**根因提示必须与实际观测到的失败类型匹配**。
        # 原来无条件追加"主进程被重启 → 会话未拉起"，而实测这次的失败全是
        # automation-workspace-unavailable（配置错，与"会话未拉起"无关）→
        # 读的人被指向"查重启来源"，真正该做的（改 cwds）反被掩盖。
        # 提示不依赖观测 = 从"提示"退化成"误导"，与"声明空掉仍报 clean"同族。
        _restart_kind = any("会话未拉起" in it["reason"] for it in new_critical)
        if _restart_kind:
            if _memwatch_restarted_recently():
                lines.append(
                    "根因=memwatch 内存看门狗超阈值重启主进程；巡检已自动诊断，必要时自动提阈值。"
                )
            else:
                lines.append(
                    "根因=主进程近期被重启（日志显示为「外部触发」，非内存超阈值），"
                    "重启时清空排队会话致部分定时任务「会话未拉起」；"
                    "建议排查外部重启来源（如巡检中枢/看门狗的 restart 调度）。"
                )
        elif any("工作目录不可用" in it["reason"] for it in new_critical):
            lines.append(
                "根因=自动化的工作目录配置无效（如 cwds 被双重 JSON 编码 → 解析成带方括号的路径）"
                "→ **确定性失败，修好前每次必失败**。修法：用 automation_update 写回干净的项目目录；"
                "注意该工具拒绝把 Claw 设为自动化工作区（cannot host automations），"
                "而这些 prompt 通常自带 `cd $CLAW`，故换任一项目目录即可，不影响功能。"
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

    # ── 派发对账结果并入（与失败巡检同一封推送，避免两封互相淹没）──
    if d_lines:
        print(f"[watchdog] 派发对账发现 {len(d_findings)} 条（新的 {len(d_new)}）")
        for ln in d_lines:
            print(f"  🔴 {ln}")
        if d_new and not args.dry_run:
            ok = push("自动化派发对账",
                      "\n".join([f"发现 {len(d_findings)} 条 ACTIVE 但从不派发的自动化：",
                                 *d_lines, "",
                                 "说明：调度器只把 next_run_at 在未来的纳入排程；落在过去即永久不派发，"
                                 "且不产生运行记录，故此前所有健康检查都看不见它。"]))
            if ok:
                pushed = True
                alerted.update(f"dispatch:{it['id']}" for it in d_new)
                save_alerted(alerted)

    print(
        "SUMMARY: "
        + json.dumps(
            {
                "failed": len(rows),
                "critical": len(critical),
                "new_critical": len(new_critical),
                "pushed": pushed,
                "dispatch_findings": len(d_findings),
                "dispatch_new": len(d_new),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
