#!/bin/bash
# ============================================================
# 盘中问答 — 飞书事件监听 daemon
# 功能：管理 lark-cli event consume 生命周期
# 用法：qa_daemon.sh {ensure|start <session>|stop|status}
# ============================================================
set -e

QA_DIR="/tmp/qa_incoming"
PID_FILE="/tmp/qa_daemon.pid"
GROUP_ID="${FEISHU_CHAT_ID:-}"

# --------------- helpers ---------------
get_timeout() {
  case "$1" in
    morning)   echo "150m" ;;   # 09:00-11:30 ≈ 2.5h
    afternoon) echo "115m" ;;   # 13:00-14:55 ≈ 115min
    test)      echo "30s" ;;
    *)         echo "150m" ;;
  esac
}

get_session() {
  local hour min dow
  hour=$(date +%H)
  min=$(date +%M)
  dow=$(date +%u)  # 1=Mon, 7=Sun

  [ "$dow" -gt 5 ] && { echo "weekend"; return 1; }

  if   [ "$hour" -ge 9 ] && { [ "$hour" -lt 11 ] || { [ "$hour" -eq 11 ] && [ "$min" -le 30 ]; }; }; then
    echo "morning"; return 0
  elif [ "$hour" -ge 13 ] && { [ "$hour" -lt 14 ] || { [ "$hour" -eq 14 ] && [ "$min" -le 55 ]; }; }; then
    echo "afternoon"; return 0
  else
    echo "off_hours"; return 1
  fi
}

# --------------- commands ---------------
start_daemon() {
  local session="${1:-morning}"
  local timeout
  timeout=$(get_timeout "$session")

  mkdir -p "$QA_DIR"
  rm -f "$QA_DIR"/*.json 2>/dev/null || true

  # 启动事件消费 daemon，只监听目标群
  # --output-dir 将每个事件写为一个文件
  lark-cli event consume im.message.receive_v1 --as bot \
    --timeout "$timeout" \
    --output-dir "$QA_DIR" \
    --jq "select(.chat_type==\"group\" and .chat_id==\"$GROUP_ID\" and .message_type==\"text\")" \
    < <(tail -f /dev/null) &

  local pid=$!
  echo "$pid" > "$PID_FILE"
  echo "daemon_started pid=$pid session=$session timeout=$timeout"
}

stop_daemon() {
  if [ -f "$PID_FILE" ]; then
    local pid
    pid=$(cat "$PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true    # SIGTERM — 安全断开订阅
      sleep 1
      kill -0 "$pid" 2>/dev/null && kill "$pid" 2>/dev/null || true
      echo "daemon_stopped pid=$pid"
    fi
    rm -f "$PID_FILE"
    echo "daemon_stopped"
  else
    echo "daemon_not_running"
  fi
}

status_daemon() {
  if [ -f "$PID_FILE" ]; then
    local pid
    pid=$(cat "$PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
      echo "running pid=$pid"
      return 0
    fi
    rm -f "$PID_FILE"
  fi
  echo "stopped"
  return 1
}

ensure_daemon() {
  local session
  session=$(get_session) || { echo "skip reason=$session"; return 1; }

  if [ "$(status_daemon)" = "stopped" ]; then
    start_daemon "$session"
  else
    echo "daemon_already_running"
  fi
}

# --------------- main ---------------
case "${1:-ensure}" in
  start)    start_daemon "${2:-morning}" ;;
  stop)     stop_daemon ;;
  status)   status_daemon ;;
  ensure)   ensure_daemon ;;
  *)        echo "Usage: $0 {ensure|start <session>|stop|status}" >&2; exit 1 ;;
esac
