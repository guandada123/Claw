# 项目记忆（精炼版）

> 架构：本文件=FACT层(铁律/技术决策)，变更→原条目加 `→superseded by <日期>` 可回溯，禁平行堆重复。SCHEMA.md=L5｜INTENT.md=L6｜CHRONICLE.md=编年史｜日日志=RAW+SUMMARY(首行记原始指令)。检索：先Grep日日志标题+MEMORY/INTENT/SCHEMA关键词再Read；L3仅具体数据才Read(≤3文件)；审计→memory-consistency-audit；蒸馏：日日志>30天→蒸馏进对应层→源移`.backups/`；单文件>15KB优先蒸馏；豁免🔴铁律+演化链段。

## 🔴 不可违反铁律
- 渠道：投资类→飞书群 oc_9ee5303497f5e0e71666b610d6bdc346(免审直推)；维护类默认不推仅⚠️/🔴异常推；前缀📈投顾操盘/📊炒股助理/🇺🇸美股监控
- 删/移文件须「先复制→验证→再删源」+ 删前用户确认
- 数据文件改动：data/字段增删改须先确认；总本金¥50,000权威(=¥30,000+加仓¥20,000@07-14，记config.capital_additions)，sim_trade.py 用 get_effective_capital() 勿硬编码；例外：实时价刷新可直拉(标来源+时间)
- 飞书推送卡片化：统一 push_card.py(interactive)禁--text降级；lark-cli≥1.0.76；notify_center已委托push_card；改脚本先--dry-run
- 成本：cost_tracker.py(数据层)/cost_monitor.py(报告层)被cost_dashboard_feishu依赖；监控自动化=1782002819199
- 自动化调LLM必走本地代理:9999：provider≠deepseek/catrouter(如local_proxy)+base_url="http://127.0.0.1:9999/v1"；preamble内置ensure_proxy(:9999 DOWN自动load两plist自愈)
- 实时价铁律(07-29)：盘中/监控/信号取价**必须走腾讯 qt.gtimg.cn**，Wind仅降级兜底；wind_quote.py已改「腾讯优先→Wind降级」DO NOT REVERT；新取价脚本禁直接wind优先

### 股价与推荐防错铁律（08-07·根因=8/6早报选股价数量级错误）：报告/选股/持仓中所有股价与买区不允许出错
- ①选股段价位必须由 advisor_rules.py check-entry --code X 脚本取价(gtimg实时+MA20+52周)，禁AI手填；②scripts/price_sanity.py 三闸门(G1实时偏差>30%/G2 52周区间/G3 MA20偏离>60%)任一失败→SANITY_FAIL+改用可信价；支持美股(--market us走Yahoo,G3跳过)；③check_entry外部价必经sanity，失败→blocked=True不输出离谱买区；④早报1782741941693+晚报1782741945710/1782817769722 prompt已嵌防错，price_sanity.ok=false标的标「🚫价格校验失败，已拦截」
- ⑤盘中监控全覆盖：fetch_holdings_quotes.py 加 `_apply_sanity()`(实盘/模拟盘每只current_price必经sanity，失败标price_sanity_fail+回填reliable_current_price，顶层sanity_failed计数)；6个盘中自动化(实盘1784039316540+投顾5策略1784506600526/634/523/665/706) prompt加「现价防错铁律」
- ⑥美股监控1780615006148：AAPL/TSLA/NVDA收盘价必过 price_sanity --market us，FAIL→标「🚫价格校验失败，已隔离」+重搜；禁手填美股价
- ⑦跨盘监控 cross_portfolio_monitor.py 二次校验：_sanity_guard()对portfolio.json current_price做sanity，失败→隔离错误价(不计入总市值)+sanity_failed计数
- ⑧工程质量周报1782002834355 PHASE2.5 加 tests/test_price_sanity.py(12用例)+跨盘测试回归门禁
- ⑨sim_trade.py交易执行校验(最高优先级)：_sanity_check_price()在cmd_update_price/cmd_update_all_prices(错误价拒绝写入保留旧价)+cmd_buy/cmd_sell(错误成交价拒绝交易)四入口拦截；auto_check_all_positions判定前硬断言(失败→continue跳过不误卖+ERROR日志)
- ⑩drill_assistant_monitor.py消费sanity：PHASE3分级前过滤price_sanity_fail=true→隔离告警+不参与止损盈亏判定+市值汇总排除。DO NOT REVERT：禁回退"AI直接写买区/现价"旧逻辑

