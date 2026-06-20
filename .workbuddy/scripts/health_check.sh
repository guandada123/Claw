#!/bin/bash
# ─── 全系统统一健康检查 ───
# 检查所有运行中服务的健康状态，输出 JSON
# 用法: bash health_check.sh [--json] [--verbose]

OUTPUT_DIR="/Users/guan/WorkBuddy/Claw/output"
TIMESTAMP=$(date +%s)
DATE_STR=$(date +%Y-%m-%d)
TIME_STR=$(date +%H:%M:%S)
VERBOSE=false
JSON_OUTPUT=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --json) JSON_OUTPUT=true; shift ;;
    --verbose) VERBOSE=true; shift ;;
    *) echo "未知参数: $1"; exit 1 ;;
  esac
done

# ─── Docker Containers ───
DOCKER_CHECKS=""
DOCKER_HEALTHY=0
DOCKER_TOTAL=0
ALL_DOCKER_HEALTHY=true

while IFS='|' read -r name status ports; do
  DOCKER_TOTAL=$((DOCKER_TOTAL + 1))
  up=false
  case "$status" in
    *Up*) up=true ; DOCKER_HEALTHY=$((DOCKER_HEALTHY + 1)) ;;
    *) ALL_DOCKER_HEALTHY=false ;;
  esac
  health="healthy"
  $up || health="unhealthy"
  $ALL_DOCKER_HEALTHY || true
  if $VERBOSE || ! $up; then
    DOCKER_CHECKS+="  $name → $health\n"
  fi
  DOCKER_CHECKS+=","
done < <(docker ps --format "{{.Names}}|{{.Status}}|{{.Ports}}" 2>/dev/null | sort)

DOCKER_CHECKS="${DOCKER_CHECKS%,}"
[ $DOCKER_TOTAL -eq 0 ] && ALL_DOCKER_HEALTHY=false && DOCKER_CHECKS="  无运行容器"

# ─── HTTP Endpoints ───
declare -A HTTP_CHECKS=(
  ["PMF 监控"]="http://localhost:8000/login"
  ["Quant Dashboard"]="http://localhost:3000"
  ["we-mp-rss"]="http://localhost:18001"
  ["Quant Strategy API"]="http://localhost:8000/health"
)

HTTP_PASS=0
HTTP_TOTAL=0
HTTP_RESULTS=""
for name in "${!HTTP_CHECKS[@]}"; do
  url="${HTTP_CHECKS[$name]}"
  HTTP_TOTAL=$((HTTP_TOTAL + 1))
  code=$(curl -sL -o /dev/null -w "%{http_code}" --connect-timeout 5 --max-time 10 "$url" 2>/dev/null || echo "000")
  if [ "$code" != "000" ] && [ "$code" -lt 500 ]; then
    HTTP_PASS=$((HTTP_PASS + 1))
    HTTP_RESULTS+="  $name: 🟢 $code\n"
  else
    HTTP_RESULTS+="  $name: 🔴 $code\n"
  fi
done

# ─── Tailscale ───
TAILSCALE_STATUS="unknown"
TAILSCALE_IP=""
TAILSCALE_DEVICES=0
if command -v tailscale &>/dev/null; then
  ts_out=$(tailscale --socket=/Users/guan/Library/Caches/tailscale/tailscaled.sock status 2>/dev/null)
  if echo "$ts_out" | grep -q "gui"; then
    TAILSCALE_STATUS="not_authenticated"
  elif echo "$ts_out" | grep -q "100\."; then
    TAILSCALE_STATUS="connected"
    TAILSCALE_IP=$(echo "$ts_out" | head -1 | awk '{print $1}')
    TAILSCALE_DEVICES=$(echo "$ts_out" | wc -l | tr -d ' ')
  else
    TAILSCALE_STATUS="stopped"
  fi
fi

# ─── Marvis Bridge ───
BRIDGE_STATUS="unknown"
BRIDGE_PENDING=0
BRIDGE_WATCHERS=0
if [ -f "/Users/guan/workbuddy_marvis_bridge/status/bridge.json" ]; then
  BRIDGE_DATA=$(cat /Users/guan/workbuddy_marvis_bridge/status/bridge.json 2>/dev/null)
  BRIDGE_STATUS=$(echo "$BRIDGE_DATA" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('mode','unknown'))" 2>/dev/null)
fi
BRIDGE_WATCHERS=$(ps aux | grep -cE "[f]ile_watcher|[b]ridge_monitor|[w]orkbuddy_poller|[f]swatch" 2>/dev/null)

# ─── Disk & System ───
DISK_USAGE=$(df -h / | awk 'NR==2{print $3"/"$2" ("$5")"}')
UPTIME=$(uptime | sed 's/.*up //' | sed 's/,.*//')
LOAD_AVG=$(uptime | awk -F'load averages:' '{print $2}' | xargs)
MEM_TOTAL=$(sysctl -n hw.memsize 2>/dev/null | awk '{printf "%.0f", $1/1024/1024/1024}')
MEM_USED=$(vm_stat 2>/dev/null | awk '/Pages active/{a=$NF} /Pages wired/{w=$NF} /Pages occupied/{o=$NF} END{printf "%.1f", (a+w+o)*16384/1024/1024/1024}')
MEM_PCT=$(awk "BEGIN {printf \"%.0f\", ($MEM_USED/$MEM_TOTAL)*100}" 2>/dev/null)

