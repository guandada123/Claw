# Changelog

## [2026-09-25 上午·D2/D3 收口] 148 项未提交逐仓清完 + D9 同组槽位配置一致性

### D2 逐仓 review：不是「一堆没提交的文件」，是三类不同的东西
89(Claw) / 35(QTS) / 16(StockInsight) / 4(marvis) / 3(pmf) / 1(eak) —— 逐仓看清后才敢动：
- **Claw** = 09-23 双副本去重**未收尾**（薄壳/删除/路径解析/测试契约）→ 拆提交收口
- **QTS** = 真实 WIP（data_fetcher/data_quality/report_service + 大量测试）→ **先跑测试**：1344 passed
- **StockInsight** = 13 个 08-30~09-11 memory 日日志未提交（与「记忆层 09-11 后闲置」互相印证）
- **pmf** = `routes/console.php` **删 3 处 `->runInBackground()`**，配对 learning「runInBackground 静默杀掉三个定时任务」→ 是修复，不是垃圾
- **marvis** = LEARNINGS + 新 memory + docs；**2 个 `.DS_Store` 被跟踪** → `git rm --cached` + gitignore
- **eak** = 只有 `.DS_Store` → gitignore，无内容可提交

### 四个关键发现（都是「看着在跑、其实没在跑」同族）
- ❌【高】**`anysearch_helper` 真断链**：📊微信早报(1782741941693) 与 📊收盘晚报(1782817769722)
  的 prompt 写的是 `import sys; sys.path.insert(0,'scripts')` + **裸** `import anysearch_helper` ——
  这种形态**不含** `scripts/X.py` 字面串，于是 `restore_forwarders.py` 与 `check_broken_refs.py`
  **双双报「✅ 健康」**，而实际 import 直接 `ModuleNotFoundError` → 两份报告静默降级。
  → 新增 `find_syspath_bareimport_breaks.py` + 在 `check_broken_refs` 末尾**委托**调用（单一实现）+
  补 `scripts/anysearch_helper.py` 薄壳。**证伪实验**：移走薄壳 → rc=1 并点名 2 条自动化；恢复 → rc=0。
  **盲区不是"没查"，是查了一个自以为完备的子集（只覆盖路径式引用，漏了 sys.path 注入式）。**
- ❌【中】**Claw 单测红灯**：`test_load_holidays_from_file` 断言旧契约（裸 set），
  而 `load_holidays` 09-25 已升级为 `{'dates','names'}`（为区分中秋/国庆）→ 同步契约并**补 2 条**
  钉住新行为 → **503 passed**（此前 1 failed / 500 passed）
- ❌【中】**QTS 测试因写死日期腐烂**：`TestLocalDbFreshnessTruncation` 用写死的 `2026-09-04`，
  而判据是 `effective >= today - 5天` → 09-09 之后必然转红，**与代码无关**。
  → 改相对今天。**写死日期的测试就是一条会腐烂的声明**：它不再验证"新鲜/截断"，
  只在验证"今天离写测试那天有多远"
- ❌【高】**拆槽会静默重置配置**（见下 D9）

### D3 → 新增 D9 同组槽位配置一致性（registry 1.6.0 / SKILL.md v1.6.0）
铁律「单 RRULE 禁多 BYHOUR」要求多时刻**拆成多条独立自动化**，而 `automation_update`
**改不了** `model_id` / `model_is_thinking` / `expert_id` / `permission_mode` / `push_to_wechat`
→ **拆槽时新建的那几条只会拿到平台默认值**。实测知识库精读三槽（prompt 自述「共用同一份正文」）：
槽1（06-22 建）= hy3+thinking+EquityResearchExpert，槽2/3（09-24 16:03 同一次拆槽）= 默认 flash+无专家
→ 同一件事每天 2/3 的产出被静默降档。**不是配置疏漏，是拆分动作的系统性副作用 —— 拆一次复发一次。**

- 判据来自 `registry.consistency_groups[]`（成员 `ids` + 必须一致的 `keys` +
  `keys_intentionally_differing`）；**有意差异也必须声明**（同 D5 K3 排除规则那条纪律）
- 一个键报**一条**（差异成员并列写一行）：逐成员各报一条 = 同一件事拆成 N 条噪音 →
  **报告粒度要对齐人的决策粒度**
- 退化形态不许静默：`group_no_keys` / `group_member_missing` / `group_too_small`（存活成员 <2）
- 七连测 **7/7**（一致→静默 / 三键不一致→恰好 3 条 / 单键→恰好 1 条 / 有意差异已声明→静默 /
  成员 ID 打错→报 / 只剩 1 条→报 / 没声明键→报）
