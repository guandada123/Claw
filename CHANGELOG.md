# Changelog

## [2026-09-24 晚·全授权收口] D5 扩容即抓到真实腐烂 + D6 待办逾期 + PA-001 改为守卫式自动切换

### 一处真实腐烂（扩容立刻见效）
- `majiu-management/references/quarterly-inspection.md` 写「每年 1/4/7/10 月 1 日 **09:00**」，且文末**创建模板**也是 `BYHOUR=9;BYMINUTE=0` —— 真值 **23:49**（早已挪进免费窗口）。
  → 按 `auto_apply` 档修正两处，并加一句"本行受 `doc_contract` 对账约束"
- **意义**：这是扩容后**当天就抓到**的第一个真实案例 —— 证明 D5 不是纸面机制。若没抓到，下一个照模板建自动化的人会把新自动化建在扣积分的 09:00

### D5 扩容与自纠
- `doc_contract.docs[]` **多文档**：字符串项继承顶层默认，对象项逐项覆盖。新增 `docs_note`（扩容纪律）与 `anchor_rule`
  - 扩容纪律：**只收"被中枢治理且文档里写死了真值"的文档**；不收叙事型报告（`output/*.md` 把旧时间当历史写会假阳性）
- **修掉 D5 自身的死守卫**：锚点 `source_id` 指向非活跃自动化时，原实现是 `continue` **静默跳过** → 锚点看着在、其实什么都没查（与"只 check 不 done"同族）。
  改为报 **`anchor_dangling`**；另增 `anchor_unverifiable`（rrule 里没有 BYHOUR/BYMINUTE 算不出真值）
- 清理上一版残留在顶层的 `must_mention_keys`/`time_anchors` 等字段（留着会被第二份文档继承 → 8 条假阳性）

### 新增 D6 待办逾期
- `pending_actions[]` 中 `due < 今天` 且 `status ∉ {done,completed,cancelled,closed,skipped}` → 报逾期天数 + `owner` + `default_if_no_action`
- 理由：**"过期本身不是故障，没人知道它过期了才是"** —— 有期限却"不动作也行"的待办最容易静默过期

### PA-001 改为守卫式自动切换（用户全授权）
- `doc_apply.auto_switch: true` + 新增 `doc_apply.switch_guard`：
  - 前置（机器判定，缺一不可）：`days_elapsed >= days_needed` + `violations == []`
  - 每候选自审：宿主存在 / **纯增量**（不删不覆盖） / 来源已登记
  - **前 3 批 live 必须推卡**（notify-on-apply）；落地后复跑 parity + 对账；越界或提交失败 → **自动 `git revert`** 并把 `mode` 退回 `calibrate`
  - `never`：核心类、禁区
- PA-001 `owner: human → auto`、`status: waiting → scheduled`，`human_step` 改为"无需动作，首批会推卡，届时抽查即可"
- 定性：自主度按 **shadow → notify-only → supervised → auto** 逐级晋升，**当前为 supervised**（人从"切换前点头"改为"切换后抽查"）

### 变更
- 「📥 每日·文档类落地（校准/生效自动）」v1.1 → **v1.2**：加 1c 模式自动切换（切完推卡 + 留一个观察日）、Step 2 自审、3b 落地后复检 + 自动回滚、Step 5 推送纪律；**名字去掉"（校准期）"** —— 状态只在 `doc_apply.mode`，名字里写死状态就是制造会腐烂的声明
- 「🩺 统一巡检·报错自诊与修复」v2.1 → **v2.2**：Step 5 五类→**六类**漂移，补 D6 说明（卡片须给 id/逾期天数/owner/default_if_no_action）
- 两处 prompt 改动均**先只读备份 → 锚点替换（`assert old in src`）→ diff 确认改动面 → 写入后逐字节比对**（2442==2442、3970==3970，差异仅平台剥离的尾换行；rrule/status 未动）
- `unified-inspection-hub/SKILL.md` v1.1.1 → **v1.2.0**：对账剧本补 D6 行 + `anchor_dangling`/`anchor_unverifiable` + 多文档规矩；分级授权补"守卫式自动切换"；落地剧本补模式自动切换/落地守卫/名字不含状态；反模式加"守卫的判据指向不存在的源"；审计检查点 9→**10**（+ 依赖的输入还在不在）
- registry：`night_slots` 键与 `free_window_decisions` 同步新名

