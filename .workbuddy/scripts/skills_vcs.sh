#!/usr/bin/env bash
# skills_vcs.sh — 技能库版本控制（统一巡检中枢 v1.10.2）
#
# 病根：~/.workbuddy/skills 长期不在任何 git 内（物理落在可移动卷）。
#      「📥 每日·文档类落地」写入的 references/*.md 与 SKILL.md 版本演进，
#      **没有真正的回滚点**；铁律「每批 1 commit + 1 回滚点」对主产物只是名义上的。
#
# 本工具把「技能库」当作一个 delivrable 仓库来管：改前打快照、改后打快照、
# 出错可精确回滚到某个文件级状态，全部由命令完成而非手工 cp。
#
# 用法：
#   skills_vcs.sh ensure                 # 幂等：未初始化则 init + 基线提交
#   skills_vcs.sh status                 # 工作区是否干净
#   skills_vcs.sh head                   # 当前 HEAD 短 sha（回滚点标识）
#   skills_vcs.sh snapshot "<note>"      # 提交全部改动（无改动则空转）
#   skills_vcs.sh log [n]                # 最近 n 次提交
#   skills_vcs.sh diff [path]            # 未提交差异
#   skills_vcs.sh rollback <path>        # 单文件回滚到 HEAD（未跟踪文件 → 移入 _trash/）
#   skills_vcs.sh revert <sha>           # 撤销某次提交（git revert，保留历史）
#
# 退出码：0=成功；2=仓库不可用/参数错（**不得当成"无事发生"**）；3=无改动（snapshot 空转）
set -uo pipefail

SKILLS_DIR="${WORKBUDDY_SKILLS_DIR:-${HOME}/.workbuddy/skills}"
GIT=(git -C "$SKILLS_DIR")

die() { echo "[skills_vcs] ERROR: $*" >&2; exit 2; }

ensure_repo() {
  [ -d "$SKILLS_DIR" ] || die "技能库目录不存在: $SKILLS_DIR"
  if ! "${GIT[@]}" rev-parse --git-dir >/dev/null 2>&1; then
    echo "[skills_vcs] 未初始化 → 执行 git init + 基线提交"
    "${GIT[@]}" init -q || die "git init 失败"
    "${GIT[@]}" add -A >/dev/null 2>&1
    "${GIT[@]}" commit -q -m "chore(skills): 自动初始化基线快照" >/dev/null 2>&1 || true
  fi
}

cmd="${1:-status}"; shift || true

case "$cmd" in
  ensure)
    ensure_repo
    echo "[skills_vcs] repo=${SKILLS_DIR} head=$("${GIT[@]}" rev-parse --short HEAD 2>/dev/null || echo '(none)')"
    ;;

  status)
    ensure_repo
    if [ -z "$("${GIT[@]}" status --porcelain 2>/dev/null)" ]; then
      echo "clean"
    else
      echo "dirty"
      "${GIT[@]}" status --porcelain | head -40
    fi
    ;;

  head)
    ensure_repo
    "${GIT[@]}" rev-parse --short HEAD 2>/dev/null || die "无提交"
    ;;

  snapshot)
    note="${1:-manual snapshot}"
    ensure_repo
    if [ -z "$("${GIT[@]}" status --porcelain 2>/dev/null)" ]; then
      echo "[skills_vcs] 无改动，空转（head=$("${GIT[@]}" rev-parse --short HEAD))"
      exit 3
    fi
    "${GIT[@]}" add -A >/dev/null 2>&1 || die "git add 失败"
    "${GIT[@]}" commit -q -m "$note" || die "git commit 失败"
    echo "[skills_vcs] snapshot ok head=$("${GIT[@]}" rev-parse --short HEAD) :: $note"
    ;;

  log)
    ensure_repo
    "${GIT[@]}" log --oneline -n "${1:-10}"
    ;;

  diff)
    ensure_repo
    if [ -n "${1:-}" ]; then "${GIT[@]}" diff -- "$1"; else "${GIT[@]}" diff; fi
    ;;

  rollback)
    target="${1:-}"
    [ -n "$target" ] || die "用法: skills_vcs.sh rollback <相对路径>"
    ensure_repo
    # 归一化：允许传绝对路径或 skills 内相对路径
    rel="${target#"$SKILLS_DIR"/}"
    if "${GIT[@]}" ls-files --error-unmatch -- "$rel" >/dev/null 2>&1; then
      "${GIT[@]}" checkout HEAD -- "$rel" || die "checkout 失败: $rel"
      echo "[skills_vcs] 已回滚到 HEAD: $rel"
    elif [ -e "$SKILLS_DIR/$rel" ]; then
      # 未跟踪（本次新增、尚未提交）→ 移入 _trash，不删除（非破坏性铁律）
      mkdir -p "$SKILLS_DIR/_trash"
      dest="$SKILLS_DIR/_trash/$(date +%Y%m%d-%H%M%S)-$(basename "$rel")"
      mv "$SKILLS_DIR/$rel" "$dest" || die "移入 _trash 失败: $rel"
      echo "[skills_vcs] 未跟踪文件已移入 _trash（未删除）: $dest"
    else
      die "路径不存在: $rel"
    fi
    ;;

  revert)
    sha="${1:-}"
    [ -n "$sha" ] || die "用法: skills_vcs.sh revert <sha>"
    ensure_repo
    "${GIT[@]}" revert --no-edit "$sha" || die "revert 失败（可能有冲突，请人工处理）"
    echo "[skills_vcs] reverted $sha → head=$("${GIT[@]}" rev-parse --short HEAD)"
    ;;

  *)
    echo "用法: skills_vcs.sh {ensure|status|head|snapshot <note>|log [n]|diff [path]|rollback <path>|revert <sha>}" >&2
    exit 2
    ;;
esac
