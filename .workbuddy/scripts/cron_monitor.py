#!/usr/bin/env python3
"""
定时监控脚本 — 由 cron 定时调用，不依赖 WorkBuddy 技能。
直接调用腾讯财经 API 获取行情，更新模拟持仓，写入报告文件。
"""

import json
import os
import subprocess
import sys
import urllib.request
from datetime import date, datetime, time
from pathlib import Path

# 加载 Claw 公共库
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
from error_handler import atomic_write_json

PROJECT_DIR = Path(__file__).resolve().parent.parent
PORTFOLIO_FILE = PROJECT_DIR / "data" / "simulation" / "portfolio.json"
REPORTS_DIR = PROJECT_DIR / "reports"
WATCH_FILE = PROJECT_DIR / "data" / "simulation" / "watchlist.json"

FEISHU_USER_ID = os.getenv("FEISHU_CHAT_ID", "")

if __name__ == "__main__" and not FEISHU_USER_ID:
    print("[ERROR] 环境变量 FEISHU_CHAT_ID 未设置，无法发送飞书通知", file=sys.stderr)

INITIAL_CAPITAL = 30000
STOP_LOSS = -0.08
TAKE_PROFIT = 0.30

# star_signal 集成 (v2.1)
try:
    from star_signal_adapter import get_star_signal, get_technical_score  # noqa: F401

    STAR_SIGNAL_AVAILABLE = True
except ImportError:
    STAR_SIGNAL_AVAILABLE = False


def fetch_quote(code: str) -> dict:
    """用腾讯财经 API 获取实时行情"""
    url = f"https://qt.gtimg.cn/q={code}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data_str = resp.read().decode("gbk", errors="replace")
    except Exception as e:
        return {"error": f"请求失败 {code}: {e}"}
    if not data_str or '="' not in data_str:
        return {"error": f"No data for {code}"}
    try:
        content = data_str.split('="')[1].rstrip('";')
        fields = content.split("~")
        return {
            "code": fields[2],
            "name": fields[1],
            "price": float(fields[3]) if fields[3] else 0,
            "prev_close": float(fields[4]) if fields[4] else 0,
            "open": float(fields[5]) if fields[5] else 0,
            "high": float(fields[33]) if len(fields) > 33 and fields[33] else 0,
            "low": float(fields[34]) if len(fields) > 34 and fields[34] else 0,
            "change": float(fields[31]) if len(fields) > 31 and fields[31] else 0,
            "change_pct": float(fields[32]) if len(fields) > 32 and fields[32] else 0,
            "volume": int(fields[6]) if fields[6] else 0,
            "amount_wan": float(fields[37]) if len(fields) > 37 and fields[37] else 0,
        }
    except Exception as e:
        return {"error": str(e)}


def fetch_money_flow(code: str) -> dict:
    """用东方财富 API 获取资金流向数据"""
    # 转换代码格式：600519 -> SH600519, 000858 -> SZ000858
    prefix = "SH" if code.startswith(("6", "5")) else "SZ"
    full_code = f"{prefix}{code}"
    url = f"http://push2.eastmoney.com/api/qt/stock/fflow/daykline/get?lmt=0&pos=-0&secid={full_code}&ut=b2884a393a59ad64002292a3e90d46a5&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63"

    try:
        result = subprocess.run(["curl", "-s", url], capture_output=True, text=True, timeout=15)
        data = json.loads(result.stdout)

        if data.get("rc") != 0 or "data" not in data or not data["data"]:
            return {"error": "No money flow data"}

        latest = data["data"]["klines"][-1] if data["data"]["klines"] else None
        if not latest:
            return {"error": "Empty klines"}

        # 格式: "日期,主力净流入,小单净流入,中单净流入,大单净流入,超大单净流入,主力净流入占比,..."
        parts = latest.split(",")
        return {
            "date": parts[0],
            "main_net": float(parts[1]),  # 主力净流入（万元）
            "small_net": float(parts[2]),
            "medium_net": float(parts[3]),
            "large_net": float(parts[4]),
            "xlarge_net": float(parts[5]),
            "main_pct": float(parts[6]),  # 主力净流入占比
        }
    except Exception as e:
        return {"error": str(e)}


