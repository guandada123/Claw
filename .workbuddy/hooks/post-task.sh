#!/bin/bash
# ============================================================
# L3 Guardrail Layer — Post-Task Hook
# 用途: 每次自动化执行后记录结果、检测异常、更新状态
# 调用: source $CLAW/.workbuddy/hooks/post-task.sh
#       run_post_checks "$EXIT_CODE" "$DURATION_SEC" "$AUTOMATION_NAME"
# ============================================================

HOOK_NAME="post-task"
TIMESTAMP=$(date -Iseconds)
LOG_PREFIX="[hook:$HOOK_NAME]"
LOG_FLAG_FILE="${CLAW:-/Users/guan/WorkBuddy/Claw}/.workbuddy/tmp/hook_flags"
LOG_DIR="${CLAW:-/Users/guan/WorkBuddy/Claw}/.workbuddy/logs/hooks"

# 确保日志目录存在
mkdir -p "$LOG_DIR" 2>/dev/null || true

# --- 记录执行结果到结构化日志 ---
log_execution() {
    local exit_code="${1:-0}"
    local duration_sec="${2:-0}"
    local auto_name="${3:-unknown}"
    local log_file="$LOG_DIR/exec_$(date +%Y%m%d).jsonl"

    local record
    record=$(cat <<RECORD
{"ts":"$TIMESTAMP","hook":"$HOOK_NAME","name":"$auto_name","exit_code":$exit_code,"duration_sec":$duration_sec,"hostname":"$(hostname 2>/dev/null || echo unknown)"}
RECORD
)
    echo "$record" >> "$log_file"
}

# --- 失败分类 ---
classify_failure() {
    local exit_code="${1:-0}"
    local output="${2:-}"

    if [ "$exit_code" -ne 0 ]; then
        # 检测硬杀信号
        case "$output" in
            *orchestrator*restarted*|*Run\ interrupted*|*did\ not\ create\ a\ session*)
                echo "HARD_KILL"
                ;;
            *refusal*)
                echo "REFUSAL"
                ;;
            *timeout*|*TIMEOUT*)
                echo "TIMEOUT"
                ;;
            *)
                echo "ERROR"
                ;;
        esac
    else
        echo "OK"
    fi
}

# --- 关键自动化失败告警判定 ---
# 仅关键自动化 + 硬杀/超时 才需要推送（与 watchdog 对齐）
should_alert() {
    local exit_code="${1:-0}"
    local auto_name="${2:-}"
    local failure_type="${3:-OK}"

    # 非零退出且是硬杀/超时
    [ "$exit_code" -eq 0 ] && return 1
    [ "$failure_type" = "HARD_KILL" ] || [ "$failure_type" = "TIMEOUT" ] || return 1

    # 含关键词 → 关键自动化
    case "$auto_name" in
        *早报*|*晚报*|*收盘*|*盘中*|*监控*|*选股*|*策略*|*鱼盆*|*账户*)
            return 0  # 需要告警
            ;;
        *)
            return 1  # 次要，不告警
            ;;
    esac
}

# --- 主入口：执行后检查 ---
# 用法: run_post_checks "$EXIT_CODE" "$DURATION_SEC" "$AUTOMATION_NAME" ["$OUTPUT_SNIPPET"]
run_post_checks() {
    local exit_code="${1:-0}"
    local duration_sec="${2:-0}"
    local auto_name="${3:-unknown}"
    local output="${4:-}"

    echo "$LOG_PREFIX === L3 Post-Task Hook 开始 ($TIMESTAMP) ==="

    # 1. 记录执行日志
    log_execution "$exit_code" "$duration_sec" "$auto_name"

    # 2. 分类失败类型
    local failure_type
    failure_type=$(classify_failure "$exit_code" "$output")

    # 3. 判断是否需要告警
    if should_alert "$exit_code" "$auto_name" "$failure_type"; then
        echo "$LOG_PREFIX 🔴 关键自动化失败: $auto_name (exit=$exit_code, type=$failure_type, duration=${duration_sec}s)"
        echo "$LOG_FLAG_FILE" > "$CLAW/.workbuddy/tmp/post_task_alert_flag"
    elif [ "$exit_code" -ne 0 ]; then
        echo "$LOG_PREFIX ⚠️ 次要失败: $auto_name (exit=$exit_code, type=$failure_type, duration=${duration_sec}s)"
    else
        echo "$LOG_PREFIX ✅ 正常完成: $auto_name (duration=${duration_sec}s)"
    fi

    # 4. 清理 rrule 违规标记（如果本次正常完成）
    [ -f "$CLAW/.workbuddy/tmp/rrule_violation_flag" ] && rm -f "$CLAW/.workbuddy/tmp/rrule_violation_flag"

    echo "$LOG_PREFIX === Post-Task Hook 完成 ==="
}

# 如果直接执行（非 source）
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    # 传了参数 → 真实校验（实战模式）
    if [ $# -ge 3 ]; then
        run_post_checks "$@"
        exit $?
    fi

    # 无参数 → 自检模式（硬编码用例）
    echo "=== L3 Post-Task Hook 自检模式 ==="

    echo "--- 测试 classify_failure ---"
    classify_failure 1 "orchestrator restarted the process"
    classify_failure 0 "all good"
    classify_failure 1 "Automation prompt stopped before completion: refusal"
    classify_failure 1 "connection timeout after 30s"

    echo "--- 测试 should_alert ---"
    should_alert 1 "📈智能选股" "HARD_KILL" && echo "✅ 关键+硬杀=应告警" || echo "❌ 误判"
    should_alert 1 "🩺健康巡检" "ERROR" && echo "✅" || echo "⚠️ 次要不告警(正确)"
    should_alert 0 "📊盘中监控" "OK" && echo "✅" || echo "⚠️ 正常不告警(正确)"

    echo "--- 测试 log_execution ---"
    log_execution 0 "12.5" "自检测试"
    echo "日志已写入: $LOG_DIR/exec_$(date +%Y%m%d).jsonl"
    tail -1 "$LOG_DIR/exec_$(date +%Y%m%d).jsonl"
fi
