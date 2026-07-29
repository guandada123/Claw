"""北辰多智能体辩论引擎

三环辩论流程 — stance → peer_review → convergence
使用 deepseek-v4-flash 通过本地代理（127.0.0.1:9999）调用，自动降级直连。
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from claw.debate.expert_prompts import (
    EXPERT_DEFINITIONS,
    build_system_prompt,
    build_user_prompt,
)

logger = logging.getLogger("debate")

DATA_DIR = Path(__file__).parent.parent.parent.parent / ".workbuddy" / "data" / "debate"
RESULT_FILE = DATA_DIR / "debate_result.json"

# ============================================================
#  LLM 调用基础设施
# ============================================================


def _llm_config() -> dict:
    """探测本地代理，返回 {base_url, api_key}"""
    base_url = "https://api.deepseek.com/v1"
    api_key = os.environ.get("DEEPSEEK_API_KEY", "sk-placeholder")
    try:
        s = socket.create_connection(("127.0.0.1", 9999), timeout=1.5)
        s.close()
        base_url = "http://127.0.0.1:9999/v1"
        logger.info("debate: 使用本地代理 %s", base_url)
    except Exception:
        logger.info("debate: 本地代理不可达，降级直连")
    return {"base_url": base_url, "api_key": api_key}


def _call_llm(
    system: str,
    user: str,
    temperature: float = 0.3,
    max_tokens: int = 500,
) -> str:
    """调 LLM，返回文本响应。内置 3 次重试+指数退避。失败抛 RuntimeError"""
    cfg = _llm_config()
    for attempt in range(3):
        try:
            resp = requests.post(
                f"{cfg['base_url']}/chat/completions",
                json={
                    "model": "deepseek-v4-flash",
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                headers={"Authorization": f"Bearer {cfg['api_key']}"},
                timeout=45,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            if attempt < 2:
                time.sleep(1.5 ** attempt)
            else:
                raise RuntimeError(f"LLM call failed after 3 attempts: {exc}") from exc


def _parse_json_response(text: str) -> dict:
    """从 LLM 文本中提取 JSON 对象。失败返回 {}"""
    text = text.strip()
    # 去掉可能的 markdown 围栏
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # 提取第一个 { ... } 块
    m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    # 嵌套花括号更复杂的提取
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    pass
    return {}


# ============================================================
#  辩论三环
# ============================================================


def _stance_phase(code: str, name: str, data: dict) -> list[dict]:
    """第一环：7 专家并行分析（Stance）
    返回：[{expert, name, stance, confidence, reasoning, risk_flags}, ...]
    """
    user_prompt = build_user_prompt(code, name, data)
    # 注入历史记忆上下文（如果存在）
    memory_ctx = data.get("memory_context", "")
    if memory_ctx:
        user_prompt += f"\n\n【历史参考案例（同股票/同板块）】\n{memory_ctx}"
    expert_keys = list(EXPERT_DEFINITIONS.keys())

    def _analyze_one(key: str) -> dict:
        system = build_system_prompt(key)
        for attempt in range(3):
            try:
                raw = _call_llm(system, user_prompt, temperature=0.3, max_tokens=400)
                parsed = _parse_json_response(raw)
                if parsed:
                    stance = parsed.get("stance", "HOLD").upper()
                    if stance not in ("BUY", "HOLD", "SELL"):
                        stance = "HOLD"
                    return {
                        "expert": key,
                        "name": EXPERT_DEFINITIONS[key]["name"],
                        "stance": stance,
                        "confidence": min(max(float(parsed.get("confidence", 0.5)), 0), 1),
                        "reasoning": parsed.get("reasoning", "")[:150],
                        "risk_flags": parsed.get("risk_flags", [])[:3],
                    }
            except Exception as exc:
                logger.warning("专家 %s 第 %d 次调用失败: %s", key, attempt + 1, exc)
                if attempt < 2:
                    time.sleep(1)
        # 3 次失败后返回降级意见
        return {
            "expert": key,
            "name": EXPERT_DEFINITIONS[key]["name"],
            "stance": "HOLD",
            "confidence": 0.3,
            "reasoning": "分析调用失败，默认持有",
            "risk_flags": ["LLM调用失败"],
        }

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=7) as pool:
        futures = {pool.submit(_analyze_one, k): k for k in expert_keys}
        for future in as_completed(futures):
            results.append(future.result())

    # 按 expert key 排序确保输出稳定
    results.sort(key=lambda r: expert_keys.index(r["expert"]))
    return results


def _peer_review_phase(
    stances: list[dict], code: str, name: str, max_rounds: int = 2
) -> list[dict]:
    """第二环：Bull/Bear Researcher 双向辩论。
    不再按多数派划线——显式指定牛市和熊市研究员公平对抗。即使全票一致也找反方。"""
    reviews: list[dict] = []

    buy_args = [s for s in stances if s["stance"] == "BUY"]
    sell_args = [s for s in stances if s["stance"] == "SELL"]
    hold_args = [s for s in stances if s["stance"] == "HOLD"]
    buy_summary = "; ".join(s["reasoning"][:80] for s in buy_args) or "无明确看多论据"
    sell_summary = "; ".join(s["reasoning"][:80] for s in sell_args) or "无明确看空论据"
    hold_summary = "; ".join(s["reasoning"][:80] for s in hold_args[:2]) if hold_args else ""

    # 第一轮：双方阐述核心观点
    try:
        bull_view = _call_llm(
            "你是一位坚持寻找价值的牛市研究员。即使市场悲观，也要找到被低估的积极信号。",
            f"你是牛市研究员，负责为 {name}({code}) 寻找买入理由。\n"
            f"看多论据: {buy_summary}\n看空论据: {sell_summary}\n观望: {hold_summary or '无'}\n"
            f"请用 3-5 句话阐述最有力的买入理由。即使多数人看空也要找积极因素。<=150字",
            temperature=0.5, max_tokens=250,
        )
    except Exception:
        bull_view = buy_summary
    try:
        bear_view = _call_llm(
            "你是一位敏锐的风险猎手。即使市场乐观，也要找出被忽视的致命弱点。",
            f"你是熊市研究员，负责为 {name}({code}) 寻找风险信号。\n"
            f"看多论据: {buy_summary}\n看空论据: {sell_summary}\n观望: {hold_summary or '无'}\n"
            f"请用 3-5 句话阐述最值得警惕的风险。即使多数人看多也要找隐患。<=150字",
            temperature=0.5, max_tokens=250,
        )
    except Exception:
        bear_view = sell_summary

    reviews.append({
        "round": 1, "mode": "bull_bear_opening",
        "bull_view": bull_view.strip()[:250],
        "bear_view": bear_view.strip()[:250],
    })

    # 第二轮：互相质疑
    if max_rounds < 2:
        return reviews

    try:
        bull_rebuttal = _call_llm(
            "你在反驳对手但保持理性。承认对手的有效观点，同时指出其盲区。",
            f"熊市研究员认为: {bear_view.strip()[:150]}\n"
            f"作为牛市研究员，请逐条反驳或承认其中有效风险，更新你的买入建议。<=120字",
            temperature=0.4, max_tokens=200,
        )
    except Exception:
        bull_rebuttal = ""
    try:
        bear_rebuttal = _call_llm(
            "你在质疑对手的乐观假设。用数据和逻辑而非情绪。",
            f"牛市研究员认为: {bull_view.strip()[:150]}\n"
            f"作为熊市研究员，请逐条质疑或用数据反驳其乐观假设。<=120字",
            temperature=0.4, max_tokens=200,
        )
    except Exception:
        bear_rebuttal = ""

    reviews.append({
        "round": 2, "mode": "bull_bear_rebuttal",
        "bull_rebuttal": bull_rebuttal.strip()[:200],
        "bear_rebuttal": bear_rebuttal.strip()[:200],
    })

    return reviews


def _convergence_phase(
    stances: list[dict],
    reviews: list[dict],
    code: str,
    name: str,
    data: dict,
) -> dict:
    """第三环：Convergence — 综合判断 + 动态止损 + 多因子评分"""
    try:
        system = (
            "你是一位 A 股投研首席策略师，正在做最终判决。"
            "请基于专家辩论和牛熊研究员对抗结果，给出最终决定、动态止损线和四因子评分。"
            "仅输出 JSON，不要任何额外文字。"
        )
        short_stances = []
        for s in stances:
            short_stances.append(f"  {s['name']}: {s['stance']} ({s['confidence']:.0%}) — {s['reasoning'][:80]}")

        # 适应新 Bull/Bear 模式
        debate_lines = []
        for r in reviews:
            mode = r.get("mode", "")
            if mode == "bull_bear_opening":
                debate_lines.append(f"  牛方: {r.get('bull_view','')[:100]}")
                debate_lines.append(f"  熊方: {r.get('bear_view','')[:100]}")
            elif mode == "bull_bear_rebuttal":
                if r.get("bull_rebuttal"):
                    debate_lines.append(f"  牛方反驳: {r['bull_rebuttal'][:100]}")
                if r.get("bear_rebuttal"):
                    debate_lines.append(f"  熊方反驳: {r['bear_rebuttal'][:100]}")
            else:
                debate_lines.append(f"  {r.get('reviewer','')} -> {r.get('target','')}: {r.get('challenge','')[:80]}")

        price = data.get("price", "N/A")
        change_pct = data.get("change_pct", 0)
        vol_hint = "高波动" if abs(change_pct) > 3 else ("低波动" if abs(change_pct) < 1 else "正常波动")

        convergence_prompt = (
            f"请审阅对 {name}({code}) 的完整辩论并做出最终判决。\n\n"
            f"【行情】价格 {price} | 涨跌 {change_pct:+.2f}% ({vol_hint})\n\n"
            f"【7位专家表态】\n"
            + "\n".join(short_stances)
            + "\n\n【牛熊研究员对抗】\n"
            + ("\n".join(debate_lines) if debate_lines else "  无辩论")
            + "\n\n输出 JSON: {\"consensus\":\"BUY|HOLD|SELL\","
            + "\"weighted_score\":0-1,\"confidence\":0-1,"
            + "\"summary\":\"<=120字理由\",\"risk_flags\":[\"风险1\"],"
            + "\"stop_loss_pct\":-8.0,"
            + "\"factor_scores\":{\"value\":0-100,\"quality\":0-100,\"growth\":0-100,\"momentum\":0-100}}"
            + f"\nstop_loss_pct: 根据行情波动({vol_hint})和辩论置信度动态调整"
            + "（高波动/-10至-15, 低波动/-5至-8, 正常/-8）。"
        )

        raw = _call_llm(system, convergence_prompt, temperature=0.2, max_tokens=700)
        parsed = _parse_json_response(raw)
        if not parsed:
            logger.warning("convergence JSON解析失败, raw[:200]=%s", raw[:200])
            return _fallback_verdict(stances)
        consensus = parsed.get("consensus", "HOLD").upper()
        if consensus not in ("BUY", "HOLD", "SELL"):
            consensus = "HOLD"
        factor = parsed.get("factor_scores", {})
        return {
            "consensus": consensus,
            "weighted_score": min(max(float(parsed.get("weighted_score", 0.5)), 0), 1),
            "confidence": min(max(float(parsed.get("confidence", 0.5)), 0), 1),
            "summary": parsed.get("summary", "")[:150],
            "risk_flags": parsed.get("risk_flags", [])[:5],
            "stop_loss_pct": float(parsed.get("stop_loss_pct", -8.0)),
            "factor_scores": {
                "value": int(factor.get("value", 50)),
                "quality": int(factor.get("quality", 50)),
                "growth": int(factor.get("growth", 50)),
                "momentum": int(factor.get("momentum", 50)),
            },
        }
    except Exception as exc:
        logger.warning("convergence 调用失败: %s", exc)
        return _fallback_verdict(stances)


def _fallback_verdict(stances: list[dict]) -> dict:
    """LLM 调用失败时的降级判决（简单多数投票）"""
    buys = sum(1 for s in stances if s["stance"] == "BUY")
    sells = sum(1 for s in stances if s["stance"] == "SELL")
    holds = sum(1 for s in stances if s["stance"] == "HOLD")
    if buys > sells and buys > holds:
        consensus = "BUY"
    elif sells > buys and sells > holds:
        consensus = "SELL"
    else:
        consensus = "HOLD"
    total = buys + sells + holds
    return {
        "consensus": consensus,
        "weighted_score": max(buys, sells, holds) / total if total else 0.5,
        "confidence": 0.4,
        "summary": f"降级判决：{buys}B/{holds}H/{sells}S（LLM不可用，简单多数）",
        "risk_flags": ["降级判决-LLM不可用"],
        "stop_loss_pct": -8.0,
        "factor_scores": {"value": 50, "quality": 50, "growth": 50, "momentum": 50},
    }


# ============================================================
#  主入口
# ============================================================


def run_debate(code: str, name: str, data: dict) -> dict:
    """对单只股票运行三环辩论，返回结构化结果"""
    t0 = time.time()

    logger.info("辩论开始: %s %s", code, name)

    # 分层记忆检索：从 trading_memory 中搜历史案例
    memory_ctx = _retrieve_memory_context(code, data)
    if memory_ctx:
        data["memory_context"] = memory_ctx

    # 第一环：Stance（含历史记忆上下文）
    stances = _stance_phase(code, name, data)
    logger.info("Stance 完成: %d 位专家 / %.1fs", len(stances), time.time() - t0)

    # 第二环：Peer Review（多轮）
    reviews = _peer_review_phase(stances, code, name)
    logger.info("Peer Review 完成: %d 条质疑", len(reviews))

    # 第三环：Convergence
    verdict = _convergence_phase(stances, reviews, code, name, data)

    elapsed = time.time() - t0
    logger.info("辩论完成: %s %s → %s (%.1fs)", code, name, verdict.get("consensus"), elapsed)

    result = {
        "code": code,
        "name": name,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "elapsed_s": round(elapsed, 1),
        "stances": stances,
        "peer_reviews": reviews,
        "verdict": verdict,
    }

    # 持久化
    _save_result(result)

    # 自我反思：提炼历史教训
    _reflect_and_learn(result)

    return result


def batch_debate(stocks: list[dict]) -> list[dict]:
    """批量辩论多只股票（顺序执行，节约 LLM 并发压力）"""
    results = []
    for stock in stocks:
        result = run_debate(stock["code"], stock["name"], stock.get("data", {}))
        results.append(result)
    return results


def _save_result(result: dict):
    """追加辩论结果到 debate_result.json，加文件锁防并发竞争"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = str(RESULT_FILE) + ".lock"
    with open(lock_path, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            records = []
            if RESULT_FILE.exists():
                try:
                    records = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, FileNotFoundError):
                    records = []
            records.append(result)
            if len(records) > 500:
                records = records[-500:]
            RESULT_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