def check_price_alert(quote: dict) -> str:
    """检测价格异动，返回预警信息或空字符串（集成 star_signal 评分）"""
    if "error" in quote:
        return ""

    code = quote.get("code", "")
    change_pct = quote.get("change_pct", 0)
    alerts = []

    # 价格异动预警
    if change_pct >= 5.0:
        alerts.append(f"📈 涨幅异常：+{change_pct:.2f}%")
    elif change_pct <= -5.0:
        alerts.append(f"📉 跌幅异常：{change_pct:.2f}%")

    # star_signal 综合评分（可选增强）
    if STAR_SIGNAL_AVAILABLE and code:
        try:
            signal = get_star_signal(code)
            if signal.get("strength", 0) >= 3:
                alerts.append(f"⭐ 信号: {signal['strength_name']}({signal['score']})")
            if signal.get("rsi", 50) > 70:
                alerts.append(f"⚠️ RSI超买: {signal['rsi']}")
        except Exception:
            print(f"[{now_str()}] ⚠️ signal read error for {code}", file=sys.stderr)

    return "  ".join(alerts)


def check_volume_alert(code: str, quote: dict) -> str:
    """检测成交量异常（简化版：基于当日成交量绝对值）"""
    if "error" in quote:
        return ""

    volume = quote.get("volume", 0)
    # 简化判断：成交量超过100万手视为异常（可根据个股调整）
    if volume > 1000000:
        return f"📊 成交量异常：{volume / 10000:.1f}万手"
    return ""