### 自测
```
D5/D6 故障注入 7 项全过 ✅
 1 多文档契约基线无 findings
 2 锚点指向不存在自动化 → anchor_dangling（死守卫不再静默）
 3 季度巡检文档改回 09:00 → time_mismatch（真值 23:49）
 4 锚点按文档隔离（docs[0] 的锚点不落到 docs[1]）
 5 D6 检出逾期 10 天（含 owner/default_if_no_action）
 6 D6 已完结不误报   7 D6 未到期不误报
对账：clean=True | D1..D6 全 0 | rc=0        py_compile / bash -n OK
```
> 注：测试 4 第一版判据写错（hub 文档里确实同时有「majiu 季度巡检」与 23:49 → 合法通过）。**是测试错，不是代码错**，已改为按 doc 隔离断言

## [2026-09-24 晚·D5] 文档漂移对账上线 —— skill 文档也纳入"会腐烂的声明"

### 判断
- 「文档腐烂」和「镜像漂移」是**同一类病**：都是声明的副本，没人对账就会默认失真。上一条审计刚抓到 v1.0 的 07:30/10:00/09:00/7 层全错 → 这次把它变成**机器每天查**的东西，而不是等下次审计再抓一次

### 新增
- `registry.doc_contract` — 文档契约（声明式）：`docs` / `key_table_header` / `must_mention_keys` / `time_anchors[]` / `table_keys_expected(+_exclude)` / `checked_by` / `why` / `scope_note`
- `hub_reconcile.py` 新增 **D5 文档漂移**（`check_doc_drift()`），四类判据：
  - **K1 `time_mismatch`** —— 契约 `time_anchors[].label` 所在行必须出现该自动化 rrule 的**真值时刻**。真值**现算**（由 `rrule_times()` 从 DB 的 BYHOUR/BYMINUTE 推 `HH:MM`），**不写死在契约里** → 文档改错、调度改错两个方向都能抓
  - **K2 `key_omitted`** —— `must_mention_keys` 必须被文档提及
  - **K3 `key_unknown` / `key_missing_from_table`** —— 中央注册表表格**双向**校验：既不许列出 registry 没有的键，也不许漏列 registry 有的键（**表 = registry 的索引，会一起腐烂**）
  - **K4 `id_dangling`** —— 文档引用的自动化 id 必须存在于 DB；**跳过含 `http` 的行**（把上一轮 A3 的"bittide 文章号子串误报"教训固化进代码）
- 新开关：`--doc PATH`（覆盖契约文档，供自测造错样本）/ `--no-doc`

### 变更
- D5 计入 `needs_human` 与退出码 20；`--brief` 输出 `[文档腐烂] <doc> — <detail>`；人类可读输出新增 D5 分组
- `--doc` 只读覆盖，**不写回任何文档**（改 skill 文档仍属 `propose_review` 档）
- 「🩺 统一巡检·报错自诊与修复」升 **v2.1**：Step 5 从"四类漂移"扩为**五类**，补 D5 说明（卡里只给"哪一行该改成什么"，不直接改文件）。改动前先从调度库只读备份原 prompt（`/tmp/autoprompt_f8443038.bak-20260924`），写入后**逐字节比对**确认一致（2236 == 2236 ✅）
- `unified-inspection-hub/SKILL.md` **v1.1.0 → v1.1.1**：注册表表补 `doc_contract` 行；对账剧本补 D5 行 + 四类判据 + 新开关；审计检查点 8→**9** 条（+ 契约文档一致性）；反模式加"文档写死时刻却无人对账"；「可扩展方向」把"待做"改为已实现

### 自测
```
$ python3 .workbuddy/scripts/hub_reconcile.py --json
clean=True | D1..D4 空 | D5=0                            rc=0   ← 契约文档自洽（dogfood）

# 故障注入（临时副本：时段改回 07:30 + 删 automation_scope 表行 + 加 ghost_key 行 + 加悬空 id）
$ python3 .workbuddy/scripts/hub_reconcile.py --doc /tmp/xxx/bad.md --json
clean=False needs_human=True                              rc=20
 - time_mismatch           | 「统一发现」真值 00:20，但文档中没有该时刻（文档同类行出现 07:30）
 - key_unknown             | 文档注册表列出了 `ghost_key`，但 registry.json 里没有这个键
 - key_missing_from_table  | registry 有键 `automation_scope`，但文档中央注册表表格漏列了
 - id_dangling             | 第 173 行引用了不存在的自动化 id automation-9999999999
```
→ **4/4 命中**；`--brief` 4 行告警文案正确；`py_compile` / `bash -n` OK

