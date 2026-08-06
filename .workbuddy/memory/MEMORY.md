# 项目记忆（精炼版 ≤3k）

> **记忆架构（对齐 Hy-Memory 演化链，07-29 固化）**
> - 本文件=`FACT`层（铁律/技术决策），事实变更→原条目加 `→superseded by <日期>` 保留可回溯，禁平行堆重复
> - `SCHEMA.md`=L5行为规律｜`INTENT.md`=L6前瞻意图｜`CHRONICLE.md`=编年史｜`YYYY-MM-DD.md`=RAW+SUMMARY（首行记原始指令）
> - 蒸馏规则：日日志>30天→蒸馏进对应层→源移`.backups/`（不物理删）；单文件>15KB优先蒸馏
> - **行数硬限（08-06 放宽）**：原 07-29 锁 80 不放宽；**用户 08-06 明确"80行也可以放宽"→解除硬限，以信息密度优先，仅超 120 行才考虑蒸馏**。豁免：`🔴铁律`+`演化链`段锁死不降级
> - 检索协议（L1-L3零Token）：查历史**强制先Grep**日日志标题+MEMORY/INTENT/SCHEMA关键词再Read命中文件；L3仅具体数据才Read（先Grep后Read、单次≤3文件）；蒸馏>30天日志→.backups/；审计→memory-consistency-audit skill；已装 left-brain-memory 可一键触发

## 🔴 不可违反铁律
- 渠道：投资类→飞书群 oc_9ee5303497f5e0e71666b610d6bdc346（免审直推）；维护类默认不推仅⚠️/🔴异常推；前缀：📈投顾操盘/📊炒股助理/🇺🇸美股监控
- 删/移文件须「先复制→验证→再删源」+ 删前用户确认
- 🔴数据文件改动：data/字段增删改须先确认；总本金¥50,000权威不可推翻(=¥30,000+加仓¥20,000@07-14，记于config.capital_additions)，sim_trade.py 用 get_effective_capital() 读勿硬编码；例外：实时价(current_price)刷新可直拉(标来源+时间)
- 飞书推送卡片化：统一 push_card.py(interactive)禁--text降级；lark-cli≥1.0.76；notify_center已委托push_card；改脚本先--dry-run
- 成本：cost_tracker.py(数据层)/cost_monitor.py(报告层)被cost_dashboard_feishu依赖；监控自动化=1782002819199
- 🔴自动化调LLM必走本地代理:9999：model_config 必设 provider≠deepseek/catrouter(如"local_proxy")+base_url="http://127.0.0.1:9999/v1"；07-31固化preamble内置ensure_proxy(:9999 DOWN自动load两个plist自愈)，脚本无需单独探测
- 🔴实时价铁律(07-29 supersedes「Wind优先」)：盘中/监控/信号取价**必须走腾讯 qt.gtimg.cn**，Wind仅降级兜底；wind_quote.py已改「腾讯优先→Wind降级」+DO NOT REVERT（Wind滞后实测5.8%）；新取价脚本禁直接wind优先
- 🔴Sidecar守护唯一执行方=com.workbuddy.memwatch(阈值RSS_RESTART_MB=6000MB)：08-06 09:00 因WB树6076MB重启→盘前3关键自动化(08:35/09:00/09:10)漏跑+sessions.json=0。⚠️**盘前窗口(08:35-09:10)是memwatch高发区**，降漏跑首选提阈值至10000MB(待用户拍板)，禁依赖看门狗兜底关键自动化
- 🛡️统一巡检中枢(08-06接管)：unified_ops_center.py(宿主QTS自动化-1785982929477每小时)；复用专项脚本(automation_health/self_heal/qts_pmf_guard/disk/feishu_channel)不重写；Runbook自愈白名单=memwatch_threshold_bump+docker_restart_container；审计unified_self_heal_log.json；被接管已PAUSED：综合健康1781780654327/跨项目1785918166172/多项目1785928720152；保留独立：watchdog失败扫表1785506975961、飞书自检1784084428353；飞书告知结构化卡，全绿SILENT

## 三系统边界（数据隔离）
- 📈投顾→.workbuddy/data/simulation/portfolio.json（AI全权只给结果）｜📊助理→.workbuddy/data/user/portfolio.json(国金)｜🇺🇸美股；🔴持仓同步(07-15)：用户发持仓截图→先diff再分析
- 报告模板(07-13锁)：早/晚/周报走push_*_report.py自建docx+卡片+「📄完整报告」；禁prompt内联/直推stdout；A股红涨绿跌禁反转
- 🔴自动化排程铁律(07-27)：**单RRULE禁多BYHOUR**（只触发首个匹配小时，余槽静默丢无日志）→多时段拆多条单BYHOUR。已拆：助理实盘1784039316540=9 + 1785123941471/596/709/786(10/11/13/14)；信号溯源1780964240621=5 + 1785284629106(15:00)；原隐患1783310235388已删