def send_feishu(markdown_text: str) -> bool:
    """通过飞书机器人发送 Markdown 消息"""
    env = os.environ.copy()
    env["LARK_CLI_NO_PROXY"] = "1"
    # 飞书 markdown 消息最大 30KB，超长截断
    max_bytes = 30000
    text_bytes = markdown_text.encode("utf-8")
    if len(text_bytes) > max_bytes:
        markdown_text = (
            text_bytes[:max_bytes].decode("utf-8", errors="replace") + "\n\n...(内容过长已截断)"
        )

    cmd = [
        "lark-cli",
        "im",
        "+messages-send",
        "--user-id",
        FEISHU_USER_ID,
        "--as",
        "bot",
        "--markdown",
        markdown_text,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
        if result.returncode == 0:
            return True
        else:
            print(f"[{now_str()}] 飞书发送失败: {result.stderr.strip()[:200]}")
            return False
    except Exception as e:
        print(f"[{now_str()}] 飞书发送异常: {e}")
        return False


def load_portfolio() -> dict:
    if PORTFOLIO_FILE.exists():
        return json.loads(PORTFOLIO_FILE.read_text())
    return {"cash": INITIAL_CAPITAL, "positions": {}, "transactions": [], "daily_snapshot": {}}


def save_portfolio(pf: dict):
    PORTFOLIO_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(PORTFOLIO_FILE, pf)


def today_str():
    return date.today().isoformat()


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_trading_day() -> bool:
    """检查今天是否是交易日（周一至五）"""
    return datetime.now().weekday() < 5


def is_market_hours() -> bool:
    """检查当前是否在 A 股交易时段 (9:30-11:30, 13:00-15:00)"""
    if not is_trading_day():
        return False
    now = datetime.now().time()
    morning = time(9, 30) <= now <= time(11, 30)
    afternoon = time(13, 0) <= now <= time(15, 0)
    return morning or afternoon


def is_close_time() -> bool:
    """检查是否刚收盘（15:00-15:10）"""
    if not is_trading_day():
        return False
    now = datetime.now().time()
    return time(15, 0) <= now <= time(15, 10)


def run_monitor():
    """盘中监控，非交易时段静默退出"""
    if not is_market_hours():
        return {"status": "silent", "reason": "非交易时段"}

    pf = load_portfolio()
    d = today_str()

    # 1. 查询持仓行情
    positions = pf.get("positions", {})
    if not positions:
        return {"status": "empty", "message": "无持仓"}

    updates = {}
    holding_lines = []
    total_market = 0.0
    alerts = []

    for code, pos in positions.items():
        q = fetch_quote(code)
        if "error" in q:
            # API 失败时使用已存储的最新价格（如收盘后）
            price = pos.get("current_price", pos["avg_cost"])
            change_pct = 0.0
        else:
            price = q["price"]
            change_pct = q["change_pct"]

        # 获取资金流向
        mf = fetch_money_flow(code)

        mv = pos["shares"] * price
        pnl = mv - pos["total_cost"]
        pnl_pct = (pnl / pos["total_cost"]) * 100 if pos["total_cost"] > 0 else 0
        total_market += mv

        # 更新价格
        pos["current_price"] = price
        updates[code] = price

        src_tag = "📡" if "error" not in q else "💾"
        emoji = "🔴" if pnl >= 0 else "🟢"

        # 资金流向显示
        money_str = ""
        if "error" not in mf:
            main_net = mf["main_net"]
            main_pct = mf["main_pct"]
            if main_net > 0:
                money_str = f" 💰主力+{main_net:.0f}万({main_pct:.1f}%)"
            else:
                money_str = f" 💸主力{main_net:.0f}万({main_pct:.1f}%)"

        holding_lines.append(
            f"  {code} {pos['name']:　<6} {src_tag}{emoji} {price:.2f} ({change_pct:+.2f}%)  "
            f"市值 ¥{mv:,.0f}  盈亏 {pnl:+,.0f} ({pnl_pct:+.2f}%){money_str}"
        )

        # 技术指标预警
        price_alert = check_price_alert(q)
        volume_alert = check_volume_alert(code, q)
        tech_alerts = [a for a in [price_alert, volume_alert] if a]
        if tech_alerts:
            alerts.append(f"📊 技术信号：{pos['name']}({code}) {'  '.join(tech_alerts)}")

        # 止损/止盈检查
        if pnl_pct <= -8.0:
            alerts.append(
                f"🚨 止损预警：{pos['name']}({code}) 亏损 {pnl_pct:.2f}%，触发止损线 -8%！"
            )
        elif pnl_pct >= 30.0:
            alerts.append(
                f"💰 止盈提醒：{pos['name']}({code}) 盈利 {pnl_pct:.2f}%，达到止盈线 30%！"
            )
        elif pnl_pct <= -5.0:
            alerts.append(f"⚠️ 注意：{pos['name']}({code}) 亏损 {pnl_pct:.2f}%，接近止损线")

        # 资金异动预警
        if "error" not in mf:
            main_net = mf["main_net"]
            if main_net < -5000:  # 主力净流出超过5000万
                alerts.append(
                    f"⚠️ 资金异动：{pos['name']}({code}) 主力大幅流出 {main_net:.0f}万，注意风险！"
                )
            elif main_net > 5000:  # 主力净流入超过5000万
                alerts.append(
                    f"💡 资金异动：{pos['name']}({code}) 主力大幅流入 {main_net:.0f}万，关注机会！"
                )

    # 2. 更新持仓价格到文件
    pf["positions"] = positions

    # 3. 记录快照
    cash = pf.get("cash", 0)
    total_asset = cash + total_market
    snapshot = pf.get("daily_snapshot", {})
    snapshot[d] = {
        "date": d,
        "time": now_str(),
        "total_asset": round(total_asset, 2),
        "cash": round(cash, 2),
        "market_value": round(total_market, 2),
        "positions": {
            code: {"price": pos.get("current_price", 0), "shares": pos["shares"]}
            for code, pos in positions.items()
        },
    }
    pf["daily_snapshot"] = snapshot

    # 4. 计算累计收益
    cum_pnl = total_asset - INITIAL_CAPITAL
    cum_pnl_pct = cum_pnl / INITIAL_CAPITAL * 100

    # 5. 写入报告文件
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report = []
    report.append(f"📈【投顾操盘】{d} 盘中监控")
    report.append("━" * 50)
    report.append(
        f"💰 总资产：¥{total_asset:,.0f} | 现金：¥{cash:,.0f} | 市值：¥{total_market:,.0f}"
    )
    report.append(f"📊 累计盈亏：{cum_pnl:+,.0f} ({cum_pnl_pct:+.2f}%)")
    report.append("")
    report.append("📋 持仓：")
    for line in holding_lines:
        report.append(line)

    if alerts:
        report.append("")
        for a in alerts:
            report.append(a)

    report_file = REPORTS_DIR / f"monitor_{d}.md"
    report_text = "\n".join(report)
    report_file.write_text(report_text)

    # 6. 发送飞书通知
    feishu_sent = send_feishu(report_text)

    # 7. 保存持仓
    save_portfolio(pf)

    return {
        "status": "ok",
        "total_asset": round(total_asset, 2),
        "cum_pnl_pct": round(cum_pnl_pct, 2),
        "alerts": alerts,
        "report_file": str(report_file),
        "feishu_sent": feishu_sent,
    }


def run_snapshot_only():
    """仅记录收盘快照，不做预警"""
    pf = load_portfolio()
    d = today_str()
    cash = pf.get("cash", 0)
    total_market = sum(
        pos["shares"] * pos.get("current_price", pos["avg_cost"])
        for pos in pf.get("positions", {}).values()
    )
    total_asset = cash + total_market

    snapshot = pf.get("daily_snapshot", {})
    snapshot[d] = {
        "date": d,
        "time": now_str(),
        "total_asset": round(total_asset, 2),
        "cash": round(cash, 2),
        "market_value": round(total_market, 2),
        "positions": {
            code: {"price": pos.get("current_price", 0), "shares": pos["shares"]}
            for code, pos in pf.get("positions", {}).items()
        },
    }
    pf["daily_snapshot"] = snapshot
    save_portfolio(pf)

    cum_pnl = total_asset - INITIAL_CAPITAL
    cum_pnl_pct = cum_pnl / INITIAL_CAPITAL * 100

    # 写收盘报告
    report = []
    report.append(f"📝 {d} 收盘回顾")
    report.append("━" * 50)
    report.append(f"💰 总资产：¥{total_asset:,.0f} | 累计：{cum_pnl:+,.0f} ({cum_pnl_pct:+.2f}%)")

    for code, pos in pf.get("positions", {}).items():
        pnl = pos["shares"] * pos.get("current_price", pos["avg_cost"]) - pos["total_cost"]
        pnl_pct = (pnl / pos["total_cost"]) * 100 if pos["total_cost"] > 0 else 0
        report.append(
            f"  {code} {pos['name']} 收盘 {pos.get('current_price', 0):.2f}  盈亏 {pnl:+,.0f} ({pnl_pct:+.2f}%)"
        )

    report_file = REPORTS_DIR / f"close_{d}.md"
    report_text = "\n".join(report)
    report_file.write_text(report_text)
    # 收盘报告不推送飞书，由自动化在微信对话中呈现


# ══════════════════════════════════════════════════
#  股债相关性监控 (v2.3 — 策略层数据基础设施)
# ══════════════════════════════════════════════════

CORR_DATA_DIR = PROJECT_DIR / "data" / "correlation"


def fetch_csi300_daily() -> list:
    """获取沪深300日线历史数据（252个交易日以上）"""
    # 东方财富K线API
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get?"
        "fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        "&lmt=300&klt=101&ut=2887a9128e9d96a09a7f33fe1e6097c7&"
        "secid=1.000300"  # 沪深300
    )
    try:
        result = subprocess.run(["curl", "-s", url], capture_output=True, text=True, timeout=15)
        data = json.loads(result.stdout)
        if data.get("rc") != 0 or "data" not in data or not data["data"]:
            return []
        klines = data["data"].get("klines", [])
        # kline format: "日期,开盘,收盘,最高,最低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率"
        daily_returns = []
        prev_close = None
        for k in klines:
            parts = k.split(",")
            close_price = float(parts[2])
            if prev_close is not None and prev_close > 0:
                ret = (close_price - prev_close) / prev_close
            else:
                ret = 0.0
            daily_returns.append({"date": parts[0], "return": round(ret, 6)})
            prev_close = close_price
        return daily_returns
    except Exception as e:
        print(f"[{now_str()}] 沪深300数据获取失败: {e}")
        return []


