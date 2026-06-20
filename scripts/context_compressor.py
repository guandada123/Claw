"""
context_compressor.py — 上下文压缩工具
========================================
适用：Claw / QTS A股投资系统
功能：
  1. 历史对话压缩（只保留最近2轮 + 摘要）
  2. 行情数据按需筛选字段
  3. 大任务分批处理
  4. 按关键词只加载相关代码片段

版本：v2.0 | 2026-06-14
"""

import ast
from pathlib import Path

# ============================================================
# 配置
# ============================================================
MAX_HISTORY_ROUNDS = 2  # 保留最近 N 轮完整对话
MAX_HISTORY_CHARS = 800  # 历史摘要最大字符数
MAX_DATA_CHARS = 600  # 行情数据最大字符数
MAX_CODE_TOKENS = 1200  # 代码片段最大 Token 数
MAX_CHUNK_SIZE = 10  # 分批每批最大数量


# ============================================================
# 1. 历史对话压缩
# ============================================================


def compress_conversation_history(history: list[dict]) -> list[dict]:
    """
    压缩对话历史。
    - 最近 MAX_HISTORY_ROUNDS 轮完整保留
    - 前面的内容摘要化为一条 system 消息

    参数
    ----
    history : list[dict]
        [{ "role": str, "content": str }, ...]

    返回
    ----
    list[dict] : 压缩后的历史
    """
    if not history or len(history) <= MAX_HISTORY_ROUNDS * 2:
        return history or []

    # 分离新旧
    cutoff = MAX_HISTORY_ROUNDS * 2
    old = history[:-cutoff]
    recent = history[-cutoff:]

    # 摘要旧对话
    summary = _summarize_history(old)

    # 如果摘要有意义，插入到最前面
    if summary:
        return [{"role": "system", "content": f"[前{len(old)}轮对话摘要] {summary}"}] + recent
    return recent


def _summarize_history(messages: list[dict]) -> str:
    """提取旧对话的关键结论"""
    if not messages:
        return ""

    # 提取最后几条 assistant 消息中的结论性内容
    conclusions = []
    for msg in messages[-4:]:  # 只取最后4条
        if msg.get("role") == "assistant":
            content = msg.get("content", "")
            # 提取关键行（结论、数字、建议等）
            lines = content.split("\n")
            for line in lines:
                line = line.strip()
                if any(
                    kw in line
                    for kw in [
                        "建议",
                        "结论",
                        "结果",
                        "推荐",
                        "买入",
                        "卖出",
                        "持有",
                        "止损",
                        "目标价",
                        "✅",
                        "⚠️",
                        "总结",
                    ]
                ):
                    conclusions.append(line[:100])  # 每行限100字符

    summary = " | ".join(conclusions[:5])  # 最多5条结论
    if len(summary) > MAX_HISTORY_CHARS:
        summary = summary[:MAX_HISTORY_CHARS] + "..."

    return summary


# ============================================================
# 2. 行情数据按需筛选
# ============================================================

FIELD_MAP = {
    "trend_analysis": ["close", "ma5", "ma10", "ma20", "volume", "macd", "rsi"],
    "breakout_check": ["high_52w", "low_52w", "current", "volume_ratio", "change_pct"],
    "sentiment": [
        "northbound_flow",
        "sector_leader",
        "limit_up_count",
        "limit_down_count",
        "market_breadth",
    ],
    "entry_point": [
        "support",
        "resistance",
        "rsi",
        "macd_signal",
        "volume_breakout",
        "capital_flow",
    ],
    "fundamental": [
        "pe",
        "pb",
        "roe",
        "eps",
        "revenue_growth",
        "profit_growth",
        "debt_ratio",
        "market_cap",
    ],
    "quick_overview": ["price", "change_pct", "volume", "pe", "market_cap"],
}


