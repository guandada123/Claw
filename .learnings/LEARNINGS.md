# LEARNINGS (Claw)

Corrections, insights, and knowledge gaps captured during development.\n\n**Categories**: correction | insight | best_practice | knowledge_gap

---
### 2026-08-11 辩论降级根因脑补误判（correction → ✅已升级(2026-08-16)）
- **类型**: correction
- **现象**: 09:10 投顾槽位辩论 valuation 专家 reasoning-only 失败降级 HOLD。我先脑补"路由到推理模型/代理挂了"，被用户两次追问才查日志。
- **根因**(日志实锤): `com.workbuddy.proxy-deepseek.plist` 设 `THINK_BUDGET=high` → 代理对 `deepseek-v4-flash` 注入 `extra_body.thinking` → 返回主 content 空仅 reasoning_content 有值 → `debate_engine._call_llm` content 空即抛错。日志铁证：`🧠 Thinking mode injected: budget=high` + `track=points is_direct=false`（全走积分池）。
- **处置**: 修复 A — `_call_llm` content 空回退 reasoning_content；加 `_is_noise_response()` 过滤服务端垃圾 token 重试。
- **防复犯**: 全局 MEMORY.md 🔴铁律新增诊断纪律 + `automation-llm-local-proxy` skill 补「故障诊断流程」节 + 本 incident-triage skill。
- **去重**: 首次

### 2026-08-11 诊断纪律：先取证禁脑补（best_practice → ✅已升级(2026-08-16)）
- **类型**: best_practice
- **现象**: 用户指出"老自己脑补结论，不是第一次犯"。
- **根因**: 故障排查未执行"先看日志再定性"纪律，凭表面现象猜根因。
- **处置**: 固化三步——①`tail`代理日志+`nc -z`探存活 ②区分三类故障(连不上/reasoning-only/internal error) ③没日志实锤前不抛推断结论。
- **防复犯**: 写入全局 🔴铁律 + incident-triage skill Step1。
- **去重**: 首次

### 2026-08-12 双自动化并发修改同一脚本致 WB 停机 5h11m（best_practice → ✅已升级(2026-08-16)）
- **类型**: best_practice
- **现象**: 08-11 23:44 memwatch 正确触发重启(修复一生效, 8500阈值+5s采样抢在系统OOM前), 但 do_restart 清理残留后正要 open 拉起时, 23:45:44 统一巡检中枢 Runbook#1(memwatch_bump) 因"阈值8500<10000且90min内有重启"执行 unload/load 打断重启流程 → open 未执行 → WB 停机至 04:56 用户手动打开。
- **根因**(日志实锤): ①两个自动化(memwatch 守护 + unified_ops_center 巡检)并发操作同一目标(脚本文件+launchd), 无互斥协调 ②巡检中枢"识别根因"把正确的保护性重启误判为"误杀盘前自动化" ③memwatch_bump 直接 sed 脚本文件改阈值, 与 memwatch 自身 do_restart 冲突。
- **处置**: ①RSS_RESTART_MB 恢复 8500(修复二误改回10000) ②memwatch 强杀识别升级"告警+自动拉起" ③unified_ops_center MEMWATCH_TARGET_MB 10000→8500 消除拉锯 ④bump 前检查 2min 内"触发重启"日志(do_restart 进行中)即跳过, 防打断。
- **防复犯**: 单一配置源(参数进 launchd plist EnvironmentVariables, 脚本只读逻辑) + 巡检中枢改 plist 而非 sed 脚本 + 写前 2min 护栏 + 调参先看 memwatch 日志实锤。
- **去重**: 首次

