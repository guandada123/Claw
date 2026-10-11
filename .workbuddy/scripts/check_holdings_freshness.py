#!/usr/bin/env python3
"""持仓数据新鲜度守卫 — 把「数据断供」从静默变成显式告警。

背景（2026-10-11 全项目审计 P0-2）：
    实盘持仓文件 `.workbuddy/data/user/portfolio.json` 自 2026-09-14 起 **27 天未刷新**，
    而 5 个「盘中助理实盘监控」自动化 + QTS `daily_risk_monitor.py` 都在读它：
    价格是实时拉的，但**持仓清单 / 成本价 / 现金 / 总资产全是陈旧快照**，
    且全链路无任何提示。本脚本负责把这件事显式化。

判据（**按交易日**计，非自然日；节假日由 astock_holidays.json 提供）：
    · 实盘 live：错过 ≥ 2 个交易日的更新 → 🔴 ALERT
    · 模拟盘 sim：错过 ≥ 2 个交易日的更新 → 🟠 WARN（有同步自动化兜底，容忍度同）
    · 文件缺失 / updated 无法解析 → 判定为「无法判定」，**不静默当正常**

退出码：0=新鲜（静默 no-op）｜1=有陈旧（已告警，或 --dry-run 检出）｜2=无法判定

用法：
    python3 check_holdings_freshness.py [--dry-run] [--json]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CLAW = SCRIPT_DIR.parent.parent  # .workbuddy/scripts → Claw 根
LIVE_PORTFOLIO = CLAW / ".workbuddy" / "data" / "user" / "portfolio.json"
SIM_PORTFOLIO = CLAW / ".workbuddy" / "data" / "simulation" / "portfolio.json"
PUSH_CARD = SCRIPT_DIR / "push_card.py"

# 错过 ≥ 该数量的交易日即告警。周五收盘更新 → 周一早晨 lag=1（正常，等待周一收盘刷新），
# 周二早晨仍未更新 → lag=2 → 告警。
MAX_MISSED_TRADING_DAYS = 2
# 交易日历不可用时的降级阈值（自然日）。必须显著放宽：否则「周五更新、周日检查」
# 会被自然日算成 2 天 → 每周末恒真误报（2026-10-11 实测踩到）。
MAX_MISSED_NATURAL_DAYS = 4

_DATE_FORMATS = ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M")


def parse_updated(raw: object) -> date | None:
    """解析 portfolio 的 updated / updated_at 字段（兼容 '2026-09-14' 与带时分秒两种）。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    s = raw.strip()[:19]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def load_trading_calendar() -> tuple[object, bool]:
    """返回 (holidays, ok)。ok=False 表示日历不可用 → 降级为自然日判断。"""
    try:
        sys.path.insert(0, str(SCRIPT_DIR))
        import is_trading_day as itd  # noqa: PLC0415

        return itd.load_holidays(), True
    except SystemExit:  # load_holidays 失败会 sys.exit(2)，必须显式捕获
        return None, False
    except Exception:  # noqa: BLE001 - 日历不可用属降级场景，不致命
        return None, False


def missed_days(since: date, today: date, holidays, cal_ok: bool) -> tuple[int, str]:
    """返回 (错过的交易日数, 口径说明)。日历不可用时退化为自然日并如实标注。"""
    if since >= today:
        return 0, "交易日"
    if not cal_ok:
        return (today - since).days, "自然日(日历不可用降级)"
    import is_trading_day as itd  # noqa: PLC0415

    n = 0
    d = since + timedelta(days=1)
    while d <= today:
        if itd.is_trading_day(d, holidays):
            n += 1
        d += timedelta(days=1)
    return n, "交易日"


def inspect(path: Path, field_hint: tuple[str, ...], today: date, holidays, cal_ok: bool) -> dict:
    """检查单个 portfolio 文件的新鲜度。"""
    row: dict = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        row.update(status="unknown", reason="文件不存在", missed=None)
        return row

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        row.update(status="unknown", reason=f"解析失败: {e}", missed=None)
        return row

    # 语义时间优先（updated / config.updated_at），mtime 仅作兜底并如实标注
    raw = None
    for key in field_hint:
        node: object = data
        for part in key.split("."):
            node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                break
        if isinstance(node, str) and node.strip():
            raw = node
            break

    updated = parse_updated(raw)
    source = f"字段 {field_hint[0]}" if updated else ""
    if updated is None:
        updated = datetime.fromtimestamp(path.stat().st_mtime).date()
        source = "mtime(兜底: 字段缺失或不可解析)"

    lag, cal = missed_days(updated, today, holidays, cal_ok)
    # 阈值随口径切换：日历可信时按交易日，降级时放宽为自然日（避免周末恒真误报）
    limit = MAX_MISSED_TRADING_DAYS if cal_ok else MAX_MISSED_NATURAL_DAYS
    row.update(
        updated=updated.isoformat(),
        updated_source=source,
        missed=lag,
        calendar=cal,
        limit=limit,
        status="stale" if lag >= limit else "fresh",
    )
    return row


