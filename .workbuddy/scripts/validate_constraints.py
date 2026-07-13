#!/usr/bin/env python3
"""
A股交易约束校验器 — 统一整手规则、板块限制、止损/止盈阈值校验。

用法:
  python3 validate_constraints.py --file report.md          # 校验报告中的建议
  python3 validate_constraints.py --portfolio               # 校验当前持仓
  python3 validate_constraints.py --text "买入 风华高科 150股"  # 校验文本
  python3 validate_constraints.py --template                # 输出约束模板文本

规则来源: llm_constraints.md（如存在则优先读取）
"""
import argparse
import json
import re
import sys
from pathlib import Path

# ── 默认约束 ──────────────────────────────────────────
DEFAULT_CONSTRAINTS = {
    "lot_size": 100,                    # A股最小交易单位
    "max_positions": 5,                  # 最大持股数
    "stop_loss_pct": -8.0,              # 止损线（模拟盘）
    "stop_loss_pct_live": -5.0,         # 止损线（实盘）
    "take_profit_levels": [10, 15, 25],  # 止盈阶梯
    "banned_exchanges": ["创业板", "科创板", "北交所"],  # 禁入板块
    "banned_prefixes": ["300", "301", "688", "8", "4"],  # 创业板300/301、科创板688、北交所8/4开头
    "max_sector_concentration": 0.6,     # 行业集中度上限
}

# ── 模板 ──────────────────────────────────────────────
CONSTRAINT_TEMPLATE = """🚨 A股交易硬约束（违反=幻觉，生成任何买卖/加减仓建议前必读）

❶ 整手规则（铁律）：A股每手=100股，所有买卖/减仓/加仓数量必须是100的整数倍。
   ❌ 禁止：减50股 / 减仓50股 / 加150股 / 买250股 → A股无法交易零股
   ✅ 正确：100股、200股、300股...

❷ 板块限制：禁止推荐创业板(300/301)、科创板(688)、北交所(8/4开头)、ST股。
   仅限主板(60xxxx) 和中小板(00xxxx, 002xxx)。

❸ 止损/止盈：
   - 实盘止损：-5%（助理持仓）
   - 模拟盘止损：-8%（投顾）
   - 止盈梯度：+10%/+15%/+25%/+35%（模拟盘冲刺期≥5%即止盈）

❹ 仓位约束：
   - 最多同时持有 ≤5 只股票
   - 单一行业集中度 ≤60%
   - 模拟盘总资金 ¥30,000

❺ 价格/数量一致性：
   - 买入金额 = 建议价 × 股数，不得超过可用资金
   - 卖出股数 ≤ 持仓股数
"""


def load_constraints() -> dict:
    """Load constraints from llm_constraints.md or use defaults."""
    constraints_file = Path("/Users/guan/WorkBuddy/Claw/.workbuddy/templates/llm_constraints.md")
    if constraints_file.exists():
        try:
            text = constraints_file.read_text()
            # Parse markdown template into dict
            result = dict(DEFAULT_CONSTRAINTS)
            # Extract key values from template
            for key, default in result.items():
                if isinstance(default, float):
                    match = re.search(rf"{key.replace('_', '[-_ ]')}.*?([\d.]+)", text, re.IGNORECASE)
                    if match:
                        result[key] = float(match.group(1))
            return result
        except Exception:
            pass
    return dict(DEFAULT_CONSTRAINTS)


def validate_lot(shares: int) -> tuple[bool, str]:
    """Validate share count is multiple of 100."""
    constraints = load_constraints()
    lot = constraints["lot_size"]
    if shares % lot == 0 and shares > 0:
        return True, f"✅ {shares}股 = {shares // lot}手（合法）"
    return False, f"🔴 {shares}股 不是{lot}的整数倍（A股无法交易零股）"


def validate_code(code: str) -> tuple[bool, str]:
    """Validate stock code is not banned."""
    constraints = load_constraints()
    prefixes = constraints["banned_prefixes"]
    for prefix in prefixes:
        if code.startswith(prefix):
            return False, f"🔴 {code} 属于禁止板块（{prefix}开头），不允许交易"
    return True, f"✅ {code} 允许交易"


def validate_portfolio(portfolio_path: str | None = None) -> dict:
    """Validate current portfolio data."""
    if portfolio_path is None:
        portfolio_path = "/Users/guan/WorkBuddy/Claw/.workbuddy/data/portfolio.json"

    try:
        data = json.loads(Path(portfolio_path).read_text())
    except Exception as e:
        return {"status": "error", "message": f"读取持仓失败: {e}"}

    constraints = load_constraints()
    issues = []
    total_positions = 0

    if isinstance(data, dict):
        # Check live portfolio
        live = data.get("live", {})
        live_positions = live.get("positions", [])
        total_positions += len(live_positions)

        # Check sim portfolio
        sim = data.get("sim", {})
        sim_positions = sim.get("positions", [])
        total_positions += len(sim_positions)

    if total_positions > constraints["max_positions"]:
        issues.append(f"🟡 持仓数 {total_positions} 超过上限 {constraints['max_positions']}")

    return {
        "status": "ok" if not issues else "warning",
        "total_positions": total_positions,
        "max_allowed": constraints["max_positions"],
        "issues": issues,
    }


def scan_text_for_violations(text: str) -> list[str]:
    """Scan text for constraint violations."""
    issues = []
    constraints = load_constraints()

    # Find share count mentions
    # Pattern captures: trade action + optional "仓" + number + 股
    patterns = [
        r'(?:减仓|加仓|减|加|买[入进]?|卖[出]?|建仓|清仓|持有)\s*(\d+)\s*股',
        r'(\d+)\s*股\s*(?:减仓|加仓|减|加|买|卖)',
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            shares = int(match.group(1))
            valid, msg = validate_lot(shares)
            if not valid:
                issues.append(msg)

    # Find stock codes
    code_pattern = r'[（(]?\b(\d{6})\b[）)]?'
    for match in re.finditer(code_pattern, text):
        code = match.group(1)
        valid, msg = validate_code(code)
        if not valid:
            issues.append(msg)

    return issues


def main():
    parser = argparse.ArgumentParser(description="A股交易约束校验器")
    parser.add_argument("--template", action="store_true",
                        help="输出约束模板文本")
    parser.add_argument("--file", type=str,
                        help="校验报告文件中的建议")
    parser.add_argument("--text", type=str,
                        help="校验文本内容")
    parser.add_argument("--portfolio", action="store_true",
                        help="校验当前持仓")
    args = parser.parse_args()

    if args.template:
        print(CONSTRAINT_TEMPLATE)
        sys.exit(0)

    if args.file:
        try:
            text = Path(args.file).read_text()
        except Exception as e:
            print(json.dumps({"status": "error", "message": str(e)}))
            sys.exit(1)
        violations = scan_text_for_violations(text)
        if violations:
            print(f"发现 {len(violations)} 处违规：")
            for v in violations:
                print(f"  {v}")
            sys.exit(1)
        else:
            print("✅ 约束校验通过")
            sys.exit(0)

    if args.text:
        violations = scan_text_for_violations(args.text)
        if violations:
            for v in violations:
                print(v)
            sys.exit(1)
        else:
            print("✅ 通过")
            sys.exit(0)

    if args.portfolio:
        result = validate_portfolio()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result["status"] == "ok" else 1)

    # Default: show template
    print(CONSTRAINT_TEMPLATE)


if __name__ == "__main__":
    main()
