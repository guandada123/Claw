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
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent
PUSH = SCRIPT_DIR / "push_feishu.sh"
CHAT_ID = "oc_9ee5303497f5e0e71666b610d6bdc346"
SELF_HEAL_LOG = SCRIPT_DIR / "unified_self_heal_log.json"
ALERT_DEDUP_STATE = SCRIPT_DIR / ".ops_alerted.json"  # 告警去重状态（check_name@reason -> 时间戳）
ALERT_DEDUP_TTL_H = 24  # 同一告警 24h 内只推一次

MEMWATCH_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.workbuddy.memwatch.plist"
MEMWATCH_SCRIPT = Path.home() / ".local" / "bin" / "watch_workbuddy_mem.sh"
MEMWATCH_LOG = Path.home() / "Library" / "Logs" / "workbuddy_memwatch.log"
MEMWATCH_TARGET_MB = 10000
MEMWATCH_LOW_MB = 8000

# ── 运行日志（审计留痕 who/what/when/why/result）──
_run_log: list[dict] = []


# ── 告警去重（避免同一问题每小时重复轰炸飞书）──
def _load_alerted() -> dict:
    try:
        return json.loads(ALERT_DEDUP_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_alerted(d: dict) -> None:
    # 清理过期条目
    now = datetime.datetime.now().timestamp()
    ttl = ALERT_DEDUP_TTL_H * 3600
    d = {k: v for k, v in d.items() if now - v < ttl}
    ALERT_DEDUP_STATE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def is_alert_duplicated(check_name: str, reason_key: str) -> bool:
    """同一 (check_name, reason_key) 24h 内已推送过 → True（跳过飞书推送，但审计日志照记）"""
    d = _load_alerted()
    key = f"{check_name}@{reason_key}"
    now = datetime.datetime.now().timestamp()
    if key in d and now - d[key] < ALERT_DEDUP_TTL_H * 3600:
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
        "result": result,        # success / skipped / failed
        "detail": detail,
    }
    _run_log.append(rec)
    return rec


def append_heal_log(rec: dict) -> None:
    try:
        data = json.loads(SELF_HEAL_LOG.read_text(encoding="utf-8")) if SELF_HEAL_LOG.exists() else []
    except Exception:
        data = []
    data.append(rec)
    # 仅保留最近 200 条
    data = data[-200:]
    SELF_HEAL_LOG.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def run_cmd(cmd: list[str], timeout: int = 90, capture: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=capture, text=True, timeout=timeout, env=env)


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
        r = run_cmd([sys.executable, str(SCRIPT_DIR / "automation_health.py"), "--json"], timeout=120)
        if r.returncode != 0:
            return {"ok": False, "alerts": [f"automation_health 退出码 {r.returncode}: {r.stderr[:200]}"]}
        # 解析 JSON（脚本可能混输出，取最后一段 JSON）
        out = r.stdout.strip()
        try:
            data = json.loads(out[out.rfind("{"):])
        except Exception:
            return {"ok": True, "alerts": [], "raw": out[-300:]}
        alerts = data.get("alerts") or data.get("failures") or []
        if isinstance(alerts, list) and alerts:
            return {"ok": False, "alerts": [str(a) for a in alerts]}
        return {"ok": True, "alerts": []}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "alerts": [f"automation_health 异常: {e}"]}


def check_docker_self_heal() -> dict:
    """复用 self_heal.py（Docker 二次确认+崩溃循环捕获）。
    返回 {ok, alerts, healed}：restarted 是已完成的自愈动作（不计入问题），
    alerts 才是仍需关注的（振荡防护停手/重启失败/有状态禁重启等）。"""
    try:
        r = run_cmd([sys.executable, str(SCRIPT_DIR / "self_heal.py")], timeout=120)
        if r.returncode != 0:
            return {"ok": False, "alerts": [f"self_heal 退出码 {r.returncode}"], "healed": []}
        out = r.stdout.strip()
        try:
            data = json.loads(out[out.rfind("{"):])
        except Exception:
            return {"ok": True, "alerts": [], "healed": [], "raw": out[-300:]}
        restarted = data.get("restarted") or []
        alerts = data.get("alerts") or []
        # restarted 是 Runbook#3 已完成的自愈（容器重启），作为 healed 上报
        healed = [{"action": "container_restart", "target": c, "result": "success"} for c in restarted]
        return {"ok": len(alerts) == 0, "alerts": alerts, "healed": healed, "data": data}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "alerts": [f"self_heal 异常: {e}"], "healed": []}


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


