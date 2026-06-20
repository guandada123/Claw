#!/usr/bin/env python3
"""
cost_dashboard_feishu.py — AI成本仪表盘 → 飞书推送
=====================================================
一键完成：生成仪表盘HTML → 上传飞书Drive → 发送日报到飞书群

用法:
  python3 cost_dashboard_feishu.py                        → 推送到默认群（盘面信息）
  python3 cost_dashboard_feishu.py --chat oc_xxx          → 指定群
  python3 cost_dashboard_feishu.py --dry-run              → 仅生成仪表盘，不上传不推送
  python3 cost_dashboard_feishu.py --no-upload            → 不上传Drive，仅发消息

依赖:
  - lark-cli 已配置（lark-cli auth status 可正常返回）
  - scripts/cost_dashboard.py 存在
  - scripts/cost_monitor.py 存在
"""

import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent.resolve()

# ============================================================
# 配置
# ============================================================

DEFAULT_CHAT_ID = "oc_9ee5303497f5e0e71666b610d6bdc346"  # 盘面信息
LARK_DOMAIN = "bytedance.feishu.cn"  # 飞书域名
LARK_CLI_PATH: str = ""  # 初始化后由 find_lark_cli() 填充

# ============================================================
# 工具函数
# ============================================================


def find_lark_cli() -> str:
    """查找 lark-cli 可执行文件路径"""
    # 1) 优先从 PATH 找
    lark = shutil.which("lark-cli")
    if lark:
        return lark

    # 2) 从 WorkBuddy 已知安装路径找
    known_paths = [
        os.path.expanduser("~/.workbuddy/binaries/node/cli-connector-packages/bin/lark-cli"),
        os.path.expanduser(
            "~/.workbuddy/binaries/node/cli-connector-packages/lib/node_modules/@larksuite/cli/bin/lark-cli"
        ),
    ]
    for p in known_paths:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p

    # 3) 从 Node 全局模块找
    for prefix in ["/usr/local/lib/node_modules", "/opt/homebrew/lib/node_modules"]:
        candidate = os.path.join(prefix, "@larksuite/cli/bin/lark-cli")
        if os.path.isfile(candidate):
            return candidate

    return ""


def run_cmd(cmd: list, **kwargs) -> subprocess.CompletedProcess:
    """执行命令并返回结果"""
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    return result


def today_str() -> str:
    return datetime.date.today().isoformat()


def now_str() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


# ============================================================
# 步骤1: 生成仪表盘
# ============================================================


def generate_dashboard() -> str | None:
    """生成成本仪表盘HTML，返回文件路径"""
    ts = now_str()
    output_path = f"/tmp/ai-cost-dashboard-{ts}.html"

    print("  📊 运行 cost_dashboard.py...")
    result = run_cmd(
        [sys.executable, str(SCRIPTS_DIR / "cost_dashboard.py"), output_path], timeout=60
    )

    if result.returncode != 0:
        print(f"  ⚠️  cost_dashboard.py 返回码 {result.returncode}")
        print(f"  stderr: {result.stderr[:300]}")

    if os.path.exists(output_path):
        size_kb = os.path.getsize(output_path) / 1024
        print(f"  ✅ 仪表盘已生成: {size_kb:.1f} KB")
        return output_path

    # 回退：检查桌面最近的文件
    desktop_files = sorted(
        Path.home().glob("Desktop/ai-cost-dashboard-*.html"), key=os.path.getmtime, reverse=True
    )
    if desktop_files:
        fp = str(desktop_files[0])
        print(f"  ✅ 使用已有仪表盘: {fp}")
        return fp

    print("  ❌ 仪表盘生成失败")
    return None


# ============================================================
# 步骤2: 上传到飞书Drive
# ============================================================


