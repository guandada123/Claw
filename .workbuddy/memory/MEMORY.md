# 项目记忆（精炼版）

> 架构：本文件=FACT层(铁律/技术决策)，变更→原条目加 `→superseded by <日期>` 可回溯，禁平行堆重复。SCHEMA.md=L5｜INTENT.md=L6｜CHRONICLE.md=编年史｜日日志=RAW+SUMMARY(首行记原始指令)。检索：先Grep日日志标题+MEMORY/INTENT/SCHEMA关键词再Read；L3仅具体数据才Read(≤3文件)；审计→memory-consistency-audit；蒸馏：日日志>30天→蒸馏进对应层→源移`.backups/`；单文件>15KB优先蒸馏；豁免🔴铁律+演化链段。

## 🧭 注入上限与速查索引（09-25 建 · 每日记忆维护自动核对）
> ⚠️ **本文件注入上下文有字节上限 ≈17KB**（09-25 实测：全文 26,710B 时截断于 16,983B）→ **超出部分模型默认读不到，且随文件增长不断下移**。
> 复现方法：拿注入块末尾原文串回文件做字节定位（见 skill `context-health-cleanup` 第零节脚本）。
> 用法：命中主题 → `grep -n "<关键词>" $CLAW/.workbuddy/memory/MEMORY.md` → Read 该行前后 30 行。
> 纪律：新增高价值规则 → **同步更新本索引 + 写靠前位置**，不要只 append 到末尾（末尾=不可见=白写）。

**A 铁律(渠道/文件/数据/推送)**：`oc_9ee5303497f5e0e71666b610d6bdc346` · `先复制` · `get_effective_capital` · `push_card` · `cost_tracker` · `qt.gtimg.cn`
**B 防错(股价/推荐)**：`price_sanity` · `check-entry` · `_apply_sanity` · `_sanity_guard` · `reliable_current_price` · `sanity_failed`
**C 排程/rrule**：`单BYHOUR` · `automation-rrule-safety-check` · `check_schedule` · `done_schedule` · `automation- 前缀` · `影子记录`
**D 巡检中枢**：`hub_reconcile` · `D1`–`D8` · `registry.json`(只读镜像) · `workbuddy.db`(唯一真值) · `doc_apply` · `parity` · `wb_health_check` · `if_no_action`
**E 系统边界/选股**：`simulation/portfolio.json` · `user/portfolio.json` · `mainboard_scan_pool` · `总资¥50,000` · `分级止盈双模` · `market_gate`
**F 盘中监控/推送**：`1784039316540` · `1784506600526` · `push_feishu.sh` · `盘中监控双链` · `push_*_report.py`
**G 数据源/抓取**：`qt.gtimg` · `Wind` · `鱼盆`/`yupen` · `wechatrss` · `backfill_wx_content` · `wechat-download-api`
**H 运维/技术债**：`实盘同步管线已死` · `ths_account_sync` · `1785421201464` · `ZHITAI` · `ruff` · `Colima`
**I 记忆/协作**：`domain_expertise_map` · `运行态目录版本控制` · `SAFE-MODE 重建` · `memory-consistency-audit` · `context-health-cleanup`

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
- 🔴🔴 锁「活/死」两查法(09-24固化)：`check_schedule <name>` **只在有 `done_schedule` 写锁时才生效**；只 check 不 done = **死守卫**（恒放行）。判据①`grep -n "done_schedule\|schedule_utils.py done" <prompt>` ②`ls /tmp/claw_lock_<name>_*`。踩坑实录：知识库挖掘被误判为"日锁生效、6h 的 4 槽只跑首个"，实为 4 槽全跑。另：`--interval-hours N` 的锁写在**完成时刻**、槽=`[小时//N]`，故 N 须保证相邻触发点不同槽且前次不越界写进后次槽（三槽 00:50/03:20/05:50 用 **N=1**；N=2 会被 03:20 越界锁死 05:50）
- 知识库精读=三槽 `00:50 automation-1782137216020` / `03:20 11a78567-823d-4d13-970b-42ed417e9b5f` / `05:50 0d18c526-eab5-4402-b949-b9439eb682e1`（DAILY 单BYHOUR，`--max 15`×3=≤45 篇/天，全在 Hy4 免费窗口）；**三条共用同一份正文，改正文须三处同步**；宿主 cwds=`/Users/guan/WorkBuddy/automation-2026-09-21-13-02-04`
- 🔴 `automation_update` 的 cwds：create 时**不认传入值**（自动塞临时目录），update 时可改；但 update **拒绝 cwds=`/Users/guan/WorkBuddy/Claw`**（报 cannot host automations）→ Claw 内自动化只能改 prompt/rrule，需换宿主时用 automation-2026-09-21-13-02-04
- Sidecar守护唯一执行方=com.workbuddy.memwatch(阈值RSS_RESTART_MB=10000MB，08-06由6000上调)；禁依赖看门狗兜底关键自动化
- 🔴🔴 automation_update 的 id **必须带 `automation-` 前缀**(08-24实锤)：传裸数字ID不报错而是**静默新建影子记录**并把改动写进影子，真实记录毫发无损→PAUSE/改配置全成假成功(工具连返success仍ACTIVE在跑)。识破线索=①返回cwds与DB该记录不符(影子为临时目录`WorkBuddy/automation-<日期>`)②view传裸ID报not found但update却"成功"③按`name LIKE`查出同名双记录。铁律：**任何status/rrule变更后必readback** `SELECT status,updated_at FROM automations WHERE id='automation-<x>'` 才算成功；写操作与查automation_runs同一前缀规则