### 2026-08-13 知识库LLM阅读层误杀新文入死链黑名单（correction → ✅已升级(2026-08-16)）
- **类型**: correction
- **现象**: 08-13 10:49 知识库自动化待读1篇《150元/股，很多人担心会破发》(猫笔刀)，LLM 解析 JSON 连败3次 → 误入死链黑名单(490df5942c10)，read=0 静默丢失正经文。
- **根因**(日志实锤): 代理 `🧠 Thinking mode injected: budget=high` 给 deepseek-v4-flash 注入 thinking → content 空仅 reasoning_content 有值(reasoning-only)。read_wx_articles 经 router.call_llm 只取 content → JSON 解析失败。debate_engine 已加 reasoning_content 兜底，但 read_wx_articles 未覆盖；且 reasoning_content 是思考链非 JSON，兜底仍解析失败 → 触发死链黑名单误判。
- **处置**: ①router._parse_success_response content空回退reasoning_content(防御) ②proxy 尊重客户端显式 extra_body.thinking(per-request opt-out，不覆盖) ③router.call_llm 新增 extra_body 透传 ④read_wx_articles 传 extra_body={"thinking":{"type":"disabled"}} 关闭思考(文章JSON抽取无需推理) ⑤launchctl 重启代理加载新逻辑 ⑥解黑名单+清失败计数→重读→read=1/actionable=1/signals=3→推送飞书。
- **防复犯**: 凡结构化 JSON 抽取类自动化(deepseek-v4-flash)必须 per-request 关闭 thinking；代理注入逻辑改「尊重客户端显式设置」而非无条件覆盖。
- **去重**: 与 08-11 辩论降级同根因(THINK_BUDGET=high)，但本例暴露三处新增缺口——①未覆盖调用方(read_wx_articles) ②reasoning_content 兜底对 JSON 任务无效 ③失败即入死链黑名单致正经文永久丢失。

### 2026-08-13 watchdog 误归因：关键失败真凶是 Marvis 外部重启，非 memwatch（correction → ✅已升级(2026-08-16)）
- **类型**: correction
- **现象**: 08-13 全天 8+ 关键自动化(投顾策略执行/午间选股/助理实盘监控)在 10:55–13:27 高峰时段"会话未拉起（排队/资源紧张）"静默失败。watchdog digest 自动归因"根因=memwatch 内存看门狗超阈值重启主进程，必要时自动提阈值"。
- **根因**(日志实锤): ①`workbuddy_memwatch.log` 今日(08-13) **零条 `==> 触发重启`**(memwatch 最后真实触发是 08-12 14:45)，即 memwatch 根本没动。②今日 8 次重启全是 `workbuddy_graceful_restart.sh`(日志前缀 `[graceful-restart]`, 原因=`外部触发`默认无 --reason)，时间戳 08:11/08:52/09:33/10:33/10:52/11:22/12:22/19:23。③该脚本注释写明"供外部调度方(马维斯/Marvis 检测到内存压力)调用"，且 `launchctl` 显示 `com.tencent.mac.marvis.app` 仍在运行 → 真凶是 **Marvis 每小时外部重启 WB 主进程**，打断高峰自动化 session 队列。**memwatch 阈值(8500MB)与此无关，提阈值无效**。④watchdog 脚本 `automation_failure_watchdog.py` 第5-7行 + 第76行**硬编码**"元凶=memwatch，Marvis 已关"——注释已与现状相悖(Marvis daemon 仍在跑且是实际外部重启方)，致 digest 持续误归因并误导"提 memwatch 阈值"。
- **处置**: 提出修正——①Marvis 在高峰(09:00–16:30)避让重启或改由调用传 `--reason` 以便溯源 ②修正 watchdog 硬编码归因(去掉"Marvis已关"误述，根因判定改为读日志实锤而非写死)。
- **防复犯**: 根因判定必须读 `workbuddy_memwatch.log` 的 `==> 触发重启` 实锤，而非脚本硬写；"会话未拉起"类失败优先排查外部重启方(Marvis/launchd)，非内存守护。
- **去重**: 与 08-12「双自动化并发改脚本致停机」同涉 memwatch 体系，但本例是**归因错位**(把 Marvis 重启算到 memwatch 头上)，非并发冲突；watchdog 分类逻辑需与实际日志对齐。
- **★08-13 20:44 深化(实锤 daemon 孤儿残留致 9h 全挂)**: 失败窗口实为 **10:55:25–19:59:26 共 58 次失败 0 成功**(非 13:27 结束)。深挖三实锤：①10:52:13 外部重启降级 TERM 强杀 8 进程(364/99765/99766/99803/99804/99811/99931/99938)，主进程 99766 被杀，但 **daemon-app-server 99824(10:34 启动)因"绝不 KILL daemon"保护幸存成孤儿(PPID=1)**；②此后 11:22/12:22/19:23 三次重启**只换主进程、不换 daemon**，daemon 99824 一直带病(stdio 管道已断) → 自动化持续 `Run did not create a session within 60000ms`，重启无法自愈；③**20:02:43 用户手动完整重启**(memwatch 无记录，主进程 73304 + 新 daemon 73361)后 20:03:45 立即恢复。**结论: 重启本身不是致命伤，致命的是 daemon-app-server 未随主进程重建，孤儿 daemon 阻断调度器**。当前残留: 99824 仍存活与 73361 并存，应清理。
- **★22:44 修复闭环(三处落地, 防复犯)**: ①`watch_workbuddy_mem.sh` + `workbuddy_graceful_restart.sh` 的 `wb_killable_procs` 移除 `daemon-app-server-entry` 排除(保留 `sidecar-entry`/`--serve`; daemon=调度器可清理, sidecar=对话载体不可动), 备份 .bak-20260813; ②`graceful_restart.sh` 新增 `mem_threshold_hit()` 内置护栏: 外部调用先自检(WB树>8500MB 或 整机可用<512MB)才执行, 否则打日志拒绝——dry-run 实测 WB=3902MB/可用=3217MB 拦截生效, **证明 Marvis 定时任务 id=26(每30min LLM 检测)今日 7 次触发全为误判**(与 memwatch 重启时间 08:52/10:52/11:22/12:22/19:23 精确对应, 记录 869~891); ③watchdog `automation_failure_watchdog.py` 归因文案改"会话未拉起(建会话超时: 资源争抢或调度器/会话服务故障)"+ 新增全失败窗口识别(关键失败≥2h 且窗口内 0 成功 → 提示系统级故障非资源争抢), py_compile + dry-run 验证通过; ④memwatch kickstart reload 加载新逻辑, 当前仅 1 个健康 daemon 73361 无孤儿。**遗留**: Marvis 任务 id=26 每30min 仍执行(护栏已兜底), 彻底停用需在腾讯 Marvis 客户端删除该定时任务, 勿直接改其数据库。

