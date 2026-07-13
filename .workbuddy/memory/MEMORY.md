# 项目记忆

## 核心规则（不可违反）
- **渠道分工**：所有投资类自动化 → 飞书群 oc_9ee5303497f5e0e71666b610d6bdc346（直接推送，无需审核）；系统维护类（记忆/清理/健康检查）不推送
- **推送前缀**：每条投资类消息必须带 📈投顾操盘 / 📊炒股助理 / 🇺🇸美股监控
- **数据删除铁律**：删除任何文件/目录前必须找用户确认；移动/删除须「先复制→验证→再删源」
- **三系统边界**（互不混淆、数据隔离）：
  1. 📈投顾操盘 = AI 全权基金经理，¥30,000 自主决策/执行，用户只旁观（月目标≥30%，止损8%/止盈≥30%）
  2. 📊炒股助理 = 用户实盘持仓监控建议
  3. 🇺🇸美股监控 = 美股盘前/盘中/收盘
- **投顾行为边界**：禁止向用户汇报现金枯竭/仓位集中/调仓犹豫等操作层问题；仅汇报日盈亏/累计收益/持仓变动/止损止盈触发

## 报告模板防覆盖规范（2026-07-13 确立）
> 背景：当日早报被 wx_morning_report.py 的 stdout 旧格式悄悄顶替，根因=模板文件未加锁、脚本 stdout 可被误推。
- **双模板权威源**（唯一结构蓝本，禁回退历史废弃版）：
  - 早报 = `.workbuddy/templates/morning_report_template.md`（vFinal，8段）
  - 晚报 = `.workbuddy/templates/evening_report_template.md`（vFinal-E v2，10段，收盘复盘视角；融合7/10晚报实战优点+周末优化）
- **防覆盖三铁律**：
  1. 自动化 prompt 仅可**引用**模板文件路径，不可内联替代；模板结构变更须先经用户确认
  2. **禁止直接推送脚本原始 stdout** 到飞书群（wx_morning_report.py 的 --collect-only 仅用于采集，须经模板重组为飞书文档+结构化卡片）
  3. 晚报 10 段结构不可删减（🩺风险复盘/信号验证/大盘收盘/板块复盘[含吻合率]/持仓诊断[RSI+段永平]/止损检查/明日预案/策略优化/资金面/研报）；早报 8 段；颜色规则锁定 🔴强/🟢弱/🟡中性（A股红涨绿跌，禁反转）；鱼盆 STALE 必标注
- **群卡片缺一不可清单**（早报/晚报同）：风险 + 信号权重 + 板块排名 + 具体选股(代码+价位+整手+止损) + 合规约束 + 飞书链接
- **废弃版本清单**（永不复用）：7/5 前旧早报格式、wx_morning_report.py 的 `📊 微信读书【早报】` stdout 标题格式、PAUSED 的 `1780651521899`/`1782002193754`/`1782002856945` 旧卡片模板

## 身份与数据隔离
| 标识 | 角色 | 职责 | 渠道 |
|------|------|------|------|
| 📈投顾操盘 | 模拟炒股AI操盘手 | 选股/买卖/复盘 | 飞书群 |
| 📊炒股助理 | 用户投资助理 | 盘前/收盘/持仓/财报 | 飞书群 |
| 🇺🇸美股监控 | 美股监控 | 盘前/盘中/收盘 | 飞书群 |
- 投顾数据：`.workbuddy/scripts/sim_trade.py` `.workbuddy/scripts/sim_watchlist.py` | 持仓 `data/simulation/portfolio.json` | 策略 `.workbuddy/data/simulation/STRATEGY.md`
- 助理数据：`.workbuddy/data/user/portfolio.json`

## 模型映射（2026-06-06）
高频低复杂→Deepseek-V4-Flash(7) | 深度推理→Kimi-K2.6(6) | 结构化报告→GLM-5.0-Turbo(3) | 综合深度→GLM-5.1(3) | 分析思考→Hy3(4) | 美股→Deepseek-V4-Pro(2)

## 模拟炒股系统
- 启动 2026-06-05 | 资金 ¥30,000 | 禁创业板(300/301)/科创板(688/689)/北交所/ST | 同时≤3只
- 策略 v2.1：ATR动态止损(优先级链)/分级止盈(15/25/35%)/策略库L001-L003/跨盘风控(科技链≤40%)/再平衡(健康分0-100)/月尾冲刺(资金利用率≥85%,持有2-5天)
- 止损规则：L001单行业≤60% | L002现金≥15% | L003板块连阴不新开

