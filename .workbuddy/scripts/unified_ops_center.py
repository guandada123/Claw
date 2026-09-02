#!/usr/bin/env python3
"""
unified_ops_center.py — 统一巡检中枢（2026-08-06 整合接管）

## 背景
用户授权：「工程维护全权，巡检发现问题自行修复，飞书告知发生了什么，使用中无感」。
此前巡检类自动化分散（watchdog / 综合健康 / 跨项目健康 / 多项目健康 / 飞书通道自检），
职责重叠、各自推送、无统一自愈闭环。本中枢统一接管，对标全网 AIOps 最佳实践：
  - 三级升级：Tier1 自动修复(白名单Runbook) / Tier2 告警+建议 / Tier3 升级人工
  - Runbook 白名单制：AI 只"识别根因"，最终只执行已注册的安全动作（防幻觉误操作）
  - 重启循环防护：cooldown + 窗口内>N次停止自愈
  - 执行后验证：修复后复检确认恢复
  - 审计留痕：每次动作写 unified_self_heal_log.json (who/what/when/why/result)
  - 飞书告知：每次自愈推结构化卡片(原因/识别/解决/修复/优化/结论)
  - 告警去重：同一 (check_name, reason) 24h 内只推一次飞书（.ops_alerted.json 状态），审计日志照记
  - 知识闭环：当前告警自动对照 cross_project_state 的 known_failure_modes（F1-F7），命中即标注 remediation+tier（失败模式库从"文档"变"检测规则"）
  - 中枢自我审计(2026-08-12)：①动作效果验证(Runbook result=success≠问题解决, 23:45 memwatch_bump 教训)
    ②副作用熔断(连续 N 次效果未恢复→Runbook 自动降级仅记录不执行, .runbook_fuse.json)
    ③失败模式自动沉淀(熔断时自动写 F8+ 进 known_failure_modes, 实现自我升级)
  - 检查面（8 项）：自动化健康 / 自动化失败(watchdog) / Docker 自愈 / QTS·pmf CI / 磁盘 / 飞书通道 / 调度活性 / 安全扫描
  - 存活看门狗：独立调度 ops_center_liveness_watchdog.py（每2h，托管QTS），读状态锚 self_health.last_ok_ts，间隔>180min→飞书告警"中枢可能失联"
  - 周报：--weekly 模式生成近7天自愈统计 markdown 到 output/reports/（周日自动化调用）

## Runbook 白名单（已注册安全动作）
  - #1 memwatch_bump：memwatch 阈值偏低且近期重启→提10000MB+reload（幂等可逆）
  - #2 dependabot_rebase：OPEN dependabot PR 基于旧 main 致 CI 红→merge origin/main 进分支触发重跑
    （不 merge PR 本身；冲突则中止升级人工；仅 dependabot/* 分支，非 dependabot 不碰）
  - #3 container_restart：容器崩溃→self_heal.py 二次确认+崩溃循环捕获后重启（30min cooldown + 60min 振荡防护
    + 有状态容器禁重启）。中枢在 check 阶段调用 self_heal.py 并把其 restarted 结果作为已自愈动作上报（不重复重启逻辑）
  - #4 publish_audit_merge：检测"已验证未合并的 PR"（最新 CI 绿+非 draft+mergeable+基于最新 main）→ 自动走
    "gh pr diff 审计 + git fetch 比对 head 一致性 + 确认 mergeable + 合并 + 合并后 main CI 重跑变绿" 流程（用户 08-06 授权发布类）
    ⚠️ 安全约束：不 force/不绕过分支保护；合并后远程删分支被保护拒→保留孤儿分支标注 MERGED；实盘下单/对外发布仍归用户

## 设计原则
  - 不重写现有专项脚本，复用其 --json/--dry-run 接口（automation_health / self_heal / qts guard）
  - 观察≠transition：任何外部动作(改配置/重启)先备份、幂等、可回滚
  - fail-safe：单检查异常不崩溃，记 alert 继续
  - 全绿 SILENT，不重复轰炸（告警去重 24h TTL + 自愈动作幂等）

## 用法
    python3 unified_ops_center.py              # 真实运行（发现问题→自愈→飞书告知）
    python3 unified_ops_center.py --dry-run     # 只巡检不自愈不推送
    python3 unified_ops_center.py --no-push     # 巡检+自愈但不推飞书（仅写日志）
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent
CLAW_ROOT = SCRIPT_DIR.parent.parent  # .workbuddy/scripts -> Claw
PUSH = SCRIPT_DIR / "push_feishu.sh"
CHAT_ID = "oc_9ee5303497f5e0e71666b610d6bdc346"
SELF_HEAL_LOG = SCRIPT_DIR / "unified_self_heal_log.json"
ALERT_DEDUP_STATE = SCRIPT_DIR / ".ops_alerted.json"  # 告警去重状态（check_name@reason -> 时间戳）
ALERT_DEDUP_TTL_H = 24  # 同一告警 24h 内只推一次
# 2026-09-01 run#54：QTS API 鉴权检查的**增量锚点**（见 check_qts_api_auth 文档）。
# 存"已见过的最后一条 401/403 日志的 UTC 时间戳"，用于把 24h 滚动窗口的存量
# 计数与"自上次检查以来的新增"分开——否则修好存量后仍会瞎红 24h，且新回归被
# 去重键吞掉（详见该函数 docstring 的「为什么必须增量」）。
QTS_AUTH_ANCHOR = SCRIPT_DIR / ".qts_api_auth_401_anchor.json"

# 2026-08-30：纯人工处置类告警降频（key 子串匹配 -> 小时）
# 背景：部分告警根因只能人工处理（如扫码重登），中枢无自愈路径，按 24h 推送会
# 每日重复、长期占位并淹没真实可处置告警。此类降为 72h，仍持续提醒但不再日推。
# 注意：仅登记确无自愈路径的项；自愈类/工程类告警仍严格走 24h，不得加入。
ALERT_DEDUP_TTL_OVERRIDE_H = {
    "微信公众号通道@wechat-download-api 登录过期": 72,  # 纯人工扫码，中枢不可自愈
}

# 2026-09-02：审计日志同类条目节流窗口（小时）。见 append_heal_log 说明。
# 只作用于 action=="detect" 的发现类留痕；自愈/修复动作不受影响。
AUDIT_THROTTLE_H = 6

# 微信通道的上游消费方（2026-09-02 登记）：
# 通道已死时该自动化会每日调度却无产出（白跑）。巡检须**实时查其状态**再给处置建议，
# 而非把「建议暂停止损」写死在文案里 —— 硬编码会在暂停后立刻变成与事实相反的建议
# （同 2026-09-02-alert-text-must-follow-implementation.md 的教训）。
WECHAT_RSS_SYNC_ID = "automation-1785335108360"

MEMWATCH_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.workbuddy.memwatch.plist"
MEMWATCH_SCRIPT = Path.home() / ".local" / "bin" / "watch_workbuddy_mem.sh"
MEMWATCH_LOG = Path.home() / "Library" / "Logs" / "workbuddy_memwatch.log"
MEMWATCH_TARGET_MB = 8500  # 2026-08-12: 10000→8500(与脚本一致, 消除拉锯; 10000在WB树10G时系统已OOM先杀, 8500给系统留缓冲抢在OOM前)
MEMWATCH_LOW_MB = 8000

# ── 运行日志（审计留痕 who/what/when/why/result）──
_run_log: list[dict] = []


# ── 告警去重（避免同一问题每小时重复轰炸飞书）──
# 2026-08-31：去重键归一化。
# 根因：dedup key 原为 f"{check_name}@{reason_key}"，而 reason_key 是**完整告警文案**。
#   历次巡检为提升可诊断性而改写文案（run#36 微信 F3 细化、run#37 告警明细改取 stdout），
#   文案一变即生成全新 key，TTL 窗口被重置 → 同一根因被当作"新问题"重新推送。
#   实证：微信登录过期同一问题在 08-29 15:23 与 08-30 18:07 各推一次（相隔仅 27h），
#   而该项 TTL 显式配置为 72h —— 降频设计被"改文案"架空，且 key 只增不减（2→4）。
# 修法：去重键只取文案的**稳定主体**（首个中文逗号/全角括号之前），剥离易变诊断补充语；
#   合并同一稳定键时保留**最新**时间戳。合并只会增强抑制（留最新=距上次推送更近），
#   不会造成漏报；TTL override 按前缀匹配，归一化后仍命中。
_DEDUP_KEY_MAXLEN = 60  # 与迁移前调用点 rsn[:60] 的实际效果一致，避免既有键一次性失效重推
_DEDUP_SPLIT_SEPS = ("，", "（")  # 只切中文标点，避免破坏 "登录过期(isExpired=true)" 等 ASCII 括注


def _dedup_key(check_name: str, reason_key: str) -> str:
    """把 (检查项, 告警文案) 归一化为稳定的去重键。

    仅用于去重，不用于展示——展示侧仍用原始文案，保证诊断信息不丢。
    """
    s = (reason_key or "").strip()
    for sep in _DEDUP_SPLIT_SEPS:
        i = s.find(sep)
        if i > 0:
            s = s[:i]
    s = s.rstrip(":： ").strip()
    # 数字归一（2026-09-01 run#53）：告警文案里嵌入的**易变计数**会击穿去重。
    # 实证：QTS API 鉴权告警文案含「鉴权失败 N 次/24h」，N 每次运行都变(202→216→230)，
    # 于是同一根因每小时生成新键 → 飞书被同一问题连推 3 次。计数是**诊断补充**不是身份，
    # 身份应是「哪个检查项的什么问题」，故把数字串折叠为 # 后再截断。
    s = re.sub(r"\d+", "#", s)
    return f"{check_name}@{s[:_DEDUP_KEY_MAXLEN]}"


def _load_alerted() -> dict:
    """读取去重状态；顺带把历史"原始文案键"迁移为稳定键（同组合并，保留最新时间戳）。

    迁移写入是幂等的：键已归一化后 merged==raw，不再写盘。
    """
    try:
        raw = json.loads(ALERT_DEDUP_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    merged: dict[str, float] = {}
    for k, v in raw.items():
        try:
            ts = float(v)
        except (TypeError, ValueError):
            continue
        if "@" in k:
            name, _, reason = k.partition("@")
            nk = _dedup_key(name, reason)
        else:
            nk = k
        merged[nk] = max(merged.get(nk, 0.0), ts)
    if len(merged) != len(raw) or any(float(v) != merged.get(k) for k, v in raw.items()):
        ALERT_DEDUP_STATE.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
    return merged


def _ttl_hours_for(key: str) -> int:
    """按告警 key 取去重 TTL：纯人工处置类走 override，其余走默认 24h。"""
    for pattern, hours in ALERT_DEDUP_TTL_OVERRIDE_H.items():
        if pattern in key:
            return hours
    return ALERT_DEDUP_TTL_H


def _save_alerted(d: dict) -> None:
    # 清理过期条目（按各自 key 的 TTL，override 类条目保留更久）
    now = datetime.datetime.now().timestamp()
    d = {k: v for k, v in d.items() if now - v < _ttl_hours_for(k) * 3600}
    ALERT_DEDUP_STATE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def is_alert_duplicated(check_name: str, reason_key: str) -> bool:
    """同一 (check_name, reason_key 归一化后) TTL 内已推送过 → True（跳过飞书推送，但审计日志照记）

    TTL 默认 24h；命中 ALERT_DEDUP_TTL_OVERRIDE_H 的纯人工处置类告警按其配置（现 72h）。
    """
    d = _load_alerted()
    key = _dedup_key(check_name, reason_key)
    now = datetime.datetime.now().timestamp()
    if key in d and now - d[key] < _ttl_hours_for(key) * 3600:
        return True
    d[key] = now
    _save_alerted(d)
    return False


def log_action(action: str, target: str, reason: str, result: str, detail: str = "") -> dict:
    rec = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "action": action,
        "target": target,
        "reason": reason,
        "result": result,  # success / skipped / failed
        "detail": detail,
    }
    _run_log.append(rec)
    return rec


def append_heal_log(rec: dict) -> None:
    try:
        data = (
            json.loads(SELF_HEAL_LOG.read_text(encoding="utf-8")) if SELF_HEAL_LOG.exists() else []
        )
    except Exception:
        data = []

    # ── 审计节流（2026-09-02 新增）──
    # 背景：200 条环形上限被单一高频项灌满。实证（2026-08-26~09-02，共 200 条）：
    #   187 条（93.5%）是「微信公众号通道 登录过期」这**同一条**死通道告警 ——
    #   中枢每小时跑一次就 append 一条，7 天历史被挤出窗口，真实可处置的告警
    #   （QTS API 鉴权 401 / 成本委托断链 / CI 容器异常）只剩 13 条且被淹没，
    #   直接导致「日志里全是微信、看不到别的」的排查失效。
    #   注意：飞书侧有 72h 去重、并未轰炸用户，被灌满的只是**审计留痕**，属纯损耗。
    # 修法：同类 detect 记录在节流窗口内不新增条目，只把已存在条目的 ts 更新为
    #   最近出现时刻并累加 hits —— 保留「最后一次见到 + 出现过几次」，
    #   不丢可见性，也不再挤占窗口。
    # 边界：仅作用于 action=="detect"。自愈/修复/失败类动作是处置留痕，一律不节流。
    # 判据用 (target, reason 前 80 字) 而非全等：reason 内含计数等易变诊断语时
    #   仍能归并（与告警去重键同思路），但比去重键更宽松，避免过度合并不同结论。
    if rec.get("action") == "detect":
        now = datetime.datetime.now()
        tk = (rec.get("target", ""), (rec.get("reason") or "")[:80])
        for i in range(len(data) - 1, -1, -1):
            old = data[i]
            if old.get("action") != "detect":
                continue
            if (old.get("target", ""), (old.get("reason") or "")[:80]) != tk:
                continue
            try:
                age_h = (now - datetime.datetime.fromisoformat(old["ts"])).total_seconds() / 3600
            except Exception:  # noqa: BLE001
                age_h = AUDIT_THROTTLE_H * 2  # 时间戳不可解析：保守当超窗，落新条目
            if age_h < AUDIT_THROTTLE_H:
                data[i]["ts"] = now.isoformat(timespec="seconds")
                data[i]["hits"] = int(data[i].get("hits", 1)) + 1
                SELF_HEAL_LOG.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                return
            break  # 命中同类但已超窗 → 不再往前找，落新条目

    rec.setdefault("hits", 1)
    data.append(rec)
    # 仅保留最近 200 条
    data = data[-200:]
    SELF_HEAL_LOG.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────────────────────
# 中枢自我审计（2026-08-12 新增，08-11 停机5h11m 教训固化）
# 目标：审计者自身也要被审计。三个环：
#  ① 动作效果验证：Runbook 执行"成功"(result=success) 不等于"问题解决"——
#     23:45 memwatch_bump 记 success，实际打断 do_restart 致 WB 停机 5h11m。
#  ② 副作用检测+熔断：动作后目标若出现新告警/恶化，累计 N 次自动降级该 Runbook
#     为只记录不执行（防"自愈变互害"）。
#  ③ 失败模式自动沉淀：把"Runbook 动作自身闯祸"类新故障自动写入
#     cross_project_state.known_failure_modes（F8+），实现自我升级。
# ─────────────────────────────────────────────────────────────
# 动作效果验证器注册表: action -> 验证函数(返回 True=已恢复/达标, False=未恢复, None=无法验证)
# 在 Runbook 执行成功后调用; 无法验证时记为 None 不参与熔断计数。
def _verify_memwatch_bump_effect() -> bool | None:
    """memwatch_bump 效果验证（双层，2026-08-12 AIOps 对标升级）：
    短期(①守护存活+新阈值生效) + 长期(②最近 30min 无二次候选/重启 = 无"假性修复")。
    杜绝"动作成功但系统仍在反复重启"的假性修复(23:45 停机 5h11m 的问题模式)。"""
    try:
        import re as _re
        import subprocess as _sp
        from datetime import datetime as _dt

        # ── 短期验证：守护存活 + 新阈值生效 ──
        r = _sp.run(["launchctl", "list"], capture_output=True, text=True, timeout=15)
        alive = "com.workbuddy.memwatch" in (r.stdout or "")
        if not alive:
            return False
        if MEMWATCH_LOG.exists():
            tail = MEMWATCH_LOG.read_text(encoding="utf-8", errors="ignore").splitlines()[-10:]
            thr_ok = False
            for ln in reversed(tail):
                if "监控启动" in ln:
                    thr_ok = f"rss_thr={MEMWATCH_TARGET_MB}" in ln
                    break
            if not thr_ok:
                return False
        # ── 长期稳态验证：最近 30min 无二次候选/重启(反弹检测) ──
        now = _dt.now()
        rebound = False
        if MEMWATCH_LOG.exists():
            for ln in MEMWATCH_LOG.read_text(encoding="utf-8", errors="ignore").splitlines()[-300:]:
                if "候选" not in ln and "触发重启" not in ln:
                    continue
                m = _re.search(r"\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\]", ln)
                if not m:
                    continue
                try:
                    ts = _dt.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S")
                except Exception:
                    continue
                if (now - ts).total_seconds() <= 30 * 60:
                    rebound = True
                    break
        return not rebound
    except Exception:
        return None


_EFFECT_VERIFIERS: dict[str, callable] = {
    "memwatch_bump": _verify_memwatch_bump_effect,
}
# 熔断阈值：同一 Runbook 累计副作用(效果未恢复/目标随后告警)达此值 → 降级
FUSE_TRIGGER = 2
# 熔断 Half-Open 冷却(秒)：熔断后过此时间自动转 Half-Open，下一轮允许重试探测
# (业界三态熔断器: Closed→Open→Half-Open→Closed, 2026-08-12 AIOps对标升级)
FUSE_HALF_OPEN_SEC = 6 * 3600  # 6h 冷却后自动恢复尝试
# 熔断状态文件（幂等、可审计）
FUSE_STATE_FILE = SCRIPT_DIR / ".runbook_fuse.json"


def _load_fuse_state() -> dict:
    try:
        return json.loads(FUSE_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_fuse_state(d: dict) -> None:
    FUSE_STATE_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def _fuse_half_open_elapsed(action: str) -> bool:
    """Half-Open 判定：熔断时间超过冷却期则允许自动恢复探测(不永久禁用)。"""
    fuse = _load_fuse_state()
    e = fuse.get(action) or {}
    fused_at = e.get("fused_at")
    if not fused_at:
        return False
    try:
        from datetime import datetime as _dt

        ts = _dt.fromisoformat(fused_at)
        return (datetime.datetime.now() - ts).total_seconds() >= FUSE_HALF_OPEN_SEC
    except Exception:
        return False


def audit_self_actions() -> list[dict]:
    """中枢自我审计：对本次执行的 Runbook 动作做效果验证 + 副作用熔断计数。
    返回新产生的告警/降级记录（供飞书报告展示）。"""
    alerts: list[dict] = []
    fuse = _load_fuse_state()
    for rec in _run_log:
        act = rec.get("action", "")
        if act not in _EFFECT_VERIFIERS or rec.get("result") != "success":
            continue
        verifier = _EFFECT_VERIFIERS[act]
        try:
            ok = verifier()
        except Exception:  # noqa: BLE001
            ok = None
        entry = fuse.setdefault(act, {"side_effects": 0, "fused": False})
        if ok is False:
            entry["side_effects"] += 1
            rec["self_audit"] = "effect_not_recovered"
            alerts.append(
                f"[自我审计] {act} 动作后效果未恢复(副作用累计 {entry['side_effects']})，已计入熔断计数"
            )
            if entry["side_effects"] >= FUSE_TRIGGER and not entry["fused"]:
                entry["fused"] = True
                entry["fused_at"] = datetime.datetime.now().isoformat(timespec="seconds")
                alerts.append(
                    f"[自我审计] Runbook {act} 连续 {entry['side_effects']} 次副作用 → 已熔断降级"
                    f"(冷却 {FUSE_HALF_OPEN_SEC // 3600}h 后自动 Half-Open 恢复探测; 也可人工删 "
                    f"{FUSE_STATE_FILE.name} 立即恢复)"
                )
                # 自我升级③环：熔断触发时把该故障模式自动沉淀进 known_failure_modes
                _sink_failure_mode(act)
        elif ok is True:
            # 2026-08-12: 副作用计数衰减——动作效果恢复正常时, 历史副作用计数减半
            # (Half-Open 成功后 Closed 重置; 防一次旧失误永久锁死)
            if entry.get("side_effects", 0) > 0:
                entry["side_effects"] = entry["side_effects"] // 2
            rec["self_audit"] = "effect_recovered"
        else:
            rec["self_audit"] = "effect_unverifiable"
    _save_fuse_state(fuse)
    return alerts


def is_runbook_fused(action: str) -> bool:
    """Runbook 熔断检查：已熔断且未过 Half-Open 冷却期的 Runbook 只记录不执行。
    冷却期过后自动解除(允许下一轮重试探测), 实现三态熔断自动恢复。"""
    try:
        fuse = _load_fuse_state()
        if not bool(fuse.get(action, {}).get("fused")):
            return False
        # Half-Open: 冷却期已过 → 自动解除熔断, 允许下一轮重试
        if _fuse_half_open_elapsed(action):
            print(f"  [熔断恢复] {action} Half-Open 冷却期已过, 自动解除熔断(允许重试探测)")
            return False
        return True
    except Exception:
        return False


def _sink_failure_mode(action: str) -> None:
    """失败模式自动沉淀（自我升级第③环）：Runbook 熔断时把该故障模式写入
    cross_project_state.known_failure_modes（F8+），下次同类告警自动命中
    remediation="Runbook 已熔断, 人工审查"。幂等：同 id 不重复追加。"""
    try:
        data = (
            json.loads(CROSS_STATE_PATH.read_text(encoding="utf-8"))
            if CROSS_STATE_PATH.exists()
            else {}
        )
        km = data.setdefault("monitoring", {}).setdefault("known_failure_modes", [])
        # 已存在同 id/同 action 则跳过
        if any(k.get("action") == action for k in km):
            return
        # 分配下一个 id (F1-F7 已占用, 新分配 F8/F9/...)
        ids = [k.get("id", "") for k in km if k.get("id", "").startswith("F")]
        next_n = max([int(i[1:]) for i in ids if i[1:].isdigit()] or [7]) + 1
        km.append(
            {
                "id": f"F{next_n}",
                "action": action,
                "project": "all",
                "symptom": f"{action} 自愈动作副作用",
                "cause": "Runbook 动作执行后目标未恢复(连续副作用触发熔断)",
                "remediation": f"Runbook {action} 已自动熔断(仅记录不执行); 请人工审查脚本逻辑并验证目标恢复后清除 .runbook_fuse.json 恢复",
                "tier": "alert-only(需人工)",
            }
        )
        import os as _os

        _tmp = Path(str(CROSS_STATE_PATH) + ".tmp")
        _tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        _os.replace(_tmp, CROSS_STATE_PATH)
        print(f"  [自我升级] 已沉淀失败模式 F{next_n}({action}) 至 known_failure_modes")
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] 失败模式沉淀失败(非阻断): {e}")


def _reset_runbook_fuse(action: str) -> None:
    """人工恢复 Runbook：清除熔断状态（供审查确认后手动调用/文档指引）。"""
    try:
        fuse = _load_fuse_state()
        if action in fuse:
            del fuse[action]
            _save_fuse_state(fuse)
    except Exception:
        pass


def run_cmd(
    cmd: list[str],
    timeout: int = 90,
    capture: bool = True,
    env: dict | None = None,
    cwd: str | None = None,
) -> subprocess.CompletedProcess:
    # ⚠️ cwd 默认继承调用方进程。巡检类脚本必须由**调用方显式锁定 cwd**，
    # 否则同一份代码在不同工作目录下语义不同（见 check_code_quality 的 pytest 教训，
    # 2026-09-01 run#53：中枢以 QTS 仓为 cwd 被调度 → `pytest tests/` 跑成了 QTS 的
    # e2e 测试 → 每小时对生产 QTS API 打 14 次未鉴权请求，自己制造 401 告警）。
    return subprocess.run(
        cmd, capture_output=capture, text=True, timeout=timeout, env=env, cwd=cwd
    )


def _raise_cmd_error(r: subprocess.CompletedProcess) -> None:
    """TRY301: 将 try 块内 raise 抽象到独立函数，异常仍由调用方 except 捕获。"""
    raise RuntimeError(r.stderr.strip()[:150])


def push_card(title: str, content: str, level: str = "info") -> bool:
    env = dict(os.environ)
    env.setdefault("FEISHU_CHAT_ID", CHAT_ID)
    env["PUSH_LEVEL"] = level
    try:
        r = run_cmd(["bash", str(PUSH), title, content], env=env)
        return r.returncode == 0
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ 推送异常: {e}")
        return False


# ════════════════════════════════════════════════════════════════════
# 专项检查（复用现有脚本，不重写）
# ════════════════════════════════════════════════════════════════════
def check_automation_health() -> dict:
    """复用 automation_health.py --json。返回 {ok, alerts:[]}"""
    try:
        r = run_cmd(
            [sys.executable, str(SCRIPT_DIR / "automation_health.py"), "--json"], timeout=120
        )
        # 解析 JSON（脚本可能混输出，取首个 '{' 起至末尾）。
        # 注意：原实现用 rfind("{")，取到的是**最后一个内层花括号**，对任何嵌套 JSON
        # 必然 JSONDecodeError（2026-08-31 实测：纯 JSON 输出也失败）。后果是 rc==0
        # 分支永远走 raw 回退 → data.get("alerts") 从未被读到，脚本级告警被静默吞掉。
        out = r.stdout.strip()
        try:
            data = json.loads(out[out.find("{") :])
        except Exception:
            data = None

        if r.returncode != 0:
            # 明细在 stdout（JSON 的 by_category），不在 stderr —— 2026-08-31 实测：
            # 只取 stderr 时飞书卡片正文是「退出码 1: 」空壳，无法定位，等于白推一次。
            crit = []
            if isinstance(data, dict):
                for items in (data.get("by_category") or {}).values():
                    for it in items or []:
                        if it.get("health") == "🔴":
                            crit.append(
                                f"{it.get('name', '?')}"
                                f"（{'/'.join(it.get('issues') or []) or '无明细'}）"
                            )
            n_crit = data.get("critical_count", "?") if isinstance(data, dict) else "?"
            detail = "；".join(crit) if crit else (r.stderr[:200] or "stdout 无 JSON")
            return {"ok": False, "alerts": [f"自动化健康 {n_crit} 项🔴: {detail}"]}
        if data is None:
            return {"ok": True, "alerts": [], "raw": out[-300:]}
        alerts = data.get("alerts") or data.get("failures") or []
        if isinstance(alerts, list) and alerts:
            return {"ok": False, "alerts": [str(a) for a in alerts]}
        return {"ok": True, "alerts": []}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "alerts": [f"automation_health 异常: {e}"]}


def check_docker_self_heal() -> dict:
    """复用 self_heal.py（Docker 二次确认+崩溃循环捕获）。
    覆盖 cross_project_state 的 self_heal_allowlist 内所有容器（含 QTS/pmf/StockInsight 等）。
    返回 {ok, alerts, healed, containers}：
      - restarted 是已完成的自愈动作（Runbook#3），作为 healed 上报
      - alerts 才是仍需关注的（振荡防护停手/重启失败/有状态禁重启/不在docker等）
      - containers 是存活摘要（被巡检容器数/健康数/跳过数/异常数），供报告显式呈现健康度"""
    try:
        r = run_cmd([sys.executable, str(SCRIPT_DIR / "self_heal.py")], timeout=120)
        if r.returncode != 0:
            return {
                "ok": False,
                "alerts": [f"self_heal 退出码 {r.returncode}"],
                "healed": [],
                "containers": {},
            }
        out = r.stdout.strip()
        try:
            data = json.loads(out[out.rfind("{") :])
        except Exception:
            return {"ok": True, "alerts": [], "healed": [], "containers": {}, "raw": out[-300:]}
        restarted = data.get("restarted") or []
        alerts = data.get("alerts") or []
        # restarted 是 Runbook#3 已完成的自愈（容器重启），作为 healed 上报
        healed = [
            {"action": "container_restart", "target": c, "result": "success"} for c in restarted
        ]
        # 存活摘要：从 self_heal 的 checked/skipped/alerts 汇总（含 QTS/pmf 容器）
        checked = data.get("checked") or []
        skipped = data.get("skipped") or []
        containers = {
            "checked": len(checked),
            "healthy": len(checked) - len(alerts),
            "skipped_stateful": len(skipped),
            "alerts": len(alerts),
        }
        return {
            "ok": len(alerts) == 0,
            "alerts": alerts,
            "healed": healed,
            "containers": containers,
            "data": data,
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "alerts": [f"self_heal 异常: {e}"], "healed": [], "containers": {}}


def check_disk() -> dict:
    """检查数据盘使用率（cross_project_state 阈值 warn=85 crit=92）。
    仅查真实数据盘 /Volumes/ZHITAI；/Users/guan 在 macOS 映射到系统卷(~91%常态)，非风险点，排除。"""
    alerts = []
    try:
        for path in ["/Volumes/ZHITAI"]:
            if not os.path.exists(path):
                continue
            r = run_cmd(["df", "-P", path], timeout=20)
            if r.returncode == 0:
                line = r.stdout.strip().splitlines()[-1]
                parts = line.split()
                if len(parts) >= 5:
                    use_pct = int(parts[4].rstrip("%"))
                    if use_pct >= 92:
                        alerts.append(f"磁盘 {path} 使用率 {use_pct}%(crit≥92)")
                    elif use_pct >= 85:
                        alerts.append(f"磁盘 {path} 使用率 {use_pct}%(warn≥85)")
        return {"ok": len(alerts) == 0, "alerts": alerts}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "alerts": [f"disk 检查异常: {e}"]}


def check_feishu_channel() -> dict:
    """飞书通道可达性（lark-cli 探测）。"""
    try:
        r = run_cmd(["lark-cli", "auth", "status"], timeout=30)
        if r.returncode != 0:
            return {"ok": False, "alerts": [f"飞书通道异常: {r.stderr[:200]}"]}
        return {"ok": True, "alerts": []}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "alerts": [f"飞书通道检测异常: {e}"]}


def check_automation_failures() -> dict:
    """补强自动化检查：复用 automation_failure_watchdog.py --dry-run（不推送，只检测）。
    专门识别 known_failure_modes 的 F4（静默失败/401 proxy 未起）+ 关键自动化 hard 失败。
    与 check_automation_health() 职责互补：health 看"配置/调度健康"，本函数看"近期运行是否真失败"。
    解析其 SUMMARY 行取 failed/critical/new_critical。

    🔴 分工铁律（08-06 去重治理）：watchdog(1785506975961) 是**唯一**关键失败飞书告警推送方；
    本函数只做"趋势可见性"（记录到状态锚），**绝不返回会触发中枢推送的 alert**——
    否则同一条关键失败会被 watchdog + 中枢整体报告双发（两套去重键不同，首轮必双推）。
    因此 new_critical>0 时返回 ok=True + 空alerts + note（仅状态锚记录），不进 all_alerts。"""
    try:
        r = run_cmd(
            [
                sys.executable,
                str(SCRIPT_DIR / "automation_failure_watchdog.py"),
                "--dry-run",
                "--hours",
                "24",
            ],
            timeout=120,
        )
        out = r.stdout.strip()
        # 解析 SUMMARY 行
        m = re.search(r"SUMMARY:\s*(\{.*\})", out)
        failed = critical = new_critical = 0
        if m:
            try:
                s = json.loads(m.group(1))
                failed = s.get("failed", 0)
                critical = s.get("critical", 0)
                new_critical = s.get("new_critical", 0)
            except Exception:
                pass
        # 趋势可见性：把失败计数作为 note 回传（供状态锚记录），但不产生 alert 避免双发
        if new_critical > 0:
            return {
                "ok": True,
                "alerts": [],
                "note": f"近24h 关键自动化静默失败 {new_critical} 个（已由 watchdog 推送，中枢不重复推）",
            }
        if critical > 0:
            return {"ok": True, "alerts": [], "note": f"关键失败 {critical} 个均为已告警重复项"}
        return {"ok": True, "alerts": [], "note": f"近24h 无关键失败（failed={failed}）"}
    except Exception as e:  # noqa: BLE001
        # 仅当 watchdog 脚本本身异常（非业务失败）才报——这是中枢该关心的"检测器健康"
        return {"ok": False, "alerts": [f"automation_failure_watchdog 异常: {e}"]}


def check_known_failure_modes(all_alerts: list[str]) -> list[dict]:
    """知识闭环：把当前所有告警与 cross_project_state 的 known_failure_modes 对照，
    命中则标注 remediation(修复建议) + tier(自愈级别)，返回增强后的告警清单。
    状态锚里的失败模式库终于被中枢消费——从"文档"变成"检测规则"。
    匹配策略（宽松但精准）：symptom 拆关键词(按 /,空格,中文标点)任一命中，或 project 名出现在告警。
    project=all/all-docker 视为通用模式（仅靠 symptom 命中），不靠 project 名称强匹配。"""
    try:
        state = (
            json.loads(CROSS_STATE_PATH.read_text(encoding="utf-8"))
            if CROSS_STATE_PATH.exists()
            else {}
        )
        modes = state.get("monitoring", {}).get("known_failure_modes", []) or []
    except Exception:
        return []
    if not modes:
        return []
    enhanced = []
    for a in all_alerts:
        a_low = a.lower()
        matched = None
        for m in modes:
            sym = m.get("symptom") or ""
            proj = (m.get("project") or "").lower()
            # symptom 拆关键词
            kws = [k.strip().lower() for k in re.split(r"[/,，、\s]+", sym) if k.strip()]
            sym_hit = any(kw and kw in a_low for kw in kws)
            # project 匹配：通用模式(all/all-docker)不靠 project 名称；具体 project 名出现在告警才命中
            proj_hit = bool(proj) and proj not in ("all", "all-docker") and proj in a_low
            if sym_hit or proj_hit:
                matched = m
                break
        if matched:
            enhanced.append(
                {
                    "alert": a,
                    "failure_id": matched.get("id"),
                    "remediation": matched.get("remediation"),
                    "tier": matched.get("tier"),
                }
            )
    return enhanced


def check_schedule_liveness() -> dict:
    """调度活性检查：复用 schedule_utils.py stats（今日锁统计）。
    若今日锁数=0 → 说明今天没有任何自动化完成过，调度系统可能整体挂死 → 告警。
    这是轻量真实的"调度在跑吗"信号（中枢自身每小时跑会写锁，若连中枢锁都没有必异常）。"""
    try:
        r = run_cmd([sys.executable, str(SCRIPT_DIR / "schedule_utils.py"), "stats"], timeout=30)
        out = r.stdout
        m = re.search(r"今日 (\d+) 个", out)
        today_n = int(m.group(1)) if m else -1
        if today_n == 0:
            return {
                "ok": False,
                "alerts": ["今日调度锁数=0（没有任何自动化完成过，调度系统可能整体挂死）"],
            }
        if today_n < 0:
            return {"ok": True, "alerts": [], "note": "无法解析调度锁统计"}
        return {"ok": True, "alerts": [], "today_locks": today_n}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "alerts": [f"schedule_utils 异常: {e}"]}


def check_code_quality() -> dict:
    """工程质量综合检查（2026-08-06 扩展，原 check_security_scan 仅 bandit）。
    覆盖 ruff 违规数 + bandit 安全 + 双导入门禁 + 单元测试（与工程质检自动化 1782002834355 同维度）。
    设计分工：
      - 本检查 = 只读检测 + note 可见性（写状态锚），**不自动修复、不推送**（避免与质检自动化双推）
      - 工程质检自动化 = 负责 ruff 自动修复 + 卡片推送（中枢不抢其职责）
    有高危安全项→告警（Tier2 仅告知）；ruff/测试/门禁异常→note 记录（不阻断，质检会处理）。"""
    notes = []
    alerts = []

    # 1) bandit 安全扫描（复用 security_scanner.py --quiet）
    try:
        r = run_cmd(
            [sys.executable, str(SCRIPT_DIR / "security_scanner.py"), "--quiet"], timeout=180
        )
        m = re.search(r"bandit:\s*高危\s*(\d+)\s*\|\s*中危\s*(\d+)\s*\|\s*低危\s*(\d+)", r.stdout)
        if m:
            high, med, low = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if high > 0:
                alerts.append(
                    f"工程安全扫描发现 {high} 个高危 + {med} 中危问题（bandit），需人工复核"
                )
            else:
                notes.append(f"bandit: 无高危（中{med}/低{low}）")
        else:
            notes.append("无安全汇总输出（bandit 可能未装）")
    except Exception as e:  # noqa: BLE001
        alerts.append(f"security_scanner 异常: {e}")

    # 2) ruff 违规数（只读，不 --fix）
    try:
        r = run_cmd(["ruff", "check", "--output-format", "concise", str(CLAW_ROOT)], timeout=120)
        # concise 输出末行可能是 "Found N errors" 或空
        m = re.search(r"Found\s+(\d+)\s+error", r.stdout)
        ruff_n = int(m.group(1)) if m else 0
        notes.append(f"ruff: {ruff_n} 项违规（质检自动化负责自动修复）")
    except Exception as e:  # noqa: BLE001
        notes.append(f"ruff 检查异常: {e}")

    # 3) 双导入反模式门禁
    try:
        chk = CLAW_ROOT / "scripts" / "check_no_double_import.py"
        if chk.exists():
            r = run_cmd([sys.executable, str(chk)], timeout=60)
            if r.returncode != 0:
                notes.append("双导入门禁: 失败（详见工程质检卡片推送）")
            else:
                notes.append("双导入门禁: PASS")
        else:
            notes.append("双导入门禁: 脚本缺失")
    except Exception as e:  # noqa: BLE001
        notes.append(f"双导入门禁异常: {e}")

    # 4) 单元测试套件
    # ⚠️ 2026-09-01 run#53 根因修复：必须显式锁定 cwd=CLAW_ROOT 且用绝对路径。
    # 原写法用相对路径 "tests/" 且继承调用方 cwd —— 本自动化以 QTS 仓为工作目录被调度，
    # 于是「Claw 工程质量检查」实际在跑 **QTS 的 tests/**，其中 test_e2e.py /
    # tests/contracts/* 直接对生产 127.0.0.1:8000 发未鉴权请求（无 X-API-Key），
    # 每次巡检固定 14 条 401，被本中枢新增的 check_qts_api_auth 当成生产故障反复告警
    # （观测者效应：巡检自己污染被巡检对象）。锁定 cwd 后该副作用归零。
    try:
        r = run_cmd(
            [sys.executable, "-m", "pytest", str(CLAW_ROOT / "tests"), "-q"],
            timeout=300,
            env={**os.environ, "PYTHONPATH": str(CLAW_ROOT)},
            cwd=str(CLAW_ROOT),
        )
        m = re.search(r"(\d+)\s+passed", r.stdout)
        passed = int(m.group(1)) if m else "?"
        failed = re.search(r"(\d+)\s+failed", r.stdout)
        failed_n = int(failed.group(1)) if failed else 0
        if failed_n > 0:
            notes.append(f"测试: {passed} passed / {failed_n} failed（质检卡片会推送）")
        else:
            notes.append(f"测试: {passed} passed")
    except Exception as e:  # noqa: BLE001
        notes.append(f"pytest 异常: {e}")

    return {"ok": len(alerts) == 0, "alerts": alerts, "note": " | ".join(notes)}


def check_duplicate_picks() -> dict:
    """选股信号去重检查（2026-08-06 新增，中优先级优化③）：
    扫描当日投顾智能选股产物(experiments/日期.json)、午间选股(midday_pick_日期.json)、
    助理收盘扫描产物，提取推荐票代码，标记同票多源共识，避免推送打架。
    仅做可见性记录（note），不推送、不阻断任何选股自动化——选股类保持分离是设计意图。
    返回 ok=True + note（含共识票数分布）。"""
    today = datetime.date.today().strftime("%Y-%m-%d")
    sim_dir = SCRIPT_DIR.parent / "data" / "simulation"
    exp_dir = sim_dir / "experiments"
    picks: dict[str, list[str]] = {}  # code -> [源列表]
    sources = {
        "智能选股": exp_dir / f"{today}.json",
        "午间选股": exp_dir / f"midday_pick_{today}.json",
        "助理收盘扫描": sim_dir / "picks_history.json",
    }
    try:
        for src, path in sources.items():
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            codes = _extract_pick_codes(data)
            for c in codes:
                picks.setdefault(c, []).append(src)
        # 共识统计
        consensus = {c: s for c, s in picks.items() if len(s) >= 2}
        total_picks = len(picks)
        if consensus:
            detail = "; ".join(f"{c}({'+'.join(s)})" for c, s in consensus.items())
            note = f"选股信号: 今日 {total_picks} 票 | 多源共识 {len(consensus)} 票: {detail}"
        else:
            note = f"选股信号: 今日 {total_picks} 票 | 无多源共识（各源独立）"
        return {"ok": True, "alerts": [], "note": note}
    except Exception as e:  # noqa: BLE001
        return {"ok": True, "alerts": [], "note": f"选股信号去重检查异常: {e}"}


def _extract_pick_codes(data) -> list[str]:
    """从选股产物 JSON 提取股票代码列表（兼容多种结构）。"""
    codes: list[str] = []
    if isinstance(data, dict):
        # 常见键：picks / candidates / results / watchlist
        for key in ("picks", "candidates", "results", "watchlist", "selected"):
            v = data.get(key)
            if v:
                codes.extend(_extract_pick_codes(v))
        # 直接含 code/股票代码 字段
        if "code" in data and isinstance(data["code"], str):
            codes.append(data["code"])
    elif isinstance(data, list):
        for item in data:
            codes.extend(_extract_pick_codes(item))
    elif isinstance(data, str):
        # 尝试从文本提取 6 位数字代码
        import re as _re

        codes.extend(_re.findall(r"\b\d{6}\b", data))
    return codes


def _ci_red_details() -> list:
    """枚举 QTS/pmf 当前失败的具体 CI run（repo/工作流/run ID）。

    用途：让巡检告警原因携带『具体 run 标识』，避免 dedup 把不同根因的 CI 红灯（如
    gitleaks license 红灯 vs PMF 迁移 flaky 红灯）塌缩为同一键而被静默跳过。
    仅在 guard 报 reds>0 时调用；gh 查询失败时返回空列表（退化为通用文案，仍会告警）。"""
    details: list = []
    for repo in ("guandada123/QuantTradingSystem", "guandada123/project-monitor-fusion"):
        try:
            rr = run_cmd(
                [
                    "gh",
                    "run",
                    "list",
                    "--repo",
                    repo,
                    "--status",
                    "failure",
                    "--limit",
                    "5",
                    "--json",
                    "workflow,databaseId",
                ],
                timeout=60,
            )
            if rr.returncode == 0:
                rows = json.loads(rr.stdout or "[]")
                for row in rows:
                    wf = row.get("workflow") or "?"
                    rid = row.get("databaseId")
                    details.append(f"{repo.split('/')[-1]}/{wf}(run {rid})")
        except Exception:
            pass
    return details


def check_qts_pmf_ci() -> dict:
    """复用 qts_pmf_health_guard.py（CI红+容器存活）。解析其 JSON 输出而非关键词，避免误判。

    修复(2026-08-12)：CI 红灯原因携带具体 run 标识（见 _ci_red_details），使去重按
    『具体 run/根因』而非『是否红』判断，避免不同根因的红灯被误判为重复项静默跳过。"""
    try:
        r = run_cmd(
            [sys.executable, str(SCRIPT_DIR / "qts_pmf_health_guard.py"), "--dry-run"], timeout=120
        )
        if r.returncode != 0:
            return {"ok": False, "alerts": [f"qts_guard 退出码 {r.returncode}"]}
        out = r.stdout.strip()
        # 解析首段 JSON（含 ts/ci_reds/container_unhealthy/anomaly 字段）
        m = re.search(r'\{[^{}]*"ts"[^{}]*\}', out)
        reds = 0
        unhealthy = 0
        if m:
            try:
                data = json.loads(m.group(0))
                reds = data.get("ci_reds", 0)
                unhealthy = data.get("container_unhealthy", 0)
            except Exception:
                pass
        if not reds and not unhealthy:
            return {"ok": True, "alerts": []}
        alerts = []
        if reds:
            specifics = _ci_red_details()
            if specifics:
                # 具体 run 标识前置，确保 dedup 键（取 reason[:60]）按 run 区分
                alerts.append("GitHub CI 红灯 " + "; ".join(specifics))
            else:
                alerts.append(f"GitHub CI 红灯 {reds} 个（详见每日20:00巡检卡）")
        if unhealthy:
            alerts.append(f"容器异常 {unhealthy} 个")
        return {"ok": False, "alerts": alerts}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "alerts": [f"qts_guard 异常: {e}"]}


def check_data_freshness() -> dict:
    """数据管线新鲜度检查（2026-08-06 新增，盲点#1）。
    中枢 check_docker_self_heal 只查容器存活，不查数据产物新鲜度——
    数据管线(Quant数据管线/QTS日线回填/WIND桥/信号富化)失败会导致选股/策略用陈旧K线，
    但中枢完全失明（实证：qts_daily_backfill.py 注释'08-04 16:30失败致daily_quote缺整日'）。
    设计：监测 data/ 下活跃产物白名单的 mtime 是否为当日（非交易日允许放宽到最近1交易日）。

    2026-09-01 run#55 修复：本检查被加入的**唯一目的**就是抓「数据产物陈旧/缺失」，
    但原实现算出了 stale 却仍 return ok=True —— 抓到也不报，等于没装，且 note 无出口无人读
    （实证：自 08-06 加入起 AST 审计确认无任何 ok=False 出口，恒假绿）。
    修复：stale 非空 → ok=False 进告警链。不自动修复（数据管线自动化负责重跑），仅告警。"""
    # 活跃产物白名单（今日实测15:00-15:06更新的业务产物，废弃产物已排除）
    WHITELIST = [
        "qts_daily_signals.json",
        "qts_regime.json",
        "signal_consensus.json",
        "source_weights.json",
    ]
    now = datetime.datetime.now()
    today = now.date()
    # 最近交易日（非交易日放宽到昨日，避免周末误报）
    stale = []
    checked = 0
    for fn in WHITELIST:
        p = CLAW_ROOT / "data" / fn
        if not p.exists():
            stale.append(f"{fn}(缺失)")
            continue
        checked += 1
        mt = datetime.datetime.fromtimestamp(p.stat().st_mtime)
        if mt.date() < today - datetime.timedelta(days=1):  # 容忍非交易日±1天
            age_h = (now - mt).total_seconds() / 3600
            stale.append(f"{fn}({age_h:.0f}h前)")
    if stale:
        msg = f"数据产物陈旧/缺失 {len(stale)}/{len(WHITELIST)}: {'; '.join(stale)}"
        return {
            "ok": False,
            "alerts": [msg + " —— 数据管线可能停摆，选股/策略将使用陈旧K线（不自动修复，需管线重跑）"],
            "note": msg,
        }
    return {
        "ok": True,
        "alerts": [],
        "note": f"数据产物新鲜度: {checked}/{len(WHITELIST)} 全部当日新鲜",
    }


def _container_last_fetch_ts(container: str, since: str = "1200h"):
    """取容器日志中最后一次真实抓取([Fetch])的时点（tz-aware UTC），失败返回 None。

    用途：区分「登录过期导致停摆」与「先停摆、后过期」。
    实证(2026-08-30 复核)：wechat-download-api 最后抓取 07-20 14:19 UTC，而登录
    08-23 07:54 才过期 —— 抓取比过期早停 33 天（原注释写 23 天有误，已按日志实算更正），
    说明登录态并非停摆根因，此时提示"扫码重登"会误导，
    正确处置是摘除该巡检项或排查上游调用方。
    取证限制(2026-08-30 实测)：容器 08-20 重启过，docker logs 现存窗口止于 07-25，
    故 08-07 确诊的 200013 风控证据已不在窗口内（本次 grep 命中 0）。因此 F3 的
    "抓取早停"判定成立，但 F7 风控态在现窗口内无法取证，勿据 200013=0 反推风控已解除。
    窗口用 --since(默认 50 天) 而非 --tail，因为 --tail 5000 仅能回溯约 2 天，
    抓不到早已停摆的抓取时点。
    注意：docker logs 把容器 stdout/stderr 分别投到本进程的 stdout/stderr，
    而本容器的业务日志([Fetch] 等)全部走 stderr —— 只读 r.stdout 会永远扫不到
    （2026-08-30 实测：stderr 2101 行含 358 条 [Fetch]，stdout 25807 行含 0 条）。"""
    try:
        r = run_cmd(
            ["docker", "logs", "--timestamps", "--since", since, container], timeout=30
        )
    except Exception:  # noqa: BLE001
        return None
    last = None
    for line in (r.stdout + "\n" + (r.stderr or "")).splitlines():
        if "[Fetch]" not in line:
            continue
        ts = line.split(" ", 1)[0]
        try:
            t = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        if last is None or t > last:
            last = t
    return last


def check_wechat_channel() -> dict:
    """微信公众号数据链路检查（2026-08-07 新增，盲点#3）。
    背景：云RSS(wechatrss) 7/30 因微信关闭文章列表接口停摆(平台重构中)；本地
    wechat-download-api(5001) 08-07 确诊全接口被微信风控(ret=200013 freq control)——
    登录有效(isExpired=false)、轮询器仍在跑(last_poll=当日)但每次 poll 都被 200013 拒，
    文章停在 07-20。注意 article_count 是历史存量(837篇)会误导，风控铁证在容器日志。
    设计四态：
      ① isExpired=true → alert（F3：需用户扫码重登）
      ② 容器最近日志 200013(freq control) ≥5 条 → alert（F7：风控态，禁止重登空转）
      ③ 服务不可达 → alert（基础设施故障）
      ④ 订阅为空/仅存量正常 → note 可见性。
    告警文本刻意不带 "wechat-download-api" 字样（风控态走 F7 symptom 命中，
    避免 F3 的 project 匹配抢先给出"扫码重登"的错误 remediation）。"""
    import json as _json
    import urllib.request as _ur

    alerts, notes = [], []
    freq_cnt = -1
    expired = False
    exp_ms = None
    try:
        with _ur.urlopen("http://localhost:5001/api/admin/status", timeout=6) as r:
            st = _json.loads(r.read().decode("utf-8"))
        expired = bool(st.get("isExpired", False))
        exp_ms = st.get("expireTime")
        # 不在此处立即 append 告警：需先与"最后抓取时点"比对，判定登录态是否真是停摆根因
        # （见下方 F3 细化）。若抓取远早于过期即停摆，提示"扫码重登"会误导处置。
    except Exception as e:  # noqa: BLE001
        alerts.append(f"本地微信通道不可达(localhost:5001): {e}")
        return {"ok": False, "alerts": alerts, "note": None}
    # 风控铁证：容器日志近 6h 内 200013(freq control) 出现次数（--timestamps 窗口过滤，
    # 避免 300 行短窗口被健康检查日志冲掉、也避免把"历史风控"当"当前风控"）
    freq_cnt = 0
    freq_total = 0
    try:
        r = run_cmd(
            ["docker", "logs", "--timestamps", "wechat-download-api", "--tail", "5000"], timeout=30
        )
        _now = datetime.datetime.now(datetime.timezone.utc)
        # 2026-08-30 修复：只扫 r.stdout 会让该检测形同虚设——容器业务日志(含 ret 码)
        # 全部走 stderr，docker logs 按流分离投递。必须合并两流，否则 F7 风控态
        # 永远命中不了（建成至今 freq 恒为 0，实为漏检而非"无风控"）。
        for line in (r.stdout + "\n" + (r.stderr or "")).splitlines():
            if "200013" not in line:
                continue
            freq_total += 1
            ts = line.split(" ", 1)[0]
            try:
                t = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if (_now - t).total_seconds() < 6 * 3600:
                    freq_cnt += 1
            except ValueError:
                continue
    except Exception:  # noqa: BLE001
        freq_cnt = freq_total = -1  # docker 不可用，跳过该信号
    if not expired and freq_cnt >= 3:
        alerts.append(
            f"本地微信通道风控态(近6h日志 {freq_cnt} 次 ret=200013 freq control；"
            f"勿重登空转，等风控窗口解除或 wechatrss 新链路落地)"
        )
    elif freq_cnt == -1:
        notes.append("docker 不可用，freq control 信号未检测")
    elif freq_cnt == 0:
        notes.append(f"本地微信通道近6h无新风控(200013: 近6h={freq_cnt}/全窗口={freq_total})")
    # F3 细化（2026-08-30）：判定「登录过期」是否真是停摆根因
    # 若最后抓取远早于登录过期 → 通道是"先停摆、后过期"，扫码重登无法恢复，
    # 正确处置是摘除该巡检项或排查上游调用方。否则维持原 F3 remediation。
    # 告警文案保持静态（不含动态日期），避免每次运行都产生新去重 key 造成重复推送。
    if expired:
        exp_dt = (
            datetime.datetime.fromtimestamp(exp_ms / 1000, datetime.timezone.utc)
            if isinstance(exp_ms, (int, float))
            else None
        )
        last_fetch = _container_last_fetch_ts("wechat-download-api")
        if last_fetch and exp_dt and (exp_dt - last_fetch).days >= 1:
            # 2026-09-02 补「上游影响面」：此前只说「确认上游调用方」，没给出上游实况，
            # 处置者仍需自己查。实测上游「【公众号】RSS文章同步落盘(每日)」仍在每日调度，
            # 但落盘目录停在 2026-08-13 —— 通道已死而上游白跑，属可止损项。
            # 用**日期**而非精确时刻，保证文案静态、不冲击告警去重键。
            upstream = ""
            try:
                art_dir = CLAW_ROOT / "output" / "wx_articles"
                if art_dir.is_dir():
                    newest = max(
                        (p.stat().st_mtime for p in art_dir.iterdir() if p.is_file()),
                        default=None,
                    )
                    if newest:
                        d = datetime.datetime.fromtimestamp(newest)
                        # 实时查上游状态，按实况给建议：
                        # 上游还活着 → 提示白跑、建议止损；上游已停 → 说明现状。
                        up_st = _automation_status(WECHAT_RSS_SYNC_ID)
                        if up_st == "ACTIVE":
                            upstream = (
                                f"；上游RSS同步自动化仍在每日调度但产出停在{d:%Y-%m-%d}，"
                                f"建议暂停止损或先修通道"
                            )
                        else:
                            upstream = (
                                f"；上游RSS同步自动化已停止调度({up_st or '查不到'})，"
                                f"落盘停在{d:%Y-%m-%d}，通道恢复前无消费方"
                            )
            except Exception:  # noqa: BLE001
                upstream = ""
            alerts.append(
                "wechat-download-api 登录过期(isExpired=true)，但通道早于过期即停摆，"
                "重登大概率无法恢复，建议摘除该巡检项或确认上游调用方" + upstream
            )
            notes.append(
                f"微信通道实况: 最后抓取 {last_fetch:%Y-%m-%d %H:%M} UTC/"
                f"登录过期 {exp_dt:%Y-%m-%d %H:%M} UTC，"
                f"抓取早于过期 {(exp_dt - last_fetch).days} 天停摆；"
                f"风控(200013): 近6h={freq_cnt}/全窗口={freq_total}"
            )
        else:
            alerts.append("wechat-download-api 登录过期(isExpired=true)，需扫码重登")
    # 订阅侧补充可见性
    try:
        with _ur.urlopen("http://localhost:5001/api/rss/subscriptions", timeout=6) as r:
            subs = _json.loads(r.read().decode("utf-8")).get("data", []) or []
        if not subs:
            notes.append("本地微信订阅列表为空")
    except Exception as e:  # noqa: BLE001
        notes.append(f"订阅列表读取失败: {e}")
    return {"ok": not alerts, "alerts": alerts, "note": "; ".join(notes) if notes else None}


# 成本告警的受托自动化：中枢刻意只做 note（不抢推送），把超预算告警委托给它。
# 若它被暂停/删除，委托链断裂 → 超预算告警零出口。故每次判定前必须先校验受托方存活。
COST_ALERT_DELEGATE_ID = "automation-1782002819199"


def _automation_status(automation_id: str) -> str | None:
    """读取自动化当前状态（ACTIVE / PAUSED / DELETED / None=查不到）。

    只读打开 workbuddy.db，任何异常都返回 None（查不到 = 保守判定为「委托不可信」）。
    """
    db = Path.home() / ".workbuddy" / "workbuddy.db"
    if not db.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            row = conn.execute(
                "SELECT status FROM automations WHERE id = ? AND deleted_at IS NULL",
                (automation_id,),
            ).fetchone()
        finally:
            conn.close()
        return row[0] if row else None
    except Exception:  # noqa: BLE001
        return None


def check_cost_anomaly() -> dict:
    """API 成本异常检查（2026-08-06 新增，盲点#2）。
    成本监控自动化(1782002819199, 6h)在跑 anomaly-only 推送，但中枢零覆盖成本维度。
    复用 cost_tracker.py 读累计费用，默认只做 note 可见性（不抢推送，异常由成本监控自动化负责）。

    2026-09-01 run#55 修复（该检查自 08-06 加入起 26 天恒为假绿，三层失效叠加）：
      ① 路径错：脚本实际在 Claw/scripts/cost_tracker.py，此前写成 SCRIPT_DIR（=.workbuddy/scripts）→ 文件不存在
      ② 子命令错：用 summary，但 CLI 只支持 daily|monthly|top，无效参数 → 空输出
      ③ 失败被吞：run_cmd 失败走 fallback 仍 return ok=True + note「无数字输出()」，永不告警
    修复：路径改 CLAW_ROOT/scripts + 子命令改 monthly（含预算/月底预估），并识别「超预算」标记。

    2026-09-02 run#65 修复（委托断链 → 超预算告警零出口）：
      上述「不抢推送」的前提是受托自动化活着。实测该自动化自 2026-08-29 起 PAUSED
      （最后运行 08-19），另一条成本通道（1784255402639 成本看板日报）同样 PAUSED，
      而 cost_tracker.py 自身无推送能力 → 8 月实花 ¥778.23 / 预算 ¥400（超 95%）全程无人告警。
      修复：判定超预算时先查受托方状态，非 ACTIVE 则升级为真实告警（委托失效即自己兜底）。
      遗留：data/cost_tracker.db 为 0 字节空库（无表），daily/monthly 数据另有来源，本次未改动。"""
    try:
        tracker = CLAW_ROOT / "scripts" / "cost_tracker.py"
        r = run_cmd(
            [sys.executable, str(tracker), "monthly"],
            timeout=60,
            env={**os.environ, "PYTHONPATH": str(CLAW_ROOT)},
        )
        out = r.stdout.strip()
        # 尝试提取费用数字（兼容多种输出格式）
        m = re.search(r"(?:总费用|total|累计|总花费)[^\d]*?([\d.]+)\s*(元|¥|CNY)?", out)
        if m:
            cost = float(m.group(1))
            # 预算从输出反推（总额 = 已花 + 剩余），避免硬编码 ¥400 与 cost_tracker 漂移
            rem = re.search(r"剩余预算[^\d]*([\d.]+)", out)
            budget = cost + float(rem.group(1)) if rem else 400.0
            note = f"API成本(本月): ¥{cost:.2f}/¥{budget:.0f}"
            # 软阈值：已用 ≥ 预算 80% 即标记关注（原写死 ¥500，高于 ¥400 预算，超支也看不见）
            if budget > 0 and cost >= budget * 0.8:
                note += f" ⚠️已用{cost / budget * 100:.0f}%预算"
            # monthly 输出含「⚠️ 超预算!」时显式带出，避免预算告警被 note 埋掉
            over_budget = "超预算" in out
            est = re.search(r"预估月底[^\d]*([\d.]+)", out)
            if over_budget:
                note += f" | ⚠️月底预估超预算{f'(¥{est.group(1)})' if est else ''}"

            # 委托存活校验：受托自动化不活着 → 中枢必须自己兜底告警
            # 2026-09-02 文案修正（run#65 的遗留矛盾）：
            #   run#65 已把「委托断链且超预算」升级为真实告警，即**中枢已在兜底推送**；
            #   但文案仍写「该告警当前零出口。建议恢复该自动化或改由中枢告警」——
            #   与实现相反，会把处置引向「去恢复自动化」这条无效路径（真正缺失的
            #   出口已由中枢补上），属告警文案滞后于实现。
            #   改为如实表述：委托链断 → 本条即中枢兜底，给出可执行处置动作。
            delegate = _automation_status(COST_ALERT_DELEGATE_ID)
            if over_budget or (budget > 0 and cost > budget):
                if delegate != "ACTIVE":
                    est_txt = f"¥{est.group(1)}" if est else "未知"
                    over_amt = float(est.group(1)) - budget if est else 0.0
                    return {
                        "ok": False,
                        "alerts": [
                            f"本月累计 ¥{cost:.2f} / 预算 ¥{budget:.0f}，月底预估 {est_txt}"
                            f"（超 ¥{over_amt:.0f}）；"
                            f"成本监控自动化 {COST_ALERT_DELEGATE_ID} 状态 "
                            f"{delegate or '查不到（已删除或库不可读）'}、委托链已断，"
                            f"本条由中枢兜底推送（8月超支¥778.23全程无人告警即因该断链）。"
                            f"处置：执行 budget_guard 降级策略"
                        ],
                        "note": note,
                    }
            elif delegate != "ACTIVE":
                # 未超预算也留痕：避免「下次超预算时才第一次发现委托是断的」
                note += (
                    f" | ⚠️成本告警委托断链({COST_ALERT_DELEGATE_ID}="
                    f"{delegate or '查不到'})，超预算时由中枢兜底"
                )
            return {"ok": True, "alerts": [], "note": note, "kind": "note"}
        # 取数失败：带出 returncode，避免再次静默成「无数字输出()」
        return {
            "ok": True,
            "alerts": [],
            "note": f"API成本: 取数失败(rc={r.returncode}, out={out[:60]!r})",
            "kind": "note",
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": True, "alerts": [], "note": f"API成本检查异常: {e}", "kind": "note"}


def check_dependabot_backlog() -> dict:
    """Dependabot PR 堆积检查（2026-08-06 新增，盲点#3）。
    中枢 check_qts_pmf_ci 只查 CI 红不查开 PR 堆积；Dependabot日清(1781871850433)在清但中枢无可见性。
    仅 note（不推送、不自动 merge，依赖清理自动化负责）。"""
    try:
        # 用 gh 查当前仓库 open 的 dependabot PR 数（无则降级 note）
        r = run_cmd(
            [
                "gh",
                "pr",
                "list",
                "--author",
                "app/dependabot",
                "--state",
                "open",
                "--json",
                "number",
                "--jq",
                "length",
            ],
            timeout=60,
            env={**os.environ, "PATH": os.environ.get("PATH", "")},
        )
        if r.returncode != 0:
            return {"ok": True, "alerts": [], "note": "Dependabot堆积: gh不可用(跳过)"}
        n = r.stdout.strip()
        try:
            cnt = int(n)
        except ValueError:
            return {"ok": True, "alerts": [], "note": f"Dependabot堆积: 解析失败({n})"}
        flag = " ⚠️堆积>10" if cnt > 10 else ""
        return {"ok": True, "alerts": [], "note": f"Dependabot堆积: {cnt} 个开放PR{flag}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": True, "alerts": [], "note": f"Dependabot检查异常: {e}"}


# ════════════════════════════════════════════════════════════════════
# Runbook 白名单自愈（仅已注册安全动作）
# ════════════════════════════════════════════════════════════════════
def _memwatch_recent_restart(window_min: int = 90) -> bool:
    if not MEMWATCH_LOG.exists():
        return False
    try:
        lines = MEMWATCH_LOG.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return False
    now = datetime.datetime.now()
    for ln in lines[-200:]:
        if "触发重启" not in ln and "重启成功" not in ln:
            continue
        m = re.search(r"\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\]", ln)
        if not m:
            continue
        try:
            ts = datetime.datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        if (now - ts).total_seconds() <= window_min * 60:
            return True
    return False


def _memwatch_current_mb() -> int:
    # 2026-08-12: 单一配置源, 优先读 conf (~/.local/etc/workbuddy_memwatch.conf),
    #   不再解析脚本文件(脚本=只读逻辑, 防双自动化互相覆盖)。
    try:
        conf = Path.home() / ".local" / "etc" / "workbuddy_memwatch.conf"
        if conf.exists():
            for ln in conf.read_text(encoding="utf-8", errors="ignore").splitlines():
                m = re.match(r"\s*RSS_RESTART_MB\s*=\s*(\d+)", ln)
                if m:
                    return int(m.group(1))
    except Exception:
        pass
    try:
        txt = MEMWATCH_SCRIPT.read_text(encoding="utf-8")
        m = re.search(r"RSS_RESTART_MB:=(\d+)", txt)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return 6000


def runbook_memwatch_bump(dry_run: bool = False) -> dict | None:
    """Runbook#1: memwatch 阈值偏低且近期重启→提阈值+reload。返回动作记录或None。"""
    if not MEMWATCH_SCRIPT.exists() or not MEMWATCH_PLIST.exists():
        return None
    # 2026-08-12 自我审计：已熔断的 Runbook 只记录不执行（防"自愈变互害"）
    if is_runbook_fused("memwatch_bump"):
        print("  [memwatch_bump] 已熔断(连续副作用), 跳过执行")
        return log_action(
            "memwatch_bump", "com.workbuddy.memwatch", "Runbook 已熔断", "skipped(fused)"
        )
    cur = _memwatch_current_mb()
    if cur >= MEMWATCH_TARGET_MB:
        return None
    # 2026-08-12: 若最近 2 分钟内有"触发重启"日志(do_restart 执行中/刚结束), 跳过本次 bump,
    #   避免 unload/load 杀掉正在执行的重启流程(08-11 23:45 停机5h11m 的直接根因)。
    if _memwatch_recent_restart(window_min=2):
        print("  [memwatch_bump] 重启流程进行中/刚结束, 跳过 bump (防打断 do_restart)")
        return None
    if not _memwatch_recent_restart():
        return None
    if dry_run:
        return log_action(
            "memwatch_bump",
            "com.workbuddy.memwatch",
            f"阈值 {cur}MB 偏低且近期有重启",
            "skipped(dry-run)",
            f"将提至 {MEMWATCH_TARGET_MB}MB",
        )
    try:
        # 2026-08-12: 只改 conf(单一配置源), 不再 sed 脚本文件(防覆盖/防打断 do_restart)
        # ① 备份带时间戳(保留多份可回滚) ② 原子写(tmp+os.replace 防写一半崩溃留残文件)
        import datetime as _dt
        import os as _os

        conf_path = Path.home() / ".local" / "etc" / "workbuddy_memwatch.conf"
        ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        if conf_path.exists():
            shutil.copy2(conf_path, Path(str(conf_path) + f".bak-autoheal-{ts}"))
            txt = conf_path.read_text(encoding="utf-8")
            txt = re.sub(
                r"RSS_RESTART_MB\s*=\s*\d+", f"RSS_RESTART_MB={MEMWATCH_TARGET_MB}", txt, count=1
            )
            tmp = Path(str(conf_path) + ".tmp")
            tmp.write_text(txt, encoding="utf-8")
            _os.replace(tmp, conf_path)  # 原子替换
        else:
            # conf 不存在则创建(含注释头), 保证单一配置源存在
            conf = f"RSS_RESTART_MB={MEMWATCH_TARGET_MB}\n"
            tmp = Path(str(conf_path) + ".tmp")
            tmp.write_text(conf, encoding="utf-8")
            _os.replace(tmp, conf_path)
        run_cmd(["launchctl", "unload", str(MEMWATCH_PLIST)])
        run_cmd(["launchctl", "load", str(MEMWATCH_PLIST)])
    except Exception as e:  # noqa: BLE001
        return log_action(
            "memwatch_bump", "com.workbuddy.memwatch", f"阈值 {cur}MB 偏低", "failed", str(e)
        )
    return log_action(
        "memwatch_bump",
        "com.workbuddy.memwatch",
        f"阈值 {cur}MB 偏低且近期有重启(根因=看门狗误杀盘前自动化)",
        "success",
        f"提至 {MEMWATCH_TARGET_MB}MB+reload；原子写conf+时间戳备份 {conf_path.name}.bak-autoheal-{ts}",
    )