### 统一巡检中枢(08-06接管)
- unified_ops_center.py(宿主QTS自动化1785982929477每小时)；复用专项脚本(automation_health/self_heal/qts_pmf_guard/disk/feishu_channel)不重写；Runbook自愈白名单=memwatch_threshold_bump+docker_restart_container；审计unified_self_heal_log.json
- 被接管已PAUSED：综合健康1781780654327/跨项目1785918166172/多项目1785928720152；保留独立：watchdog失败扫表1785506975961、飞书自检1784084428353；飞书告知结构化卡，全绿SILENT
- 🔴 **真值纪律(09-24固化)**：scheduler DB(`~/.workbuddy/workbuddy.db`)=**唯一真值**，`.workbuddy/inspection_hub/registry.json`=**只读镜像**。发现漂移一律 **DB→校正镜像**，绝不反向改 DB。skill 定义=`.workbuddy/skills/unified-inspection-hub/SKILL.md`（v1.1）
- 对账器 **`hub_reconcile.py`**：observe(DB 只读URI)→diff(vs registry)→act(**只写镜像**)。漂移 **D1**镜像漂移(`--fix`自愈)/**D2**声明悬空/**D3**未纳管/**D4**心跳超时/**D5**文档漂移/**D6**待办逾期/**D7**契约盲区/**D8**跨项目闭环；心跳阈值=`周期+max(3h,周期×25%)`；退出码 **0干净/10仅D1/20须人**，且 **rc 同时写进 JSON**（`out.rc` 与进程退出码同源，消费方不必两条路各读一次）；开关 `--json`/`--brief`/`--fix`/`--doc`/`--no-doc`/`--cross-state`
- 🔴🔴 **错误 ≠ 通过；缺键 ≠ 空集(09-24三次审计固化)**：任何"校验器 → 消费方"的链路，**退出码必须区分「通过 / 有问题 / 输入不可信」**（0/1/2），且输入不可信时**所有可求值键显式置 `null`**（绝不能留 `violations: []` 这种"看起来像空集"的形状）。实例：`doc_apply_parity.py` 读不出 registry 时 `return 0` 且无 `violations` 键 → 切换守卫判据 `violations == []` 求值为 undefined → **"读不到"被读成"没有越界" → 可能放行生产 mode 切 live**。修法三处同时改：脚本 rc=2+置 null / 契约判据首条加 `parity.ok == true` / prompt 明确"先看退出码"。原语：**错误 ≠ 通过；缺键 ≠ 空集。**
- 🔴🔴 **看门狗不得静默消失(09-24三次审计)**：`wb_health_check.sh` 第 5 节原为**合取条件** `[ -x $PY ] && [ -f $RECON ] && [ -r $DB ]` —— 任一不成立就**整节无声跳过**（无日志无告警）。"Never rely on the AI to report its own death" 那层被反向击穿。且 `rc<20` 一律记"正常" → 对账器崩溃(rc=1)/用法错误(rc=2) 被当健康。**修法**：输入缺失 → 显式告警并**列出缺哪个**（24h 冷却）；退出码改**白名单**（0/10/20，其余=执行异常→告警）。**任何"看门狗/巡检"都要能被"它自己的输入消失"这件事触发告警。**
- 🔴 **D7 契约盲区(09-24三次审计，hub_reconcile 第 7 类漂移)**：D1–D6 全建立在"声明完整且指向的东西还在"这个假设上；假设一破，检查器**不报错、报 clean** —— **空声明与零漂移在输出上完全一样**。D7 自检：scope/清单/契约文档列表非空、每份契约文档至少一类判据、`autonomy_methods.ladder` 存在、`precondition_verifier` 脚本存在且**不依赖 cwd**、`preconditions` 含 `ok==true`、条件标识符能在校验器源码里找到。**上线首跑即从自己 registry 抓出 6 条真问题。**
- 🔴 **同一事实只允许一处自由(09-24三次审计)**：其余副本必须是**枚举 + 机器交叉核对**，不许是自由文本。三处实例：①SKILL.md 同时写"跃迁必须人点头"与 `auto_switch=true`（同文档两条相反铁律）②PA-001 `default_if_no_action` 自由文本"保持 calibrate" vs 真值自动切 live ③diag prompt 的 D5 清单/判据条数已过期。修法：`default_if_no_action` → **`if_no_action` 枚举**（`auto_switch_to_live|keep_calibrate|keep_as_is|escalate`）+ D7 与 `auto_switch` 交叉核对。
- **声明里的路径/命令必须 cwd 无关(09-24)**：`python3 .workbuddy/scripts/x.py` 换个目录就 No such file，而**没人会知道守卫已失效** → 一律写 `$CLAW/...` 或绝对路径（D7 会报相对路径）。
- **自动化 prompt 里的声明不在 D5 覆盖内(09-24，已登记 known_failure_modes)**：prompt 写的文档清单/判据条数/状态字样会静默腐烂（实测 diag prompt 滞后两轮）。后续可把 prompt 当"虚拟文档"纳入契约（读 DB `prompt` 列做 K1/K2）。
- **"在生产里看起来没问题" ≠ "这条分支被验证过"(09-24)**：D4 的 PAUSED 修复生产环境**没有样本**（71 ACTIVE / 0 声明项为 PAUSED）→ 只能**合成调度库**（`--db` 参数）才验得了。测不到的修复等于没验。

- **D5 文档漂移(v1.1.1)**：`registry.doc_contract` 声明「本部 SKILL.md 里哪些时段/键名/id 必须与真值一致」。**真值现算**（`rrule_times()` 从 DB 的 BYHOUR/BYMINUTE 推 HH:MM），**绝不写死在契约里**（写死=第三份会腐烂且自证的副本）。判据 K1 时段不一致/K1' **`anchor_dangling`**（锚点源非活跃→原为静默跳过=死守卫，已改报）/K2 契约键未提及/K3 注册表表格双向校验/K4 引用不存在 id（跳过含 `http` 行，防 bittide 文章号子串误报）。**文档腐烂 ≡ 镜像漂移，同一类病（都是声明的副本）**。多文档：`docs[]` 字符串继承默认/对象逐项覆盖；扩容只收"被中枢治理且文档写死真值"者（不收叙事报告）
- 🔴 **通用铁律(09-24 三度踩坑固化)**：**凡"检查需要有输入"的机制，必须自检"输入还在不在"** —— 同族三例：①锁只 check 不 done（锁文件永不产生）②契约锚点指向已删自动化（静默 continue）③看门狗读空目录。三者形态都"看着在跑"，实际恒放行。**第四例(二次审计)：`--doc` 覆盖只继承顶层默认 → 取证通道静默失效**
- 🔴 **观测不得污染生产(09-24 二次审计)**：只读/试跑用的参数（`--mode`、`--doc`）**绝不许回写生产状态**，也**不许写进权威产物/证据链**（曾：`--mode live` 试跑把 registry.mode 永久翻成 live + 往 parity.json 写伪造 live 记录 + 覆盖当日校准报告）。试跑一律写 `_probe`/临时区
- 🔴 **审计必须复核上一次的修复(09-24 二次审计)**：第二轮审计找到的缺陷**全是第一轮"修完之后新引入"的** → 用"能证伪它的实验"再测一遍上一轮的改动，否则**修复本身成为新的腐烂源**
- 🔴 **compile 通过 ≠ 能跑**：Edit 时 old_string 含某行而 new_string 漏带 → 整行被删，`py_compile` 不报（NameError 是运行时的）→ **改完脚本必须真执行一次**
- 🔴 **回滚点必须是真 commit**：声明的 `rollback_point` 要用 `git cat-file -t <sha>` 验到，否则"一键回滚"是谎话
- **D6 待办逾期(v1.2)**：`pending_actions[].due < 今天` 且 status ∉ {done,completed,cancelled,closed,skipped} → 报逾期天数/owner/`if_no_action`（原字段名 `default_if_no_action` 已废，见上"同一事实只允许一处自由"）。**"过期本身不是故障，没人知道它过期了才是"**
- **PA-001 = 守卫式自动切换（用户 09-24 全授权）**：`doc_apply.auto_switch=true` + `switch_guard`（前置=满14天∧0越界（parity 机器判定）；每候选自审=宿主存在/**纯增量**/来源已登记；**前 3 批 live 必推卡**；落地后复跑 parity+对账，越界/提交失败→自动 `git revert`+mode 退回 calibrate）。定性 **supervised**（非 auto）→ 人从"切换前点头"改为"切换后抽查"
- 🔴 **改名纪律**：自动化名/prompt 里**不写死状态字样**（如"（校准期）"）—— 状态只在 `registry.doc_apply.mode`。改名须同步 registry `night_slots` 键 + `free_window_decisions` + 文档；`_state_sync_notes` 里的历史记录**不改**（改了就是伪造历史）
- 🔴 **改长 prompt 的安全姿势**：调度库只读导出备份(`/tmp/autoprompt_<id>.bak-<date>`) → `assert old in src` 锚点替换生成 `.new`（不命中即中止）→ `diff` 看改动面 → `automation_update` 写入 → **逐字节比对 DB 与 `.new`**（平台会剥尾换行）+ 回归 `rrule/status` 未动
- 纳管范围由 **`registry.automation_scope`**(include/exclude 名称规则) 声明，**不手写全量**（库里71条约50条是业务/投研类=噪音）。范围内未登记→报 D3；新增治理类自动化会自动被抓出
- 🔴 **D9 同组槽位配置不一致(09-25，hub_reconcile 第 9 类漂移)**：铁律「单 RRULE 禁多 BYHOUR」要求多时刻**拆成多条独立自动化**，而 **`automation_update` 改不了** `model_id`/`model_is_thinking`/`expert_id`/`permission_mode`/`push_to_wechat` → **拆槽时新建的那几条只会拿到平台默认值**。实测知识库精读三槽（prompt 自述「共用同一份正文」）：槽1 = hy3+thinking+EquityResearchExpert，槽2/3 = 默认 flash+无专家 → 同一件事每天 2/3 的产出被静默降档。**这不是配置疏漏，是拆分动作的系统性副作用（拆一次复发一次）**。判据来自 `registry.consistency_groups[]`（成员 `ids` + 必须一致的 `keys` + **有意差异也要声明**）。退化形态不许静默：`group_no_keys`/`group_member_missing`/`group_too_small`。修好后自动转静默（七连测 7/7）。已记 `known_failure_modes: slot-split-resets-config`。
- 🔴 **报告粒度要对齐人的决策粒度(09-25)**：D9 初版对「一个键不一致」逐成员各报一条（3 成员 → 6 条），简报里读不出重点 → 改为一键一条、差异成员并列写一行。**把一个事实拆成 N 条噪音，等于让读的人替我做聚合。**
- 🔴 **写死日期的测试 = 会腐烂的声明(09-25)**：QTS `TestLocalDbFreshnessTruncation` 用写死的 `2026-09-04`，而判据是 `effective >= today - 5天` → 09-09 后必然转红，**与代码无关**；它不再验证"新鲜/截断"，只在验证"今天离写测试那天多远"。同族三例：相对时间的交接项（"明日开盘前"）、契约里写死真值、**写死日期的测试**。→ 一律相对化，判据本身由专门用例钉住。
- 🔴 **哨兵盲区：只覆盖"路径式引用"，漏了"sys.path 注入式引用"(09-25)**：`restore_forwarders.py` 与 `check_broken_refs.py` 的正则只认 `scripts/X.py` 字面串，而 prompt 里还有 `sys.path.insert(0,'scripts')` + **裸** `import X` 这种形态 → 两道哨兵**双双报「✅ 健康」**，实际 import 直接 ModuleNotFoundError（📊微信早报 / 📊收盘晚报 静默降级）。→ 新增 `find_syspath_bareimport_breaks.py` 并**委托**接入 check_broken_refs（单一实现）。**盲区不是"没查"，是查了一个自以为完备的子集。**
- 🔴 **技能/声明类"没用"的三条判据(09-25 技能库审计固化)**：①**能不能被触发** —— 没有 frontmatter 就拿不到 `description`，**语义匹配不到 = 装了等于没装**（比"没装"更糟：照样占技能列表的位置）。②**是不是被取代** —— 新技能 description 写"基于旧技能扩展"时，旧的应**退役**而非并存。③**运行时依赖还在不在** —— 逐个探测技能里写的脚本/目录/CLI。**同时必须避开四类误判**：CLI 不在 PATH ≠ 依赖缺失（`pinchtab` 二进制在 `binaries/`、`tushare` 是 Python 包）；自己解析器读不了多行 YAML 块 ≠ 技能 description 为空；引用 `~/.openclaw`·`~/.claude` 的多半是**安装说明/示例**不是运行时依赖；与插件同名时先比体量（用户级可能更全）。**处置一律"隔离不删除"**：`.quarantine-<date>/` + MANIFEST（含"为什么移出"与恢复命令），并有"删前确认"这道铁律。
- 🔴 **自报结果不能代替外部复核(09-25)**：写盘的脚本被重复执行 + 中途异常退出 → 留下「6 个已移走 / 1 个没移走 / MANIFEST 记 0」的半成品，**而脚本输出还在报成功**。只有拿"移动前记录的基线（文件数+哈希）"独立复核才发现。→ 同「检查器空转」家族：**凡是会写盘且自报结果的动作，事后必须自己重新数一遍。**
- 🔴 **外部死信层(09-24)**：AI 看门狗守中枢有**双死盲区**(中枢死→看门狗同死)→ 给**无LLM的 launchd 看门狗** `~/.local/bin/wb_health_check.sh`(30min) 加第5检查段调 `hub_reconcile.py`，rc≥20→飞书(6h冷却,锚`~/.local/etc/wb_health/…/hub_alert_ts`)。**绝不让 AI 报告自己的死**
- 🔴 **D8 跨项目闭环(09-24，「所有项目都要闭环」轮，hub_reconcile 第 8 类漂移)**：D1–D7 全部只覆盖 Claw 一个项目，而"闭环"是**跨项目**概念 —— 7 个项目的闭环状态写在 `~/.workbuddy/cross_project_state.json`，**此前不在任何机器检查覆盖内**：其 `handoff` 里 08-04 写的「明日开盘前」**静默躺了 50 天**（相对时间从写下那一刻就不可判定）。D8 K1 cwd/cwds 悬挂 / K2 磁盘真实 git 仓库未登记（**按 realpath 去重**，`~/WorkBuddy` 整树是软链→同目录两路径可见，不去重全是假阳性）/ K3 无 surfaces 健康声明 / K4 `updated_at` 与 mtime 差>24h（**写者只改子节点不 bump 顶层 → 「最后更新」这个字段在说谎 6.4 天**）/ K5 handoff 逾期 / K6 相对时间·未结构化·无 due。**判据：闭环 = 有声明 + 有覆盖 + 有留痕 + 有期限（且期限机器可判定）；休眠 ≠ 无主。**
- 🔴🔴 **新检查的第一条判据必须是"输入自证"(09-24 D8 自查，第四次踩同一个坑)**：D8 初版只写 `if not cs:` 就报"状态锚不存在" → 文件明明在、内容为 `{}` 时**输出在说谎**；更危险的是把「读不出来」和「没有跨项目问题」混成一个结果。修法：`state_anchor_missing`（不在）/`state_anchor_unreadable`（在但读不出=输入不可信）/`state_anchor_empty`（空对象）/`no_active_projects`（空 → K1/K3/K5 全空转）/`scan_root_missing`（`~/WorkBuddy` 不可访问 → K2 一条都不查，须明写"本次 D8=0 不代表没有漏项"）**五种各报各的**。→ 已把 K0 输入自证写进 skill 的 D8 判据清单：**「错误 ≠ 通过；缺键 ≠ 空集」要成为每条新检查的起手式。**
- 🔴 **记忆层是同一事实的第三份副本，同样会腐烂(09-24)**：`MEMORY.md` 曾写"不设自动切换(`auto_switch=false`)"，而 registry 真值早已是 `true`（PA-001 守卫式自动切换）—— D7 只能核对 registry 内部自洽，**管不到 MEMORY.md 里的自由文本副本**。修法：本文件里"状态/开关/条数"类事实**只写指针（指 registry 键）**，不复述具体值；D5 契约目前只收 SKILL.md 与季度巡检文档，**MEMORY.md 尚未纳入**（记为可扩展方向）。
- 🔴 **被排除的点也必须声明出来(09-24 v1.5)**：D5 K3 表校验的例外（`table_keys_expected_exclude` + 新增 `..._exclude_prefixes: ["_"]`）**声明在 registry 里，不许硬写进代码** —— 硬写 = 看着在查、其实默默少查一批键（死守卫家族）。`_` 前缀（`_history`/`_state_sync_notes`）= 元数据/审计痕迹，不是配置。
- 落地通道 `doc_apply`：开关与状态**一律查 `registry.doc_apply`**（`mode`/`auto_switch`/`calibrate_until`），本文件不复述具体值 →superseded by 2026-09-24（原文写"不设自动切换(`auto_switch=false`)"，而真值已是 `true`：PA-001 守卫式自动切换；见上条"记忆层是第三份副本"）；对账器 `doc_apply_parity.py` + 账本 `calibration/parity.json`

