#!/usr/bin/env python3
"""
verify_prompt_cache.py — DeepSeek Prompt Cache 命中率验证工具
==================================================================
验证优化后的 prompt 结构在实际 DeepSeek API 调用中的缓存命中率。

用法：
  python3 verify_prompt_cache.py [--api-key KEY] [--base-url URL] [--rounds N]

环境变量：
  DEEPSEEK_API_KEY          DeepSeek API key（优先级高于 --api-key）
  DEEPSEEK_BASE_URL         API 端点（默认 https://api.deepseek.com/v1）

输出：
  - 控制台报告（支持 --json 输出）
  - data/prompt_cache_report.json — 详细追踪记录

版本: v1.0 | 2026-06-14
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# ============================================================
# 配置
# ============================================================
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_ROUNDS = 5  # 每组测试的重复次数
REQUEST_TIMEOUT = 30  # 单次请求超时（秒）

# claude-opus-4 使用 catrouter/crazyrouter 时通过 base_url 切换
# 支持的 DeepSeek 模型
DEEPSEEK_MODELS = {
    "deepseek-v4-flash": {"input_price": 0.5, "output_price": 1.5},  # ¥/万Token
    "deepseek-v4-pro": {"input_price": 4.0, "output_price": 12.0},  # ¥/万Token
    "deepseek-reasoner": {"input_price": 8.0, "output_price": 24.0},  # ¥/万Token
}

# ============================================================
# 测试用例 — 从 prompt-cache-optimization.md 提取的优化结构
# ============================================================

# 每个测试用例包含：
#   name:       测试名称
#   fixed_part: 固定前缀（应命中缓存）
#   dynamic_part: 动态数据（不命中缓存）
#   model:      测试用模型
#   description: 测试说明

TEST_CASES = [
    # ---- 角色A：模拟炒股 ----
    {
        "name": "模拟炒股-Flash-盘前",
        "fixed": """[角色] 模拟炒股AI操盘手 | 管理¥30,000模拟资金
[权限] 自主买卖调仓，无需用户确认
[约束] 禁创科北ST | 止盈≥30% | 止损≥8% | 最多3只
[框架] 牛熊辩论 + 四维评分 + 风控检查
[输出] [模拟炒股]前缀 | 推飞书

指令：
1. 分析今日大盘走势
2. 评估持仓个股表现
3. 给出买卖建议""",
        "dynamic": f"[time] {datetime.now().strftime('%Y-%m-%d %H:%M')}\n[prices] 上证:3200 深证:10500 创业板:2200\n[portfolio] 茅台:1手 五粮液:2手",
        "model": "deepseek-v4-flash",
        "description": "模拟炒股角色，高频盘前分析，Flash模型",
    },
    {
        "name": "模拟炒股-Flash-盘中",
        "fixed": """[角色] 模拟炒股AI操盘手 | 管理¥30,000模拟资金
[权限] 自主买卖调仓，无需用户确认
[约束] 禁创科北ST | 止盈≥30% | 止损≥8% | 最多3只
[框架] 牛熊辩论 + 四维评分 + 风控检查
[输出] [模拟炒股]前缀 | 推飞书

指令：
1. 监控持仓异动
2. 检查止盈止损触发
3. 执行必要调仓""",
        "dynamic": f"[time] {datetime.now().strftime('%Y-%m-%d %H:%M')}\n[holdings] 茅台:¥168.5(+2.3%) 五粮液:¥85.2(-1.1%)\n[market] 上证:3210(+0.3%)",
        "model": "deepseek-v4-flash",
        "description": "模拟炒股角色，高频盘中监控，Flash模型",
    },
    # ---- 角色B：投资助理 ----
    {
        "name": "投资助理-Pro-深度分析",
        "fixed": """[角色] 用户A股投资助理 | 提供分析建议
[权限] 仅建议权，不操作实盘
[约束] 只碰 data/user/ 数据 | 不动 simulation/
[框架] 牛熊辩论 + 四维评分 + 风控检查
[输出] [投资助理]前缀 | 推飞书 + 告知用户

