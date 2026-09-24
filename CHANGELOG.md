# Changelog

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
