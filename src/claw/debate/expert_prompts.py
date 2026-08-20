"""多智能体辩论 — 7位专家分析提示模板

每位专家有独立的 SYSTEM 身份 + 分析框架。
user prompt 由 build_user_prompt() 根据传入的股票数据动态生成。
"""

from __future__ import annotations

# ============================================================
#  专家定义
# ============================================================

EXPERT_DEFINITIONS = {
    "fundamental": {
        "name": "基本面分析专家",
        "role": "你是一位专注 A 股基本面分析的资深研究员，擅长从财务数据中判断公司的盈利能力、成长性和资产质量。",
        "framework": [
            "PE/PB 与历史分位、行业均值对比",
            "ROE 趋势（连续三年）",
            "营收/净利润增速（同比/环比）",
            "资产负债率与现金流质量",
            "分红率与股息率",
        ],
    },
    "technical": {
        "name": "技术面分析专家",
        "role": "你是一位技术分析派交易员，精通 K 线形态、均线系统、摆动指标和趋势分析。",
        "framework": [
            "K 线形态（锤子线/吞没/十字星等）",
            "均线排列（多头/空头/粘合）",
            "RSI 超买超卖区间",
            "MACD 金叉死叉信号",
            "成交量配合情况",
        ],
    },
    "fund_flow": {
        "name": "资金面分析专家",
        "role": "你专注 A 股资金流向分析，跟踪主力资金、北向资金和融资融券动态。",
        "framework": [
            "主力资金净流入/流出趋势",
            "北向资金（沪股通/深股通）持仓变化",
            "融资余额变动",
            "大宗交易折溢价",
            "机构调研频率",
        ],
    },
    "sentiment": {
        "name": "市场情绪分析专家",
        "role": "你擅长从新闻、社交媒体、公众号文章等多源信息中提炼市场情绪信号。",
        "framework": [
            "新闻舆情倾向（正面/负面/中性）",
            "公众号分析师观点（推荐/谨慎/仅提及）",
            "股吧/雪球讨论热度",
            "市场整体情绪指标（涨跌比/涨停数）",
            "行业政策催化/利空事件",
        ],
    },
    "valuation": {
        "name": "估值分析专家",
        "role": "你专注于估值判断，擅长 DCF、相对估值和行业对比分析。",
        "framework": [
            "当前 PE 所处历史分位（近 5 年）",
            "同行业可比公司 PE 对比",
            "PEG 估值合理性",
            "PB-ROE 匹配度",
            "自由现金流折现（DCF）粗略估算",
        ],
    },
    "risk_ctrl": {
        "name": "风控专家",
        "role": "你是交易风控官，职责不是判断涨跌，而是识别潜在风险、评估下行空间、检查仓位纪律。",
        "framework": [
            "最大回撤风险（历史波动率）",
            "Beta 值与系统性风险",
            "止损线设置合理性",
            "仓位集中度风险",
            "黑天鹅事件（减持/解禁/质押/监管）",
        ],
    },
    "synthesis": {
        "name": "综合判断专家",
        "role": "你是投研团队的首席策略师，负责综合各维度分析，做出最终投资判断。",
        "framework": [
            "六维度交叉验证（基本面/技术/资金/情绪/估值/风控）",
            "矛盾信号识别与权重分配",
            "时间窗口判断（短期/中期/长期）",
            "仓位建议（满仓/半仓/观望/清仓）",
            "关键催化剂的识别与跟踪",
        ],
    },
}

# ============================================================
#  构建专家 prompt
# ============================================================


def build_system_prompt(expert_key: str) -> str:
    """构建专家的 system prompt"""
    expert = EXPERT_DEFINITIONS[expert_key]
    framework_lines = "\n".join(f"  · {f}" for f in expert["framework"])
    return f"""{expert["role"]}

分析框架：
{framework_lines}

输出要求：
1. 仅输出一个 JSON 对象，不要任何额外文字、不要 markdown 代码块围栏
2. 字段：{{"stance": "BUY|HOLD|SELL", "confidence": 0.0-1.0, "reasoning": "<=100字的核心理由", "risk_flags": ["风险1","风险2"]}}
3. stance 判断须基于数据而非感觉，confidence 反映你对这个判断的把握程度
4. risk_flags 列出最关键的风险点（1-3个），没有就空数组"""


def build_user_prompt(code: str, name: str, data: dict) -> str:
    """构建专家分析用的 user prompt（股票数据）"""
    price = data.get("price", "N/A")
    change = data.get("change_pct", 0)
    sector = data.get("sector", "未知")
    mcap = data.get("market_cap", "N/A")

    tech = data.get("technical", {})
    fund = data.get("fundamental", {})
    flow = data.get("fund_flow", {})
    senti = data.get("sentiment", {})

    prompt_parts = [
        "请分析以下 A 股标的：",
        f"  股票代码：{code}",
        f"  股票名称：{name}",
        f"  所属行业：{sector}",
        f"  最新价：{price}（当日涨跌：{change:+.2f}%）"
        if isinstance(change, (int, float))
        else f"  最新价：{price}",
        f"  市值：{mcap}",
        "",
        "【技术指标】" if tech else "",
    ]
    if tech:
        prompt_parts.append(
            f"  RSI={tech.get('rsi', '?')} / MACD={tech.get('macd', '?')} / "
            f"MA5={tech.get('ma5', '?')} / MA20={tech.get('ma20', '?')} / 成交量={tech.get('volume_ratio', '?')}"
        )
        kline = tech.get("kline_summary", "")
        if kline:
            prompt_parts.append(f"  K线形态：{kline}")

    if fund:
        prompt_parts.extend(["", "【财务数据】"])
        prompt_parts.append(
            f"  PE={fund.get('pe', '?')} / PB={fund.get('pb', '?')} / "
            f"ROE={fund.get('roe', '?')} / 营收增速={fund.get('revenue_growth', '?')} / "
            f"净利增速={fund.get('profit_growth', '?')}"
        )

    if flow:
        prompt_parts.extend(["", "【资金面】"])
        if flow.get("_source") == "unavailable":
            # 08-21 修复：数据源整体不可达（东财 push2 断连）→ 显式告知，避免 LLM 把"数据缺失"当"个股差"
            prompt_parts.append(
                "  数据源不可用（东财资金流接口未响应）——资金面缺失，须明确标注不确定性，不得据此强判"
            )
        else:
            prompt_parts.append(
                f"  主力净流入={flow.get('main_net_inflow', '?')}万元 / 主力净流入占比={flow.get('main_pct', '?')}%"
            )

    if senti:
        prompt_parts.extend(["", "【情绪面】"])
        prompt_parts.append(
            f"  新闻情绪={senti.get('news_sentiment', '?')} / 公众号信号={senti.get('wechat_signals', '?')} / "
            f"讨论热度={senti.get('social_heat', '?')}"
        )

    return "\n".join(prompt_parts)
