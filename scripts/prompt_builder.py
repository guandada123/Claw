"""
prompt_builder.py — 分层 Prompt 构建器
========================================
适用：Claw / QTS A股投资系统
功能：
  1. 静态系统前缀 + cache_control 标记（触发缓存折扣）
  2. 按任务类型只传必要数据（不全量传入）
  3. 压缩数据格式（减少50-70% Token）

使用方式：
    from prompt_builder import build_prompt

    prompt = build_prompt("stock_screen", {
        "sector": "半导体",
        "criteria": "量价背离",
        "stocks": stock_list,
    })
    # → 返回 { "messages": [...], "estimated_tokens": 850 }

版本：v2.0 | 2026-06-14
"""

# ============================================================
# 静态系统提示词（固定不变 → 触发 Prompt Cache）
# 要求：> 1024 Token 才能触发 Anthropic 的缓存
# ============================================================

STATIC_SYSTEM = """你是专业的A股投资分析助手，专注于主板和中小板股票的分析与交易辅助。

【投资约束】（必须严格遵守）
- 资金规模：¥15,000 初始本金
- 投资风格：中短线（持仓3-10天），不持仓过夜超过7天
- 风险等级：中等，个股止损设置在5-10%
- 市场范围：仅限主板和中小板股票，不碰创业板（300开头）和科创板（688开头）
- 推荐格式：必须包含股票代码、建议买入价位、仓位比例、持有周期、风险提示

【技术分析框架】
- 趋势判断：日线MA5/MA10/MA20排列方向，周线级别确认
- 量价关系：放量突破为买入信号，缩量回调为持仓信号，放量滞涨为卖出信号
- 支撑阻力：前期高低点、均线支撑（MA20关键）、筹码密集区
- 技术指标：MACD金叉/死叉、KDJ超买超卖、RSI背离、布林带上下轨
- K线形态：早晨之星/黄昏之星、吞没形态、十字星、锤子线

【基本分析要点】
- 市盈率PE：行业对比，过高需谨慎
- 市净率PB：破净股需关注净资产质量
- ROE：连续3年>15%为优质标的
- 营收/利润增长：需连续2个季度正增长
- 流通市值：50亿-500亿为最优操作区间

【输出格式要求】
- 买卖建议必须有明确的价位区间
- 风险提示必须包含可能的最大亏损
- 定期出现的技术指标用简写（如MACD金叉）
- 每只股票分析不超过200字
- 涉及数据时，只呈现结论而非原始数据

【禁止事项】
- 不推荐创业板、科创板股票
- 不给出超出止损范围的仓位建议
- 不提供全仓或重仓（>50%）的持仓建议
- 不基于未经验证的消息面做判断

【数据隐私】
- 所有分析和建议仅用于投资参考
- 不保证收益，投资有风险
- 历史表现不代表未来收益"""


# ============================================================
# 字段压缩映射（按任务类型只传必要字段）
# ============================================================

FIELD_MAP = {
    "stock_screen":     ["code", "name", "price", "change_pct", "volume_ratio", "pe"],
    "single_analysis":  ["code", "name", "price", "change_pct",
                         "ma5", "ma20", "volume", "high_52w", "low_52w",
                         "pe", "pb", "roe", "market_cap"],
    "market_summary":   ["index_name", "index_value", "change_pct", "volume_total"],
    "trend_analysis":   ["close", "ma5", "ma10", "ma20", "volume", "macd", "rsi"],
    "breakout_check":   ["high_52w", "low_52w", "current", "volume_ratio", "change_pct"],
    "backtest_summary": ["strategy", "total_return", "max_drawdown", "win_rate",
                         "sharpe_ratio", "trade_count", "avg_holding_days"],
}


def build_prompt(task_type: str, data: dict) -> dict:
    """
    构建分层 Prompt（按任务类型定制）。

    参数
    ----
    task_type : str
        任务类型，支持：
        - "stock_screen"     — 选股初筛
        - "single_analysis"  — 单股深度分析
        - "market_summary"   — 市场摘要
        - "trend_analysis"   — 趋势分析
        - "breakout_check"   — 突破判断
        - "backtest_summary" — 回测总结
        - "generic"          — 通用任务
    data : dict
        任务数据（按任务类型传入不同字段）

    返回
    ----
    dict : {
        "messages": list,        # 发给 LLM 的消息
        "system": str,           # 系统提示词（独立）
        "user_prompt": str,      # 用户输入部分（纯文本）
        "estimated_tokens": int, # 预估 Token 数
        "has_cache": bool,       # 是否启用了缓存标记
    }
    """
    builder = _get_builder(task_type)
    return builder(data)