def compress_market_data(raw_data: dict, task_focus: str = "quick_overview") -> str:
    """
    根据任务重点，只传相关数据字段。

    参数
    ----
    raw_data : dict
        原始行情数据
    task_focus : str
        任务重点（见 FIELD_MAP 的 key）

    返回
    ----
    str : 压缩后的数据字符串
    """
    fields = FIELD_MAP.get(task_focus, FIELD_MAP["quick_overview"])
    filtered = {}

    for k, v in raw_data.items():
        key_lower = k.lower()
        # 检查是否在需要的字段中
        if any(f.lower() in key_lower or key_lower in f.lower() for f in fields):
            # 数字精度压缩
            if isinstance(v, float):
                if (
                    "price" in key_lower
                    or "close" in key_lower
                    or "pct" in key_lower
                    or "ratio" in key_lower
                ):
                    filtered[k] = round(v, 2)
                elif "volume" in key_lower or "cap" in key_lower:
                    filtered[k] = _compress_number(v)
                else:
                    filtered[k] = round(v, 2)
            else:
                filtered[k] = v

    result = str(filtered)
    if len(result) > MAX_DATA_CHARS:
        result = result[:MAX_DATA_CHARS] + "..."

    return result


def _compress_number(n: float) -> str:
    """压缩大数字显示"""
    if abs(n) >= 1_0000_0000:  # 亿
        return f"{n / 1_0000_0000:.2f}亿"
    elif abs(n) >= 1_0000:  # 万
        return f"{n / 1_0000:.2f}万"
    return str(round(n, 2))


# ============================================================
# 3. 大任务分批处理
# ============================================================


def chunk_large_task(data_list: list, chunk_size: int = None) -> list:
    """
    大任务分批 —— 避免超大 Context。

    参数
    ----
    data_list : list
        待处理的数据列表
    chunk_size : int, optional
        每批最大数量

    返回
    ----
    list[list] : 分批后的列表
    """
    if not data_list:
        return [[]]

    chunk_size = chunk_size or MAX_CHUNK_SIZE
    return [data_list[i : i + chunk_size] for i in range(0, len(data_list), chunk_size)]


def estimate_chunk_count(total_items: int, chunk_size: int = None) -> int:
    """估算需要多少批"""
    chunk_size = chunk_size or MAX_CHUNK_SIZE
    return (total_items + chunk_size - 1) // chunk_size


# ============================================================
# 4. 代码按需加载（开发场景）
# ============================================================


def load_relevant_code(question: str, project_root: str) -> str:
    """
    根据问题关键词，只加载相关代码文件和函数。
    而不是把整个项目代码塞入 Prompt。

    参数
    ----
    question : str
        用户问题（用于提取关键词）
    project_root : str
        项目根目录

    返回
    ----
    str : 相关代码片段（最多 MAX_CODE_TOKENS Token）
    """
    keywords = _extract_keywords(question)
    if not keywords:
        return ""

    project_path = Path(project_root).expanduser()
    relevant_snippets = []

    for py_file in sorted(project_path.rglob("*.py")):
        # 跳过 venv、__pycache__、node_modules
        if any(
            p in str(py_file)
            for p in ["__pycache__", "venv", ".venv", "node_modules", ".git", "dist", "build"]
        ):
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        # 检查文件是否相关
        if any(kw in content.lower() for kw in keywords):
            snippets = _extract_relevant_functions(content, keywords)
            relevant_snippets.extend(snippets)

    if not relevant_snippets:
        return f"# 未找到与关键词 {keywords} 相关的代码"

    # 限制总量
    total = "\n\n".join(relevant_snippets)
    token_estimate = len(total) * 0.4  # 代码 Token 估算是字符数的 0.4 倍
    if token_estimate > MAX_CODE_TOKENS:
        # 截断到 Token 限制
        char_limit = int(MAX_CODE_TOKENS / 0.4)
        total = total[:char_limit] + "\n# ... (截断)"

    return total


def _extract_keywords(question: str) -> list[str]:
    """从问题中提取关键词"""
    # 移除常见无意义词
    stop_words = {
        "我",
        "你",
        "的",
        "了",
        "是",
        "在",
        "有",
        "和",
        "就",
        "不",
        "人",
        "都",
        "一",
        "一个",
        "上",
        "也",
        "很",
        "到",
        "说",
        "要",
        "去",
        "会",
        "着",
        "没有",
        "看",
        "好",
        "自己",
        "这",
        "那",
        "吗",
        "吧",
        "啊",
        "呢",
    }

    # 分词（简单按字拆分 + 提取2-4字词）
    import re as _re

    words = _re.findall(r"[\u4e00-\u9fff]{2,}", question)

    # 过滤停用词，保留有意义的业务词
    business_keywords = [w for w in words if w not in stop_words and len(w) >= 2]

    # 加入英文关键词
    eng_words = _re.findall(r"[a-zA-Z_]{3,}", question)
    business_keywords.extend(eng_words)

    return list(set(business_keywords))


