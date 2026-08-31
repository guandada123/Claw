# 2026-08-24 signal_verify Wind 日限刷屏 — _check_query_limit 去重修复

**现象**：signal_verify（信号溯源 15:00 自动化 STEP1）连续 25 天在 Wind 日限 180 次耗尽后，输出刷屏 40+ 行 `Wind 每日查询上限已达 (180次)，今日暂停`，单次执行 4m56s。

**根因**（证据链）：
1. `src/claw/feeds/wind_utils.py::_check_query_limit()` 在日限达到时每次调用都 `logger.warning(...)`，**无进程内去重标志**。
2. `signal_verify.py::fetch_realtime`（Wind 优先→腾讯降级）与 `fetch_history`（腾讯 qfq→Wind→akshare）逐股调用，每只股票触发 `get_wind_realtime_price`/`get_wind_kline` → `call_wind_cli` → `_check_query_limit`，日限时每只股票打印 1-2 次 warning。
3. `wind_available()` 只检查 CLI 存在 + API Key，**不检查日限** → 日限耗尽后仍走 Wind 分支，逐股触发打印。
4. 执行时长来源：`signal_verify` 二次补拉 `sleep(3.0)+sleep(1.0)` 对每个失败 code 重试（规避新浪限流），Wind 日限 + 腾讯 qfq 失败时大量 code 进 failed → 5 分钟。

**修复**（08-24，最小零风险）：
- `wind_utils.py` 加模块级 `_limit_warned = False` 标志，`_check_query_limit` 内 `global _limit_warned`；日限警告仅打印一次，跨天（`_daily_query_date != today`）重置标志。
- 仅影响日志打印，不改查询计数/返回逻辑，零行为风险。

**验证**：`ast.parse` 语法 OK；`from claw.feeds.wind_utils import _check_query_limit` import OK；`get_query_stats()` → `{used:180, remaining:0}` 确认日限已满触发场景。

**✅已升级(2026-08-30)**：任何「日限/配额/熔断类」日志打印必须进程内去重（once flag + 跨周期重置），禁止在逐股/逐条循环内裸 `logger.warning`——否则会刷屏 + 掩盖真实异常行，且每次执行多耗数分钟 CPU。

**遗留待办（💭 P2，未在本轮处理）**：signal_verify 二次补拉对「Wind 日限导致的历史数据失败」仍做 `sleep(3)+sleep(1)` 无意义重试（日限当天不会恢复），可加「日限达时跳过二次补拉」短路。需先确认腾讯 qfq 源为何失败（疑似代理不可达，非本脚本职责）。
