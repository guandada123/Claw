"""
router.py — 智能四层模型路由器 + 统一 API 调用层
====================================================
适用：Claw / QTS A股投资系统
架构：关键词路由 → 预算感知降级 → 真实API调用 → Fallback链 → 成本日志
集成：与 budget_guard.py + cost_tracker.py + local_model.py 无缝配合
模型分级：Local(¥0) / Flash(¥0.5/万) / Pro(¥4/万) / 旗舰(¥18-36/万)

版本：v4.0 | 2026-06-15 — 集成真实 API 调用、成本追踪、CatRouter 代理
"""

import json
import random
import re
import secrets
import time
import urllib.error
import urllib.request
from enum import Enum

# ============================================================
# 模块级依赖导入（替代函数内动态导入，避免 import lock 死锁）
# ============================================================
try:
    from cost_tracker import MODEL_PRICES, _match_model
    from cost_tracker import log_call as _log_call
except ImportError:
    from scripts.cost_tracker import MODEL_PRICES, _match_model
    from scripts.cost_tracker import log_call as _log_call

try:
    from local_model import call as _local_call
    from local_model import is_available as _local_check

    _LOCAL_AVAILABLE = True
except ImportError:
    try:
        from scripts.local_model import call as _local_call
        from scripts.local_model import is_available as _local_check

        _LOCAL_AVAILABLE = True
    except ImportError:
        _LOCAL_AVAILABLE = False

        def _local_call(*a, **kw):
            raise ImportError("local_model 未安装")

        def _local_check():
            return False


# ============================================================
# API 密钥（从 secrets.py 加载，环境变量可覆盖）
# ============================================================
import os

try:
    from secrets import CATROUTER_API_KEY, CATROUTER_BASE_URL, DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL
except ImportError:
    # 没有 secrets.py → 从环境变量读取（CI / Docker 场景）
    DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
    CATROUTER_API_KEY = os.environ.get("CATROUTER_API_KEY", "")
    DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    CATROUTER_BASE_URL = os.environ.get("CATROUTER_BASE_URL", "https://api.catrouter.net/v1")

# 环境变量优先级最高（覆盖 secrets.py）
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", DEEPSEEK_API_KEY)
CATROUTER_API_KEY = os.environ.get("CATROUTER_API_KEY", CATROUTER_API_KEY)


# ============================================================
# 模型层级定义
# ============================================================


class ModelTier(Enum):
    """模型层级（按成本递增）"""

    LOCAL = "ollama-local"  # ¥0 本地 Ollama（心跳/简单任务）
    FLASH = "deepseek-v4-flash"  # ¥0.5/万输入Token
    PRO = "deepseek-v4-pro"  # ¥4/万输入Token
    PREMIUM = "premium"  # GPT-5 / Claude（动态选择）

    def __str__(self):
        return self.value


# ============================================================
# 路由规则（按优先级从高到低）
# ============================================================

ROUTING_RULES = {
    ModelTier.LOCAL: [
        # ---- 心跳/状态检查 ----
        r"heartbeat|ping|is.?alive|健康检查|服务状态|是否在线|存活检查",
        # ---- 系统巡检 ----
        r"巡检|daily.?check|status.?check|系统检查|环境检查",
        # ---- 极简格式任务 ----
        r"JSON格式化|json.?format|格式美化|pretty.?print",
        # ---- 简单布尔判断 ----
        r"是否|是不是|true.?or.?false|yes.?or.?no|判断.*是否",
        # ---- 简单提取 ----
        r"提取数字|提取日期|提取.*字段|extract.?\w+.?from",
    ],
    ModelTier.FLASH: [
        # ---- 简单数据处理 ----
        r"格式化|清洗|整理|提取关键词|标注|分类|排序",
        r"数据清洗|格式转换|字段提取|批量处理",
        # ---- 代码辅助 ----
        r"注释|文档注释|代码补全|简单代码|格式化代码",
        r"lint|pylint|eslint|类型标注|type hint",
        # ---- 翻译/摘要 ----
        r"翻译|摘要|总结|提炼|简报|概览|一句话概括",
        r"翻译成.*英文|翻译成.*中文",
        # ---- 股票初筛 ----
        r"初筛|筛选|过滤|候选|量价背离|异动",
        r"涨停|跌停|板块涨幅|排行|排名",
        # ---- 推送模板 ----
        r"飞书推送|消息模板|格式化通知|告警文案",
        # ---- 通用 FAQ ----
        r"是什么意思|简单解释|定义|概念",
    ],
    ModelTier.PRO: [
        # ---- 业务逻辑（保持不变）----
        r"策略函数|交易逻辑|选股条件|买入信号|卖出信号",
        r"止损|止盈|仓位|资金管理|风险管理|风控",
        # ---- 中等分析 ----
        r"技术分析|K线|均线|MACD|RSI|KDJ|布林带|成交量",
        r"支撑|阻力|突破|回调|趋势|形态|背离",
        r"资金流向|北向资金|主力资金|大单|换手率",
        # ---- 代码开发（中等） ----
        r"实现|重构|优化|调试|修bug|单元测试|集成测试",
        r"API设计|接口实现|函数实现|业务逻辑",
        # ---- 单股分析 ----
        r"个股分析|深度分析|操作建议|买卖建议|入场|离场",
        r"基本面|财务数据|市盈率|市净率|ROE|EPS",
        # ---- 回测 ----
        r"回测|回溯测试|策略表现|收益率|胜率|夏普|最大回撤",
    ],
    # ModelTier.PREMIUM 在无法匹配上述规则且命中 premium_signal 时使用
}

