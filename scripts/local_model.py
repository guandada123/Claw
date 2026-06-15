"""
local_model.py — Ollama 本地模型调用封装
========================================
适用：Claw / QTS 心跳、状态检查、简单格式化等零成本任务
模型：qwen2.5:7b（Mac Mini M4 16G 跑得飞起）
成本：¥0（免费! 免费! 免费!）
依赖：Ollama（localhost:11434）

版本：v1.0 | 2026-06-14
"""

import json
import time
import urllib.request
import urllib.error
from typing import Optional

OLLAMA_BASE = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:7b"
TIMEOUT = 30  # 默认超时（秒）

# is_available() 的 30 秒 TTL 缓存
_is_available_cache: bool | None = None
_is_available_cache_time: float = 0
_IS_AVAILABLE_CACHE_TTL = 30  # 秒


# ============================================================
# 可用性检测
# ============================================================

def is_available() -> bool:
    """检查 Ollama 服务是否在运行（30 秒 TTL 缓存）"""
    global _is_available_cache, _is_available_cache_time
    now = time.time()
    if _is_available_cache is not None and now - _is_available_cache_time < _IS_AVAILABLE_CACHE_TTL:
        return _is_available_cache

    try:
        req = urllib.request.Request(f"{OLLAMA_BASE}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            _is_available_cache = (resp.status == 200)
    except (urllib.error.URLError, ConnectionRefusedError, OSError):
        _is_available_cache = False
    _is_available_cache_time = time.time()
    return _is_available_cache


def list_models() -> list:
    """列出已安装的本地模型"""
    try:
        req = urllib.request.Request(f"{OLLAMA_BASE}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        models = []
        for m in data.get("models", []):
            name = m.get("name", "")
            size = m.get("size", 0)
            modified = m.get("modified_at", "")
            models.append({"name": name, "size_gb": round(size / 1e9, 1), "modified": modified})
        return models
    except (urllib.error.URLError, json.JSONDecodeError, KeyError, OSError):
        return []


def get_running_models() -> list:
    """列出当前正在运行的模型"""
    try:
        req = urllib.request.Request(f"{OLLAMA_BASE}/api/ps", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
        return [m.get("name", "") for m in data.get("models", [])]
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        return []


# ============================================================
# 核心调用
# ============================================================

def call(prompt: str, model: str = DEFAULT_MODEL,
         system: Optional[str] = None, timeout: int = TIMEOUT,
         stream: bool = False) -> dict:
    """
    调用本地 Ollama 模型（Generate API）。

    参数
    ----
    prompt : str
        用户输入
    model : str
        模型名称（默认 qwen2.5:7b）
    system : str, optional
        系统提示
    timeout : int
        超时秒数
    stream : bool
        是否流式（默认False）

    返回
    ----
    dict : {
        "success": bool,      是否成功
        "response": str|None, 模型输出
        "model": str,         实际使用的模型
        "prompt_tokens": int, 输入Token数
        "response_tokens": int, 输出Token数
        "cost_cny": 0.0,      永远免费！
        "duration_ms": int,   耗时（毫秒）
        "error": str|None,    错误信息
    }
    """
    if not is_available():
        return {
            "success": False,
            "response": None,
            "model": model,
            "prompt_tokens": 0,
            "response_tokens": 0,
            "cost_cny": 0.0,
            "duration_ms": 0,
            "error": "Ollama 服务未运行（http://localhost:11434）",
        }

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": stream,
        "options": {
            "temperature": 0.7,
            "num_predict": 1024,  # 本地模型不需要太多输出
        },
    }
    if system:
        payload["system"] = system

    start = time.time()
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{OLLAMA_BASE}/api/generate",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read())

        elapsed = int((time.time() - start) * 1000)

        return {
            "success": True,
            "response": result.get("response", ""),
            "model": result.get("model", model),
            "prompt_tokens": result.get("prompt_eval_count", 0),
            "response_tokens": result.get("eval_count", 0),
            "cost_cny": 0.0,
            "duration_ms": elapsed,
            "error": None,
        }

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {
            "success": False,
            "response": None,
            "model": model,
            "prompt_tokens": 0,
            "response_tokens": 0,
            "cost_cny": 0.0,
            "duration_ms": int((time.time() - start) * 1000),
            "error": f"HTTP {e.code}: {body[:200]}",
        }
    except urllib.error.URLError as e:
        return {
            "success": False,
            "response": None,
            "model": model,
            "prompt_tokens": 0,
            "response_tokens": 0,
            "cost_cny": 0.0,
            "duration_ms": int((time.time() - start) * 1000),
            "error": f"连接失败: {e.reason}",
        }
    except Exception as e:
        return {
            "success": False,
            "response": None,
            "model": model,
            "prompt_tokens": 0,
            "response_tokens": 0,
            "cost_cny": 0.0,
            "duration_ms": int((time.time() - start) * 1000),
            "error": str(e),
        }


def chat(messages: list, model: str = DEFAULT_MODEL,
         timeout: int = TIMEOUT) -> dict:
    """
    调用 Ollama Chat API（支持多轮对话）。

    参数
    ----
    messages : list
        [{"role": "user", "content": "..."}, ...]
    model : str
        模型名称
    timeout : int
        超时秒数

    返回
    ----
    dict
    """
    if not is_available():
        return {"success": False, "error": "Ollama 服务未运行"}

    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.7, "num_predict": 1024},
    }

    start = time.time()
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{OLLAMA_BASE}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read())

        return {
            "success": True,
            "response": result.get("message", {}).get("content", ""),
            "model": result.get("model", model),
            "prompt_tokens": result.get("prompt_eval_count", 0),
            "response_tokens": result.get("eval_count", 0),
            "cost_cny": 0.0,
            "duration_ms": int((time.time() - start) * 1000),
            "error": None,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "duration_ms": int((time.time() - start) * 1000),
        }


