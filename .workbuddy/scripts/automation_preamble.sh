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
# 08-05 根治：支持 --interval-hours N → 转发 schedule_utils.py 真槽位锁。
#   背景：6h 间隔自动化(FREQ=HOURLY;INTERVAL=6)用每日锁会被 stale 锁屏蔽后续槽位
#   （8/2 日报静默丢失根因）。interval>0 时按 [当前小时//间隔] 槽位独立去重。
check_schedule() {
    local name interval_hours
    name="$1"
    shift
    interval_hours=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --interval-hours) interval_hours="$2"; shift 2 ;;
            *) shift ;;
        esac
    done
    if [ "${interval_hours:-0}" -gt 0 ] 2>/dev/null; then
        cd "$CLAW" && python3 .workbuddy/scripts/schedule_utils.py check --name "$name" --interval-hours "$interval_hours"
        return $?
    fi
    local today lockfile
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
    local name interval_hours
    name="$1"
    shift
    interval_hours=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --interval-hours) interval_hours="$2"; shift 2 ;;
            *) shift ;;
        esac
    done
    if [ "${interval_hours:-0}" -gt 0 ] 2>/dev/null; then
        cd "$CLAW" && python3 .workbuddy/scripts/schedule_utils.py done --name "$name" --interval-hours "$interval_hours"
        cd "$CLAW" && python3 scripts/cost_tracker.py log_estimate "$name" 2>/dev/null
        return 0
    fi
    local today
    today=$(date +%Y%m%d)
    touch "/tmp/claw_lock_${name}_${today}"
    cd $CLAW && python3 scripts/cost_tracker.py log_estimate "$name" 2>/dev/null
}

# 交易日检查
check_trading_day() {
    cd $CLAW && python3 scripts/is_trading_day.py
    return $?
}

# ============================================================
# LLM 本地代理保活（2026-07-31 新增，根治 #74/#78 故障）
# ============================================================
# 根因：com.workbuddy.proxy-deepseek 与 proxy-watchdog 两个 launchd 作业
#       可能被 WorkBuddy/Marvis 内存守卫重启时连带 unload，看门狗自身也被摘
#       → 自愈链断裂，代理 :9999 失联，所有走 router 的自动化命中错误 key → 401。
# 治本：把自愈下沉到每次自动化的入口——source 本 preamble 即探测 :9999，
#       DOWN 则自动 launchctl load -w 恢复。比 watchdog 更可靠（watchdog 也会死，
#       而本入口每次执行都跑）。正常情况 :9999 已监听 → 直接返回，零开销零副作用。
# 注意：恢复失败也 return 0（不阻断自动化），由后续 LLM 调用暴露问题，避免误杀任务。
ensure_proxy() {
    local plist_deepseek="$HOME/Library/LaunchAgents/com.workbuddy.proxy-deepseek.plist"
    local plist_watchdog="$HOME/Library/LaunchAgents/com.workbuddy.proxy-watchdog.plist"
    # 无代理配置则跳过（不影响非 LLM 自动化）
    [ -f "$plist_deepseek" ] || return 0
    # 已监听则无需动作
    nc -z 127.0.0.1 9999 2>/dev/null && return 0

    echo "[preamble] ⚠️ LLM 代理 :9999 未监听，尝试自动恢复..." >&2
    launchctl load -w "$plist_deepseek" 2>/dev/null
    [ -f "$plist_watchdog" ] && launchctl load -w "$plist_watchdog" 2>/dev/null
    sleep 2
    if nc -z 127.0.0.1 9999 2>/dev/null; then
        echo "[preamble] ✅ LLM 代理已自动恢复 (:9999)" >&2
    else
        echo "[preamble] ❌ LLM 代理 :9999 仍不可达，自动化可能命中 401（请检查代理进程）" >&2
    fi
    return 0
}

# ============================================================
# 🔴 stateful backoff 统一入口（08-04 T3 落地）
# 读取全局状态锚 ~/.workbuddy/cross_project_state.json 的 backoff_state，
# 供外部 API 依赖类自动化（anysearch/微信RSS/tushare/Wind等）判断是否处于退避期。
# 用法（source preamble 后）:
#   check_backoff <identity>        # 退避中 → echo 剩余分钟+return 1；可执行 → return 0
#   record_backoff_fail <identity>  # 记录一次连续失败（幂等，供状态锚更新）
# 规则（对齐 long-running-agent-control-plane skill）：
#   同 identity 连续失败共享退避 15→30→60→120min；任一 todo/gate/evidence 变化即重置。
# ============================================================
_STATE_ANCHOR="$HOME/.workbuddy/cross_project_state.json"

