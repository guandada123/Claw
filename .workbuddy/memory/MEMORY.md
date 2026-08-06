# 项目记忆（精炼版 ≤3k）

> **记忆架构（对齐 Hy-Memory 演化链，07-29 固化）**
> - 本文件=`FACT`层（铁律/技术决策），事实变更→原条目加 `→superseded by <日期>` 保留可回溯，禁平行堆重复
> - `SCHEMA.md`=L5行为规律｜`INTENT.md`=L6前瞻意图｜`CHRONICLE.md`=编年史｜`YYYY-MM-DD.md`=RAW+SUMMARY（首行记原始指令）
> - 蒸馏规则：日日志>30天→蒸馏进对应层→源移`.backups/`（不物理删）；单文件>15KB优先蒸馏
> - **行数硬限（07-29锁80不放宽）**：触限走轻量降级（①不用→.backups/ ②偶尔用→压单行+详情进CHRONICLE ③常用最旧→挪SCHEMA/INTENT），仅标⚠️待蒸馏不自动删；豁免：`🔴铁律`+`演化链`段锁死不降级

## 🔴 不可违反铁律
- 渠道：投资类→飞书群 oc_9ee5303497f5e0e71666b610d6bdc346（免审直推）；维护类默认不推仅⚠️/🔴异常推；前缀：📈投顾操盘/📊炒股助理/🇺🇸美股监控
- 删/移文件须「先复制→验证→再删源」+ 删前用户确认
- 🔴数据文件改动：data/字段增删改须先确认；总本金¥50,000权威不可推翻(=¥30,000+加仓¥20,000@07-14，记于config.capital_additions)，sim_trade.py 用 get_effective_capital() 读勿硬编码；例外：实时价(current_price)刷新可直拉(标来源+时间)
- 飞书推送卡片化：统一 push_card.py(interactive)禁--text降级；lark-cli≥1.0.76；notify_center已委托push_card；改脚本先--dry-run，验链路至多1次--no-upload
- 成本：cost_tracker.py(数据层)/cost_monitor.py(报告层)被cost_dashboard_feishu依赖；监控自动化=1782002819199
- 🔴自动化调LLM必走本地代理：preamble注入的DEEPSEEK_API_KEY错/积分轨→router直连deepseek必401。凡自动化用 router.call_llm，model_config 必设 provider≠deepseek/catrouter(如"local_proxy")+base_url="http://127.0.0.1:9999/v1"；→2026-07-31固化：preamble已内置ensure_proxy(:9999 DOWN自动launchctl load -w两个plist自愈)，无需各脚本单独探测，#74/#78已从业务层根治
- 🔴实时价优先级铁律(07-29→supersedes早期「Wind优先」)：盘中/监控/信号取实时价**必须走腾讯 qt.gtimg.cn**，Wind仅降级兜底；wind_quote.py 已改「腾讯优先→Wind降级」+DO NOT REVERT注释（Wind盘中滞后实测5.8%）；新增取价脚本禁直接wind优先
- 🔴Sidecar守护唯一执行方=com.workbuddy.memwatch(内存看门狗,阈值RSS_RESTART_MB=6000MB)：曾误判为Marvis(已关)。08-06 09:00 因WB树6076MB>6GB触发重启主进程→盘前3关键自动化(早报08:35/智能选股09:00/策略09:10)静默漏跑+重启后sessions.json=0。⚠️**盘前窗口(08:35-09:10)是memwatch高发区**，降低漏跑首选提高阈值至10000MB(待用户拍板)，禁日常依赖看门狗自愈兜底关键自动化
- 🛡️**统一巡检中枢(08-06接管)**：所有健康巡检整合为单一入口 `unified_ops_center.py`(宿主QTS自动化-1785982929477每小时)。复用现有专项脚本(automation_health/self_heal/qts_pmf_guard/disk/feishu_channel)，不重写。Runbook白名单自愈：memwatch_threshold_bump(提10000+reload) + docker_restart_container。审计日志 unified_self_heal_log.json。被接管已PAUSED：综合健康1781780654327/跨项目1785918166172/多项目1785928720152。保留独立：watchdog失败扫表1785506975961、飞书通道自检1784084428353。飞书告知结构化卡(原因/识别/解决/修复/优化/结论)，全绿SILENT。

