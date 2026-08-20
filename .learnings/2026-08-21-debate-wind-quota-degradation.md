# 2026-08-21 Wind 配额耗尽致辩论降级 — 数据链路去 Wind 化修复

**现象**：08-20 早报标注「Wind MCP 当日 180 次查询限额已耗尽，辩论/基本面部分指标降级为本地+AnySearch，置信度偏低」；辩论记录 `data:{}` 无法审计，候选股（神华/恒瑞/中铝）出现「数据缺失」盲猜。

**根因**（证据链）：
1. `run_debate.py::_enrich_stock_data` 技术面 RSI/MA20 走 `AdvisorRules._get_rsi/_get_ma20` → **Wind 优先** → 每次辩论烧 180 次/日配额（与鱼盆/宏观共用），限额耗尽后技术面拿不到。
2. `fundamental_cache.json` 仅覆盖 7 只持仓，**候选股无基本面** → 格雷厄姆等专家「数据缺失」→ LLM 诚实输出「数据不足→观望」→ 表现像降级、实际空跑。
3. `debate_from_codes` 路径（08-20 09:16 亨通/神华/石化）**根本没调 `_enrich_stock_data`**，只喂价格+涨跌幅。
4. `run_debate` 结果不存 `data` 字段 → 复盘时无法判断是「真降级(LLM失败)」还是「数据不足(LLM正常)」。

**修复**（08-21）：
- 技术面全本地化：RSI=calc_rsi.py 子进程（腾讯 ifzq）、MA20/MACD/量比=qts_client 本地K线 → **辩论链路零 Wind 配额**。
- 基本面兜底：缓存缺失 → `anysearch_helper.a_stock_quote/a_stock_indicator`（westock→AnySearch，不耗 Wind）→ 回写缓存（`_write_fundamental_cache`，fcntl 锁）。
- `debate_from_codes` 补 `_enrich_stock_data` 调用。
- `run_debate` result 增加 `data` 快照 + `verdict.data_insufficient` 标记缺失视角。

**验证**：10/10 全量覆盖（RSI/MA20/MACD + PE/PB/ROE）；长电 600584 端到端 SELL conf=0.85（此前数据缺失降级 0.4）；中铝 601600 从「数据缺失」→ HOLD conf=0.62 含 PE10.73/PB1.95/ROE7.1 实质分析；454 tests passed。

**★ 升级候选**：辩论数据链路不得依赖 Wind 配额（技术面本地/基本面多源兜底）；「降级」须区分 LLM 失败 vs 数据不足（data_insufficient 标记）。

**关键坑**：`str(Path) / "file"` 字符串除法 TypeError 被 except 吞掉 → RSI 静默缺失；debug 时先单跑子进程再集成。

**审计跟进（08-21 变更审计）**：
- ✅ 修复有效：454 tests passed；最新记录 601600/600584 已带实质基本面分析（conf 0.62/0.85），technical/fundamental 不再缺失。
- ⚠️ P1-1：`data_insufficient` 判定四键(technical/fundamental/fund_flow/sentiment)与 `_enrich_stock_data` 补全维度(仅前两者)不对齐 → 恒标 `['fund_flow','sentiment']`（实证：全部新记录）。fund_flow 可用 cron_monitor 腾讯+东财资金流补齐；或判定基准改为设计内必备键 + 单列 design_gap。
- ⚠️ P1-2：`data_insufficient` 无消费方（trace_signal/早报/日报均未读）→ 字段埋了没接线，修复效果无法在报告体现。
- 💭 P2：`debate_from_scan` 仍用 `item.get("data",{})` 原始 scan 数据未补全（当前 scan_candidates 不存在，路径未激活）；`_ema_series` 无 len<period 防御；market_cap 单位假设(万元/1e4→亿)未端到端抽查；debate_from_codes change_pct 恒 0。
- 💭 P3：`except Exception: pass` 静默风格仍在，建议至少 logger.debug。

**二次修复（08-21 07:15 执行）**：
- ✅ P1-1 落地：`_assess_data_sufficiency` 纯函数判定键=设计内三维(technical/fundamental/fund_flow)，sentiment 无本地源→单列 design_gap（只说明不降级）。
- ✅ **P1-1 深层发现**：`build_user_prompt` 资金面读 `main_net_inflow/northbound_change/margin_change`，而补全写入 `main_net_wan/main_pct` → 键不对齐专家 prompt 恒显 "?"。统一为 `main_net_inflow`(万元)+`main_pct`(%)，删除无数据源的 northbound/margin 占位。
- ✅ **P1-1 二次修正（恒标噪音换键问题）**：东财 push2 整体不可达时 fund_flow 缺失 → 恒标 `['fund_flow']`（同病复发）。改：数据源不可达写 `{"_source":"unavailable"}` 显式标记 → 不标 data_insufficient，prompt 显式告知"数据源不可用，不得据此强判"（环境问题≠个股数据不足，须先修数据源）。
- ✅ P1-2 接线：trace_signal + run_debate show_latest 展示 data_insufficient/design_gap（早报/日报为独立流程不消费辩论结果）。
- ✅ P2：scan 路径补 `_enrich_stock_data`；`_ema_series` len<period 防御；codes change_pct 解析腾讯昨收不再恒 0；fundamental_cache 市值单位抽查通过（美的6302/招行9779/神华10096 均=亿）。
- ✅ P3：关键失败路径由 `pass` 改 `logger.debug`（run_debate 全程）。
- 测试：tests/ 471 + .workbuddy/tests/ 185 = **656 passed**（新增 17 例数据完整性回归）；ruff 0 待处理；bandit(CI 口径) 0 中危/0 高危。
- 冒烟：长电 600584 data_insufficient=`[]`（此前恒标 fund_flow），design_gap=`['sentiment']`，prompt 资金面显式"数据源不可用"。
