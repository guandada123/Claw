#!/bin/bash
# ============================================================
# WorkBuddy 自动化公共 preamble
# 用途: 所有自动化统一使用的快捷变量定义
# 使用: source $CLAW/.workbuddy/scripts/automation_preamble.sh
# ============================================================

# --- 路径变量 ---
export PYTHON=/Users/guan/.workbuddy/binaries/python/envs/default/bin/python
export SCRIPTS=/Users/guan/WorkBuddy/Claw/.workbuddy/scripts
export CLAW=/Users/guan/WorkBuddy/Claw

# --- 数据路径 ---
export USER_DATA=$CLAW/.workbuddy/data/user/portfolio.json
export SIM_DATA=$CLAW/.workbuddy/data/simulation/portfolio.json
export PORTFOLIO=$CLAW/.workbuddy/data/portfolio.json
export STOCK_POOL=$CLAW/.workbuddy/data/stock_pool.json
export STRATEGY_LIB=$CLAW/.workbuddy/data/simulation/strategy_library.json
export EXP_DIR=$CLAW/.workbuddy/experiments

# --- 飞书配置 ---
export FEISHU_CHAT=oc_9ee5303497f5e0e71666b610d6bdc346

# --- 工具函数 ---

# 幂等锁（文件级，避免同一天重复执行）
check_schedule() {
    local name="$1"
    local today=$(date +%Y%m%d)
    local lockfile="/tmp/claw_lock_${name}_${today}"
    if [ -f "$lockfile" ]; then
        echo "🔒 今日已执行: $name"
        return 1
    fi
    return 0
}

# 调度完成标记 + 成本记录
done_schedule() {
    local name="$1"
    local today=$(date +%Y%m%d)
    touch "/tmp/claw_lock_${name}_${today}"
    cd $CLAW && python3 scripts/cost_tracker.py log_estimate "$name" 2>/dev/null
}

# 交易日检查
check_trading_day() {
    cd $CLAW && python3 scripts/is_trading_day.py
    return $?
}

# 飞书推送（基于 push_feishu.sh 封装，兼容新旧两种调用方式）
# 新用法: push_feishu "标题" "内容"
# 旧用法: push_feishu "event" "message" "dedup-key" [cooldown] — 自动兼容
push_feishu() {
    export FEISHU_CHAT_ID="$FEISHU_CHAT"
    if [ $# -ge 2 ]; then
        bash $SCRIPTS/push_feishu.sh "$1" "$2"
    else
        bash $SCRIPTS/push_feishu.sh "WorkBuddy通知" "$1"
    fi
}

echo "[preamble] 公共变量已加载: CLAW=$CLAW SCRIPTS=$SCRIPTS"

# --- 新公共脚本快捷引用（P3 新增） ---

# 鱼盆数据（替换内联 bash 解析）
read_yupen() {
    $PYTHON $SCRIPTS/read_yupen_data.py "$@"
}

# 美股市场数据（替换 curl + WebSearch）
fetch_us_market() {
    $PYTHON $SCRIPTS/fetch_us_market.py "$@"
}

# A股约束校验（替换内联整手规则声明）
check_constraints() {
    $PYTHON $SCRIPTS/validate_constraints.py "$@"
}