# 预编译路由正则（避免每次 route_task() 调用都编译）
_COMPILED_RULES = {
    tier: [re.compile(p) for p in patterns] for tier, patterns in ROUTING_RULES.items()
}

# 旗舰模型信号词（触发 PREMIUM 层级）— frozenset 禁止运行时修改
PREMIUM_SIGNALS = frozenset(
    {
        "架构",
        "架构设计",
        "系统设计",
        "方案设计",
        "技术选型",
        "代码审查",
        "代码Review",
        "审查",
        "安全审查",
        "审计",
        "核心策略",
        "核心逻辑",
        "交易核心",
        "文档",
        "技术文档",
        "架构文档",
        "方案评审",
        "全量Review",
        "全量审查",
        "复杂算法",
        "高性能",
        "大规模",
        "GPT-5",
        "Claude",
        "旗舰",
    }
)

# PREMIUM 信号词编译正则（替代 17 次线性子串检查）
_COMPILED_PREMIUM_SIGNALS = re.compile(
    "|".join(re.escape(s) for s in PREMIUM_SIGNALS),
    re.IGNORECASE,
)

# ============================================================
# 核心路由函数
# ============================================================


def route_task(
    prompt: str, task_type: str = "", force_tier: ModelTier = None, allow_local: bool = True
) -> ModelTier:
    """
    根据 Prompt 内容决定使用哪个模型层级。

    参数
    ----
    prompt : str
        用户输入 / 任务描述
    task_type : str
        额外任务类型标签（可选）
    force_tier : ModelTier, optional
        强制指定层级（覆盖路由判断）
    allow_local : bool
        是否允许路由到本地模型（默认True）

    返回
    ----
    ModelTier
    """
    if force_tier:
        return force_tier

    combined = f"{prompt} {task_type}".lower()

    # 1. 检查旗舰信号 —— 优先级最高（编译正则，单次搜索替代 17 次子串检查）
    if _COMPILED_PREMIUM_SIGNALS.search(combined):
        return ModelTier.PREMIUM

    # 2. LOCAL 层匹配（仅当 allow_local=True 时）
    if allow_local:
        for compiled in _COMPILED_RULES[ModelTier.LOCAL]:
            if compiled.search(combined):
                return ModelTier.LOCAL

    # 3. Flash 层匹配
    for compiled in _COMPILED_RULES[ModelTier.FLASH]:
        if compiled.search(combined):
            return ModelTier.FLASH

    # 4. Pro 层匹配
    for compiled in _COMPILED_RULES[ModelTier.PRO]:
        if compiled.search(combined):
            return ModelTier.PRO

    # 5. 默认：Pro（宁可保守也不要路由不足）
    return ModelTier.PRO


def _select_premium_model(prompt: str, task_type: str) -> dict:
    """选择 PREMIUM 层模型：代码审查用 Claude Sonnet 4，其他用 GPT-5"""
    return {
        "model": "claude-sonnet-4-20250514" if "代码审查" in f"{prompt} {task_type}" else "gpt-5",
        "provider": "premium",
        "cost_per_10k": 21.6,
        "base_url": "https://api.catrouter.net/v1",
        "note": "架构/审查/核心策略",
    }


