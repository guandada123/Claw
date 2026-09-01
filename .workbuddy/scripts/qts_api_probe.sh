#!/usr/bin/env bash
# QTS API 探活封装 —— 强制带 X-API-Key
#
# 背景（2026-09-01）：巡检 agent 在回读验证时自拼 curl 探活 QTS 策略服务，
# 未带 X-API-Key → 24h 内产生 201 次 401（14 个接口 × 14 轮）。
# 这批 401 又被巡检中枢新增的 check_qts_api_auth() 读出来，差点被误判为
# "生产调用链漏配 key 导致数据不落库" —— 典型的观测者效应（监控污染被监控对象）。
#
# 用法：
#   qts_api_probe.sh GET  /api/v1/strategies/
#   qts_api_probe.sh POST /api/v1/backtest/run '{"strategy":"vwm"}'
# 输出：HTTP 状态码 + 响应体

set -uo pipefail

CLAW="${CLAW:-/Users/guan/WorkBuddy/Claw}"
BASE="http://127.0.0.1:8000"

# 从 Claw/.env 读 key（环境变量优先）
KEY="${QTS_API_KEY:-}"
if [ -z "$KEY" ]; then
    KEY=$(grep -E '^QTS_API_KEY=' "$CLAW/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'"'"'\r')
fi
if [ -z "$KEY" ]; then
    echo "ERROR: QTS_API_KEY 未配置（Claw/.env 或环境变量）" >&2
    exit 2
fi

METHOD="${1:-GET}"
PATH_="${2:-/health}"
BODY="${3:-}"

ARGS=(-s -o /tmp/qts_probe_body.txt -w '%{http_code}'
      --max-time 15 -H "X-API-Key: ${KEY}" -X "$METHOD")

if [ -n "$BODY" ]; then
    ARGS+=(-H 'Content-Type: application/json' -d "$BODY")
fi

CODE=$(curl "${ARGS[@]}" "${BASE}${PATH_}")
echo "HTTP ${CODE}"
head -c 800 /tmp/qts_probe_body.txt 2>/dev/null
echo
[ "$CODE" = "200" ] || [ "$CODE" = "201" ]
