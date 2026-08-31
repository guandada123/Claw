# 2026-08-31 模拟盘收盘价回写断流 —— 巡检「数据新鲜度」三重盲区

★升级候选

## 现象
统一巡检中枢 15 项检查**全绿**（数据新鲜度 0 异常），但模拟盘 `portfolio.json`
的持仓价格停留在 3 个交易日前（8/28 收盘），8/31 收盘价从未回写。

## 取证链（每一步都实锤，非推断）
1. `simulation/portfolio.json` → `config.updated_at = 2026-08-31 10:14:57`（**盘中**）
   ，但三只持仓 `price_source = "腾讯qt.gtimg.cn·收盘2026-08-28 15:55"`。
2. 腾讯 `qt.gtimg.cn` 实拉 8/31 收盘，与账面价逐一比对（8/31 为交易日，16:14 数据时间戳）：

| 标的 | 股数 | 账面价(8/28) | 实际收盘(8/31) | 涨跌 | 市值偏差 |
|---|---|---|---|---|---|
| 招商银行(600036) | 200 | 39.35 | 40.12 | +1.96% | +154 |
| 中国建筑(601668) | 1000 | 4.46 | 4.33 | **-2.91%** | -130 |
| 三一重工(600031) | 300 | 19.10 | 20.01 | **+4.76%** | +273 |

   持仓市值偏差 **+297 元**；按 5 万本金，账面收益率 +6.57% vs 真实 +7.16%（差 0.59pct）。
   单只偏差最大 4.76% —— 已逼近 -8% 止损线的半程，**会污染止损/止盈判定**。

## 根因
`sim_trade.py:924 cmd_update_price` / `:949 cmd_update_all_prices` 是**被动命令**，
必须由外部自动化显式调用才会回写。**当前 55 个自动化中没有任何一个在收盘后调用它**：
- `automation-1787758708068`「Claw→QTS 模拟盘 portfolio 同步」15:10 —— 名字最像，
  但脚本 `sync_claw_to_qts_portfolio.py` 全文只有 `copy2` 搬运逻辑，**零价格刷新代码**。
- 15:50「综合复盘+晚报」等其余任务均不写 `simulation/portfolio.json` 的 `current_price`。

## 三重盲区（这才是真问题）
1. **白名单盲区**：`unified_ops_center.py:874 check_data_freshness()` 只盯 4 个文件
   （`qts_daily_signals` / `qts_regime` / `signal_consensus` / `source_weights`），
   全在 `CLAW_ROOT/data/`，**不含** `.workbuddy/data/simulation/portfolio.json`。
2. **★本质盲区：mtime 新鲜 ≠ 内容新鲜**。该函数判据是「文件 mtime 是否为当日」。
   而 portfolio.json 今天 10:14 **被写过**（mtime 当日）→ 判定"当日新鲜"，
   但**业务字段（价格日期）陈旧 3 天**。**即使把 portfolio.json 加进白名单也照样漏检。**
   → 凡"文件被写但业务字段未更新"的场景，mtime 判据**系统性失效**。
   正确判据：解析内容里的业务时间戳（`price_source` / `updated_at`）与最近交易日比对。
3. **命名误导盲区**：自动化名含"同步"易被当成"数据已刷新"。同步 ≠ 刷新，
   搬运脚本不会创造价值。查"数据是否新鲜"必须追到**写入方**，而非名称相近的任务。

## 副产物：旧待办①已修复（记忆滞后，非缺陷）
用户长期待办记「portfolio.json initial_capital 未同步（3万→5万）」。**实测已正确修复**：
`initial_capital` 保持 30000（保留原始本金可追溯）+ `capital_additions` 记录 20000，
Claw 侧三个消费方口径一致 = 50000：
- `sim_trade.py:83-88`（注释即写"与 capital_additions 脱节"已修）
- `performance_dashboard.py:56-60`
- `generate_daily_report.py:97-105`（报告展示本金构成）
→ **这是正确设计，不是 bug。待办①应关闭。**
注意 QTS 仓 grep `capital_additions` 零命中、**Claw 仓 3 命中** —— 又一次证明
「跨仓资产必须在归属仓查消费方，否则必然误判」（同 run#39「同构表象≠同构根因」）。

## 排查中推翻的两次误判（值得记）
1. QTS 仓 grep `initial_capital` 命中 `feishu_daily_report.py:318` → 差点定性"日报收益率分母错"。
   **实查：`sim_total` 全文件仅 1 处赋值、零引用 = 死变量**，无实际影响。
   → **命中消费方 ≠ 真消费。必须验证变量/字段真的被下游读取。**
2. `capital_additions` 在 QTS 仓零消费方 → 差点定性"字段无人消费=缺陷"。
   **实查：Claw 仓 3 个消费方正确实现。** → 排查范围错了，结论必然错。

## 未擅自处置（交用户决策）
- **未改 portfolio.json 数据**：改价格会影响浮亏/止损链路，且正确口径（收盘价 vs 均价）
  未经确认、15:50 复盘任务是否稍后覆盖亦未知。盲改风险 > 收益。
- **未改 unified_ops_center.py**：每 1h 的守护级脚本，改错 = 整层巡检失效。

## 建议修复（3 条，按优先级）
1. **P0 补调度**：新建/改「模拟盘收盘价回写」自动化，交易日 15:05 调用
   `sim_trade.py update-all-prices`（腾讯源），跑在 15:10 同步**之前**。
2. **P1 补巡检**：`check_data_freshness()` 增加 portfolio 价格时效检查，
   **用内容里 `price_source` 的日期 vs 最近交易日**，而非 mtime。
3. **P2 补语义**：把 `check_data_freshness` 的 mtime 判据整体升级为
   「mtime 粗筛 + 业务时间戳精判」双段式，覆盖所有"写了但没更新"的场景。
