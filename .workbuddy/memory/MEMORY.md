# 项目记忆

## 核心规则（不可违反）
- 渠道分工：投资类自动化→飞书群 oc_9ee5303497f5e0e71666b610d6bdc346（直接推，免审）；**系统维护类（记忆/清理/健康检查）不推送**
- 推送前缀：投资类必带 📈投顾操盘 / 📊炒股助理 / 🇺🇸美股监控
- 数据删除铁律：删文件/目录前必须找用户确认；移动/删除须「先复制→验证→再删源」
- 🔴数据文件改动铁律（2026-07-17 用户纠偏确立）：`data/`下任何数据文件（portfolio.json / user/portfolio.json / daily_snapshot / 信号JSON 等）的**字段增删改，必须先跟用户确认再动**，严禁 AI 自行判定"对错"后擅自修改。用户已授权的配置值（如模拟盘 `initial_capital=50000` 为 2026-07-14 放宽授权）视为权威，不可推翻重判。唯一例外：实时行情价刷新（current_price 等）属"同步真实市价"非"改决策数据"，可直拉更新但须标注来源与更新时间。
- 飞书推送卡片化规范（2026-07-17 确立+全量迁移）：所有自动化推送**统一走 `push_card.py`**（interactive 卡片，语义配色+分区块+可选表格/按钮），**禁止** `--text` 纯文本降级（丢格式）。🔧 lark-cli 1.0.68 **无 `--card` flag**，发卡片=`--content '<card_json>' --msg-type interactive`。push_card 自带 429 退避+markdown 兜底+30KB 检查。**迁移架构**：`push_feishu.sh`/`notify_center.py`(v2) 内部已委托 `push_card.py`，故调这两者的自动化自动升级卡片（无需逐个改 prompt）；`notify_center` 新增 `--level` 显式配色 + 按 event-type 关键词推断（失败/告警/止损→alert，警告/降级/滞后→warning，成功/完成→success，兜底 info）。仍直发 `lark-cli` 的仅飞书通道自检(需真实 message_id 验连通)。
- 推送验证纪律（2026-07-17 确立）：脚本改动验收**一律先用 `--dry-run` 纯本地验证**（不上传Drive/不发群，零副作用）；确需验推送链路才用 `--no-upload`（仅发文字、不污染Drive），且每改动至多1次，禁止群内反复发测试消息。飞书机器人消息不支持API撤回，发出即留痕。
- 成本追踪链路（2026-07-17 核实）：`scripts/cost_tracker.py` 是数据层（子命令 `daily|monthly|top|estimate_today|log_estimate|log|log_cache|cache`，**无 `summary`/`daily-summary`**）；`scripts/cost_monitor.py` 是报告层（有 `daily|monthly|summary|dashboard` 子命令），被 `cost_dashboard_feishu.py` 依赖（第203行跑 `cost_monitor.py daily`），**非废弃、可引用**。成本监控自动化=automation-1782002819199（v9，6h槽位锁+单小时阈值¥20）。注意：`cost_dashboard_feishu.py` 当前无 ACTIVE 自动化调用（看板链路暂为死链）。

## 三系统边界（数据隔离，互不混淆）
| 标识 | 角色 | 职责 | 渠道 |
|------|------|------|------|
| 📈投顾操盘 | 模拟盘AI全权操盘手 | 选股/买卖/复盘(¥50,000自主) | 飞书群 |
| 📊炒股助理 | 用户实盘助理 | 主动选股+持仓监控/盘前收盘 | 飞书群 |
| 🇺🇸美股监控 | 美股监控 | 盘前/盘中/收盘 | 飞书群 |
- 投顾自主决策铁律(2026-07-14)：模拟盘所有买卖/选股/复盘 AI 全权，直接推飞书；不逐次汇报过程，只给最终结果
- 投顾行为边界：禁汇报现金枯竭/仓位集中/调仓犹豫等操作层问题；仅报日盈亏/累计/持仓变动/止损止盈
- 助理数据：.workbuddy/data/user/portfolio.json（国金单账户，广发已清仓转国金）；投顾数据：data/simulation/portfolio.json
- 🔴持仓同步铁律(2026-07-15 用户纠偏)：用户发成交/持仓截图→第一步 diff portfolio.json 与实际，不一致先更新再分析，禁"先分析旧数据"