### 自动化排程与 rrule 铁律
- 🔴🔴 单RRULE禁多BYHOUR(只触发首个匹配小时，余槽静默丢无日志)→多时段拆多条单BYHOUR
- 🔴🔴 创建/修改rrule强制自检gate(08-07二次踩坑固化)：凡automation_update创建/更新或直写db automations表且rrule含BYHOUR多值→必load automation-rrule-safety-check skill走Gate1-4(禁多BYHOUR拆多条/备份/创建后验证创建数+单BYHOUR+告知用户当天剩余时段能否排上/回溯查昨天同坑一并修)。信任红线，复发即严重失职
- 已拆：助理实盘1784039316540=9 + 1785123941471/596/709/786(10/11/13/14)；信号溯源1780964240621=5 + 1785284629106(15:00)；原隐患1783310235388已删
- Sidecar守护唯一执行方=com.workbuddy.memwatch(阈值RSS_RESTART_MB=10000MB，08-06由6000上调)；禁依赖看门狗兜底关键自动化

### 统一巡检中枢(08-06接管)
- unified_ops_center.py(宿主QTS自动化1785982929477每小时)；复用专项脚本(automation_health/self_heal/qts_pmf_guard/disk/feishu_channel)不重写；Runbook自愈白名单=memwatch_threshold_bump+docker_restart_container；审计unified_self_heal_log.json
- 被接管已PAUSED：综合健康1781780654327/跨项目1785918166172/多项目1785928720152；保留独立：watchdog失败扫表1785506975961、飞书自检1784084428353；飞书告知结构化卡，全绿SILENT

## 三系统边界（数据隔离）
- 📈投顾→.workbuddy/data/simulation/portfolio.json(全权只给结果)｜📊助理→.workbuddy/data/user/portfolio.json(国金)｜🇺🇸美股；持仓同步(07-15)：用户发持仓截图→先diff再分析
- 报告模板(07-13锁)：早/晚/周报走push_*_report.py自建docx+卡片+「📄完整报告」；禁prompt内联/直推stdout；A股红涨绿跌禁反转

## 模拟炒股+选股
- 总资¥50,000(07-14)，禁科创/北交/ST(创业板300/301已于07-29放开)；sim_trade.py: RESTRICTED_PREFIXES=["688","689","8","4"]+ST；MAX_POS=0.50/MAX_SECTOR=0.60/STOP_LOSS=0.08；创业板CYB_STOP_LOSS_PCT=0.15
- 分级止盈双模(08-04)：冲刺期(每月20号后/6月14号后)=5/10/15%清仓；正常期=15/25/35%清仓；运行时判定，模式切换自动重置take_profit_level；投顾prompt须同步双模口径(禁写死)
- 助理主板选股：mainboard_scan_pool.json(COMBO=VWM0.6+BBR0.4,ADX≥25,RSI>80拦截)，单只≤¥5000止损-8%
- 选股池增量补全(07-29)：refill_scan_pool.py枚举允许板块腾讯qt增量拉新，过滤退/PT/零成交/ST；自动化1785309382755@08:30
- 多智能体辩论(07-29)：run_debate.py→src/claw/debate/(7专家三环)；接入09:10策略(1784506600526)+15:50复盘(1782817769722)
- 持仓数不限制(08-05)：保留单只≤50%/行业≤60%/同日仅开1仓/留现≥15%风控
- 策略风控体系(08-05)：market_gate大盘门控/correlation_monitor/position_coeff仓位系数/止盈市场状态驱动/risk_note机制(中国建筑601668跌破4.40减半)/C2风格平衡；7处投顾prompt全部接入｜绩效面板(07-29)performance_dashboard.py/信号追溯trace_signal.py/绩效周报1785336744681@周日16:00

## 盘中监控双链（07-20方案B）
- 助理实盘1784039316540(:00)｜投顾策略5×DAILY(:10)：1784506600526/1784506634174/1784506653523/1784506653665/1784506653706
- 投顾推送统一(07-24)：智能选股1780738597945+午间选股1782188906018用 push_feishu.sh "$TITLE" "$CONTENT" 封装禁--json-stdin；model=deepseek-v4-flash；PORTFOLIO/EXP_DIR须指simulation/｜科技红利聚焦扫描仅保留 automation-1784821193894(依赖fetch_holdings_quotes+fetch_northbound_flow缺失降级)；sim读positions优先(holdings=死副本勿依赖)