### 2026-08-13 投顾【盘中】策略执行(小时级)冗余且自锁=无效自动化（insight）
- **类型**: insight
- **现象**: 巡检报告把 automation-1784039339114(📈【盘中】投顾策略执行, rrule=HOURLY)的 19:03/19:59 会话失败列为"2 关键失败"。核实发现该自动化**冗余且自阻塞**，其失败零产物损失。
- **根因**(日志/DB 实锤): ①真实盘中交易执行由 5 个窗口自动化(1784506600526/634/523/665/706, rrule=BYHOUR=9,10,11,13,14;BYMINUTE=10, **prompt 内无 check_schedule/done_schedule**)承担,盘中直接跑 PHASE3。②本小时级自动化**独自**用 `check_schedule "投顾策略执行"` 日锁;HOURLY rrule 令首个凌晨运行(~01:34)抢到日锁,之后所有盘中运行命中"已锁"退出,且凌晨那次因 prompt 写明"非交易时段→跳过执行"——**其 PHASE3 交易执行永不在交易时段发生**。③故该自动化 24 次/日运行基本是 no-op;今日 19:03/19:59 会话失败本就会命中日锁跳过,无交易需补跑。
- **处置**: 标记冗余;建议删除该小时级自动化或改 rrule 为 5 窗口(与真实执行器对齐),消除 24 次/日空跑与"关键失败"误报。真实执行器今日 09:10/10:10 成功、11:10/13:10/14:10 因舰队故障漏跑,但今日 5 持仓均未触发止损/止盈(中国建筑收 4.44>4.40 减半阈值),无实际交易损失。
- **防复犯**: 自动化 rrule 须与 prompt 文档频率一致;带日锁的自动化禁止 HOURLY(否则凌晨抢锁阻塞自身);新增自动化前先查是否已有同功能窗口自动化。
- **去重**: 与同日本条 Marvis→daemon 孤儿舰队故障是不同问题——本例是该自动化**自身配置缺陷**,非本次故障受害者。
- **★校正**: 同日本条"watchdog digest 误归因 memwatch"已过时——watchdog 08-13 已修正运行时逻辑(328–333 行),本次 digest 实际准确输出"外部触发，非内存超阈值";仅第 76 行分类函数仍硬编码"memwatch 内存看门狗,非 Marvis"残留,可在下次维护时清掉。

