#!/usr/bin/env python3
"""
实盘交易接口 — 通过MiniQMT API实现实盘自动交易（模拟接口）
注：实际使用时需要安装并配置MiniQMT，此处提供模拟接口用于测试
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import requests

# 加载 Claw 公共库
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
from error_handler import atomic_write_json

# MiniQMT API配置（根据实际安装情况修改）
API_BASE_URL = "http://127.0.0.1:8888"  # MiniQMT API地址（默认）
API_KEY = ""  # 从MiniQMT客户端获取
API_SECRET = ""  # 从MiniQMT客户端获取

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data" / "user"
CONFIG_FILE = DATA_DIR / "trading_config.json"

# 风控参数（与模拟交易一致）
MAX_POSITION_PCT = 0.50  # 单只股票最大仓位 50%
MAX_SECTOR_PCT = 0.60  # 同行业最大仓位 60%
STOP_LOSS_PCT = 0.08  # 止损线 -8%
TAKE_PROFIT_PCT = 0.30  # 止盈线 +30%


def load_config() -> dict:
    """加载交易配置"""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {
        "enabled": False,  # 默认关闭实盘交易
        "api_base_url": API_BASE_URL,
        "api_key": API_KEY,
        "api_secret": API_SECRET,
        "risk_control": {
            "max_position_pct": MAX_POSITION_PCT,
            "max_sector_pct": MAX_SECTOR_PCT,
            "stop_loss_pct": STOP_LOSS_PCT,
            "take_profit_pct": TAKE_PROFIT_PCT,
        },
        "dry_run": True,  # 默认模拟运行（不实际下单）
    }


def save_config(config: dict):
    """保存交易配置"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(CONFIG_FILE, config)


def get_account_info() -> dict:
    """
    获取账户信息
    实际使用时调用MiniQMT API
    """
    config = load_config()
    if config.get("dry_run"):
        # 模拟返回
        return {
            "total_assets": 30000.0,
            "cash": 15000.0,
            "market_value": 15000.0,
            "success": True,
        }

    # 实际调用MiniQMT API
    try:
        url = f"{config['api_base_url']}/account"
        headers = {
            "API-KEY": config["api_key"],
            "API-SECRET": config["api_secret"],
        }
        response = requests.get(url, headers=headers, timeout=10)
        return response.json()
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_positions() -> dict:
    """
    获取持仓信息
    实际使用时调用MiniQMT API
    """
    config = load_config()
    if config.get("dry_run"):
        # 模拟返回（从用户持仓文件读取）
        user_portfolio = DATA_DIR / "portfolio.json"
        if user_portfolio.exists():
            with open(user_portfolio, encoding="utf-8") as f:
                data = json.load(f)
                return {"success": True, "positions": data.get("positions", [])}
        return {"success": True, "positions": []}

    # 实际调用MiniQMT API
    try:
        url = f"{config['api_base_url']}/positions"
        headers = {
            "API-KEY": config["api_key"],
            "API-SECRET": config["api_secret"],
        }
        response = requests.get(url, headers=headers, timeout=10)
        return response.json()
    except Exception as e:
        return {"success": False, "error": str(e)}


def place_order(symbol: str, side: str, quantity: int, price: float) -> dict:
    """
    下单
    side: "buy" or "sell"
    实际使用时调用MiniQMT API
    """
    config = load_config()
    if config.get("dry_run"):
        # 模拟返回
        order_id = f"mock_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        return {
            "success": True,
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": price,
            "dry_run": True,
        }

    # 实际调用MiniQMT API
    try:
        url = f"{config['api_base_url']}/order"
        headers = {
            "API-KEY": config["api_key"],
            "API-SECRET": config["api_secret"],
        }
        data = {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": price,
        }
        response = requests.post(url, headers=headers, json=data, timeout=10)
        return response.json()
    except Exception as e:
        return {"success": False, "error": str(e)}


def cancel_order(order_id: str) -> dict:
    """
    撤单
    实际使用时调用MiniQMT API
    """
    config = load_config()
    if config.get("dry_run"):
        return {"success": True, "order_id": order_id, "dry_run": True}

    try:
        url = f"{config['api_base_url']}/order/{order_id}"
        headers = {
            "API-KEY": config["api_key"],
            "API-SECRET": config["api_secret"],
        }
        response = requests.delete(url, headers=headers, timeout=10)
        return response.json()
    except Exception as e:
        return {"success": False, "error": str(e)}


def check_risk_control(signal: dict) -> dict:
    """
    检查风险控制
    返回：{"pass": True/False, "reason": "..."}
    """
    config = load_config()
    risk = config.get("risk_control", {})

    # 1. 仓位检查
    if signal["action"] == "buy":
        account = get_account_info()
        if not account.get("success"):
            return {"pass": False, "reason": f"获取账户信息失败：{account.get('error', '')}"}

        total_assets = account.get("total_assets", 0)
        position_value = signal["quantity"] * signal["price"]
        position_pct = position_value / total_assets if total_assets > 0 else 1.0

        max_position_pct = risk.get("max_position_pct", MAX_POSITION_PCT)
        if position_pct > max_position_pct:
            return {
                "pass": False,
                "reason": f"单只股票仓位{position_pct:.1%}超过{max_position_pct:.1%}警戒线",
            }

    # 2. 止损检查（在execute_trading_signal中处理）
    # 3. 止盈检查（在execute_trading_signal中处理）

    return {"pass": True, "reason": ""}


