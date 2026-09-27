#!/usr/bin/env python3
"""hub_liveness_probe.py — 只读探针：查「中枢纳管的自动化」最后一次**真实运行**时间。

为什么需要它（2026-09-27）
--------------------------
`💓 中枢存活看门狗` v1.0 的判据是「读 registry.automations[].last_run，超 180min 判失联」——
而 registry 里那个 `last_run` 是**镜像**，由 **5 条自动化各自回写**（SKILL.md 旧规范要求
"结束回写 last_run"），于是实测出现：
  · 5 种时间格式（`+0800` 无冒号 / 无时区 / 带微秒 / 含中文注释 `(每2h)`）
  · 39 处格式不规范、2 处 `fromisoformat` 直接解析失败
  · 多条 `next_run` 过期 2–4 天而无人知（D1 只比 rrule/status，这两个字段从不在覆盖内）
  · 没人负责定期刷新镜像 → 看门狗看到陈旧值 → 它只好"顺手"改别人的字段（越界写声明文件）

本探针把判据换成 **DB 真值**（`automation_runs` / `automation_runtime_state`，唯一真值来源），
于是 `automations[].last_run` / `next_run` 已从 registry 摘除，看门狗从此**完全只读**。

用法
----
    python3 hub_liveness_probe.py --json
    python3 hub_liveness_probe.py --json --threshold-hours 3
    python3 hub_liveness_probe.py --brief

退出码
------
    0  正常（**不代表没有失联**，只代表探针取到了证据；失联项看 items[].stale）
    2  输入不可读（registry / DB / python-sqlite 任一不可用）—— **「读不出来」绝不等于「没有失联」**，
       调用方必须把 rc=2 当成告警，而不是当成"全部健康"。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_REGISTRY = Path("/Users/guan/WorkBuddy/Claw/.workbuddy/inspection_hub/registry.json")
DEFAULT_DB = Path(os.environ.get("HOME", "/Users/guan")) / ".workbuddy" / "workbuddy.db"
MAX_CADENCE_H = 48.0   # 与 hub_reconcile.HEARTBEAT_MAX_CADENCE_H 一致：长周期项不判心跳


def _load_cadence_helpers():
    """复用 hub_reconcile 的节奏/阈值实现（**单一实现，不重写第二份公式**）。

    自己再写一份"周期 → 阈值"的映射，就是给同一个事实造第二份副本 —— 本项目已因此吃过亏。
    """
    try:
        import importlib.util
        p = Path(__file__).resolve().parent / "hub_reconcile.py"
        spec = importlib.util.spec_from_file_location("_hub_reconcile_for_probe", p)
        m = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(m)                 # type: ignore[union-attr]
        return m.cadence_hours, m.stale_threshold_h, None
    except Exception as e:  # noqa: BLE001
        return None, None, f"无法加载 hub_reconcile 的节奏实现({type(e).__name__}: {e})"


def _load_registry(path: Path) -> tuple[list[dict], str | None]:
    """返回 (纳管清单, 错误)。错误非空 ⇒ 调用方必须按 rc=2 处理。"""
    if not path.exists():
        return [], f"registry 不存在: {path}"
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return [], f"registry 读不动({type(e).__name__}): {e}"
    if not isinstance(d, dict):
        return [], "registry 根不是对象"
    autos = d.get("automations")
    if not isinstance(autos, list):
        return [], "registry.automations 缺失或不是数组（声明被清空 = 不该被读成『没有失联』）"
    out = [{"id": a.get("id"), "name": a.get("name"), "rrule": a.get("rrule")}
           for a in autos if isinstance(a, dict) and a.get("id")]
    if not out:
        return [], "registry.automations 为空数组（同上：空声明不是健康）"
    return out, None


def _to_ms(v) -> float | None:
    """平台字段既有毫秒也有秒，统一成毫秒。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f <= 0:
        return None
    return f if f > 1e11 else f * 1000.0