### 2026-08-14 scripts/secrets.py 遮蔽标准库致 numpy 全线 ImportError（correction → ✅已升级(2026-08-16)）
- **类型**: correction
- **现象**: Claw venv 跑 `alpha_eval.py` 报 `ImportError: cannot import name randbits`(numpy/random/bit_generator.pyx)。`python -c "import numpy,pandas"` 单独跑成功、脚本跑必失败;重装 numpy/清 __pycache__ 均无效。
- **根因**(隔离实验实锤): `scripts/secrets.py`(旧密钥兼容层)与 Python 标准库 `secrets` **同名**。脚本运行时 `scripts/` 自动进入 `sys.path[0]`,numpy.random 内部 `import secrets` 解析到 Claw 的 secrets.py(无 randbits 符号)→ C 扩展 init 失败。判定实验: `sys.path.insert(0, scripts)` 后 `import numpy.random` 必挂;不加必过;`import secrets` 打印 `__file__` 指向 scripts/secrets.py 证实。
- **处置**: ①重命名 `scripts/secrets.py → legacy_secrets.py`(git mv 不可用,文件未跟踪,直接 mv),`router.py` 的 `from secrets import ...` 同步改 `from legacy_secrets import ...`;②清 scripts/__pycache__/secrets*.pyc;③连带补 `requirements.txt`+`pyproject.toml` 的 `scipy>=1.10`(alpha_eval Spearman IC 依赖缺失,同批暴露);④420 tests passed 零回归 + ruff 全绿。
- **防复犯**: 项目脚本目录内**禁止出现标准库同名模块**(secrets/random/sys/os/subprocess...),尤其是被 numpy/pandas 内部依赖的模块;新增脚本前 `python -c "import <name>; print(__file__)"` 自查。numpy 相关 ImportError 优先怀疑 sys.path 遮蔽而非 numpy 本体。
- **去重**: 与 08-13 其它 numpy/环境类故障不同源(那是依赖缺失 scipy);本例是**命名遮蔽**,scipy 是暴露出的第二个问题。
- **✅已升级(2026-08-16)**: 建议加 CI 检查——grep 脚本目录内是否存在与 stdlib 同名的 .py 文件。(并入🔴铁律"项目脚本目录禁与 stdlib 同名模块")

### 2026-08-14 做T报告 T仓股数未取整到100整手（用户指出"30/40股无法买卖"）(✅已升级(2026-08-16))
- **类型**: correction
- **现象**: 08-14 09:15 推送的「做T早盘自检」卡片中，长电科技 T仓=30股、华天科技 T仓=40股。用户指出"又犯了这个错误，100股才能买卖"——A股最小交易单位=1手=100股且须为100整数倍，30/40股根本无法下单成交。
- **根因**(代码实锤): `t0_strategy.py` 原第181行 `result["t_position_shares"] = round(t_value / price)` 仅四舍五入到整数，未做整手取整。长电300股底仓×10%额度≈¥2339，÷价77.99≈30股；华天同理≈40股。额度算法正确，唯独"股数→成交量"漏了整手约束。
- **处置**: ①新增 `LOT_SIZE=100` 常量；②`t_position_shares` 改为整手取整(理想股数÷100四舍五入×100，不足1手至少取100，且不超底仓向下取整)，新增 `t_lot_cost`(股数×价)保证卡片金额与股数一致；③小底仓下整手占比高于1/10时加 LOT info 提示(不拦截)；④`t0_daily_check.py` 卡片展示改用 `整手` 标注+实际成本；⑤补 5 个测试(test_t_position_shares_is_valid_lot/floor_to_one_lot/rounds_to_nearest_lot/base_below_one_lot_no_t/small_base_lot_exceeds_ratio_flagged)。修复后重跑：长电/华天均=100股(整手,~¥7782/~¥1788)，并已重推更正卡片到飞书群。44 tests passed 零回归。
- **防复犯**: A股任何"股数"输出(T仓/买卖量/补仓量)都必须 `int(round(x/100))*100` 且 ≥100；持仓/选股/交易执行类脚本统一复用 LOT_SIZE 常量，禁止裸 `round(金额/价)`。
- **去重**: 与 08-13 自动化失败/Marvis 故障不同源(那是调度层)，本例是**数量计算漏整手约束**的纯逻辑 bug。
- **✅已升级(2026-08-16)**: 建议在 `sim_trade.py`/选股/任何涉及"股数"的脚本做一次整手约束审计(grep `round(.*/.*price)` 或 `// 100` 缺失处)，避免同类再生产。(并入🔴铁律"A股股数必须整手")