## 分析要求
1. 牛熊辩论框架（看多/看空各至少3条）
2. 四维评分（消息/技术/基本/资金各0-10）
3. 风控检查（仓位/行业/大盘/止损）
4. 结论先行（代码+价位+操作）

## 输出格式
📊 [股票名称](代码) | 建议:[买入/卖出/持有]
📈 牛熊辩论
   🐂 看多理由：...
   🐻 看空理由：...
📊 四维评分: 消息X/10 技术X/10 基本X/10 资金X/10
⚠️ 风险提示：...""",
        "dynamic": f"[time] {datetime.now().strftime('%Y-%m-%d %H:%M')}\n[stock] 贵州茅台(600519) 现价:¥168.00\n[financial] PE:25 PB:6 ROE:20% 营收增长+15%\n[market] 白酒板块:+0.8% 北向:+2.5亿",
        "model": "deepseek-v4-pro",
        "description": "投资助理深度分析，Pro模型，结构化输出",
    },
    {
        "name": "投资助理-Flash-快速问答",
        "fixed": """[角色] 用户A股投资助理 | 提供分析建议
[权限] 仅建议权，不操作实盘
[约束] 只碰 data/user/ 数据 | 不动 simulation/
[输出] [投资助理]前缀 | 推飞书 + 告知用户

指令：
1. 回答用户咨询
2. 提供客观分析
3. 标明风险""",
        "dynamic": f"[time] {datetime.now().strftime('%Y-%m-%d %H:%M')}\n[question] {sys.argv[5] if len(sys.argv) > 5 else '茅台现在能买吗？'}\n[price] 当前价:¥168.00",
        "model": "deepseek-v4-flash",
        "description": "投资助理快速问答，Flash模型",
    },
    # ---- 角色C：美股监控 ----
    {
        "name": "美股监控-Flash",
        "fixed": """[角色] 美股市场监控助理 | 关注道指/纳指/标普
[权限] 仅监控分析，不交易
[约束] 北京时间21:00-05:00
[输出] [美股监控]前缀 | 推飞书

指令：
1. 监控三大指数走势
2. 关注重要经济数据
3. 提醒重大事件""",
        "dynamic": f"[time] {datetime.now().strftime('%Y-%m-%d %H:%M')}\n[market] 道指:42000(-0.5%) 纳指:18500(+0.8%) 标普:5800(+0.2%)\n[events] CPI即将公布 利率决议本周",
        "model": "deepseek-v4-flash",
        "description": "美股监控，Flash模型",
    },
    # ---- 系统维护类 ----
    {
        "name": "成本监控-Flash",
        "fixed": """[角色] 系统监控助理 | AI成本追踪
[权限] 只读不写 | 仅监控
[输出] [成本监控]前缀 | 推飞书

