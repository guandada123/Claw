# 文档类候选落地 · 校准报告 2026-10-14 00:40

- 模式：**live**（calibrate=只读零写入；live=产出待执行计划，由 agent 落地）
- 过闸并入选：3 条（top_n 限流后）
- 被滤除：77 条

## 本会改什么（入选）
1. `disc-20261013-03` · majiu-management · 价值=high / 风险=low
   - 目标锚点：**meta** · 轨道=external_trend
   - 目标文件：`references/skill_supply_chain.md`
   - 成功指标：references/skill_supply_chain.md 新增『技能供应链安全三件套（每次更新重新扫描 + 不可变版本钉版 append-only + 密码学签名/发布者校验）』小节，且被 majiu-management SKILL.md 或入库脚本引用
   - 来源：https://itecs.ai/insights/ai-agent-tool-poisoning-secure-tool-supply-chain
   - 依据：外部 supply-chain 研究（itecs.ai / mech.app：Snyk 13% 市场技能含严重漏洞；工具投毒可经描述/schema 绕过）：技能治理不能只靠一次性入库门禁。补 majiu-management 未覆盖的『供应链安全三件套』—— ① 每次更新重新扫描（非一次性门禁）；② 不可变版本钉版（append-only，版本不被覆盖）；③ 密码学签名/发布者校验。沉淀为 references/skill_supply_chain.md（与 skill-registry-security-scan.md / skill-version-pinning.md 同源）。
2. `disc-20261014-01` · unified-inspection-hub · 价值=high / 风险=low
   - 目标锚点：**Claw** · 轨道=project_signal
   - 目标文件：`references/write_read_path_split_brain.md`
   - 成功指标：references/write_read_path_split_brain.md 新增『写读异径防护（读路径必须 resolve 到与写路径相同的 canonical 路径）』小节，且被 unified-inspection_hub SKILL.md 或对应数据读写脚本引用
   - 来源：file:///Users/guan/WorkBuddy/Claw/.learnings/2026-10-13-write-read-dir-split-brain-silent-degradations.md
   - 依据：Claw 学习（2026-10-13，P1）：一个 parent.parent 缺陷遗留三处『写读异径』静默降级——数据写到 A 路径、却从 B 路径读，排名陈旧三周才被发现。外部 2026 静默失效研究（stridenote / clawdbytes：『HTTP 200 不代表任务真做了』『wrapper 记退出码、监控层高于被监控对象』）交叉验证：路径/状态读写分叉是静默失效高发地带。将『写读异径防护清单』沉淀为 references/write_read_path_split_brain.md（落在 unified-inspection-hub 治理技能下），并提示在相关 loader 加『读路径==写路径 canonical』断言。
3. `disc-20261014-02` · majiu-management · 价值=high / 风险=low
   - 目标锚点：**meta** · 轨道=external_trend
   - 目标文件：`references/skill_runtime_egress_guard.md`
   - 成功指标：references/skill_runtime_egress_guard.md 新增『运行时出口层守卫（egress DLP + 域名黑名单 + 云元数据默认拦截 + Ed25519 哈希链行为回执）』小节，且被 majiu-management SKILL.md 或入库脚本引用
   - 来源：https://pipelab.org/learn/skill-supply-chain-security/
   - 依据：外部 supply-chain 研究（pipelab / agentpatterns.ai，2026）：静态扫描会被『结构混淆 + 自解包』绕过（arxiv 2607.02357 Cloak and Detonate），因此需要在扫描层之外补『运行时出口层守卫』——出口 DLP 拦凭据外泄、域名黑名单 + 云元数据(169.254.169.254)默认拦截、Ed25519 哈希链签名行为回执作为审计证据。这是 disc-20261013-03（静态供应链三件套：重扫+钉版+签名）的运行时补偿层，非重复。本地单团队场景可降级为 hash pinning+代码评审，但须把『何时升级到出口层』的决策边界写进规范。