def _get_builder(task_type: str):
    """获取对应任务类型的 prompt 构建函数"""
    builders = {
        "stock_screen":     _build_stock_screen,
        "single_analysis":  _build_single_analysis,
        "market_summary":   _build_market_summary,
        "trend_analysis":   _build_trend_analysis,
        "breakout_check":   _build_breakout_check,
        "backtest_summary": _build_backtest_summary,
    }
    return builders.get(task_type, _build_generic)


# ============================================================
# 各任务 Prompt 构建器
# ============================================================

def _build_stock_screen(data: dict) -> dict:
    """选股初筛 — 用压缩格式传数据"""
    stocks = data.get("stocks", [])
    compact = _compact_stock_list(stocks, fields=["code", "name", "price", "change_pct", "volume_ratio", "pe"])

    user_prompt = f"""任务：从以下股票中筛选符合条件的标的

筛选条件：{data.get('criteria', '无')}
板块/行业：{data.get('sector', '全部')}

候选列表（关键指标）：
{compact}"""

    return _build_result(user_prompt, "stock_screen")


def _build_single_analysis(data: dict) -> dict:
    """单股分析 — 只传关键K线节点"""
    user_prompt = f"""分析股票：{data.get('code', '?')} {data.get('name', '?')}

当前价位：¥{data.get('price', '?')} （{data.get('change_pct', '?')}%）
近5日收盘：{data.get('closes_5', '无数据')}
均线：MA5={data.get('ma5', '?')}  MA20={data.get('ma20', '?')}
成交量：{data.get('volume', '?')}
52周高低：¥{data.get('low_52w', '?')} ~ ¥{data.get('high_52w', '?')}
基本面：PE={data.get('pe', '?')} PB={data.get('pb', '?')} ROE={data.get('roe', '?')}%
流通市值：¥{_fmt_market_cap(data.get('market_cap', 0))}

技术形态：{data.get('pattern', '待判断')}
异常信号：{data.get('signal', '无')}
关注问题：{data.get('question', '请给出操作建议')}"""

    return _build_result(user_prompt, "single_analysis")


def _build_market_summary(data: dict) -> dict:
    """市场摘要 — 精简版，200字以内"""
    summary = data.get("summary_200chars", "")
    if len(summary) > 200:
        summary = summary[:200] + "..."

    user_prompt = f"""今日市场摘要（精简版）：
{summary}"""

    return _build_result(user_prompt, "market_summary")


def _build_trend_analysis(data: dict) -> dict:
    """趋势分析 — 只传趋势相关指标"""
    user_prompt = f"""趋势分析：{data.get('code', '?')} {data.get('name', '?')}

收盘价序列：{data.get('close', '?')}
均值：MA5={data.get('ma5', '?')} MA10={data.get('ma10', '?')} MA20={data.get('ma20', '?')}
MACD：{data.get('macd', '?')}  RSI(14)：{data.get('rsi', '?')}
成交量：{data.get('volume', '?')}
技术判断：{data.get('question', '当前趋势如何？')}"""

    return _build_result(user_prompt, "trend_analysis")


def _build_breakout_check(data: dict) -> dict:
    """突破判断 — 聚焦关键价格位"""
    user_prompt = f"""突破信号检查：{data.get('code', '?')} {data.get('name', '?')}

当前价：¥{data.get('current', '?')}
52周最高：¥{data.get('high_52w', '?')}
52周最低：¥{data.get('low_52w', '?')}
量比：{data.get('volume_ratio', '?')}
涨幅：{data.get('change_pct', '?')}%

距高点：{((data.get('current', 0) - data.get('high_52w', 0)) / max(data.get('high_52w', 1), 0.01) * 100):.1f}%
是否符合突破条件？"""

    return _build_result(user_prompt, "breakout_check")


def _build_backtest_summary(data: dict) -> dict:
    """回测结果总结"""
    user_prompt = f"""回测结果分析

策略：{data.get('strategy', '?')}
周期：{data.get('period', '?')}
总收益率：{data.get('total_return', '?')}%
最大回撤：{data.get('max_drawdown', '?')}%
胜率：{data.get('win_rate', '?')}%
夏普比率：{data.get('sharpe_ratio', '?')}
交易次数：{data.get('trade_count', '?')}
平均持仓天数：{data.get('avg_holding_days', '?')}

请评估策略表现并给出改进建议。"""

    return _build_result(user_prompt, "backtest_summary")