def _db_last_runs(db: Path) -> tuple[dict[str, float], str | None]:
    """返回 ({automation_id: 最近运行时刻(ms)}, 错误)。取两处证据的较大值：
       ① automation_runs.created_at —— 每次派发都留一行
       ② automation_runtime_state.last_run_at —— 平台回写的最近一次完成时刻
    """
    if not db.exists():
        return {}, f"调度库不存在: {db}"
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except Exception as e:  # noqa: BLE001
        return {}, f"调度库打不开({type(e).__name__}): {e}"
    out: dict[str, float] = {}
    try:
        for aid, ts in con.execute(
                "select automation_id, max(created_at) from automation_runs group by automation_id"):
            ms = _to_ms(ts)
            if aid and ms:
                out[aid] = max(out.get(aid, 0.0), ms)
        try:
            for aid, ts in con.execute("select automation_id, last_run_at from automation_runtime_state"):
                ms = _to_ms(ts)
                if aid and ms:
                    out[aid] = max(out.get(aid, 0.0), ms)
        except sqlite3.OperationalError:
            pass  # 该表可能不存在：不是致命，runs 表已够
    except sqlite3.OperationalError as e:
        return {}, f"调度库查询失败: {e}"
    finally:
        con.close()
    return out, None


def probe(registry: Path, db: Path, now: datetime.datetime | None = None,
          threshold_override: float | None = None) -> dict:
    """判据 = **每条自己的调度周期 + 宽限**（小时类 +3h / 每日类 +6h），
       与 hub_reconcile 的 D4 心跳判据**共用同一实现**。

    为什么不沿用 v1.0 的固定 180min：实测会把**每日/每周/每月**的自动化全判成"失联"
    （25 条声明里 20 条中招，其中日更的刚跑完 9h 就被报失联、周更的 157h 也在名单里）
    → 假告警淹掉真告警。**判据必须按各自的节奏定，不能一刀切。**
    """
    now = now or datetime.datetime.now()
    declared, err_r = _load_registry(registry)
    last, err_d = _db_last_runs(db)
    cadence_hours, stale_threshold_h, err_h = _load_cadence_helpers()
    errors = [e for e in (err_r, err_d, err_h) if e]

    items = []
    for a in declared:
        cad = cadence_hours(a.get("rrule")) if cadence_hours else None
        if threshold_override is not None:
            thr, judge = float(threshold_override), "override"
        elif cad is None:
            thr, judge = None, "cadence_unknown"          # 解析不出周期 → 不判（但如实标注，不当成健康）
        elif cad > MAX_CADENCE_H:
            thr, judge = None, "long_period_skipped"      # 与 D4 一致：>48h 的项不走心跳判据
        else:
            thr, judge = stale_threshold_h(cad), "cadence+grace"

        ms = last.get(a["id"])
        if ms is None:
            items.append({**a, "last_run": None, "age_h": None, "threshold_h": thr,
                          "judge": judge, "stale": False,
                          "note": "从未运行（视为未初始化，不判失联）"})
            continue
        age_h = (now.timestamp() * 1000 - ms) / 3600000.0
        items.append({**a,
                      "last_run": datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S"),
                      "age_h": round(age_h, 1),
                      "threshold_h": round(thr, 1) if thr is not None else None,
                      "judge": judge,
                      "stale": bool(thr is not None and age_h > thr)})

    stale = [i for i in items if i["stale"]]
    return {
        "ok": not errors,
        "errors": errors,
        "generated_at": now.isoformat(timespec="seconds"),
        "registry": str(registry),
        "db": str(db),
        "counts": {
            "declared": len(items),
            "judged": sum(1 for i in items if i["judge"] in ("cadence+grace", "override")),
            "not_judged": sum(1 for i in items if i["judge"] not in ("cadence+grace", "override")),
            "stale": len(stale),
            "never_run": sum(1 for i in items if i["last_run"] is None),
        },
        "items": items,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="只读探针：中枢纳管自动化的最后一次真实运行时间")
    ap.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--threshold-hours", type=float, default=None,
                    help="强制统一阈值（默认不传 = 按每条自己的调度周期算，推荐）")
    ap.add_argument("--json", action="store_true", help="输出 JSON（默认即 JSON）")
    ap.add_argument("--brief", action="store_true", help="只输出失联项，一行一条")
    args = ap.parse_args()

    r = probe(Path(args.registry), Path(args.db), threshold_override=args.threshold_hours)

    if args.brief:
        if not r["ok"]:
            print(f"❌ 探针输入不可读：{'; '.join(r['errors'])}")
            return 2
        for i in [x for x in r["items"] if x["stale"]]:
            print(f"[失联] {i['name']} 上次 {i['last_run']} 已 {i['age_h']}h（阈 {i['threshold_h']}h）")
        return 0

    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
