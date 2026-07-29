#!/bin/bash
# ============================================================
# CI 门禁 — 代码质量全检
# ============================================================
# 覆盖: 单测+覆盖率 / 类型检查 / 安全扫描 / Bash 检查 / 双导入门禁
# 用法: bash .workbuddy/scripts/ci_guard.sh
# 退出码: 0=全部通过, 1=至少一项失败
# ============================================================
set -euo pipefail

RED="\033[31m"
GREEN="\033[32m"
YELLOW="\033[33m"
NC="\033[0m"

FAILURES=0
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PYTHON="/Users/guan/.workbuddy/binaries/python/envs/default/bin/python"

echo "========================================"
echo "  CI 门禁 — 代码质量全检"
echo "  项目: $ROOT"
echo "========================================"

# ---- Phase 1: 单元测试 + 覆盖率 ----
echo ""
# 从 pyproject.toml 读取 fail_under 阈值（默认 9）
FAIL_UNDER=$(python3 -c "
import tomllib, pathlib
try:
    cfg = tomllib.loads(pathlib.Path('$ROOT/pyproject.toml').read_text())
    print(cfg.get('tool',{}).get('coverage',{}).get('report',{}).get('fail_under',9))
except Exception:
    print(9)
" 2>/dev/null || echo 9)
echo -e "${YELLOW}[1/5] 单元测试 + 覆盖率门禁（fail_under=${FAIL_UNDER}%）${NC}"
cd "$ROOT"
if $PYTHON -m pytest tests/ --cov=scripts --cov-report=term --cov-report=html:.workbuddy/data/coverage --no-header -q 2>&1; then
    echo -e "${GREEN}  ✅ 单测通过${NC}"
else
    echo -e "${RED}  ❌ 单测失败${NC}"
    FAILURES=$((FAILURES+1))
fi

# ---- Phase 2: 类型检查 (mypy) ----
echo ""
echo -e "${YELLOW}[2/5] 类型检查 (mypy — scripts/)${NC}"
cd "$ROOT"
MYPY_OUT=$($PYTHON -m mypy scripts/ --no-error-summary 2>&1 || true)
MYPY_ISSUES=$(echo "$MYPY_OUT" | grep -c "error:" || true)
if [ "${MYPY_ISSUES:-0}" -eq 0 ]; then
    echo -e "${GREEN}  ✅ mypy 通过${NC}"
else
    echo "$MYPY_OUT"
    echo -e "${YELLOW}  ⚠️  mypy 发现 $MYPY_ISSUES 处类型问题（非阻塞告警，逐步收敛）${NC}"
fi

# ---- Phase 3: 安全扫描 (bandit) ----
echo ""
echo -e "${YELLOW}[3/5] 安全扫描 (bandit)${NC}"
cd "$ROOT"
BANDIT_OUT=$($PYTHON -m bandit -r scripts/ -f custom 2>&1 || true)
# bandit 以 exit 1 表示发现 issue（正常行为），我们按行数判断
ISSUE_COUNT=$(echo "$BANDIT_OUT" | grep -c "Issue:" || true)
if [ "${ISSUE_COUNT:-0}" -eq 0 ]; then
    echo -e "${GREEN}  ✅ bandit 无安全告警${NC}"
else
    echo "$BANDIT_OUT"
    echo -e "${RED}  ❌ bandit 发现 $ISSUE_COUNT 处安全告警${NC}"
    FAILURES=$((FAILURES+1))
fi

# ---- Phase 4: Bash 检查 (shellcheck) ----
echo ""
echo -e "${YELLOW}[4/5] Bash 语法检查 (shellcheck)${NC}"
BASH_FILES=$(find "$ROOT/.workbuddy/scripts" -name "automation_preamble.sh" -o -name "push_feishu.sh" -o -name "ci_guard.sh" -type f 2>/dev/null || true)
if command -v shellcheck &>/dev/null && [ -n "$BASH_FILES" ]; then
    SHELL_ISSUES=0
    for f in $BASH_FILES; do
        if ! shellcheck -S warning -x "$f" 2>&1; then
            SHELL_ISSUES=$((SHELL_ISSUES+1))
        fi
    done
    if [ "$SHELL_ISSUES" -eq 0 ]; then
        echo -e "${GREEN}  ✅ shellcheck 通过${NC}"
    else
        echo -e "${RED}  ❌ shellcheck 发现 $SHELL_ISSUES 处问题${NC}"
        FAILURES=$((FAILURES+1))
    fi
else
    echo -e "${YELLOW}  ⚠️  shellcheck 未安装 / 无 .sh 文件，跳过${NC}"
fi

# ---- Phase 5: 双导入门禁 ----
echo ""
echo -e "${YELLOW}[5/5] 双导入门禁${NC}"
cd "$ROOT"
if $PYTHON scripts/check_no_double_import.py; then
    echo -e "${GREEN}  ✅ 双导入门禁通过${NC}"
else
    echo -e "${RED}  ❌ 双导入门禁失败${NC}"
    FAILURES=$((FAILURES+1))
fi

# ---- 汇总 ----
echo ""
echo "========================================"
if [ "$FAILURES" -eq 0 ]; then
    echo -e "${GREEN}  🎉 全部门禁通过 (5/5)${NC}"
    exit 0
else
    echo -e "${RED}  ❌ $FAILURES 项门禁未通过${NC}"
    exit 1
fi