## 模拟炒股+选股
- 总资¥50,000(07-14)，**禁科创/北交/ST**（创业板300/301已于07-29放开）；sim_trade.py: RESTRICTED_PREFIXES=["688","689","8","4"]+ST；MAX_POS=0.50/MAX_SECTOR=0.60/STOP_LOSS=0.08；创业板CYB_STOP_LOSS_PCT=0.15
- 🔄分级止盈双模(08-04落地·对齐trading-dual-mode-seamless skill)：冲刺期(每月20号后/6月14号后)=5/10/15%清仓；正常期(其余)=15/25/35%清仓；`_is_sprint_period()`+`_get_take_profit_levels()`运行时判定，模式切换自动重置take_profit_level；投顾prompt须同步双模口径(禁写死「冲刺期止盈5%」或「+30%强制线」)
- 助理主板选股：mainboard_scan_pool.json(COMBO=VWM0.6+BBR0.4,ADX≥25,RSI>80拦截)，单只≤¥5000止损-8%
- 🔄选股池增量补全(07-29)：refill_scan_pool.py(泛化版)枚举所有允许板块(深主000/001/002/003+沪主600/601/603/605+创业300/301)腾讯qt增量拉新，过滤退/PT/零成交/ST，写回不覆盖；自动化1785309382755@08:30
- 📈多智能体辩论(07-29)：run_debate.py→src/claw/debate/（7专家三环：stance→peer_review→convergence）；接入09:10策略(1784506600526)+15:50复盘(1782817769722)；测试18/18绿
- 🔄持仓数不限制(08-05)：智能选股1780738597945=v10.5/午间选股1782188906018=v6.3 取消≤3/≤5只；日报generate_daily_report.py取消≤3检查；validate_constraints.py max_positions 5→99；保留单只≤50%/行业≤60%/同日仅开1仓/留现≥15%风控
- 🛡️策略风控体系(08-05)：①market_gate.py大盘门控(上证<MA20且连续3日缩量→DEFENSE暂停新开仓) ②correlation_monitor.py(>0.7且≥3只→50%→40%) ③position_coeff.py仓位系数(量价×估值利差0.6-1.0) ④止盈市场状态驱动(正常期+强修复→第一档15→18%) ⑤risk_note机制(持仓position含risk_note优先执行,当前中国建筑601668跌破4.40减半) ⑥C2风格平衡(持仓全价值/红利→加1只科技≤15%)；7处投顾prompt全部接入
- 📊绩效面板(07-29)performance_dashboard.py｜🔍信号追溯trace_signal.py｜📅绩效周报自动化1785336744681@周日16:00

## 盘中监控双链（07-20方案B）
- 助理实盘1784039316540(:00监测)｜投顾策略5×DAILY(:10)：1784506600526/1784506634174/1784506653523/1784506653665/1784506653706（旧1784039339114已删）
- 投顾推送统一(07-24)：智能选股1780738597945+午间选股1782188906018用 **push_feishu.sh "$TITLE" "$CONTENT"** 封装禁--json-stdin；model=deepseek-v4-flash；PORTFOLIO/EXP_DIR 须指 simulation/
- 科技红利聚焦扫描：仅保留 automation-1784821193894；依赖 fetch_holdings_quotes.py+fetch_northbound_flow.py(缺失降级)；07-27修：sim读positions优先(权威源)，holdings仅实盘结构回退，sim的holdings=死副本勿依赖

## 防回退锁定（07-14，禁未经确认改）
- 鱼盆1783472286775=deepseek-v4-flash(禁glm-5.0-turbo)；OCR必LLM Read→JSON(v4)禁tesseract；早报唯一推送1782741941693
- 鱼盆文件名：raw=抓取日期，结构化=表头数据日期，常差1天勿混淆；补抓 fetch_yupen_rss.py --article-id <URL> --date <日>
- 鱼盆双源(07-21)：yupen_primary_*=Wind主源(07-23起Wind+雅虎)，yupen_*=RSS OCR兜底；read_yupen_data.py自动merge Wind优先+RSS补缺；v5.1双推已根治(07-23)：软失败判定 rss_updated==False 才推未推进

## 公众号抓取链（07-31 根因修复）
- 🔴 付费RSS `wechatrss.waytomaster.com/api/article` **有服务端防风控限流**（旧注释「无限流」错）→ 请求间隔须≥1.5s（0.3s必触发）
- 🔴 铁律：**抓取失败绝不落盘空壳**（_existing_urls()按url去重，空壳落盘该文永不重抓、正文永久丢失，曾致92%空正文）→ 用 fetch_article_content_ex() 返回(content,err)区分限流；禁 except:return "" 吞错
- 回填 backfill_wx_content.py(幂等)+自动化1785506323216每2h跑40篇，remaining==0才推；processed主键=file_key()=md5(filename)[:12]（根治#38换行符bug）
- 🔴 公众号双轨状态(08-06)：付费云停更第8天(07-29起)；本地 wechat-download-api 登录已恢复(isExpired=false)但**轮询器卡 07-20**。wx_rss_local.py 发现层已修(per-account `/api/rss/{fakeid}` 404→改 `/api/rss/all` 聚合源含content:encoded全文，按[昵称]前缀过滤)。**决策：sync_wx_articles.py 暂保持 --source cloud（本地07-20比云端07-29旧），待轮询器恢复过07-29再切 local**