## [2026-09-24 晚·审计+全网优化] 补上「中枢自己静默死掉」盲区 + 声明覆盖率 11→25

### 审计发现（4 项，2 项真问题）
- A1 registry ↔ 调度器 DB 漂移 = **0** ✅ ／ A2 registry 引用脚本缺失 = **0** ✅
- A3 1 处疑似 dangling id **证伪**：`9bb11e84-…` 是候选 url 里 `bittide.aicompass.dev/article/<uuid>` 的文章号，属子串误报
- A4 **真问题：registry 只声明 11 条，中枢治理类活跃实有 ~25 条** → 清单落后于现实（对应外部原则 "a reconciler only covers what you remembered to declare"）

### 新增（3 件能力）
- `.workbuddy/scripts/hub_reconcile.py` — 声明式**对账器**：observe（DB 只读 URI）→ diff（vs registry）→ act（只写镜像）。漂移分类 **D1 镜像漂移 / D2 声明悬空 / D3 未纳管 / D4 心跳超时**；心跳阈值 `周期 + max(3h, 周期×25%)`；退出码 **0 干净 / 10 仅 D1 可自愈 / 20 须人**；开关 `--json` / `--brief` / `--fix`
- registry 新增 `automation_scope`（include/exclude **名称规则** + 理由 + `why_not_all`）→ **用规则声明范围，而非手写列全 71 条**；据此**补登记 14 条**（声明 11 → **25**）
- **原生 launchd 看门狗加第 5 检查段**：`wb_health_check.sh`（**无 LLM**，30min/次）调 `hub_reconcile.py`，rc≥20 → 飞书告警（**6h 冷却** `$STATE_DIR/hub_alert_ts`）+ 卡片新增区块 → 这是**平台无关的外部死信层**：原设计里"AI 看门狗守中枢"存在双死盲区（中枢死 → 看门狗同死）
- `.workbuddy/skills/unified-inspection-hub/SKILL.md` **v1.0 → v1.1**（见「文档」）

### 变更
- 「🩺 统一巡检·报错自诊与修复」升 **v2**：新增 Step 5 声明式对账（D1 → `--fix`；D2/D3/D4 → `--brief` + propose_review 卡），并注明原生看门狗是平台无关第二层
- registry `_state_sync_notes` 增记本轮收敛；`automations[]` note 标注补登记来源

### 全网依据（真实来源，已落进 skill 文档）
- "Never rely on the AI to report its own death" / 非 AI 原始看门狗 / Safety Net 模式 — https://67ailab.com/posts/day-10-architecture-sre-agents
- 四不可协商护栏（白名单 / 爆炸半径 / 高风险人工闸门 / **默认可回滚**）+ 分级成熟路径 — https://devtocash.com/blog/ai-agents-sre-autonomous-incident-response-2026
- 置信阈值 + 写操作须人批 plan — https://devops.gheware.com/blog/posts/agentic-ai-incident-response-sre-2026.html
- observe→diff→remediate + **持久漂移才值得自动处置** — https://www.acejournal.org/2025/07/03/gitops-drift-detection-and-reconciliation-loops.html
- 漂移失败模式 F1–F7（含**静默漂移**）— https://devopsschool.jp?p=1840/
- diff 须**语义化**（剥离服务端字段）— https://oneuptime.com/blog/post/2026-02-26-configuration-drift-detection-gitops/view

### 文档
- `unified-inspection-hub/SKILL.md` **v1.0 → v1.1**：修正腐烂内容 —— 统一发现 07:30→**00:20**；演进 10:00→**SA 23:22**；卫生 09:00→**23:44**、季度 09:00→**季首日 23:49**；中央注册表 6 键→**全键表**（补 `doc_apply`/`schedule_policy`/`pending_actions`/`automation_scope`/`known_failure_modes`/`_state_sync_notes`）；架构 7 层→**8 层**（+外部死信层）；新增「排程铁律」4 条（含**两查法判锁死活**）、「声明式对账剧本」、「外部依据（真实 URL）」；反模式 8→**11** 条（+死守卫 / 假静默 / 自作主张升级自主度）；审计检查点 5→**8** 条。备份 `SKILL.md.v1.0.bak-20260924`
- `output/discovery-loop-upgrade-2026-09-24.md` 增补 §十五（自审四项 + 落地 5 条外部模式 + 新增 3 件能力 + **刻意不做 5 条** + 文档同步对照表 + 自测证据）

