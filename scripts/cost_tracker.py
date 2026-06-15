"""
cost_tracker.py — AI调用成本追踪器（v2.1 + Prompt Cache 支持）
=====================================
适用：1-2人小团队，月预算¥400
集成：与 Claw/QTS 项目的 LLM 调用入口无缝对接
输出：JSONL 日志 → daily_report() / monthly_report()
预算：¥400/月硬约束，¥350触发Flash锁定
新增：v2.1 支持 prompt_cache_hit_tokens / prompt_cache_miss_tokens 追踪

版本：v2.1 | 2026-06-14 — 新增 Prompt Cache 指标追踪
"""

import json
import os
import sys
import threading
from datetime import datetime, date
from pathlib import Path
from typing import Optional

# ============================================================
# 模型定价（¥/万Token，2026年6月）
# ============================================================
MODEL_PRICES = {
    # 旗舰模型
    "claude-opus-4-20250514":    {"input": 36.0,  "output": 180.0},
    "claude-sonnet-4-20250514":  {"input": 21.6,  "output": 108.0},
    "gpt-5":                     {"input": 18.0,  "output": 72.0},
    "claude-haiku-3-5":          {"input": 0.5,   "output": 1.5},

    # DeepSeek 系列（你们的主力）
    "deepseek-v4-pro":       {"input": 4.0,   "output": 12.0},
    "deepseek-v4-flash":     {"input": 0.5,   "output": 1.5},

    # 老模型兼容
    "gpt-4o":                {"input": 18.0,  "output": 72.0},
    "gpt-4o-mini":           {"input": 1.1,   "output": 4.4},
    "deepseek-v3":           {"input": 1.0,   "output": 2.0},

    # 本地模型（零成本）
    "ollama-local":          {"input": 0.0,   "output": 0.0},
    "qwen2.5-7b":            {"input": 0.0,   "output": 0.0},
    "qwen2.5-14b":           {"input": 0.0,   "output": 0.0},

    # 其他国产模型
    "kimi-k2.6":             {"input": 8.0,   "output": 24.0},
    "glm-5.1":               {"input": 3.5,   "output": 10.5},
    "glm-5.0-turbo":         {"input": 3.0,   "output": 9.0},
    "hy3-preview":           {"input": 2.0,   "output": 6.0},
    "deepseek-reasoner":     {"input": 8.0,   "output": 24.0},
}

# ============================================================
# 预算约束
# ============================================================
MONTHLY_BUDGET_CNY = 400.0      # ¥400/月硬上限
FLASH_LOCK_THRESHOLD = 350.0    # ¥350触发Flash锁定
DAILY_WARNING_CNY = 25.0        # 日消费¥25告警

# 日志路径（~/.ai_cost_log.jsonl）
LOG_FILE = Path.home() / ".ai_cost_log.jsonl"

# 缓存日志路径（~/.ai_cache_log.jsonl）
CACHE_LOG_FILE = Path.home() / ".ai_cache_log.jsonl"

# 写入锁（防并发损坏）
_write_lock = threading.Lock()


# ============================================================
# 核心追踪函数
# ============================================================

def log_call(model: str, input_tokens: int, output_tokens: int,
             task: str = "", project: str = "",
             prompt_cache_hit_tokens: int = None,
             prompt_cache_miss_tokens: int = None) -> float:
    """
    记录一次AI API调用（支持 Prompt Cache 指标）。

    参数
    ----
    model : str
        模型名称（匹配 MODEL_PRICES 中的 key，支持部分匹配）
    input_tokens : int
        输入 Token 数
    output_tokens : int
        输出 Token 数
    task : str
        任务描述（如 "代码审查"、"选股分析"）
    project : str
        项目名（如 "Claw"、"QTS"）
    prompt_cache_hit_tokens : int, optional
        命中缓存的输入 Token 数（DeepSeek API 返回）
    prompt_cache_miss_tokens : int, optional
        未命中缓存的输入 Token 数（DeepSeek API 返回）

    返回
    ----
    float : 本次调用花费（¥）
    """
    # 模糊匹配模型定价
    model_key = _match_model(model)
    prices = MODEL_PRICES.get(model_key, {"input": 0, "output": 0})

    # 计算成本（元）
    cost = (input_tokens * prices["input"] + output_tokens * prices["output"]) / 10000

    record = {
        "ts":       datetime.now().isoformat(),
        "date":     date.today().isoformat(),
        "model":    model,
        "model_key": model_key,
        "input":    input_tokens,
        "output":   output_tokens,
        "cost_cny": round(cost, 6),
        "task":     task,
        "project":  project,
    }

    # 如果提供了缓存指标，追加写入日志
    if prompt_cache_hit_tokens is not None or prompt_cache_miss_tokens is not None:
        record["prompt_cache_hit_tokens"] = prompt_cache_hit_tokens or 0
        record["prompt_cache_miss_tokens"] = prompt_cache_miss_tokens or 0

        # 同时写入独立的缓存日志
        CACHE_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _write_lock:
            with open(CACHE_LOG_FILE, "a") as f:
                f.write(json.dumps({
                "ts":       record["ts"],
                "date":     record["date"],
                "model":    model,
                "model_key": model_key,
                "prompt_cache_hit_tokens": prompt_cache_hit_tokens or 0,
                "prompt_cache_miss_tokens": prompt_cache_miss_tokens or 0,
                "total_input_tokens": (prompt_cache_hit_tokens or 0) + (prompt_cache_miss_tokens or 0),
                "hit_rate": round((prompt_cache_hit_tokens or 0) / max(1, (prompt_cache_hit_tokens or 0) + (prompt_cache_miss_tokens or 0)) * 100, 2),
                "task":     task,
                "project":  project,
            }, ensure_ascii=False) + "\n")

    # 追加写入主日志
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return cost