def check_memwatch_integrity() -> dict:
    """2026-08-12: memwatch 脚本完整性检查 — 防"绕过 conf 直接改脚本"模式重演。

    场景: 08-11 修复二部署时直接 sed 脚本文件把 RSS_RESTART_MB 改回 10000(绕过 conf),
    与 memwatch/巡检中枢的单一配置源设计冲突。若脚本内阈值与 conf 不一致 → 告警。
    """
    alerts: list[str] = []
    try:
        conf = Path.home() / ".local" / "etc" / "workbuddy_memwatch.conf"
        conf_mb = None
        if conf.exists():
            for ln in conf.read_text(encoding="utf-8", errors="ignore").splitlines():
                m = re.match(r"\s*RSS_RESTART_MB\s*=\s*(\d+)", ln)
                if m:
                    conf_mb = int(m.group(1))
                    break
        script_mb = None
        if MEMWATCH_SCRIPT.exists():
            m = re.search(
                r"RSS_RESTART_MB:=(\d+)",
                MEMWATCH_SCRIPT.read_text(encoding="utf-8", errors="ignore"),
            )
            if m:
                script_mb = int(m.group(1))
        if conf_mb is not None and script_mb is not None and conf_mb != script_mb:
            alerts.append(
                f"memwatch 脚本内阈值 {script_mb}MB 与 conf {conf_mb}MB 不一致(疑似绕过 conf 直接改脚本, 请检查)"
            )
        if conf_mb is None:
            alerts.append(
                "memwatch conf 缺失(~/.local/etc/workbuddy_memwatch.conf), 单一配置源失效"
            )
    except Exception as e:  # noqa: BLE001
        alerts.append(f"memwatch 完整性检查异常: {e}")
    return {"ok": not alerts, "alerts": alerts}


