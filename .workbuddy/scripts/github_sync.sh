#!/bin/bash
# GitHub 自动备份 — 将模拟炒股数据推送到 GitHub 仓库
#
# 用法: ./scripts/github_sync.sh [commit_message]
#
# 首次使用：
#   1. 在 GitHub 创建一个私有仓库（如 claw-simulation）
#   2. 在项目目录执行：
#      git init
#      git remote add origin git@github.com:<your-username>/claw-simulation.git
#   3. 确保 SSH key 已配置：ssh -T git@github.com

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

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

# 生成 commit message
COMMIT_MSG="${1:-📊 Auto backup $(date '+%Y-%m-%d %H:%M')}"

echo "📤 开始备份到 GitHub..."
echo ""

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

# Push
echo "📡 推送到 origin/main..."
git push origin main 2>&1 || git push origin master 2>&1 || {
  echo "⚠️ Push 失败。请检查网络和 remote 配置"
  echo "  可以手动执行: git push origin main"
  exit 1
}

echo ""
echo "✅ 备份完成！$(date '+%H:%M:%S')"