- 记入 `known_failure_modes: slot-split-resets-config`；diag prompt → **v2.5**（逐字节校验）
- PA-003：`if_no_action` keep_as_is → **escalate**（不动 = 每天 2/3 精读继续用弱模型，有实际代价，
  不适合表述为"无害保持现状"）；修复动作**只能在自动化管理 UI 做**（工具层改不了这四个字段）

### 结果
`hub_reconcile`：D1–D7 = 0 / **D8 = 1**（H1 实盘止损）/ **D9 = 3**（槽位配置）→ rc=20；
`check_broken_refs` rc=0；Claw pytest **503 passed**；QTS pytest **1344 passed**


## [2026-09-24 晚·跨项目闭环] D8 把"7 个项目是否都闭环"变成机器可判定 —— 顺带抓出状态锚自己在说谎 6.4 天

### 起因
用户要求"统计目前所有项目，所有项目都要闭环"。问题是：**"闭环"此前只是一句声明** ——
D1–D7 全部只覆盖 Claw 一个项目，7 个项目的闭环状态写在 `~/.workbuddy/cross_project_state.json`，
而那份文件**不在任何机器检查的覆盖范围内**。

### 新增 D8 跨项目闭环（`hub_reconcile.py` K1–K6）
- 权威清单 = `active_projects`（7 条）；磁盘实际 git 仓库 8 个（`wechat_api`/`wechat-download-api` 同一 entry 承载两仓，
  用 `cwds` 声明）→ 8/8 一一对应，无漏项
- K1 `project_cwd_missing` / K2 `project_unregistered`（**按 realpath 去重**，`~/WorkBuddy` 整树是软链）/
  K3 `coverage_gap`（休眠项也必须显式声明 runtime=none 的理由）/ K4 `updated_at` 三态 /
  K5 `handoff_overdue` / K6 `handoff_relative_time`·`handoff_unstructured`·`handoff_no_due`
- 新开关 `--cross-state`（默认指向真实锚，故**默认就在跑**，不是"要手工加参才生效"的摆设）

### D8 首跑抓出的真问题（全部已修）
- ❌【高】**状态锚的"最后更新"在说谎 6.4 天**：`updated_at` 停在 09-18，而文件每天都真在被改（mtime 09-24 19:30）——
  写者（`unified_ops_center._sync_state_anchor`）只更新子节点不 bump 顶层。→ 补"谁写谁 bump"（已用副本验证：写→bump / dry_run→不写 / 真文件未动）
- ❌【高】**一条 50 天前就该到期的交接项没人知道它到期了**：`handoff` 原为自由文本 + 相对时间（"明日开盘前"），
  写进文件那一刻就不可判定。→ 重构为 `items[]`（`id`/`owner`/`due`(ISO)/`status`/`escalate`/`evidence`/`why_not_auto`），
  旧自由文本保留为 `_legacy_next_session_action` 并附腐烂说明；**没有替用户"清掉"这条，而是让它每天被 D8 报出来**
- ❌ `pmf` 的 note 与事实矛盾（写"不配看门狗"，实测 docker 5 容器 Up 8 天）→ 删矛盾叙述，改记 runtime 实测
- ❌ `wechat-download-api` 曾被误报"未登记"（entry 只带一个 `cwd` 而目录下有两个仓）→ 支持 `cwds`
- ❌ `marvis_bridge` / `wechat_api` 无记忆层 → 建 `.workbuddy/memory/2026-09-24.md` 闭环快照
- ❌ `_contract.closure_rules` 缺失（闭环的判据本身没写下来）→ 补 6 条 + `why`

### D8 自查：新检查的第一条判据必须是"输入自证"（第四次踩同一个坑）
D8 初版只写 `if not cs:` 就报"**状态锚不存在**"—— 文件明明在、只是内容为 `{}` 时**输出在说谎**；
更危险的是把"读不出来"和"没有跨项目问题"混成同一个结果。→ 拆成五种各报各的：
`state_anchor_missing` / `state_anchor_unreadable` / `state_anchor_empty` / `no_active_projects` /
`scan_root_missing`（`~/WorkBuddy` 不可访问时 K2 一条都不查，须明写"本次 D8=0 不代表没有漏项"）。
六连测（不存在 / 空 / 坏 JSON / active_projects 空 / 扫描根缺失 / 真实锚）**6/6 PASS**。

### 顺带修掉的自身缺陷
- `hub_reconcile` 的 `rc` 此前**只作为进程退出码存在，JSON 里没有这个键**，而文档和告警都在说"看退出码" →
  消费方读 `out["rc"]` 会缺键（又一次"缺键 ≠ 空集"）→ **rc 现在与退出码同源写进 JSON**（四场景验证一致）
- `D5 K3` 表校验的排除项**声明化**：新增 `table_keys_expected_exclude_prefixes: ["_"]`，
  `_` 前缀（`_history`/`_state_sync_notes`）= 元数据而非配置。**排除规则必须在 registry 里声明，不许硬写进代码** ——
  硬写就是"看着在查、其实默默少查一批键"
