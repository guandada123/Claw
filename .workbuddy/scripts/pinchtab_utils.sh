#!/usr/bin/env bash
# pinchtab_utils.sh — PinchTab 浏览器自动化包装脚本
# 供自动化 prompt 中调用，节省 Token（~800 Token/页 vs 截图 10000+）
#
# 使用方式（在自动化 prompt 中）：
#   source .workbuddy/scripts/pinchtab_utils.sh
#   pinchtab_nav <url>          # 打开页面并返回可访问性树
#   pinchtab_text <url>         # 提取页面纯文本
#   pinchtab_search <keyword>   # 打开搜索引擎搜索
#
# 如果 PinchTab server 未运行会自动启动（后台）

PINCHTAB_BIN="$HOME/.workbuddy/binaries/pinchtab/pinchtab"
PINCHTAB_PORT=10909
PINCHTAB_URL="http://127.0.0.1:${PINCHTAB_PORT}"
TIMEOUT_SEC=30

# 检查 PinchTab server 是否运行，未运行则启动
_pinchtab_ensure_server() {
  if ! curl -sf "${PINCHTAB_URL}/health" > /dev/null 2>&1; then
    echo "[pinchtab] 启动 server (port ${PINCHTAB_PORT})..." >&2
    nohup "$PINCHTAB_BIN" server --port "$PINCHTAB_PORT" > /tmp/pinchtab-server.log 2>&1 &
    local waited=0
    while [ $waited -lt $TIMEOUT_SEC ]; do
      if curl -sf "${PINCHTAB_URL}/health" > /dev/null 2>&1; then
        echo "[pinchtab] server 已就绪" >&2
        return 0
      fi
      sleep 1
      waited=$((waited + 1))
    done
    echo "[pinchtab] 启动超时 (${TIMEOUT_SEC}s)" >&2
    return 1
  fi
  return 0
}

# 导航到 URL 并返回可访问性树快照
pinchtab_nav() {
  local url="$1"
  if [ -z "$url" ]; then
    echo "用法: pinchtab_nav <url>" >&2
    return 1
  fi
  _pinchtab_ensure_server || return 1
  "$PINCHTAB_BIN" --server "$PINCHTAB_URL" nav "$url" --snap 2>/dev/null
  local rc=$?
  echo "[pinchtab] nav done: $url (exit=$rc)" >&2
  return $rc
}

# 提取页面纯文本
pinchtab_text() {
  local url="$1"
  if [ -z "$url" ]; then
    echo "用法: pinchtab_text <url>" >&2
    return 1
  fi
  _pinchtab_ensure_server || return 1
  "$PINCHTAB_BIN" --server "$PINCHTAB_URL" nav "$url" > /dev/null 2>&1
  "$PINCHTAB_BIN" --server "$PINCHTAB_URL" text 2>/dev/null
  local rc=$?
  echo "[pinchtab] text done: $url (exit=$rc)" >&2
  return $rc
}

# 搜索关键词（默认用百度）
pinchtab_search() {
  local keyword="$1"
  if [ -z "$keyword" ]; then
    echo "用法: pinchtab_search <keyword>" >&2
    return 1
  fi
  local encoded
  encoded=$(python3 -c "import urllib.parse, sys; print(urllib.parse.quote(sys.argv[1]))" "$keyword")
  pinchtab_nav "https://www.baidu.com/s?wd=${encoded}"
}

# 关闭浏览器并释放资源
pinchtab_close() {
  _pinchtab_ensure_server 2>/dev/null || return 0
  "$PINCHTAB_BIN" --server "$PINCHTAB_URL" close 2>/dev/null
  echo "[pinchtab] 已关闭" >&2
}

# 检查 PinchTab 是否可用
pinchtab_check() {
  if [ ! -x "$PINCHTAB_BIN" ]; then
    echo "[pinchtab] ❌ 未安装 (bin not found at $PINCHTAB_BIN)" >&2
    return 1
  fi
  echo "[pinchtab] ✅ 二进制文件就绪" >&2
  if curl -sf "${PINCHTAB_URL}/health" > /dev/null 2>&1; then
    echo "[pinchtab] ✅ server 运行中 (port ${PINCHTAB_PORT})" >&2
  else
    echo "[pinchtab] ⚠️ server 未运行（按需自动启动）" >&2
  fi
  return 0
}