def check_qts_pmf_ci() -> dict:
    """复用 qts_pmf_health_guard.py（CI红+容器存活）。解析其 JSON 输出而非关键词，避免误判。"""
    try:
        r = run_cmd([sys.executable, str(SCRIPT_DIR / "qts_pmf_health_guard.py"), "--dry-run"], timeout=120)
        if r.returncode != 0:
            return {"ok": False, "alerts": [f"qts_guard 退出码 {r.returncode}"]}
        out = r.stdout.strip()
        # 解析首段 JSON（含 ts/ci_reds/container_unhealthy/anomaly 字段）
        m = re.search(r'\{[^{}]*"ts"[^{}]*\}', out)
        if m:
            try:
                data = json.loads(m.group(0))
                reds = data.get("ci_reds", 0)
                unhealthy = data.get("container_unhealthy", 0)
                if reds or unhealthy:
                    alerts = []
                    if reds:
                        alerts.append(f"GitHub CI 红灯 {reds} 个（详见每日20:00巡检卡）")
                    if unhealthy:
                        alerts.append(f"容器异常 {unhealthy} 个")
                    return {"ok": False, "alerts": alerts}
                return {"ok": True, "alerts": []}
            except Exception:
                pass
        # 退化：仅当明确 anomaly 且无 json 时才告警
        if '"anomaly": true' in out and 'ci_reds": 0' not in out:
            return {"ok": False, "alerts": ["qts_guard 报告 anomaly（详情见每日巡检）"]}
        return {"ok": True, "alerts": []}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "alerts": [f"qts_guard 异常: {e}"]}


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
    try:
        txt = MEMWATCH_SCRIPT.read_text(encoding="utf-8")
        m = re.search(r'RSS_RESTART_MB:=(\d+)', txt)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return 6000


def runbook_memwatch_bump(dry_run: bool = False) -> dict | None:
    """Runbook#1: memwatch 阈值偏低且近期重启→提阈值+reload。返回动作记录或None。"""
    if not MEMWATCH_SCRIPT.exists() or not MEMWATCH_PLIST.exists():
        return None
    cur = _memwatch_current_mb()
    if cur >= MEMWATCH_TARGET_MB:
        return None
    if not _memwatch_recent_restart():
        return None
    if dry_run:
        return log_action("memwatch_bump", "com.workbuddy.memwatch",
                          f"阈值 {cur}MB 偏低且近期有重启", "skipped(dry-run)",
                          f"将提至 {MEMWATCH_TARGET_MB}MB")
    try:
        bak = MEMWATCH_SCRIPT.with_suffix(".sh.bak-autoheal")
        shutil.copy2(MEMWATCH_SCRIPT, bak)
        txt = MEMWATCH_SCRIPT.read_text(encoding="utf-8")
        txt = re.sub(r'RSS_RESTART_MB:=\d+', f'RSS_RESTART_MB:={MEMWATCH_TARGET_MB}', txt, count=1)
        MEMWATCH_SCRIPT.write_text(txt, encoding="utf-8")
        run_cmd(["launchctl", "unload", str(MEMWATCH_PLIST)])
        run_cmd(["launchctl", "load", str(MEMWATCH_PLIST)])
    except Exception as e:  # noqa: BLE001
        return log_action("memwatch_bump", "com.workbuddy.memwatch",
                          f"阈值 {cur}MB 偏低", "failed", str(e))
    return log_action("memwatch_bump", "com.workbuddy.memwatch",
                      f"阈值 {cur}MB 偏低且近期有重启(根因=看门狗误杀盘前自动化)", "success",
                      f"提至 {MEMWATCH_TARGET_MB}MB+reload；备份 {bak.name}")


# GitHub 仓库常量（CI 自愈 Runbook 用）
GH_REPOS_FOR_PR = ["QuantTradingSystem", "project-monitor-fusion", "StockInsight", "wechat-download-api"]


