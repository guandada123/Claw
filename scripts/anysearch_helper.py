#!/usr/bin/env python3
"""金融数据 Helper — 统一封装 westock CLI（优先）+ AnySearch（降级）。

数据源优先级：
1. **westock CLI**（npx westock-data-clawhub）：数据最全，含财报预约/分红/研报
2. **AnySearch**（匿名）：westock 失败时补财务/日历/行情
3. **标 [缺失]**：两者皆失败时不阻断报告

设计原则：
- 统一返回 Python dict / list，调用方无需解析 CLI 文本输出
- 超时保护（westock ≤60s，anysearch ≤40s）
- 失败降级：返回空结构 + 错误标记，不抛异常阻断报告生成

依赖：
- westock CLI: npx westock-data-clawhub@1.0.4（Node.js）
- anysearch-skill: ~/.workbuddy/skills/anysearch/
"""
import json
import os
import re
import shutil
import subprocess
import sys

SKILL_DIR = os.path.expanduser("~/.workbuddy/skills/anysearch")
SKILL_CLI = os.path.join(SKILL_DIR, "scripts", "anysearch_cli.py")
PY = "/Users/guan/.workbuddy/binaries/python/versions/3.13.12/bin/python3"
NODE = "/Users/guan/.workbuddy/binaries/node/versions/22.22.2/bin/npx"
WESTOCK_CLI = "westock-data-clawhub@1.0.4"
TIMEOUT_WESTOCK = 60
TIMEOUT_ANYSEARCH = 40

# 字段名白名单（防止解析到无关内容）
_QUOTE_FIELDS = ["ts_code", "trade_date", "open", "close", "high", "low",
                 "pre_close", "change", "pct_chg", "vol", "amount",
                 "turnover_rate", "pe", "pe_ttm", "pb", "ps", "ps_ttm",
                 "total_mv", "circ_mv", "dv_ratio", "dv_ttm"]


def _westock_available() -> bool:
    """检测 westock CLI 是否可用（npx 存在）。"""
    return shutil.which(NODE) is not None or shutil.which("npx") is not None


def _run_westock(args: list) -> str:
    """调用 westock CLI，返回 stdout 文本。失败返回空字符串。"""
    if not _westock_available():
        return ""
    try:
        env = os.environ.copy()
        # 禁用 TLS 校验警告（westock CLI 内部设置，不影响功能）
        env.setdefault("NODE_TLS_REJECT_UNAUTHORIZED", "0")
        cmd = [NODE, "-y", WESTOCK_CLI] + args
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=TIMEOUT_WESTOCK, cwd="/Users/guan/WorkBuddy/Claw")
        return r.stdout if r.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return ""


def _run_cli(args: list) -> str:
    """调用 anysearch CLI，返回 stdout 文本。失败返回空字符串。"""
    if not os.path.isfile(SKILL_CLI):
        return ""
    try:
        r = subprocess.run(
            [PY, SKILL_CLI] + args,
            capture_output=True, text=True, timeout=TIMEOUT_ANYSEARCH
        )
        return r.stdout if r.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return ""


def _extract_first_json_block(text: str) -> dict:
    """从 CLI 输出中提取第一个 JSON 对象（花括号配对）。"""
    start = text.find("{")
    if start == -1:
        return {}
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return {}
    return {}


def _extract_all_json_blocks(text: str) -> list:
    """提取所有顶层 JSON 对象。"""
    blocks = []
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                try:
                    blocks.append(json.loads(text[start:i + 1]))
                except json.JSONDecodeError:
                    pass
                start = -1
    return blocks


def a_stock_quote(cn_code: str) -> dict:
    """A股实时/最新日线行情。

    Args:
        cn_code: 如 '600522.SH' / '000636.SZ'
    Returns:
        dict: 含 ts_code/close/pct_chg/pe/pb 等字段，失败返回 {'error': ...}
    """
    out = _run_cli([
        "search", cn_code,
        "--domain", "finance",
        "--sub_domain", "finance.quote",
        "--sub_domain_params", json.dumps({"type": "stock", "cn_code": cn_code, "symbol": ""})
    ])
    if not out:
        return {"error": "anysearch_unavailable", "ts_code": cn_code}
    block = _extract_first_json_block(out)
    if not block:
        return {"error": "no_data", "ts_code": cn_code}
    # 只保留白名单字段
    return {k: v for k, v in block.items() if k in _QUOTE_FIELDS} or block