def upload_to_drive(file_path: str) -> dict | None:
    """上传文件到飞书Drive，返回结果字典"""
    date_str = today_str()
    file_name = f"AI成本仪表盘-{date_str}.html"

    print(f"  ☁️  上传到飞书Drive (文件名: {file_name})...")

    # lark-cli 要求相对路径，复制到当前目录后用相对路径上传
    local_copy = Path.cwd() / file_name
    shutil.copy2(file_path, local_copy)
    print(f"  📄 本地副本: {local_copy}")

    result = run_cmd(
        [
            LARK_CLI_PATH,
            "drive",
            "+upload",
            "--file",
            file_name,
            "--name",
            file_name,
            "--as",
            "user",
        ],
        timeout=30,
    )

    # 清理本地副本
    if local_copy.exists():
        local_copy.unlink()

    if result.returncode != 0:
        print(f"  ⚠️  上传返回码 {result.returncode}")
        print(f"  stderr: {result.stderr[:500]}")
        # 尝试解析 JSON 输出中的错误信息
        try:
            err_data = json.loads(result.stdout) if result.stdout.strip() else {}
            if not err_data:
                err_data = json.loads(result.stderr) if result.stderr.strip() else {}
            err_msg = err_data.get("error", {}).get("message", "未知错误")
            err_type = err_data.get("error", {}).get("type", "")
            print(f"  ❌ 上传失败: {err_type} — {err_msg}")
        except (json.JSONDecodeError, IndexError):
            print("  ❌ 上传失败")
        return None

    try:
        data = json.loads(result.stdout)
        print("  ✅ 上传成功!")
        return data
    except json.JSONDecodeError:
        print("  ⚠️  无法解析上传结果")
        print(f"  stdout: {result.stdout[:300]}")
        return None


def build_file_url(upload_result: dict) -> str:
    """从上传结果构建文件URL"""
    data = upload_result.get("data", {})
    file_token = data.get("file_token", "")
    if file_token:
        return f"https://{LARK_DOMAIN}/drive/{file_token}"
    return ""


# ============================================================
# 步骤3: 获取日报摘要
# ============================================================


def get_daily_cost_summary() -> tuple[str, dict]:
    """
    获取成本日报摘要，返回 (markdown_text, stats_dict)
    """
    print("  📋 获取成本日报摘要...")

    # 从cost_monitor获取日报
    result = run_cmd([sys.executable, str(SCRIPTS_DIR / "cost_monitor.py"), "daily"], timeout=30)

    daily_raw = result.stdout.strip() if result.returncode == 0 else ""

    # 同时从cost_tracker获取摘要统计
    cost_stats = {}
    result2 = run_cmd([sys.executable, str(SCRIPTS_DIR / "cost_tracker.py"), "status"], timeout=15)
    if result2.returncode == 0:
        try:
            cost_stats = json.loads(result2.stdout)
        except json.JSONDecodeError:
            pass

    return daily_raw, cost_stats


# ============================================================
# 步骤4: 发送飞书消息
# ============================================================


def build_message(daily_summary: str, file_url: str, cost_stats: dict) -> str:
    """构建飞书消息文本（Markdown格式）"""
    today = today_str()

    # 提取关键指标
    total_cost_today = cost_stats.get("today_cost", 0)
    total_cost_month = cost_stats.get("month_cost", 0)
    budget_remaining = max(0, 400.0 - total_cost_month)

    # 计算今天的日期用于显示
    weekday_cn = ["一", "二", "三", "四", "五", "六", "日"][datetime.date.today().weekday()]
    date_display = f"{today} 周{weekday_cn}"

    msg = f"""💰 **AI 成本监控日报** | {date_display}

━━━━━━━━━━━━━━━━

📊 **今日预估成本**: ¥{total_cost_today:.2f}
📅 **本月累计**: ¥{total_cost_month:.2f}
🎯 **剩余预算**: ¥{budget_remaining:.2f}
📈 **预算消耗**: {total_cost_month / 400.0 * 100:.1f}%

━━━━━━━━━━━━━━━━

**详细日报:**
{daily_summary[:1500]}

━━━━━━━━━━━━━━━━
📎 **[查看完整仪表盘]({file_url})** — 含趋势图/模型分布/烧钱任务Top

> ⏱ {datetime.datetime.now().strftime("%H:%M")} 自动生成
"""

    return msg


