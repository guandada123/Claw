# 文档类候选落地 · 校准报告 2026-09-25 00:40

- 模式：**calibrate**（calibrate=只读零写入；live=产出待执行计划，由 agent 落地）
- 过闸并入选：3 条（top_n 限流后）
- 被滤除：7 条

## 本会改什么（入选）
1. `disc-20260924-01` · majiu-management · 价值=high / 风险=low
   - 目标：`references/workspace-governance-patterns.md`
   - 来源：https://github.com/mars2003/workspace-governance/blob/main/SKILL.md
   - 依据：强化 majiu-management 的『边界优先+可逆优先+非交互安全』治理框架：引入 SKILL_ADAPT 适配覆盖层与 immutable/protected 声明、非交互环境 blocked 输出范式；落到 references/workspace-governance-patterns.md（文档类 auto_apply）
2. `disc-20260924-02` · a-stock-data · 价值=high / 风险=low
   - 目标：`references/quant-skills-ecosystem.md`
   - 来源：https://github.com/lzwme/finance-quant-skills
   - 依据：对标 a-stock-data 的数据+工具广度：评估是否补『回测(Backtrader/RQAlpha)/策略(AKQuant/MiniQMT)/QMT文档』维度；写 references/quant-skills-ecosystem.md 能力对比矩阵（文档类 auto_apply，不改动核心 SKILL.md/脚本）
3. `disc-20260924-03` · majiu-management · 价值=medium-high / 风险=low
   - 目标：`references/skill-version-lock-eval.md`
   - 来源：https://www.alphalab.site/agent-skill-lifecycle + https://graphwiz.ai/content/ai/agent-skill-management
   - 依据：强化 majiu-management 版本/生命周期治理：引入 skills.lock.yaml 锁版+with/without Eval 闭环、风险分级审批框架；落到 references/skill-version-lock-eval.md（文档类 auto_apply）

## 被滤除（质量闸）
- `disc-20260922-01` — status=applied 不在允许集
- `disc-20260922-02` — status=proposal_new_capability 不在允许集
- `disc-20260922-03` — status=applied 不在允许集
- `disc-20260923-01` — status=applied_doc_proposed_core 不在允许集
- `disc-20260923-02` — status=applied 不在允许集
- `disc-20260923-03` — status=proposal_new_capability 不在允许集
- `(+3 条过闸但被 top_n 限流)` — top_n

## 说明
- 校准期（calibrate）本脚本**不写任何 skill 文档**，仅供对拍「本会改什么」。
- 生效期（live）由「📥 每日·文档类落地」自动化里的 agent 合成文档，每批 = 1 commit + 1 行 CHANGELOG + 1 个回滚点，top≤3。
- 核心类（SKILL.md 主流程 / 脚本 / 自动化 prompt）**永不**进本通道，恒走周度审。