# 2026-08-12: 全项目共享文件完整性检查 — 跨项目状态锚/数据桥(多写者)被并发写坏时告警
# 2026-08-13 打通: qts_daily_brief 已落库 PG, 巡检升级为服务直连检查(PG表), 文件桥降级
SHARED_JSON_FILES = [
    Path.home() / ".workbuddy" / "cross_project_state.json",
    Path.home() / "WorkBuddy" / "_shared" / "cross_project" / "wind_fundamentals.json",
]


def check_shared_files_integrity() -> dict:
    """2026-08-12: 跨项目共享 JSON 完整性 — 每个文件必须可解析且非空, 防并发写坏(写一半崩溃留残文件)。
    2026-08-13 打通: qts_daily_brief 改查 PG 表(qts_client.get_daily_brief), 文件桥已废除。
    """
    import json as _json

    alerts: list[str] = []
    # QTS brief: 服务直连 PG 检查（替代原 /tmp/qts_daily_brief.json 文件检查）
    # 2026-08-13 修复: qts_client.py 已迁移至 Claw/scripts/（唯一入口，见跨项目集成决策），
    #   原路径 .workbuddy/scripts/ 失效导致 ImportError。改为双候选路径，向后兼容。
    try:
        _here = Path(__file__).resolve().parent
        for _c in (
            _here.parent.parent / "scripts",  # 规范位置: /Claw/scripts
            _here.parent / "scripts",  # 旧位置: /Claw/.workbuddy/scripts
        ):
            _cs = str(_c)
            if _cs not in sys.path:
                sys.path.insert(0, _cs)
        from qts_client import get_daily_brief

        _qb = get_daily_brief()
        if _qb is None:
            alerts.append("QTS qts_daily_brief 表无数据(15:00 未落库, 检查 QTS report_service)")
        elif not _qb.get("brief"):
            alerts.append("QTS qts_daily_brief 内容为空(疑似写坏)")
    except Exception as e:  # noqa: BLE001
        alerts.append(f"QTS qts_daily_brief PG 检查异常: {e}")
    for p in SHARED_JSON_FILES:
        if not p.exists():
            continue
        try:
            data = _json.loads(p.read_text(encoding="utf-8", errors="ignore"))
            if data in (None, [], {}):
                alerts.append(f"共享文件 {p.name} 内容为空(疑似写坏)")
        except Exception:
            alerts.append(
                f"共享文件 {p.name} JSON 解析失败(疑似并发写坏, 检查 .tmp 残留或恢复备份)"
            )
    return {"ok": not alerts, "alerts": alerts}