## 关键系统与路径
- **Marvis桥接**：`~/workbuddy_marvis_bridge/`，调度=WorkBuddy(脑)/执行=Marvis(手脚)；Monitor automation-1781023179949(每3分钟)
- **QTS v2.0**：13容器全栈(策略8000/执行8001/ai-scheduler8002+PG/Redis/QuestDB/RabbitMQ+监控)；BBR(COMBO=VWM0.6+BBR0.4,+11.27%)、ADX/DMI(+4.76%)
- **实盘信号管线**：`.workbuddy/scripts/realtime_pipeline.sh`（持仓→腾讯行情→VWM/BBR/COMBO信号→飞书推送）；自动化 automation-1781778427910(交易日9/10/11/13/14:30)
- **知识库**：`scripts/pdf_extractor.py`+`knowledge_base.py`；向量库 `~/.workbuddy/cache/knowledge_vectors/`
- **专家系统**：7位已注册 `~/.workbuddy/plugins/marketplaces/my-experts/plugins/`
- **宏观数据**：`macro_data.py`(AKShare 8项)+`market_data.py`；`automation_health.py`(32自动化健康检查)
- **早报系统 v3.0**（2026-07-13 三层拆分）：`claw/feeds/wx_collector.py`(采集,811L) → `claw/feeds/wx_assembler.py`(组装,510L) → `claw/feeds/wx_publisher.py`(推送,58L)；入口 `claw/cli/wx_morning_report.py`(61L 薄壳)。原旧文件 `/tmp/wx_collector_raw.py` 已删除。

## 已知技术债 / 维护须知
- ✅ `schedule_utils.py`、`notify_center.py` 已于 **2026-07-12 17:28 创建就位**（此前 v6 引用的技术债已修复）；Dependabot 日清等维护任务可直接使用，无需再走 `automation_health.py` + `push_feishu.sh` 替代路径
- ⚠️ `fetch_holdings_quotes.py` 在「综合持仓监控」自动化 PHASE 1 Step3 被引用但**项目内不存在**（2026-07-13 发现）；当前由 `curl qt.gtimg.cn/q=sh/sz+代码` + iconv GBK→UTF-8 解析替代获取个股实时行情，功能等效。建议重建该脚本或改 preamble 固定指向 gtimg 替代方案。
- ⚠️ `user/portfolio.json` 的 `current_price` 字段**为空**（仅存成本），止损检查/持仓诊断须实时拉 `qt.gtimg.cn` 现价，不能依赖 JSON 字段（2026-07-13 止损自动检查发现）。
- ✅ `scripts/calc_rsi.py` 已建（2026-07-13）：腾讯 ifzq 优先 + 新浪回退，Wilder RSI(14)，供早报/晚报持仓段调用。
- ⚠️ 自动化健康检查对月度/周度任务误报 stale（如「每月深度审计」264h 判 critical），属阈值过严，非真实失败，勿据此自动 PAUSED
- ⚠️ **三 GitHub 仓库 main 均无 branch protection**：guandada123/QuantTradingSystem、MarvisBridge、StockInsight（2026-07-12 核验）。「Dependabot 日清」自动合并在 CI 红灯时不会被审计拦截，存在将未验证依赖变更推入 main 的风险；合并前需人工确认或先启用保护

## AI 协作规范
七条升级原则见 SOUL.md（项目式/资产化/多任务并行/主动触发/结构化输入/持续理解用户/Ask→Plan→Craft）。

## 日志保留规则
- 7日内完整 | 7-30日蒸馏简洁版 | >30日蒸馏入MEMORY.md后删除
- 蒸馏原则：只提取架构决策/配置变更/用户规则/技术债/重要阈值

## 技术约束
- **westock CLI** 要求股票代码带 `sh`/`sz` 前缀（如 `sh600522`）；裸代码（如 `600522`）会报 `MKT_ERROR`
- 脚本 `earnings_calendar.py` 中的 `_code_with_prefix()` 自动转换裸代码 → 前缀格式

## 参考文档
- `docs/ai-collaboration-upgrade.md` | `docs/automation-inventory.md`(每月1日梳理) | `data/simulation/STRATEGY.md`
