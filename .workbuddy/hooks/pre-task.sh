#!/bin/bash
# ============================================================
# L3 Guardrail Layer — Pre-Task Hook
# 用途: 每次自动化执行前强制校验，拦截已知陷阱
# 调用: source $CLAW/.workbuddy/hooks/pre-task.sh
#       或在 automation prompt 中引用
# ============================================================
set -euo pipefail

HOOK_NAME="pre-task"
TIMESTAMP=$(date -Iseconds)
LOG_PREFIX="[hook:$HOOK_NAME]"
LOG_FLAG_FILE="$CLAW/.workbuddy/tmp/hook_flags"

# --- Gate 1: RRULE 安全门禁（🔴 08-07 固化：根治多BYHOUR坑） ---
# 平台行为：单条 RRULE 的 BYHOUR 多值（如 BYHOUR=9,10,11,13,14）
#         仅触发第一个匹配小时，其余槽位静默丢失，无日志无报错。
# 治本：创建/修改自动化时必须拆为多条单-BYHOUR。
# 本 hook 在运行时做最后防线检测——发现多值 BYHOUR 立即告警。
check_rrule_safety() {
    local rrule="${1:-}"
    [ -n "$rrule" ] || return 0  # 无 rrule → 不涉及

    # 提取 BYHOUR 值（macOS 兼容：不用 grep -P）
    local byhour
    byhour=$(echo "$rrule" | sed -n 's/.*BYHOUR=\([^;]*\).*/\1/p' || true)

    if [ -n "$byhour" ]; then
        # 检查是否含逗号（多值）
        case "$byhour" in
            *,*)
                echo "$LOG_PREFIX 🔴 RRULE 危险: BYHOUR 含多值 [$byhour]"
                echo "$LOG_PREFIX    平台仅触发首个匹配小时，其余槽位静默丢失！"
                echo "$LOG_FLAG_FILE" > "$CLAW/.workbuddy/tmp/rrule_violation_flag"
                return 1
                ;;
        esac
    fi
    return 0
}

# --- Gate 2: 自动化 ID 必须带 automation- 前缀 ---
# 查询 workbuddy.db 时必须用带前缀的 ID，裸 ID 必误报。
validate_automation_id() {
    local aid="${1:-}"
    [ -n "$aid" ] || return 0
    case "$aid" in
        automation-*) return 0 ;;
        *)
            echo "$LOG_PREFIX ⚠️ Automation ID 格式异常: '$aid'（缺少 automation- 前缀）"
            return 1
            ;;
    esac
}

# --- Gate 3: 交易日感知（交易类自动化专用）---
# 非交易日且自动化含交易关键词 → 跳过并记录
check_trading_context() {
    local prompt="${1:-}"
    local is_trading=false

    # 检测 prompt 是否含交易关键词
    case "$prompt" in
        *选股*|*策略*|*监控*|*持仓*|*买卖*|*止损*|*止盈*|*开盘*|*收盘*|*盘中*|*模拟盘*|*实盘*)
            is_trading=true
            ;;
    esac

    [ "$is_trading" = true ] || return 0

    # 调用 is_trading_day.py 判断
    if command -v python3 &>/dev/null && [ -f "$CLAW/scripts/is_trading_day.py" ]; then
        if ! python3 "$CLAW/scripts/is_trading_day.py" 2>/dev/null; then
            echo "$LOG_PREFIX 📅 非交易日 + 交易类自动化 → 跳过（is_trading_day=false）"
            return 1
        fi
    fi
    return 0
}

# --- Gate 4: 内存看门狗窗口保护（08-06 教训）---
# 盘前 08:35-09:10 是 memwatch 高发区，关键自动化可能被硬杀
# 本 gate 不阻断，仅记录警告供事后排查
check_memwatch_risk() {
    local current_hour current_min
    current_hour=$(date +%H)
    current_min=$(date +%M)

    # 高危窗口: 08:35-09:10
    if [ "$current_hour" -eq 8 ] && [ "$current_min" -ge 35 ]; then
        echo "$LOG_PREFIX ⚠️ 处于 memwatch 高危窗口(08:35-09:xx)，历史有硬杀先例"
        return 0  # 仅警告，不阻断
    fi
    if [ "$current_hour" -eq 9 ] && [ "$current_min" -le 10 ]; then
        echo "$LOG_PREFIX ⚠️ 处于 memwatch 高危窗口(09:00-09:10)，历史有硬杀先例"
        return 0
    fi
    return 0
}

# --- 主入口：依次执行所有 Gate ---
# 用法: run_pre_checks "$RRULE" "$AUTOMATION_ID" "$PROMPT_TEXT"
run_pre_checks() {
    local rrule="${1:-}"
    local auto_id="${2:-}"
    local prompt="${3:-}"

    echo "$LOG_PREFIX === L3 Pre-Task Hook 开始 ($TIMESTAMP) ==="

    local failures=0

    # Gate 1: RRULE 安全（🔴 最高优先级）
    check_rrule_safety "$rrule" || ((failures++))

    # Gate 2: Automation ID 格式
    validate_automation_id "$auto_id" || ((failures++))

    # Gate 3: 交易日上下文
    check_trading_context "$prompt" || ((failures++))  # 返回1=跳过非交易日，不算失败

    # Gate 4: memwatch 风险提示（仅记录，不阻断）
    check_memwatch_risk  # always returns 0

    echo "$LOG_PREFIX === Pre-Task Hook 完成 (gates_failed=$failures) ==="

    # 有致命失败 → 返回非零
    [ "$failures" -eq 0 ]
}

# 如果直接执行（非 source）
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    # 传了参数 → 真实校验（实战模式）
    if [ $# -ge 1 ]; then
        run_pre_checks "$@"
        exit $?
    fi

    # 无参数 → 自检模式（硬编码用例）
    echo "=== L3 Pre-Task Hook 自检模式 ==="
    echo "--- 测试 Gate 1: 危险多值 BYHOUR ---"
    check_rrule_safety "FREQ=DAILY;BYHOUR=9,10,11,13,14;BYMINUTE=0" && echo "✅ 通过" || echo "❌ 正确拦截"

    echo "--- 测试 Gate 1: 安全单值 BYHOUR ---"
    check_rrule_safety "FREQ=DAILY;BYHOUR=9;BYMINUTE=0" && echo "✅ 通过" || echo "❌ 误拦"

    echo "--- 测试 Gate 2: ID 格式 ---"
    validate_automation_id "automation-1785506975961" && echo "✅ 通过" || echo "❌ 误拦"
    validate_automation_id "1785506975961" && echo "✅ 通过" || echo "❌ 正确拦截"

    echo "--- 测试 Gate 4: 当前时间风险 ---"
    check_memwatch_risk
fi
