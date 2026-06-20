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

HOLIDAYS_FILE = "data/astock_holidays.json"


def load_holidays(path: str = HOLIDAYS_FILE) -> set:
    """加载休市日期集合"""
    try:
        with open(path) as f:
            data = json.load(f)
        return set(data.get("all_holiday_dates", []))
    except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
        print(f"[错误] 无法加载休市日历: {e}", file=sys.stderr)
        sys.exit(2)


def is_trading_day(d: date, holidays: set) -> bool:
    """判断某日是否为A股交易日"""
    # 周末直接排除
    if d.weekday() >= 5:  # 5=周六, 6=周日
        return False
    # 法定节假日排除
    return d.isoformat() not in holidays


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
            reason = "（法定节假日休市）"

    print(f"{target.isoformat()} → {status} {reason}")
    sys.exit(0 if trading else 1)


if __name__ == "__main__":
    main()