## 防回退锁定（07-14，禁未经确认改）
- 鱼盆1783472286775=deepseek-v4-flash(禁glm-5.0-turbo)；OCR必LLM Read→JSON(v4)禁tesseract；早报唯一推送1782741941693
- 鱼盆文件名：raw=抓取日期，结构化=表头数据日期，常差1天勿混淆；补抓 fetch_yupen_rss.py --article-id <URL> --date <日>｜鱼盆双源(07-21)：yupen_primary_*=Wind主源(07-23起Wind+雅虎)，yupen_*=RSS OCR兜底；read_yupen_data.py自动merge Wind优先+RSS补缺；v5.1双推已根治：rss_updated==False才推未推进

## 公众号抓取链（07-31根因修复）
- 🔴 付费RSS wechatrss.waytomaster.com/api/article 有服务端防风控限流→请求间隔须≥1.5s｜抓取失败绝不落盘空壳(空壳落盘该文永不重抓、正文永久丢失)→用 fetch_article_content_ex() 返回(content,err)区分限流；禁 except:return "" 吞错
- 回填 backfill_wx_content.py(幂等)+自动化1785506323216每2h跑40篇，remaining==0才推；processed主键=file_key()=md5(filename)[:12]
- 🔴 公众号双轨状态(08-06)：付费云停更(07-29起)；本地 wechat-download-api 登录有效(isExpired=false)但**轮询器卡07-20**。决策：sync_wx_articles.py 暂保持 --source cloud，待轮询器恢复过07-29再切local

## 运维/技术债
- 已裁维护推送(07-17)：7维护自动化「默认不推仅异常推」；保留1782035436209/1783742027380
- 🔴 发布类授权升级(08-06)：发布前必 gh pr diff 全量审计+git fetch 比对head+确认mergeable且合并后main CI变绿；已合并分支被保护规则拒删→保留孤儿分支标注MERGED待清理；实盘下单/对外发布仍归用户
- $SCRIPTS=.workbuddy/scripts(preamble:10)；主脚本 cd $CLAW && python3 scripts/xxx.py；westock CLI代码带sh/sz前缀
- user/portfolio.json current_price空(仅成本)，诊断实时拉qt.gtimg.cn；健康检查对月/周度误报stale勿自动PAUSED
- 存储=致态SSD(/Volumes/ZHITAI)+Colima，~/.workbuddy等符号链接禁删/移
- 断链澄清(07-20误报07-29更正)：1781778427910已DELETED；1782741941693引calc_rsi.py存在无断链；审计须排除DELETED状态
- proxy看门狗(07-26)：com.workbuddy.proxy-watchdog(StartInterval=30)；launchd后台agent须managed python3直跑.py｜自动化运维排障(08-04)：①查automation_runs表必须用带 automation- 前缀ID；②status恒PENDING_REVIEW属默认记录态非失败；③验真运行看last_run_at/created_at；④新建定时自动化须走 automation API(勿直插DB绕过调度注册)，建后须验证next_run_at+实测触发；盘中监控明确归Claw托管(既有可用)勿迁QTS；git用git -C <abs>
- 备份清理核实(08-04)：output/.backups/daily/ 15个tar.gz是14天滚动正常(156M预期)；禁自动prune
- Claw CI全绿(08-04)：ci.yml已删；ruff锁0.15.17；🔐DeepSeek key轮换完成(活跃sk-faaf…2796)

## QTS日线数据架构（07-23）
- 本地回填主源 qts_daily_backfill.py(腾讯K线32线程→upsert 127.0.0.1:15432 daily_quote，自动化1784811393302@16:30)；容器daily_data_refresh仅增量；daily_quote加updated_at列

## 📐 记忆维护规则（固化）
- 密度=结论+依据+例外，日志首行记原始指令；查询分类：recall→L1-L3｜compress→蒸馏(>30天→.backups/)｜audit→memory-consistency-audit｜learn→self-improving-agent/SCHEMA