def fetch_bond_10y_yield() -> list:
    """
    获取10年期国债收益率日频数据。

    数据源优先级:
    1. AKShare bond_china_yield（需 AKShare 安装 + 循环拉取每年数据）
    2. 东方财富中债收益率曲线（备选）

    ⚠️ 当前状态：AKShare 存在架构兼容问题 (x86_64 vs arm64)，
    此函数在 AKShare 修复前返回空列表，可通过手动下载 CSV 补充。
    """
    # AKShare 方案（当前不可用）
    # import akshare as ak
    # curve = ak.bond_china_yield(start_date="20250101", end_date="20251231")
    # yield_10y = curve[curve['曲线名称'] == '中债国债收益率曲线'][['日期', '10年']]

    # TODO: 备选方案 — 东方财富中债API
    # 中国债券信息网每日公布，可通过 https://yield.chinabond.com.cn/ 获取

    return []  # 当前无数据源


def calc_pearson_corr(x: list, y: list) -> float:
    """计算 Pearson 相关系数（纯标准库，无依赖）"""
    n = len(x)
    if n != len(y) or n < 3:
        return 0.0

    sum_x = sum(x)
    sum_y = sum(y)
    sum_xy = sum(xi * yi for xi, yi in zip(x, y))
    sum_x2 = sum(xi * xi for xi in x)
    sum_y2 = sum(yi * yi for yi in y)

    numerator = n * sum_xy - sum_x * sum_y
    denominator = ((n * sum_x2 - sum_x * sum_x) * (n * sum_y2 - sum_y * sum_y)) ** 0.5

    if denominator == 0:
        return 0.0
    return numerator / denominator