## 被滤除（质量闸）
- `disc-20260922-01` — status=applied 不在允许集
- `disc-20260922-02` — status=rejected 不在允许集
- `disc-20260922-03` — status=applied 不在允许集
- `disc-20260923-01` — status=applied 不在允许集
- `disc-20260923-02` — status=applied 不在允许集
- `disc-20260923-03` — status=rejected 不在允许集
- `disc-20260924-01` — status=applied 不在允许集
- `disc-20260924-02` — status=applied 不在允许集
- `disc-20260924-03` — status=applied 不在允许集
- `disc-20260925-01` — status=applied 不在允许集
- `disc-20260925-02` — status=applied 不在允许集
- `disc-20260925-03` — status=applied 不在允许集
- `disc-20260925-04` — status=applied 不在允许集
- `disc-20260925-05` — status=applied 不在允许集
- `disc-20260926-01` — status=applied 不在允许集
- `disc-20260926-02` — status=applied 不在允许集
- `disc-20260926-03` — status=applied 不在允许集
- `disc-20260927-01` — status=applied 不在允许集
- `disc-20260927-02` — status=applied 不在允许集
- `disc-20260927-03` — status=applied 不在允许集
- `disc-20260928-01` — status=applied 不在允许集
- `disc-20260928-02` — status=applied 不在允许集
- `disc-20260928-03` — status=applied 不在允许集
- `disc-20260929-01` — status=applied 不在允许集
- `disc-20260929-02` — status=applied 不在允许集
- `disc-20260929-03` — status=applied 不在允许集
- `disc-20260930-01` — status=applied 不在允许集
- `disc-20260930-02` — status=superseded 不在允许集
- `disc-20260930-03` — status=applied 不在允许集
- `disc-20261001-01` — status=superseded 不在允许集
- `disc-20261001-02` — status=applied 不在允许集
- `disc-20261001-03` — status=applied 不在允许集
- `disc-20261002-01` — status=applied 不在允许集
- `disc-20261002-02` — status=applied 不在允许集
- `disc-20261002-03` — status=applied 不在允许集
- `disc-20261003-01` — status=applied 不在允许集
- `disc-20261003-02` — status=applied 不在允许集
- `disc-20261003-03` — status=applied 不在允许集
- `disc-20261004-01` — status=applied 不在允许集
- `disc-20261004-02` — status=applied 不在允许集
- `disc-20261004-03` — status=applied 不在允许集
- `disc-20261005-01` — status=applied 不在允许集
- `disc-20261005-02` — status=rejected 不在允许集
- `disc-20261005-03` — status=rejected 不在允许集
- `disc-20261006-01` — status=rejected 不在允许集
- `disc-20261006-02` — status=applied 不在允许集
- `disc-20261006-03` — status=applied 不在允许集
- `disc-20261007-01` — status=rejected 不在允许集
- `disc-20261007-02` — status=rejected 不在允许集
- `disc-20261007-03` — status=rejected 不在允许集
- `disc-20261008-01` — status=rejected 不在允许集
- `disc-20261008-02` — status=rejected 不在允许集
- `disc-20261008-03` — status=applied 不在允许集
- `disc-20261009-01` — status=rejected 不在允许集
- `disc-20261009-02` — status=applied 不在允许集
- `disc-20261009-03` — status=rejected 不在允许集
- `disc-20261009-04` — status=applied 不在允许集
- `disc-20261009-05` — status=applied 不在允许集
- `disc-20261009-06` — status=applied 不在允许集
- `disc-20261009-07` — status=applied 不在允许集
- `disc-20261009-08` — status=applied 不在允许集
- `disc-20261010-01` — status=applied 不在允许集
- `disc-20261010-02` — status=rejected 不在允许集
- `disc-20261010-03` — status=applied 不在允许集
- `disc-20261009-09` — status=applied 不在允许集
- `disc-20261010-04` — status=applied 不在允许集
- `disc-20261011-01` — status=applied 不在允许集
- `disc-20261011-02` — status=applied 不在允许集
- `disc-20261011-03` — status=applied 不在允许集
- `disc-20261011-04` — status=applied 不在允许集
- `disc-20261012-01` — status=applied 不在允许集
- `disc-20261012-02` — risk=medium 高于阈
- `disc-20261012-03` — status=applied 不在允许集
- `disc-20261013-01` — status=applied 不在允许集
- `disc-20261013-02` — risk=medium 高于阈
- `disc-20261013-04` — 无可判定指标(success_metric) — 优化无法证真
- `disc-20261014-03` — top_n 限流（本批配额 3）

## v1.10 指标闸（新）
- 因**无目标锚点**被拦：0 条
- 因**无可判定指标**被拦：1 条
- 因**check_cmd 路径不存在**（指标无法证真）被拦：0 条
- 因**性能类缺数值基线**被拦：0 条
- 药方：在「🛡️ 统一发现-每日扫描」里给候选补 `target_project` + `success_metric`（可判定、可复核）后才进本通道；
  补不出指标的 → 在周度排序里落到 `Assess`（先评估）或 `rejected`，**不再默认落地**。
- check_cmd 请用**实际落地根**（`$HOME/.workbuddy/skills/...`）而非 Claw 侧路径；
  Claw 侧只对部分 skill 建了 symlink，写 Claw 路径会让命令恒失败、指标永远到期不了。
- 以**量化性能目标**（如「耗时下降 ≥50%」「<30s」）作验收的候选，必须自带 `baseline`
  （当前值 + 测量方法，**要带数字**）—— 没有对照值，既不能判断该不该改也不能验收。

## ⚠️ 弱退出码告警（仅上报，不拦截） —— 0 条
check_cmd 的契约是「退出码 0 = achieved」。以 `| head` / `| wc` / `|| true` 收尾时
退出码不再由判据决定 → 指标形同虚设（实例：`grep -rl … | head -1` 对不存在的字符串也 rc=0）。

## 说明
- 校准期（calibrate）本脚本**不写任何 skill 文档**，仅供对拍「本会改什么」。
- 生效期（live）由「📥 每日·文档类落地」自动化里的 agent 合成文档，每批 = 1 commit + 1 行 CHANGELOG + 1 个回滚点，top≤3。
- 核心类（SKILL.md 主流程 / 脚本 / 自动化 prompt）**永不**进本通道，恒走周度审。