## 运维/技术债
- 已裁维护推送(07-17)：7维护自动化顶层「默认不推仅异常推」；保留1782035436209/1783742027380
- 🔴**发布类授权升级(08-06)**：用户明确"发布类也授权给你，但发布前须审计+与Git内容比对避免发布错误"。落地铁律：①发布前必 `gh pr diff` 拉全量 diff 做内容审计（范围/风险/是否含敏感）②必与本地仓 `git fetch` 后比对 head 一致性（防本地/远程错位）③必确认 `mergeable` 状态+合并后 main CI 重跑变绿才算发布成功 ④已合并分支远程删除被保护规则拒→保留孤儿分支不强行绕过（标注MERGED待清理）⑤实盘下单/对外发布仍归用户（handoff.dangerous_permissions）
- $SCRIPTS=.workbuddy/scripts(preamble:10)；主脚本 cd $CLAW && python3 scripts/xxx.py 避同名碰撞；westock CLI代码带sh/sz前缀
- ⚠️user/portfolio.json current_price空(仅成本)，诊断实时拉qt.gtimg.cn；健康检查对月/周度误报stale勿自动PAUSED
- 存储=致态SSD(/Volumes/ZHITAI)+Colima，~/.workbuddy等符号链接禁删/移；公众号双轨=付费RSS+本地API(localhost:5001)
- ✅断链已澄清(07-20误报07-29更正)：1781778427910已DELETED；1782741941693引calc_rsi.py存在无断链；审计须排除DELETED状态
- 🔧proxy看门狗(07-26)：com.workbuddy.proxy-watchdog(StartInterval=30, proxy_watchdog.py自动load回)；launchd后台agent禁/bash脚本须managed python3直跑.py
- 🔧自动化运维排障(08-04)：①查 `automation_runs` 表**必须用带 `automation-` 前缀的 ID**，裸 ID 必误报"无运行记录"；②status 恒为 `PENDING_REVIEW` 属默认记录态非失败；③验真运行看 `last_run_at`/`created_at`；④**Claw 本地助手工作区禁托管新定时自动化 = WorkBuddy 平台级限制**(08-06实测，守卫约08-04上线，无本地开关)→新建须用其他项目工作区宿主(如QTS)，git 用 `git -C <abs>` 不依赖 cwd
- 🔧备份清理核实(08-04→supersedes「需建prune自动化」)：**不成立不建**。output/.backups/daily/ 15个tar.gz是14天滚动正常(156M预期)；`.bak-*` 0个；.backups是记忆蒸馏归档删了丢历史且已有🧹记忆体检(1780769419635)→禁自动prune
- ✅Claw CI全绿(08-04)：ci.yml已删；ruff锁0.15.17+ignore PLR0917；pre-commit EOF排除机器JSON；benchmark缺test优雅跳过；safety禁--full-report；gitleaks按commit豁免dd5df15c；🔐DeepSeek key轮换完成(sk-443b…e408吊销，活跃sk-faaf…2796，本地明文已清)

## QTS日线数据架构（07-23，详情CHRONICLE）
- 本地回填主源 qts_daily_backfill.py(腾讯K线32线程→upsert 127.0.0.1:15432 daily_quote，自动化1784811393302@16:30)；容器daily_data_refresh仅增量(tushare限频慢)；./strategy-service:/app挂载即时生效；daily_quote加updated_at列

---

## 📐 记忆维护规则（固化）
- 演化链：变更→原条目加 `→superseded by <日期>` 保留可回溯，新条目写 `supersedes<旧>`；旧条目保留不删（可回溯）；冲突新优先
- 分层职责：MEMORY=FACT｜SCHEMA=L5行为规律｜INTENT=L6意图｜CHRONICLE=编年史｜日日志=RAW+SUMMARY；密度=结论+依据+例外，日志首行记原始指令
- 蒸馏阈值：日日志>30天→长效信息入对应层→源移.backups/；>15KB优先蒸馏；**行数硬限已放宽(08-06用户"80行也可以放宽")→仅超120行才考虑蒸馏**

## 🔍 检索协议（L1-L3零Token，07-29固化）
- L1 Metadata：查历史**强制先Grep**日日志标题+MEMORY/INTENT/SCHEMA关键词（Grep/Glob不调模型），禁跳过直Read全量
- L2 Instructions：仅Read Grep命中文件，单次≤3日日志；MEMORY.md非命中不读；80%+查询在此层解决
- L3 Resources：仅需具体数据(portfolio.json等)才Read；硬约束：先Grep再Read、单次≤3文件、压缩前走audit确认一致性
- 查询分类：recall→L1-L3｜compress→蒸馏流程(>30天日志→.backups/)｜audit→memory-consistency-audit skill｜learn→self-improving-agent/SCHEMA；已装 left-brain-memory skill 可一键触发全链路
