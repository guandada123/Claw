#!/usr/bin/env python3
"""
backup_data.py — Claw 项目每日数据备份
打包 SQLite DB + output 扫描结果到 output/.backups/daily/
自动清理 14 天前的旧备份，失败时推送飞书通知。
"""

import datetime
import fnmatch
import glob
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path


def _find_project_dir(start):
    """向上查找项目根目录:含 pyproject.toml 且含 output/ 的目录。

    不依赖固定的目录层级(backup.py 位于 <root>/src/claw/utils/ 或 <root>/scripts/),
    即使包结构被移入/移出 src/ 也能正确解析到 Claw 根目录。
    """
    cur = os.path.abspath(start)
    while True:
        if os.path.isfile(os.path.join(cur, "pyproject.toml")) and os.path.isdir(
            os.path.join(cur, "output")
        ):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            # 到达文件系统根仍未找到,退而求其次返回起始目录
            return os.path.abspath(start)
        cur = parent


def today_str():
    return datetime.datetime.now().strftime("%Y-%m-%d")


def now_str():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def find_lark_cli():
    candidates = [
        "lark-cli",
        os.path.expanduser(
            "~/.workbuddy/binaries/node/cli-connector-packages/bin/lark-cli"
        ),
        os.path.expanduser(
            "~/.workbuddy/binaries/node/cli-connector-packages/lib/node_modules/@larksuite/cli/bin/lark-cli"
        ),
    ]
    for c in candidates:
        if c == "lark-cli":
            loc = shutil.which("lark-cli")
            if loc:
                return loc
        elif os.path.exists(c):
            return c
    return None


