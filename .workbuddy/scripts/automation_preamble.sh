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
    local name today lockfile
    name="$1"
    today=$(date +%Y%m%d)
    lockfile="/tmp/claw_lock_${name}_${today}"
    if [ -f "$lockfile" ]; then
        echo "🔒 今日已执行: $name"
        return 1
    fi
    return 0
}

# 调度完成标记 + 成本记录
done_schedule() {
    local name today
    name="$1"
    today=$(date +%Y%m%d)
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

# ============================================================
# F8: 安全清理包装器（dry-run 先行 + 禁止吞错 + 白名单）
# ============================================================
# 替换裸 `rm -rf 2>/dev/null`。
# - 仅允许白名单前缀内操作（/tmp/workbuddy/ + 项目 .workbuddy 临时目录）
# - 自动拒绝危险路径（/、/*、$HOME、$HOME/*、空、含 .. 的相对路径）
# - 解析符号链接；realpath 不在白名单 → 拒绝
# - 默认 dry-run；传 "--confirm" 才执行实际删除
# 用法: safe_cleanup [--confirm] [target_dir]
safe_cleanup() {
    local confirm=0
    local target="${1:-$CLAW}"
    if [ "$1" = "--confirm" ]; then
        confirm=1
        target="${2:-$CLAW}"
    fi

    # --- 危险路径白名单 ---
    local allowed_prefixes=("/tmp/workbuddy/" "/private/tmp/workbuddy/" "$CLAW/.workbuddy/tmp/" "$CLAW/.workbuddy/data/")
    local real
    real=$(python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$target" 2>/dev/null) || {
        echo "[safe_cleanup] ❌ 无法解析路径: $target"
        return 2
    }

    # 拒绝显式危险模式（在 realpath 之前）
    # shellcheck disable=SC2088  # case 中的 ~ 作为字面量比较，不展开
    case "$target" in
        ""|"/"|"/*"|"$HOME"|"$HOME"/*|"~"|"~/"*)
            echo "[safe_cleanup] ❌ REFUSE: 危险路径模式 '$target'"
            return 2
            ;;
    esac
    # 拒绝含 .. 的相对路径
    case "$target" in
        ../*|*/../*|*"/..")
            echo "[safe_cleanup] ❌ REFUSE: 相对路径含 '..' — '$target'"
            return 2
            ;;
    esac

    # realpath 是否在白名单前缀内
    local ok=0
    for prefix in "${allowed_prefixes[@]}"; do
        case "$real/" in
            "$prefix"*) ok=1; break ;;
        esac
    done
    if [ "$ok" -eq 0 ]; then
        echo "[safe_cleanup] ❌ REFUSE: 路径 '$real' 不在白名单内"
        echo "[safe_cleanup]    允许的前缀: ${allowed_prefixes[*]}"
        return 3
    fi

    # --- 实际操作 ---
    echo "[safe_cleanup] 预览: $target (real=$real)"
    local pyc_count
    pyc_count=$(find "$real" -name "__pycache__" -type d 2>/dev/null | wc -l | tr -d ' ')
    if [ "$pyc_count" -gt 0 ]; then
        echo "[safe_cleanup] 将清理 $pyc_count 个 __pycache__ 目录:"
        find "$real" -name "__pycache__" -type d -print 2>/dev/null
        if [ "$confirm" -eq 1 ]; then
            find "$real" -name "__pycache__" -type d -exec rm -rf {} +
            echo "[safe_cleanup] ✅ __pycache__ 清理完成 ($pyc_count 个)"
        else
            echo "[safe_cleanup] 🔍 [DRY-RUN] 以上目录会被删除（加 --confirm 执行）"
        fi
    fi

    local pyc_file_count
    pyc_file_count=$(find "$real" -name "*.pyc" -type f 2>/dev/null | wc -l | tr -d ' ')
    if [ "$pyc_file_count" -gt 0 ]; then
        if [ "$confirm" -eq 1 ]; then
            find "$real" -name "*.pyc" -type f -delete
            echo "[safe_cleanup] ✅ .pyc 文件清理完成 ($pyc_file_count 个)"
        else
            echo "[safe_cleanup] 🔍 [DRY-RUN] $pyc_file_count 个 .pyc 文件会被删除（加 --confirm 执行）"
        fi
    fi
}

# Docker 安全清理（白名单模式，非 -f 全清）
safe_docker_prune() {
    echo "[safe_docker_prune] 清理悬空镜像（不含正在使用的）"
    docker image prune -f --filter "until=24h" 2>&1
    echo "[safe_docker_prune] ✅ 完成"
}

# ============================================================
# F9: 运行中互斥锁（进程级，崩溃自动释放 + 僵尸锁回收）
# ============================================================
# 替代 check_schedule（日归档锁）用于需要防并发的场景。
# 用 mkdir 实现原子操作，trap EXIT 确保崩溃不泄漏。
#
# 僵尸锁回收：锁内记录 pid+start_time；若超 TTL（默认 2h）且进程已死，
# 则带审计日志强制回收，避免长期饥饿。
#
# 用法: mutex_lock "名称" || exit 0
mutex_lock() {
    local name="$1"
    local lockdir="/tmp/claw_mutex_${name}"
    local ttl_seconds=7200  # 2h TTL
    local host
    host=$(hostname 2>/dev/null || echo "unknown")

    if mkdir "$lockdir" 2>/dev/null; then
        # 成功获取锁 → 持久化 pid + start_time
        echo "$$" > "$lockdir/pid"
        date +%s > "$lockdir/started"
        echo "$host" > "$lockdir/host"
        date -Iseconds > "$lockdir/acquired_at"
        # 注册退出时自动释放（崩溃/SIGTERM/正常退出均触发）
        # shellcheck disable=SC2064  # lockdir 须在 trap 设置时展开（local 变量），非 signal 时
        trap "rm -rf '$lockdir' 2>/dev/null" EXIT
        return 0
    fi

    # --- 锁已存在 → 检查是否为僵尸锁 ---
    local lock_pid lock_started
    lock_pid=$(cat "$lockdir/pid" 2>/dev/null)
    lock_started=$(cat "$lockdir/started" 2>/dev/null)

    if [ -n "$lock_pid" ] && [ -n "$lock_started" ]; then
        local now_ts age
        now_ts=$(date +%s)
        age=$((now_ts - lock_started))

        # 进程不存在且超过 TTL → 僵尸锁，带审计回收
        if ! kill -0 "$lock_pid" 2>/dev/null && [ "$age" -gt "$ttl_seconds" ]; then
            echo "[mutex_lock] ⚠️ 僵尸锁回收: name='$name' pid=$lock_pid age=${age}s host=$(cat "$lockdir/host" 2>/dev/null) acquired=$(cat "$lockdir/acquired_at" 2>/dev/null)"
            echo "[mutex_lock]    原因: 进程已不存在且超过 TTL (${ttl_seconds}s)，强制回收"
            rm -rf "$lockdir"
            # 回收后重试获取
            if mkdir "$lockdir" 2>/dev/null; then
                echo "$$" > "$lockdir/pid"
                echo "$now_ts" > "$lockdir/started"
                echo "$host" > "$lockdir/host"
                date -Iseconds > "$lockdir/acquired_at"
                # shellcheck disable=SC2064
                trap "rm -rf '$lockdir' 2>/dev/null" EXIT
                return 0
            fi
        fi
    fi

    echo "🔒 另一个实例正在运行: $name (锁: $lockdir, pid=$lock_pid, age=${age:-?}s)"
    return 1
}

mutex_unlock() {
    local name="$1"
    local lockdir="/tmp/claw_mutex_${name}"
    rm -rf "$lockdir" 2>/dev/null
    # 清除 EXIT trap（可选，正常退出后不再需要）
    trap - EXIT
}
