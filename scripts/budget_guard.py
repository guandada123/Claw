"""
budget_guard.py — 预算守护与用量拦截器
========================================
功能：实时检查预算状态，¥350 触发 Flash 锁定
      日超¥25 告警，单次调用¥5上限拦截
集成：作为 Claw/QTS 项目的 LLM 调用前置过滤

用法：
    from budget_guard import check_budget_status, get_allowed_model, verify_call_cost

版本：v2.0 | 2026-06-14
"""

import calendar
import time
from datetime import date

# check_budget_status() 的 60 秒 TTL 缓存
_budget_cache: dict | None = None
_budget_cache_time: float = 0
_BUDGET_CACHE_TTL = 60  # 秒


# ============================================================
# 预算配置（MONTHLY_BUDGET 从 cost_tracker 统一导入）
# ============================================================
# 注意：MONTHLY_BUDGET 在 check_budget_status() 内延迟导入，
#       避免模块级别的循环依赖和路径问题
FLASH_LOCK_THRESHOLD = 350.0  # 超过¥350自动锁定为Flash模式
DAILY_WARNING = 25.0          # 日超¥25告警
MAX_SINGLE_CALL = 5.0         # 单次调用 ¥5 上限（超限自动拦截）


# ============================================================
# 预算状态检查
# ============================================================

def check_budget_status() -> dict:
    """
    检查当前月预算状态（委托 cost_tracker 读取，消除重复 JSONL 解析）。

    返回
    ----
    dict : {
        "spent": float,      # 本月已花费（¥）
        "remaining": float,  # 剩余预算（¥）
        "pct": float,        # 已用百分比
        "tier": str,         # full / normal / flash_preferred / flash_only
        "msg": str,          # 状态描述
    }
    """
    global _budget_cache, _budget_cache_time
    now = time.time()
    if _budget_cache is not None and now - _budget_cache_time < _BUDGET_CACHE_TTL:
        return dict(_budget_cache)

    try:
        from cost_tracker import get_monthly_spent, MONTHLY_BUDGET_CNY as MONTHLY_BUDGET
    except ImportError:
        from scripts.cost_tracker import get_monthly_spent, MONTHLY_BUDGET_CNY as MONTHLY_BUDGET
    month = date.today().strftime("%Y-%m")
    spent = get_monthly_spent(month)

    remaining = MONTHLY_BUDGET - spent
    pct = spent / MONTHLY_BUDGET

    # 层级判定
    if pct >= 0.875:  # ≥¥350
        tier = "flash_only"
        msg = f"⛔ 预算已用{pct*100:.0f}%（¥{spent:.0f}/¥{MONTHLY_BUDGET:.0f}），已锁定Flash模式"
    elif pct >= 0.7:   # ≥¥280
        tier = "flash_preferred"
        msg = f"⚠️  预算已用{pct*100:.0f}%（¥{spent:.0f}），建议优先使用Flash"
    elif pct >= 0.5:   # ≥¥200
        tier = "normal"
        msg = f"🟡 预算已用{pct*100:.0f}%（¥{spent:.0f}），正常调度"
    else:
        tier = "full"
        msg = f"🟢 预算充足（已用¥{spent:.1f}，剩余¥{remaining:.1f}）"

    _budget_cache = {
        "spent": spent,
        "remaining": remaining,
        "pct": pct,
        "tier": tier,
        "msg": msg,
    }
    _budget_cache_time = now
    return dict(_budget_cache)


def get_allowed_model(intended_model: str, task_priority: str = "normal") -> str:
    """
    根据预算状态返回实际可用模型。

    参数
    ----
    intended_model : str
        请求使用的模型（如 "gpt-5", "deepseek-v4-pro"）
    task_priority : str
        任务优先级 ("normal" / "high" / "critical")

    返回
    ----
    str : 实际允许使用的模型
    """
    status = check_budget_status()

    # 预算充足 → 允许任何模型
    if status["tier"] == "full":
        return intended_model

    # Flash 锁定模式
    if status["tier"] == "flash_only":
        if task_priority == "critical" and intended_model in ("gpt-5", "claude-sonnet-4-20250514", "claude-opus-4-20250514", "gpt-4.1", "gpt-4o-mini"):
            # 关键任务 + 旗舰模型 → 自动允许（自动化场景无需人工确认）
            print(f"\n⚠️ 预算紧张（已用¥{status['spent']:.0f}），关键任务允许使用 {intended_model}")
            return intended_model
        return "deepseek-v4-flash"

    # Flash 优先模式
    if status["tier"] == "flash_preferred":
        if intended_model in ("deepseek-v4-pro",) and task_priority == "normal":
            # 普通任务的 Pro 降为 Flash
            return "deepseek-v4-flash"
        elif intended_model in ("gpt-5", "claude-sonnet-4-20250514", "claude-opus-4-20250514") and task_priority != "critical":
            return "deepseek-v4-pro"  # 非关键的旗舰降为 Pro

    return intended_model