指令：
1. 检查今日AI调用成本
2. 对比预算阈值
3. 报告异常消耗""",
        "dynamic": f"[time] {datetime.now().strftime('%Y-%m-%d %H:%M')}\n[daily_cost] ¥{3.5:.2f} / ¥25.00\n[monthly_cost] ¥{120:.2f} / ¥400.00",
        "model": "deepseek-v4-flash",
        "description": "成本监控日任务，Flash模型",
    },
]


# ============================================================
# API 调用函数
# ============================================================


def get_api_key() -> str:
    """获取 API key：环境变量 > --api-key > 提示输入"""
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if key:
        return key
    # 检查 config 文件
    config_path = Path(__file__).parent / ".prompt_cache_config.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
            key = config.get("api_key", "")
            if key:
                return key
        except (json.JSONDecodeError, OSError):
            pass
    return ""


def get_base_url() -> str:
    """获取 API base URL"""
    url = os.environ.get("DEEPSEEK_BASE_URL", "")
    if url:
        return url.rstrip("/")
    config_path = Path(__file__).parent / ".prompt_cache_config.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
            url = config.get("base_url", DEFAULT_BASE_URL)
            return url.rstrip("/")
        except (json.JSONDecodeError, OSError):
            pass
    return DEFAULT_BASE_URL


def call_deepseek(prompt: str, model: str, api_key: str, base_url: str) -> dict:
    """
    调用 DeepSeek Chat API，返回包含缓存指标的响应。

    返回:
    ----
    dict: {
        "success": bool,
        "response": str,
        "usage": dict (含 prompt_cache_hit_tokens / prompt_cache_miss_tokens),
        "duration_ms": int,
        "error": str|None
    }
    """
    if not api_key:
        return {
            "success": False,
            "error": "未配置 API key。请设置 DEEPSEEK_API_KEY 环境变量或创建 .prompt_cache_config.json",
            "usage": {},
            "duration_ms": 0,
        }

    url = f"{base_url}/chat/completions"
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 500,
            "temperature": 0.3,
        }
    ).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    start = time.time()
    try:
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:  # nosec B310: API proxy
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:300]
        return {
            "success": False,
            "error": f"HTTP {e.code}: {err_body}",
            "usage": {},
            "duration_ms": int((time.time() - start) * 1000),
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "usage": {},
            "duration_ms": int((time.time() - start) * 1000),
        }

    elapsed = int((time.time() - start) * 1000)
    usage = body.get("usage", {})
    content = body.get("choices", [{}])[0].get("message", {}).get("content", "")

    return {
        "success": True,
        "response": content[:200],  # 只保留前200字符用于验证
        "usage": usage,
        "model_actual": body.get("model", model),
        "duration_ms": elapsed,
        "error": None,
    }


# ============================================================
# 缓存分析
# ============================================================


def extract_cache_metrics(usage: dict) -> dict:
    """
    从 API usage 响应中提取缓存指标。

    DeepSeek 返回字段（OpenAI 兼容格式）:
    - prompt_tokens: 总输入 tokens
    - completion_tokens: 输出 tokens
    - prompt_cache_hit_tokens: 命中缓存的 tokens
    - prompt_cache_miss_tokens: 未命中缓存的 tokens
    """
    hit = usage.get("prompt_cache_hit_tokens", 0)
    miss = usage.get("prompt_cache_miss_tokens", 0)
    total = hit + miss
    # 有些版本用 prompt_tokens 表示总输入
    if total == 0:
        total = usage.get("prompt_tokens", 0)
        # 如果没有分开统计，尝试从总输入推导
        if total > 0:
            return {
                "hit_tokens": hit,
                "miss_tokens": miss,
                "total_input_tokens": total,
                "hit_rate": 0.0,
                "note": "API 未返回分开的缓存指标（hit/miss），建议检查模型版本",
            }

    hit_rate = hit / total * 100 if total > 0 else 0.0

    return {
        "hit_tokens": hit,
        "miss_tokens": miss,
        "total_input_tokens": total,
        "hit_rate": round(hit_rate, 2),
        "note": "",
    }


def run_single_test(test_case: dict, api_key: str, base_url: str, round_num: int = 1) -> dict:
    """
    运行单次缓存测试。
    1) 只用固定前缀（无动态数据）— 应命中缓存
    2) 固定前缀 + 动态数据 — 只有前缀命中
    """
    model = test_case["model"]
    name = test_case["name"]
    fixed = test_case["fixed"]
    dynamic = test_case["dynamic"]
    full_prompt = f"{fixed}\n\n=== 动态数据 ===\n{dynamic}"

    results = {}

    # 测试1：纯固定前缀
    result_fixed = call_deepseek(fixed, model, api_key, base_url)
    metrics_fixed = extract_cache_metrics(result_fixed.get("usage", {}))
    results["fixed_only"] = {
        "success": result_fixed["success"],
        "duration_ms": result_fixed["duration_ms"],
        "cache_metrics": metrics_fixed,
        "error": result_fixed["error"],
    }

    # 测试2：固定前缀 + 动态数据
    result_full = call_deepseek(full_prompt, model, api_key, base_url)
    metrics_full = extract_cache_metrics(result_full.get("usage", {}))
    results["fixed_plus_dynamic"] = {
        "success": result_full["success"],
        "duration_ms": result_full["duration_ms"],
        "cache_metrics": metrics_full,
        "error": result_full["error"],
    }

    return results


# ============================================================
# 报告生成
# ============================================================


def run_verification(api_key: str, base_url: str, rounds: int = DEFAULT_ROUNDS) -> dict:
    """运行完整验证流程"""

    print(f"\n{'=' * 65}")
    print("  🔍 DeepSeek Prompt Cache 命中率验证")
    print(f"  {'=' * 65}")
    print(f"  API端点: {base_url}")
    print(f"  API Key: {'✅ 已配置' if api_key else '❌ 未配置'}")
    print(f"  每组重复: {rounds} 次")
    print(f"  测试用例: {len(TEST_CASES)} 组")
    print(f"  {'=' * 65}")

    if not api_key:
        print("\n  ⚠️  需要配置 DeepSeek API key 才能执行实际 API 验证。")
        print("  请设置环境变量 DEEPSEEK_API_KEY 或创建配置文件。")
        print("  当前将执行结构化验证（不调用 API）。")
        print(f"  {'=' * 65}\n")
        return structural_analysis_only()

    all_results = []
    summary_rows = []

    for test_case in TEST_CASES:
        name = test_case["name"]
        model = test_case["model"]
        fixed_len = len(test_case["fixed"])
        total_len = fixed_len + len(test_case["dynamic"])

        print(f"\n  ─── 测试: {name} ({model}) ───")
        print(f"  固定前缀: ~{fixed_len}字符 | 总prompt: ~{total_len}字符")

        round_results = []
        for r in range(1, rounds + 1):
            result = run_single_test(test_case, api_key, base_url, r)
            round_results.append(result)

            # 提取结果
            fixed_metrics = result["fixed_only"]["cache_metrics"]
            full_metrics = result["fixed_plus_dynamic"]["cache_metrics"]

            # 显示简要结果
            if result["fixed_only"]["success"]:
                hit = fixed_metrics.get("hit_rate", 0)
                dur = result["fixed_only"]["duration_ms"]
                print(f"    Round {r}/{rounds}: 固定前缀✅ hit={hit:.0f}% {dur}ms", end="")
            else:
                print(
                    f"    Round {r}/{rounds}: ❌ {result['fixed_only'].get('error', '')[:60]}",
                    end="",
                )

            if result["fixed_plus_dynamic"]["success"]:
                hit = full_metrics.get("hit_rate", 0)
                dur = result["fixed_plus_dynamic"]["duration_ms"]
                print(f" | 完整prompt✅ hit={hit:.0f}% {dur}ms")
            else:
                print(f" | ❌ {result['fixed_plus_dynamic'].get('error', '')[:60]}")

        # 汇总统计
        fixed_successes = [r for r in round_results if r["fixed_only"]["success"]]
        full_successes = [r for r in round_results if r["fixed_plus_dynamic"]["success"]]

        avg_fixed_hit = (
            sum(r["fixed_only"]["cache_metrics"]["hit_rate"] for r in fixed_successes)
            / len(fixed_successes)
            if fixed_successes
            else 0
        )
        avg_full_hit = (
            sum(r["fixed_plus_dynamic"]["cache_metrics"]["hit_rate"] for r in full_successes)
            / len(full_successes)
            if full_successes
            else 0
        )
        avg_fixed_dur = (
            sum(r["fixed_only"]["duration_ms"] for r in fixed_successes) / len(fixed_successes)
            if fixed_successes
            else 0
        )

        row = {
            "name": name,
            "model": model,
            "fixed_chars": fixed_len,
            "total_chars": total_len,
            "rounds": rounds,
            "fixed_only": {
                "success_rate": round(len(fixed_successes) / rounds * 100, 1) if rounds else 0,
                "avg_hit_rate": round(avg_fixed_hit, 2),
                "avg_duration_ms": round(avg_fixed_dur, 1),
            },
            "full_prompt": {
                "success_rate": round(len(full_successes) / rounds * 100, 1) if rounds else 0,
                "avg_hit_rate": round(avg_full_hit, 2),
            },
        }
        summary_rows.append(row)
        all_results.append(
            {
                "test_case": test_case["name"],
                "description": test_case["description"],
                "rounds": round_results,
            }
        )

        # 单组小结
        print("\n  📊 结果汇总:")
        print(
            f"     固定前缀: 成功率{row['fixed_only']['success_rate']}%  "
            f"缓存命中率{row['fixed_only']['avg_hit_rate']:.1f}%  "
            f"平均耗时{row['fixed_only']['avg_duration_ms']}ms"
        )
        print(
            f"     完整prompt: 成功率{row['full_prompt']['success_rate']}%  "
            f"缓存命中率{row['full_prompt']['avg_hit_rate']:.1f}%"
        )

    # 生成全局汇总
    return generate_summary(summary_rows, all_results, rounds)


def structural_analysis_only() -> dict:
    """仅做结构化分析（不调用 API）"""
    print("\n  📋 结构化验证结果：\n")

    findings = []
    for tc in TEST_CASES:
        fixed = tc["fixed"]
        dynamic = tc["dynamic"]
        issues = []

        # 检查固定前缀是否包含动态数据
        # 注意：排除 [约束] 行中的静态时间范围（如 "北京时间21:00-05:00" 是约束，不是动态数据）
        time_patterns = ["{time}", "{current_time}", "{current time}", "[time]"]
        for pat in time_patterns:
            if pat in fixed:
                issues.append(f"固定前缀包含动态时间标记「{pat}」")

        # 检查方括号动态标记（排除 [角色] [权限] [约束] [输出] [框架] 等结构化标记）
        bracket_patterns = re.findall(r"\[(\w+)\]", fixed)
        dynamic_brackets = [
            b
            for b in set(bracket_patterns)
            if b.lower()
            not in (
                "角色",
                "权限",
                "约束",
                "输出",
                "框架",
                "time",
                "美股监控",
                "投资助理",
                "模拟炒股",
                "成本监控",
            )
        ]
        if dynamic_brackets:
            issues.append(f"固定前缀包含疑似动态标记「{', '.join(dynamic_brackets)}」")

        # 检查是否有价格/数字动态数据
        price_keywords = [
            "{prices}",
            "{price}",
            "{portfolio}",
            "{holding}",
            "{market}",
            "{data}",
            "[prices]",
            "[price]",
        ]
        for kw in price_keywords:
            if kw in fixed:
                issues.append(f"固定前缀包含动态数据标记「{kw}」")

        # 检查结构是否分区
        has_section_marker = "━━━" in fixed or "===" in fixed or "缓存命中区" in fixed
        has_dynamic_section = bool(dynamic and dynamic.strip())

        status = "✅" if not issues else "⚠️"
        note = "通过" if not issues else "; ".join(issues)

        findings.append(
            {
                "name": tc["name"],
                "model": tc["model"],
                "status": status,
                "note": note,
                "fixed_chars": len(fixed),
                "has_section_marker": has_section_marker,
                "has_dynamic_section": has_dynamic_section,
            }
        )

        print(f"  {status} {tc['name']:>30} | {tc['model']:>18} | {note}")

    # 统计
    passed = sum(1 for f in findings if f["status"] == "✅")
    warned = sum(1 for f in findings if f["status"] == "⚠️")
    print(f"\n  {'=' * 65}")
    print(f"  结构化验证: {passed}/{len(findings)} 通过, {warned} 个警告")
    print("  缓存优化建议: 固定前缀中绝对不能出现动态数据")
    print(f"  {'=' * 65}\n")

    return {
        "type": "structural_only",
        "passed": passed,
        "warned": warned,
        "total": len(findings),
        "findings": findings,
        "note": "未配置 API key，仅执行结构化验证",
    }


def generate_summary(summary_rows: list, all_results: list, rounds: int) -> dict:
    """生成最终汇总报告"""

    if not summary_rows:
        return {"error": "无有效测试数据"}

    total_success = sum(1 for r in all_results for rr in r["rounds"] if rr["fixed_only"]["success"])
    total_rounds = len(TEST_CASES) * rounds
    overall_success_rate = round(total_success / total_rounds * 100, 1) if total_rounds else 0

    avg_fixed_hit = sum(r["fixed_only"]["avg_hit_rate"] for r in summary_rows) / len(summary_rows)
    avg_full_hit = sum(r["full_prompt"]["avg_hit_rate"] for r in summary_rows) / len(summary_rows)
    avg_duration = sum(r["fixed_only"]["avg_duration_ms"] for r in summary_rows) / len(summary_rows)

    print(f"\n{'=' * 65}")
    print("  📊 综合验证报告")
    print(f"  {'=' * 65}")
    print(f"  测试总数: {len(TEST_CASES)} 组 × {rounds} 轮 = {total_rounds} 次调用")
    print(f"  整体成功率: {overall_success_rate}%")
    print(f"  {'─' * 40}")
    print(f"  固定前缀缓存命中率: {avg_fixed_hit:.1f}%")
    print(f"  完整prompt缓存命中率: {avg_full_hit:.1f}%")
    print(f"  平均响应耗时: {avg_duration:.0f}ms")
    print(f"  {'─' * 40}")
    print("  缓存优化效果: ", end="")

    if avg_fixed_hit >= 95:
        print("🟢 优秀！固定前缀缓存命中率≥95%")
    elif avg_fixed_hit >= 80:
        print("🟡 良好，但仍有优化空间")
    else:
        print("🔴 需要检查固定前缀是否包含动态数据")

    print("\n  各测试详情:")
    print(f"  {'测试名称':>30} | {'模型':>16} | {'固定前缀命中率':>14} | {'完整prompt命中率':>14}")
    print(f"  {'─' * 80}")
    for row in summary_rows:
        f_hit = row["fixed_only"]["avg_hit_rate"]
        p_hit = row["full_prompt"]["avg_hit_rate"]
        print(f"  {row['name']:>30} | {row['model']:>16} | {f_hit:>13.1f}% | {p_hit:>13.1f}%")

    print(f"  {'=' * 65}")
    print("  成本效益分析:")
    print(f"  {'─' * 40}")

    # 计算节省
    total_savings = 0
    for row in summary_rows:
        miss_price = DEEPSEEK_MODELS.get(row["model"], {}).get("input_price", 0)
        hit_price = miss_price * 0.008  # 缓存命中约1/120的价格
        if row["fixed_only"]["avg_hit_rate"] > 0:
            savings_rate = row["fixed_only"]["avg_hit_rate"] / 100
            per_call_saving = row["fixed_chars"] * miss_price * savings_rate * 0.8 / 10000
            total_savings += per_call_saving * 30  # 假设每天调用1次，30天

    print(f"  当月预估节省（按30天计）: ¥{total_savings:.2f}")
    print(f"  参考：未命中定价 ¥{miss_price:.1f}/万Token")
    print(f"  参考：命中定价    ¥{hit_price:.3f}/万Token")
    print("\n  💡 提示: 缓存命中 vs 未命中的价差约 120 倍")
    print(f"  {'=' * 65}\n")

    return {
        "type": "full_verification",
        "test_date": datetime.now().isoformat(),
        "total_test_cases": len(TEST_CASES),
        "rounds_per_case": rounds,
        "overall_success_rate": overall_success_rate,
        "avg_fixed_hit_rate": round(avg_fixed_hit, 2),
        "avg_full_hit_rate": round(avg_full_hit, 2),
        "avg_duration_ms": round(avg_duration, 1),
        "details": summary_rows,
        "raw_results": all_results,
        "estimated_monthly_savings": round(total_savings, 2),
    }


# ============================================================
# 配置保存
# ============================================================


def save_config(api_key: str = "", base_url: str = ""):
    """保存 API 配置到本地文件"""
    config_path = Path(__file__).parent / ".prompt_cache_config.json"
    config = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    if api_key:
        config["api_key"] = api_key
    if base_url:
        config["base_url"] = base_url
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False))
    print(f"✅ 配置已保存到 {config_path}")


def save_report(report: dict):
    """保存验证报告到 JSON 文件"""
    data_dir = Path(__file__).parent.parent / ".workbuddy" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    report_path = data_dir / "prompt_cache_report.json"

    # 保留历史记录
    history = []
    if report_path.exists():
        try:
            history = json.loads(report_path.read_text())
            if not isinstance(history, list):
                history = [history]
        except (json.JSONDecodeError, OSError):
            pass

    report["timestamp"] = datetime.now().isoformat()
    # 只保留最近30条记录
    history.append(report)
    if len(history) > 30:
        history = history[-30:]

    report_path.write_text(json.dumps(history, indent=2, ensure_ascii=False))
    print(f"📁 报告已保存到 {report_path}")


# ============================================================
# CLI 入口
# ============================================================


def print_help():
    print("""用法: python3 verify_prompt_cache.py [OPTIONS]