MEMORY_FILE = DATA_DIR.parent / "simulation" / "trading_memory.json"
PORTFOLIO_FILE = DATA_DIR.parent / "simulation" / "portfolio.json"
DECISION_LOG_FILE = DATA_DIR.parent / "simulation" / "decision_log.json"
EXPERIMENTS_DIR = Path(__file__).parent.parent.parent.parent / "experiments"
USER_PORTFOLIO_FILE = DATA_DIR.parent / "user" / "portfolio.json"


def _retrieve_memory_context(code: str, data: dict, max_items: int = 5) -> str:
    """从多个源检索历史数据：transactions + trading_memory + decision_log
    + experiments(投顾决策) + 实盘数据，构建辩论参考上下文。"""
    lines = []

    # 源1：portfolio.json transactions（模拟交易记录）
    try:
        if PORTFOLIO_FILE.exists():
            pf = json.loads(PORTFOLIO_FILE.read_text(encoding="utf-8"))
            txns = [t for t in pf.get("transactions", []) if t.get("code") == code]
            for t in txns[-3:]:
                action = "买入" if t.get("type") == "BUY" else "卖出"
                pnl_info = ""
                if t.get("realized_pnl"):
                    pnl_info = f" 盈亏 ¥{t['realized_pnl']:+,.2f}"
                lines.append(
                    f"  [交易] {t.get('date', '')} {action} "
                    f"{t.get('shares', 0)}股 @¥{t.get('price', 0):.2f}{pnl_info}"
                )
    except (json.JSONDecodeError, OSError):
        pass

    # 源2：trading_memory（历史记忆+反思）
    try:
        if MEMORY_FILE.exists():
            records = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
            for r in records:
                if not isinstance(r, dict):
                    continue
                if code not in r.get("symbols", []):
                    continue
                lines.append(
                    f"  [{r.get('memory_type', 'memory')}] "
                    f"{r.get('created_at', '')[:10]} {r.get('title', '')}"
                    f" — {r.get('lesson', '')[:80]}"
                )
    except (json.JSONDecodeError, OSError):
        pass

    # 源3：decision_log（历史决策）
    try:
        if DECISION_LOG_FILE.exists():
            dl = json.loads(DECISION_LOG_FILE.read_text(encoding="utf-8"))
            if isinstance(dl, list):
                for d in dl[-5:]:
                    if d.get("code") != code:
                        continue
                    lines.append(
                        f"  [决策] {d.get('timestamp', '')[:10]} "
                        f"决策 {d.get('decision', '?')} "
                        f"理由: {d.get('reason', '')[:60]}"
                    )
    except (json.JSONDecodeError, OSError):
        pass

    # 源4：experiments/（投顾每日选股决策记录）
    try:
        if EXPERIMENTS_DIR.exists():
            for exp_file in sorted(EXPERIMENTS_DIR.glob("*.json"), reverse=True)[:3]:
                exp_data = json.loads(exp_file.read_text(encoding="utf-8"))
                if not isinstance(exp_data, dict):
                    continue
                date = exp_data.get("date", exp_file.stem)
                action = exp_data.get("action", "?")
                reason = exp_data.get("reason", "")[:60]
                monitor = exp_data.get("monitor", [])
                # 检查这只股票是否在监测列表中
                stock_in_monitor = any(
                    (m if isinstance(m, str) else m.get("code", "")) == code
                    for m in (monitor if isinstance(monitor, list) else [])
                )
                if stock_in_monitor:
                    lines.append(
                        f"  [投顾决策] {date} 本日决策: {action} "
                        f"({reason})"
                    )
    except (json.JSONDecodeError, OSError):
        pass

    # 源5：实盘数据（全局风控参考）
    try:
        if USER_PORTFOLIO_FILE.exists():
            upf = json.loads(USER_PORTFOLIO_FILE.read_text(encoding="utf-8"))
            for h in upf.get("holdings", []):
                if h.get("code") == code:
                    cost = h.get("avg_cost", 0)
                    current = h.get("current_price", cost)
                    pnl_pct = round((current - cost) / cost * 100, 2) if cost > 0 else 0
                    lines.append(
                        f"  [实盘] 持仓 {h.get('shares', 0)}股 "
                        f"成本 ¥{cost:.2f} 现价 ¥{current:.2f} "
                        f"浮亏 {pnl_pct:+.2f}%"
                    )
            # 如果实盘有止损触发的股票，作为全局风控参考
            breached = [
                h for h in upf.get("holdings", [])
                if h.get("current_price", 0) > 0
                and (h.get("current_price", 0) - h.get("avg_cost", 1))
                / h.get("avg_cost", 1) < -0.08
            ]
            if breached and not any("实盘止损" in line for line in lines):
                lines.append(
                    f"  [实盘风控] ⚠️ 实盘有 {len(breached)} 只股票击穿-8%止损线，"
                    f"市场风险偏高，建议偏向防守"
                )
    except (json.JSONDecodeError, OSError):
        pass

    if not lines:
        return ""
    return "\n".join(lines[-max_items:])