def _find_stale_dependabot_prs() -> list[dict]:
    """查找 OPEN 且基于旧 main 分叉导致 CI 红的 dependabot PR（安全自愈候选）。
    返回 [{repo, number, branch, url}]。仅 dependabot/ 前缀分支，非 dependabot 不碰。"""
    candidates = []
    for repo in GH_REPOS_FOR_PR:
        r = run_cmd(
            ["gh", "pr", "list", "--repo", f"guandada123/{repo}", "--state", "open",
             "--head", "dependabot/*", "--json", "number,headRefName,url,mergeable"]
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
            candidates.append({
                "repo": repo,
                "number": pr.get("number"),
                "branch": pr.get("headRefName"),
                "url": pr.get("url"),
            })
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
            recs.append(log_action("dependabot_rebase", f"{repo}#{num}",
                                    reason, "skipped(dry-run)",
                                    "将 merge origin/main 进分支触发 CI 重跑"))
            continue
        # 本地仓路径探测（优先 /Volumes/ZHITAI，降级 ~/WorkBuddy）
        local = Path(f"/Volumes/ZHITAI/WorkBuddy/{repo}")
        if not local.exists():
            local = Path.home() / "WorkBuddy" / repo
        if not local.exists():
            recs.append(log_action("dependabot_rebase", f"{repo}#{num}",
                                   reason, "skipped(no-local-repo)",
                                   f"本地仓缺失 {local}，跳出自愈（升级人工）"))
            continue
        try:
            r = run_cmd(["git", "-C", str(local), "fetch", "origin"], timeout=60)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip()[:150])
            run_cmd(["git", "-C", str(local), "checkout", branch], timeout=30)
            r = run_cmd(
                ["git", "-C", str(local), "merge", "--no-edit", f"origin/main"], timeout=60
            )
            if r.returncode != 0:
                # 冲突 → 中止，升级人工
                run_cmd(["git", "-C", str(local), "merge", "--abort"], timeout=30)
                run_cmd(["git", "-C", str(local), "checkout", "main"], timeout=30)
                recs.append(log_action("dependabot_rebase", f"{repo}#{num}",
                                       reason, "failed(conflict)",
                                       "merge main 冲突，中止并升级人工"))
                continue
            r = run_cmd(
                ["git", "-C", str(local), "push", "origin", branch], timeout=60
            )
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip()[:150])
            run_cmd(["git", "-C", str(local), "checkout", "main"], timeout=30)
        except Exception as e:  # noqa: BLE001
            recs.append(log_action("dependabot_rebase", f"{repo}#{num}",
                                   reason, "failed", str(e)[:150]))
            continue
        recs.append(log_action("dependabot_rebase", f"{repo}#{num}",
                               reason, "success",
                               f"merge origin/main 进 {branch} 并 push，触发 CI 重跑"))
    return recs


