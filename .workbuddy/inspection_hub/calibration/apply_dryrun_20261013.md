# 文档类候选落地 · 校准报告 2026-10-13 00:40

- 模式：**live**（calibrate=只读零写入；live=产出待执行计划，由 agent 落地）
- 过闸并入选：3 条（top_n 限流后）
- 被滤除：73 条

## 本会改什么（入选）
1. `disc-20261012-01` · unified-inspection-hub · 价值=high / 风险=low
   - 目标锚点：**Claw** · 轨道=project_signal
   - 目标文件：`references/counting_trap_guardrails.md`
   - 成功指标：references/counting_trap_guardrails.md 新增『计数陷阱防护清单（行数下限断言+下游心跳+第三方配额核对）』小节，且被 inspection_hub 或对应 SKILL.md 引用
   - 来源：file:///Users/guan/WorkBuddy/Claw/.learnings/2026-10-09-signal-quota-starved-by-venv-and-three-counting-traps.md
   - 依据：Claw 采集器曾因 venv 配额吃光 + 三个『数出来的是假的』计数陷阱失真（P1 学习 2026-10-09）。外部研究 crosstowntech『Green Checkmark Lie』给出可落地防护分类：行数下限断言（rows>0 且 ≥预期*0.9 捕获空/部分）、schema/shape 校验、下游心跳（消费者确认收到新鲜数据）、dead-man's switch。将三者沉淀为 references/counting_trap_guardrails.md，并在 project_signal_collect.py 输出中强化 third_party_excluded 配额核对与行数下限自检，使『数出来的』可被证伪。
2. `disc-20261012-03` · majiu-management · 价值=high / 风险=low
   - 目标锚点：**meta** · 轨道=external_trend
   - 目标文件：`references/skill_output_contract.md`
   - 成功指标：references/skill_output_contract.md 新增『技能输出 schema 版本化契约 + 注册期依赖环检测』小节，且被 majiu-management SKILL.md 或 inspection_hub 引用
   - 来源：https://zylos.ai/research/2026-05-04-agent-workflow-composition-skill-reuse
   - 依据：zylos.ai（2026-05）指出技能版化的头号运营风险是『版本化缺口』：技能行为靠自然语言描述、无内置版本系统，更新即全局生效。新兴解法是把技能输出 schema 当作版本化 API 契约（semantic versioning for output structure）+ 注册期依赖环检测（cycle detection at registration）。补 majiu-management 治理未覆盖的『输出契约版本化 + 依赖环』维度。
3. `disc-20261013-01` · majiu-management · 价值=high / 风险=low
   - 目标锚点：**Claw** · 轨道=project_signal
   - 目标文件：`references/security_skill_authoring.md`
   - 成功指标：skills 基线 git 仓库经内容级凭据扫描（gitleaks/trufflehog 等效）零命中真实密钥；且入库/更新 SOP 内置『内容扫描』环节（不只靠 .gitignore 文件名匹配）；references/security_skill_authoring.md 新增『文件名忽略 ≠ 内容安全』小节并被 majiu-management SKILL.md 或 skills_vcs.sh 引用
   - 来源：file:///Users/guan/WorkBuddy/Claw/.learnings/2026-10-12-content-secret-survives-name-level-fix.md
   - 依据：Claw 学习（2026-10-12，P1·系统性）：给技能库加 GitHub 远端前做推送前凭据扫描，发现基线快照里一行『反面示例』粘进了真实 API Key，存活 3 天——根因是 .gitignore 只防文件名、防不住文件内容。外部 supply-chain 研究（itecs.ai / Snyk 13% 市场技能含严重漏洞）佐证：文件名忽略 ≠ 内容安全。把『内容级凭据扫描』接入 skills 基线推送前 SOP（gitleaks/trufflehog 等效），并沉淀为安全规范（references/security_skill_authoring.md，落在 majiu-management 治理技能下，与 skill-registry-security-scan.md 同源）。

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
- `disc-20261012-02` — risk=medium 高于阈
- `disc-20261013-02` — risk=medium 高于阈
- `disc-20261013-03` — top_n 限流（本批配额 3）

## v1.10 指标闸（新）
- 因**无目标锚点**被拦：0 条
- 因**无可判定指标**被拦：0 条
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