## 三系统边界（数据隔离）
- 📈投顾→.workbuddy/.workbuddy/data/simulation/portfolio.json(全权只给结果)｜📊助理→.workbuddy/.workbuddy/data/user/portfolio.json(国金)｜🇺🇸美股；持仓同步(07-15)：用户发持仓截图→先diff再分析
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
- 🔴 **实盘同步管线已死(09-04定性)**：`user/portfolio.json` 冻结于 **2026-08-28 15:57**（股数/成本/可用资金全是 08-28 值，只有价格靠 qt 实时重算）。
  根因：同步源 `/Users/guan/WorkBuddy/2026-07-30-08-18-28/ths_account_sync/sync.py` **整个目录已不存在**，全盘 `find` 无 `*account_sync*`/`ths_*sync*` 残留；
  而 `📋 盘后账户汇总报告`(1785421201464) 的 prompt 里写了「目录不存在则跳过同步步骤，不报错不尝试重建」→ **每天 15:07 照跑且 success=true，实际只做“读快照+推送”，从不写回**。
  自动化列表中已无任何「实盘持仓/委托/成交同步」条目（旧记忆里“每30分钟同步”已失效）。
  → 危害：若 08-28 之后有买卖，摘要显示的持仓（长电300/华天400）可能是**幻觉持仓**。须重建 QMT/同花顺同步，或改手工维护并在摘要加“数据冻结于08-28”警示。