## 三系统边界（数据隔离）
- 📈投顾→.workbuddy/data/simulation/portfolio.json（AI全权只给结果，非data/simulation/portfolio.json 07-22已删过时副本）｜📊助理→.workbuddy/data/user/portfolio.json(国金)｜🇺🇸美股；🔴持仓同步(07-15)：用户发持仓截图→先diff再分析
- 报告模板(07-13锁)：早/晚/周报走push_*_report.py自建docx+卡片+「📄完整报告」；禁prompt内联/直推stdout；A股红涨绿跌禁反转
- 🔴自动化排程铁律(07-27)：**单RRULE禁多BYHOUR**（只触发首个匹配小时，余槽静默丢无日志）→多时段拆多条单BYHOUR。已拆：助理实盘1784039316540=9 + 1785123941471/596/709/786(10/11/13/14)；信号溯源1780964240621=5 + 1785284629106(15:00)；原隐患1783310235388已删

## 模拟炒股+选股
- 总资¥50,000(07-14)，**禁科创/北交/ST**（创业板300/301已于07-29放开→supersedes 07-14禁创）；sim_trade.py: RESTRICTED_PREFIXES=["688","689","8","4"]+ST；MAX_POS=0.50/MAX_SECTOR=0.60/STOP_LOSS=0.08；创业板CYB_STOP_LOSS_PCT=0.15
- 🔄分级止盈双模(08-04落地·对齐 trading-dual-mode-seamless skill): 冲刺期(每月20号后/6月14号后)=5/10/15%清仓；正常期(其余)=15/25/35%清仓；`_is_sprint_period()`+`_get_take_profit_levels()`运行时判定，模式切换自动重置take_profit_level；投顾 prompt 角色行/调仓规则须同步双模口径(禁写死「冲刺期止盈5%」或「+30%强制线」)
- 助理主板选股：mainboard_scan_pool.json(COMBO=VWM0.6+BBR0.4,ADX≥25,RSI>80拦截)，单只≤¥5000止损-8%
- 🔄选股池增量补全(07-29)：refill_scan_pool.py(泛化版，旧refill_cyb_pool.py已删)枚举所有允许板块(深主000/001/002/003+沪主600/601/603/605+创业300/301)腾讯qt增量拉新，过滤退/PT/零成交/ST，写回不覆盖；自动化1785309382755@08:30
- 📈多智能体辩论(07-29)：run_debate.py→src/claw/debate/（7专家三环：stance→peer_review→convergence）；接入09:10策略(1784506600526)+15:50复盘(1782817769722)；测试18/18绿
- 🔄持仓数不限制(08-05)：智能选股1780738597945=v10.5/午间选股1782188906018=v6.3 取消≤3/≤5只（对齐STRATEGY"不限制"）；日报generate_daily_report.py取消≤3检查；validate_constraints.py max_positions 5→99安全上限；保留单只≤50%/行业≤60%/同日仅开1仓/留现≥15%风控
- 🛡️策略风控体系升级(08-05,全网方法论落地)：①market_gate.py大盘门控(上证<MA20且连续3日缩量→DEFENSE暂停新开仓) ②correlation_monitor.py持仓相关性(>0.7且≥3只→单只上限50%→40%) ③position_coeff.py仓位系数(量价×估值利差0.6-1.0) ④sim_trade.py止盈市场状态驱动(正常期+大盘强修复→第一档15→18%) ⑤STRATEGY.md risk_note机制(持仓position含risk_note优先执行,当前中国建筑601668跌破4.40减半) ⑥选股C2风格平衡(持仓全价值/红利→加1只科技≤15%)；7处投顾prompt全部接入
- 📊绩效面板(07-29)performance_dashboard.py｜🔍信号追溯(07-29)trace_signal.py｜📅绩效周报(07-29)自动化1785336744681@周日16:00