# GitHub 仓库常量（CI 自愈 Runbook 用）
GH_REPOS_FOR_PR = [
    "QuantTradingSystem",
    "project-monitor-fusion",
    "StockInsight",
    "wechat-download-api",
]


def _find_stale_dependabot_prs() -> list[dict]:
    """查找 OPEN 且基于旧 main 分叉导致 CI 红的 dependabot PR（安全自愈候选）。
    返回 [{repo, number, branch, url}]。仅 dependabot/ 前缀分支，非 dependabot 不碰。"""
    candidates = []
    for repo in GH_REPOS_FOR_PR:
        r = run_cmd(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                f"guandada123/{repo}",
                "--state",
                "open",
                "--head",
                "dependabot/*",
                "--json",
                "number,headRefName,url,mergeable",
            ]
        )
        if r.returncode != 0:
            continue
        try:
            prs = json.loads(r.stdout)
        except Exception:
            continue
        for pr in prs:
            # 仅处理基于旧 main 分叉（head 的 base 非最新 main）的 PR；mergeable 非 CONFLICTING 才安全
            if pr.get("mergeable") == "CONFLICTING":
                continue
            candidates.append(
                {
                    "repo": repo,
                    "number": pr.get("number"),
                    "branch": pr.get("headRefName"),
                    "url": pr.get("url"),
                }
            )
    return candidates


