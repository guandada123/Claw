#!/usr/bin/env python3
"""
Claw 财报日历 — 用 westock MCP 替代原有 akshare 财报追踪

查询持仓股的：
  1. 财报预约披露日期（reserve）
  2. 分红除权日期（exdiv）
  3. 业绩预告（earnings_forecast 通过 westock-data 查询）

用法:
  python3 earnings_calendar.py                     # 查询全部持仓
  python3 earnings_calendar.py --code sh600522      # 单股查询
  python3 earnings_calendar.py --output json        # JSON 格式输出
  python3 earnings_calendar.py --push               # 推送到飞书

依赖: Node.js (npx), jq (可选)
"""
import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ── 双数据源统一层：anysearch_helper（westock 优先 + AnySearch 降级）──
_HELPER_PATH = Path(__file__).resolve().parent / "anysearch_helper.py"
sys.path.insert(0, str(_HELPER_PATH.parent))
try:
    import anysearch_helper as _helper
except Exception:
    _helper = None

# ── 持仓股列表（优先从 portfolio.json 读取，失败时用硬编码兜底）──
def _code_with_prefix(raw: str) -> str:
    """将裸代码转为 westock CLI 所需的 sh/sz 前缀格式"""
    raw = raw.strip()
    # 已有前缀则直接返回
    if raw.startswith(("sh", "sz")):
        return raw
    # 仅数字则按交易所规则加前缀
    if raw.isdigit():
        if raw.startswith(("6", "9")):
            return f"sh{raw}"
        elif raw.startswith(("0", "1", "2", "3")):
            return f"sz{raw}"
    return raw


def _load_portfolio() -> list[dict]:
    """从 Claw 项目持仓文件加载股票列表"""
    portfolio_path = Path(__file__).resolve().parent.parent / ".workbuddy" / "data" / "user" / "portfolio.json"
    try:
        if portfolio_path.exists():
            data = json.loads(portfolio_path.read_text(encoding="utf-8"))
            holdings = data.get("holdings", [])
            if holdings:
                return [{"code": _code_with_prefix(h["code"]), "name": h.get("name", h["code"])} for h in holdings]
    except Exception:
        pass
    # 硬编码兜底
    return [
        {"code": "sh600522", "name": "中天科技"},
        {"code": "sh600206", "name": "有研新材"},
        {"code": "sz000021", "name": "深科技"},
        {"code": "sz000636", "name": "风华高科"},
        {"code": "sh600584", "name": "长电科技"},
    ]

PORTFOLIO = _load_portfolio()

# westock CLI（本地 npx 优先，失败时用系统默认）
NPX = "/Users/guan/.workbuddy/binaries/node/versions/22.22.2/bin/npx"
CLI = "westock-data-clawhub@1.0.4"
CWD = "/Volumes/ZHITAI/WorkBuddy/Claw"

# 飞书推送配置
FEISHU_CHAT = "oc_9ee5303497f5e0e71666b610d6bdc346"


def _check_prerequisites() -> bool:
    """检查运行所需的 CLI 工具是否可用"""
    ok = True
    if not shutil.which(NPX) and not shutil.which("npx"):
        print("[ERROR] npx 未安装，请安装 Node.js", file=sys.stderr)
        ok = False
    return ok


def run_cmd(cmd: list) -> str:
    """执行 CLI 命令并返回 stdout"""
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=60, cwd=CWD
    )
    if result.returncode != 0:
        print(f"[WARN] 命令失败: {' '.join(cmd)}: {result.stderr[:200]}", file=sys.stderr)
        return ""
    return result.stdout.strip()


def parse_markdown_table(md: str) -> list[dict]:
    """解析 westock CLI 输出的 Markdown 表格为 dict 列表"""
    if not md:
        return []
    lines = [l.strip() for l in md.split("\n") if l.strip()]
    if len(lines) < 3:
        return []

    # 表头
    header = [h.strip() for h in lines[0].strip("|").split("|")]
    # 跳过分隔行（|---|）
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