def _reflect_and_learn(result: dict, _async: bool = True):
    """辩论后自我反思——用 LLM 提炼教训，写入 trading_memory"""
    code = result.get("code", "")
    name = result.get("name", "")
    verdict = result.get("verdict", {})
    stances = result.get("stances", [])

    # 构造反思 prompt
    buys = sum(1 for s in stances if s["stance"] == "BUY")
    holds = sum(1 for s in stances if s["stance"] == "HOLD")
    sells = sum(1 for s in stances if s["stance"] == "SELL")
    stance_lines = [
        f"  {s['name']}: {s['stance']} ({s['confidence']:.0%}) — {s['reasoning'][:60]}"
        for s in stances
    ]

    reflection_prompt = (
        f"你刚刚完成了对 {name}({code}) 的一次投资辩论。\n"
        f"辩论投票: {buys}B/{holds}H/{sells}S → 共识 {verdict.get('consensus','?')}\n"
        f"专家观点:\n" + "\n".join(stance_lines) + "\n\n"
        "请提炼一条投资教训（<=60字），格式："
        "「教训陈述 + 下次应采取什么行动」。\n"
        "输出纯文本，不要任何额外文字。"
    )
    try:
        lesson = _call_llm(
            "你是一位投资反思教练。从每次辩论中提炼一条可操作的教训。",
            reflection_prompt, temperature=0.3, max_tokens=150,
        )
    except Exception:
        return  # 反思失败静默跳过，不阻塞主流程

    # 写入 trading_memory
    import hashlib as _hashlib
    raw_fp = f"reflect:{code}:{time.strftime('%Y-%m-%d')}:{verdict.get('consensus','')}"
    fingerprint = _hashlib.sha256(raw_fp.encode()).hexdigest()[:16]

    memory_entry = {
        "id": f"mem_reflect_{time.strftime('%Y%m%d')}_{code}",
        "fingerprint": fingerprint,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "memory_type": "reflection",
        "title": f"辩论反思: {name}({code}) → {verdict.get('consensus','?')}",
        "summary": f"{buys}B/{holds}H/{sells}S 投票",
        "lesson": lesson.strip()[:200],
        "symbols": [code],
        "authors": ["北辰_反思"],
        "strategies": [],
        "experts": [s["name"] for s in stances],
        "market_regime": "unknown",
        "positive_signals": [],
        "negative_signals": [],
        "applicable_conditions": ["辩论自动反思"],
        "avoid_conditions": [],
        "source_decision_ids": [],
        "evidence": {},
        "confidence": verdict.get("confidence", 0.5),
        "status": "active",
    }

    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_path = str(MEMORY_FILE) + ".lock"
    with open(lock_path, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            existing = []
            if MEMORY_FILE.exists():
                try:
                    existing = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    existing = []
            if not any(r.get("fingerprint") == fingerprint for r in existing):
                existing.append(memory_entry)
                MEMORY_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
                logger.info("反思已写入: %s", memory_entry["title"])
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