def execute_trading_signal(signal: dict) -> dict:
    """
    执行交易信号
    signal格式：
    {
        "action": "buy" or "sell",
        "symbol": "600519",
        "quantity": 100,
        "price": 1800.0,
    }
    """
    # 1. 检查风险控制
    risk_check = check_risk_control(signal)
    if not risk_check["pass"]:
        return {"success": False, "reason": risk_check["reason"]}

    # 2. 执行交易（下单）
    result = place_order(
        signal["symbol"],
        signal["action"],
        signal["quantity"],
        signal["price"],
    )

    if not result.get("success"):
        return {"success": False, "reason": f"下单失败：{result.get('error', '')}"}

    # 3. 记录交易日志
    log_trade(signal, result)

    return {
        "success": True,
        "order_id": result.get("order_id"),
        "dry_run": result.get("dry_run", False),
    }


def log_trade(signal: dict, result: dict):
    """记录交易日志"""
    log_file = DATA_DIR / "trade_log.json"

    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "signal": signal,
        "result": result,
    }

    logs = []
    if log_file.exists():
        with open(log_file, encoding="utf-8") as f:
            logs = json.load(f)

    logs.append(log_entry)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(log_file, logs)


def analyze_trading_signal() -> list:
    """
    分析交易信号（从AI建议中获取）
    返回：[{ "action": "buy"/"sell", "symbol": "...", "quantity": 100, "price": 1800.0 }, ...]
    """
    # 这里简化实现，实际应该从WorkBuddy对话中或自动化输出中获取AI建议
    # 示例：从文件读取AI建议
    ai_suggestion_file = DATA_DIR / "ai_suggestions.json"
    if ai_suggestion_file.exists():
        with open(ai_suggestion_file, encoding="utf-8") as f:
            suggestions = json.load(f)
            return suggestions.get("signals", [])

    return []


def auto_trade():
    """
    自动交易主函数
    1. 分析交易信号
    2. 执行交易信号
    3. 检查止损止盈
    """
    # 1. 分析交易信号
    signals = analyze_trading_signal()

    results = []
    for signal in signals:
        # 2. 执行交易信号
        result = execute_trading_signal(signal)
        results.append(result)

    # 3. 检查止损止盈
    check_stop_loss_take_profit()

    return {
        "success": True,
        "signals_count": len(signals),
        "results": results,
    }


def check_stop_loss_take_profit():
    """
    检查持仓的止损止盈条件
    如果触发，自动卖出
    """
    config = load_config()
    risk = config.get("risk_control", {})
    stop_loss_pct = risk.get("stop_loss_pct", STOP_LOSS_PCT)
    take_profit_pct = risk.get("take_profit_pct", TAKE_PROFIT_PCT)

    # 获取持仓
    positions_result = get_positions()
    if not positions_result.get("success"):
        return

    positions = positions_result.get("positions", [])

    for pos in positions:
        symbol = pos.get("symbol", "")
        cost_price = pos.get("cost_price", 0)
        current_price = pos.get("current_price", 0)
        shares = pos.get("shares", 0)

        if cost_price == 0 or current_price == 0 or shares == 0:
            continue

        pnl_pct = (current_price - cost_price) / cost_price

        # 止损检查
        if pnl_pct <= -stop_loss_pct:
            # 触发止损，卖出
            signal = {
                "action": "sell",
                "symbol": symbol,
                "quantity": shares,
                "price": current_price,
            }
            execute_trading_signal(signal)

        # 止盈检查
        elif pnl_pct >= take_profit_pct:
            # 触发止盈，卖出
            signal = {
                "action": "sell",
                "symbol": symbol,
                "quantity": shares,
                "price": current_price,
            }
            execute_trading_signal(signal)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == "account":
            # 获取账户信息
            result = get_account_info()
            print(json.dumps(result, ensure_ascii=False, indent=2))

        elif command == "positions":
            # 获取持仓信息
            result = get_positions()
            print(json.dumps(result, ensure_ascii=False, indent=2))

        elif command == "trade":
            # 执行自动交易
            result = auto_trade()
            print(json.dumps(result, ensure_ascii=False, indent=2))

        elif command == "config":
            # 查看/修改配置
            if len(sys.argv) > 2 and sys.argv[2] == "set":
                # 修改配置（示例）
                config = load_config()
                config["dry_run"] = False  # 关闭模拟运行
                save_config(config)
                print("✅ 配置已更新：dry_run = False")
            else:
                config = load_config()
                print(json.dumps(config, ensure_ascii=False, indent=2))

        else:
            print(f"未知命令：{command}")
            print("可用命令：account, positions, trade, config")
    else:
        print("实盘交易接口脚本")
        print("用法：")
        print("  python3 real_trade.py account       # 获取账户信息")
        print("  python3 real_trade.py positions    # 获取持仓信息")
        print("  python3 real_trade.py trade         # 执行自动交易")
        print("  python3 real_trade.py config       # 查看配置")
        print("  python3 real_trade.py config set   # 修改配置")
