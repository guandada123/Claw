#!/bin/bash
# push_feishu.sh - 推送到飞书群的封装脚本
# 用法: bash push_feishu.sh "标题" "正文markdown"

TITLE="$1"
CONTENT="$2"
CHAT_ID="oc_9ee5303497f5e0e71666b610d6bdc346"

if [ -z "$TITLE" ] && [ -z "$CONTENT" ]; then
    echo "用法: bash push_feishu.sh '标题' '正文'"
    exit 1
fi

# 如果只传了一个参数，当作完整消息
if [ -z "$CONTENT" ]; then
    MSG="$TITLE"
else
    MSG="${TITLE}\n${CONTENT}"
fi

lark-cli im +messages-send \
  --as bot \
  --chat-id "${CHAT_ID}" \
  --markdown "${MSG}" 2>&1

echo "exit_code: $?"