## 报告模板防覆盖规范（2026-07-13 锁定，禁回退）
- 早报=.workbuddy/templates/morning_report_template.md(vFinal,8段) | 晚报=.workbuddy/templates/evening_report_template.md(vFinal-E v2,11段) | 周报=自治生成（Part A+B+结论+免责声明）
- 推送统一入口：早报→`scripts/push_morning_report.py`、晚报→`scripts/push_evening_report.py`、周报→`scripts/push_weekly_report.py`；三者均自治建飞书 docx + 解析 md 为卡片 sections + 按钮「📄完整报告」指向真实 url，**禁止 prompt 手写 `lark-cli`/`push_card --section` 占位符**。
- 三铁律：①prompt仅引用模板路径，不可内联替代，结构变更先确认 ②禁直接推脚本stdout(须模板重组为飞书文档+卡片) ③晚报/周报等长报告必须拆成「完整 md 文件」+「卡片脚本」，md 是权威源；颜色🔴强/🟢弱/🟡中性(A股红涨绿跌禁反转)，鱼盆STALE必标
- 群卡片必备：风险+信号权重+板块排名+具体选股(代码+价位+整手+止损)+合规约束+飞书链接
- 废弃永不复用：7/5前旧早报、wx_morning_report.py的`📊微信读书【早报】`stdout格式、1780651521899公众号早报安全版(2026-07-14下线，由主早报付费RSS替代)

## 助理全主板选股规则（2026-07-14，B方案）
- 池：QTS daily_quote→主板(600/601/603/605/000/001/002/003)+近20日均额≥3亿+非ST/退→1076只 mainboard_scan_pool.json
- 主信号：COMBO=VWM(0.6)+BBR(0.4)，ADX≥25过滤，RSI(14)>80拦截，COMBO≥0.2买；风控闸门：系统性调整日/双源背离/COMBO=0(死水期)→不推
- 仓位纪律：单只≤¥5000(总¥15000的1/3)，止损-8%，周期3-10天，格式=代码+价位+整手+止损+周期+风险
- 脚本：scan_mainboard_full.py(容器跑)；依赖 mainboard_scan_pool.json+astock_code_name.json(ST映射)；现状0候选(死水期)

## 模拟炒股系统
- 启动2026-06-05 | 总资¥50,000(2026-07-14由3万放宽) | 禁创业板(300/301)/科创板(688/689)/北交所/ST | 持仓数不限 | 现金保留限制已取消(可全仓)
- 体系：COMBO=VWM(0.6)+BBR(0.4)主信号，ADX/DMI(≥25)趋势过滤；STRATEGY.md v2.0
- 代码层(sim_trade.py)：INITIAL_CAPITAL=50000/MAX_POSITION_PCT=0.50/MAX_SECTOR_PCT=0.60/STOP_LOSS_PCT=0.08
- 止损：单只≤50% |单行业≤60% |亏损≥8%无条件止损 | COMBO翻-1减仓离场

## 盘中监控双链架构（2026-07-14 方案B）
- 📊助理实盘监控(automation-1784039316540)：:00整点，只监测不交易；采集→验证→持仓诊断(三级沉默)→北向预警
- 📈投顾策略执行(automation-1784039339114)：:10触发，全权执行；采集→信号判定(止损/止盈/COMBO)→执行(sim_trade.py)→推送
- 资产隔离：助理→user/portfolio.json，投顾→simulation/portfolio.json；依赖 fetch_holdings_quotes.py(实时行情)+fetch_northbound_flow.py(北向,缺失降级)
- 旧链待清理：1783310235388(综合持仓监控)，验证新链稳定后PAUSE