def get_model(
    prompt: str,
    budget_status: dict = None,
    task_type: str = "",
    force_tier: ModelTier = None,
    local_available: bool = None,
) -> dict:
    """
    综合路由决策：任务分类 + 预算约束 + 本地可用性 + 模型选择。

    参数
    ----
    prompt : str
        用户输入
    budget_status : dict
        check_budget_status() 返回值，含 spent/remaining/tier
    task_type : str
        任务类型标签
    force_tier : ModelTier
        强制层级
    local_available : bool, optional
        Ollama 是否可用（None = 自动检测，需导入 local_model）

    返回
    ----
    dict : { "model": str, "provider": str, "tier": ModelTier,
             "base_url": str, "cost_per_10k": float, "note": str }
    """
    # 检查本地模型可用性
    if local_available is None:
        local_available = _local_check()

    tier = route_task(prompt, task_type, force_tier, allow_local=local_available)

    # ---- 预算约束覆盖 ----
    if budget_status:
        budget_tier = budget_status.get("tier", "full")
        if budget_tier == "flash_only":
            # 已超¥350，锁定Flash（LOCAL仍可用，不受预算约束）
            if tier in (ModelTier.PRO, ModelTier.PREMIUM):
                print(
                    f"   ⛔ budget_guard: {tier} 被降级为 Flash（¥{budget_status['spent']:.0f}/¥400已用）"
                )
                tier = ModelTier.FLASH
        elif budget_tier == "flash_preferred":
            # ¥280-350，Pro降级为Flash
            if tier == ModelTier.PRO:
                tier = ModelTier.FLASH
        elif budget_tier == "normal":
            pass

    # ---- 映射到具体模型 ----
    configs = {
        ModelTier.LOCAL: {
            "model": "qwen2.5:7b",
            "provider": "ollama",
            "cost_per_10k": 0.0,
            "base_url": "http://localhost:11434/v1",
            "note": "本地模型，零成本，适合心跳/状态检查",
        },
        ModelTier.FLASH: {
            "model": "deepseek-v4-flash",
            "provider": "deepseek",
            "cost_per_10k": 0.5,
            "base_url": "https://api.deepseek.com/v1",
            "note": "日常轻量任务",
        },
        ModelTier.PRO: {
            "model": "deepseek-v4-pro",
            "provider": "deepseek",
            "cost_per_10k": 4.0,
            "base_url": "https://api.deepseek.com/v1",
            "note": "复杂开发/分析任务",
        },
        ModelTier.PREMIUM: _select_premium_model(prompt, task_type),
    }

    result = dict(configs[tier])
    result["tier"] = tier
    return result


# ============================================================
# 统一 API 调用层 — 响应工具函数
# ============================================================


def _make_error_resp(model: str, provider: str, elapsed: int, error: str) -> dict:
    """统一构造 API 失败响应"""
    return {
        "success": False,
        "response": None,
        "model": model,
        "provider": provider,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_cny": 0.0,
        "prompt_cache_hit_tokens": None,
        "prompt_cache_miss_tokens": None,
        "duration_ms": elapsed,
        "error": error,
    }


def _resolve_api_config(provider: str, model_config: dict) -> tuple:
    """根据 provider 解析 API Key 和 Base URL"""
    if provider == "deepseek":
        return DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL
    elif provider in ("catrouter", "premium"):
        return CATROUTER_API_KEY, CATROUTER_BASE_URL
    else:
        return DEEPSEEK_API_KEY, model_config.get("base_url", DEEPSEEK_BASE_URL)


def _build_chat_messages(system: str, prompt: str) -> list:
    """构建聊天消息列表"""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return messages


def _parse_success_response(
    body: dict, model: str, provider: str, cost_per_10k: float, task: str, project: str
) -> dict:
    """解析成功的 API 响应，计算成本，记录日志"""
    usage = body.get("usage", {})
    input_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0))
    output_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0))
    hit_tokens = usage.get("prompt_cache_hit_tokens", None)
    miss_tokens = usage.get("prompt_cache_miss_tokens", None)

    model_key = _match_model(model)
    prices = MODEL_PRICES.get(model_key, {"input": cost_per_10k / 2, "output": cost_per_10k})
    cost_cny = (input_tokens * prices["input"] + output_tokens * prices["output"]) / 10000

    response_text = body.get("choices", [{}])[0].get("message", {}).get("content", "")

    _try_log_call(model, input_tokens, output_tokens, task, project, hit_tokens, miss_tokens)

    return {
        "response": response_text,
        "model": body.get("model", model),
        "provider": provider,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_cny": round(cost_cny, 6),
        "prompt_cache_hit_tokens": hit_tokens,
        "prompt_cache_miss_tokens": miss_tokens,
    }


