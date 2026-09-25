#!/usr/bin/env python3
"""A股交易日检查脚本。

用法:
    python3 scripts/is_trading_day.py              # 检查今天
    python3 scripts/is_trading_day.py 2026-06-22   # 检查指定日期

返回:
    exit 0 = 交易日
    exit 1 = 非交易日（含周末+节假日）
"""

import json
import sys
from datetime import date, datetime
from pathlib import Path

# cwd 无关：基于脚本位置推导 holiday 日历，不再要求“必须在 Claw 根目录运行”（F13 修复）
HOLIDAYS_FILE = str(Path(__file__).resolve().parent.parent / "data" / "astock_holidays.json")


def load_holidays(path: str = HOLIDAYS_FILE) -> dict:
    """加载休市日历。返回 {'dates': set, 'names': {date: 假期名}}。

    09-25 修复：原先只读 all_holiday_dates 扁平列表，无法说明"因何休市" →
    reason 恒为笼统的"法定节假日休市"，中秋节/国庆节无法区分（日志与告警歧义）。
    现优先用 holidays{} 分节（可命名），all_holiday_dates 仅作兜底来源。
    """
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
        print(f"[错误] 无法加载休市日历: {e}", file=sys.stderr)
        sys.exit(2)

    names: dict = {}
    dates: set = set()
    for hname, info in (data.get("holidays") or {}).items():
        for ds in info.get("dates", []) or []:
            dates.add(ds)
            names[ds] = hname
    # 兜底：分节缺失时用扁平列表
    for ds in data.get("all_holiday_dates", []) or []:
        dates.add(ds)
        names.setdefault(ds, "法定节假日")
    return {"dates": dates, "names": names}


def is_trading_day(d: date, holidays) -> bool:
    """判断某日是否为A股交易日。

    holidays 兼容两种入参：load_holidays() 的 dict，或裸 set（旧调用方）。
    """
    # 周末直接排除
    if d.weekday() >= 5:  # 5=周六, 6=周日
        return False
    # 法定节假日排除
    dates = holidays["dates"] if isinstance(holidays, dict) else holidays
    return d.isoformat() not in dates


def main():
    if len(sys.argv) > 1:
        try:
            target = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
        except ValueError:
            print(f"[错误] 日期格式无效: {sys.argv[1]}，请使用 YYYY-MM-DD", file=sys.stderr)
            sys.exit(2)
    else:
        target = date.today()

    holidays = load_holidays()
    trading = is_trading_day(target, holidays)

    status = "交易日 ✅" if trading else "非交易日 ❌"
    reason = ""
    if not trading:
        if target.weekday() >= 5:
            weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            reason = f"（{weekdays[target.weekday()]}，周末休市）"
        else:
            hname = holidays["names"].get(target.isoformat(), "法定节假日")
            reason = f"（{hname}休市）"

    print(f"{target.isoformat()} → {status} {reason}")
    sys.exit(0 if trading else 1)


if __name__ == "__main__":
    main()