## 盘中监控双链（07-20方案B）
- 助理实盘1784039316540(:00监测)｜投顾策略5×DAILY(:10)：1784506600526/1784506634174/1784506653523/1784506653665/1784506653706（旧1784039339114已删）
- 投顾推送统一(07-24)：智能选股1780738597945(v10.2)+午间选股1782188906018(v6)用 **push_feishu.sh "$TITLE" "$CONTENT"** 封装禁--json-stdin；model=deepseek-v4-flash；PORTFOLIO/EXP_DIR 须指 simulation/（.workbuddy/data/portfolio.json 07-22已删勿引）
- 科技红利聚焦扫描：仅保留 automation-1784821193894；依赖 fetch_holdings_quotes.py+fetch_northbound_flow.py(缺失降级)；07-27修：sim读positions优先(权威源)，holdings仅实盘结构回退，sim的holdings=死副本勿依赖

## 防回退锁定（07-14，禁未经确认改）
- 鱼盆1783472286775=deepseek-v4-flash(禁glm-5.0-turbo)；OCR必LLM Read→JSON(v4)禁tesseract；早报唯一推送1782741941693
- 鱼盆文件名：raw=抓取日期，结构化=表头数据日期，常差1天勿混淆；补抓 fetch_yupen_rss.py --article-id <URL> --date <日>
- 鱼盆双源(07-21)：yupen_primary_*=Wind主源(07-23起Wind+雅虎)，yupen_*=RSS OCR兜底；read_yupen_data.py自动merge Wind优先+RSS补缺；路径历史混淆：早期yupen_2026-07-21_*.json误作primary存放属预期，RSS重抓会覆盖非primary数据；v5.1双推已根治(07-23)：软失败判定 rss_updated==False 才推未推进

## 公众号抓取链（07-31 根因修复）
- 🔴 付费RSS `wechatrss.waytomaster.com/api/article` **有服务端防风控限流**（旧注释「无限流」错，误导排查5轮）→ 请求间隔须≥1.5s（0.3s必触发）
- 🔴 铁律：**抓取失败绝不落盘空壳**（_existing_urls()按url去重，空壳落盘该文永不重抓、正文永久丢失，曾致92%空正文）→ 用 fetch_article_content_ex() 返回(content,err)区分限流；禁 except:return "" 吞错
- 回填 backfill_wx_content.py(幂等，只改content_text/len/backfill_time)+自动化1785506323216每2h跑40篇，remaining==0才推；processed主键=file_key()=md5(filename)[:12]（根治#38换行符bug，备份.bak-20260731）
- 🔴 公众号双轨状态(08-06)：付费云停更第8天(07-29起)；本地 wechat-download-api 登录已恢复(isExpired=false)但**轮询器卡 07-20**未重发现新文。wx_rss_local.py 发现层已修复(per-account `/api/rss/{fakeid}` 重登后404→改 `/api/rss/all` 聚合源含 content:encoded 全文，按[昵称]前缀过滤，正文直提免慢抓)。**决策：sync_wx_articles.py 暂保持 --source cloud（本地07-20比云端磁盘池07-29旧），待轮询器恢复过07-29再切 local**