def call_llm(
    prompt: str,
    model_config: dict,
    system: str = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    task: str = "",
    project: str = "Claw",
) -> dict:
    """
    统一的 LLM API 调用函数。

    支持供应商：
      - deepseek  → DeepSeek 直连（api.deepseek.com）
      - catrouter → CatRouter 代理（api.catrouter.net）
      - ollama    → 本地模型（委托 local_model.py）

    参数
    ----
    prompt : str
        用户输入
    model_config : dict
        get_model() 返回的模型配置
    system : str, optional
        系统提示
    temperature : float
        温度参数（默认 0.3）
    max_tokens : int
        最大输出 Token 数（默认 4096）
    task : str
        任务描述（用于成本追踪）
    project : str
        项目名（用于成本追踪）

    返回
    ----
    dict : {
        "success": bool,
        "response": str|None,
        "model": str,
        "provider": str,
        "input_tokens": int,
        "output_tokens": int,
        "cost_cny": float,
        "prompt_cache_hit_tokens": int|None,
        "prompt_cache_miss_tokens": int|None,
        "duration_ms": int,
        "error": str|None,
    }
    """
    model = model_config.get("model", "deepseek-v4-flash")
    provider = model_config.get("provider", "deepseek")
    cost_per_10k = model_config.get("cost_per_10k", 0.5)
    start = time.time()

    # ---- 本地模型 ----
    if provider == "ollama":
        try:
            local_result = _local_call(prompt, model=model, system=system)
            elapsed = int((time.time() - start) * 1000)
            if local_result["success"]:
                _try_log_call(
                    model,
                    local_result.get("prompt_tokens", 0),
                    local_result.get("response_tokens", 0),
                    task,
                    project,
                )
                return {
                    "success": True,
                    "response": local_result["response"],
                    "model": model,
                    "provider": "ollama",
                    "input_tokens": local_result.get("prompt_tokens", 0),
                    "output_tokens": local_result.get("response_tokens", 0),
                    "cost_cny": 0.0,
                    "prompt_cache_hit_tokens": None,
                    "prompt_cache_miss_tokens": None,
                    "duration_ms": elapsed,
                    "error": None,
                }
            return _make_error_resp(
                model, "ollama", elapsed, local_result.get("error", "Ollama 调用失败")
            )
        except ImportError:
            return _make_error_resp(
                model, "ollama", int((time.time() - start) * 1000), "local_model.py 未找到"
            )

    # ---- 远程 API（DeepSeek 直连 / CatRouter 代理）----
    api_key, base_url = _resolve_api_config(provider, model_config)
    messages = _build_chat_messages(system, prompt)

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    url = f"{base_url}/chat/completions"
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    try:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())

        elapsed = int((time.time() - start) * 1000)
        parsed = _parse_success_response(body, model, provider, cost_per_10k, task, project)
        return {
            "success": True,
            **parsed,
            "duration_ms": elapsed,
            "error": None,
        }

    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:300]
        elapsed = int((time.time() - start) * 1000)
        error_msg = f"HTTP {e.code}: {err_body}"
        print(f"   ❌ [{provider}] {model} — {error_msg}")
        return _make_error_resp(model, provider, elapsed, error_msg)
    except urllib.error.URLError as e:
        elapsed = int((time.time() - start) * 1000)
        error_msg = f"连接失败: {e.reason}"
        print(f"   ❌ [{provider}] {model} — {error_msg}")
        return _make_error_resp(model, provider, elapsed, error_msg)
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        error_msg = str(e)
        print(f"   ❌ [{provider}] {model} — {error_msg}")
        return _make_error_resp(model, provider, elapsed, error_msg)