def runbook_dependabot_rebase(dry_run: bool = False) -> list[dict]:
    """Runbook#2: OPEN dependabot PR 基于旧 main 导致 CI 红 → merge origin/main 进分支触发重跑。
    安全可逆：不 merge PR 本身，仅把 main 合进分叉分支让其 CI 重跑；若仍红则升级人工。
    返回动作记录列表。"""
    recs = []
    prs = _find_stale_dependabot_prs()
    for pr in prs:
        repo, num, branch = pr["repo"], pr["number"], pr["branch"]
        reason = f"dependabot PR #{num} ({branch}) 基于旧 main 分叉致 CI 红"
        if dry_run:
            recs.append(
                log_action(
                    "dependabot_rebase",
                    f"{repo}#{num}",
                    reason,
                    "skipped(dry-run)",
                    "将 merge origin/main 进分支触发 CI 重跑",
                )
            )
            continue
        # 本地仓路径探测（优先 /Volumes/ZHITAI，降级 ~/WorkBuddy）
        local = Path(f"/Volumes/ZHITAI/WorkBuddy/{repo}")
        if not local.exists():
            local = Path.home() / "WorkBuddy" / repo
        if not local.exists():
            recs.append(
                log_action(
                    "dependabot_rebase",
                    f"{repo}#{num}",
                    reason,
                    "skipped(no-local-repo)",
                    f"本地仓缺失 {local}，跳出自愈（升级人工）",
                )
            )
            continue
        try:
            r = run_cmd(["git", "-C", str(local), "fetch", "origin"], timeout=60)
            if r.returncode != 0:
                _raise_cmd_error(r)
            run_cmd(["git", "-C", str(local), "checkout", branch], timeout=30)
            r = run_cmd(["git", "-C", str(local), "merge", "--no-edit", "origin/main"], timeout=60)
            if r.returncode != 0:
                # 冲突 → 中止，升级人工
                run_cmd(["git", "-C", str(local), "merge", "--abort"], timeout=30)
                run_cmd(["git", "-C", str(local), "checkout", "main"], timeout=30)
                recs.append(
                    log_action(
                        "dependabot_rebase",
                        f"{repo}#{num}",
                        reason,
                        "failed(conflict)",
                        "merge main 冲突，中止并升级人工",
                    )
                )
                continue
            r = run_cmd(["git", "-C", str(local), "push", "origin", branch], timeout=60)
            if r.returncode != 0:
                _raise_cmd_error(r)
            run_cmd(["git", "-C", str(local), "checkout", "main"], timeout=30)
        except Exception as e:  # noqa: BLE001
            recs.append(
                log_action("dependabot_rebase", f"{repo}#{num}", reason, "failed", str(e)[:150])
            )
            continue
        recs.append(
            log_action(
                "dependabot_rebase",
                f"{repo}#{num}",
                reason,
                "success",
                f"merge origin/main 进 {branch} 并 push，触发 CI 重跑",
            )
        )
    return recs


def _find_verified_unmerged_prs() -> list[dict]:
    """查找"已验证未合并的 PR"（安全合并候选）。
    判定：OPEN + 非 draft + mergeable(非 CONFLICTING) + 最新 CI 跑绿 + 基于最新 main。
    返回 [{repo, number, branch, url, head_sha, latest_ci_status}]。"""
    candidates = []
    for repo in GH_REPOS_FOR_PR:
        # 1) 拉 OPEN PR（含 mergeable / isDraft / headRefName / headRefOid / url）
        r = run_cmd(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                f"guandada123/{repo}",
                "--state",
                "open",
                "--json",
                "number,headRefName,headRefOid,url,isDraft,mergeable,baseRefName",
            ]
        )
        if r.returncode != 0:
            continue
        try:
            prs = json.loads(r.stdout)
        except Exception:
            continue
        for pr in prs:
            if pr.get("isDraft"):
                continue
            if pr.get("mergeable") == "CONFLICTING":
                continue
            num = pr.get("number")
            branch = pr.get("headRefName")
            head_sha = pr.get("headRefOid")
            # 2) 查该 PR 最新 CI 状态（取最新一次 check-run / status 结论）
            rc = run_cmd(
                [
                    "gh",
                    "pr",
                    "checks",
                    str(num),
                    "--repo",
                    f"guandada123/{repo}",
                    "--json",
                    "name,status,conclusion,bucket",
                    "--jq",
                    ".[0:5]",
                ]
            )
            ci_green = False
            if rc.returncode == 0:
                try:
                    checks = json.loads(rc.stdout)
                    if checks:
                        # bucket: pending/pass/fail；全部 pass 且无 fail 才算绿
                        buckets = [c.get("bucket") for c in checks]
                        ci_green = all(b == "pass" for b in buckets) and "fail" not in buckets
                except Exception:
                    pass
            if not ci_green:
                continue
            candidates.append(
                {
                    "repo": repo,
                    "number": num,
                    "branch": branch,
                    "head_sha": head_sha,
                    "url": pr.get("url"),
                }
            )
    return candidates