## 运维/技术债
- 已裁维护推送(07-17)：7维护自动化顶层「默认不推仅异常推」；保留1782035436209/1783742027380
- $SCRIPTS=.workbuddy/scripts(preamble:10)；主脚本 cd $CLAW && python3 scripts/xxx.py 避同名碰撞；westock CLI代码带sh/sz前缀
- ⚠️user/portfolio.json current_price空(仅成本)，诊断实时拉qt.gtimg.cn；健康检查对月/周度误报stale勿自动PAUSED
- 存储=致态SSD(/Volumes/ZHITAI)+Colima，~/.workbuddy等符号链接禁删/移；公众号双轨=付费RSS+本地API(localhost:5001)
- ✅断链已澄清(07-20误报07-29更正)：1781778427910已DELETED死问题；1782741941693引绝对路径/Users/guan/WorkBuddy/Claw/scripts/calc_rsi.py（存在无断链）；审计prompt-LIKE须排除DELETED状态
- 🔧proxy看门狗(07-26)：com.workbuddy.proxy-watchdog(StartInterval=30, python3跑proxy_watchdog.py自动load回，防外部unload空窗)；launchd后台agent禁/bash脚本须managed python3直跑.py
- 🔧自动化运维排障(08-04)：①查 `automation_runs` 表**必须用带 `automation-` 前缀的 ID**（如 `automation-1785506975961`），裸 ID 必误报"无运行记录"（曾误判7/8管家自动化静默失败）；②该表全451行 status 恒为 `PENDING_REVIEW`（0条SUCCESS/FAILED），属默认记录态非失败，勿据此判静默失败；③验真运行看 `last_run_at`/`created_at` 时间戳；④**Claw 本地助手工作区禁托管新定时自动化 = WorkBuddy 平台级限制**（非自主铁律，08-06 实测 `automation_update create` 用 Claw cwd 仍硬报错"cannot host automations"；DB 内 107 个 Claw cwd 自动化全为 06-05~08-04 创建，守卫约 08-04 后上线，无本地开关可解除）→ 新建须用其他项目工作区宿主（如QTS），git 命令用 `git -C <abs>` 绝对路径不依赖 cwd
- 🔧备份清理缺口核实(08-04→supersedes早期「需建prune自动化」判断)：**不成立，不建**。output/.backups/daily/ 15个tar.gz(07-21~08-04)是每日备份脚本14天滚动清理正常结果(156M为预期)；`.bak-*` 全文0个；其余 `.backups`(memory288K/data8K)是记忆蒸馏归档(文件移入非复制)删了丢历史且已有🧹记忆体检(1780769419635)在跑，禁自动prune
- ✅Claw CI 全绿(08-04收口)：ci.yml已删(原委托休眠engineering-audit-kit@v2→跨仓红)；ruff锁0.15.17+ignore PLR0917禁新版误报；pre-commit EOF排除机器JSON；benchmark缺test_benchmark.py优雅跳过+Save baseline加存在守卫；safety禁--full-report(--output互斥)；gitleaks .gitleaks.toml按commit豁免dd5df15c；🔐DeepSeek key轮换已完成(08-05用户平台吊销sk-443b…e408，活跃键sk-faaf…2796不受影响，本地明文已清，历史豁免保留)

## QTS日线数据架构（07-23，详情CHRONICLE）
- 本地回填主源 qts_daily_backfill.py(腾讯K线32线程→upsert 127.0.0.1:15432 daily_quote，自动化1784811393302@16:30)；容器daily_data_refresh仅增量(tushare限频慢)；./strategy-service:/app挂载即时生效；daily_quote加updated_at列

---

## 📐 记忆维护规则（固化）
- 演化链：变更→原条目加→superseded，新条目写supersedes<旧>；旧条目保留不删（可回溯）；冲突新优先
- 分层职责：MEMORY=FACT｜SCHEMA=L5行为规律｜INTENT=L6意图｜CHRONICLE=编年史｜日日志=RAW+SUMMARY；密度=结论+依据+例外，日志首行记原始指令
- 蒸馏阈值：日日志>30天→长效信息入对应层→源移.backups/；>15KB优先蒸馏；行数硬限80(用户级100)触限走轻量降级（见头部）

## 🔍 检索协议（L1-L3零Token，07-29固化）
- L1 Metadata：查历史**强制先Grep**日日志标题+MEMORY/INTENT/SCHEMA关键词（Grep/Glob不调模型），禁跳过直Read全量
- L2 Instructions：仅Read Grep命中文件，单次≤3日日志；MEMORY.md非命中不读；80%+查询在此层解决
- L3 Resources：仅需具体数据(portfolio.json等)才Read；硬约束：先Grep再Read、单次≤3文件、压缩前走audit确认一致性
- 查询分类：recall→L1-L3｜compress→蒸馏流程(>30天日志→.backups/)｜audit→memory-consistency-audit skill｜learn→self-improving-agent/SCHEMA；已装 left-brain-memory skill 可一键触发全链路
