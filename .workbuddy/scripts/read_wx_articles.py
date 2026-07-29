#!/usr/bin/env python3
"""
read_wx_articles.py — 公众号文章「阅读理解」层（v1）

替代 mine_wx_articles_v8.py 的纯词袋法（正则扫股票名 + ±60字数关键词）。
本脚本让 LLM 像人一样通读全文，提炼每篇文章的精髓：
  - 一句话主旨
  - 作者核心观点（2-3 条）
  - 提及个股 + 对每只的真实态度/理由（区分推荐 / 举例 / 反面教材）
  - 催化剂与时间窗口
  - 风险提示
  - 观点置信度（高/中/低）

产物：
  1) .workbuddy/data/article_insights.json  — 每篇结构化读后笔记（累积，去重）
  2) .workbuddy/data/article_signals.json    — 兼容旧格式的信号（供 signal_report / 验证管线复用）
  3) 每日「公众号精华」文本（stdout + 供推送）

增量闸：复用 mine_wx 的思路，用 read_wx_articles_processed.txt 清单比对。
成本：逐篇调 deepseek-v4-flash（~¥0.06/篇量级），默认限速 + 每次最多处理 N 篇。
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent          # /Users/guan/WorkBuddy/Claw
WX_DIR = ROOT / "output" / "wx_articles"
INSIGHTS_FILE = ROOT / ".workbuddy" / "data" / "article_insights.json"
SIGNALS_FILE = ROOT / ".workbuddy" / "data" / "article_signals.json"
RESONANCE_FILE = ROOT / ".workbuddy" / "data" / "article_resonance.json"
NAME_DICT = ROOT / ".workbuddy" / "scripts" / "astock_code_name.json"
PROCESSED_FILE = ROOT / ".workbuddy" / "knowledge" / "index" / "read_wx_articles_processed.txt"

# 复用项目统一 LLM 路由
sys.path.insert(0, str(ROOT / "scripts"))
try:
    from router import call_llm  # type: ignore
except Exception as e:  # pragma: no cover
    print(f"[fatal] 无法导入 router.call_llm: {e}")
    raise

def _build_flash_cfg():
    """构造 deepseek-v4-flash 调用配置。
    优先走本地代理 proxy-deepseek(127.0.0.1:9999)——它持有正确的 DEEPSEEK_API_KEY 并自动注入，
    可绕过 automation 环境里注入的错误/积分轨 key（否则直连会 HTTP 401）。
    代理不可达时降级为 deepseek 直连（依赖环境 key）。
    """
    import socket
    base = {"model": "deepseek-v4-flash", "cost_per_10k": 0.06}
    with contextlib.suppress(Exception):
        s = socket.create_connection(("127.0.0.1", 9999), timeout=1.5)
        s.close()
        # provider 非 deepseek/catrouter → router 走 else 分支，用我们给的 base_url（本地代理）
        return {**base, "provider": "local_proxy", "base_url": "http://127.0.0.1:9999/v1"}
    return {**base, "provider": "deepseek"}


FLASH_CFG = _build_flash_cfg()

SYSTEM_PROMPT = (
    "你是一名资深 A 股投研分析师，正在阅读一篇券商/自媒体的投资类公众号文章。"
    "你的任务不是做关键词匹配，而是像人一样读懂文章，提炼作者真正想表达的核心观点。"
    "务必区分：作者【真正推荐/看好】的标的，与仅【举例说明、反面教材、顺带提及】的标的——"
    "后者绝不能算作看多信号。若文章只是复盘行情、罗列涨跌，没有明确前瞻观点，就如实说明。"
)

# 输出 JSON 的结构说明（放进 user prompt）
SCHEMA_HINT = """请通读全文后，仅输出一个 JSON 对象（不要任何额外文字、不要 markdown 代码块围栏），字段如下：
{
  "gist": "一句话主旨（<=40字，概括文章到底在说什么）",
  "core_views": ["作者核心观点1", "观点2", "观点3"],   // 2-3条，抓推理链与结论，不是复述行情
  "market_stance": "bullish|bearish|neutral",          // 作者对后市/所述方向的整体态度
  "stocks": [                                            // 文章真正表达了态度的个股，没有就空数组
    {
      "name": "股票名",
      "code": "6位代码或留空",
      "attitude": "recommend|caution|mention_only|negative_example",  // 对该股的真实态度
      "reason": "作者给出的理由（<=30字）",
      "catalyst": "催化剂/驱动因素，无则空",
      "time_window": "时间窗口，如'短线/1个月/长期'，无则空"
    }
  ],
  "catalysts": ["文章提到的关键催化剂或事件"],           // 宏观/行业级，无则空数组
  "risks": ["作者提示的风险"],                           // 无则空数组
  "confidence": "high|medium|low",                       // 你对'本文有明确可用投资观点'的判断
  "is_actionable": true                                  // 是否含明确可操作观点(纯行情复盘=false)
}
只输出 JSON。"""


def normalize_account(acc: str) -> str:
    acc = (acc or "").strip()
    if not acc:
        return "未知"
    n = len(acc)
    if n % 2 == 0 and acc[: n // 2] == acc[n // 2:]:
        acc = acc[: n // 2]
    return acc


def extract_content(art: dict) -> str:
    """兼容多种抓取字段名提取正文（content 优先，回退 content_text / content_html 去标签）。
    抓取层不同版本落盘字段名不一致（content / content_text / content_html），统一兜底避免漏读。"""
    import re as _re
    c = (art.get("content") or "").strip()
    if c:
        return c
    for key in ("content_text", "body", "text", "article"):
        v = art.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    html = art.get("content_html")
    if isinstance(html, str) and html.strip():
        return _re.sub(r"<[^>]+>", "", html).strip()
    return ""


def load_name_map():
    """name -> code，用于给 LLM 没给出代码的股票补全代码。"""
    try:
        d = json.loads(Path(NAME_DICT).read_text(encoding="utf-8"))
        return {v: k for k, v in d.items()}
    except Exception:
        return {}


def load_processed() -> set:
    if not PROCESSED_FILE.exists():
        return set()
    try:
        return {ln.strip() for ln in PROCESSED_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()}
    except Exception:
        return set()


def save_processed(names):
    PROCESSED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with PROCESSED_FILE.open("a", encoding="utf-8") as fh:
        for n in names:
            fh.write(n + "\n")


def _extract_json(text: str) -> dict | None:
    """从 LLM 回复里稳健地抠出 JSON 对象。"""
    if not text:
        return None
    t = text.strip()
    # 去掉可能的 ```json ... ``` 围栏
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    # 定位第一个 { 到最后一个 }
    i, j = t.find("{"), t.rfind("}")
    if i == -1 or j == -1 or j <= i:
        return None
    frag = t[i:j + 1]
    try:
        return json.loads(frag)
    except Exception:
        return None


def read_one_article(title: str, content: str, account: str) -> dict | None:
    """让 LLM 阅读单篇文章，返回结构化 insight（失败返回 None）。"""
    # 控制输入长度：正文超长截断（保留开头与结尾，观点常在这两处）
    if len(content) > 6000:
        content = content[:4500] + "\n……（中略）……\n" + content[-1200:]
    user_prompt = (
        f"【公众号】{account}\n【标题】{title}\n【正文】\n{content}\n\n{SCHEMA_HINT}"
    )
    for attempt in (1, 2):
        res = call_llm(
            user_prompt,
            FLASH_CFG,
            system=SYSTEM_PROMPT,
            temperature=0.2,
            max_tokens=2000,
            task="wx_article_read",
            project="Claw",
        )
        if not res.get("success"):
            print(f"   ⚠️ LLM 调用失败(第{attempt}次): {res.get('error')}")
            continue
        parsed = _extract_json(res.get("response") or "")
        if parsed:
            return _normalize_insight(parsed)
        print(f"   ⚠️ 解析 JSON 失败(第{attempt}次)，前120字: {(res.get('response') or '')[:120]}")
    return None


def _normalize_insight(ins: dict) -> dict:
    """后处理：校准 is_actionable / confidence。
    - 纯行情复盘（无任何 recommend/caution 个股）一律 is_actionable=false，
      不计入置信度分布噪声（P2-⑤）。
    - 兜底：LLM 漏填 is_actionable 时按是否有明确态度推导。"""
    actionable_att = {"recommend", "caution"}
    has_opinion = any(
        (s.get("attitude") or "").strip() in actionable_att
        for s in ins.get("stocks", [])
    )
    if not has_opinion:
        ins["is_actionable"] = False
        # 复盘类不污染置信度分布：标为 recap（下游统计可跳过）
        ins["confidence"] = ins.get("confidence") or "recap"
    else:
        ins["is_actionable"] = bool(ins.get("is_actionable", True))
    return ins


ATTITUDE_TO_SIGNAL = {
    "recommend": "bullish",
    "caution": "bearish",
    "negative_example": "bearish",
    "mention_only": "neutral",
}


def insight_to_signals(insight: dict, meta: dict) -> list:
    """把 LLM insight 转成兼容旧管线的信号记录（供 signal_report / 验证复用）。
    只有 attitude=recommend/caution 才落信号，mention_only 丢弃（这正是词袋法做不到的降噪）。
    枚举校验：signal 必须是 bullish/bearish/neutral，否则丢弃——防止脏格式污染信号库。"""
    name_map = meta["name_map"]
    out = []
    for st in insight.get("stocks", []):
        att = (st.get("attitude") or "").strip()
        if att in ("mention_only",):
            continue  # 顺带提及 → 不算信号
        signal = ATTITUDE_TO_SIGNAL.get(att, "neutral")
        if signal == "neutral":
            continue
        if signal not in ("bullish", "bearish", "neutral"):
            # 防御性校验：异常方向直接丢弃，不写盘
            print(f"   ⚠️ 丢弃异常 signal={signal!r}（{st.get('name')}）")
            continue
        name = (st.get("name") or "").strip()
        code = (st.get("code") or "").strip()
        if not code and name in name_map:
            code = name_map[name]
        if not code:
            continue
        # 与交易约束一致：信号层也过滤不可交易标的（创/科/北/ST），避免误导
        if code[:3] in ("300", "301", "688", "689") or code[:1] in ("4", "8"):
            continue
        if "ST" in name.upper():
            continue
        out.append({
            "article_id": meta["article_id"],
            "account": meta["account"],
            "title": meta["title"],
            "stock_code": code,
            "stock_name": name,
            "signal": signal,
            "target_price": None,
            "confidence": {"high": 5, "medium": 3, "low": 2}.get(insight.get("confidence"), 3),
            "recorded_at": meta["rec_date"],
            "verified": False,
            "hit_target": None,
            "hit_stop": None,
            "final_return_pct": None,
            "source_file": meta["source_file"],
            "realtime_chg_pct": None,
            "realtime_price": None,
            "hit": None,
            "verify_note": None,
            "verify_at": None,
            "source": "微信文章(LLM阅读)",
            "reason": st.get("reason", ""),
            "catalyst": st.get("catalyst", ""),
            "time_window": st.get("time_window", ""),
        })
    return out


def build_digest(new_insights: list, today: str) -> str:
    """把当日读完的文章汇总成人类可读的「公众号精华」。"""
    actionable = [x for x in new_insights if x["insight"].get("is_actionable")]
    actionable.sort(key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(
        x["insight"].get("confidence"), 3))
    lines = [f"📰 公众号精华 · {today}", f"共读 {len(new_insights)} 篇，其中有明确观点 {len(actionable)} 篇", "━━━━━━━━━━━━"]
    if not actionable:
        lines.append("今日文章多为行情复盘，无明确前瞻观点。")
        return "\n".join(lines), []
    for x in actionable[:8]:
        ins = x["insight"]
        conf = {"high": "高", "medium": "中", "low": "低"}.get(ins.get("confidence"), "?")
        stance = {"bullish": "偏多", "bearish": "偏空", "neutral": "中性"}.get(ins.get("market_stance"), "")
        lines.append(f"\n【{x['account']}】{stance}·置信{conf}")
        lines.append(f"  主旨：{ins.get('gist', '')}")
        views = ins.get("core_views") or []
        if views:
            lines.append(f"  观点：{views[0]}")
        recs = [s for s in ins.get("stocks", []) if s.get("attitude") == "recommend"]
        for s in recs[:3]:
            tw = f"·{s['time_window']}" if s.get("time_window") else ""
            lines.append(f"  ★ {s.get('name', '')} — {s.get('reason', '')}{tw}")
    # 汇总被多个不同账号推荐的个股（按账号去重，避免单账号多篇文章虚高共振）
    rec_accounts = {}
    for x in actionable:
        acct = x["account"]
        seen_in_article = set()
        for s in x["insight"].get("stocks", []):
            if s.get("attitude") == "recommend":
                name = s.get("name", "")
                if name and name not in seen_in_article:
                    seen_in_article.add(name)
                    rec_accounts.setdefault(name, set()).add(acct)
    hot = sorted([(k, len(v)) for k, v in rec_accounts.items() if k and len(v) >= 2],
                 key=lambda t: -t[1])
    if hot:
        lines.append("\n🔥 多篇共振推荐（≥2账号）：" + "、".join(f"{k}×{v}" for k, v in hot[:5]))
    resonance = [
        {"stock_name": k, "count": v, "accounts": sorted(rec_accounts[k])}
        for k, v in hot[:10]
    ]
    return "\n".join(lines), resonance


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=15, help="本次最多阅读文章数（控成本）")
    ap.add_argument("--file", type=str, default="", help="只处理指定单篇文件（调试/补读）")
    ap.add_argument("--dry-run", action="store_true", help="只打印不写盘")
    args = ap.parse_args()

    name_map = load_name_map()
    processed = load_processed()
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    if args.file:
        files = [Path(args.file)]
    else:
        files = sorted(WX_DIR.glob("*.json"), reverse=True)  # 新文章优先
        files = [f for f in files if f.name not in (".cache.json", "fetched_cache.json")]
        files = [f for f in files if f.name not in processed][: args.max]

    print(f"[gate] 待读 {len(files)} 篇（已读清单 {len(processed)} 篇，本次上限 {args.max}）")
    if not files:
        print("[gate] 无新文章，SILENT")
        return {"read": 0, "actionable": 0, "digest": ""}

    new_insights = []
    new_signals = []
    done_names = []
    for idx, f in enumerate(files, 1):
        try:
            art = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(art, dict):
            continue
        title = (art.get("title") or f.stem) or ""
        account = normalize_account(art.get("account"))
        content = extract_content(art)
        pub = art.get("pub_date", "") or art.get("publish_time", "") or ""
        if account == "未知" or not (title + content).strip():
            done_names.append(f.name)
            continue
        print(f"[{idx}/{len(files)}] 阅读《{title[:26]}》· {account}")
        insight = read_one_article(title, content, account)
        done_names.append(f.name)
        if not insight:
            continue
        article_id = hashlib.md5(f.name.encode(), usedforsecurity=False).hexdigest()[:12]
        rec_date = (pub[:10] if pub else datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d"))
        rec = {
            "article_id": article_id,
            "account": account,
            "title": title,
            "recorded_at": rec_date,
            "source_file": f.name,
            "read_at": now.strftime("%Y-%m-%d %H:%M"),
            "insight": insight,
        }
        new_insights.append(rec)
        meta = {
            "article_id": article_id, "account": account, "title": title,
            "rec_date": rec_date, "source_file": f.name, "name_map": name_map,
        }
        new_signals.extend(insight_to_signals(insight, meta))
        gist = insight.get("gist", "")
        n_rec = len([s for s in insight.get("stocks", []) if s.get("attitude") == "recommend"])
        print(f"     → {gist} | 推荐股 {n_rec} | 置信 {insight.get('confidence')}")

    actionable = [x for x in new_insights if x["insight"].get("is_actionable")]
    digest, resonance = build_digest(new_insights, today)

    if args.dry_run:
        print("\n===== DRY-RUN 摘要 =====")
        print(digest)
        if resonance:
            print("\n[resonance] 共振股:", json.dumps(resonance, ensure_ascii=False))
        return {"read": len(new_insights), "actionable": len(actionable),
                "resonance": resonance, "digest": digest}

    # 落 insights（累积去重）
    existing = []
    if INSIGHTS_FILE.exists():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            existing = json.loads(INSIGHTS_FILE.read_text(encoding="utf-8"))
    seen = {r.get("article_id") for r in existing}
    merged = existing + [r for r in new_insights if r["article_id"] not in seen]
    INSIGHTS_FILE.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    # 落兼容信号（与旧文件合并去重）
    old_sig = []
    if SIGNALS_FILE.exists():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            old_sig = json.loads(SIGNALS_FILE.read_text(encoding="utf-8"))
    sig_seen = {(s.get("article_id"), s.get("stock_code")) for s in old_sig}
    truly_new_sig = [s for s in new_signals if (s["article_id"], s["stock_code"]) not in sig_seen]
    SIGNALS_FILE.write_text(json.dumps(old_sig + truly_new_sig, ensure_ascii=False, indent=2), encoding="utf-8")

    # 落共振结果（供选股自动化直接消费，P2-⑥）
    if resonance:
        old_res = []
        if RESONANCE_FILE.exists():
            with contextlib.suppress(json.JSONDecodeError, OSError):
                old_res = json.loads(RESONANCE_FILE.read_text(encoding="utf-8"))
        merged_res = {r["stock_name"]: r for r in old_res}
        for r in resonance:
            if r["stock_name"] in merged_res:
                m = merged_res[r["stock_name"]]
                m["count"] = max(m["count"], r["count"])
                m["accounts"] = sorted(set(m.get("accounts", []) + r.get("accounts", [])))
            else:
                merged_res[r["stock_name"]] = r
        RESONANCE_FILE.write_text(
            json.dumps(list(merged_res.values()), ensure_ascii=False, indent=2), encoding="utf-8")

    if done_names:
        save_processed(done_names)

    print(f"\n[archive] 新读 insight {len(new_insights)} | 有效信号 {len(truly_new_sig)} | insight库累计 {len(merged)} | 共振 {len(resonance)}")
    print("\n" + digest)
    return {
        "read": len(new_insights),
        "actionable": len(actionable),
        "signals_new": len(truly_new_sig),
        "resonance": resonance,
        "digest": digest,
    }


if __name__ == "__main__":
    summary = main()
    print("\nSUMMARY:", json.dumps({k: v for k, v in summary.items() if k != "digest"}, ensure_ascii=False))
