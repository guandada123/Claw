#!/usr/bin/env python3
"""
mine_wx_articles_v8.py — 全网挖掘 v8 信号归档器
复用 archive_and_signal.py 的抽取范式，但：
  1. 数据源指向真实微信文章库 output/wx_articles（v8 prompt 中 'archive/output/wx_articles' 为笔误）
  2. 股票词典从 31 只扩展为全 A（astock_code_name.json 全名 + 3字以上无歧义简称）
  3. 增量闸基于「已处理文件名清单」比对（根治 mtime 假阴性），首跑做全量回填，后续只处理清单外的新落盘文章
  4. 信号写入 article_signals.json（source="微信文章"），并按胜率表筛选优质信号推飞书
"""
import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent          # /Users/guan/WorkBuddy/Claw
WX_DIR = ROOT / "output" / "wx_articles"
SIGNALS_FILE = ROOT / ".workbuddy" / "data" / "article_signals.json"
VERIFY_REPORT = ROOT / ".workbuddy" / "data" / "signal_verify_report.json"
NAME_DICT = ROOT / ".workbuddy" / "scripts" / "astock_code_name.json"
PROCESSED_FILE = ROOT / ".workbuddy" / "knowledge" / "index" / "wx_articles_processed.txt"

# ---- 方向判定关键词（沿用 canonical） ----
BULLISH = ["买入", "推荐", "看好", "加仓", "爆发", "上涨", "突破", "反弹", "龙头", "机会", "低吸", "建仓", "关注", "目标"]
BEARISH = ["卖出", "减仓", "风险", "下跌", "回避", "止损", "利空", "见顶", "退潮"]

# 优质信号门槛：胜率 >= 25% 的号（来自 signal_verify_report ranking）
# v8 @2026-07-18：由 40% 临时下调至 25%。
# 背景：A股普跌期全部28个源胜率均 <40%（好运侠客 64.7%→17.6%），原阈值使质量门恒为空、
#       推送条件(>=2优质信号)在普跌期永远无法触发。下调后熊市期仍能有信号流。
# TODO：后续应改为"近N日滚动胜率"口径，避免单日阈值断崖。
QUALITY_WIN_RATE = 25.0


def build_stock_map():
    d = json.loads(Path(NAME_DICT).read_text(encoding="utf-8"))
    rev = {v: k for k, v in d.items()}            # name -> code
    full = dict(rev)                                # 全名副本
    suffixes = "股份|银行|证券|科技|集团|有限|公司|控股|实业|能源|传媒|医药|材料|化工|电子|智能|环境|国际|发展|投资|网络|电气|汽车|地产|重工|航空|保险|健康|生物|食品|家居|黄金|稀土|锂业|光电|信息|数据|通信|医疗|电力|矿业|钢铁|水泥|造纸|纺织|机械|装备|环保|水务|燃气|港口|航运|高速|铁路|建筑|置业|商业|百货|超市|酒店|旅游|机场|影视|出版|教育|游戏|软件|芯片|半导体|新能源|储能|光伏|风电|电池|零件|轮胎|化肥|农药|种子|养殖|饮料|酒业|乳业|制药|器械|基因|疫苗|技术"
    short = {}
    for name, code in rev.items():
        s = re.sub(suffixes, "", name)
        if len(s) >= 3:
            short.setdefault(s, set()).add(code)
    short_uniq = {s: next(iter(c)) for s, c in short.items() if len(c) == 1}
    return full, short_uniq


