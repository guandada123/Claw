#!/usr/bin/env python3
"""credit_vs_token.py — 把「官方积分」与「Token 用量」对齐（2026-10-10 建）

回答一个问题：**每百万 token 实际扣了多少积分**，以及积分花在哪些会话/哪一天。

## 为什么要单开一个脚本，而不是把两个看板合并
两者测的是同一笔消耗的**两个投影**，真值地位不同，合并成一个看板会立刻产生
「谁的口径对」的争论：

| | Token 侧 | 积分侧 |
| --- | --- | --- |
| 真值来源 | `~/.workbuddy/projects/**/*.jsonl`（平台日志） | `workbuddy.db → session_usage.credit_json`（官方计费） |
| 粒度 | **请求级**（含模型/时间/缓存） | **会话级**（值是 `{UUID: 积分数}`，不直接带模型名） |
| 覆盖 | 全量（本机 170+ 天） | 只有实际扣积分的会话（实测约 27%） |
| 单位 | token | 积分 |

→ 所以正确形态是**对齐（join）**而不是**合并（merge）**：
Token 当分母、积分当分子，产出一个**实测倍率**，再按会话/按日下钻。
两边各自仍是各自口径的唯一真值，谁也不覆盖谁。

## 单源约定（重要）
本脚本**不自己解析会话日志**——那会造出第二个 token 解析器（dual parser）。
它消费 `gen_dashboard.py` 已生成的看板 HTML 里内嵌的 `D` 数据块（token 侧唯一解析者）。
看板过期时先重跑看板，再跑本脚本。

## 用法
    /usr/bin/python3 .workbuddy/scripts/credit_vs_token.py                 # 打印 + 写 md 报告
    /usr/bin/python3 .workbuddy/scripts/credit_vs_token.py --json          # 只出 JSON
    /usr/bin/python3 .workbuddy/scripts/credit_vs_token.py --days 30       # 只看近 30 天

退出码：0 正常；2 数据源缺失/不可解析（**不静默放过**）。
"""

from __future__ import annotations

import argparse
import datetime
import json
import sqlite3
import sys
from pathlib import Path

CLAW = Path(__file__).absolute().parent.parent.parent
DEFAULT_DASHBOARD = CLAW / ".workbuddy" / "reports" / "token-dashboard.html"
DEFAULT_DB = Path.home() / ".workbuddy" / "workbuddy.db"
DEFAULT_OUT = CLAW / ".workbuddy" / "reports" / "credit-vs-token.md"
# 会话 id 在两侧的表示不同：token 侧是 uuid 前 8 位，积分侧是完整 uuid
SID_PREFIX = 8


def load_token_side(dashboard: Path) -> tuple[dict[str, int], dict[str, dict], int]:
    """从看板 HTML 里取出内嵌 D 数据块 → {会话前缀: token 数} + 生成时刻。"""
    if not dashboard.is_file():
        print(f"❌ 看板不存在：{dashboard}（先跑 gen_dashboard.py）", file=sys.stderr)
        sys.exit(2)
    s = dashboard.read_text(encoding="utf-8", errors="ignore")
    i = s.find("const D=")
    if i < 0:
        print("❌ 看板里找不到内嵌数据块 `const D=`（上游格式变了？）", file=sys.stderr)
        sys.exit(2)
    try:
        D, _ = json.JSONDecoder().raw_decode(s[i + len("const D=") :])
    except json.JSONDecodeError as e:
        print(f"❌ 内嵌数据块解析失败：{e}", file=sys.stderr)
        sys.exit(2)

    tok: dict[str, int] = {}
    per_model: dict[str, dict[str, int]] = {}
    for ses in D.get("sess", []):
        sid = str(ses.get("id", ""))[:SID_PREFIX]
        total = 0
        for r in ses.get("r", []):
            total += r[2]
            # r = [ts_min, model_idx, total, in, out, cache_read, cache_write]
            midx = r[1]
            mname = D["models"][midx] if 0 <= midx < len(D["models"]) else "?"
            per_model.setdefault(mname, {"tokens": 0, "sessions": 0})
            per_model[mname]["tokens"] += r[2]
        if sid:
            tok[sid] = tok.get(sid, 0) + total
    return tok, per_model, D.get("genMs", 0)


