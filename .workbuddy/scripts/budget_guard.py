"""
budget_guard.py — 预算守护与用量拦截器（v3.0：积分口径）
========================================================
单位：**积分**（官方计费，本工作区唯一真实的消耗单位）。
阈值：月预算 / 87.5% 触发 Flash 锁定 / 日警告线，全部以积分为单位。

## 为什么从 ¥ 换成积分（2026-10-10，PA-009）
v2.x 的 ¥ 口径全部来自 `cost_tracker.MODEL_PRICES`，实测两处硬伤：
  ① 该表约为公开牌价的数十倍（同一批 token，与看板价目表差约 200 倍）；
  ② 自记账只覆盖全量 token 的 0.031%（仅显式插桩的调用，且 `log_estimate()` 写的是手填估值）。
→ 它诞生于「自付 API 账单」时代，现在走积分，管的是一个不存在的量。
新口径的数据源：`workbuddy.db → session_usage.credit_json`（只读），
读取逻辑单源在 `credit_meter.py`（本文件不自己解析 credit_json）。

## 月额度怎么来的（2026-10-10 用户确认口径：¥58 档 + 历史积分 + 每日签到）
月可支配额度 = **套餐月度发放 + 签到累计**，不把历史余额算进来（读不到余额，且它是缓冲而非月流量）：
- 套餐发放：¥58 档（个人专业版/标准版）= **基础 2000 积分/月**；官网当期该档另有
  「每月赠送 2,000 积分」，故 `DEFAULT_MONTHLY_GRANT = 4000`（赠送一停就改回 2000）；
- 签到：**100 积分/天**（实测 `wb-signin status` 的 `daily_credit`，Buddy加油站 赛季10）→ 按当月天数计入；
- 于是 10 月额度 = 4000 + 100×31 = **7100**。

三个旋钮（优先级从高到低）：
| 环境变量 | 作用 | 默认 |
| --- | --- | --- |
| `WB_CREDIT_BUDGET` | 直接指定月额度（覆盖下面两项） | 不设 |
| `WB_CREDIT_GRANT_MONTHLY` | 套餐月发（基础+赠送） | 4000 |
| `WB_CREDIT_CHECKIN_DAILY` | 签到日产 | 100 |

⚠️ **读不到余额这件事本身是个已知缺口**：真正的硬约束是「钱包还能撑多久」，
而客户端只暴露 3 个账务接口（`checkin-activity-status` / `daily-checkin` /
`get-enterprise-user-usage`），余额类路径实测全部 404 → 只能用「月度流量」近似。

用法：
    from budget_guard import check_budget_status, get_allowed_model, verify_call_cost
"""

from __future__ import annotations  # 兼容 3.9: X|Y 注解字符串化

import calendar
import os
import sys
import time
from datetime import date

# 确保本脚本目录在 sys.path，使兄弟模块（credit_meter / cost_tracker）始终可解析
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import credit_meter
from credit_meter import CreditReadError, total_for_month

# check_budget_status() 的 60 秒 TTL 缓存
_budget_cache: dict | None = None
_budget_cache_time: float = 0
_BUDGET_CACHE_TTL = 60  # 秒


# ============================================================
# 预算配置（积分口径）
# ============================================================
# 月额度 = 套餐月发 + 签到累计（组成见文件头）；也可用 WB_CREDIT_BUDGET 一把覆盖。
BUDGET_ENV_VAR = "WB_CREDIT_BUDGET"
GRANT_ENV_VAR = "WB_CREDIT_GRANT_MONTHLY"
CHECKIN_ENV_VAR = "WB_CREDIT_CHECKIN_DAILY"
DEFAULT_MONTHLY_GRANT = 4000.0  # ¥58 档：基础 2000 + 当期活动赠送 2000
DEFAULT_CHECKIN_DAILY = 100.0  # 实测 wb-signin status → daily_credit
FLASH_LOCK_PCT = 0.875  # 已用 ≥ 该比例 → 锁定 Flash 模式（沿用 v2.x 的 350/400）
FLASH_PREFERRED_PCT = 0.7
NORMAL_PCT = 0.5
# 单次调用上限：护栏而非节流器。按实测 2.68 积分/百万 token，30 积分 ≈ 1,120 万 token，
# 现实中顶不到；留着是为了「异常巨大的单次调用」有个硬闸。
MAX_SINGLE_CALL_CREDITS = 30.0
MAX_BUDGET_CAP = 1_000_000  # 预算上限护栏：超大值截顶，防止失控

