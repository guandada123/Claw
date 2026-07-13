#!/usr/bin/env python3
"""
notify_center.py — 飞书推送通知中台
薄壳封装：委托 push_feishu.sh，兼容所有旧自动化调用。

用法:
  python3 notify_center.py push \
    --event-type "事件类型" \
    --message "消息内容" \
    --dedupe-key "去重键" \
    [--cooldown 1440]
"""
import argparse
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PUSH_SCRIPT = os.path.join(SCRIPT_DIR, "push_feishu.sh")
CHAT_ID = "oc_9ee5303497f5e0e71666b610d6bdc346"


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")

    push_parser = sub.add_parser("push")
    push_parser.add_argument("--event-type", default="通知")
    push_parser.add_argument("--message", required=True)
    push_parser.add_argument("--dedupe-key", default="")
    push_parser.add_argument("--cooldown", type=int, default=1440)

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

    # 委托 push_feishu.sh
    env = os.environ.copy()
    env["FEISHU_CHAT_ID"] = CHAT_ID
    try:
        subprocess.run(
            ["bash", PUSH_SCRIPT, args.event_type, args.message],
            env=env, check=True, timeout=30,
            capture_output=True, text=True,
        )
        print(f"✅ 推送成功: {args.event_type}")
    except Exception as e:
        # 降级：用 lark-cli 直推
        print(f"⚠️ push_feishu.sh 失败: {e}，降级 lark-cli")
        msg = (args.message or "")[:500]
        subprocess.run(
            ["lark-cli", "im", "+messages-send", "--as", "bot",
             "--chat-id", CHAT_ID, "--text",
             f"[{args.event_type}]\n{msg}"],
            check=False, timeout=15,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