def _match_model(model: str) -> str:
    """模糊匹配模型名称到定价表"""
    model_lower = model.lower()
    # 精确匹配优先
    if model_lower in MODEL_PRICES:
        return model_lower

    # 模糊匹配
    for key in sorted(MODEL_PRICES.keys(), key=len, reverse=True):
        if key in model_lower or model_lower in key:
            return key

    print(f"⚠️ 未知模型: {model}，按¥0计费。请在 MODEL_PRICES 中添加定价。")
    return "unknown"


# ============================================================
# 报告函数
# ============================================================

def _load_records(date_filter: str = None) -> list:
    """加载日志文件中符合条件的记录"""
    if not LOG_FILE.exists():
        return []

    records = []
    with open(LOG_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if date_filter is None or r.get("date", "").startswith(date_filter):
                records.append(r)
    return records


def get_monthly_spent(month: str = None) -> float:
    """返回指定月份已花费金额（¥），由 budget_guard 调用以避免重复读 JSONL"""
    month = month or date.today().strftime("%Y-%m")
    records = _load_records(month)
    return sum(r.get("cost_cny", 0) for r in records)


def daily_report(target_date: str = None, records: list = None) -> dict:
    """
    打印并返回每日成本报告。

    参数
    ----
    target_date : str, optional
        目标日期（YYYY-MM-DD），默认今天
    records : list, optional
        预加载的记录（避免重复读文件）

    返回
    ----
    dict : { "total", "count", "by_model", "by_project", "by_task" }
    """
    target = target_date or date.today().isoformat()
    if records is None:
        records = _load_records(target)

    if not records:
        print(f"\n📊 今日成本报告 ({target})")
        print("   暂无调用记录")
        return {"total": 0, "count": 0}

    total = sum(r["cost_cny"] for r in records)

    # 按模型聚合
    by_model = {}
    by_project = {}
    by_task = {}
    for r in records:
        m = r.get("model_key", r["model"])
        by_model[m] = by_model.get(m, 0) + r["cost_cny"]

        p = r.get("project", "未知")
        by_project[p] = by_project.get(p, 0) + r["cost_cny"]

        t = r.get("task", "未知")
        by_task[t] = by_task.get(t, 0) + r["cost_cny"]

    print(f"\n📊 今日成本报告 ({target})")
    print(f"   {'总花费':>10}：¥{total:.4f}")
    print(f"   {'调用次数':>10}：{len(records)}")
    print(f"   {'项目':>10}：")
    for p, c in sorted(by_project.items(), key=lambda x: -x[1]):
        print(f"     {p}: ¥{c:.4f}")
    print(f"   模型分布：")
    for m, c in sorted(by_model.items(), key=lambda x: -x[1]):
        print(f"     {m}: ¥{c:.4f}")

    # 日预算警告
    if total > DAILY_WARNING_CNY:
        print(f"   ⚠️  日花费 ¥{total:.2f} 超出警告线 ¥{DAILY_WARNING_CNY}")

    return {
        "total": total,
        "count": len(records),
        "by_model": by_model,
        "by_project": by_project,
        "by_task": by_task,
    }


def monthly_report(year_month: str = None) -> dict:
    """
    打印并返回月度成本汇总。

    参数
    ----
    year_month : str, optional
        目标月份（YYYY-MM），默认当月

    返回
    ----
    dict : { "total", "count", "by_model", "by_project", "remain_budget", "projection" }
    """
    month = year_month or date.today().strftime("%Y-%m")
    records = _load_records(month)

    total = sum(r["cost_cny"] for r in records)

    if not records:
        print(f"\n📅 {month} 月度汇总 — 暂无数据")
        return {"total": 0, "count": 0}

    by_model = {}
    by_project = {}
    for r in records:
        m = r.get("model_key", r["model"])
        by_model[m] = by_model.get(m, 0) + r["cost_cny"]
        p = r.get("project", "未知")
        by_project[p] = by_project.get(p, 0) + r["cost_cny"]

    # 预算预测
    today_day = date.today().day
    days_in_month = 30  # 近似
    projection = total / today_day * days_in_month if today_day > 0 else 0
    remain = MONTHLY_BUDGET_CNY - total

    print(f"\n📅 {month} 月度汇总")
    print(f"   {'=' * 40}")
    print(f"   {'总花费':>10}：¥{total:.2f}")
    print(f"   {'调用次数':>10}：{len(records)}")
    print(f"   {'剩余预算':>10}：¥{remain:.2f}")
    print(f"   {'预估月底':>10}：¥{projection:.0f} {'⚠️ 超预算!' if projection > MONTHLY_BUDGET_CNY else '✅ 预算内'}" if projection > 0 else "")
    print(f"   模型分布：")
    for m, c in sorted(by_model.items(), key=lambda x: -x[1]):
        bar = "█" * max(1, int(c / total * 30)) if total > 0 else ""
        print(f"     {m:>20}：¥{c:<8.2f} {bar}")
    print(f"   项目分布：")
    for p, c in sorted(by_project.items(), key=lambda x: -x[1]):
        bar = "█" * max(1, int(c / total * 30)) if total > 0 else ""
        print(f"     {p:>20}：¥{c:<8.2f} {bar}")

    if projection > MONTHLY_BUDGET_CNY:
        print(f"   🔴 按当前速度，月底将超预算 ¥{projection - MONTHLY_BUDGET_CNY:.0f}！")
        print(f"      建议：立即执行 budget_guard 降级策略")
    elif remain < (MONTHLY_BUDGET_CNY * 0.1):
        print(f"   🟡 剩余预算不足10%，请注意控制")
    else:
        print(f"   🟢 预算情况良好")

    return {
        "total": total,
        "count": len(records),
        "by_model": by_model,
        "by_project": by_project,
        "remain_budget": remain,
        "projection": projection,
    }


def top_expensive_tasks(n: int = 5) -> list:
    """找出本月最烧钱的 N 个任务类型"""
    month = date.today().strftime("%Y-%m")
    records = _load_records(month)

    by_task = {}
    for r in records:
        t = r.get("task", "未知")
        by_task[t] = by_task.get(t, 0) + r["cost_cny"]

    sorted_tasks = sorted(by_task.items(), key=lambda x: -x[1])
    print(f"\n🔥 本月TOP{n}高消耗任务")
    for i, (task, cost) in enumerate(sorted_tasks[:n], 1):
        pct = cost / sum(by_task.values()) * 100
        print(f"   {i}. {task} — ¥{cost:.2f} ({pct:.1f}%)")

    return sorted_tasks[:n]


# ============================================================
# 成本估算配置（用于无真实 Token 计数时的估算日志）
#
# 每项记录：
#   model:    分配的模型（留空则从 MODEL_PRICES 自动匹配）
#   inp_est:  预估输入 Token 数
#   out_est:  预估输出 Token 数
#   freq:     调度频次（仅供参考，不计入估算逻辑）
# ============================================================

AUTO_COST_ESTIMATES = {
    # === 投资分析类（A股） ===
    "盘前分析":       {"model": "deepseek-v4-flash", "inp_est": 3000, "out_est": 800,  "freq": "日"},
    "收盘回顾":       {"model": "deepseek-v4-flash", "inp_est": 3000, "out_est": 800,  "freq": "日"},
    "公众号投资早报": {"model": "deepseek-v4-flash", "inp_est": 4000, "out_est": 1000, "freq": "日"},
    "财报预警":       {"model": "deepseek-v4-flash", "inp_est": 3000, "out_est": 500,  "freq": "日"},
    "股票池技术体检": {"model": "deepseek-v4-flash", "inp_est": 3500, "out_est": 1200, "freq": "周"},
    "宏观数据周报":   {"model": "deepseek-v4-flash", "inp_est": 3000, "out_est": 1500, "freq": "周"},
    "每日复盘":       {"model": "deepseek-v4-flash", "inp_est": 3500, "out_est": 1000, "freq": "日"},
    "文章归档索引":   {"model": "deepseek-v4-flash", "inp_est": 2000, "out_est": 300,  "freq": "日"},

    # === 投顾操盘类 ===
    "智能选股":       {"model": "kimi-k2.6",         "inp_est": 6000, "out_est": 2000, "freq": "日"},
    "每周总结":       {"model": "glm-5.1",           "inp_est": 5000, "out_est": 2000, "freq": "周"},
    "月度总结":       {"model": "glm-5.1",           "inp_est": 5000, "out_est": 2500, "freq": "月"},
    "季度回顾":       {"model": "kimi-k2.6",         "inp_est": 8000, "out_est": 3000, "freq": "季"},
    "半年回顾":       {"model": "kimi-k2.6",         "inp_est": 8000, "out_est": 3000, "freq": "半年"},
    "年度回顾":       {"model": "kimi-k2.6",         "inp_est": 10000,"out_est": 4000, "freq": "年"},
    "下周前瞻":       {"model": "kimi-k2.6",         "inp_est": 6000, "out_est": 2000, "freq": "周"},

    # === 美股 ===
    "美股盘前分析":   {"model": "deepseek-v4-flash", "inp_est": 3000, "out_est": 800,  "freq": "日"},
    "美股收盘回顾":   {"model": "deepseek-v4-pro",   "inp_est": 4000, "out_est": 1200, "freq": "日"},
    "美股盘中监控":   {"model": "deepseek-v4-flash", "inp_est": 2000, "out_est": 400,  "freq": "高频"},

    # === 系统维护 ===
    "信号溯源":       {"model": "deepseek-reasoner", "inp_est": 6000, "out_est": 2000, "freq": "周"},
    "全局记忆":       {"model": "kimi-k2.6",         "inp_est": 5000, "out_est": 2000, "freq": "月"},
    "成本监控":       {"model": "deepseek-v4-flash", "inp_est": 1500, "out_est": 400,  "freq": "日"},
    "健康巡检":       {"model": "deepseek-v4-flash", "inp_est": 1000, "out_est": 300,  "freq": "日"},
    "心跳检测":       {"model": "deepseek-v4-flash", "inp_est": 300,  "out_est": 100,  "freq": "时"},
    "状态巡检":       {"model": "deepseek-v4-flash", "inp_est": 1500, "out_est": 500,  "freq": "日"},
}


def log_estimate(automation_name: str, project: str = "Claw",
                 override_model: str = None,
                 override_inp: int = None,
                 override_out: int = None) -> Optional[float]:
    """
    根据自动化名称估算本次调用成本并记录到日志文件。

    参数
    ----
    automation_name : str
        自动化任务名称（从 AUTO_COST_ESTIMATES 中查找）
    project : str
        项目名称
    override_model : str, optional
        覆盖模型（用于非标准配置）
    override_inp : int, optional
        覆盖输入 Token 估算值
    override_out : int, optional
        覆盖输出 Token 估算值

    返回
    ----
    float or None: 本次估算花费（¥），如果未找到配置则返回 None
    """
    # 查找配置（精确匹配优先，按 key 长度降序避免"盘前分析"误配"美股盘前分析"）
    config = None
    matched_key = None

    # 按 key 长度降序排序，确保"美股盘前分析"先于"盘前分析"匹配
    sorted_keys = sorted(AUTO_COST_ESTIMATES.keys(), key=len, reverse=True)

    for key in sorted_keys:
        # 精确匹配
        if key == automation_name:
            config = AUTO_COST_ESTIMATES[key]
            matched_key = key
            break
        # 包含匹配（长 key 优先）
        if key in automation_name:
            config = AUTO_COST_ESTIMATES[key]
            matched_key = key
            break

    # 最后尝试：自动化名称包含配置 key
    if config is None:
        for key in sorted_keys:
            if automation_name in key:
                config = AUTO_COST_ESTIMATES[key]
                matched_key = key
                break

    if config is None:
        print(f"⚠️ 未找到自动化「{automation_name}」的估算配置，跳过成本记录")
        return None

    model = override_model or config["model"]
    inp = override_inp or config["inp_est"]
    out = override_out or config["out_est"]

    cost = log_call(model, inp, out, task=automation_name, project=project)
    print(f"📝 估算日志: «{matched_key}» → {model} | 输入≈{inp} 输出≈{out} | ¥{cost:.6f}")
    return cost


def log_estimate_all_today(project: str = "Claw") -> float:
    """
    列出所有注册自动化的单次估算成本（含频次标注），
    供成本监控日报参考。

    返回
    ----
    float : 所有自动化单次调用总成本（¥）
    """
    total_cost = 0.0
    logged = []

    print("📋 各自动化单次调用估算成本：")

    for name, cfg in sorted(AUTO_COST_ESTIMATES.items()):
        model = cfg.get("model", "?")
        inp = cfg.get("inp_est", 0)
        out = cfg.get("out_est", 0)
        freq = cfg.get("freq", "?")
        prices = MODEL_PRICES.get(model, {"input": 0, "output": 0})
        cost = (inp * prices["input"] + out * prices["output"]) / 10000
        total_cost += cost
        logged.append((name, cost, freq))
        print(f"  {name:>16}  {freq:>4}  ¥{cost:<8.6f}")

    print(f"\n{'=' * 50}")
    print(f"📊 全量自动化单次调用估算总成本: ¥{total_cost:.4f}")
    if logged:
        print(f"   单次最高: ¥{max(logged, key=lambda x: x[1])[1]:.4f} ({max(logged, key=lambda x: x[1])[0]})")
    print(f"   注意: 这是所有自动化各跑一次的成本，实际取决于触发频次")
    print(f"{'=' * 50}")

    return total_cost



# ============================================================
# Prompt Cache 报告
# ============================================================

def cache_report(target_date: str = None) -> dict:
    """
    打印并返回 Prompt Cache 命中率报告。

    从 ~/.ai_cache_log.jsonl 读取缓存指标数据。

    参数
    ----
    target_date : str, optional
        目标日期（YYYY-MM-DD），默认今天

    返回
    ----
    dict : 缓存指标汇总
    """
    target = target_date or date.today().isoformat()

    if not CACHE_LOG_FILE.exists():
        print(f"\n📊 Prompt Cache 命中率报告 ({target})")
        print("   暂无缓存日志数据。")
        print("   提示: 需要 API 调用时传入 prompt_cache_hit_tokens / miss_tokens 参数")
        return {}

    records = []
    for line in CACHE_LOG_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("date", "").startswith(target):
            records.append(r)

    if not records:
        print(f"\n📊 Prompt Cache 命中率报告 ({target})")
        print(f"   {target} 无缓存数据")
        print(f"   缓存日志文件: {CACHE_LOG_FILE}")
        return {}

    total_hit = sum(r.get("prompt_cache_hit_tokens", 0) for r in records)
    total_miss = sum(r.get("prompt_cache_miss_tokens", 0) for r in records)
    total_input = total_hit + total_miss
    overall_hit_rate = round(total_hit / total_input * 100, 2) if total_input > 0 else 0.0

    # 按任务聚合
    by_task = {}
    for r in records:
        task = r.get("task", "未知")
        if task not in by_task:
            by_task[task] = {"hit": 0, "miss": 0, "count": 0}
        by_task[task]["hit"] += r.get("prompt_cache_hit_tokens", 0)
        by_task[task]["miss"] += r.get("prompt_cache_miss_tokens", 0)
        by_task[task]["count"] += 1

    # 按模型聚合
    by_model = {}
    for r in records:
        model = r.get("model_key", r.get("model", "未知"))
        if model not in by_model:
            by_model[model] = {"hit": 0, "miss": 0, "count": 0}
        by_model[model]["hit"] += r.get("prompt_cache_hit_tokens", 0)
        by_model[model]["miss"] += r.get("prompt_cache_miss_tokens", 0)
        by_model[model]["count"] += 1

    print(f"\n📊 Prompt Cache 命中率报告 ({target})")
    print(f"   {'=' * 50}")
    print(f"   总调用次数:      {len(records)}")
    print(f"   缓存命中 Tokens: {total_hit:,}")
    print(f"   缓存未命中 Tokens: {total_miss:,}")
    print(f"   总输入 Tokens:   {total_input:,}")

    # 根据缓存命中计算节省
    # 未命中定价按 deepseek-v4-flash 计算
    miss_price = 0.5  # ¥/万Token
    hit_price = 0.008  # 缓存命中价格（约1/60）
    actual_cost = (total_hit * hit_price + total_miss * miss_price) / 10000
    nocache_cost = total_input * miss_price / 10000
    savings = nocache_cost - actual_cost

    # 颜色标记
    if overall_hit_rate >= 95:
        status = "🟢"
    elif overall_hit_rate >= 80:
        status = "🟡"
    else:
        status = "🔴"

    print(f"   {'─' * 50}")
    print(f"   {status} 综合缓存命中率:  {overall_hit_rate:.2f}%")

    print(f"\n   模型维度:")
    for model, data in sorted(by_model.items(), key=lambda x: -x[1]["hit"] + x[1]["miss"]):
        rate = round(data["hit"] / max(1, data["hit"] + data["miss"]) * 100, 2)
        bar = "█" * max(1, int(rate / 5))
        print(f"     {model:>20}: {rate:>6.2f}%  ({data['hit']:,}H / {data['miss']:,}M) {bar}")

    print(f"\n   任务维度:")
    for task, data in sorted(by_task.items(), key=lambda x: -x[1]["hit"] + x[1]["miss"]):
        rate = round(data["hit"] / max(1, data["hit"] + data["miss"]) * 100, 2)
        print(f"     {task:>20}: {rate:>6.2f}%  (调用{data['count']}次)")

    print(f"\n   成本效益:")
    print(f"     实际成本:    ¥{actual_cost:.4f}")
    print(f"     无缓存成本: ¥{nocache_cost:.4f}")
    print(f"     节省:       ¥{savings:.4f} ({round(savings/nocache_cost*100 if nocache_cost else 0, 1)}%)")
    print(f"   {'=' * 50}")

    return {
        "date": target,
        "total_calls": len(records),
        "total_hit_tokens": total_hit,
        "total_miss_tokens": total_miss,
        "total_input_tokens": total_input,
        "overall_hit_rate": overall_hit_rate,
        "by_model": by_model,
        "by_task": by_task,
        "actual_cost_cny": round(actual_cost, 6),
        "nocache_cost_cny": round(nocache_cost, 6),
        "savings_cny": round(savings, 6),
    }


# ============================================================
# CLI 入口
# ============================================================

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "daily"

    if cmd == "daily":
        daily_report()
    elif cmd == "monthly":
        monthly_report()
    elif cmd == "top":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        top_expensive_tasks(n)
    elif cmd == "log":
        # 快速记录一次调用（命令行）
        model = sys.argv[2]
        inp = int(sys.argv[3])
        out = int(sys.argv[4])
        task = sys.argv[5] if len(sys.argv) > 5 else ""
        cost = log_call(model, inp, out, task)
        print(f"记录完成: {model} → ¥{cost:.6f}")
    elif cmd == "log_estimate":
        # 按自动化名称估算成本
        if len(sys.argv) < 2:
            print("用法: python cost_tracker.py log_estimate <自动化名称> [项目名]")
            sys.exit(1)
        name = sys.argv[2]
        project = sys.argv[3] if len(sys.argv) > 3 else "Claw"
        cost = log_estimate(name, project)
        if cost is None:
            sys.exit(1)
    elif cmd == "estimate_today":
        # 估算当天所有自动化成本
        project = sys.argv[2] if len(sys.argv) > 2 else "Claw"
        log_estimate_all_today(project)
    elif cmd == "cache":
        # 缓存命中率报告
        target = sys.argv[2] if len(sys.argv) > 2 else None
        cache_report(target)
    elif cmd == "log_cache":
        # 记录一次带缓存指标的调用（命令行调试用）
        if len(sys.argv) < 5:
            print("用法: python cost_tracker.py log_cache <model> <hit_tokens> <miss_tokens> [task]")
            sys.exit(1)
        model = sys.argv[2]
        hit = int(sys.argv[3])
        miss = int(sys.argv[4])
        task = sys.argv[5] if len(sys.argv) > 5 else ""
        cost = log_call(model, hit + miss, 0, task, prompt_cache_hit_tokens=hit, prompt_cache_miss_tokens=miss)
        print(f"缓存记录完成: {model} hit={hit} miss={miss} → ¥{cost:.6f}")
    else:
        print("用法: python cost_tracker.py [daily|monthly|top [N]|log <model> <in> <out> [task]|log_estimate <名称> [项目]|estimate_today [项目]|cache [日期]|log_cache <model> <hit> <miss> [task]]")
