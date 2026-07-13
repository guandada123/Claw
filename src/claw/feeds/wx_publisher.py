"""微信早报/晚报推送到飞书群。

仅负责输出和推送逻辑，不依赖采集或组装模块。
"""

import json
import subprocess
import sys

FEISHU_CHAT_ID = "oc_9ee5303497f5e0e71666b610d6bdc346"


def push_to_feishu(report_text: str) -> None:
    """用 lark-cli (bot身份) 将报告推送到飞书群。

    自动分片，单条消息不超过 1800 字符。
    """
    max_len = 1800
    chunks = []
    current = ""
    for line in report_text.split("\n"):
        if len(current) + len(line) + 1 > max_len:
            chunks.append(current)
            current = line + "\n"
        else:
            current += line + "\n"
    if current:
        chunks.append(current)

    for i, chunk in enumerate(chunks):
        prefix = f"[第{i+1}/{len(chunks)}页]\n" if len(chunks) > 1 else ""
        content = prefix + chunk
        try:
            r = subprocess.run(
                ["lark-cli", "im", "+messages-send", "--as", "bot",
                 "--chat-id", FEISHU_CHAT_ID, "--text", content],
                capture_output=True, text=True, timeout=30,
            )
            result = json.loads(r.stdout)
            if result.get("ok"):
                print(f"  ✅ 飞书推送第{i+1}页成功", file=sys.stderr)
            else:
                print(f"  ⚠️ 飞书推送第{i+1}页失败: {result}", file=sys.stderr)
        except Exception as e:
            print(f"  ⚠️ 飞书推送异常: {e}", file=sys.stderr)


def print_report(report_text: str, push: bool = False) -> None:
    """打印报告到 stdout，可选推送飞书。

    注意：本函数默认【不推送飞书群】。直接 build_morning_report
    的输出是「公众号文章聚合」原始格式，并非最终早报标准格式
    （标准格式由自动化 prompt 定义的飞书文档 + 结构化群卡片生成）。
    若误推到群会造成格式混乱。如需推群，必须显式传 push=True。
    """
    if push:
        push_to_feishu(report_text)