## 关键系统与路径
- Marvis桥接：~/workbuddy_marvis_bridge/，调度=WB(脑)/执行=Marvis(手脚)，Monitor automation-1781023179949(每3分钟)
- QTS v2.0：13容器全栈(策略8000/执行8001/ai-scheduler8002+PG/Redis/QuestDB/RabbitMQ)；BBR(+11.27%)/ADX-DMI(+4.76%)
- 实盘信号管线：.workbuddy/scripts/realtime_pipeline.sh；自动化 automation-1781778427910(交易日9/10/11/13/14:30)
- 知识库：scripts/pdf_extractor.py+knowledge_base.py；向量库 ~/.workbuddy/cache/knowledge_vectors/
- 宏观数据：macro_data.py(AKShare 8项)+market_data.py；automation_health.py(32自动化健康检查)
- 早报系统v3.0(2026-07-13三层)：claw/feeds/wx_collector.py→wx_assembler.py→wx_publisher.py；入口 claw/cli/wx_morning_report.py

## 防回退锁定（2026-07-14，禁未经确认修改）
- 鱼盆自动化1783472286775模型必须 deepseek-v4-flash(积分轨+视觉)，禁改glm-5.0-turbo
- 鱼盆OCR：必须 LLM Read 看图→写JSON(v4)，禁 transcribe_yupen_image.py(tesseract)
- 鱼盆RSS fetch_yupen_rss.py：_get_article_detail 重试8次；「优先取最新+评分验证」(每号只取最新，评分仅验证含表)
- 早报推送唯一性：仅 automation-1782741941693 推早报；鱼盆提取自动化只更 output/yupen/ 不推
- 鱼盆数据新鲜度：猫笔叨每日发，表日期滞后文章1天，freshness=stale多为滞后非bug
- **鱼盆文件名约定**：raw/PNG 用「抓取日期」（如 `yupen_2026-07-17_raw.json`），结构化 JSON 用「表头数据日期」（如 `yupen_2026-07-16_sector_rotation.json`）。两者常差1天，切勿把 raw 文件名日期当成数据日期，也勿因 `pending_ocr` 状态误判 OCR 卡住；OCR 真实产物在 `data_date` 文件。
- **鱼盆补抓命令**：`python3 .workbuddy/scripts/fetch_yupen_rss.py --article-id <URL> --date 2026-07-17`（2026-07-17 新增 `--article-id` 参数，用于漏抓文章的补抓；补抓后仍需 LLM 视觉 OCR 生成 `yupen_<data_date>_*.json`）。

## ✅ 已裁：系统维护类推送冲突（2026-07-17 用户拍板 + 已落地）
- **决策（用户原话）**：「维护类噪音 5 条，有异常再推送，每日重启提醒删除」。
- **落地结果（全部完成）**：
  1. 删除 `1783784056233` 每日重启提醒（soft-delete，`deleted_at` 已置，DB 不再 SELECT 到）。
  2. 7 个 ACTIVE 维护类自动化 prompt 顶层统一加「🚨 系统维护类铁律（禁改）：默认不推送飞书群；仅 ⚠️/🔴 异常/失败/主动修复才推送，正常一律 [SILENT]」：
     - `1780769419635` 记忆维护 / `1782002819199` 成本监控 / `1782394153045` 数据备份 / `1781663740184` Quant管线（首轮已加）
     - `1781780654327` 综合健康巡检 / `1782002834355` 工程质量 / `1784084428353` 飞书通道自检（次轮补齐）
  3. push_to_wechat 维持现状（部分=1 但被 top-rule 压制，与「运行不推、异常才推」一致），不再改配置位。