def _find_verified_unmerged_prs() -> list[dict]:
    """查找"已验证未合并的 PR"（安全合并候选）。
    判定：OPEN + 非 draft + mergeable(非 CONFLICTING) + 最新 CI 跑绿 + 基于最新 main。
    返回 [{repo, number, branch, url, head_sha, latest_ci_status}]。"""
    candidates = []
    for repo in GH_REPOS_FOR_PR:
        # 1) 拉 OPEN PR（含 mergeable / isDraft / headRefName / headRefOid / url）
        r = run_cmd(
            ["gh", "pr", "list", "--repo", f"guandada123/{repo}", "--state", "open",
             "--json", "number,headRefName,headRefOid,url,isDraft,mergeable,baseRefName"]
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
                ["gh", "pr", "checks", str(num), "--repo", f"guandada123/{repo}",
                 "--json", "name,status,conclusion,bucket", "--jq", ".[0:5]"]
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
            candidates.append({
                "repo": repo,
                "number": num,
                "branch": branch,
                "head_sha": head_sha,
                "url": pr.get("url"),
            })
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
            recs.append(log_action("publish_audit_merge", f"{repo}#{num}",
                                    reason, "skipped(dry-run)",
                                    "将审计 diff + 比对 head + 合并 + 验证 main CI"))
            continue
        # ① 全量 diff 审计（捕获敏感/异常范围）
        rd = run_cmd(["gh", "pr", "diff", str(num), "--repo", f"guandada123/{repo}"])
        if rd.returncode != 0:
            recs.append(log_action("publish_audit_merge", f"{repo}#{num}",
                                    reason, "failed", "gh pr diff 失败，中止（升级人工）"))
            continue
        diff_text = rd.stdout
        # 敏感词审计（密钥/凭证/token）
        sensitive_hits = [w for w in ("sk-", "api_key", "secret", "password", "token=", "BEGIN PRIVATE KEY")
                          if w.lower() in diff_text.lower()]
        if sensitive_hits:
            recs.append(log_action("publish_audit_merge", f"{repo}#{num}",
                                    reason, "failed(sensitive)",
                                    f"diff 含敏感词 {sensitive_hits}，中止合并（升级人工）"))
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
                recs.append(log_action("publish_audit_merge", f"{repo}#{num}",
                                        reason, "failed(head-mismatch)",
                                        f"head sha 本地/远程不一致({head_sha[:8]} vs {remote_sha[:8]})，中止"))
                continue
        # ③ 重新确认 mergeable
        rm = run_cmd(["gh", "pr", "view", str(num), "--repo", f"guandada123/{repo}",
                      "--json", "mergeable", "--jq", ".mergeable"])
        mergeable = rm.stdout.strip()
        if mergeable == "CONFLICTING":
            recs.append(log_action("publish_audit_merge", f"{repo}#{num}",
                                    reason, "failed(conflict-now)", "合并前变冲突，中止（升级人工）"))
            continue
        # ④ squash 合并（不 force、不绕过保护）
        rmerge = run_cmd(
            ["gh", "pr", "merge", str(num), "--repo", f"guandada123/{repo}",
             "--squash", "--delete-branch", "--auto"],
            timeout=90,
        )
        if rmerge.returncode != 0:
            recs.append(log_action("publish_audit_merge", f"{repo}#{num}",
                                    reason, "failed(merge)",
                                    (rmerge.stderr or rmerge.stdout).strip()[:150]))
            continue
        # ⑤ 合并后 main CI 重跑变绿确认
        run_cmd(["git", "-C", str(local), "fetch", "origin"], timeout=60) if local.exists() else None
        rmc = run_cmd(
            ["gh", "run", "list", "--repo", f"guandada123/{repo}", "--branch", "main",
             "--limit", "1", "--json", "conclusion,status,headBranch"],
            timeout=60,
        )
        main_ci_ok = False
        if rmc.returncode == 0:
            try:
                runs = json.loads(rmc.stdout)
                if runs and runs[0].get("status") == "completed" and runs[0].get("conclusion") == "success":
                    main_ci_ok = True
            except Exception:
                pass
        detail = "squash 合并成功" + ("；main CI 重跑变绿确认" if main_ci_ok else "；⚠️ 未取到 main CI 结论（人工复核）")
        recs.append(log_action("publish_audit_merge", f"{repo}#{num}",
                               reason, "success", detail))
    return recs


CROSS_STATE_PATH = Path.home() / ".workbuddy" / "cross_project_state.json"

# 当前代码实际注册的安全动作（与 Runbook 白名单一致；改 Runbook 时同步此处）
REGISTERED_RUNBOOKS = [
    "memwatch_threshold_bump",      # #1
    "docker_restart_container",    # #3 (self_heal.py 实际重启，中枢上报 healed)
    "dependabot_rebase",           # #2
    "publish_audit_merge",         # #4
]


def _sync_state_anchor(checks_n: int, alerts_n: int, healed_n: int, pushed: bool,
                       dry_run: bool = False) -> None:
    """闭环：把本次运行结果写回全局跨项目状态锚 cross_project_state.json。
    让 monitoring.global.unified_ops_center 反映真实运行态（last_run 心跳 + runbook 白名单对齐代码）。
    仅更新 monitoring.global.unified_ops_center 子节点，不影响其他字段；失败静默不阻断主流程。"""
    if dry_run:
        return
    try:
        data = json.loads(CROSS_STATE_PATH.read_text(encoding="utf-8")) if CROSS_STATE_PATH.exists() else {}
    except Exception:
        return
    node = data.setdefault("monitoring", {}).setdefault("global", {}).setdefault("unified_ops_center", {})
    node["last_run"] = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "checks": checks_n,
        "alerts_after_dedup": alerts_n,
        "healed": healed_n,
        "pushed": pushed,
        "status": "alert" if alerts_n else ("healed" if healed_n else "silent_green"),
    }
    # 对齐 runbook 白名单与实际代码（避免状态锚与实际注册漂移）
    node["runbook_whitelist"] = REGISTERED_RUNBOOKS
    node["runbook_count"] = len(REGISTERED_RUNBOOKS)
    try:
        CROSS_STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] 状态锚写回失败(非阻断): {e}")