def _try_log_call(
    model, input_tokens, output_tokens, task, project, hit_tokens=None, miss_tokens=None
):
    """安全地记录成本日志（不抛出异常）"""
    try:
        _log_call(
            model,
            input_tokens,
            output_tokens,
            task=task,
            project=project,
            prompt_cache_hit_tokens=hit_tokens,
            prompt_cache_miss_tokens=miss_tokens,
        )
    except Exception as e:
        print(f"   ⚠️ cost_tracker 日志失败: {e}")


# ============================================================
# Fallback 链
# ============================================================

FALLBACK_CHAIN = [
    # 注意：从贵到便宜排序！跨供应商 Fallback。
    # LOCAL 不进 Fallback，单独由 local_model.py 处理
    # CatRouter 代理的旗舰模型（从贵到便宜）
    {"provider": "catrouter", "model": "claude-opus-4-20250514", "timeout": 30},  # ¥36.0
    {"provider": "catrouter", "model": "claude-sonnet-4-20250514", "timeout": 30},  # ¥21.6
    {"provider": "catrouter", "model": "gpt-4.1", "timeout": 25},  # ¥18.0
    # DeepSeek 直连（兜底）
    {"provider": "deepseek", "model": "deepseek-v4-pro", "timeout": 20},  # ¥4.0
    {"provider": "deepseek", "model": "deepseek-v4-flash", "timeout": 15},  # ¥0.5
]


def call_with_fallback(
    prompt: str,
    task_type: str = "",
    budget_status: dict = None,
    system: str = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    task: str = "",
    project: str = "Claw",
    verbose: bool = True,
) -> dict:
    """
    带完整 Fallback 链的 LLM 调用 —— 实时 API 调用 + 成本追踪。

    路由流程：
      1. route_task() 根据 prompt 决策模型层级
      2. budget_guard 降级（如预算紧张）
      3. 首选模型调用
      4. 失败 → Fallback 链逐个重试
      5. 全部失败 → 返回错误

    返回
    ----
    dict : {
        "success": bool,
        "response": str|None,
        "model": str,
        "provider": str,
        "tier": str,
        "input_tokens": int,
        "output_tokens": int,
        "cost_cny": float,
        "duration_ms": int,
        "attempts": list,        # 每次尝试的详细记录
        "fallback_used": bool,   # 是否使用了 fallback
    }
    """
    attempts = []

    # 1. 路由决策
    config = get_model(prompt, budget_status, task_type)
    tier = config.get("tier", ModelTier.PRO)

    # 2. 当前首选模型
    primary = {
        "model": config["model"],
        "provider": config["provider"],
        "base_url": config.get("base_url", ""),
        "cost_per_10k": config.get("cost_per_10k", 0.5),
    }

    if verbose:
        print(f"\n  🔀 路由: {tier.value} → {primary['model']} ({primary['provider']})")
        if budget_status:
            print(f"  💰 预算: {budget_status.get('msg', '正常')}")

    # 3. 尝试调用（首选 + Fallback 链）
    candidates = [primary] + [
        {
            "model": c["model"],
            "provider": c["provider"],
            "base_url": CATROUTER_BASE_URL
            if c["provider"] in ("catrouter", "premium")
            else DEEPSEEK_BASE_URL,
            "cost_per_10k": c.get("cost_per_10k", 4.0),
        }
        for c in FALLBACK_CHAIN
        # 排除已尝试过的模型
        if c["model"] != primary["model"]
    ]

    # 去重：保留第一次出现的模型
    seen_models = set()
    unique_candidates = []
    for c in candidates:
        key = (c["model"], c["provider"])
        if key not in seen_models:
            seen_models.add(key)
            unique_candidates.append(c)

    for idx, candidate in enumerate(unique_candidates):
        is_fallback = idx > 0
        model_name = candidate["model"]
        provider_name = candidate["provider"]

        if verbose and is_fallback:
            print(f"  ⚡ Fallback #{idx}: {model_name} ({provider_name})")

        # 构建 model_config
        mc = {
            "model": model_name,
            "provider": provider_name,
            "base_url": candidate.get("base_url", DEEPSEEK_BASE_URL),
            "cost_per_10k": candidate.get("cost_per_10k", 0.5),
        }

        call_start = time.time()
        result = call_llm(
            prompt,
            mc,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            task=task or task_type,
            project=project,
        )

        elapsed = int((time.time() - call_start) * 1000)

        attempt = {
            "model": model_name,
            "provider": provider_name,
            "success": result["success"],
            "error": result.get("error"),
            "duration_ms": elapsed,
            "cost_cny": result.get("cost_cny", 0.0),
            "input_tokens": result.get("input_tokens", 0),
            "output_tokens": result.get("output_tokens", 0),
        }
        attempts.append(attempt)

        if result["success"]:
            if verbose:
                print(
                    f"  ✅ {model_name}: {elapsed}ms | "
                    f"in={result['input_tokens']} out={result['output_tokens']} "
                    f"| ¥{result['cost_cny']:.6f}"
                )
                if result.get("prompt_cache_hit_tokens"):
                    hit = result["prompt_cache_hit_tokens"]
                    miss = result["prompt_cache_miss_tokens"]
                    rate = hit / (hit + miss) * 100 if (hit + miss) > 0 else 0
                    print(f"  📦 Cache: hit={hit} miss={miss} rate={rate:.1f}%")

            return {
                "success": True,
                "response": result["response"],
                "model": model_name,
                "provider": provider_name,
                "tier": tier.value if hasattr(tier, "value") else str(tier),
                "input_tokens": result["input_tokens"],
                "output_tokens": result["output_tokens"],
                "cost_cny": result["cost_cny"],
                "duration_ms": elapsed,
                "attempts": attempts,
                "fallback_used": is_fallback,
            }
        else:
            if verbose:
                print(f"  ❌ {model_name}: 失败 ({result.get('error', '未知错误')[:80]})")
            # 失败后指数退避 + jitter（避免 429 限速拔高重试压力）
            sleep_sec = min(8, (2**idx) * 0.5 + secrets.randbelow(300) / 1000.0)  # jitter 0～0.3s
            time.sleep(sleep_sec)

    # 全部失败
    return {
        "success": False,
        "response": None,
        "model": primary["model"],
        "provider": primary["provider"],
        "tier": tier.value if hasattr(tier, "value") else str(tier),
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_cny": 0.0,
        "duration_ms": sum(a.get("duration_ms", 0) for a in attempts),
        "attempts": attempts,
        "fallback_used": True,
        "error": f"全部 {len(attempts)} 次尝试均失败",
    }