### 2026-08-14 整手约束审计闭环（sim_trade.py/选股脚本，上条★升级候选已执行）
- **触发**: 上条★升级候选建议的整手约束审计已执行。
- **审计范围**: grep `scripts/` + `.workbuddy/scripts/` 全部"股数"计算点；逐文件核验买入/卖出/止盈/T仓股数是否 `×100整数倍且≥100`。
- **已合规(无需改)**: `sim_signal_advisor.py`(653/922 `int(.../100)*100`)、`scan_mainboard_local.py`(343)、`scan_mainboard_full.py`(145)、`backtest.py`(145/215/395 `int(capital/price/100)*100`+`>=100`)、`validate_constraints.py`(已有 `validate_lot(shares, lot_size=100)`)；根 `scripts/` 无裸 `round(value/price)`。
- **发现并修复的违规（3处 sim_trade.py + 1处 execute_buy_plan.py）**:
  1. `sim_trade.py` 新增 `LOT_SIZE=100` 常量（整手约束单一真相源）。
  2. `check_take_profit`：`int(pos["shares"]*sell_ratio)` 产生非整手(如500×0.33=165) → 改为 `//LOT_SIZE*LOT_SIZE`，<100 兜底整仓卖出。
  3. `cmd_buy`：无整手门禁 → 加 fail-closed 拒绝（非100整数倍或<100 直接返回错误，落库前拦截）。
  4. `cmd_sell` 部分卖出：`0<shares<pos["shares"]` 段加整手取整；全仓卖出(pos["shares"]) 保留零股尾仓放行。
  5. `execute_buy_plan.py` `do_buy`：消费端深度防御，计划股数 `int(plan["shares"])` 后向下规整到100整数倍，<100 跳过并告警（避免触发 sim_trade 失败闭合导致自动化静默失败）。
- **回归**: 新增 6 个整手约束用例于 tests/test_sim_trade_sanity.py（cmd_buy 拒绝非整手/接受整手、cmd_sell 拒绝非整手部分/部分整手取整+全仓零股尾仓、check_take_profit 整手取整/小持仓兜底整仓）。连同原有用例共 **57 passed 零回归**(19.85s)。
- **遗留(非本次范围)**: `cmd_buy` 成功返回体不含 `ok` 键（仅返回 cash_remaining/total_asset，既有不一致），测试中改以"持仓副作用"验证成交；建议后续单列小重构统一返回体（可选）。
- **闭环**: ★升级候选 → 已执行+修复完成；已升级(2026-08-16)为🔴铁律（"任何股数输出须 `int(round(x/100))*100` 且≥100，统一复用 LOT_SIZE，禁裸 round(金额/价)"）。

### 2026-08-20 automation_runs "refusal" 集群实为 429 配额耗尽（correction + insight → ✅已升级(2026-08-23)）
- **类型**: correction（修正初判）/ insight（诊断模式）
- **现象**: 08-19 20:09–21:28 出现 12 条 `Automation prompt stopped before completion: refusal` 集群，watchdog 归次要 SILENT。初判误写为"模型拒绝执行/内容策略异常"。
- **根因**(DB 实锤): 实际是 **HTTP 429 配额/频率限制**（code -32003, "Quota exceeded: 429 您的使用量已超出频率限制，将在 2026-08-20 02:09:25 UTC+8 重置"）。6 个互不相干自动化同享同一 reset 时间戳 → 单一模型路由当日配额耗尽（系统性）。08-16/17/18 均 0 条、08-20 02:10 重置后 0 条 → 偶发用量 spike 非周期。关联待办：DeepSeek 直连余额近枯竭/路由切换待完成。
- **处置**: 无需修复，配额每日 02:09 UTC+8 重置后自愈，08-20 巡检全绿印证。
- **防复犯**: ①"refusal" 字眼≠内容安全拒绝，含 429；诊断须取完整 thread_title 看 code/message。②同享 reset 时间戳 + 多互不相干自动化同时失败 = 单路由配额耗尽（系统性）。③确认恢复看 reset 后是否还有新 429。④watchdog 归次要 SILENT 行为正确，但 digest "refusal" 文案易误导，建议对 429 单独标注。
- **去重**: 与 08-11/08-13 THINK_BUDGET 注入（content 空）不同源；与 08-13 Marvis 重启（会话未拉起）不同源。
- **✅已升级(2026-08-23)**: 若 429 复现频率上升，给 watchdog 加「429 配额集群」专项检测（窗口内 ≥3 条同 reset 时间戳 429 → 推飞书提示切模型/查路由）。(已升🔴铁律"自动化 refusal/失败集群须先按 code+reset 时间戳聚类定因")