def load_credit_side(db: Path) -> dict[str, dict]:
    """读 session_usage.credit_json → {会话前缀: {credits, ts, keys}}。"""
    if not db.is_file():
        print(f"❌ 平台库不存在：{db}", file=sys.stderr)
        sys.exit(2)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out: dict[str, dict] = {}
    try:
        rows = con.execute(
            "SELECT session_id, credit_json, updated_at FROM session_usage "
            "WHERE credit_json IS NOT NULL AND credit_json != ''"
        ).fetchall()
    except sqlite3.Error as e:
        print(f"❌ 读 session_usage 失败：{e}", file=sys.stderr)
        sys.exit(2)
    finally:
        con.close()
    for sid, cj, ts in rows:
        try:
            d = json.loads(cj)
            val = sum(float(v) for v in d.values() if isinstance(v, (int, float)))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        key = str(sid)[:SID_PREFIX]
        rec = out.setdefault(key, {"credits": 0.0, "ts": ts or 0, "calls": 0})
        rec["credits"] += val
        rec["calls"] += len(d) if isinstance(d, dict) else 0
        rec["ts"] = max(rec["ts"], ts or 0)
    return out


def month_key(ms: int) -> str:
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m")


def main() -> int:
    ap = argparse.ArgumentParser(description="官方积分 × Token 用量 对齐")
    ap.add_argument("--dashboard", default=str(DEFAULT_DASHBOARD))
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--days", type=int, default=0, help="只统计近 N 天（按积分侧记录时间）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    ap.add_argument("--top", type=int, default=8, help="按积分 TOP 会话数")
    args = ap.parse_args()

    tok, per_model, genms = load_token_side(Path(args.dashboard))
    cred = load_credit_side(Path(args.db))

    cutoff = 0
    if args.days:
        cutoff = (datetime.datetime.now() - datetime.timedelta(days=args.days)).timestamp() * 1000

    matched, missing = [], []
    for sid, rec in cred.items():
        if cutoff and rec["ts"] < cutoff:
            continue
        t = tok.get(sid)
        (matched if t else missing).append((sid, t or 0, rec))

    tot_tok = sum(m[1] for m in matched)
    tot_cred = sum(m[2]["credits"] for m in matched)
    rate = tot_cred / tot_tok * 1e6 if tot_tok else 0.0

    by_month: dict[str, dict[str, float]] = {}
    month_top: dict[str, float] = {}
    for _sid, t, rec in matched:
        k = month_key(rec["ts"]) if rec["ts"] else "未知"
        b = by_month.setdefault(k, {"credits": 0.0, "tokens": 0, "sessions": 0})
        b["credits"] += rec["credits"]
        b["tokens"] += t
        b["sessions"] += 1
        month_top[k] = max(month_top.get(k, 0.0), float(t))

    # 单会话主导度：某月倍率若被一个大会话带偏，跨月比较就会得出错误结论
    # （2026-10 实测：整体 0.88 积分/M，但 80% token 来自单个高缓存会话；剔除后 2.19，与历史同量级）
    for k, b in by_month.items():
        b["top1_token_share"] = (
            round(month_top.get(k, 0.0) / b["tokens"], 3) if b["tokens"] else 0.0
        )

    top = sorted(matched, key=lambda x: -x[2]["credits"])[: args.top]

    result = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "dashboard_generated": (
            datetime.datetime.fromtimestamp(genms / 1000).strftime("%Y-%m-%d %H:%M")
            if genms
            else None
        ),
        "window_days": args.days or None,
        "coverage": {
            "token_sessions": len(tok),
            "credit_sessions": len(cred),
            "matched": len(matched),
            "credit_sessions_without_token_detail": len(missing),
        },
        "matched_totals": {"tokens": tot_tok, "credits": round(tot_cred, 2)},
        "credits_per_million_tokens": round(rate, 3),
        "by_month": {
            k: {
                "credits": round(v["credits"], 2),
                "tokens": int(v["tokens"]),
                "sessions": int(v["sessions"]),
                "credits_per_million": round(v["credits"] / v["tokens"] * 1e6, 3)
                if v["tokens"]
                else 0,
                "top1_token_share": v.get("top1_token_share", 0.0),
            }
            for k, v in sorted(by_month.items())
        },
        "top_sessions": [
            {
                "session": sid,
                "tokens": t,
                "credits": round(rec["credits"], 2),
                "calls": rec["calls"],
                "credits_per_million": round(rec["credits"] / t * 1e6, 2) if t else None,
                "time": datetime.datetime.fromtimestamp(rec["ts"] / 1000).strftime("%Y-%m-%d %H:%M")
                if rec["ts"]
                else None,
            }
            for sid, t, rec in top
        ],
        "token_side_by_model": dict(
            sorted(per_model.items(), key=lambda kv: -kv[1]["tokens"])[:12]
        ),
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        c = result["coverage"]
        print("=" * 62)
        print("官方积分 × Token 用量 对齐")
        print("=" * 62)
        print(f"看板生成：{result['dashboard_generated']}｜对齐时刻：{result['generated_at']}")
        if args.days:
            print(f"窗口：近 {args.days} 天")
        print(
            f"覆盖：积分侧 {c['credit_sessions']} 会话，其中 {c['matched']} 个能在 token 侧对上"
            f"（{c['matched'] / max(c['credit_sessions'], 1) * 100:.1f}%）；"
            f"token 侧共 {c['token_sessions']} 会话"
        )
        print(
            f"命中集：{tot_tok:,} token ↔ {tot_cred:,.2f} 积分"
            f"  →  实测 {rate:.2f} 积分 / 百万 token"
        )
        print()
        print("按月：")
        for k, v in result["by_month"].items():
            print(
                f"  {k}  {v['sessions']:>4} 会话  {v['tokens']:>14,} tok  "
                f"{v['credits']:>10,.2f} 积分  → {v['credits_per_million']:>7.2f} 积分/M"
                + (
                    f"  ⚠️单会话占 {v['top1_token_share']:.0%}"
                    if v["top1_token_share"] > 0.5
                    else ""
                )
            )
        print()
        print(f"积分最高的 {len(top)} 个会话：")
        for x in result["top_sessions"]:
            print(
                f"  {x['session']}  {x['tokens']:>13,} tok  {x['credits']:>9,.2f} 积分"
                f"  ({x['calls']:>3} 次调用)  {x['credits_per_million'] or 0:>7.2f} 积分/M  {x['time']}"
            )
        print()
        print("⚠️ 口径提醒：")
        print("  · 积分侧只覆盖「实际扣积分的会话」，故**不能用它对账全量 token 总量**；")
        print("    但命中集内的**倍率**（积分/百万 token）是可信的，可用于横向比模型/比时段。")
        print("  · 积分单位以平台页面显示为准（本脚本只做数值求和，不做单位换算）。")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    window_label = f"近 {args.days} 天" if args.days else "全历史"
    lines = [
        "# 官方积分 × Token 用量 对齐",
        "",
        f"> 生成：{result['generated_at']}｜看板生成：{result['dashboard_generated']}"
        f"｜窗口：{window_label}",
        "",
        f"- 覆盖：积分侧 {c['credit_sessions']} 会话 → 命中 token 侧 **{c['matched']}** 个"
        f"（{c['matched'] / max(c['credit_sessions'], 1) * 100:.1f}%）；token 侧共 {c['token_sessions']} 会话",
        f"- 命中集合计：**{tot_tok:,} token ↔ {tot_cred:,.2f} 积分** → 实测 **{rate:.2f} 积分/百万 token**",
        "",
        "## 按月",
        "",
        "| 月份 | 会话 | token | 积分 | 积分/百万 token |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for k, v in result["by_month"].items():
        lines.append(
            f"| {k} | {v['sessions']} | {v['tokens']:,} | {v['credits']:,.2f} | {v['credits_per_million']:.2f} |"
        )
    lines += [
        "",
        "## 积分 TOP 会话",
        "",
        "| 会话 | token | 积分 | 调用次数 | 积分/M | 时间 |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for x in result["top_sessions"]:
        lines.append(
            f"| `{x['session']}` | {x['tokens']:,} | {x['credits']:,.2f} | {x['calls']} | "
            f"{x['credits_per_million'] or 0:.2f} | {x['time']} |"
        )
    lines += [
        "",
        "## 口径提醒",
        "",
        "- 积分侧只覆盖「实际扣积分的会话」，**不能对账全量 token 总量**；命中集内的**倍率**可信。",
        "- 积分单位以平台页面为准（本脚本只做数值求和，不做单位换算）。",
        "- token 侧由 `token-dashboard` skill 的看板提供（本脚本不重复解析日志）。",
        "",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    if not args.json:
        print(f"\n报告已写：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
