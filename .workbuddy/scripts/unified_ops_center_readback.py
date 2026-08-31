#!/usr/bin/env python3
"""统一巡检中枢 — 回读验证器（只读，绝不写任何 state）

存在的理由：run#1~#26 每次手写 heredoc 回读，重复踩 4 类坑：
  1) wechat /api/admin/status 是「扁平结构」，字段在顶层（无 data.data 双层）→ 写 d['data'] 得到 {} 差点误判"接口挂了"
  2) 状态锚不在 Claw 下，而在 ~/.workbuddy/cross_project_state.json；且 last_run 是 dict（含 .ts），不是 ISO 字符串
  3) .ops_alerted.json 的 value 可能是 epoch(int) 也可能是 ISO 字符串；key 里的 `通道@服务` 是合法命名，
     污染检查必须找「6 位以上连续数字串」，不能拿 `@` 当污染特征（否则 100% 误报）
  4) interval_min 不在 monitoring.global.unified_ops_center 顶层，而在 .self_health.interval_min

用法：python3 unified_ops_center_readback.py
"""
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path("/Users/guan/WorkBuddy/Claw/.workbuddy/scripts")
ALERTED = SCRIPT_DIR / ".ops_alerted.json"
CROSS_STATE = Path.home() / ".workbuddy" / "cross_project_state.json"
DEDUP_TTL_H = 72          # 当前 F3 类告警的去重窗口
WATCHDOG_MAX_MIN = 180    # 调度活性护栏阈值
now = dt.datetime.now()
now_ts = now.timestamp()


def _ts(v):
    """epoch(int/float) 或 ISO 字符串 → datetime；都不是则 None。"""
    if isinstance(v, (int, float)):
        return dt.datetime.fromtimestamp(v)
    s = str(v).replace("Z", "").strip()
    try:
        return dt.datetime.fromisoformat(s)
    except Exception:
        return None


def sec1_alerted():
    print("== 1) 去重键 .ops_alerted.json ==")
    if not ALERTED.exists():
        print("  ❌ 文件不存在", ALERTED)
        return
    d = json.loads(ALERTED.read_text(encoding="utf-8"))
    # 只认「6 位以上连续数字串」为污染；`通道@服务` 是合法命名，不可当污染
    bad = [k for k in d if re.search(r"\d{6,}", str(k))]
    print(f"  keys={len(d)} | 污染(6+位数字串)={bad or '无 ✅'}")
    for k, v in d.items():
        ts = _ts(v)
        if ts is None:
            print(f"  ⚠️ 无法解析时间: {k[:60]} = {v!r}")
            continue
        age = (now_ts - ts.timestamp()) / 3600
        nxt = ts + dt.timedelta(hours=DEDUP_TTL_H)
        print(f"  age={age:6.2f}h  next_push={nxt:%Y-%m-%d %H:%M:%S}  key={k[:66]}")


def sec2_anchor():
    print("== 2) 状态锚 cross_project_state ==")
    if not CROSS_STATE.exists():
        print("  ❌ 文件不存在", CROSS_STATE)
        return
    a = (json.loads(CROSS_STATE.read_text(encoding="utf-8"))
         .get("monitoring", {}).get("global", {}).get("unified_ops_center", {}))
    lr = a.get("last_run")
    if isinstance(lr, dict):          # last_run 是 dict，时间在 .ts
        print("  last_run:", json.dumps(lr, ensure_ascii=False))
        ts = _ts(lr.get("ts"))
    else:
        print("  last_run:", lr)
        ts = _ts(lr)
    if ts:
        print(f"  新鲜度={(now_ts - ts.timestamp()) / 60:.2f}min")
    sh = a.get("self_health", {}) or {}
    im = sh.get("interval_min")
    print(f"  interval_min={im} (watchdog 阈值 {WATCHDOG_MAX_MIN}min)"
          f" → {'✅ 未触发' if (im is None or im < WATCHDOG_MAX_MIN) else '❌ 超阈值'}")
    print(f"  known_failure_hits={a.get('known_failure_hits')}")