def a_stock_indicator(cn_code: str) -> dict:
    """A股财务指标（ROE/毛利率/负债率等）。

    数据源：AnySearch fundamental（直接返回 ROE 字段，最干净）
    注：westock finance 返回三大报表原始数据无直接 ROE 字段，
        计算复杂，故 indicator 仅用 AnySearch；财报日历用 westock reserve。

    Args:
        cn_code: 如 '600206.SH'
    Returns:
        dict: 最新一期财务指标，失败返回 {'error': ...}
    """
    out = _run_cli([
        "search", "财务指标",
        "--domain", "finance",
        "--sub_domain", "finance.fundamental",
        "--sub_domain_params", json.dumps({"type": "indicator", "cn_code": cn_code, "symbol": ""})
    ])
    if not out:
        return {"error": "anysearch_unavailable", "ts_code": cn_code}
    blocks = _extract_all_json_blocks(out)
    if not blocks:
        return {"error": "no_data", "ts_code": cn_code}
    blocks.sort(key=lambda b: b.get("end_date", ""), reverse=True)
    b = blocks[0]
    b["source"] = "anysearch"
    return b


def earnings_calendar(days: int = 7, cn_code: str = "") -> list:
    """财报披露日历。

    数据源优先级：
    1. westock CLI `reserve`（持仓股财报预约披露，最准）
    2. AnySearch calendar（全市场，兜底）

    Args:
        days: 前瞻天数（AnySearch 用），默认 7
        cn_code: 指定股票（如 '600522.SH'），空则查持仓全部
    Returns:
        list[dict]: 披露日程，失败返回 []
    """
    result = []

    # 1. 优先 westock reserve（持仓股）
    if cn_code:
        prefix = cn_code[:6]
        wp = ("sh" + prefix) if prefix.startswith(("6", "9")) else ("sz" + prefix)
        wout = _run_westock(["reserve", wp])
        if wout:
            # 解析 markdown 表格
            rows = _parse_md_table(wout)
            for r in rows:
                result.append({
                    "ts_code": cn_code,
                    "disclosureDate": r.get("disclosureDate", ""),
                    "disclosureDesc": r.get("disclosureDesc", ""),
                    "source": "westock",
                })
        if result:
            return result

    # 2. 降级 AnySearch calendar（全市场）
    sdp = {"type": "earnings"}
    if cn_code:
        sdp["symbol"] = cn_code
    out = _run_cli([
        "search", "财报日历",
        "--domain", "finance",
        "--sub_domain", "finance.calendar",
        "--sub_domain_params", json.dumps(sdp)
    ])
    if not out:
        return result  # 可能已有 westock 部分结果
    blocks = _extract_all_json_blocks(out)
    for b in blocks:
        if "ts_code" in b or "Symbol" in b or "symbol" in b:
            b["source"] = "anysearch"
            result.append(b)
    return result[:15]


# AnySearch finance.macro 支持的指标类型
# 注：pmi / social_financing 经实测返回被维基/可汗学院/网页噪音污染，
#     无结构化数据，故回退层仅覆盖以下 5 类干净源
_MACRO_SUPPORTED = ["gdp", "cpi", "money_supply", "lpr", "shibor"]


def macro_indicator(macro_type: str) -> dict:
    """宏观指标（AnySearch finance.macro 回退源）。

    适用类型（实测返回干净 JSON）：
        gdp / cpi / money_supply / lpr / shibor
    不适用（AnySearch 无结构化数据，回退时返回 sentinel，调用方应保留 AKShare）：
        pmi / social_financing

    Args:
        macro_type: 上述类型之一
    Returns:
        dict: 最新一期宏观数据 + source=anysearch；
              不支持/失败返回 {'error': ..., 'type': macro_type}
    """
    if macro_type not in _MACRO_SUPPORTED:
        return {"error": "unsupported_type", "type": macro_type,
                "hint": f"AnySearch 仅覆盖 {_MACRO_SUPPORTED}"}
    out = _run_cli([
        "search", "宏观",
        "--domain", "finance",
        "--sub_domain", "finance.macro",
        "--sub_domain_params", json.dumps({"type": macro_type}),
        "--max_results", "10",
    ])
    if not out:
        return {"error": "anysearch_unavailable", "type": macro_type}
    blocks = _extract_all_json_blocks(out)
    # 过滤掉无日期/无关键字段的脏块（pmi/social_financing 污染时）
    blocks = [b for b in blocks if any(
        k in b for k in ("date", "quarter", "month", "gdp", "cpi", "m2", "on", "1y")
    )]
    if not blocks:
        return {"error": "no_structured_data", "type": macro_type}
    # 取最新一期（按 date/quarter/month 降序）
    blocks.sort(key=lambda b: str(b.get("date", b.get("quarter", b.get("month", "")))), reverse=True)
    b = blocks[0]
    b["source"] = "anysearch"
    b["type"] = macro_type
    return b