def build_live_target() -> dict:
    return {"name": "实盘", "path": LIVE_PORTFOLIO, "fields": ("updated",), "level": "alert"}


def build_sim_target() -> dict:
    return {
        "name": "模拟盘",
        "path": SIM_PORTFOLIO,
        "fields": ("config.updated_at", "updated"),
        "level": "warning",
    }


def main() -> int:
    """检查 → 必要时告警；返回退出码 0/1/2。"""
    ap = argparse.ArgumentParser(description="持仓数据新鲜度守卫")
    ap.add_argument("--dry-run", action="store_true", help="只检查不推送")
    ap.add_argument("--json", action="store_true", help="输出 JSON（供自动化解析）")
    ap.add_argument(
        "--dedupe-key",
        default="",
        help="传给 push_card 的去重键（默认按日：持仓新鲜度-YYYY-MM-DD）",
    )
    args = ap.parse_args()

    today = date.today()
    holidays, cal_ok = load_trading_calendar()

    targets = [build_live_target(), build_sim_target()]
    rows = []
    for t in targets:
        row = inspect(t["path"], t["fields"], today, holidays, cal_ok)
        row["name"] = t["name"]
        row["level"] = t["level"]
        rows.append(row)

    stale = [r for r in rows if r["status"] == "stale"]
    unknown = [r for r in rows if r["status"] == "unknown"]

    if args.json:
        print(json.dumps({"date": today.isoformat(), "rows": rows}, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            icon = {"fresh": "🟢", "stale": "🔴", "unknown": "❓"}[r["status"]]
            detail = (
                f"最后更新 {r.get('updated')}（{r.get('updated_source')}），"
                f"错过 {r.get('missed')} 个{r.get('calendar', '')}"
                if r["status"] != "unknown"
                else r.get("reason", "")
            )
            print(f"{icon} {r['name']}：{r['status']} — {detail}")
        print(
            f"口径：{'交易日' if cal_ok else '自然日(日历不可用降级)'}；阈值 ≥{MAX_MISSED_TRADING_DAYS}"
        )

    if stale:
        if args.dry_run:
            print("[DRY-RUN] 检出陈旧，未推送")
            return 1
        dedupe = args.dedupe_key or f"持仓新鲜度-{today.isoformat()}"
        pushed = push_alert_with_dedupe(stale, today, dedupe)
        return 1 if pushed else 2

    if unknown:
        # 无法判定 ≠ 正常：显式暴露，交由自动化记忆/看门狗处置
        print("[WARN] 存在无法判定的目标，需要人工确认")
        return 2
    return 0


def push_alert_with_dedupe(stale: list[dict], today: date, dedupe: str) -> bool:
    """带去重的推送：同日重复运行不重复打扰（去重命中视为已告警成功）。"""
    lines = []
    for r in stale:
        lines.append(
            f"**{r['name']}**：最后更新 `{r.get('updated', '?')}`（{r.get('updated_source', '?')}）\n"
            f"→ 已错过 **{r.get('missed')}** 个{r.get('calendar', '')}未刷新"
        )
    payload = {
        "title": f"🔴 持仓数据断供告警 — {today.isoformat()}",
        "level": "alert",
        "sections": [
            ["🩺 检测结果", "\n\n".join(lines)],
            [
                "🔧 处置建议",
                "① 实盘：确认国金账户持仓快照是否需要更新（截图/录入 → `portfolio.json`）\n"
                "② 模拟盘：检查「Claw→QTS 模拟盘 portfolio 同步」自动化是否正常\n"
                "③ 更新完成后本告警自动消失（按日去重，同日不重复打扰）",
            ],
        ],
        "footer": "持仓新鲜度守卫 · 口径=交易日（节假日日历感知）",
    }
    try:
        r = subprocess.run(
            [
                sys.executable,
                str(PUSH_CARD),
                "--title",
                payload["title"],
                "--json-stdin",
                "--dedupe-key",
                dedupe,
            ],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as e:
        print(f"[ERROR] 推送异常: {e}")
        return False
    out = (r.stdout or "") + (r.stderr or "")
    if "去重" in out:  # push_card 命中去重 → 视为「今日已告警」，不算失败
        print("[OK] 今日已告警过（去重命中）")
        return True
    if r.returncode != 0:
        print(f"[ERROR] 推送失败(rc={r.returncode}): {out[-300:]}")
        return False
    print("[OK] 飞书告警已推送")
    return True


if __name__ == "__main__":
    sys.exit(main())