## QTS日线数据架构（07-23）
- 本地回填主源 qts_daily_backfill.py(腾讯K线32线程→upsert 127.0.0.1:15432 daily_quote，自动化1784811393302@16:30)；容器daily_data_refresh仅增量；daily_quote加updated_at列

## 📐 记忆维护规则（固化）
- 密度=结论+依据+例外，日志首行记原始指令；查询分类：recall→L1-L3｜compress→蒸馏(>30天→.backups/)｜audit→memory-consistency-audit｜learn→self-improving-agent/SCHEMA
- 🔴 **10个缺失脚本已重建(2026-09-21)**：is_trading_day/cost_tracker/calc_rsi/workspace_scan/skill_hygiene 为忠实实现；advisor_rules/run_debate/discover_gzh/merge_signal/subscription_brief 为 SAFE-MODE 重建(头部标注 reconstruction, 不编造确定性买卖/成本/多空共识)。discover_gzh 依赖外部「红狐API」仍不可用→空结果；advisor_rules/run_debate 仅保守默认。原脚本逻辑与外部API凭证未恢复, 调用方勿当权威使用, 必要时补回原实现或凭证。
- 🔴 **运行态目录版本控制策略(09-21 用户授权 agent 代决)**：`.workbuddy/memory/automations/`(B,0 tracked/46文件756K纯生成态)整目录 gitignore；`.workbuddy/automations/`(A)保留29个历史 tracked 文件(msg_content.json/dedup_*.json/.backups)，仅忽略新增噪声(signal_trace_*.md/.archive/)+沿用既有 `*/memory.md` 忽略；自动化定义真相源 `registry.json` 已 tracked，运行记忆不入库。理由：避免每次运行产生 git churn 与跨机克隆冲突；显式忽略+文档说明不构成盲区(文件仍在磁盘,巡检读运行时态而非 git)。禁止巡检自动化擅自改动此策略。
- 🔴 **领域分工与共同盲区台账见 `domain_expertise_map.md`(09-21 建)**：金融=用户领衔(专家)、CS/模型/AI=agent 领衔+译术语；用户具「技术读写能力」(读得懂论证、能就技术议题拍板)，故 CS 域是术语翻译非降智科普。共同盲区(外部API凭证/券商终端对接/细分合规)与外部依赖台账见该文件。协作校准总纲见跨项目 `~/.workbuddy/MEMORY.md` 的「沟通姿态校准 / 领域分区」条。