# ============================================================
# 简易稳定性测试
# ============================================================

def run_stability_test(n: int = 20) -> dict:
    """
    稳定性压测：连续调用 n 次，统计成功率。

    参数
    ----
    n : int
        调用次数（默认20）

    返回
    ----
    dict : { "success_rate", "total", "passed", "avg_duration_ms", "errors" }
    """
    if not is_available():
        print("❌ Ollama 未运行，无法执行稳定性测试")
        return {"success_rate": 0, "total": 0, "passed": 0}

    passed = 0
    durations = []
    errors = []

    print(f"\n{'=' * 50}")
    print(f"  Ollama 稳定性测试（{n} 次连续调用）")
    print(f"{'=' * 50}")

    for i in range(1, n + 1):
        result = call(f'reply with OK for test #{i}', timeout=15)
        status = "✅" if result["success"] else "❌"
        dur = result.get("duration_ms", 0)
        print(f"  {status} 第{i:>2}/{n}次  {dur}ms", end="")
        if result["success"]:
            passed += 1
            durations.append(dur)
            print(f"  Tokens: in={result['prompt_tokens']} out={result['response_tokens']}")
        else:
            errors.append(result.get("error", ""))
            print(f"  Error: {result.get('error', '')[:60]}")

    rate = passed / n * 100
    avg_dur = sum(durations) / len(durations) if durations else 0
    print(f"\n{'=' * 50}")
    print(f"  结果：{passed}/{n} 通过 ({rate:.0f}%)")
    print(f"  平均耗时：{avg_dur:.0f}ms")
    print(f"  达标：{'✅' if rate >= 95 else '❌'} (目标≥95%)")
    print(f"{'=' * 50}\n")

    return {
        "success_rate": rate,
        "total": n,
        "passed": passed,
        "avg_duration_ms": avg_dur,
        "errors": errors,
    }


# ============================================================
# CLI 入口
# ============================================================

if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "status":
        avail = is_available()
        print(f"\n🔍 Ollama 本地模型状态")
        print(f"{'=' * 35}")
        print(f"  服务运行中: {'✅ 是' if avail else '❌ 否'}")
        if avail:
            models = list_models()
            print(f"  已安装模型:")
            for m in models:
                print(f"    • {m['name']:>15}  {m['size_gb']}GB")
            running = get_running_models()
            if running:
                print(f"  正在运行: {', '.join(running)}")

    elif cmd == "test":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        run_stability_test(n)

    elif cmd == "call":
        prompt = sys.argv[2] if len(sys.argv) > 2 else "你好，回复OK即可"
        result = call(prompt)
        if result["success"]:
            print(f"\n  ✅ 响应 ({result['duration_ms']}ms)")
            print(f"  {result['response'][:500]}")
            print(f"\n  输入Token: {result['prompt_tokens']}")
            print(f"  输出Token: {result['response_tokens']}")
            print(f"  费用: ¥{result['cost_cny']}")
        else:
            print(f"\n  ❌ 失败: {result.get('error')}")

    else:
        print("用法: python local_model.py [status|test [N]|call '<prompt>']")