### 2026-08-17 北辰辩论降级复发：兜底式修复未关注入源头（correction → ✅已升级(2026-08-16 铁律已含)）
- **类型**: correction
- **现象**: 8/15 `482a030` 已"修复"辩论降级（content 空回退 reasoning_content 兜底），但 8/17 用户反馈北辰"完全没有参考度"，实测 13 只持仓辩论 conf 全=0.4、专家 risk_flags=["LLM调用失败"]、降级 HOLD。
- **根因**(实锤): 8/15 修复方向错——只加 content 空→reasoning_content 兜底，**未关闭代理 THINK_BUDGET=high 注入**。①stance 阶段用 reasoning 顶上；②但收敛阶段要**严格 JSON**，从 reasoning 自由文本提不出 → `_fallback_verdict` 简单多数降级；③更严重：生产环境专家仍 3 次重试全失败（`LLM调用失败` 降级 HOLD），兜底根本没兜住。**8/15 修复形同虚设**。
- **处置**: ①`debate_engine._call_llm` 请求**双写** thinking opt-out（顶层 `thinking` + `extra_body.thinking`），实测后端透传生效，content 正常返回，收敛 JSON 可解析；②新增 `tests/test_debate_engine.py::TestCallLlmThinkingOptOut` 固化防 8/15 复发；③`run_debate.py` 顺带修 gtimg 行情解析（GBK 崩溃+字段错位）+ 新增 `_enrich_stock_data()` 补全 RSI/MA20/MACD/量比，消除"数据饥饿"第二根因；④`proxy-deepseek.py` 注释固化 opt-out 契约。验证：601668 conf 0.40→0.75、因子全50→V75/Q55/G45/M30、0 降级专家。commit `97f040b`。
- **防复犯**: 🔴 **结构化 JSON 抽取类调用（deepseek-v4-flash）的修复必须"从源头关闭思考注入"，不能靠 content 空回退 reasoning 兜底**——reasoning 是思考链非 JSON，兜底对 JSON 任务无效，必降级。正确做法见 router.py / read_wx_articles.py / debate_engine.py：请求携带 `thinking=disabled`（顶层+extra_body 双写）。
- **去重**: 与 08-11 / 08-13 同根因（THINK_BUDGET=high 注入）。但本例暴露**新缺口**：8/15 那次只做了"症状缓解"（加兜底）而非"根因消除"（关注入），且**假设 reasoning 能当 JSON 用**——逻辑假设错误。这是典型的"假修复"，须记入反模式。
- **✅已升级(2026-08-16 铁律已含, 本run补CI契约测试+假修复反模式)**: 建议升级为🔴铁律：①凡结构化 JSON 抽取调用必须 per-request 关 thinking（双写），禁仅靠 content 空兜底；②修复"LLM 返回空/content 异常"类故障时，先查代理 THINK_BUDGET 注入日志（`Thinking mode injected`）而非加兜底；③CI 加契约测试：JSON 类调用方 payload 须含 thinking 关闭字段（已有 test_debate_engine 示例，建议推广到 router.call_llm / read_wx_articles）。

