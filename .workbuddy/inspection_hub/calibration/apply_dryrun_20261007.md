# 文档类候选落地 · 校准报告 2026-10-07 00:41

- 模式：**calibrate**（calibrate=只读零写入；live=产出待执行计划，由 agent 落地）
- 过闸并入选：3 条（top_n 限流后）
- 被滤除：44 条

## 本会改什么（入选）
1. `disc-20261004-01` · majiu-management · 价值=high / 风险=low
   - 目标：`references/skill-effectiveness-benchmark.md`
   - 来源：https://github.com/benchflow-ai/skillsbench
   - 依据：借鉴为 majiu-management 的『技能效能基准』：把 with_skill/without_skill A/B 度量法 + oracle-先过-verifier 闸门 落成 references/skill-effectiveness-benchmark.md（文档类 auto_apply，不改动核心 SKILL.md）；补既有 eval/安全候选缺的『可量化增益』维度（与安全扫描互补：一个管质量，一个管安全）。
2. `disc-20261004-02` · majiu-management · 价值=high / 风险=low
   - 目标：`references/skill-source-trust.md`
   - 来源：https://www.agensi.io/learn/best-ai-agent-skills-marketplaces-2026
   - 依据：借鉴为 majiu-management 的『来源信任分级 + 安装审计闸门』：把 8 点安全扫描清单 + ClawHavoc 实证(36% 注入/7.1% 严重漏洞) 落成 references/skill-source-trust.md（文档类 auto_apply，不改动核心 SKILL.md）；与 disc-20260929-02(OWASP AST10) / disc-20260928-01(ESR 安全扫描) 互补——本源提供『市场级实测 prevalence + 可勾选扫描清单』，后者提供『风险分类框架 + 工具』。
3. `disc-20261005-01` · majiu-management · 价值=high / 风险=low
   - 目标：`references/skill-package-manifest.md`
   - 来源：https://github.com/agentskills/agentskills/discussions/210
   - 依据：借鉴为 majiu-management 的『技能包清单』：把 skills.json(声明依赖/版本/分发) + skills.lock(钉版校验) 落成 references/skill-package-manifest.md（文档类 auto_apply，不改动核心 SKILL.md）；与 disc-20260925-02(gh skill 钉版+溯源) / disc-20261003-01(zooz manifest owner/pin/scope) 互补——本源提供『跨仓库依赖解析 + lockfile 可复现』的标准提案，后者提供『发布/钉版 CLI』。

## 被滤除（质量闸）
- `disc-20260922-01` — status=applied 不在允许集
- `disc-20260922-02` — status=proposed 不在允许集
- `disc-20260922-03` — status=applied 不在允许集
- `disc-20260923-01` — status=proposed 不在允许集
- `disc-20260923-02` — status=applied 不在允许集
- `disc-20260923-03` — status=proposed 不在允许集
- `disc-20260924-01` — status=applied 不在允许集
- `disc-20260924-02` — status=applied 不在允许集
- `disc-20260924-03` — status=applied 不在允许集
- `disc-20260925-01` — status=applied 不在允许集
- `disc-20260925-02` — status=applied 不在允许集
- `disc-20260925-03` — status=applied 不在允许集
- `disc-20260925-04` — status=proposed 不在允许集
- `disc-20260925-05` — status=proposed 不在允许集
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
- `disc-20260930-02` — status=applied 不在允许集
- `disc-20260930-03` — status=applied 不在允许集
- `disc-20261001-01` — status=applied 不在允许集
- `disc-20261001-02` — status=applied 不在允许集
- `disc-20261001-03` — status=applied 不在允许集
- `disc-20261002-01` — status=applied 不在允许集
- `disc-20261002-02` — status=applied 不在允许集
- `disc-20261002-03` — status=applied 不在允许集
- `disc-20261003-01` — status=applied 不在允许集
- `disc-20261003-02` — status=applied 不在允许集
- `disc-20261003-03` — status=applied 不在允许集
- `disc-20261004-03` — 无文档落地目标(references/README/CHANGELOG)
- `disc-20261005-02` — 无文档落地目标(references/README/CHANGELOG)
- `disc-20261007-01` — 无文档落地目标(references/README/CHANGELOG)
- `disc-20261007-02` — 无文档落地目标(references/README/CHANGELOG)
- `disc-20261007-03` — 无文档落地目标(references/README/CHANGELOG)
- `(+4 条过闸但被 top_n 限流)` — top_n

## 说明
- 校准期（calibrate）本脚本**不写任何 skill 文档**，仅供对拍「本会改什么」。
- 生效期（live）由「📥 每日·文档类落地」自动化里的 agent 合成文档，每批 = 1 commit + 1 行 CHANGELOG + 1 个回滚点，top≤3。
- 核心类（SKILL.md 主流程 / 脚本 / 自动化 prompt）**永不**进本通道，恒走周度审。