- **易混淆（非维护类，勿动）**：`1782035436209` 风险巡检 / `1783742027380` 财报日历 实为投资/项目类，应保留推送。
- **PAUSED+push=1 遗留（恢复前需裁，未动）**：`1780759171289` 全局记忆梳理 / `178079008345` 每月深度审计 / `1781220962599` 持仓自动刷新。

## automation_preamble $SCRIPTS 纪律（2026-07-14）
- $SCRIPTS=.workbuddy/scripts(preamble:10)，设计决策不可改 scripts/
- 命名碰撞：scripts/与.workbuddy/scripts/同名时$SCRIPTS/xxx静默取后者(可能废弃)；主脚本用 cd $CLAW && python3 scripts/xxx.py 显式路径
- 双副本审计 automation-1783671485008(每日21:00)扫描同名碰撞

## 已知技术债 / 维护须知
- ✅ schedule_utils.py/notify_center.py(v2 委托 push_card) 已就位(2026-07-12起)，维护任务可直接用
- ✅ fetch_holdings_quotes.py 位于 .workbuddy/scripts/(2026-07-14重建稳定)；✅ scripts/calc_rsi.py(2026-07-13,腾讯ifzq优先+新浪回退,Wilder RSI14)
- ⚠️ user/portfolio.json 的 current_price 为空(仅存成本)，止损/诊断须实时拉 qt.gtimg.cn 现价
- ⚠️ 自动化健康检查对月/周度任务误报stale(阈值过严)，非真实失败，勿据此自动PAUSED
- ⚠️ 三GitHub仓库main无branch protection(guandada123/QuantTradingSystem/MarvisBridge/StockInsight)，Dependabot日清合并前需人工确认

## 公众号信号源双轨（2026-07-17）
- 付费RSS(wechatrss.waytomaster.com)：18号，主源；本地WeChat API(localhost:5001,Colima)：15号补充
- 合并：wx_collector.py:_fetch_today_via_api()拉双源，_HAS_LOCAL_FEEDS降级
- article_signals.json：23信源/133信号；新号先注册本地API即自动纳入，无需改付费RSS

## 存储架构：致态SSD外置盘（2026-07-17）
- Mac mini M4→雷雳→扩展坞→致态(2TB,APFS,/Volumes/ZHITAI/)
- 符号链接(禁删/移)：~/.colima/_lima/_disks/colima/datadisk→致态；~/.colima/_lima/colima/disk→致态；~/.workbuddy→致态；~/WorkBuddy_OLD~/Movies~/gbrain~/workbuddy_marvis_bridge等→致态
- Docker引擎：Colima(非Docker Desktop)，docker context=colima
- 经验教训：①动手前先审计环境(曾误判Docker Desktop实际Colima) ②不能迁正在跑程序的数据(~/.workbuddy靠Marvis迁) ③删前先复制→验证→再删源(APFS rm后df延迟，空间标记purgeable非立即回收) ④ES 8.x默认安全认证需显式user+password ⑤.app勿符号链接迁移(破签名) ⑥westock CLI代码需sh/sz前缀

## 模型映射（2026-06-06）
高频低复杂→Deepseek-V4-Flash | 深度推理→Kimi-K2.6 | 结构化报告→GLM-5.0-Turbo | 综合深度→GLM-5.1 | 分析思考→Hy3 | 美股→Deepseek-V4-Pro

## 协作 / 日志 / 约束
- AI协作七原则见 SOUL.md（项目式/资产化/多任务并行/主动触发/结构化输入/持续理解用户/Ask→Plan→Craft）
- 日志保留：7日内完整 | 7-30日蒸馏 | ＞30日蒸馏入MEMORY.md后删
- 技术约束：westock CLI 代码带 sh/sz 前缀；earnings_calendar.py 的 _code_with_prefix() 自动转换
- 参考文档：docs/ai-collaboration-upgrade.md | docs/automation-inventory.md(每月1日) | data/simulation/STRATEGY.md