# ⚠️ v2.x 的「日警告线」（¥25/日）已删，不是遗漏：
#   积分侧只有**会话级**归属（按会话最后一次活动日整块计入），长会话会把几天的量
#   全落在结束那天 → 日数字是尖峰、不是当日增量，拿它发警告必然误报（实测：今天
#   「今日归属 1143 积分」其中绝大部分来自一个跨日会话）。
#   真要日粒度，得走 token 侧（请求级时间戳）× 实测倍率，不属本模块职责。

# ============================================================
# 模型分层常量
# ============================================================
# 统一维护入口：模型名或 Tier 规则变更时只需改此处，不再散落各函数

# 旗舰模型（flash_only 关键任务可豁免降级）
FLAGSHIP_MODELS = (
    "gpt-5",
    "claude-sonnet-4-20250514",
    "claude-opus-4-20250514",
    "gpt-4.1",
    "gpt-4o-mini",
)
PRO_MODEL = "deepseek-v4-pro"  # 中级模型
FLASH_MODEL = "deepseek-v4-flash"  # 兜底降级目标

# flash_preferred 层级下，非关键任务需降级的旗舰子集
# （gpt-4.1 / gpt-4o-mini 成本较低，不在此列）
HIGH_COST_FLAGSHIP = (
    "gpt-5",
    "claude-sonnet-4-20250514",
    "claude-opus-4-20250514",
)


# ============================================================
# 预算值健壮解析
# ============================================================


def parse_budget(raw: str | None) -> int:
    """将环境变量/配置中的预算值解析为安全整数（单位：积分）。

    规则（fail-safe）：
      - None / 空字符串 / 仅空白 → 0
      - 非数字字符串 → 0
      - 小数 → 向下取整
      - 负数 → 0
      - 超出 MAX_BUDGET_CAP → 截顶为 CAP

    调用方使用返回值前应检查 `<= 0` → fail-closed。
    """
    if raw is None:
        return 0
    stripped = str(raw).strip()
    if not stripped:
        return 0
    try:
        val = float(stripped)
    except (ValueError, TypeError):
        return 0
    if val < 0:
        return 0
    if val > MAX_BUDGET_CAP:
        return MAX_BUDGET_CAP
    return int(val)


def _env_float(name: str, default: float) -> float:
    """读环境变量的数值；未设/非数字/<=0 一律回退默认值（不让配置写错就失能）。"""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    parsed = parse_budget(raw)
    return float(parsed) if parsed > 0 else default


def monthly_budget_credits() -> float:
    """当前月的可支配额度（积分）。

    优先级：`WB_CREDIT_BUDGET`（直接给额度）> 套餐月发 + 签到×当月天数。
    `WB_CREDIT_BUDGET` **显式设了但写错**（非数字/<=0）→ 返回 <b>0</b>，
    由调用方按 fail-closed 处理（沿用 v2.x 的「配置异常即锁 Flash」约定），
    不静默回退——配置写错必须看得见。
    """
    raw = os.environ.get(BUDGET_ENV_VAR)
    if raw is not None and str(raw).strip():
        return float(parse_budget(raw))
    today = date.today()
    days = calendar.monthrange(today.year, today.month)[1]
    grant = _env_float(GRANT_ENV_VAR, DEFAULT_MONTHLY_GRANT)
    daily = _env_float(CHECKIN_ENV_VAR, DEFAULT_CHECKIN_DAILY)
    return grant + daily * days


# ============================================================
# 预算状态检查
# ============================================================


