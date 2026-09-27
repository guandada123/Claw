# 文档类候选落地 · 校准报告 2026-09-27 00:40

- 模式：**calibrate**（calibrate=只读零写入；live=产出待执行计划，由 agent 落地）
- 过闸并入选：3 条（top_n 限流后）
- 被滤除：17 条

## 本会改什么（入选）
1. `disc-20260927-01` · majiu-management · 价值=high / 风险=low
   - 目标：`references/company-skill-registry.md`
   - 来源：https://luismori.dev/article/how-to-build-company-skill-registry-agent-skills
   - 依据：强化 majiu-management 的「治理注册表」职责：补 ownership/版本钉固/审批/回滚/安全审查维度到 references/company-skill-registry.md（文档类 auto_apply，不触碰核心流程）；可直接映射到本中枢 registry.json 这一私有 registry 雏形。
2. `disc-20260927-02` · a-stock-data · 价值=high / 风险=low
   - 目标：`references/stoke-rate-limit-healthcheck.md`
   - 来源：https://github.com/birdilsss-byte/stoke
   - 依据：对标本地 a-stock-data 的「数据层健壮性」：评估补『per-source health_check + 内置限流 + 随机抖动』封装范式（本地 skill 缺独立健康探测与限流纪律，易静默空数据/被封IP）；写 references/stoke-rate-limit-healthcheck.md 兼容性对比（文档类 auto_apply）。
3. `disc-20260927-03` · majiu-management · 价值=medium-high / 风险=low
   - 目标：`references/skill-quality-eval-gate.md`
   - 来源：http://www.jxxy.net/ai/articles/ah-google-agent-skills-build-test-scale
   - 依据：强化 majiu-management 的「质量门禁/零幻觉链接」治理：引入 EVAL.yaml 评估套件 + CI 链接检查(lychee) + 持续评估闭环到 references/skill-quality-eval-gate.md（文档类 auto_apply）。直接对应 registry known_failure_modes.ff-hygiene-falsepos（坏链/幻觉链接）。

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

## 说明
- 校准期（calibrate）本脚本**不写任何 skill 文档**，仅供对拍「本会改什么」。
- 生效期（live）由「📥 每日·文档类落地」自动化里的 agent 合成文档，每批 = 1 commit + 1 行 CHANGELOG + 1 个回滚点，top≤3。
- 核心类（SKILL.md 主流程 / 脚本 / 自动化 prompt）**永不**进本通道，恒走周度审。