def check_stock_bond_correlation() -> dict:
    """
    计算沪深300与10年期国债的滚动相关性，监控分散化失效风险。

    参考：长江证券大类资产配置报告方法论 —
    股债相关性转正时，固定比例配置和风险平价均可能失效。
    """
    # 1. 获取沪深300日收益率
    csi300 = fetch_csi300_daily()
    if not csi300 or len(csi300) < 252:
        return {
            "status": "insufficient_data",
            "message": f"沪深300仅有 {len(csi300)} 天数据，需≥252天",
        }

    # 2. 获取10年期国债收益率变化
    bond_yield = fetch_bond_10y_yield()
    if not bond_yield or len(bond_yield) < 252:
        return {
            "status": "missing_bond_data",
            "message": "10年期国债收益率数据不可用（AKShare 架构兼容问题），需手动提供债券日频数据",
            "suggestion": "安装 AKShare arm64 版本 或 手动下载中债收益率CSV",
        }

    # 3. 计算滚动相关系数（252日窗口）
    csi300_rets = [d["return"] for d in csi300[-252:]]
    bond_yield_changes = [d["change"] for d in bond_yield[-252:]]

    rolling_corr_252d = calc_pearson_corr(csi300_rets, bond_yield_changes)

    # 4. 6个月滚动窗口（约126个交易日）
    rolling_corr_126d = calc_pearson_corr(csi300_rets[-126:], bond_yield_changes[-126:])

    # 5. 判定
    corr_126d = rolling_corr_126d
    status = "normal"
    alert = None

    if corr_126d is not None:
        if corr_126d > 0.3:
            status = "warning"
            alert = (
                f"⚠️ 股债相关性转正（6月滚动相关系数={corr_126d:+.3f}），"
                f"252日滚动={rolling_corr_252d:+.3f}。固定比例/风险平价配置可能失效。"
            )
        elif corr_126d > 0:
            status = "caution"
            alert = (
                f"📊 股债相关性接近零值（6月滚动={corr_126d:+.3f}），"
                f"关注趋势变化。252日滚动={rolling_corr_252d:+.3f}。"
            )

    return {
        "status": status,
        "corr_252d": round(rolling_corr_252d, 3) if rolling_corr_252d else None,
        "corr_126d": round(corr_126d, 3) if corr_126d else None,
        "alert": alert,
        "data_points": len(csi300),
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "auto"

    if mode == "daemon":
        import time as _time

        print(f"[{now_str()}] 守护进程启动，每15分钟检查一次（仅交易日 9:30-15:00）")
        while True:
            try:
                if is_close_time():
                    run_snapshot_only()
                    print(f"[{now_str()}] 收盘快照已记录")
                elif is_market_hours():
                    result = run_monitor()
                    print(
                        f"[{now_str()}] 监控完成: 总资产 ¥{result.get('total_asset', 0):,.0f}, 盈亏 {result.get('cum_pnl_pct', 0):+.2f}%, 飞书{'✓' if result.get('feishu_sent') else '✗'}"
                    )
                else:
                    pass  # 非交易时段静默
            except Exception as e:
                print(f"[{now_str()}] 错误: {e}")
            _time.sleep(900)  # 15 分钟
    elif mode == "auto":
        if is_close_time():
            run_snapshot_only()
            print(f"[{now_str()}] 收盘快照已记录: {today_str()}")
        elif is_market_hours():
            result = run_monitor()
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            pass
    elif mode == "monitor":
        result = run_monitor()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif mode == "close":
        run_snapshot_only()
        print(f"收盘快照已记录: {today_str()}")
    elif mode == "portfolio":
        pf = load_portfolio()
        print(
            json.dumps(
                {"cash": pf["cash"], "positions": pf["positions"]}, ensure_ascii=False, indent=2
            )
        )
    else:
        print(f"Unknown mode: {mode}")