def _parse_md_table(md: str) -> list:
    """解析 markdown 表格为 dict 列表（westock CLI 输出格式）。"""
    if not md:
        return []
    lines = [l.strip() for l in md.split("\n") if l.strip()]
    if len(lines) < 3:
        return []
    header = [h.strip() for h in lines[0].strip("|").split("|")]
    rows = []
    for line in lines[2:]:
        if not line or line.startswith("| ---"):
            continue
        values = [v.strip() for v in line.strip("|").split("|")]
        row = {}
        for i, h in enumerate(header):
            if i < len(values) and values[i]:
                row[h] = values[i]
        if row:
            rows.append(row)
    return rows


def finance_news(src: str = "10jqka", period: str = "1d", limit: int = 8) -> list:
    """全市场财经快讯（互补公众号）。

    Args:
        src: 数据源 sina/10jqka/eastmoney/cls/yicai 等
        period: 时间范围，默认 1d
        limit: 返回条数
    Returns:
        list[str]: 快讯文本列表，失败返回 []
    """
    out = _run_cli([
        "search", "财经快讯",
        "--domain", "finance",
        "--sub_domain", "finance.news",
        "--sub_domain_params", json.dumps({"type": "flash", "news_src": src, "period": period}),
        "--max_results", str(min(limit, 10))
    ])
    if not out:
        return []
    # 提取 ### N. 开头的快讯文本行
    lines = []
    for line in out.splitlines():
        line = line.strip()
        if re.match(r"^###\s+\d+\.", line):
            # 去掉前缀 ### N.
            text = re.sub(r"^###\s+\d+\.\s*", "", line)
            if text:
                lines.append(text)
    return lines[:limit]


def batch_quotes(cn_codes: list) -> dict:
    """批量行情（并行查询封装）。

    Args:
        cn_codes: ['600522.SH', '600206.SH', ...]
    Returns:
        dict: {cn_code: quote_dict}
    """
    result = {}
    for code in cn_codes:
        result[code] = a_stock_quote(code)
    return result


if __name__ == "__main__":
    # 自测：用实盘三只票验证双数据源
    test_codes = ["600522.SH", "600206.SH", "000636.SZ"]
    print("=== batch_quotes ===")
    for code, q in batch_quotes(test_codes).items():
        print(code, "->", {k: q.get(k) for k in ["close", "pct_chg", "pe", "pb"]} if "error" not in q else q)

    print("\n=== a_stock_indicator (600206.SH) ===")
    ind = a_stock_indicator("600206.SH")
    print({k: ind.get(k) for k in ["source", "end_date", "roe", "grossprofit_margin", "debt_to_assets"]} if "error" not in ind else ind)

    print("\n=== earnings_calendar (600522.SH, westock优先) ===")
    cal = earnings_calendar(7, "600522.SH")
    print(f"返回 {len(cal)} 条:", cal[:3])

    print("\n=== earnings_calendar (全市场, anysearch兜底) ===")
    cal2 = earnings_calendar(7)
    print(f"返回 {len(cal2)} 条，前 3:", cal2[:3])

    print("\n=== finance_news ===")
    news = finance_news()
    print(f"返回 {len(news)} 条，前 2:", news[:2])

    print("\n=== macro_indicator (lpr / shibor / gdp) ===")
    for mt in ["lpr", "shibor", "gdp", "pmi"]:
        m = macro_indicator(mt)
        print(mt, "->", {k: m.get(k) for k in ["source", "type", "date", "quarter", "1y", "5y", "on", "gdp_yoy", "error"]} if "error" not in m else m)