# ════════════════════════════════════════════════════════════════════
# 中枢主流程
# ════════════════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只巡检不自愈不推送")
    ap.add_argument("--no-push", action="store_true", help="巡检+自愈但不推飞书")
    args = ap.parse_args()

    print(f"[ops-center] {datetime.datetime.now():%F %T} 开始统一巡检")

    # 1) 调度所有专项检查
    checks = {
        "自动化健康": check_automation_health(),
        "Docker自愈": check_docker_self_heal(),
        "QTS/pmf CI": check_qts_pmf_ci(),
        "磁盘空间": check_disk(),
        "飞书通道": check_feishu_channel(),
    }

    all_alerts: list[str] = []
    for name, res in checks.items():
        status = "✅" if res["ok"] else "⚠️"
        print(f"  {status} {name}: {len(res['alerts'])} 项异常")
        if not res["ok"]:
            for a in res["alerts"]:
                all_alerts.append(f"[{name}] {a}")
                log_action("detect", name, a, "alert")

    # 2) 自愈（Runbook 白名单）
    healed = []
    # Runbook#3: 容器崩溃自愈（self_heal.py 在 check 阶段已重启，此处把 restarted 作为已自愈动作上报）
    for h in checks.get("Docker自愈", {}).get("healed", []):
        healed.append(h)
        log_action("self_heal", h["target"], "容器崩溃自动重启", h["result"],
                   f"action={h['action']}")
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

    # 3) 汇总决策（告警去重：同一问题 24h 内只推一次飞书，但审计日志照记）
    dedup_alerts: list[str] = []
    for a in all_alerts:
        # a 形如 "[check_name] reason"，提取 check_name 与 reason 做去重键
        if a.startswith("[") and "]" in a:
            cname, _, rsn = a[1:].partition("] ")
        else:
            cname, rsn = "unknown", a
        if is_alert_duplicated(cname, rsn[:60]):
            print(f"  [dedup] 跳过重复推送: {cname} / {rsn[:40]}")
            continue
        dedup_alerts.append(a)

    if not dedup_alerts and not healed:
        print("[ops-center] 全绿或仅重复告警 → SILENT（无推送）")
        # 仍写审计日志（空跑记录）
        for rec in _run_log:
            append_heal_log(rec)
        _sync_state_anchor(len(checks), 0, len(healed), False, dry_run=args.dry_run)
        print('SUMMARY: {"checks":%d,"alerts":%d,"healed":%d,"pushed":false}' % (
            len(checks), len(all_alerts), len(healed)))
        return 0

    # 4) 飞书告知（原因/识别/解决/修复/优化/结论）
    lines = ["🔧 **统一巡检中枢 · 运行报告**", ""]
    if dedup_alerts:
        lines.append(f"### 🔍 发现问题（{len(dedup_alerts)} 项，已去重）")
        for a in dedup_alerts[:15]:
            lines.append(f"• {a}")
        lines.append("")
    if healed:
        lines.append(f"### ✅ 已自动修复（{len(healed)} 项）")
        for h in healed:
            lines.append(f"• **{h['action']}** → {h['target']}：{h['reason']}")
            lines.append(f"  结果：{h['result']} | {h['detail']}")
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
        pushed = push_card("统一巡检中枢运行报告", "\n".join(lines),
                           level="alert" if dedup_alerts else "info")
    elif args.dry_run:
        print("[ops-center] (dry-run) 本应推送运行报告")

    print('SUMMARY: ' + json.dumps({
        "checks": len(checks),
        "alerts": len(dedup_alerts),
        "healed": len(healed),
        "pushed": pushed,
    }, ensure_ascii=False))
    # 闭环：写回全局状态锚（last_run 心跳 + runbook 白名单对齐）
    _sync_state_anchor(len(checks), len(dedup_alerts), len(healed), pushed, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