def normalize_account(acc):
    acc = (acc or "").strip()
    if not acc:
        return "未知"
    n = len(acc)
    if n % 2 == 0 and acc[: n // 2] == acc[n // 2 :]:   # "红鼻子小丑红鼻子小丑" -> "红鼻子小丑"
        acc = acc[: n // 2]
    return acc


def find_positions(text, s):
    return [m.start() for m in re.finditer(re.escape(s), text)]


def extract_signals_for_article(title, content, full_map, short_map):
    """返回 [{name,code,direction,in_title}]，带局部共现精度过滤。

    精度规则：
      - 标题含股票名 → 强信号，保留
      - 正文出现，且股票名 ±60 字符窗口内有推荐/风险关键词 → 保留，方向取局部关键词
      - 仅泛泛提及（无局部关键词）→ 丢弃（避免盘口逻辑拆解类“举例”噪声）
    """
    text = f"{title}\n{content}"
    # 候选名称（全名 + 无歧义简称）
    cand = {}
    for name, code in full_map.items():
        if name in text:
            cand[code] = name
    for sname, code in short_map.items():
        if sname in text and code not in cand:
            cand[code] = sname

    out = []
    for code, name in cand.items():
        in_title = name in title
        if in_title:
            direction = dir_of_signal_local(title) or "bullish"
            out.append({"name": name, "code": code, "direction": direction, "in_title": True})
            continue
        # 局部共现
        loc_b = loc_r = 0
        for p in find_positions(text, name):
            win = text[max(0, p - 60): p + len(name) + 60]
            loc_b += sum(1 for kw in BULLISH if kw in win)
            loc_r += sum(1 for kw in BEARISH if kw in win)
        if loc_b == 0 and loc_r == 0:
            continue
        if loc_b > loc_r:
            direction = "bullish"
        elif loc_r > loc_b:
            direction = "bearish"
        else:
            direction = "bullish" if loc_b > 0 else "neutral"
        out.append({"name": name, "code": code, "direction": direction, "in_title": False})
    return out


def dir_of_signal(text):
    b = sum(1 for kw in BULLISH if kw in text)
    r = sum(1 for kw in BEARISH if kw in text)
    if b > r:
        return "bullish"
    if r > b:
        return "bearish"
    return "bullish" if b > 0 else "neutral"


def dir_of_signal_local(title):
    b = sum(1 for kw in BULLISH if kw in title)
    r = sum(1 for kw in BEARISH if kw in title)
    if b > r:
        return "bullish"
    if r > b:
        return "bearish"
    return "bullish" if b > 0 else None


def load_processed():
    """已处理文件名清单（增量闸比对基准）。

    根治 mtime 假阴性：原方案用 find -newermt 比对文件 mtime，晚到但 mtime 旧的
    文章会被永久漏捕（只能靠每 6h 一次 genesis 全量重扫兜底）。改为「清单比对」后，
    只要文件出现在目录里、且不在已处理清单中，下次增量扫描必被捕获。
    """
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


def load_quality_accounts():
    if not VERIFY_REPORT.exists():
        return set()
    try:
        rep = json.loads(Path(VERIFY_REPORT).read_text(encoding="utf-8"))
    except Exception:
        return set()
    qa = set()
    for r in rep.get("ranking", []):
        try:
            if float(r.get("win_rate", 0)) >= QUALITY_WIN_RATE:
                qa.add(r.get("account"))
        except Exception:
            continue
    return qa


def push_feishu(title, content):
    env = dict(os.environ)
    env["FEISHU_CHAT_ID"] = "oc_9ee5303497f5e0e71666b610d6bdc346"
    p = subprocess.run(
        ["bash", str(ROOT / ".workbuddy" / "scripts" / "push_feishu.sh"), title, content],
        capture_output=True, text=True, env=env, check=False,
    )
    return p.returncode, p.stdout + p.stderr


def main():
    full_map, short_map = build_stock_map()
    processed = load_processed()
    genesis = not PROCESSED_FILE.exists()
    now = datetime.now()

    files = sorted(WX_DIR.glob("*.json"))
    files = [f for f in files if f.name not in (".cache.json", "fetched_cache.json")]
    # 增量闸：只处理「不在已处理清单」的文件（根治 mtime 假阴性——晚到旧 mtime 文件不再漏捕）
    new_files = [f for f in files if f.name not in processed]
    print(f"[gate] 总文章 {len(files)} | 已处理 {len(processed)} | 本次处理 {len(new_files)}" + (" [genesis]" if genesis else ""))

    quality_accounts = load_quality_accounts()
    print(f"[weights] 优质号(胜率>={QUALITY_WIN_RATE}%): {sorted(quality_accounts)}")

    new_signals = []
    acc_counter = {}
    for f in new_files:
        try:
            art = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(art, dict):
            continue
        title = art.get("title", f.stem) or ""
        account = normalize_account(art.get("account"))
        if account == "未知":
            continue  # 无署名文章无法溯源验证，跳过
        content = art.get("content", "") or ""
        pub = art.get("pub_date", "") or art.get("publish_time", "") or ""
        if not (title + content).strip():
            continue
        stocks = extract_signals_for_article(title, content, full_map, short_map)
        if not stocks:
            continue
        article_id = hashlib.md5(f.name.encode()).hexdigest()[:12]
        rec_date = (pub[:10] if pub else datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d"))
        for s in stocks:
            sig = {
                "article_id": article_id,
                "account": account,
                "title": title,
                "stock_code": s["code"],
                "stock_name": s["name"],
                "signal": s["direction"],
                "target_price": None,
                "confidence": 5 if s["in_title"] else 3,
                "recorded_at": rec_date,
                "verified": False,
                "hit_target": None,
                "hit_stop": None,
                "final_return_pct": None,
                "source_file": f.name,
                "realtime_chg_pct": None,
                "realtime_price": None,
                "hit": None,
                "verify_note": None,
                "verify_at": None,
                "source": "微信文章",
            }
            new_signals.append(sig)
            acc_counter[account] = acc_counter.get(account, 0) + 1

    # 去重：与已有 (article_id, stock_code) 合并
    existing = []
    if SIGNALS_FILE.exists():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            existing = json.loads(SIGNALS_FILE.read_text(encoding="utf-8"))
    seen = {(s.get("article_id"), s.get("stock_code")) for s in existing}
    truly_new = [s for s in new_signals if (s["article_id"], s["stock_code"]) not in seen]
    all_signals = existing + truly_new
    SIGNALS_FILE.write_text(json.dumps(all_signals, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[archive] 提取 {len(new_signals)} 条候选 | 去重后新增 {len(truly_new)} 条 | 累计 {len(all_signals)} 条")
    print(f"[accounts] {dict(sorted(acc_counter.items(), key=lambda x:-x[1])[:10])}")

    # 优质信号筛选
    quality = [s for s in truly_new if s["account"] in quality_accounts]
    print(f"[quality] 优质新信号 {len(quality)} 条")

    # 推送：有 >=2 条优质新信号才推
    pushed = False
    if len(quality) >= 2:
        today = now.strftime("%Y-%m-%d")
        q_by_acc = {}
        for s in quality:
            q_by_acc.setdefault(s["account"], []).append(s)
        lines = [f"📚知识库挖掘 | 增量归档 {len(truly_new)} 条信号（优质 {len(quality)}）", "━━━━━━━━━━━━"]
        for acc in sorted(q_by_acc, key=lambda a: -len(q_by_acc[a])):
            lines.append(f"【{acc} · {len(q_by_acc[acc])}】")
            for s in q_by_acc[acc][:4]:
                arrow = "看多" if s["signal"] == "bullish" else ("看空" if s["signal"] == "bearish" else "中性")
                lines.append(f"  · {s['stock_name']}({s['stock_code']}) {arrow} — 《{s['title'][:18]}》")
        lines.append(f"累计信号池 {len(all_signals)} 条 → .workbuddy/data/article_signals.json")
        title = f"📚知识库挖掘 {today}"
        rc, out = push_feishu(title, "\n".join(lines))
        pushed = rc == 0
        print(f"[push] 飞书推送 {'成功' if pushed else '失败'} (rc={rc}) {out[:200]}")

    # 增量闸推进：把本次处理过的文件名写入已处理清单（替代原 mtime baseline）
    if new_files:
        save_processed([f.name for f in new_files])
        print(f"[gate] 已追加 {len(new_files)} 个文件名到已处理清单")
    else:
        print("[gate] 无新文件，已处理清单不变")

    # 返回结构化摘要供调用方
    return {
        "processed": len(new_files),
        "new_candidates": len(new_signals),
        "truly_new": len(truly_new),
        "quality_new": len(quality),
        "total": len(all_signals),
        "pushed": pushed,
        "genesis": genesis,
    }


if __name__ == "__main__":
    summary = main()
    print("SUMMARY:", json.dumps(summary, ensure_ascii=False))