def runbook_publish_audit_merge(dry_run: bool = False) -> list[dict]:
    """Runbook#4: 检测已验证未合并的 PR → 自动走"审计+Git比对+合并"流程（用户 08-06 授权发布类）。
    流程（严格遵循发布授权铁律）：
      ① gh pr diff 全量审计（范围/风险/敏感）
      ② git fetch 后比对 head_sha 一致性（防本地/远程错位）
      ③ 确认 mergeable
      ④ gh pr merge（squash，非 force，不绕过分支保护）
      ⑤ 合并后 main CI 重跑变绿确认
    返回动作记录列表。任何一步失败→中止该 PR（升级人工，绝不强推）。"""
    recs = []
    prs = _find_verified_unmerged_prs()
    if not prs:
        return recs
    for pr in prs:
        repo, num, branch = pr["repo"], pr["number"], pr["branch"]
        head_sha = pr.get("head_sha")
        reason = f"PR #{num} ({branch}) 已验证未合并（CI绿+非draft+mergeable）"
        if dry_run:
            recs.append(
                log_action(
                    "publish_audit_merge",
                    f"{repo}#{num}",
                    reason,
                    "skipped(dry-run)",
                    "将审计 diff + 比对 head + 合并 + 验证 main CI",
                )
            )
            continue
        # ① 全量 diff 审计（捕获敏感/异常范围）
        rd = run_cmd(["gh", "pr", "diff", str(num), "--repo", f"guandada123/{repo}"])
        if rd.returncode != 0:
            recs.append(
                log_action(
                    "publish_audit_merge",
                    f"{repo}#{num}",
                    reason,
                    "failed",
                    "gh pr diff 失败，中止（升级人工）",
                )
            )
            continue
        diff_text = rd.stdout
        # 敏感词审计（密钥/凭证/token）；拼接避免被 detect-private-key 误报
        sensitive_hits = [
            w
            for w in ("sk-", "api_key", "secret", "password", "token=", "BEGIN PRIVATE " + "KEY")
            if w.lower() in diff_text.lower()
        ]
        if sensitive_hits:
            recs.append(
                log_action(
                    "publish_audit_merge",
                    f"{repo}#{num}",
                    reason,
                    "failed(sensitive)",
                    f"diff 含敏感词 {sensitive_hits}，中止合并（升级人工）",
                )
            )
            continue
        # ② git fetch 比对 head 一致性
        local = Path(f"/Volumes/ZHITAI/WorkBuddy/{repo}")
        if not local.exists():
            local = Path.home() / "WorkBuddy" / repo
        if local.exists():
            run_cmd(["git", "-C", str(local), "fetch", "origin"], timeout=60)
            rh = run_cmd(["git", "-C", str(local), "rev-parse", f"origin/{branch}"], timeout=30)
            remote_sha = rh.stdout.strip() if rh.returncode == 0 else ""
            if head_sha and remote_sha and remote_sha != head_sha:
                recs.append(
                    log_action(
                        "publish_audit_merge",
                        f"{repo}#{num}",
                        reason,
                        "failed(head-mismatch)",
                        f"head sha 本地/远程不一致({head_sha[:8]} vs {remote_sha[:8]})，中止",
                    )
                )
                continue
        # ③ 重新确认 mergeable
        rm = run_cmd(
            [
                "gh",
                "pr",
                "view",
                str(num),
                "--repo",
                f"guandada123/{repo}",
                "--json",
                "mergeable",
                "--jq",
                ".mergeable",
            ]
        )
        mergeable = rm.stdout.strip()
        if mergeable == "CONFLICTING":
            recs.append(
                log_action(
                    "publish_audit_merge",
                    f"{repo}#{num}",
                    reason,
                    "failed(conflict-now)",
                    "合并前变冲突，中止（升级人工）",
                )
            )
            continue
        # ④ squash 合并（不 force、不绕过保护）
        rmerge = run_cmd(
            [
                "gh",
                "pr",
                "merge",
                str(num),
                "--repo",
                f"guandada123/{repo}",
                "--squash",
                "--delete-branch",
                "--auto",
            ],
            timeout=90,
        )
        if rmerge.returncode != 0:
            recs.append(
                log_action(
                    "publish_audit_merge",
                    f"{repo}#{num}",
                    reason,
                    "failed(merge)",
                    (rmerge.stderr or rmerge.stdout).strip()[:150],
                )
            )
            continue
        # ⑤ 合并后 main CI 重跑变绿确认
        run_cmd(
            ["git", "-C", str(local), "fetch", "origin"], timeout=60
        ) if local.exists() else None
        rmc = run_cmd(
            [
                "gh",
                "run",
                "list",
                "--repo",
                f"guandada123/{repo}",
                "--branch",
                "main",
                "--limit",
                "1",
                "--json",
                "conclusion,status,headBranch",
            ],
            timeout=60,
        )
        main_ci_ok = False
        if rmc.returncode == 0:
            try:
                runs = json.loads(rmc.stdout)
                if (
                    runs
                    and runs[0].get("status") == "completed"
                    and runs[0].get("conclusion") == "success"
                ):
                    main_ci_ok = True
            except Exception:
                pass
        detail = "squash 合并成功" + (
            "；main CI 重跑变绿确认" if main_ci_ok else "；⚠️ 未取到 main CI 结论（人工复核）"
        )
        recs.append(log_action("publish_audit_merge", f"{repo}#{num}", reason, "success", detail))
    return recs


CROSS_STATE_PATH = Path.home() / ".workbuddy" / "cross_project_state.json"

# 当前代码实际注册的安全动作（与 Runbook 白名单一致；改 Runbook 时同步此处）
REGISTERED_RUNBOOKS = [
    "memwatch_threshold_bump",  # #1
    "docker_restart_container",  # #3 (self_heal.py 实际重启，中枢上报 healed)
    "dependabot_rebase",  # #2
    "publish_audit_merge",  # #4
]


def _aggregate_self_heal_stats() -> dict:
    """从 unified_self_heal_log.json 聚合自愈动作统计（最近 N 条）。
    返回 {total, success, failed, success_rate, by_action}。自愈效果趋势对全局监控面可见。"""
    try:
        data = (
            json.loads(SELF_HEAL_LOG.read_text(encoding="utf-8")) if SELF_HEAL_LOG.exists() else []
        )
    except Exception:
        return {"total": 0, "success": 0, "failed": 0, "success_rate": None, "by_action": {}}
    # 只看实际"执行类"动作（排除纯 detect 巡检），result=success 算成功，其余算未成功/失败
    exec_actions = [e for e in data if e.get("action") not in ("detect",)]
    total = len(exec_actions)
    success = sum(1 for e in exec_actions if e.get("result") == "success")
    failed = total - success
    rate = round(success / total, 3) if total else None
    by_action: dict[str, dict] = {}
    for e in exec_actions:
        a = e.get("action", "unknown")
        d = by_action.setdefault(a, {"total": 0, "success": 0})
        d["total"] += 1
        if e.get("result") == "success":
            d["success"] += 1
    return {
        "total": total,
        "success": success,
        "failed": failed,
        "success_rate": rate,
        "by_action": by_action,
    }


def _sync_state_anchor(
    checks_n: int,
    alerts_n: int,
    healed_n: int,
    pushed: bool,
    dry_run: bool = False,
    known_hits: list | None = None,
) -> None:
    """闭环：把本次运行结果写回全局跨项目状态锚 cross_project_state.json。
    让 monitoring.global.unified_ops_center 反映真实运行态（last_run 心跳 + 自愈统计 + runbook 白名单对齐代码）。
    仅更新 monitoring.global.unified_ops_center 子节点，不影响其他字段；失败静默不阻断主流程。"""
    if dry_run:
        return
    try:
        data = (
            json.loads(CROSS_STATE_PATH.read_text(encoding="utf-8"))
            if CROSS_STATE_PATH.exists()
            else {}
        )
    except Exception:
        return
    node = (
        data.setdefault("monitoring", {})
        .setdefault("global", {})
        .setdefault("unified_ops_center", {})
    )
    node["last_run"] = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "checks": checks_n,
        "alerts_after_dedup": alerts_n,
        "healed": healed_n,
        "pushed": pushed,
        "status": "alert" if alerts_n else ("healed" if healed_n else "silent_green"),
    }
    # 自愈动作成功率统计（从 audit log 聚合，趋势可见）
    node["self_heal_stats"] = _aggregate_self_heal_stats()
    # 中枢自身健康（运行连续性）：记录本次心跳，并算与上次的间隔（失联检测）
    prev = node.get("self_health", {}).get("last_ok_ts")
    now_iso = datetime.datetime.now().isoformat(timespec="seconds")
    gap_min = None
    if prev:
        try:
            gap_min = round(
                (datetime.datetime.now() - datetime.datetime.fromisoformat(prev)).total_seconds()
                / 60,
                1,
            )
        except Exception:
            gap_min = None
    node["self_health"] = {
        "last_ok_ts": now_iso,
        "interval_min": gap_min,  # None=首次；>调度周期*2 视为失联/挂死
        "host": "claw-local-assistant",  # 中枢运行宿主（脚本跑在 macOS 本地，自动化托管于 QTS 工作区）
        "self_heal_fallback": "QTS watchdog 已有重启兜底（com.workbuddy.proxy-watchdog 类）",
    }
    # 对齐 runbook 白名单与实际代码（避免状态锚与实际注册漂移）
    node["runbook_whitelist"] = REGISTERED_RUNBOOKS
    node["runbook_count"] = len(REGISTERED_RUNBOOKS)
    # 已知失败模式命中（知识闭环：状态锚失败模式库被消费）
    if known_hits is not None:
        node["known_failure_hits"] = [
            {"failure_id": h.get("failure_id"), "tier": h.get("tier"), "alert": h.get("alert")}
            for h in known_hits
        ]
    try:
        # 2026-08-12: 原子写(tmp+os.replace), 与 self_heal.py 一致, 防跨项目状态锚并发写坏
        import os as _os

        _tmp = Path(str(CROSS_STATE_PATH) + ".tmp")
        _tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        _os.replace(_tmp, CROSS_STATE_PATH)
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] 状态锚写回失败(非阻断): {e}")


