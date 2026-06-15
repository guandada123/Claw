#!/usr/bin/env python3
"""Claw 本地心跳/存活检测（Ollama 版）
======================================
取代原 AI Agent 驱动的「Claw 心跳存活检测」自动化，零成本运行。

改动概要
--------
- 保留原 heartbeat.py 全部检查（DB / 数据目录 / 核心脚本）
- 新增 Ollama 本地模型可用性检测
- 新增 Ollama 快速响应测试（验证本地模型可正常推理）
- 成本日志记录到 cost_tracker，模型标记为 qwen2.5-7b（¥0）
- 每小时节省约 ¥0.03（deepseek-v4-flash），每月节省约 ¥21.6

用法
----
    python3 scripts/heartbeat_local.py          # 写心跳 + 完整检查
    python3 scripts/heartbeat_local.py --check  # 仅检查，不写心跳
    python3 scripts/heartbeat_local.py --json   # JSON 格式输出

版本：v1.0 | 2026-06-14
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

# ── 路径 ───────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).parent.parent
HEARTBEAT_FILE = PROJECT_DIR / "data" / "heartbeat.json"
DB_PATH = Path.home() / ".workbuddy" / "workbuddy.db"

# ── Ollama 相关 ──────────────────────────────────────────────
OLLAMA_BASE = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b"

# 当执行 scripts/heartbeat_local.py 时，scripts/ 已自动在 sys.path 中
# 当被其他脚本 import 时，通过相对项目路径导入
try:
    import local_model
    _dbg = f"local_model loaded from: {local_model.__file__}"
except ImportError as e:
    sys.path.insert(0, str(PROJECT_DIR / "scripts"))
    import local_model
    _dbg = f"local_model loaded from (fallback): {local_model.__file__}"


# ============================================================
# 依赖检查（原 heartbeat.py 内容）
# ============================================================

def check_dependencies() -> dict:
    """检查关键系统依赖"""
    results = {"db": False, "data_dir": False, "scripts": False}

    # 1. WorkBuddy DB
    try:
        if DB_PATH.exists():
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute("SELECT 1 FROM automations LIMIT 1")
            conn.close()
            results["db"] = True
    except Exception:
        pass

    # 2. 数据目录
    data_dir = PROJECT_DIR / "data"
    results["data_dir"] = data_dir.is_dir()

    # 3. 核心脚本 (在 .workbuddy/scripts/ 下)
    scripts_dir = PROJECT_DIR / ".workbuddy" / "scripts"
    required = ["sim_trade.py", "market_data.py", "strategy_generator.py"]
    results["scripts"] = all((scripts_dir / s).exists() for s in required)

    return results


# ============================================================
# Ollama 检查（新增）
# ============================================================

def check_ollama() -> dict:
    """检查本地 Ollama 服务状态"""
    result = {
        "running": False,
        "model_available": False,
        "model_response_ok": False,
        "response_sample": "",
        "duration_ms": 0,
    }

    start = time.time()

    # Step 1: 判断服务是否在线
    if not local_model.is_available():
        result["duration_ms"] = int((time.time() - start) * 1000)
        return result

    result["running"] = True

    # Step 2: 检查模型是否安装
    models = local_model.list_models()
    installed = [m["name"] for m in models]
    if OLLAMA_MODEL not in installed and "qwen2.5:7b" not in installed:
        result["model_available"] = False
        result["duration_ms"] = int((time.time() - start) * 1000)
        return result

    result["model_available"] = True

    # Step 3: 快速推理测试（发一个极短的 prompt 验证可用性）
    test = local_model.call(
        prompt="OK",
        model=OLLAMA_MODEL,
        timeout=15,
    )
    result["duration_ms"] = int((time.time() - start) * 1000)

    if test["success"]:
        result["model_response_ok"] = True
        result["response_sample"] = test["response"][:60].strip()
        result["prompt_tokens"] = test.get("prompt_tokens", 0)
        result["response_tokens"] = test.get("response_tokens", 0)
    else:
        result["error"] = test.get("error", "unknown")

    return result


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Claw 本地心跳检测（Ollama 版）")
    parser.add_argument("--check", action="store_true", help="仅检查，不写心跳")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    args = parser.parse_args()

    # 1. 系统依赖检查
    deps = check_dependencies()
    system_healthy = all(deps.values())

    # 2. Ollama 检查
    ollama_status = check_ollama()
    local_healthy = ollama_status.get("running") and ollama_status.get("model_response_ok")

    # 3. 汇总判定
    healthy = system_healthy  # 系统健康是最低标准；Ollama 掉线不影响 Claw 核心功能
    now = datetime.now().isoformat(timespec="seconds")

    # 4. 构造结果
    result = {
        "last_heartbeat": now,
        "healthy": healthy,
        "local_model_available": local_healthy,
        "dependencies": deps,
        "ollama": {
            "running": ollama_status["running"],
            "model_available": ollama_status["model_available"],
            "response_ok": ollama_status["model_response_ok"],
            "sample": ollama_status.get("response_sample", ""),
            "tokens": {
                "prompt": ollama_status.get("prompt_tokens", 0),
                "response": ollama_status.get("response_tokens", 0),
            },
        },
    }

    # 5. 写心跳文件
    if not args.check:
        HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    # 6. 记录成本到 cost_tracker（标记为 qwen2.5-7b，¥0）
    # 非 JSON 模式下打印日志；JSON 模式下静默记录
    try:
        from cost_tracker import log_estimate
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()) if args.json else contextlib.nullcontext():
            log_estimate(
                "心跳检测",
                project="Claw",
                override_model="qwen2.5-7b",
                override_inp=ollama_status.get("prompt_tokens", 0),
                override_out=ollama_status.get("response_tokens", 0),
            )
    except Exception:
        pass  # cost_tracker 不是关键路径

    # 7. 输出
    if args.json:
        out = {
            "healthy": healthy,
            "local_model_available": local_healthy,
            "system_deps": deps,
            "ollama": ollama_status.get("running", False),
            "ollama_response": ollama_status.get("model_response_ok", False),
            "checked_at": now,
        }
        print(json.dumps(out, ensure_ascii=False))
    else:
        # ── 头部状态 ──
        status_icon = "✅" if healthy else "❌"
        local_icon = "🟢" if local_healthy else "⚪"
        print(f"\n{'=' * 50}")
        print(f"  Claw 本地心跳（Ollama 版）")
        print(f"{'=' * 50}")
        print(f"  {status_icon} 系统: {'健康' if healthy else '异常'}")
        print(f"  {local_icon} 本地模型: {'运行中' if local_healthy else '离线'}")
        print(f"  时间: {now}")
        print()

        # ── Ollama 详情 ──
        print(f"  🔍 Ollama 状态")
        print(f"  {'─' * 35}")
        print(f"    服务: {'✅ 运行中' if ollama_status.get('running') else '❌ 未启动'}")
        print(f"    模型: {'✅ ' + OLLAMA_MODEL if ollama_status.get('model_available') else '❌ 未安装'}")
        if ollama_status.get("model_response_ok"):
            print(f"    推理: ✅ {(ollama_status.get('duration_ms', 0))}ms")
            sample = ollama_status.get("response_sample", "")
            if sample:
                print(f"    响应: \"{sample}\"")
            print(f"    Tokens: inp={ollama_status.get('prompt_tokens', 0)} out={ollama_status.get('response_tokens', 0)}")
            print(f"    费用: ¥0.0000 (本地模型)")
        else:
            err = ollama_status.get("error", "unknown")
            print(f"    推理: ❌ {err[:80]}")

        # ── 依赖详情 ──
        print(f"\n  📦 系统依赖")
        print(f"  {'─' * 35}")
        for name, ok in deps.items():
            print(f"    {'✅' if ok else '❌'} {name}")

        print(f"\n{'=' * 50}")
        print(f"  心跳文件: {HEARTBEAT_FILE}")
        print(f"{'=' * 50}\n")

    sys.exit(0 if healthy else 1)


if __name__ == "__main__":
    main()