- **记忆层是同一事实的第三份副本，同样会腐烂**：`MEMORY.md` 还写着"不设自动切换(`auto_switch=false`)"，
  而 registry 真值早已是 `true`（PA-001 守卫式自动切换）。D7 只核 registry 内部自洽，管不到这里。
  → 本文件里"状态/开关"类事实改为**只写指针**，不复述具体值

### 结果
`hub_reconcile --cross-state` → **rc=20，D1–D7 全 0，D8 = 1（唯一那条真逾期项）**；
registry **1.5.0**、SKILL.md **v1.5.0**（D8 加 K0 输入自证、K3 排除声明化）；报告 `output/all-projects-closure-2026-09-24.md`

### 留给人的只有一件事
**H1 实盘止损**（`owner: human`，`due: 2026-08-05`，逾期 50 天）：长电 600584 / 华天 002185 双仓击穿 -8% 止损仍未清仓。
三选一：执行 / 作废该纪律 / 挂起并给 ISO 复核日。**机器只负责让它每天都出现，不替人做决定。**


## [2026-09-24 晚·三次审计] 检查器"空转"的三种形态 —— 切换守卫可被错误放行 / 看门狗会静默死 / 声明清空=clean

### 方法
① 把二次审计 7 项修复逐条当"待证伪断言"，每条配造错样本；
② 换攻击轴：不问"检测对不对"，问"**声明空了 / 输入没了 / 错误码被误读**会怎样"；
③ 端到端（真实脚本 + 真实 launchd + 真实库，非 mock）。

### 二次审计修复复核：8/8 全部真实有效（含 `--fix` 不动库、不动文档的指纹比对）

### 新发现（4 类高危 + 3 类中危）
- ❌【高危】**切换守卫可被"错误"放行**：`doc_apply_parity.py` 读不出 registry 时 `return 0`（=没有越界），
  payload 里**连 `violations` 键都没有**，而守卫判据是 `violations == []` / `days_elapsed >= days_needed` →
  registry 一损坏，"读不到"会被读成"没有越界"，**可能把生产 mode 切成 live**。
  → parity 输入不可信改 **rc=2 且所有可求值键置 `null`**；`switch_guard.preconditions` 两条→**三条（首条 `parity.ok == true`）**；
  落地 prompt v1.3 明确"先看退出码"。铁律：**错误 ≠ 通过；缺键 ≠ 空集**
- ❌【高危】**外部看门狗自己会静默死**：第 5 节合取条件任一不成立就**整节无声跳过**（无日志无告警）；
  且 `rc<20` 一律记"正常"→ 对账器崩溃(rc=1)/用法错误(rc=2) 都被当成健康（实测三种输入缺失 + rc=1 全静默）。
  → 输入缺失显式告警（列出缺哪个，24h 冷却）；退出码改**白名单**（0/10/20，其余=执行异常→告警）
- ❌【高】**声明被清空 = clean**：`automation_scope` 删掉 → D3 恒 0；`doc_contract.docs` 为空 → D5 恒 0；
  契约文档"登记了但判据全空" → 什么都没查。→ 新增 **D7 契约盲区**（检查器自查）；
  **D7 首跑即从自己 registry 里抓出 6 条真问题**
- ❌【高】**同一事实多副本互相矛盾**：SKILL.md 同时写着"跃迁必须人点头"与 `auto_switch=true`；
  PA-001 `default_if_no_action` 自由文本写"保持 calibrate"而真值是自动切 live；diag prompt 的 D5 清单/判据条数已过期。
  → 文档改写为"人在环 **或** 有守卫的自动"；`default_if_no_action` → **`if_no_action` 枚举 + D7 交叉核对**；两份 prompt 同步
- ❌【中】`apply_doc_candidates.py` 读不出 registry 也 `return 0`（"没跑成"会被当作"跑完了没事"）→ rc=2
- ❌【中】`ledger_vs_registry_gaps` 只报不拦 → **升级为 blocker**（证据不完整不放行切换）
- ❌【中】D5 K1 遇多槽 rrule 只校第一个时刻 → 新增 `anchor_multi_slot` 显式报出

### 判定为"不是缺陷"的两处（避免误修）
- 6 条外部依据 URL 逐条实测 200 且内容相符（防幻觉纪律成立）；探链应做**月度**而非每日（网络抖动会狼来了）
- D1 对看门狗不可见属有意设计（owner = 23:02 自诊 `--fix`；自诊若死由 D4 覆盖）→ 把"交给谁"写进日志文案

### 结果
`hub_reconcile` clean=True、**D1–D7 全 0**、rc=0；独立核验台 **30/30**；SKILL.md → **v1.3.0**（反模式 16 / 检查点 16）；
land **v1.3**、diag **v2.3**；报告见 `output/discovery-loop-upgrade-2026-09-24.md` §十九