### 自测
- `hub_reconcile.py --json` → `declared=25 / clean=true / rc=0`
- D1 实测：造漂移 → `rc=10` → `--fix` → 镜像按 DB 校正 + 留痕 ✅
- D4 实测：`VACUUM INTO` 副本 runs 回拨 40h → `rc=20`，`--brief` 名称/时间/阈值正确 ✅
- `bash -n wb_health_check.sh` OK；`--dry-run` 日志 `== 中枢对账正常(rc=0) ==` ✅

## [2026-09-24 晚·判断] 校准期「对拍」可验证化：机器算得清的让机器算，人只点头

### 判断（明确不做）
- **不设自动切换**（`doc_apply.auto_switch = false`）：三条切换判据不等价 —— ①时长、②对拍越界可机器判定；**③"真写进去会不会更糟"是价值判断**。live 一旦生效即自动改写 skill 文档，放过①③的错要等人偶然翻到才发现。→ 机器把①②算清并每日自检，人只做③。理由记入 `doc_apply.auto_switch_reason`

### 新增
- `.workbuddy/scripts/doc_apply_parity.py` — 校准期对拍校验器：判据①算 `days_elapsed/days_needed`；判据②逐条校验 `selected.target ∈ references/*.md|README.md|CHANGELOG.md`（**越界退出码 1**）；判据③只输出 `human_spotcheck_sample` 样本、不下结论。输出 `ready_for_live_by_machine_criteria`
- `.workbuddy/inspection_hub/calibration/parity.json` — 机器可读账本（每次闸门运行一条，`<日期>:<模式>` 幂等覆盖），免去解析 markdown

### 变更
- `apply_doc_candidates.py` 新增 `write_parity_ledger()`（写上述账本）
- 「📥 每日·文档类落地」升 **v1.1**：calibrate 分支新增 Step 1b 跑校验器 —— `violations` 非空 → 飞书告警（闸门漏了非文档目标，属客观异常）；为空 → 静默留痕
- registry `doc_apply` 增 `parity_verifier` / `parity_ledger` / `switch_criteria_machine`（逐条标注 machine vs human-only）/ `auto_switch` / `auto_switch_reason`；PA-001 增 `verifier` / `auto_check` / `human_step`

### 文档
- `output/discovery-loop-upgrade-2026-09-24.md` 增补 §十四（判断依据 + 校验器自测：正常 rc=0 / 越界样本 rc=1）

## [2026-09-24 晚·收口] 备份搬夜 + 周度审闭路 + 中枢三件静默语义统一

### 变更
- 「💾【系统维护】每日数据备份」`automation-1782394153045`：`HOURLY;INTERVAL=6`（4 次/天、相位漂移，3 次落在扣分时段）→ **`DAILY 00:10`**（Hy4 免费窗口）。
  依据：`backup_data.py` 产物按日命名（`claw-backup-<date>.tar.gz`，保留 14 天）→ 当天 4 次是**同一文件反复覆盖**，3 次纯冗余打包。prompt 升 v2。与一致备份对拍差集仅此 1 条

### 修复
- 🔴 **发现回路「审」半程接不上（闭环断点）**：`🧬 技能演进-周度` 只拾取 `status="pending"`，而核心类候选状态为 `proposal_new_capability` / `applied_doc_proposed_core` → **永不会被审**。修法：候选加 `needs_review=true`（3 条），prompt 升 v1.1 改为拾取 `status=="pending"` **或** `needs_review==true`，审毕清标记防重复提
- 「🧬 技能演进-周度」v1.1 与「🧹 技能卫生审计-月度」v1.1：修**同族假静默** —— 收尾原为无条件 `push_feishu.sh` + `cat /tmp/{evolution,hygiene}_push.md 2>/dev/null || echo '...SILENT'`，跨周/跨月残留会被当本期结果推送。改为判定前 `rm -f`、无内容不写文件不推送、收尾 `[ -s ]` 守卫（与「🛡️ 统一发现」v1.1 口径一致）