def send_feishu_message(chat_id: str, message: str) -> bool:
    """发送消息到飞书群"""
    print(f"  💬 发送消息到群 {chat_id}...")

    result = run_cmd(
        [
            LARK_CLI_PATH,
            "im",
            "+messages-send",
            "--chat-id",
            chat_id,
            "--markdown",
            message,
            "--as",
            "bot",
        ],
        timeout=30,
    )

    if result.returncode != 0:
        print(f"  ⚠️  消息发送返回码 {result.returncode}")
        print(f"  stderr: {result.stderr[:500]}")
        try:
            err_data = json.loads(result.stdout) if result.stdout.strip() else {}
            err_msg = err_data.get("error", {}).get("message", "")
            if err_msg:
                print(f"  ❌ 发送失败: {err_msg}")
        except json.JSONDecodeError:
            pass
        return False

    try:
        data = json.loads(result.stdout)
        msg_id = data.get("data", {}).get("message_id", "?")
        print(f"  ✅ 消息已发送! message_id: {msg_id}")
        return True
    except json.JSONDecodeError:
        print("  ✅ 消息已发送 (无法解析message_id)")
        return True


# ============================================================
# 步骤5: 清理
# ============================================================


def cleanup(file_path: str):
    """清理临时文件"""
    if file_path and file_path.startswith("/tmp/") and os.path.exists(file_path):
        os.remove(file_path)
        print("  🧹 已清理临时文件")


# ============================================================
# 主流程
# ============================================================


def main():
    # 解析参数
    args = sys.argv[1:]
    chat_id = DEFAULT_CHAT_ID
    dry_run = False
    skip_upload = False
    keep_html = False

    for i, arg in enumerate(args):
        if arg == "--chat" and i + 1 < len(args):
            chat_id = args[i + 1]
        elif arg == "--dry-run":
            dry_run = True
        elif arg == "--no-upload":
            skip_upload = True
        elif arg == "--keep-html":
            keep_html = True

    # 检测飞书CLI
    global LARK_CLI_PATH
    LARK_CLI_PATH = find_lark_cli()
    if not LARK_CLI_PATH:
        print("❌ lark-cli 未安装")
        print("  安装方式: npm install -g @larksuite/cli")
        sys.exit(1)
    print(f"  🔧 lark-cli: {LARK_CLI_PATH}")

    print(f"\n{'=' * 50}")
    print("  🤖 AI成本仪表盘 → 飞书推送")
    print(f"  📅 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  📢 目标群: {chat_id}")
    if dry_run:
        print("  🔍 DRY RUN 模式 — 仅生成，不上传/不推送")
    print(f"{'=' * 50}\n")

    # === 步骤1: 生成仪表盘 ===
    print("📊 [1/4] 生成成本仪表盘...")
    html_path = generate_dashboard()
    if not html_path:
        print("\n❌ 仪表盘生成失败，终止流程")
        sys.exit(1)

    if dry_run:
        print(f"\n🔍 DRY RUN — 仪表盘已保存到: {html_path}")
        if not keep_html:
            cleanup(html_path)
        else:
            print(f"   Dashboard saved (--keep-html): {html_path}")
        print("\n✅ DRY RUN 完成\n")
        return

    # === 步骤2: 上传到飞书Drive ===
    file_url = ""
    if not skip_upload:
        print("\n☁️  [2/4] 上传到飞书Drive...")
        upload_result = upload_to_drive(html_path)
        if upload_result:
            file_url = build_file_url(upload_result)
            if file_url:
                print(f"  🔗 仪表盘链接: {file_url}")
        else:
            print("  ⚠️  上传失败，消息中将不包含仪表盘链接")
    else:
        print("\n☁️  [2/4] 已跳过上传 (--no-upload)")

    # === 步骤3: 获取成本摘要 ===
    print("\n📋 [3/4] 获取成本摘要...")
    daily_summary, cost_stats = get_daily_cost_summary()
    if daily_summary:
        # 截取摘要核心部分
        lines = daily_summary.split("\n")
        brief = "\n".join(lines[:20])  # 只取前20行
        print(f"  ✅ 已获取摘要 ({len(lines)} 行)")
    else:
        brief = "(暂无成本数据)"
        print("  ℹ️  暂无成本数据")

    # === 步骤4: 发送飞书消息 ===
    print("\n💬 [4/4] 发送飞书消息...")
    message = build_message(brief, file_url, cost_stats)
    success = send_feishu_message(chat_id, message)

    # === 清理 ===
    cleanup(html_path)

    # === 总结 ===
    print(f"\n{'=' * 50}")
    if success:
        print("  🎉 飞书推送完成!")
    else:
        print("  ⚠️  推送完成但消息发送可能有异常")
    if file_url:
        print(f"  🔗 仪表盘: {file_url}")
    print(f"{'=' * 50}\n")


if __name__ == "__main__":
    main()