def sec3_wechat():
    print("== 3) 微信通道实锤 ==")
    try:
        out = subprocess.run(["curl", "-s", "--max-time", "8",
                              "http://127.0.0.1:5001/api/admin/status"],
                             capture_output=True, text=True, timeout=15).stdout
        d = json.loads(out)
    except Exception as e:
        print("  ❌ 取状态失败:", e)
        return
    dd = d.get("data") or d                       # 扁平结构：字段在顶层
    if isinstance(dd.get("data"), dict):          # 兼容双层
        dd = dd["data"]
    print(f"  loggedIn={dd.get('loggedIn')} isExpired={dd.get('isExpired')} "
          f"account={dd.get('account')}")
    et = dd.get("expireTime")
    v = None
    if isinstance(et, (int, float)):
        v = et / 1000 if et > 1e11 else et        # ms 级
    elif isinstance(et, str) and et.isdigit():
        n = int(et)
        v = n / 1000 if n > 1e11 else n
    else:
        t = _ts(et)
        v = t.timestamp() if t else None
    if v:
        e = dt.datetime.fromtimestamp(v)
        print(f"  expireTime={e:%Y-%m-%d %H:%M}  已过期 {(now_ts - v) / 86400:.2f} 天")
    else:
        print("  expireTime raw:", et)


def sec4_code():
    print("== 4) 代码持久性（历史修复是否还在位）==")
    src = SCRIPT_DIR / "unified_ops_center.py"
    if not src.exists():
        print("  ❌ 主脚本不存在")
        return
    txt = src.read_text(encoding="utf-8")
    marks = {
        "run#9 去重TTL覆写": "ALERT_DEDUP_TTL_OVERRIDE_H",
        "run#9 TTL分派": "_ttl_hours_for",
        "run#18 容器抓取时间": "_container_last_fetch_ts",
    }
    for label, needle in marks.items():
        ok = needle in txt
        print(f"  {'✅' if ok else '❌'} {label} ({needle})")


def sec5_git():
    """git 漂移。

    2026-08-31 补「观察盲区」段：此前只列 .workbuddy/scripts/ 的文件名、
    全仓只报计数，导致仓库根的 .gitignore 修复（run#32 落地但未入库）连续
    3 轮没被发现。教训：计数持平会掩盖组成变化，且自己修的东西常落在
    自己不看的目录。故改为逐项列出全仓文件名 + 高亮根级配置文件。
    """
    print("== 5) git 漂移 ==")
    repo = Path("/Users/guan/WorkBuddy/Claw")
    r = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                       capture_output=True, text=True)
    all_lines = [l for l in r.stdout.splitlines() if l.strip()]
    in_scripts = [l for l in all_lines if ".workbuddy/scripts/" in l]
    outside = [l for l in all_lines if ".workbuddy/scripts/" not in l]

    print(f"  .workbuddy/scripts/ 未提交 = {len(in_scripts)} 项")
    for l in in_scripts[:10]:
        print("   ", l)

    # 盲区：scripts 目录之外——修复动作常落到仓库根配置/CI/tests，此前完全不观察
    print(f"  [盲区] scripts 之外未提交 = {len(outside)} 项")
    ROOT_CFG = (".gitignore", "pyproject.toml", "ruff.toml", "Makefile",
                "requirements", ".github/")
    flagged = [l for l in outside if any(p in l for p in ROOT_CFG)]
    for l in outside[:25]:
        mark = "  ⚠️ 根级配置/基建" if any(p in l for p in ROOT_CFG) else ""
        print("   ", l + mark)
    if len(outside) > 25:
        print(f"    ... 另有 {len(outside) - 25} 项未列出")
    if flagged:
        print(f"  ⚠️ 根级配置类未提交 {len(flagged)} 项 —— 修复动作若改到此处极易漏入库，需人工确认")
    print(f"  全仓合计 = {len(all_lines)} 项")


if __name__ == "__main__":
    for fn in (sec1_alerted, sec2_anchor, sec3_wechat, sec4_code, sec5_git):
        try:
            fn()
        except Exception as e:
            print(f"  ⚠️ {fn.__name__} 异常: {type(e).__name__}: {e}")
        print()
    sys.exit(0)