# ─── WorkBuddy Automations ───
AUTOMATION_TOTAL=0
AUTOMATION_FAILURES=0
if [ -f "$HOME/.workbuddy/workbuddy.db" ]; then
  AUTOMATION_DATA=$(python3 -c "
import sqlite3, json
conn = sqlite3.connect('$HOME/.workbuddy/workbuddy.db')
c = conn.cursor()
c.execute('SELECT COUNT(*) FROM automations WHERE status=\"ACTIVE\"')
total = c.fetchone()[0]
c.execute('''
  SELECT COUNT(*) FROM automation_runs 
  WHERE status=\"failed\" AND created_at > datetime(\"now\", \"-24 hours\")
''')
failures = c.fetchone()[0]
print(json.dumps({'total': total, 'failures_24h': failures}))
conn.close()
" 2>/dev/null)
  AUTOMATION_TOTAL=$(echo "$AUTOMATION_DATA" | python3 -c "import json,sys; print(json.load(sys.stdin)['total'])" 2>/dev/null || echo "0")
  AUTOMATION_FAILURES=$(echo "$AUTOMATION_DATA" | python3 -c "import json,sys; print(json.load(sys.stdin)['failures_24h'])" 2>/dev/null || echo "0")
fi

# ─── Overall Status ───
TOTAL_FAILURES=0
DOCKER_FAIL=$((DOCKER_TOTAL - DOCKER_HEALTHY))
HTTP_FAIL=$((HTTP_TOTAL - HTTP_PASS))
[ "$TAILSCALE_STATUS" != "connected" ] && TOTAL_FAILURES=$((TOTAL_FAILURES + 1))
TOTAL_FAILURES=$((TOTAL_FAILURES + DOCKER_FAIL + HTTP_FAIL + AUTOMATION_FAILURES))

if [ $TOTAL_FAILURES -eq 0 ]; then
  OVERALL_STATUS="healthy"
  STATUS_ICON="🟢"
elif [ $TOTAL_FAILURES -lt 3 ]; then
  OVERALL_STATUS="degraded"
  STATUS_ICON="🟡"
else
  OVERALL_STATUS="critical"
  STATUS_ICON="🔴"
fi

# ─── Output JSON ───
OUTPUT=$(python3 -c "
import json
d = {
  'timestamp': $TIMESTAMP,
  'date': '$DATE_STR',
  'time': '$TIME_STR',
  'overall': {
    'status': '$OVERALL_STATUS',
    'icon': '$STATUS_ICON',
    'total_failures': $TOTAL_FAILURES,
  },
  'docker': {
    'total': $DOCKER_TOTAL,
    'healthy': $DOCKER_HEALTHY,
    'unhealthy': $DOCKER_FAIL,
    'all_healthy': 'true' if $ALL_DOCKER_HEALTHY else 'false',
  },
  'http': {
    'total': $HTTP_TOTAL,
    'passing': $HTTP_PASS,
    'failing': $HTTP_FAIL,
    'checks': {$(for name in \"\${!HTTP_CHECKS[@]}\"; do
      url=\"\${HTTP_CHECKS[\"$name\"]}\"
      code=\$(curl -sL -o /dev/null -w \"%{http_code}\" --connect-timeout 5 --max-time 10 \"\$url\" 2>/dev/null || echo \"000\")
      echo \"\\\"$name\\\": {\\\"url\\\": \\\"$url\\\", \\\"status_code\\\": $code, \\\"healthy\\\": $( [ \"\$code\" != \"000\" ] && [ \"\$code\" -lt 500 ] && echo \"true\" || echo \"false\")}\", 
    done)}
  },
  'network': {
    'tailscale': {
      'status': '$TAILSCALE_STATUS',
      'ip': '$TAILSCALE_IP',
      'devices': $TAILSCALE_DEVICES,
    },
  },
  'bridge': {
    'status': '$BRIDGE_STATUS',
    'watchers': $BRIDGE_WATCHERS,
    'pending_tasks': $BRIDGE_PENDING,
  },
  'system': {
    'disk': '$DISK_USAGE',
    'uptime': '$UPTIME',
    'load': '$LOAD_AVG',
    'memory': '${MEM_USED:-?}G / ${MEM_TOTAL:-?}G ($MEM_PCT%)',
  },
  'automations': {
    'active': $AUTOMATION_TOTAL,
    'failures_24h': $AUTOMATION_FAILURES,
  },
}
print(json.dumps(d, ensure_ascii=False))
")

# Save to file
mkdir -p "$OUTPUT_DIR"
echo "$OUTPUT" > "$OUTPUT_DIR/health_status.json"
echo "$OUTPUT" > "$OUTPUT_DIR/health_status_latest.json"

# Terminal output
if ! $JSON_OUTPUT; then
  echo "═══════════════════════════════════════════"
  echo "  全系统健康检查  $STATUS_ICON  $DATE_STR $TIME_STR"
  echo "═══════════════════════════════════════════"
  echo ""
  echo "📦 Docker ($DOCKER_HEALTHY/$DOCKER_TOTAL)"
  echo -e "$DOCKER_CHECKS" | head -20
  echo ""
  echo "🌐 HTTP Endpoints ($HTTP_PASS/$HTTP_TOTAL)"
  echo -e "$HTTP_RESULTS"
  echo ""
  echo "🔗 Tailscale: $TAILSCALE_STATUS ($TAILSCALE_IP, $TAILSCALE_DEVICES 设备)"
  echo "🔗 Marvis Bridge: $BRIDGE_STATUS (watchers: $BRIDGE_WATCHERS)"
  echo ""
  echo "💻 System: $DISK_USAGE | load: $LOAD_AVG | mem: $MEM_USED/$MEM_TOTAL G"
  echo "⚙️  Automations: $AUTOMATION_TOTAL active, $AUTOMATION_FAILURES failures/24h"
  echo ""
  echo "═══════════════════════════════════════════"
fi

# Return JSON for programmatic use
if $JSON_OUTPUT; then
  echo "$OUTPUT"
fi