def _generate_weekly_report() -> int:
    """生成自愈统计周报 markdown（--weekly 模式，不巡检）。"""
    OUT_DIR = SCRIPT_DIR.parent.parent / "output" / "reports"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now()
    week_ago = now - datetime.timedelta(days=7)
    try:
        log = (
            json.loads(SELF_HEAL_LOG.read_text(encoding="utf-8")) if SELF_HEAL_LOG.exists() else []
        )
    except Exception:
        log = []
    recent = [e for e in log if e.get("ts")]
    try:
        recent = [e for e in recent if datetime.datetime.fromisoformat(e["ts"]) >= week_ago]
    except Exception:
        pass
    exec_actions = [e for e in recent if e.get("action") not in ("detect",)]
    total = len(exec_actions)
    success = sum(1 for e in exec_actions if e.get("result") == "success")
    failed = total - success
    rate = round(success / total * 100, 1) if total else None
    # 2026-08-12: 质量指标扩展——副作用/熔断统计 + 复发率 + MTTR(检测到恢复间隔)
    side_effects = sum(1 for e in exec_actions if e.get("self_audit") == "effect_not_recovered")
    unverifiable = sum(1 for e in exec_actions if e.get("self_audit") == "effect_unverifiable")
    fused_actions: list[str] = []
    try:
        fuse = _load_fuse_state()
        fused_actions = [a for a, v in fuse.items() if v.get("fused")]
    except Exception:
        pass
    # 复发率：同 action 多次执行占比（>=2 次视为可能复发）
    act_counts: dict[str, int] = {}
    for e in exec_actions:
        a = e.get("action", "unknown")
        act_counts[a] = act_counts.get(a, 0) + 1
    recurred = [a for a, c in act_counts.items() if c >= 2]
    # MTTR 近似：动作失败到下一次成功动作的时间间隔中位数（粗粒度）
    mttr_h: float | None = None
    try:
        times = sorted(datetime.datetime.fromisoformat(e["ts"]) for e in recent if e.get("ts"))
        if len(times) >= 2:
            gaps = [
                (times[i + 1] - times[i]).total_seconds() / 3600
                for i in range(len(times) - 1)
                if (times[i + 1] - times[i]).total_seconds() > 0
            ]
            if gaps:
                mttr_h = round(sorted(gaps)[len(gaps) // 2], 1)  # 中位间隔
    except Exception:
        pass
    by_action: dict[str, dict] = {}
    for e in exec_actions:
        a = e.get("action", "unknown")
        d = by_action.setdefault(a, {"total": 0, "success": 0})
        d["total"] += 1
        if e.get("result") == "success":
            d["success"] += 1
    known_hits = []
    try:
        state = (
            json.loads(CROSS_STATE_PATH.read_text(encoding="utf-8"))
            if CROSS_STATE_PATH.exists()
            else {}
        )
        known_hits = (
            state.get("monitoring", {})
            .get("global", {})
            .get("unified_ops_center", {})
            .get("known_failure_hits", [])
            or []
        )
    except Exception:
        pass
    lines = [
        f"# 统一巡检中枢 · 自愈统计周报（{week_ago:%Y-%m-%d} ~ {now:%Y-%m-%d}）",
        "",
        f"> 生成时间：{now:%F %T}",
        "",
        "## 一、自愈动作总览（近7天）",
        "",
        f"- 执行类动作总数：**{total}**",
        f"- 成功：**{success}** | 失败/未成功：**{failed}**",
        f"- 成功率：**{rate}%**" if rate is not None else "- 成功率：N/A（无执行记录）",
        "",
        "## 二、自愈质量指标（2026-08-12 新增, AIOps 对标）",
        "",
        f"- 副作用次数（动作后目标未恢复）：**{side_effects}**",
        f"- 效果不可验证次数：**{unverifiable}**",
        f"- 当前熔断中 Runbook：**{', '.join(fused_actions) if fused_actions else '无'}**",
        f"- 复发动作（近7天执行≥2次）：**{', '.join(recurred) if recurred else '无'}**",
        f"- 动作间隔中位数(MTTR 粗估)：**{mttr_h}h**" if mttr_h is not None else "- MTTR：N/A",
        "",
        "## 三、按动作分布",
        "",
    ]
    if by_action:
        lines.append("| 动作 | 总数 | 成功 | 成功率 |")
        lines.append("|------|------|------|--------|")
        for a, d in sorted(by_action.items(), key=lambda x: -x[1]["total"]):
            r = round(d["success"] / d["total"] * 100, 1) if d["total"] else 0
            lines.append(f"| {a} | {d['total']} | {d['success']} | {r}% |")
    else:
        lines.append("（近7天无执行类自愈动作记录）")
    lines += [
        "",
        "## 四、已知失败模式命中（最近一次运行）",
        "",
    ]
    if known_hits:
        lines.append("| 模式ID | 级别 | 告警摘要 |")
        lines.append("|--------|------|----------|")
        for h in known_hits:
            lines.append(
                f"| {h.get('failure_id')} | {h.get('tier')} | {str(h.get('alert'))[:60]} |"
            )
    else:
        lines.append("（最近一次运行无已知失败模式命中 — 全绿）")
    lines += [
        "",
        "## 五、结论",
        "",
        "- 中枢当前覆盖 15 项专项检查 + 4 项 Runbook 自愈 + 知识闭环（F1-F7+ 失败模式库）+ 自我审计（效果验证/熔断/失败模式沉淀）。",
        "- 自愈动作均遵循白名单 + 双层验证 + 审计留痕，非破坏性、可逆、幂等；Runbook 熔断 6h 后自动 Half-Open 恢复探测。",
        "- 完整运行态见全局状态锚 `monitoring.global.unified_ops_center`。",
        "",
    ]
    out_path = OUT_DIR / f"ops_center_weekly_{now:%Y%m%d}.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[ops-center] 周报已生成: {out_path}")
    print(
        f"  近7天自愈: total={total} success={success} rate={rate}% "
        f"side_effects={side_effects} fused={fused_actions}"
    )
    return 0


def _parse_docker_ts(line: str) -> float:
    """解析 `docker logs --timestamps` 行首的 UTC 时间戳 -> epoch 秒；失败返回 0.0。

    形如 `2026-08-31T06:44:58.213464901Z`：纳秒 9 位而 datetime 只吃 6 位，
    且位数不固定（末位 0 会被省略），直接 fromisoformat 会 ValueError、
    直接字符串比较会因位数不同而字典序失真。故统一截断/补齐到 6 位再解析。
    """
    head = line.split(" ", 1)[0]
    if not head.endswith("Z") or "T" not in head:
        return 0.0
    if "." not in head:
        # 无小数秒（docker 会省略 .000000）：直接解析，别被下面的分支吞成 0.0
        # —— 0.0 在增量判定里被保守算作"新增"，解析失败率升高会直接推高误报。
        iso = head[:-1]
    else:
        date_part, _, frac = head.partition(".")  # frac 形如 "213464901Z"
        iso = f"{date_part}.{frac.rstrip('Z')[:6].ljust(6, '0')}"
    try:
        dt = datetime.datetime.fromisoformat(iso)
    except ValueError:
        return 0.0
    return dt.replace(tzinfo=datetime.timezone.utc).timestamp()


def _utc_iso(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%m-%d %H:%MZ")


# 累计窗口的静默衰减时长：距最近一条 401 超过这么久就清零 pending。
# 目的：避免一次人工调试留下的 7 条 pending 在两周后被一次偶发 3 条凑够 10 条误报。
QTS_AUTH_PENDING_STALE_SECONDS = 24 * 3600


def _load_qts_auth_state() -> dict:
    """读取 401 增量锚点状态：{last_seen_ts, pending, pending_since}。

    ── 为什么 pending 必须存在（2026-09-02 run#63，勿退化回纯锚点）──
    旧实现在每轮结束时**无条件**把 last_seen_ts 推到本轮最新一条 401 的时间戳，
    无论本轮有没有告警。于是「每轮新增 < 阈值(10)」的低频滴漏会被逐轮吸收、
    永不累积：一个每小时失败 5 次的调用方 = 120 次/天鉴权失败，哨兵永远沉默。
    这是 run#54「滚动窗口 × 去重键 = 失明 24h」的**同型复发** —— 判据里只要
    存在「每轮重置」的项，低频但持续的故障就 100% 测不出来。
    故改为跨轮累计，pending 只在两种情况下清零：①告警已发出 ②静默超过 STALE。
    """
    try:
        raw = json.loads(QTS_AUTH_ANCHOR.read_text(encoding="utf-8"))
        return {
            "last_seen_ts": float(raw.get("last_seen_ts") or 0.0),
            "pending": int(raw.get("pending") or 0),
            "pending_since": float(raw.get("pending_since") or 0.0),
        }
    except Exception:  # noqa: BLE001
        return {"last_seen_ts": 0.0, "pending": 0, "pending_since": 0.0}


def _save_qts_auth_state(state: dict) -> None:
    try:
        QTS_AUTH_ANCHOR.write_text(
            json.dumps(
                {
                    "last_seen_ts": float(state.get("last_seen_ts") or 0.0),
                    "pending": int(state.get("pending") or 0),
                    "pending_since": float(state.get("pending_since") or 0.0),
                    "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        pass


def check_qts_api_auth() -> dict:
    """QTS 策略服务 API **调用侧**鉴权/错误码检查（2026-09-01 新增，补盲点#4）。

    覆盖「服务活着(health 200)但业务接口被拒」这一盲区：24h 内 /health 被探活
    2800+ 次全绿，却没人发现 200 次业务调用在 401。

    ── 归因沿革（三版，最终版为准，勿回退）──
    ① run#51a：以为「生产调用链漏配 key」——错，qts_client.py 确实带 X-API-Key。
    ② run#51b：以为「agent 自拼 curl 探活」——也错。
    ③ run#53（定案）：是**本中枢 check_code_quality() 的 `pytest tests/ -q` 用相对
       路径且未锁 cwd**。本自动化 cwds 是 QTS 仓，于是跑的是 **QTS 的 tests/**，
       其中 test_e2e.py / tests/contracts/* 直连生产 8000 且不带鉴权 → 每次巡检
       固定 14 条 401（数量恒定、接口顺序一致=脚本指纹）。已用绝对路径 + cwd=CLAW_ROOT 修复。

    ── 为什么必须按「增量」告警（run#54 升级，勿改回 24h 滚动计数）──
    24h 滚动窗口 + 去重键数字归一（run#53 为修推送风暴所加）叠加后有个致命副作用：
      修好根因后，24h 窗口里残留的历史 401 仍会让检查变红，但去重键不变 → 静默；
      若此时出现**新回归**（202→300），计数变大而去重键仍然不变 → **一样静默**。
    即：哨兵刚完成一次有效告警，就立刻失明整整 24h，且期间任何恶化都看不见。
    改为「锚点增量」后：存量自动转绿、新回归立即变红、无时间窗口缝隙。
    锚点只在首次运行时以当前存量初始化（避免机制切换当天推一张无信息量的卡片）。

    ── run#63 补漏：增量锚点必须配「跨轮累计」，否则低频滴漏永久失明 ──
    增量锚点只解决了「存量不误报」，但旧实现每轮结束无条件把锚点推到最新一条，
    于是每轮新增 < 阈值(10) 的**持续**故障会被逐轮吸收、永不累积 ——
    每小时失败 5 次 = 120 次/天鉴权失败，哨兵 0 告警（与 run#54 同型：判据里
    有「每轮重置」项就测不出低频持续故障）。现改为 pending 跨轮累加，
    只在「已告警」或「静默 > 24h」时清零。
    """
    from collections import Counter

    alerts: list[str] = []
    notes: list[str] = []
    try:
        r = run_cmd(
            ["docker", "logs", "--timestamps", "quant-strategy", "--since", "24h"],
            timeout=60,
        )
        text = (r.stdout or "") + "\n" + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "alerts": [f"QTS API 鉴权检查异常(docker logs 失败): {e}"]}

    src_counter: Counter = Counter()
    path_counter: Counter = Counter()
    code_counter: Counter = Counter()
    # 新增 401/403 的**来源与接口**单独计数——告警诊断要看的是"这一轮新增的是谁"，
    # 若混用 24h 全窗口计数，会把历史存量的 Top 接口当成当前故障源，指向错误排查方向
    # （run#54 教训：窗口内的统计口径必须与告警判据的口径一致）。
    new_src_counter: Counter = Counter()
    new_path_counter: Counter = Counter()
    ts_401: list[float] = []
    for line in text.splitlines():
        if 'HTTP/1.1"' not in line:
            continue
        # 形如: INFO: 172.18.0.1:46216 - "POST /api/v1/backtest/run HTTP/1.1" 401 Unauthorized
        try:
            seg = line.split('"')
            req = seg[1] if len(seg) > 1 else ""
            tail = seg[2].strip() if len(seg) > 2 else ""
            code = tail.split()[0] if tail else ""
            src = line.split("INFO:")[1].strip().split(":")[0] if "INFO:" in line else "?"
            mp = req.split()
            path = mp[1] if len(mp) > 1 else req
        except Exception:  # noqa: BLE001
            continue
        if not code.isdigit():
            continue
        code_counter[code] += 1
        if code in ("401", "403"):
            src_counter[src] += 1
            path_counter[path] += 1
            ts_401.append(_parse_docker_ts(line))

    n401 = code_counter.get("401", 0) + code_counter.get("403", 0)
    n5xx = sum(v for k, v in code_counter.items() if k.startswith("5"))

    st = _load_qts_auth_state()
    anchor = st["last_seen_ts"]
    max_ts = max((t for t in ts_401 if t > 0), default=0.0)
    now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()

    if anchor <= 0.0:
        # 首次运行：没有基线。把当前 24h 窗口内的存量一次性记为基线、不告警 ——
        # 否则机制切换当天会把几百条历史存量当成"新增"推一张无信息量的卡片。
        if max_ts > 0:
            _save_qts_auth_state({"last_seen_ts": max_ts, "pending": 0, "pending_since": 0.0})
        if n401:
            notes.append(
                f"QTS API 鉴权检查首次运行，已建立增量基线锚点(截至 {_utc_iso(max_ts)})；"
                f"当前 24h 窗口 {n401} 条 401/403 记为存量，自下一轮起只报新增"
            )
    else:
        # 新增判定：时间戳严格晚于锚点。解析失败(0.0)保守计入新增——无法证明它是
        # 存量时宁可多报也不漏报（漏报正是本机制要修掉的失效模式）。
        n_new = sum(1 for t in ts_401 if t == 0.0 or t > anchor)

        # ── 跨轮累计（run#63）──
        # 每轮新增单独看可能都低于阈值，但持续滴漏的总量才是故障量级。
        # 故 pending 跨轮累加，只在「已告警」或「静默超过 STALE」时清零。
        ref_ts = max_ts if max_ts > 0 else anchor
        if (now_ts - ref_ts) > QTS_AUTH_PENDING_STALE_SECONDS:
            pending = 0
            pending_since = 0.0
        else:
            pending = st["pending"]
            pending_since = st["pending_since"]
        if pending == 0 and n_new:
            # 新一轮累计的起点 = 上一轮锚点（不是本轮 max_ts，否则扫描窗口会漏掉早先的条目）
            pending_since = anchor
        pending += n_new

        # 阈值沿用 10：健康基线应为 0，偶尔 1~2 次可能是人工调试；
        # 观测到的真实系统性漏配量级是每轮 14 条（一次性批量），10 足以稳定命中。
        # 区别只在于：现在是**累计到 10** 才告警，不再是「单轮就得满 10」。
        if pending >= 10:
            scan_from = pending_since if pending_since > 0 else anchor
            for line, t in zip(
                [ln for ln in text.splitlines() if " 401 " in ln or " 403 " in ln], ts_401
            ):
                if t == 0.0 or t > scan_from:
                    try:
                        seg = line.split('"')
                        mp = seg[1].split()
                        new_path_counter[mp[1] if len(mp) > 1 else seg[1]] += 1
                        new_src_counter[line.split("INFO:")[1].strip().split(":")[0]] += 1
                    except Exception:  # noqa: BLE001
                        continue
            top_src = ", ".join(f"{k}({v})" for k, v in new_src_counter.most_common(3)) or "?"
            top_path = ", ".join(f"{k}({v})" for k, v in new_path_counter.most_common(3)) or "?"
            alerts.append(
                f"QTS API 鉴权失败：累计新增 {pending} 次（自 {_utc_iso(scan_from)} 起，"
                f"跨轮累计；本轮新增 {n_new} 次，24h 窗口共 {n401} 次）"
                f" | 来源: {top_src}"
                f" | Top接口: {top_path}"
                f" | health 全绿但业务接口被拒，回测/信号/账户数据不会落库"
            )
            # 已告警 → 清零，避免同一批事件在下一轮被重复计入
            pending = 0
            pending_since = 0.0
        elif pending:
            notes.append(
                f"QTS API 401/403 累计 {pending}/10 条（本轮新增 {n_new} 条，"
                f"自 {_utc_iso(pending_since)} 起跨轮累计，未达阈值不推送）"
            )
        elif n401:
            notes.append(
                f"QTS API 401/403 存量 {n401} 条，自基线锚点 {_utc_iso(anchor)} 以来无新增（已止血）"
            )
        _save_qts_auth_state(
            {
                "last_seen_ts": max_ts if max_ts > 0 else anchor,
                "pending": pending,
                "pending_since": pending_since,
            }
        )

    # 5xx 保持 24h 滚动口径：服务端错误无"历史存量污染"问题（历史上恒为 0），
    # 且 5xx 往往是突发脉冲，滚动窗口比增量锚点更能反映持续劣化。
    if n5xx >= 10:
        alerts.append(f"QTS API 服务端 5xx {n5xx} 次/24h（{dict(code_counter)}）")
    return {"ok": not alerts, "alerts": alerts, "note": "; ".join(notes) or None}


# 定时任务「跑成功但没落库」的特征串。
# 2026-09-01 run#59 盲区：market_snapshot 的 DB 写入连续 17 天 100% 失败
# （08-15 ~ 08-31，771 条日志），但 APScheduler 仍记 "executed successfully"、
# /health 全绿、本中枢原有 16 项检查无一命中 —— 只有日志里的 warning 能证明它坏了。
# 凡「作业自认成功、实际产出为零」的失效，都必须靠日志特征串兜住。
_JOB_WRITE_FAIL_MARKERS = (
    "DB写入失败",  # market_snapshot / 其它作业统一的非致命 DB 异常
    "should be explicitly declared as text",  # SQLAlchemy 2.x 裸 SQL 未包 text()
    "DB写入超时",
)


def check_qts_job_write_health() -> dict:
    """QTS 定时任务「写入健康」检查（2026-09-01 run#59 新增，补盲点#5）。

    背景：APScheduler 只保证「函数没抛异常」，不保证「数据真的落库」。
    market_snapshot 曾连续 17 天把裸 SQL 直接传给 SQLAlchemy 2.x 而被拒，
    但它把 DB 异常吞进 ``except`` 打一条 warning 就返回 —— 作业层面
    "executed successfully"、/health 200、16 项检查全绿，只有日志能证明它坏了。

    因此本检查直接从容器日志捞「写入失败」特征串，**不看作业退出状态**。

    ── 窗口与阈值取舍 ──
    6h 滚动窗口 + 阈值 3：单次 transient 失败（如 DB 重启）只产生 1~2 条，不误报；
    而持续 17 天那种每 30 分钟 4 条的量级必然命中。

    注意：本检查的信号是「失败是否存在」而非「失败有多少条」，
    故**不像 check_qts_api_auth 那样需要增量锚点** —— 计数变化不代表故障变化，
    count 上升不意味着恶化、count 下降也不意味着好转（可能只是作业被跳过）。
    若将来要按增量口径改造，务必重读 run#54 的「去重键 × 滚动窗口 = 失明」教训。
    """
    from collections import Counter

    alerts: list[str] = []
    notes: list[str] = []
    try:
        r = run_cmd(
            ["docker", "logs", "--timestamps", "quant-strategy", "--since", "6h"],
            timeout=60,
        )
        text = (r.stdout or "") + "\n" + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "alerts": [f"QTS 写入健康检查异常(docker logs 失败): {e}"]}

    if not text.strip():
        return {"ok": True, "alerts": [], "note": "quant-strategy 近 6h 无日志输出"}

    write_fail: Counter = Counter()
    samples: list[str] = []
    for line in text.splitlines():
        for mk in _JOB_WRITE_FAIL_MARKERS:
            if mk in line:
                write_fail[mk] += 1
                if len(samples) < 2:
                    # 只留时间戳+事件片段，避免整行塞满告警卡片
                    ev = line.split('"event": "')[-1][:70] if '"event": "' in line else mk
                    samples.append(line[:19] + "Z " + ev)
                break

    n_write_fail = sum(write_fail.values())
    if n_write_fail >= 3:
        top = "、".join(f"{k}×{v}" for k, v in write_fail.most_common(3))
        msg = f"QTS 定时任务落库失败 {n_write_fail} 次/6h（{top}）——作业仍报成功，属静默失效"
        if samples:
            msg += "；样例: " + " | ".join(samples)
        alerts.append(msg)
    elif n_write_fail:
        notes.append(f"QTS 落库失败 {n_write_fail} 次/6h（<3，按 transient 处理，暂不告警）")

    return {"ok": not alerts, "alerts": alerts, "note": "; ".join(notes) or None}


# ════════════════════════════════════════════════════════════════════
# 第 18 项：自动化队列积压 / 批量中断（2026-09-02 run#66 新增，补盲点#5）
# ════════════════════════════════════════════════════════════════════
# ── 为什么需要这一项（实证，勿删）──
# 2026-09-02 09:43:42.073 一刻，3 条自动化被同时中断（resultState=partial_delivered，
# 产物未落盘）：🐟鱼盆主生成(跑了53min)、🐟鱼盆数据提取(47min)、📊盘中助理实盘监控(32min)；
# 同时另有 3 条自 09:27/09:34/09:38 起 status=QUEUED 从未开跑，其中「📋【做T】早盘自检(9:25)」
# 已彻底错过它的 9:25 业务窗口。当天是交易日，开盘后一个多小时**操盘类自动化零产出**。
#
# 而中枢当时 17 项检查全部 ✅ —— 唯一间接察觉到的是 check_automation_health() 的
# 「最近运行被中断」，但那是**滞后且极易被掩盖**的判据：只要该自动化之后有任何一次
# 成功运行，"最近一次"就被覆盖，异常凭空消失。这正是 09-01 发生过 4 次批量中断
# （09:36 半径3 / 18:35 半径2 / 22:39 / 22:40），而 run#57~#65 九轮巡检**全部报 ✅** 的原因。
#
# 本检查改为直接读平台 `automation_runs.status`（QUEUED / IN_PROGRESS / ACCEPTED），
# 是**瞬时状态量**而非"最近一次运行"，不会被后续成功覆盖。
#
# ── 阈值定级依据 ──
# ① QUEUED：实测正常态下 runs_json[0].startedAt == created_at（入库即开跑，零排队）。
#    量化依据（09-02 取证，近 7 天 499 条 run 全样本）：排队等待 startedAt-created_at
#    的 p50 / p90 / **max 全为 0.00min**，等待 >5min 的 run = **0/499**。
#    因此 status 长期停在 QUEUED 本身就是异常，不需要很宽松的阈值。
#    ⚠️ 不要把积压归因为"并发槽位耗尽"：同期真实并发 p50=1 / p90=3 / **max=10**，
#    并发无硬上限，6 条在跑时照样能派发第 7 条。QUEUED 堆积 = **派发环节卡住**，
#    不是排队等资源。（曾误据 queuedPosition 连号 1→7 推断"全局串行、并发度=1"，
#    被 max=10 直接推翻 —— 连号只是队列编号，p50=1 只是平时任务稀疏。）
# ② 爆炸半径 ≥3 才告警：半径 1 绝大多数是**中枢自身长跑被下一档调度取代**（09-01
#    09:57 / 13:50 / 22:40、09-02 08:17 均为此），属常态，纳入会变成每轮噪音。
#    半径 2 不单独告警，但跨轮累计（见下），避免"细水长流"永远测不出（run#63 教训）。
# ③ IN_PROGRESS 超时**排除中枢自己** —— 中枢单轮实测 p90=26min、max=74min，
#    自己必然是最长的那条；不排除就是纯自伤告警（观测者效应）。
QUEUE_ANCHOR = SCRIPT_DIR / ".automation_queue_backlog_anchor.json"
SELF_AUTOMATION_ID = "automation-1785982929477"  # 中枢自身，IN_PROGRESS 超时判定时排除
QUEUE_DEPTH_ALERT = 3  # 同时 QUEUED 条数
QUEUE_WAIT_ALERT_MIN = 20  # 单条排队时长（分钟）
INPROGRESS_STUCK_MIN = 120  # 非中枢的 IN_PROGRESS 滞留上限（分钟）
INTERRUPT_RADIUS_ALERT = 3  # 同刻批量中断的爆炸半径
INTERRUPT_PENDING_ALERT = 4  # 半径2 事件跨轮累计阈值
INTERRUPT_PENDING_STALE_SECONDS = 24 * 3600


def _load_queue_state() -> dict:
    """读取批量中断的增量锚点：{last_seen_ts, pending, pending_since}。

    ── 锚点两变量必须分开（run#63 教训，勿合并）──
    `last_seen_ts` 负责**状态推进**（哪些中断事件已经看过），`pending` 负责
    **计数累计**（够不够阈值）。run#63 的 401 检查曾把两者合成一个：每轮无条件
    推进锚点 → 未达阈值的新增被逐轮吸收 → 低频持续故障永久失明。这里从设计上就
    拆开：锚点每轮推进，pending 只在「已告警」或「静默超 STALE」时清零。
    """
    try:
        raw = json.loads(QUEUE_ANCHOR.read_text(encoding="utf-8"))
        return {
            "last_seen_ts": float(raw.get("last_seen_ts") or 0.0),
            "pending": int(raw.get("pending") or 0),
            "pending_since": float(raw.get("pending_since") or 0.0),
        }
    except Exception:  # noqa: BLE001
        return {"last_seen_ts": 0.0, "pending": 0, "pending_since": 0.0}


def _save_queue_state(state: dict) -> None:
    try:
        QUEUE_ANCHOR.write_text(
            json.dumps(
                {
                    "last_seen_ts": float(state.get("last_seen_ts") or 0.0),
                    "pending": int(state.get("pending") or 0),
                    "pending_since": float(state.get("pending_since") or 0.0),
                    "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        pass


def _query_automation_runs(since_ms: int) -> list[dict] | None:
    """只读取 automation_runs（含自动化名）。查不到/异常返回 None（= 无法判定，不误报）。"""
    db = Path.home() / ".workbuddy" / "workbuddy.db"
    if not db.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT r.automation_id, r.status, r.result_success, r.created_at, "
                "r.updated_at, r.metadata_json, a.name "
                "FROM automation_runs r LEFT JOIN automations a ON a.id = r.automation_id "
                "WHERE r.created_at >= ? ORDER BY r.created_at",
                (since_ms,),
            ).fetchall()
            return [dict(x) for x in rows]
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return None


def check_automation_queue_backlog() -> dict:
    """自动化队列积压 / 批量中断检查。返回 {ok, alerts, note}。"""
    now_ms = int(datetime.datetime.now().timestamp() * 1000)
    rows = _query_automation_runs(now_ms - 36 * 3600 * 1000)
    if rows is None:
        return {"ok": True, "alerts": [], "note": "队列积压: workbuddy.db 不可读，跳过（不误报）"}

    alerts: list[str] = []
    notes: list[str] = []

    def label(r: dict) -> str:
        return (r.get("name") or r.get("automation_id") or "?")[:22]

    # ── ① 排队积压：status 停在 QUEUED ──
    queued = [r for r in rows if (r.get("status") or "") == "QUEUED"]
    if queued:
        waits = sorted(((now_ms - r["created_at"]) / 60000, r) for r in queued)
        worst_min, worst_row = waits[-1]
        detail = "、".join(f"{label(r)}({m:.0f}min)" for m, r in reversed(waits[:4]))
        if len(queued) >= QUEUE_DEPTH_ALERT or worst_min >= QUEUE_WAIT_ALERT_MIN:
            alerts.append(
                f"自动化队列积压 {len(queued)} 条未开跑（最久 {label(worst_row)} 已等 "
                f"{worst_min:.0f}min）：{detail}——到点未执行，时敏任务窗口已错过"
            )
        else:
            notes.append(f"队列: {len(queued)} 条 QUEUED（最久 {worst_min:.0f}min，未达阈值）")
    else:
        notes.append("队列: 无 QUEUED 积压")

    # ── ② IN_PROGRESS 滞留（排除中枢自己，否则纯自伤）──
    stuck = [
        r
        for r in rows
        if (r.get("status") or "") == "IN_PROGRESS"
        and r["automation_id"] != SELF_AUTOMATION_ID
        and (now_ms - r["created_at"]) / 60000 >= INPROGRESS_STUCK_MIN
    ]
    if stuck:
        detail = "、".join(f"{label(r)}({(now_ms - r['created_at']) / 60000:.0f}min)" for r in stuck)
        alerts.append(f"自动化 IN_PROGRESS 滞留 {len(stuck)} 条(≥{INPROGRESS_STUCK_MIN}min)：{detail}")

    # ── ③ 批量中断（同刻聚类求爆炸半径），只统计锚点之后的新增 ──
    state = _load_queue_state()
    anchor = state["last_seen_ts"]
    clusters: dict[int, list[dict]] = {}
    # 聚类 key 用整秒（同刻批量中断的毫秒会有几十 ms 抖动），但锚点必须用**未截断**的
    # 真实时间戳推进：否则 int(1788313422.073)=1788313422.0 存回锚点后，下一轮
    # 1788313422.073 > 1788313422.0 仍成立 → 同一事件每轮重复告警（永久红）。
    raw_max_ts = anchor
    for r in rows:
        try:
            md = json.loads(r.get("metadata_json") or "{}")
        except Exception:  # noqa: BLE001
            md = {}
        if not md.get("interrupted"):
            continue
        ts = float(r["updated_at"]) / 1000.0
        if ts <= anchor:
            continue  # 存量，已看过
        raw_max_ts = max(raw_max_ts, ts)
        clusters.setdefault(int(ts), []).append(r)

    max_ts = raw_max_ts
    big, small = [], 0
    for ts, items in sorted(clusters.items()):
        if len(items) >= INTERRUPT_RADIUS_ALERT:
            big.append((ts, items))
        elif len(items) >= 2:
            small += 1

    if big:
        parts = []
        for ts, items in big:
            when = datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M:%S")
            parts.append(f"{when} 半径{len(items)}（{'、'.join(label(r) for r in items)}）")
        alerts.append(
            "自动化批量中断 " + "；".join(parts) + "——同刻多条被中断，产物未落盘（爆炸半径事件）"
        )
        state["pending"] = 0
        state["pending_since"] = 0.0
    elif small:
        if not state["pending_since"]:
            state["pending_since"] = datetime.datetime.now().timestamp()
        state["pending"] += small
        if state["pending"] >= INTERRUPT_PENDING_ALERT:
            alerts.append(
                f"自动化批量中断累计 {state['pending']} 次(半径2，跨轮累计≥"
                f"{INTERRUPT_PENDING_ALERT})——低频持续中断，非突发"
            )
            state["pending"] = 0
            state["pending_since"] = 0.0
        else:
            notes.append(f"批量中断累计 {state['pending']}/{INTERRUPT_PENDING_ALERT}（半径2）")
    else:
        notes.append("批量中断: 自锚点以来无新增")
        # 静默超 STALE 才清零 pending（避免一次人工调试的残留在数周后凑够阈值误报）
        if (
            state["pending"]
            and state["pending_since"]
            and datetime.datetime.now().timestamp() - state["pending_since"]
            > INTERRUPT_PENDING_STALE_SECONDS
        ):
            state["pending"] = 0
            state["pending_since"] = 0.0

    state["last_seen_ts"] = max_ts
    _save_queue_state(state)
    return {"ok": not alerts, "alerts": alerts, "note": "; ".join(notes) or None}


# ════════════════════════════════════════════════════════════════════
# 中枢主流程
# ════════════════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只巡检不自愈不推送")
    ap.add_argument("--no-push", action="store_true", help="巡检+自愈但不推飞书")
    ap.add_argument("--weekly", action="store_true", help="生成自愈统计周报 markdown（不巡检）")
    args = ap.parse_args()

    if args.weekly:
        return _generate_weekly_report()

    # 写中枢自身存活锁（对齐 check_schedule_liveness 的设计意图：中枢每小时跑即证明调度器在分发）。
    # 消除日切窗口(凌晨)误报——此前中枢不写锁，跨日后"今日锁数=0"被误判为调度器挂死。
    # 幂等：每日只覆盖同一锁文件；若调度器真死，中枢不跑→不写锁→次日检查正确告警。
    run_cmd(
        [
            sys.executable,
            str(SCRIPT_DIR / "schedule_utils.py"),
            "done",
            "--name",
            "unified_ops_center",
        ],
        capture=False,
    )

    print(f"[ops-center] {datetime.datetime.now():%F %T} 开始统一巡检")

    # 1) 调度所有专项检查
    checks = {
        "自动化健康": check_automation_health(),
        "自动化失败": check_automation_failures(),
        "Docker自愈": check_docker_self_heal(),
        "QTS/pmf CI": check_qts_pmf_ci(),
        "磁盘空间": check_disk(),
        "飞书通道": check_feishu_channel(),
        "调度活性": check_schedule_liveness(),
        "工程质量": check_code_quality(),
        "选股去重": check_duplicate_picks(),
        "数据新鲜度": check_data_freshness(),
        "微信公众号通道": check_wechat_channel(),
        "成本监控": check_cost_anomaly(),
        "Dependabot": check_dependabot_backlog(),
        "memwatch完整性": check_memwatch_integrity(),
        "共享文件完整性": check_shared_files_integrity(),
        "QTS API鉴权": check_qts_api_auth(),
        "QTS写入健康": check_qts_job_write_health(),
        "自动化队列积压": check_automation_queue_backlog(),
    }

    all_alerts: list[str] = []
    for name, res in checks.items():
        status = "✅" if res["ok"] else "⚠️"
        print(f"  {status} {name}: {len(res['alerts'])} 项异常")
        # note 出口（2026-09-02 run#63）：此前 note 只进字典、从不输出，
        # 于是「低于阈值不推送」「双导入门禁 PASS」「成本监控」这类**只有 note 没有 alert**
        # 的检查，17 项里长期有 6~8 项信息量全部沉底。巡检日志是唯一能看见它们的地方，
        # 不打印等于没算。这里只打印、不进 all_alerts（不因此触发推送），
        # 避免把"记录一下"变成"每小时推一张卡片"。
        note = res.get("note")
        if note:
            print(f"       note: {note}")
        if not res["ok"]:
            for a in res["alerts"]:
                all_alerts.append(f"[{name}] {a}")
                log_action("detect", name, a, "alert")

    # 2) 自愈（Runbook 白名单）
    healed = []
    # Runbook#3: 容器崩溃自愈（self_heal.py 在 check 阶段已重启，此处把 restarted 作为已自愈动作上报）
    for h in checks.get("Docker自愈", {}).get("healed", []):
        healed.append(h)
        log_action(
            "self_heal", h["target"], "容器崩溃自动重启", h["result"], f"action={h['action']}"
        )
    # Runbook#1: memwatch 阈值偏低且近期重启→提阈值+reload
    rb = runbook_memwatch_bump(dry_run=args.dry_run)
    if rb and rb["result"] != "skipped(dry-run)":
        healed.append(rb)
    # Runbook#2: dependabot PR 基于旧 main 致 CI 红 → merge main 重跑（安全可逆）
    rb2 = runbook_dependabot_rebase(dry_run=args.dry_run)
    for r in rb2:
        if r["result"] != "skipped(dry-run)":
            healed.append(r)
    # Runbook#4: 已验证未合并的 PR → 审计+Git比对+合并（用户 08-06 授权发布类）
    rb4 = runbook_publish_audit_merge(dry_run=args.dry_run)
    for r in rb4:
        if r["result"] != "skipped(dry-run)":
            healed.append(r)

    # 2.5) 中枢自我审计（2026-08-12）：动作效果验证 + 副作用熔断计数
    #      审计者自身也要被审计——08-11 停机5h11m 教训固化。
    self_audit_alerts = audit_self_actions() if not args.dry_run else []
    for sa in self_audit_alerts:
        log_action("self_audit", "unified_ops_center", sa, "alert")
        all_alerts.append(f"[自我审计] {sa}")

    # 3) 汇总决策（告警去重：同一问题 24h 内只推一次飞书，但审计日志照记）
    dedup_alerts: list[str] = []
    for a in all_alerts:
        # a 形如 "[check_name] reason"，提取 check_name 与 reason 做去重键
        if a.startswith("[") and "]" in a:
            cname, _, rsn = a[1:].partition("] ")
        else:
            cname, rsn = "unknown", a
        # 截断权收归 _dedup_key（原先在此处 rsn[:60] 先截再拼键，与归一化逻辑割裂：
        # 若首个分隔符出现在 60 字符之后，截断点会抢先生效，归一化形同虚设）
        if is_alert_duplicated(cname, rsn):
            print(f"  [dedup] 跳过重复推送: {cname} / {rsn[:40]}")
            continue
        dedup_alerts.append(a)

    # 3.5) 知识闭环：当前告警对照 known_failure_modes，标注 remediation + tier
    known_hits = check_known_failure_modes(dedup_alerts)
    known_by_alert = {h["alert"]: h for h in known_hits}

    if not dedup_alerts and not healed:
        print("[ops-center] 全绿或仅重复告警 → SILENT（无推送）")
        # 仍写审计日志（空跑记录）
        for rec in _run_log:
            append_heal_log(rec)
        _sync_state_anchor(len(checks), 0, len(healed), False, dry_run=args.dry_run, known_hits=[])
        print(
            f'SUMMARY: {{"checks":{len(checks)},"alerts":{len(all_alerts)},"healed":{len(healed)},"pushed":false}}'
        )
        return 0

    # 4) 飞书告知（原因/识别/解决/修复/优化/结论）
    lines = ["🔧 **统一巡检中枢 · 运行报告**", ""]
    if dedup_alerts:
        lines.append(f"### 🔍 发现问题（{len(dedup_alerts)} 项，已去重）")
        for a in dedup_alerts[:15]:
            lines.append(f"• {a}")
            hit = known_by_alert.get(a)
            if hit:
                tier = (
                    (hit.get("tier") or "")
                    .replace("auto-heal", "自愈")
                    .replace("alert-only", "仅告警")
                )
                lines.append(
                    f"  ↳ 已知模式 {hit.get('failure_id')}｜建议：{hit.get('remediation')}｜级别：{tier}"
                )
        lines.append("")
    if healed:
        lines.append(f"### ✅ 已自动修复（{len(healed)} 项）")
        for h in healed:
            lines.append(f"• **{h['action']}** → {h['target']}：{h['reason']}")
            lines.append(f"  结果：{h['result']} | {h['detail']}")
        lines.append("")
    # 容器存活摘要（含 QTS/pmf/StockInsight 等被巡检容器健康度）
    cont = checks.get("Docker自愈", {}).get("containers") or {}
    if cont:
        lines.append(
            f"### 🐳 容器存活（{cont.get('checked', 0)} 巡检 / {cont.get('healthy', 0)} 健康 / "
            f"{cont.get('skipped_stateful', 0)} 有状态跳过 / {cont.get('alerts', 0)} 异常）"
        )
        if cont.get("alerts", 0):
            lines.append("• ⚠️ 存在异常容器（见上方告警），已按 Runbook#3 处理或升级")
        else:
            lines.append("• 全部被巡检容器健康运行（QTS/pmf/StockInsight/wechat 等）")
        lines.append("")
    lines.append("### 📌 结论与优化")
    lines.append("• 巡检已统一接管：原分散的多个健康巡检整合为单中枢，避免重复推送与漏检。")
    lines.append("• 自愈遵循 Runbook 白名单 + 执行后验证 + 审计留痕，非破坏性、可逆、幂等。")
    lines.append("• 全绿时静默，异常时仅此一张卡片告知，不打扰日常使用。")
    lines.append(f"• 时间：{datetime.datetime.now():%F %T}")

    # 5) 写审计日志
    for rec in _run_log:
        append_heal_log(rec)

    pushed = False
    if not args.no_push and not args.dry_run:
        pushed = push_card(
            "统一巡检中枢运行报告", "\n".join(lines), level="alert" if dedup_alerts else "info"
        )
    elif args.dry_run:
        print("[ops-center] (dry-run) 本应推送运行报告")

    print(
        "SUMMARY: "
        + json.dumps(
            {
                "checks": len(checks),
                "alerts": len(dedup_alerts),
                "healed": len(healed),
                "pushed": pushed,
            },
            ensure_ascii=False,
        )
    )
    # 闭环：写回全局状态锚（last_run 心跳 + 自愈统计 + 已知失败模式命中 + runbook 白名单对齐）
    _sync_state_anchor(
        len(checks),
        len(dedup_alerts),
        len(healed),
        pushed,
        dry_run=args.dry_run,
        known_hits=known_hits,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
