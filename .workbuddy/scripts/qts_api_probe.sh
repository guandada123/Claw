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
#   QTS_PROBE_TIMEOUT=120 qts_api_probe.sh GET /api/v1/stocks/index/realtime
# 输出：HTTP 状态码 + 响应体
#
# ⚠️ 超时（2026-09-01 run#58 实犯）：默认 --max-time 15s，而「指数行情」类端点
#    在后端数据源故障时可能耗时 19s+ → curl 客户端先超时，返回 **HTTP 000**，
#    此时 /tmp/qts_probe_body.txt 不会被覆盖，仍留着上一次的响应体。
#    表现是「HTTP 000 + 打印出一个完全不相关的旧响应」，极易误判为服务挂了。
#    慢端点请先放大 QTS_PROBE_TIMEOUT。
#
# ⚠️ 取 key 别自己拼 shell 管道（2026-09-01 实犯）：手写
#    `grep ... | cut -d= -f2- | tr -d '"'\''\r'` 引号转义复杂，实测漏掉 1 个字符
#    （23 → 22）→ 直接 401。本脚本内部的提取逻辑是验证过的，直接用它。
#    需要裸 key 时用 python 解析 .env，别用 sed/tr 管道。

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

# 慢端点（如指数行情，后端数据源故障时可到 19s+）需放大超时，见文件头说明
TIMEOUT="${QTS_PROBE_TIMEOUT:-15}"

ARGS=(-s -o /tmp/qts_probe_body.txt -w '%{http_code}'
      --max-time "$TIMEOUT" -H "X-API-Key: ${KEY}" -X "$METHOD")

if [ -n "$BODY" ]; then
    ARGS+=(-H 'Content-Type: application/json' -d "$BODY")
fi

CODE=$(curl "${ARGS[@]}" "${BASE}${PATH_}")
echo "HTTP ${CODE}"
head -c 800 /tmp/qts_probe_body.txt 2>/dev/null
echo
[ "$CODE" = "200" ] || [ "$CODE" = "201" ]