### 2026-08-24 自动化健康误报:工作日(HOURLY+BYDAY=MO-FR)自动化周末被误判 critical（insight → ★升级候选）
- **类型**: insight（检测逻辑缺陷）
- **现象**: 统一巡检中枢首轮运行 `自动化健康` 触发「退出码1」critical 飞书告警（📈【盘中】投顾策略执行 🔴 48h未运行）。实为误报。
- **根因**: `automation_health.py::check_health()` 对 `FREQ=HOURLY` 用固定 48h 阈值，未识别 rrule 的 `BYDAY=MO,TU,WE,TH,FR` 工作日限制；周五23:29→周一00:28 的周末间隔(~49h)为自然间隔，调度器 `next_run_at`(未来)已证明排期正常，却被 gap 启发式误判 critical。此前多次巡检为绿是因缺口恰 <48h（周六日间），本次(周一00:22)刚越阈值。
- **处置**: ①新增 `_extract_byday(rrule)` 解析 BYDAY；②BYDAY 仅工作日时阈值放宽至 72h；③`next_run_at` 在未来→直接跳过 stale 判定（权威证据，兼覆盖节假日）。单测确认真故障(next_run_at 过期+100h)仍报 🔴，未掩盖真实故障。重跑 `unified_ops_center.py` 15 项全绿。已补 success 飞书卡片纠正误报。
- **防复犯**: 任何"Xh 未运行/stale/静默失败"检测须先查 `next_run_at` 是否未来 + rrule 的 BYDAY/频率；gap 阈值必须按 MONTHLY/WEEKLY/BYDAY/once 分级，禁用单一固定值；飞书已误推的 critical 须补发纠正卡片。
- **★升级候选**: 巡检 stale 判定铁律 = "next_run_at 未来即健康,固定 gap 阈值须按 BYDAY/频率分级"（每周一必复发,建议周度自动化升🔴铁律）。

### 2026-08-24 signal_verify Wind 日限刷屏 25 天（correction → ★升级候选）
- **类型**: correction（日志刷屏 + 执行时长浪费）
- **现象**: signal_verify（信号溯源 15:00 STEP1）连续 25 天在 Wind 日限 180 次耗尽后刷屏 40+ 行 `Wind 每日查询上限已达`，单次执行 4m56s。
- **根因**(代码实锤): `wind_utils.py::_check_query_limit()` 日限达到时每次调用都 `logger.warning`，无进程内去重；`wind_available()` 只查 CLI+Key 不查日限 → signal_verify 逐股循环 fetch_realtime/fetch_history 每只触发 Wind 调用打印 warning。执行时长来自二次补拉 `sleep(3)+sleep(1)` 对失败 code 的重试。
- **处置**: 加模块级 `_limit_warned` 标志，日限警告仅打印一次、跨天重置。零行为风险（仅日志）。验证：import OK、stats used=180/remaining=0 复现场景。
- **防复犯**: 任何「日限/配额/熔断类」日志打印须进程内去重（once flag + 跨周期重置），禁止逐股/逐条循环裸 warning。
- **★升级候选**: 配额类日志打印去重铁律；signal_verify 二次补拉对「Wind 日限导致的历史数据失败」跳过无意义重试（💭 P2 遗留，需先确认腾讯 qfq 源为何失败）。
- **去重**: 与 08-21 debate-wind-quota-degradation 同涉 Wind 配额，但本例是**日志刷屏 + 执行时长**问题，非辩论数据链路去 Wind 化。

### 2026-08-24 automation refusal 集群第二族:-32603 上游500（insight → ★升级候选）
- **类型**: insight（故障分族）
- **现象**: 08-24 16:22 统一巡检中枢 / 16:30 自动补跑 连续2条 refusal 失败（间隔8min），watchdog 归次要 SILENT（正确）。
- **根因**(实锤): `code=-32603 / "500 internal server error"` = **上游模型服务端故障**，与 08-20 的 `-32003` 429配额族**不同源**；本地代理 :9999 同期健康（nc 通、stdout 无错、track=points 正常计费）；17:11 主动 curl 探测 HTTP=200/0.72s → 上游已自愈，窗口≈16:22-16:30。
- **连带核查**: 失败的"自动补跑"本身是漏报检查员 → 人工代跑其 STEP1，收盘复盘/早报/盘中锁全在，今日无遗漏可补；但 `claw_lock_自动补跑_20260824` 已占，若真有缺失锁会掩盖补跑机会。
- **防复犯**: refusal 必按 code 分三族(-32003配额/-32603上游500/内容拒绝)，禁字面归因；判上游 vs 本地先看代理日志+计费；确认恢复须主动 curl 探测而非等下轮；「补跑/校验类自动化自身失败」= 双重故障须人工代核其检查项 + 查 schedule 锁。
- **★升级候选**: watchdog `classify()` 增加 refusal code 分族标注（digest 输出 429配额/上游500/内容拒绝），并对「补跑/校验类自动化自身失败」提级为"需人工代核"。
- **去重**: 与 2026-08-20-automation-429-quota-cluster 同为 refusal 集群但不同 code 族；与 THINK_BUDGET thinking 注入（08-11/13/17）无关，本例未进入生成阶段。