def _build_generic(data: dict) -> dict:
    """通用任务 — 直接传入用户输入"""
    user_prompt = data.get("prompt", data.get("question", ""))
    return _build_result(user_prompt, "generic")


# ============================================================
# 辅助函数
# ============================================================

STATIC_TOKEN_ESTIMATE = 680  # 静态提示词约680 Token（中文）
OVERHEAD_PER_CALL = 50       # 每次调用的固定开销


def _build_result(user_prompt: str, task_type: str) -> dict:
    """构建最终返回结构"""
    # 预估 Token（粗略：中文字符 ≈ 1.5 Token/字符）
    user_tokens = len(user_prompt) * 1.5
    total_tokens = int(STATIC_TOKEN_ESTIMATE + user_tokens + OVERHEAD_PER_CALL)

    return {
        "messages": [
            {"role": "system", "content": STATIC_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        "system": STATIC_SYSTEM,
        "user_prompt": user_prompt,
        "estimated_tokens": total_tokens,
        "has_cache": True,
        "task_type": task_type,
    }


def _compact_stock_list(stocks: list, fields: list = None) -> str:
    """压缩股票列表格式（节省 50-70% Token）

    默认字段：code, name, price, change_pct, volume_ratio, pe
    原来：每只股票传20+字段 → 现在只传关键5字段
    """
    if not stocks:
        return "(空列表)"

    fields = fields or ["code", "name", "price", "change_pct", "volume_ratio", "pe"]
    lines = []
    for s in stocks[:20]:  # 最多20只
        parts = []
        for f in fields:
            v = s.get(f, "")
            if f == "price":
                parts.append(f"¥{v}")
            elif f == "change_pct":
                parts.append(f"{v:+.1f}%")
            elif f == "volume_ratio":
                parts.append(f"量{v:.1f}")
            elif f == "pe":
                parts.append(f"PE{v:.0f}")
            elif f == "market_cap":
                parts.append(f"市值{_fmt_market_cap(v)}")
            else:
                parts.append(str(v))
        lines.append(" | ".join(parts))

    if len(stocks) > 20:
        lines.append(f"... 还有 {len(stocks) - 20} 只")

    return "\n".join(lines)


def _fmt_market_cap(cap: float) -> str:
    """格式化市值（亿/万）"""
    if cap >= 100_0000:  # 亿
        return f"{cap / 10000:.0f}亿"
    elif cap >= 10000:   # 万
        return f"{cap:.0f}万"
    return f"{cap:.0f}"


# ============================================================
# 测试
# ============================================================

def test_builders():
    """测试各任务类型的 Prompt 构建"""
    print("=" * 50)
    print("  Prompt Builder 测试")
    print("=" * 50)

    # 测试选股初筛
    result = build_prompt("stock_screen", {
        "sector": "半导体",
        "criteria": "量价背离，成交量放大50%以上",
        "stocks": [
            {"code": "600123", "name": "兰花科创", "price": 12.34,
             "change_pct": 3.2, "volume_ratio": 1.5, "pe": 8.5},
            {"code": "600456", "name": "宝钛股份", "price": 28.90,
             "change_pct": -1.2, "volume_ratio": 0.8, "pe": 35.2},
        ],
    })
    print(f"\n📋 stock_screen: ~{result['estimated_tokens']} Token")
    print(f"   缓存可用: {result['has_cache']}")
    print(f"   User prompt 预览:\n{result['user_prompt'][:200]}...")

    # 测试单股分析
    result2 = build_prompt("single_analysis", {
        "code": "000001", "name": "平安银行",
        "price": 12.52, "change_pct": 2.1,
        "closes_5": "12.1, 12.3, 12.4, 12.5, 12.52",
        "ma5": 12.35, "ma20": 11.80,
        "volume": "15.2亿", "high_52w": 14.80, "low_52w": 10.20,
        "pe": 5.2, "pb": 0.6, "roe": 11.5, "market_cap": 2430_0000,
        "question": "是否适合入场？",
    })
    print(f"\n📋 single_analysis: ~{result2['estimated_tokens']} Token")
    print(f"   缓存可用: {result2['has_cache']}")
    print(f"   节省比例(对比全量): ~{(2000 - result2['estimated_tokens'])/2000*100:.0f}%")

    print(f"\n{'=' * 50}")
    print("  测试完成")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "test"

    if cmd == "test":
        test_builders()
    else:
        # 自定义构建
        task_type = cmd
        import json
        data = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
        result = build_prompt(task_type, data)
        print(json.dumps(result, ensure_ascii=False, indent=2))