def send_feishu_error(message):
    cli = find_lark_cli()
    if not cli:
        print("  ⚠️  lark-cli 未安装，无法发送飞书通知")
        return False
    try:
        subprocess.run(
            [
                cli,
                "im",
                "+messages-send",
                "--chat-id",
                DEFAULT_CHAT_ID,
                "--as=bot",
                "--markdown",
                message,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return True
    except Exception as e:
        print(f"  ⚠️  飞书推送失败: {e}")
        return False


def should_include(path, base_dir):
    rel = os.path.relpath(path, base_dir)
    for pat in EXCLUDE_PATTERNS:
        if fnmatch.fnmatch(rel, pat):
            return False
    return not ("__pycache__" in rel.split(os.sep) or rel.endswith(".pyc"))


def collect_files(base_dir):
    result = []
    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if f.endswith(".pyc"):
                continue
            result.append(os.path.join(root, f))
    return result


def create_backup(base_dir, output_path):
    added = 0
    with tarfile.open(output_path, "w:gz") as tar:
        for item in BACKUP_ITEMS:
            item_path = os.path.join(base_dir, item)
            if os.path.isfile(item_path) and should_include(item_path, base_dir):
                tar.add(item_path, arcname=item.rstrip("/"))
                added += 1
            elif os.path.isdir(item_path):
                for fp in collect_files(item_path):
                    if should_include(fp, base_dir):
                        arcname = os.path.relpath(fp, base_dir)
                        tar.add(fp, arcname=arcname)
                        added += 1
            else:
                print(f"  ⚠️  跳过不存在的项: {item}")
        # 可选文件:缺失属正常(可再生成),静默跳过不告警
        for item in OPTIONAL_ITEMS:
            item_path = os.path.join(base_dir, item)
            if os.path.isfile(item_path) and should_include(item_path, base_dir):
                tar.add(item_path, arcname=item.rstrip("/"))
                added += 1
    if added == 0:
        print("  ⚠️  没有找到需要备份的文件")
    return os.path.getsize(output_path) if os.path.exists(output_path) else 0


def clean_old_backups(backup_dir, prefix, days):
    pattern = os.path.join(backup_dir, prefix + "*.tar.gz")
    cutoff = time.time() - days * 86400
    removed = 0
    for fp in glob.glob(pattern):
        if os.path.getmtime(fp) < cutoff:
            try:
                os.remove(fp)
                print(f"  🧹 已清理过期备份: {os.path.basename(fp)}")
                removed += 1
            except OSError as e:
                print(f"  ⚠️  清理失败 {fp}: {e}")
    if removed == 0:
        print(f"  ℹ️  无需清理 (无超过 {days} 天的备份)")
    else:
        print(f"  🧹 共清理 {removed} 个过期备份")


# ---- 配置 ----
PROJECT_DIR = _find_project_dir(__file__)
BACKUP_DIR = os.path.join(PROJECT_DIR, "output", ".backups", "daily")
RETENTION_DAYS = 14
BACKUP_PREFIX = "claw-backup-"
DEFAULT_CHAT_ID = "oc_9ee5303497f5e0e71666b610d6bdc346"
BACKUP_ITEMS = (
    ".workbuddy/workbuddy.db",
    "output/",
    "docker-compose.yml",
    "pyproject.toml",
    "ruff.toml",
    "requirements.txt",
)
# 可再生成的环境锁文件:缺失不影响备份完整性,静默跳过不告警。
# (skills-lock.json / requirements.lock 为 WorkBuddy 技能锁与依赖锁,
#  历史上曾多次从项目根目录消失且可随时再生成,不纳入缺失告警。)
OPTIONAL_ITEMS = (
    "skills-lock.json",
    "requirements.lock",
)
EXCLUDE_PATTERNS = (
    "output/.backups/**",
    "output/__pycache__/**",
    "**/__pycache__/**",
)


def main():
    print("\n==================================================")
    print("  📦 Claw 每日数据备份")
    print(f"  📅 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("==================================================")

    backup_name = f"{BACKUP_PREFIX}{today_str()}.tar.gz"
    backup_path = os.path.join(str(BACKUP_DIR), backup_name)

    print(f"  📂 项目目录: {PROJECT_DIR}")
    print(f"  💾 备份文件: {backup_path}")

    os.makedirs(str(BACKUP_DIR), exist_ok=True)

    size = create_backup(str(PROJECT_DIR), backup_path)

    if size == 0:
        raise RuntimeError("备份文件为空或未生成")

    size_mb = size / 1048576
    print(f"\n  ✅ 备份完成! 大小: {size_mb:.2f} MB")

    with tarfile.open(backup_path, "r:gz") as tar:
        members = tar.getmembers()
        dirs = sum(1 for m in members if m.isdir())
        regular = sum(1 for m in members if m.isfile())
    print(f"  📊 包含: {dirs} 个目录, {regular} 个文件")

    print(f"\n  🧹 清理超过 {RETENTION_DAYS} 天的旧备份...")
    clean_old_backups(str(BACKUP_DIR), BACKUP_PREFIX, RETENTION_DAYS)

    current = sorted(glob.glob(os.path.join(str(BACKUP_DIR), BACKUP_PREFIX + "*.tar.gz")))
    print(f"\n  📋 当前备份 ({len(current)} 个):")
    for fp in current:
        fsize = os.path.getsize(fp) / 1048576
        fdate = datetime.datetime.fromtimestamp(os.path.getmtime(fp)).strftime(
            "%Y-%m-%d %H:%M"
        )
        print(f"    📄 {os.path.basename(fp)}  ({fsize:.2f} MB, {fdate})")

    print("\n==================================================")
    print("  🎉 备份成功完成!\n")
    print("==================================================")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        error_msg = f"备份失败: {type(e).__name__}: {e}"
        print(f"\n  ❌ {error_msg}")
        send_feishu_error(
            f"⚠️ **Claw 每日备份失败**\n━━━━━━━━━━━━━━━━\n"
            f"📅 {today_str()} {datetime.datetime.now().strftime('%H:%M')}\n"
            f"❌ **错误**: {error_msg}\n\n请尽快检查服务器状态。"
        )
        print("\n  📢 已推送飞书错误通知")
        print("==================================================")
        sys.exit(1)