### 新增（registry）
- `pending_actions[]`：PA-001（**2026-10-08** 人工将 `doc_apply.mode` 置 live｜判据 + 不动作时的默认行为）、PA-002（2026-09-26 周度审 3 条 core 候选）
- `schedule_policy.free_window_decisions`：显式记录"搬进窗口"4 项 vs "刻意保留窗口外"（盘中/盘后时点依赖、必须在 15:50 晚报前跑完、监控类需白天覆盖、需人白天读），避免后续被误改

### 决策（明确不做）
- **监控类不降频**（中枢存活看门狗 2h / 统一巡检中枢 3h / 失败扫表 3h / 巡检中枢存活 6h）：时效价值高于省下的积分，挪夜=白天失明
- **105 条软删记录不清理**：软删是平台的回滚能力，手动删行零收益且有风险
- **校准期不提前**：维持只读对拍到 2026-10-08

## [2026-09-24 晚] 知识库精读扩为三槽（方案 B）＋ 锁语义更正

### 修复（关键更正）
- ❌ 推翻当日早前结论「`check_schedule 全网挖掘` 是有效日锁」：该自动化**只 check、从不 done**，且 `/tmp/claw_lock_全网挖掘_*` 一个都不存在 → 守卫**恒放行**（实测 rc=0）。旧 `HOURLY;INTERVAL=6` 的 4 个槽是**全部真跑**（runs 自 09-18 起稳定 4 次/天），并非「1 真跑 + 3 被锁空跑」
- 判定方法升级为**两查**：①`grep -n "done_schedule\|schedule_utils.py done" <prompt>` ②`ls /tmp/claw_lock_<name>_*` —— **只 check 不 done = 死守卫**；已作为 Gate 0.5 写入 `automation-rrule-safety-check`

### 新增（方案 B）
- 自动化「📚【知识库】全网挖掘+文章精读 · 槽位2」`11a78567-823d-4d13-970b-42ed417e9b5f`（DAILY **03:20**）
- 自动化「📚【知识库】全网挖掘+文章精读 · 槽位3」`0d18c526-eab5-4402-b949-b9439eb682e1`（DAILY **05:50**）
  （早前误删的两条已不可恢复，此为重建；宿主 cwds 统一为 `/Users/guan/WorkBuddy/automation-2026-09-21-13-02-04`）

### 变更
- 三槽 prompt 升 **v11s1/s2/s3**：`check_schedule "全网挖掘" --interval-hours 1` **＋ 新增 `done_schedule "全网挖掘" --interval-hours 1`**（死守卫修活）；正文三处同步互指 id；硬编码 `CLAW=/Users/guan/WorkBuddy/Claw` 不再依赖宿主注入
- 精读吞吐 15 → **≤45 篇/天**（3 槽 × `--max 15`），全部落在 Hy4 免费窗口
- registry：`automations[]` 补三槽并按时刻重排；`schedule_policy` 新增 `interval_lock_note`（`--interval-hours N` 锁在**完成时刻**写，故 N 须保证相邻触发点不同槽 —— 本例用 N=1 而非 N=2，否则 03:20 跑到 04:00 后会连坐锁死 05:50）；`day_lock_note` 重写为更正版
- registry 镜像校正（DB=真值）：季度巡检真值 = `马厩管理法-季度空间巡检` `52db25b4-…`（`YEARLY …BYHOUR=23;BYMINUTE=49`，原镜像误记 🐴/09:00）；`🩺 统一巡检·报错自诊与修复` = `DAILY 23:02`（原镜像误记 20:00）

### 文档
- `output/discovery-loop-upgrade-2026-09-24.md` 增补 §十二（含 §十一 错误结论的更正说明与当前夜间编排表）

## [2026-09-24] 统一巡检中枢 · 发现回路落地半程（Mode B P0）＋ 夜间免费窗口改点

### 新增
- `.workbuddy/scripts/apply_doc_candidates.py` — 文档类候选质量闸 + 校准账本（calibrate=只读对拍 / live=出计划，永不写 skill 文档）
- 自动化「📥 每日·文档类落地（校准期）」`1fd52465-899f-4525-a82d-4cc5df01155c`（DAILY 00:40，校准至 2026-10-08）
- `.workbuddy/inspection_hub/calibration/apply_dryrun_20260924.md` — 首份校准对拍报告

