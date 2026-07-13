"""
signal_weights_update.py — 公众号信号权重 + 升降级（v7.1 STEP 1 核心逻辑）

读取 signal_verify_report.json（由 signal_verify.py 生成），为已验证账户计算：
  - weighted_hit_rate : 验证信号命中率（=报告 win_rate）
  - avg_return        : 已验证信号平均累计收益
  - signals_verified  : 已验证信号数
  - weight_multiplier : 权重倍数（基于命中率分级）
  - status            : ⭐推荐 / ✅正常 / ⚠️降级 / ⚪未验证

升降级规则（命中率驱动，可解释、可回测）：
  win_rate >= 60%  -> 3 (⭐推荐)
  30% <= win_rate < 60% -> 2 (✅正常)
  win_rate < 30%   -> 1 (⚠️降级)
  win_rate is None (未验证) -> 不纳入活跃权重集

未验证账户（信号过旧 >1年无行情）不纳入 signal_weights.json，避免污染活跃权重。

升降级变化检测：与上一版 signal_weights.json 逐账户比较 weight_multiplier，
任何账户倍数变动 / 新增 / 移除均记为 change，供 STEP 3 推送判定。
"""
from __future__ import annotations

import datetime
import json
import pathlib

ROOT = pathlib.Path("/Users/guan/WorkBuddy/Claw")
DATA = ROOT / ".workbuddy" / "data"
REPORT = DATA / "signal_verify_report.json"
WEIGHTS = DATA / "signal_weights.json"

RECOMMEND, NORMAL, DOWNGRADE = 3, 2, 1


def multiplier(win_rate):
    if win_rate is None:
        return None
    if win_rate >= 60:
        return RECOMMEND
    if win_rate >= 30:
        return NORMAL
    return DOWNGRADE


def status_label(m):
    return {
        RECOMMEND: "⭐推荐",
        NORMAL: "✅正常",
        DOWNGRADE: "⚠️降级",
    }.get(m, "⚪未验证")


def main():
    if not REPORT.exists():
        raise SystemExit("signal_verify_report.json 不存在，请先运行 signal_verify.py")
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    today = datetime.date.today().strftime("%Y-%m-%d")

    prev = {}
    if WEIGHTS.exists():
        try:
            prev = json.loads(WEIGHTS.read_text(encoding="utf-8"))
        except Exception:
            prev = {}
    prev_acct = prev.get("accounts", {})

    accounts = {}
    for x in report["ranking"]:
        wr = x["win_rate"]
        if wr is None:
            continue  # 未验证账户不纳入活跃权重集
        m = multiplier(wr)
        accounts[x["account"]] = {
            "weighted_hit_rate": wr,
            "avg_return": x["avg_return"],
            "signals_verified": x["verified"],
            "weight_multiplier": m,
            "status": status_label(m),
        }

    # 升降级变化检测
    changes = []
    for a, info in accounts.items():
        pm = prev_acct.get(a, {}).get("weight_multiplier")
        if pm is None:
            changes.append({"account": a, "from": "新增", "to": info["weight_multiplier"],
                            "status": info["status"]})
        elif pm != info["weight_multiplier"]:
            changes.append({"account": a, "from": pm, "to": info["weight_multiplier"],
                            "status": info["status"]})
    for a, info in prev_acct.items():
        if a not in accounts:
            changes.append({"account": a, "from": info.get("weight_multiplier"),
                            "to": "移除", "status": "—"})

    out = {"updated": today, "accounts": accounts}
    WEIGHTS.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "updated": today,
        "accounts_tracked": len(accounts),
        "changes": changes,
        "changed": len(changes) > 0,
    }, ensure_ascii=False))
    return out


if __name__ == "__main__":
    main()
