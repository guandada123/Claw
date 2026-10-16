#!/bin/bash
# GitHub 自动备份 — 将模拟炒股数据推送到 GitHub 仓库
#
# ⚠️ 2026-10-11 收窄为**默认只读**（改前：跑起来就 `git add` 8 组路径 + commit + push）。
#   背景：本脚本**当前没有任何自动化/脚本引用**（旧「GitHub 自动备份」流程遗留），
#   而它的形态是危险的 —— "谁顺手跑一下"就会**一把扫走工作树里的在写文件并推到远端**。
#   它原本要干的活（把 data/ · memory/ · reports/ 等运行产物入库）现在由统一巡检中枢的
#   `chore(data,hub)` 提交承担，且那些提交是**逐路径**的、不会扫走别人的在写状态。
#   故：不删（能力保留），但改成**要显式 --apply / --push 才动手**，并**打印会提交什么**。
#
# 用法:
#   ./scripts/github_sync.sh [commit_message]           # 默认：只读，列出"会提交什么"
#   ./scripts/github_sync.sh [commit_message] --apply   # 真 add + commit（不推）
#   ./scripts/github_sync.sh [commit_message] --apply --push   # 再推 origin
#
# 首次使用：
#   1. 在 GitHub 创建一个私有仓库（如 claw-simulation）
#   2. 在项目目录执行：
#      git init
#      git remote add origin git@github.com:<your-username>/claw-simulation.git
#   3. 确保 SSH key 已配置：ssh -T git@github.com

set -e

# ── 参数解析（2026-10-11：默认只读，见文件头）──────────────────────────
APPLY=0
DO_PUSH=0
ARGS=()
for a in "$@"; do
  case "$a" in
    --apply) APPLY=1 ;;
    --push) DO_PUSH=1 ;;
    *) ARGS+=("$a") ;;
  esac
done
# shellcheck disable=SC2128  # (bash 数组取首元素；此处刻意兼容空数组)
COMMIT_MSG="${ARGS[0]:-📊 Auto backup $(date '+%Y-%m-%d %H:%M')}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ⚠️ 2026-10-11 修：原写法 `PROJECT_DIR=$SCRIPT_DIR/..` 是按旧布局 `Claw/scripts/` 推的，
#    而本文件现在住在 `.workbuddy/scripts/` → 推出的是 `Claw/.workbuddy`（不是仓库根）
#    → 每次都落在「❌ 当前目录不是 git 仓库」直接退出。**即：本脚本一直是惰性的**
#    （所以它那「一把扫走」的形态此前并未真的能发生 —— 我上一轮把它说成"顺手一调就危险"是**判断错误**）。
#    改成**向上找 `.git`**：与住哪个目录无关，重定位后仍正确。
_repo_root() {
  local d="$1"
  while [ "$d" != "/" ] && [ -n "$d" ]; do
    [ -d "$d/.git" ] && { printf '%s' "$d"; return 0; }
    d="$(dirname "$d")"
  done
  return 1
}
PROJECT_DIR="$(_repo_root "$SCRIPT_DIR")" || {
  echo "❌ 向上找不到 git 仓库根（从 $SCRIPT_DIR 起）"; exit 1
}

cd "$PROJECT_DIR"

# 检查是否为 git 仓库
if [ ! -d .git ]; then
  echo "❌ 当前目录不是 git 仓库"
  echo "  请先初始化：git init && git remote add origin <your-repo>"
  exit 1
fi

# 检查是否有 remote
if ! git remote -v 2>/dev/null | grep -q origin; then
  echo "❌ 未配置 git remote"
  echo "  请设置：git remote add origin git@github.com:<your-username>/claw-simulation.git"
  exit 1
fi

echo "📤 github_sync（默认只读；--apply 才写入，--push 才推远端）"
echo ""

PATHS=(
  data/simulation/portfolio.json
  data/simulation/decision_log.json
  data/simulation/strategy_library.json
  data/simulation/history/
  reports/
  .workbuddy/memory/
  .workbuddy/automations/*/memory.md
  scripts/
)

# ── 默认只读：列出"本脚本会碰哪些路径的改动"，不动 git ────────────────
if [ "$APPLY" -ne 1 ]; then
  echo "🔎 只读模式：以下改动是**本脚本会提交的范围**（未执行任何写操作）"
  echo "   要真提交：加 --apply；还要推远端：再加 --push"
  echo ""
  git status --porcelain -- "${PATHS[@]}" 2>/dev/null || true
  echo ""
  # 路径失配提示：本清单来自**旧布局**，仓库改版后可能大多已不存在 ——
  # 不说出来，后人会以为"没输出 = 没改动"，其实可能是"这些路径早没了"。
  MISSING=()
  for f in "${PATHS[@]}"; do
    [ -e "$f" ] || MISSING+=("$f")
  done
  if [ "${#MISSING[@]}" -gt 0 ]; then
    # ⚠️ 不写 "${#MISSING[@]}/${#PATHS[@]}" 这种分母：数组赋值时通配符已展开，
    #    分母会变成 84（含 .workbuddy/automations/*/memory.md 展开出的文件），读起来像"4/84"很突兀。
    echo "⚠️ 清单里有 ${#MISSING[@]} 个路径**已不存在**（本脚本早于仓库改版）："
    printf '     - %s\n' "${MISSING[@]}"
    echo "   → 所以\"没有输出\"多半不是\"没有改动\"，而是\"这些路径已经不是产物所在处\"。"
    echo ""
  fi
  echo "⚠️ 注意：本脚本按\"整目录\"add（如 reports/、scripts/），若此刻有其它自动化"
  echo "   正在写这些路径，--apply 会把**别人写了一半的文件**一并提交。"
  echo "   更安全的做法是用统一巡检中枢的 chore(data,hub) 逐路径提交。"
  exit 0
fi

# 添加所有数据文件
git add data/simulation/portfolio.json 2>/dev/null || true
git add data/simulation/decision_log.json 2>/dev/null || true
git add data/simulation/strategy_library.json 2>/dev/null || true
git add data/simulation/history/ 2>/dev/null || true
git add reports/ 2>/dev/null || true
git add .workbuddy/memory/ 2>/dev/null || true
git add .workbuddy/automations/*/memory.md 2>/dev/null || true
git add scripts/ 2>/dev/null || true

# Commit
if git diff --cached --quiet 2>/dev/null; then
  echo "✅ 没有需要同步的变更"
  exit 0
fi

git commit -m "$COMMIT_MSG" 2>/dev/null || echo "⚠️ commit 可能没有新的变更"

# Push（需显式 --push）
if [ "$DO_PUSH" -ne 1 ]; then
  echo "✅ 已提交（未推送）。要推远端：加 --push"
  exit 0
fi
echo "📡 推送到 origin/main..."
git push origin main 2>&1 || git push origin master 2>&1 || {
  echo "⚠️ Push 失败。请检查网络和 remote 配置"
  echo "  可以手动执行: git push origin main"
  exit 1
}

echo ""
echo "✅ 备份完成！$(date '+%H:%M:%S')"