# ============================================================
# 测试工具
# ============================================================


def run_routing_test() -> float:
    """
    运行路由准确性测试。
    返回准确率（%）。
    """
    test_cases = [
        # (prompt, expected_tier, 描述)
        # ---- LOCAL 场景（新增）----
        ("heartbeat check", ModelTier.LOCAL, "心跳-英"),
        ("健康检查", ModelTier.LOCAL, "心跳-中"),
        ("系统巡检", ModelTier.LOCAL, "巡检"),
        ("判断这个服务是否在线", ModelTier.LOCAL, "在线判断"),
        ("daily check", ModelTier.LOCAL, "日检-英"),
        # ---- Flash 场景 ----
        ("帮我格式化这段代码", ModelTier.FLASH, "代码格式化"),
        ("总结这篇新闻的要点", ModelTier.FLASH, "新闻总结"),
        ("翻译成英文", ModelTier.FLASH, "翻译"),
        ("整理这份股票列表，只保留上涨的", ModelTier.FLASH, "数据清洗"),
        ("今天哪个板块涨幅最大", ModelTier.FLASH, "板块排行"),
        ("用一句话概括今天的市场", ModelTier.FLASH, "市场概览"),
        ("生成飞书推送消息模板", ModelTier.FLASH, "推送模板"),
        ("KDJ是什么意思", ModelTier.FLASH, "术语解释"),
        # ---- Pro 场景 ----
        ("实现一个选股策略函数，要求...", ModelTier.PRO, "策略实现"),
        ("分析000001的技术形态，给出建议", ModelTier.PRO, "个股分析"),
        ("这个股票回测结果怎么样", ModelTier.PRO, "回测分析"),
        ("帮我调试这个bug，错误信息是...", ModelTier.PRO, "Bug调试"),
        ("设计一个风控接口，需要止损逻辑", ModelTier.PRO, "接口设计"),
        # ---- Premium 场景 ----
        ("帮我审查这整个模块的代码", ModelTier.PREMIUM, "代码审查"),
        ("设计交易系统的整体架构", ModelTier.PREMIUM, "架构设计"),
        ("审查核心策略逻辑的安全性", ModelTier.PREMIUM, "安全审查"),
        ("写一份系统架构文档", ModelTier.PREMIUM, "文档撰写"),
        ("方案评审：这三种技术方案的对比", ModelTier.PREMIUM, "方案评审"),
    ]

    correct = 0
    print(f"\n{'=' * 65}")
    print(f"  路由准确性测试（{len(test_cases)} 个用例）四层路由 v4.0")
    print(f"{'=' * 65}")

    for prompt, expected, desc in test_cases:
        actual = route_task(prompt)
        status = "✅" if actual == expected else "❌"
        print(f"  {status} [{desc:>10}] 预期:{expected.value:<18} 实际:{actual.value:<18}")
        if actual == expected:
            correct += 1

    accuracy = correct / len(test_cases) * 100
    print(f"\n  📊 路由准确率: {accuracy:.1f}% ({correct}/{len(test_cases)})")
    print(f"  {'✅ 通过' if accuracy >= 80 else '❌ 需要调整'} (目标≥80%)")
    print(f"{'=' * 65}\n")

    return accuracy


