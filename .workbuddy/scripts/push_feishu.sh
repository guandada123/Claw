#!/bin/bash
# push_feishu.sh - 推送到飞书群的封装脚本
# 用法: bash push_feishu.sh "标题" "正文markdown"
#
# v2 (2026-07-17): 委托 push_card.py 发 interactive 卡片，
# 旧纯 --markdown 推送统一升级为「语义配色 + 分区」卡片。
# 向后兼容：调用方不改，自动获得卡片化效果。
#
# 可选环境变量（调用方无需改，仅高级场景用）：
#   PUSH_LEVEL  = alert|warning|info|success  (默认 info)
#   PUSH_CHAT_ID = 目标群 (默认从 FEISHU_CHAT_ID 或 push_card 内置默认)

TITLE="$1"
CONTENT="$2"
CHAT_ID="${FEISHU_CHAT_ID:-}"
LEVEL="${PUSH_LEVEL:-info}"

if [ -z "$TITLE" ] && [ -z "$CONTENT" ]; then
    echo "用法: bash push_feishu.sh '标题' '正文'"
    exit 1
fi

# 拼装正文：单参数当完整消息；双参数拼 TITLE + CONTENT
if [ -z "$CONTENT" ]; then
    BODY="$TITLE"
else
    BODY="${TITLE}\n${CONTENT}"
fi

# 委托 push_card.py（卡片失败会自动 markdown 兜底，绝不用 --text 丢格式）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-/Users/guan/.workbuddy/binaries/python/envs/default/bin/python}"

ARGS=("$PYTHON" "$SCRIPT_DIR/push_card.py" --title "$TITLE" --level "$LEVEL" --section "" "$BODY")
if [ -n "$CHAT_ID" ]; then
    ARGS+=(--chat-id "$CHAT_ID")
fi

"${ARGS[@]}" 2>&1
echo "exit_code: $?"
