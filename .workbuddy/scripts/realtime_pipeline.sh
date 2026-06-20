#!/bin/bash
# ─── 实盘数据管线：Host 端调度 ───
# 调用 Quant 容器内的 live_pipeline.py，将信号 JSON 拉回本地
# 用法: bash realtime_pipeline.sh [--push] [--stocks 002049.SZ,002601.SZ]

PUSH=false
STOCKS=""
AUTO_DETECT=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --push) PUSH=true; shift ;;
    --stocks) STOCKS="$2"; shift 2 ;;
    --auto) AUTO_DETECT=true; shift ;;
    *) echo "未知参数: $1"; exit 1 ;;
  esac
done

# 自动检测：从 Claw 持仓 + 观察列表提取股票代码
if $AUTO_DETECT && [ -z "$STOCKS" ]; then
  STOCKS=$(python3 -c "
import json, os, glob
from pathlib import Path

codes = set()

# 读取模拟盘持仓
for path in [
    Path('/Users/guan/WorkBuddy/Claw/.workbuddy/data/simulation/portfolio.json'),
    Path('/Users/guan/WorkBuddy/Claw/.workbuddy/data/user/portfolio.json'),
]:
    if path.exists():
        try:
            d = json.loads(path.read_text())
            for code in d.get('positions', {}):
                ts_code = f\"{code}.SZ\" if code.startswith('0') or code.startswith('3') else f\"{code}.SH\"
                codes.add(ts_code)
        except: pass

# 读取观察列表
wl_path = Path('/Users/guan/WorkBuddy/Claw/.workbuddy/data/simulation/watchlist.json')
if wl_path.exists():
    try:
        for s in json.loads(wl_path.read_text()):
            code = s['code']
            ts_code = f\"{code}.SZ\" if code.startswith('0') or code.startswith('3') else f\"{code}.SH\"
            codes.add(ts_code)
    except: pass

# 读取用户实盘持仓
user_path = Path('/Users/guan/WorkBuddy/Claw/.workbuddy/data/user/portfolio.json')
if user_path.exists():
    try:
        for code in json.loads(user_path.read_text()).get('positions', {}):
            ts_code = f\"{code}.SZ\" if code.startswith('0') or code.startswith('3') else f\"{code}.SH\"
            codes.add(ts_code)
    except: pass

print(','.join(sorted(codes)))
" 2>/dev/null)
  echo "自动检测股票: $STOCKS"
fi

# 1. 在容器内运行 pipeline
OUTPUT_FILE="/app/output/live_signals_$(date +%Y%m%d_%H%M).json"
LATEST_LINK="/app/output/live_signals_latest.json"

if [ -n "$STOCKS" ]; then
  docker exec quant-strategy python3 /app/scripts/live_pipeline.py \
    --stocks "$STOCKS" --output "$OUTPUT_FILE" 2>&1
else
  docker exec quant-strategy python3 /app/scripts/live_pipeline.py \
    --output "$OUTPUT_FILE" 2>&1
fi

# 失败处理
EXIT_CODE=$?
if [ $EXIT_CODE -ne 0 ]; then
  echo "❌ Pipeline 执行失败 (exit=$EXIT_CODE)"
  exit $EXIT_CODE
fi

# 2. 复制 JSON 到本地
LOCAL_DIR="/Users/guan/WorkBuddy/Claw/output"
mkdir -p "$LOCAL_DIR"
docker cp "quant-strategy:$OUTPUT_FILE" "$LOCAL_DIR/$(basename $OUTPUT_FILE)"
docker exec quant-strategy bash -c "ln -sf $OUTPUT_FILE $LATEST_LINK" 2>/dev/null
echo "✅ 信号已保存: $LOCAL_DIR/$(basename $OUTPUT_FILE)"

# 3. (可选) 推送飞书
if $PUSH; then
  SIGNAL_JSON="$LOCAL_DIR/$(basename $OUTPUT_FILE)"
  
  # 解析信号
  BUY_COUNT=$(python3 -c "import json; d=json.load(open('$SIGNAL_JSON')); print(d['summary']['buy_signals'])" 2>/dev/null || echo "0")
  SELL_COUNT=$(python3 -c "import json; d=json.load(open('$SIGNAL_JSON')); print(d['summary']['sell_signals'])" 2>/dev/null || echo "0")
  HOLD_COUNT=$(python3 -c "import json; d=json.load(open('$SIGNAL_JSON')); print(d['summary']['hold_signals'])" 2>/dev/null || echo "0")
  TRADE_DATE=$(python3 -c "import json; d=json.load(open('$SIGNAL_JSON')); print(d.get('trade_date','?'))" 2>/dev/null || echo "?")

  # 构建飞书消息
  MSG="📊 [Quant 策略信号] $TRADE_DATE\n"
  MSG+="BUY: $BUY_COUNT  |  SELL: $SELL_COUNT  |  HOLD: $HOLD_COUNT\n"
  
  if [ "$BUY_COUNT" -gt 0 ]; then
    MSG+="\n🔴 BUY 信号:\n"
    BUY_STOCKS=$(python3 -c "
import json; d=json.load(open('$SIGNAL_JSON'))
for s in d['buy']:
    print(f\"  {s['ts_code']} {s.get('name','')} (confidence: {s.get('combo_confidence',0)})\")
" 2>/dev/null)
    MSG+="$BUY_STOCKS\n"
  fi
  
  if [ "$SELL_COUNT" -gt 0 ]; then
    MSG+="\n🟢 SELL 信号:\n"
    SELL_STOCKS=$(python3 -c "
import json; d=json.load(open('$SIGNAL_JSON'))
for s in d['sell']:
    print(f\"  {s['ts_code']} {s.get('name','')} (confidence: {s.get('combo_confidence',0)})\")
" 2>/dev/null)
    MSG+="$SELL_STOCKS\n"
  fi

  MSG+="\n---\n"
  MSG+="💡 投资有风险，策略信号仅供参考"

  # 推送到飞书
  /Users/guan/.local/bin/lark-cli im message send \
    --chat-id oc_9ee5303497f5e0e71666b610d6bdc346 \
    --content "$MSG" \
    --msg-type text \
    --as bot 2>&1 || echo "⚠️ 飞书推送失败"
  echo "✅ 飞书推送完成"
fi

echo "✅ 管线执行完毕"