# ============================================================
# CLI 入口
# ============================================================

if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "test"

    if cmd == "test":
        run_routing_test()
    elif cmd == "route":
        prompt = sys.argv[2]
        result = get_model(prompt)
        print(f"Prompt: {prompt}")
        print(f"路由决策 → {result['tier'].value} ({result['model']})")
        print(f"  成本: ¥{result['cost_per_10k']}/万Token | {result['note']}")
    elif cmd == "call":
        # 直接调用 LLM（单次，无 fallback）
        prompt = sys.argv[2] if len(sys.argv) > 2 else "你好，回复一个OK即可。"
        task_type = sys.argv[3] if len(sys.argv) > 3 else "test"
        provider = sys.argv[4] if len(sys.argv) > 4 else "deepseek"
        model = sys.argv[5] if len(sys.argv) > 5 else "deepseek-v4-flash"
        mc = {
            "model": model,
            "provider": provider,
            "base_url": DEEPSEEK_BASE_URL if provider == "deepseek" else CATROUTER_BASE_URL,
            "cost_per_10k": 0.5,
        }
        result = call_llm(prompt, mc, task=task_type)
        if result["success"]:
            print(f"\n✅ 调用成功 ({result['duration_ms']}ms)")
            print(f"   {result['response'][:200]}")
            print(f"\n   📊 Token: in={result['input_tokens']} out={result['output_tokens']}")
            print(f"   💰 成本: ¥{result['cost_cny']}")
            if result.get("prompt_cache_hit_tokens"):
                print(
                    f"   📦 Cache: hit={result['prompt_cache_hit_tokens']} miss={result['prompt_cache_miss_tokens']}"
                )
        else:
            print(f"\n❌ 调用失败: {result.get('error')}")
    elif cmd == "fallback":
        # 带 Fallback 链的完整调用
        prompt = sys.argv[2] if len(sys.argv) > 2 else "你好，回复一个OK即可。"
        task_type = sys.argv[3] if len(sys.argv) > 3 else "test"
        result = call_with_fallback(prompt, task_type, verbose=True)
        if result["success"]:
            print(f"\n✅ 最终成功: {result['model']} ({result['provider']})")
            print(f"   {result['response'][:200]}")
            print(f"\n   📊 Token: in={result['input_tokens']} out={result['output_tokens']}")
            print(f"   💰 成本: ¥{result['cost_cny']}")
            print(f"   🔀 使用了 Fallback: {'是' if result.get('fallback_used') else '否'}")
        else:
            print(f"\n❌ 全部失败: {result.get('error', '未知错误')}")
            for i, a in enumerate(result.get("attempts", [])):
                print(
                    f"   #{i + 1} {a['model']}: {'✅' if a['success'] else '❌'} {a.get('error', '')[:80]}"
                )
    elif cmd == "budget":
        # 测试预算感知调用
        from budget_guard import check_budget_status

        prompt = sys.argv[2] if len(sys.argv) > 2 else "分析000001的技术形态"
        budget = check_budget_status()
        print(f"💰 当前预算: {budget['msg']}")
        result = call_with_fallback(prompt, budget_status=budget, verbose=True)
        if result["success"]:
            print(f"\n✅ 预算感知调用成功: {result['model']} | ¥{result['cost_cny']}")
    else:
        print(
            "用法: python router.py [test|route '<prompt>'|call '<prompt>' [task] [provider] [model]|fallback '<prompt>' [task]|budget '<prompt>']"
        )