验证 DeepSeek Prompt Cache 命中率。

选项:
  --api-key KEY      DeepSeek API key（可选，优先使用环境变量 DEEPSEEK_API_KEY）
  --base-url URL     API 端点（默认 https://api.deepseek.com/v1）
  --rounds N         每组重复次数（默认 5）
  --configure        交互式配置 API key 和 base_url
  --json             输出 JSON 格式报告（保存到 data/prompt_cache_report.json）
  --analyze-only     仅做结构化分析（不调用 API）
  --help             显示帮助信息

环境变量:
  DEEPSEEK_API_KEY   DeepSeek API key
  DEEPSEEK_BASE_URL  API 端点（可选）

示例:
  python3 verify_prompt_cache.py                              # 结构化分析
  python3 verify_prompt_cache.py --configure                  # 配置 API key
  python3 verify_prompt_cache.py --rounds 10                  # 10轮测试每组
  python3 verify_prompt_cache.py --json                       # 保存 JSON 报告
  DEEPSEEK_API_KEY=sk-xxx python3 verify_prompt_cache.py      # 直接指定 key
""")


if __name__ == "__main__":
    # 解析参数
    import argparse

    parser = argparse.ArgumentParser(description="DeepSeek Prompt Cache 验证工具", add_help=False)
    parser.add_argument("--api-key", type=str, default="", help="DeepSeek API key")
    parser.add_argument("--base-url", type=str, default="", help="API base URL")
    parser.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS, help="每组重复次数")
    parser.add_argument("--configure", action="store_true", help="交互式配置")
    parser.add_argument("--json", action="store_true", help="输出 JSON 报告")
    parser.add_argument("--analyze-only", action="store_true", help="仅结构化分析")
    parser.add_argument("--help", action="store_true", help="显示帮助")
    args, _ = parser.parse_known_args()

    if args.help:
        print_help()
        sys.exit(0)

    # 配置模式
    if args.configure:
        print("\n🔧 Prompt Cache 验证配置\n")
        key = input("请输入 DeepSeek API key: ").strip()
        url = input(f"请输入 API base URL（回车默认 {DEFAULT_BASE_URL}）: ").strip()
        if not url:
            url = DEFAULT_BASE_URL
        if key:
            save_config(key, url)
        else:
            print("❌ API key 不能为空")
            sys.exit(1)
        sys.exit(0)

    # 获取 API key
    api_key = args.api_key or get_api_key()
    base_url = args.base_url or get_base_url()

    # 运行验证
    if args.analyze_only or not api_key:
        report = structural_analysis_only()
    else:
        report = run_verification(api_key, base_url, args.rounds)

    # 保存报告
    if args.json or report.get("type") == "full_verification":
        save_report(report)

    # JSON 输出模式
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