def _extract_relevant_functions(code: str, keywords: list[str]) -> list[str]:
    """提取包含关键词的函数，不传无关代码"""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        # 无法解析的文件，整文件返回
        if any(kw in code.lower() for kw in keywords):
            return [code[:500]]
        return []

    relevant = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # 获取函数源码
            func_lines = code.split("\n")[node.lineno - 1 : node.end_lineno]
            func_code = "\n".join(func_lines)

            # 检查函数名或内容是否有关键词
            func_text = f"{node.name} {func_code}".lower()
            if any(kw in func_text for kw in keywords):
                # 限制函数大小
                if len(func_code) > 500:
                    func_code = func_code[:500] + "\n    # ... (截断)"
                relevant.append(
                    f"# {py_file.name if 'py_file' in dir() else ''} → {node.name}\n{func_code}"  # noqa: F821
                )

        elif isinstance(node, ast.ClassDef):
            # 检查类名
            class_name = node.name.lower()
            if any(kw in class_name for kw in keywords):
                class_lines = code.split("\n")[node.lineno - 1 : node.end_lineno]
                class_code = "\n".join(class_lines[:20])  # 只取类定义的前20行
                if len(class_code) > 500:
                    class_code = class_code[:500]
                relevant.append(f"# 类定义: {node.name}\n{class_code}")

    return relevant


# ============================================================
# 测试
# ============================================================


def test_compressor():
    """测试上下文压缩"""
    print("=" * 50)
    print("  Context Compressor 测试")
    print("=" * 50)

    # 测试历史压缩
    history = [
        {"role": "user", "content": "帮我分析600123"},
        {"role": "assistant", "content": "结论：短期看多，建议持有"},
        {"role": "user", "content": "止损位设在哪里？"},
        {"role": "assistant", "content": "建议止损设在10.5，目标价12.8"},
        {"role": "user", "content": "今天有什么操作建议？"},
        {"role": "assistant", "content": "建议买入，目标价15元"},
    ]
    compressed = compress_conversation_history(history)
    print("\n📋 历史压缩:")
    print(f"   原始 {len(history)} 条 → 压缩后 {len(compressed)} 条")
    for m in compressed:
        print(f"   [{m['role']}] {m['content'][:60]}...")

    # 测试数据压缩
    raw = {
        "code": "600123",
        "name": "兰花科创",
        "price": 12.3456,
        "change_pct": 3.256,
        "volume": 152000000,
        "volume_ratio": 1.56,
        "high_52w": 15.80,
        "low_52w": 9.50,
        "ma5": 11.95,
        "ma20": 11.20,
        "macd": 0.35,
        "rsi": 62.5,
        "pe": 8.5,
        "pb": 0.9,
        "market_cap": 5432000000,
    }
    compressed_data = compress_market_data(raw, "trend_analysis")
    print("\n📋 数据压缩:")
    print(f"   原始 {len(str(raw))} 字符 → {len(compressed_data)} 字符")
    print(f"   输出: {compressed_data[:150]}...")

    # 测试分批
    batch = chunk_large_task(list(range(55)), 10)
    print("\n📋 分批处理:")
    print(f"   55 项 → {len(batch)} 批 (每批最多10项)")

    print(f"\n{'=' * 50}")
    print("  测试完成")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "test"

    if cmd == "test":
        test_compressor()
    elif cmd == "load_code":
        question = sys.argv[2] if len(sys.argv) > 2 else "风控"
        root = sys.argv[3] if len(sys.argv) > 3 else str(Path.home() / "WorkBuddy/Claw")
        code = load_relevant_code(question, root)
        print(code[:500])
        print(f"\n... (共 {len(code)} 字符)")
    else:
        print("用法: python context_compressor.py [test|load_code '<question>' [<root>]]")