## [2026-09-24 晚·二次审计] 审计"我修完之后新引入的缺陷" —— 3 处死守卫同族 + 证据链污染

### 方法（刻意不复用被审对象的判断）
独立脚本直连调度库复算（不 import `hub_reconcile`）；对每个"已修/已建"都设计**能证伪它的实验**。

### 独立真值复核
- registry ↔ 调度库逐条（含 name）**25/25 全一致**；scope 覆盖 71 活跃 / **0 未登记**；无状态字样残留 ✅
- ❌ `registry.version` 仍 **1.0.0**（跨 4 次改动没动过）→ 已修（见下）

### 找到的缺陷（全部是"死守卫"同族）
- ❌ **`--doc` 覆盖回归**：多文档契约迁移后，覆盖项只继承顶层默认，锚点/键名清单全丢 →
  取证通道**静默失效**（修前用"故意写错的文档"测 → findings=0；修后 → 命中 time_mismatch）。
  **死守卫第四例**：形态上是一条可用通道，实际恒放行
- ❌ **`apply_doc_candidates.py --mode live` 能翻转生产 mode**：副本实测 mode 变 live。
  一条观测用参数能**提前打开"会自动写 skill 文档"的开关且不留痕**
- ❌ **同 Bug 第二处面：污染证据链** —— 那次试跑还往权威账本 `parity.json` 写了一条**伪造的 `20260924:live` 记录**，
  并覆盖当日校准报告。而 `parity.json` 正是 10-08 切换决策的**证据**
- ❌ 文档引用 `autonomy_methods` 但 registry 无此键（悬空引用）
- ❌ D4 会对 PAUSED 的已声明自动化持续报心跳超时（潜伏误报）

### 端到端验证（不接受"代码看起来对"）
- launchd `com.workbuddy.wb-health-check` 已加载，`StartInterval=1800` ✅
- 用 **rc=20 的假对账器跑真脚本** → 告警分支触发（`⚠️ 中枢心跳/声明漂移异常(2 项)`）；第二次 → 6h 冷却 suppressed ✅
- 生产日志有真实运行 `== 中枢对账正常(rc=0) ==` ✅
- **回滚点 `0540a62` 真实存在**（`git cat-file -t` = commit）→ 一键回滚不是谎话 ✅

### 修复
| # | 缺陷 | 处置 |
|---|---|---|
| 1 | `--doc` 覆盖丢设置 | 覆盖项**沿用主文档完整契约设置**；文档写明该姿势 |
| 2 | `--mode` 回写生产 mode | 显式 `--mode` 时**不回写 registry**，stderr 提示切 mode 的正途（1c 或人工） |
| 3 | 试跑污染账本/报告 | `--mode` 时不写权威账本、报告写 `_probe` 后缀；删除伪造条目并留注 |
| 4 | `version` 落后 | 1.0.0 → **1.2.0**；新增 **K5 版本对**（文档 frontmatter `version` ↔ `registry.version`，逐文档开关 `version_check`） |
| 5 | `autonomy_methods` 悬空 | 落成真键（阶梯 + 当前档位 + 升级所需证据），并入文档注册表表 |
| 6 | D4 对 PAUSED 误报 | D4 跳过非 ACTIVE |
| 7 | 三槽 model/expert 差异 | **PA-003**（工具改不动 → 交人在 UI 决定） |

### 判定：一处"看起来是缺陷、实际不是"
- 知识库槽 2/3 的 `permission_mode=NULL` **不是缺陷** —— 既有中枢自动化（统一发现 / 文档类落地 / 技能演进）
  同为 NULL 且已稳定运行。**真问题是 model/expert 差异**（同任务三槽，产出质量可能不一致）→ PA-003

### 自测
```
D5/D6 故障注入 8 项全过 ✅（含新增：--doc 覆盖沿用契约设置、K5 版本对一致）
对账 clean=True | D1..D6 全 0 | rc=0        py_compile / bash -n OK
真实 registry 未被测试污染：mode=calibrate / live_runs=None / last_live=None
```

### 审计者自身的错（也记一笔）
- 改 `apply_doc_candidates.py` 时 `old_string` 含 `sel, dropped = select(...)` 而 `new_string` 漏带 → **整行被删**；
  `py_compile` **不报错**（NameError 是运行时的）→ 靠"真跑一次"才发现。**compile 通过 ≠ 能跑**
- 审计脚本自己写错两次 → **先怀疑测试再怀疑代码；也别为了让测试变绿去改代码**

### 结论
第一轮审计找"设计缺陷"；第二轮找到的全是**"修完之后新引入的缺陷"**，其中一个用的正是我刚总结的同一条铁律。
→ **审计必须包含"对上一次修复的复核"，否则修复本身会成为新的腐烂源。**

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