### 变更
- 「🛡️ 统一发现-每日扫描」07:30 → **00:20**；「📥 每日·文档类落地」07:45 → **00:40**（挪入 Hy4 夜间免费窗口 23:00→08:00；避开 23:0x 起始潮与周六 23:22 技能演进的 registry 读写竞态）
- `registry.json` 新增 `doc_apply` 配置块（mode/top_n/quality_gate/batch_rule/switch_criteria）与 `schedule_policy` 时段纪律块、`_state_sync_notes` 漂移登记
- `registry.json` `automations[]` 补新条目；rrule/next_run 按 scheduler DB 真值校正（技能演进→SA 23:22、技能卫生→1日 23:44）
- 同步纪律确立：**DB=唯一真值，registry=只读镜像**

### 文档
- `output/discovery-loop-upgrade-2026-09-24.md` 增补 §八 P0 实施记录、§九 校准期→生效期切换门槛、§十 时段调整
- `.workbuddy/docs/unified_inspection_hub_overview.md`、`skill_self_evolution_plan.md` 时段与节奏表同步

### 修复
- 调度改动前的 DB 备份方式纠正：`cp workbuddy.db` 会**漏掉 WAL 中未 checkpoint 的写入**（实测漏 1 行）→ 改用 `sqlite3 ... "VACUUM INTO"` 一致快照；已同步修订 `automation-rrule-safety-check` skill 的 Gate 2、并新增 Gate 4「改点槽位体检」
- 「🛡️ 统一发现-每日扫描」v1.1：修「假静默」——收尾原本**无条件**调 `push_feishu.sh` 且内容取 `/tmp/discovery_push.md`，跨天残留会把**昨日候选当今日推送**。现改为：判定前 `rm -f`、无候选不写文件不推送、收尾用 `[ -s ]` 守卫
- ~~「📚【知识库】全网挖掘+文章精读」v10：发现并修正「日锁空跑」~~ ⚠️ **该结论当日 16:10 被推翻** —— 见顶部 `[2026-09-24] 晚` 条目：该自动化**只 check 不 done**，「日锁」从未生效，原 6h RRULE 的 4 个槽**全部真跑**

### 变更（第二批）
- 「📚【知识库】全网挖掘+文章精读(LLM)」`HOURLY;INTERVAL=6` → **DAILY 00:50**（Hy4 夜间免费窗口内）
- `registry.json` `schedule_policy` 增补 `day_lock_note`（日锁语义与反模式）与知识库夜间槽位

## [2026-06-13] Phase 13: 配置模板

### 新增
- .env.example (行情/AI/飞书/交易参数/微信读书)

## [2026-06-13] Phase 11: CI 加固 + 类型安全 + 项目文档

### 项目文档
- 新增 README.md (功能模块/快速开始/项目结构/技术栈)

### 类型安全
- pyproject.toml: 添加 [tool.mypy] 渐进式类型检查配置
- CI: 添加 type-check (mypy) 步骤 (非阻塞)

### CI 加固
- 覆盖率阈值 30% → 50%
- CI 流水线: lint → type-check → test → security

### 开发体验
- Makefile: 添加 test-cov/type-check 目标
- pyproject.toml: 添加 [tool.coverage] 配置

## [2026-06-13] 全维度代码质量优化

### 安全修复
- backtest.py 空异常吞没 → 分类型日志记录
- expert_team_analyst.py 正则注入 → 非贪婪匹配

### 质量基础设施
- ruff 16组规则 + 582处自动修复
- pre-commit hooks (ruff lint/format + conventional commits)
- GitHub Actions CI (lint → test → security)
- Dependabot 依赖自动更新

### 测试
- 72个测试全部通过 (单元35 + 集成9 + 系统28)
- test_backtest.py: calc_ma/calc_highest/backtest_ma_cross/backtest_breakout/fetch_kline
- test_sim_trade.py: check_restricted/calc_commission/calc_stamp_tax/calc_total_asset/check_stop_loss
- test_integration.py: CLI端到端/买卖完整流程/止损触发

### Bug修复
- AtomicJSONWriter 并发写入竞态 → threading.Lock + 唯一临时文件名
- error_handler.py re-export 误删 → noqa:F401 保护

### 性能
- calc_ma: O(n²) → O(n) 滑动窗口
- calc_highest: O(n²) → O(n) 单调队列

### 开发体验
- Makefile: make setup/lint/test/ci 一键操作
- Python 3.12 标准化
