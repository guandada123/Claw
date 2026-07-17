#!/usr/bin/env python3
"""
notify_center.py — 飞书推送通知中台
v2 (2026-07-17): 委托 push_card.py 发 interactive 卡片（经 push_feishu.sh），
旧纯文本推送统一升级为「语义配色 + 分区」卡片。
新增 --level 显式指定配色（alert/warning/info/success）；
未指定时按 event-type 关键词推断，兜底 info。
"""
import argparse
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PUSH_SCRIPT = os.path.join(SCRIPT_DIR, "push_feishu.sh")
CHAT_ID = "oc_9ee5303497f5e0e71666b610d6bdc346"

# event-type 关键词 → level 推断
ALERT_KW = ("失败", "告警", "止损", "击穿", "异常", "错误", "崩溃", "断", "缺失", "未更新")
WARN_KW = ("警告", "降级", "逼近", "阈值", "滞后", "stale", "未推进", "注意")
SUCCESS_KW = ("成功", "完成", "已更新", "无异常", "通过")


def infer_level(event_type: str) -> str:
    et = (event_type or "").lower()
    if any(k in et for k in ALERT_KW):
        return "alert"
    if any(k in et for k in WARN_KW):
        return "warning"
    if any(k in et for k in SUCCESS_KW):
        return "success"
    return "info"


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")

    push_parser = sub.add_parser("push")
    push_parser.add_argument("--event-type", default="通知")
    push_parser.add_argument("--message", required=True)
    push_parser.add_argument("--dedupe-key", default="")
    push_parser.add_argument("--cooldown", type=int, default=1440)
    push_parser.add_argument("--level", default="",
                             choices=["", "alert", "warning", "info", "success"],
                             help="显式配色；缺省按 event-type 关键词推断")

    args = parser.parse_args()

    if args.cmd != "push":
        print("仅支持 push 命令", file=sys.stderr)
        return 1

    # 去重检查（文件级）
    dedupe = args.dedupe_key
    if dedupe:
        dedupe_file = f"/tmp/feishu_dedup_{dedupe.replace(' ', '_')[:40]}"
        if os.path.exists(dedupe_file):
            print(f"⚠️ 去重: {dedupe} (已在冷却期内)")
            return 0
        with open(dedupe_file, "w") as f:
            f.write("sent")

    # level：显式优先，否则按 event-type 推断
    level = args.level or infer_level(args.event_type)

    # 委托 push_feishu.sh（其内已路由到 push_card.py）
    env = os.environ.copy()
    env["FEISHU_CHAT_ID"] = CHAT_ID
    env["PUSH_LEVEL"] = level
    try:
        subprocess.run(
            ["bash", PUSH_SCRIPT, args.event_type, args.message],
            env=env, check=True, timeout=30,
            capture_output=True, text=True,
        )
        print(f"✅ 推送成功: {args.event_type} (level={level})")
    except Exception as e:
        # 降级：直接走 push_card.py markdown 兜底（绝不用 --text 丢格式）
        print(f"⚠️ push_feishu.sh 失败: {e}，降级 push_card")
        try:
            subprocess.run(
                [sys.executable, os.path.join(SCRIPT_DIR, "push_card.py"),
                 "--title", args.event_type, "--level", level,
                 "--section", "", (args.message or "")[:2000]],
                check=False, timeout=15,
            )
        except Exception as e2:
            print(f"🔴 兜底也失败: {e2}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