check_backoff() {
    local identity="$1"
    [ -f "$_STATE_ANCHOR" ] || return 0
    local last_fail now_ts interval_min elapsed
    last_fail=$("$PYTHON" - "$identity" <<'PYEOF' 2>/dev/null || echo ""
import json, os, sys, time
identity = sys.argv[1]
p = os.path.expanduser("~/.workbuddy/cross_project_state.json")
try:
    d = json.load(open(p))
    bs = d.get("backoff_state", {}).get("shared_identities", {}).get(identity, {})
    lf = bs.get("last_fail")
    iv = bs.get("current_interval_min", 30)
    print(f"{lf or ''}|{iv}")
except Exception:
    pass
PYEOF
)
    [ -n "$last_fail" ] || return 0
    local lf="${last_fail%%|*}" iv="${last_fail##*|}"
    [ -n "$lf" ] && [ "$lf" != "null" ] || return 0
    now_ts=$(date +%s)
    # last_fail 形如 2026-08-04T07:45+08:00（ISO）——取日期+时间转 epoch
    elapsed=$("$PYTHON" - "$lf" <<'PYEOF' 2>/dev/null || echo 99999
import sys, datetime
s = sys.argv[1].strip()
try:
    dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    print(int(datetime.datetime.now().timestamp() - dt.timestamp()))
except Exception:
    print(99999)
PYEOF
)
    if [ "$elapsed" -lt "$((iv * 60))" ]; then
        echo "[backoff] ⚠️ $identity 处于退避期(剩 $((iv * 60 - elapsed))s)，跳过本次执行"
        return 1
    fi
    return 0
}

# 记录一次退避失败（写回状态锚，幂等）。identity + 可选 note
record_backoff_fail() {
    local identity="$1"
    [ -f "$_STATE_ANCHOR" ] || return 0
    "$PYTHON" - "$identity" <<'PYEOF' 2>/dev/null || true
import json, os, sys, datetime
identity = sys.argv[1]
p = os.path.expanduser("~/.workbuddy/cross_project_state.json")
try:
    with open(p) as f:
        d = json.load(f)
    bs = d.setdefault("backoff_state", {}).setdefault("shared_identities", {}).setdefault(identity, {})
    cf = bs.get("consecutive_failures", 0) + 1
    iv = bs.get("current_interval_min", 30)
    # 退避阶梯: 15→30→60→120（上限240）
    step = [15, 30, 60, 120, 240]
    idx = min(cf - 1, len(step) - 1)
    bs["consecutive_failures"] = cf
    bs["current_interval_min"] = step[idx]
    bs["last_fail"] = datetime.datetime.now().astimezone().isoformat(timespec="minutes")
    d["updated_at"] = datetime.datetime.now().astimezone().isoformat(timespec="minutes")
    with open(p, "w") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    print(f"[backoff] {identity} 连续失败 {cf} 次 → 退避 {step[idx]}min")
except Exception:
    pass
PYEOF
}
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

# ============================================================
# L3 Guardrail Hooks 集成（08-07 五层架构落地）
# 封装 pre/post-task hook 调用，让所有 source 本 preamble 的自动化一键接入护栏层。
# 用法：
#   run_l3_pre "$RRULE" "$AUTOMATION_ID" "$PROMPT_TEXT"   # 自动化前：RRULE/ID/交易日/memwatch 校验
#   run_l3_post "$EXIT_CODE" "$DURATION_SEC" "$AUTO_NAME" ["$OUTPUT"]  # 自动化后：失败分类+日志+告警判定
# 失败不阻断自动化（pre 返回非零仅告警，post 始终 return 0），由外部决策是否 abort。
# ============================================================
HOOKS_DIR="$CLAW/.workbuddy/hooks"

run_l3_pre() {
    [ -f "$HOOKS_DIR/pre-task.sh" ] || return 0
    bash "$HOOKS_DIR/pre-task.sh" "$@" 2>&1 || true
}

run_l3_post() {
    [ -f "$HOOKS_DIR/post-task.sh" ] || return 0
    bash "$HOOKS_DIR/post-task.sh" "$@" 2>&1 || true
}

# ============================================================
# LLM 本地代理保活：每次 source 本 preamble 自动探测并自愈（见 ensure_proxy 定义）
# 放在末尾确保函数已定义。失败不阻断（return 0）。
# ============================================================
ensure_proxy 2>/dev/null || true