def query_reserve(code: str) -> list[dict]:
    """查询财报预约披露日期（统一走 anysearch_helper：westock 优先 + AnySearch 降级）"""
    if _helper is None:
        return []
    # helper 接收 ts_code 格式（600522.SH），需从 sh/sz 前缀转换
    raw = code[2:] if code.startswith(("sh", "sz")) else code
    prefix = "SH" if code.startswith("sh") else "SZ"
    ts_code = f"{raw}.{prefix}"
    rows = _helper.earnings_calendar(days=7, cn_code=ts_code)
    # 统一字段名以兼容 build_report
    out = []
    for r in rows:
        out.append({
            "disclosureDate": r.get("disclosureDate", r.get("date", "")),
            "disclosureDesc": r.get("disclosureDesc", r.get("desc", "")),
            "source": r.get("source", "unknown"),
        })
    return out


def query_exdiv(code: str) -> list[dict]:
    """查询分红除权日（westock exdiv，helper 未覆盖）"""
    cmd = [NPX, "-y", CLI, "exdiv", code]
    return parse_markdown_table(run_cmd(cmd))


def build_report(stocks: list[dict]) -> str:
    """生成财报日历报告"""
    now = datetime.now()
    lines = []
    lines.append(f"📅 持仓股财报日历 — {now.strftime('%Y-%m-%d')}")
    lines.append("=" * 50)

    has_data = False
    sources_seen = set()

    for s in stocks:
        code = s["code"]
        name = s["name"]

        # 财报预约披露（双数据源）
        reserves = query_reserve(code)
        exdivs = query_exdiv(code)

        if not reserves and not exdivs:
            continue

        has_data = True
        lines.append(f"\n📌 {name} ({code})")

        for r in reserves:
            desc = r.get("disclosureDesc", "")
            date = r.get("disclosureDate", "")
            src = r.get("source", "unknown")
            sources_seen.add(src)
            lines.append(f"  📊 财报披露: {date}  [源:{src}]")
            if desc:
                lines.append(f"     {desc}")

        for e in exdivs:
            ex_date = e.get("exDivDate", "")
            plan = e.get("dividendPlan", "")
            sources_seen.add("westock")
            lines.append(f"  💰 除权除息: {ex_date}  [源:westock] {plan}")

    if not has_data:
        lines.append("\n  （当前无持仓股财报预约数据）")

    lines.append("\n" + "=" * 50)
    src_label = "/".join(sorted(sources_seen)) if sources_seen else "westock"
    lines.append(f"数据来源: {src_label}（双数据源：westock 优先 + AnySearch 降级）")
    return "\n".join(lines)


def push_to_feishu(report: str):
    """推送到飞书群（经 push_card.py 发 interactive 卡片）"""
    card_script = os.path.abspath(
        os.path.join(CWD, ".workbuddy", "scripts", "push_card.py")
    )
    if not os.path.isfile(card_script):
        card_script = os.path.join(CWD, "scripts", "push_card.py")
    cmd = [sys.executable, card_script,
            "--title", "📅 持仓财报日历", "--level", "info",
            "--section", "", report, "--chat-id", FEISHU_CHAT]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=60, cwd=CWD)
        if result.returncode == 0:
            print("[feishu] ✅ 财报日历已推送（卡片）")
        else:
            print(f"[feishu] ⚠️ 推送失败: {result.stderr[:200]}", file=sys.stderr)
    except FileNotFoundError:
        print("[feishu] ⚠️ push_card.py 未找到，跳过推送", file=sys.stderr)


def main():
    if not _check_prerequisites():
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Claw 持仓股财报日历")
    parser.add_argument("--code", help="单只股票代码")
    parser.add_argument("--output", choices=["text", "json"], default="text",
                        help="输出格式 (默认 text)")
    parser.add_argument("--push", action="store_true", help="推送到飞书")
    args = parser.parse_args()

    stocks = []
    if args.code:
        name = next((s["name"] for s in PORTFOLIO if s["code"] == args.code), args.code)
        stocks.append({"code": args.code, "name": name})
    else:
        stocks = PORTFOLIO

    report = build_report(stocks)

    if args.output == "json":
        # 结构化输出
        data = {"report": report, "timestamp": datetime.now().isoformat()}
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(report)

    if args.push:
        push_to_feishu(report)


if __name__ == "__main__":
    main()