def verify_call_cost(estimated_input: int, estimated_output: int,
                     model: str) -> tuple:
    """
    调用前验证预估成本是否超限。

    参数
    ----
    estimated_input : int
        预估输入 Token
    estimated_output : int
        预估输出 Token
    model : str
        目标模型

    返回
    ----
    (bool, float) : (是否允许调用, 预估成本 ¥)
    """
    try:
        from cost_tracker import MODEL_PRICES, _match_model  # noqa: F401
    except ImportError:
        from scripts.cost_tracker import MODEL_PRICES, _match_model

    model_key = _match_model(model)
    prices = MODEL_PRICES.get(model_key, {"input": 0, "output": 0})

    estimated_cost = (
        estimated_input * prices["input"]
        + estimated_output * prices["output"]
    ) / 10000

    if estimated_cost > MAX_SINGLE_CALL:
        print(f"\n🔴 单次调用预估 ¥{estimated_cost:.2f} 超出上限 ¥{MAX_SINGLE_CALL}，已自动拦截")
        print(f"   模型: {model}")
        print(f"   输入: {estimated_input} Token, 输出: {estimated_output} Token")
        return (False, estimated_cost)

    return (True, estimated_cost)


# ============================================================
# 便捷函数
# ============================================================

def budget_summary() -> str:
    """快速预算摘要（用于日志/推送）"""
    status = check_budget_status()
    try:
        from cost_tracker import MODEL_PRICES, _match_model  # noqa: F401
    except ImportError:
        pass
    try:
        from cost_tracker import MONTHLY_BUDGET_CNY as MONTHLY_BUDGET
    except ImportError:
        from scripts.cost_tracker import MONTHLY_BUDGET_CNY as MONTHLY_BUDGET
    _, days_in_month = calendar.monthrange(date.today().year, date.today().month)
    today_day = date.today().day
    projected = status["spent"] / max(today_day, 1) * days_in_month

    lines = [
        f"📊 本月预算：¥{status['spent']:.1f} / ¥{MONTHLY_BUDGET:.0f}",
        f"   {'=' * 30}",
        f"   已用比例：{status['pct']*100:.1f}%",
        f"   剩余预算：¥{status['remaining']:.1f}",
        f"   预估月底：¥{projected:.0f} {'⚠️' if projected > MONTHLY_BUDGET else '✅'}",
        f"   当前层级：{status['tier']}",
        f"   {status['msg']}",
    ]

    # 日检查（预加载当日记录，避免 daily_report 重复读取 JSONL）
    try:
        from cost_tracker import daily_report, _load_records
    except ImportError:
        from scripts.cost_tracker import daily_report, _load_records
    today_records = _load_records(date.today().isoformat())
    today = daily_report("today_only", records=today_records)
    if isinstance(today, dict) and today.get("total", 0) > DAILY_WARNING:
        lines.append(f"   ⚠️ 今日已消费 ¥{today['total']:.2f}，超过日警告线 ¥{DAILY_WARNING}")

    return "\n".join(lines)


# ============================================================
# CLI 入口
# ============================================================

if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "status":
        print(budget_summary())
    elif cmd == "check":
        model = sys.argv[2]
        allowed = get_allowed_model(model)
        print(f"请求模型: {model} → 实际可用: {allowed}")
    elif cmd == "verify":
        inp = int(sys.argv[2])
        out = int(sys.argv[3])
        model = sys.argv[4]
        allowed, cost = verify_call_cost(inp, out, model)
        print(f"预估成本: ¥{cost:.4f} | 允许: {'✅' if allowed else '❌'}")
    else:
        print("用法: python budget_guard.py [status|check <model>|verify <in> <out> <model>]")
