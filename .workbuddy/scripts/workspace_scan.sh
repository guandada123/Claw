#!/usr/bin/env bash
# workspace_scan.sh — 工作空间磁盘占用扫描(非破坏性, 仅盘点不清理)。
# 用法: workspace_scan.sh [PATH] [TOP_N]
# 输出: 总占用 + Top N 子目录 + 可重生缓存候选(仅提示)。
# 铁律: 仅盘点; 清理需人工授权, 禁止自动 prune(遵循禁区/非破坏性)。
set -u

TARGET="${1:-/Users/guan/WorkBuddy/Claw}"
TOP_N="${2:-10}"

[ -d "$TARGET" ] || { echo "路径不存在: $TARGET" >&2; exit 2; }

TOTAL=$(du -sm "$TARGET" 2>/dev/null | cut -f1)
echo "工作空间: $TARGET"
echo "总占用: ${TOTAL} MB"
echo "--- Top $TOP_N 子目录 (MB) ---"
du -sm "$TARGET"/* 2>/dev/null | sort -rn | head -n "$TOP_N"
echo "--- 可重生缓存候选(仅提示, 不清理) ---"
find "$TARGET" -maxdepth 3 -type d \( \
    -name target -o -name node_modules -o -name __pycache__ \
    -o -name dist -o -name build -o -name .next -o -name .venv \) 2>/dev/null \
    | while read -r d; do
        sz=$(du -sm "$d" 2>/dev/null | cut -f1)
        echo "  $d -> ${sz}MB (可重生, 需人工授权再清理)"
      done
echo "说明: 仅盘点; 清理需人工授权, 禁止自动 prune(禁区/非破坏性铁律)。"