def check_budget_status() -> dict:
    """
    检查当前月积分消耗状态（数据源单源：credit_meter）。

    返回
    ----
    dict : {
        "spent": float,      # 本月已消耗（积分）
        "remaining": float,  # 剩余（积分）
        "pct": float,        # 已用百分比
        "tier": str,         # full / normal / flash_preferred / flash_only
        "msg": str,          # 状态描述
        "unit": str,         # 固定 "credits"
    }
    """
    global _budget_cache, _budget_cache_time
    now = time.time()
    if _budget_cache is not None and now - _budget_cache_time < _BUDGET_CACHE_TTL:
        return dict(_budget_cache)

    budget = monthly_budget_credits()
    month = date.today().strftime("%Y-%m")

    # 读数失败 → fail-closed：绝不因「读不到」而放行高消耗模型
    try:
        spent = total_for_month(month)
    except CreditReadError as e:
        return {
            "spent": 0.0,
            "remaining": 0.0,
            "pct": 1.0,
            "tier": "flash_only",
            "msg": f"⛔ 积分读数失败（{e}），已锁定Flash模式",
            "unit": "credits",
        }

    # 预算配置异常（预算=0/误改/非数字）→ 同样 fail-closed
    if budget <= 0:
        return {
            "spent": spent,
            "remaining": 0.0,
            "pct": 1.0,
            "tier": "flash_only",
            "msg": "⛔ 预算配置异常（月预算<=0），已锁定Flash模式",
            "unit": "credits",
        }

    remaining = budget - spent
    pct = spent / budget

    # 层级判定
    if pct >= FLASH_LOCK_PCT:
        tier = "flash_only"
        msg = f"⛔ 积分已用{pct * 100:.0f}%（{spent:.0f}/{budget:.0f} 积分），已锁定Flash模式"
    elif pct >= FLASH_PREFERRED_PCT:
        tier = "flash_preferred"
        msg = f"⚠️  积分已用{pct * 100:.0f}%（{spent:.0f} 积分），建议优先使用Flash"
    elif pct >= NORMAL_PCT:
        tier = "normal"
        msg = f"🟡 积分已用{pct * 100:.0f}%（{spent:.0f} 积分），正常调度"
    else:
        tier = "full"
        msg = f"🟢 积分充足（已用{spent:.1f}，剩余{remaining:.1f}）"

    _budget_cache = {
        "spent": spent,
        "remaining": remaining,
        "pct": pct,
        "tier": tier,
        "msg": msg,
        "unit": "credits",
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
        if task_priority == "critical" and intended_model in FLAGSHIP_MODELS:
            # 关键任务 + 旗舰模型 → 自动允许（自动化场景无需人工确认）
            print(f"\n⚠️ 积分紧张（已用{status['spent']:.0f}），关键任务允许使用 {intended_model}")
            return intended_model
        return FLASH_MODEL

    # Flash 优先模式
    if status["tier"] == "flash_preferred":
        if intended_model == PRO_MODEL and task_priority == "normal":
            # 普通任务的 Pro 降为 Flash
            return FLASH_MODEL
        elif intended_model in HIGH_COST_FLAGSHIP and task_priority != "critical":
            return PRO_MODEL  # 非关键的旗舰降为 Pro

    return intended_model


def estimate_call_credits(estimated_input: int, estimated_output: int) -> float:
    """按实测倍率把 token 估算成积分（唯一来源：credit_meter 的实测常数）。"""
    return (
        (estimated_input + estimated_output) / 1_000_000 * credit_meter.CREDITS_PER_MILLION_TOKENS
    )


def verify_call_cost(estimated_input: int, estimated_output: int, model: str = "") -> tuple:
    """
    调用前验证预估消耗是否超限（单位：积分）。

    参数
    ----
    estimated_input : int
        预估输入 Token
    estimated_output : int
        预估输出 Token
    model : str
        目标模型（仅用于提示文案；倍率按实测的「积分/百万 token」总口径折算）

    返回
    ----
    (bool, float) : (是否允许调用, 预估积分)
    """
    estimated = estimate_call_credits(estimated_input, estimated_output)

    if estimated > MAX_SINGLE_CALL_CREDITS:
        print(
            f"\n🔴 单次调用预估 {estimated:.2f} 积分超出上限 {MAX_SINGLE_CALL_CREDITS}，已自动拦截"
        )
        if model:
            print(f"   模型: {model}")
        print(f"   输入: {estimated_input} Token, 输出: {estimated_output} Token")
        return (False, estimated)

    return (True, estimated)


# ============================================================
# 便捷函数
# ============================================================


def budget_summary() -> str:
    """快速预算摘要（用于日志/推送）"""
    status = check_budget_status()
    budget = monthly_budget_credits()
    _, days_in_month = calendar.monthrange(date.today().year, date.today().month)
    today_day = date.today().day
    projected = status["spent"] / max(today_day, 1) * days_in_month

    lines = [
        f"📊 本月积分：{status['spent']:.1f} / {budget:.0f}",
        f"   {'=' * 30}",
        f"   已用比例：{status['pct'] * 100:.1f}%",
        f"   剩余额度：{status['remaining']:.1f} 积分",
        f"   预估月底：{projected:.0f} 积分 {'⚠️' if projected > budget else '✅'}",
        f"   当前层级：{status['tier']}",
        f"   {status['msg']}",
    ]

    # 不再输出「今日消耗」：积分侧只有会话级归属，日粒度必然尖峰失真（见常量区说明）。
    # 需要日粒度时看 token 侧（`.workbuddy/reports/token-dashboard.html`）。
    return "\n".join(lines)


# ============================================================
# CLI 入口
# ============================================================

if __name__ == "__main__":
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
        model = sys.argv[4] if len(sys.argv) > 4 else ""
        allowed, credits = verify_call_cost(inp, out, model)
        print(f"预估积分: {credits:.4f} | 允许: {'✅' if allowed else '❌'}")
    else:
        print("用法: python budget_guard.py [status|check <model>|verify <in> <out> [model]]")
