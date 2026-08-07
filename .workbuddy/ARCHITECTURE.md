# Claw 五层架构配置 (2026-08-07 落地)

> 对齐「WorkBuddy 五层架构配置手册」标准结构
> 核心定位：以本地文件操作能力为核心，融合记忆、技能、专家、远程助理、定时自动化、IMA 知识库联动全套餐能力

## 目录结构

```
workbuddy-codex/  →  /Users/guan/WorkBuddy/Claw/
├── .workbuddy/
│   ├── memory/              # L1: 记忆层 Memory Layer
│   │   ├── architecture.rules # 架构索引+铁律总入口（对齐文章规范）
│   │   ├── MEMORY.md        #   项目铁律+技术决策（FACT层）
│   │   ├── SCHEMA.md        #   L5行为规律
│   │   ├── INTENT.md        #   L6前瞻意图
│   │   ├── CHRONICLE.md     #   编年史
│   │   └── YYYY-MM-DD.md    #   RAW+SUMMARY 日日志
│   ├── skills/              # L2: 知识层 Knowledge Layer (38个symlink)
│   │   ├── */               #   → ~/.workbuddy/skills/* (Claw专属)
│   │   └── templates/       #   输出模板（早报/巡检卡/架构文档）⬅️ NEW
│   ├── hooks/               # L3: 护栏层 Guardrail Layer ⬅️ NEW
│   │   ├── pre-task.sh      #   4 Gate: RRULE安全/ID格式/交易日/memwatch风险
│   │   └── post-task.sh     #   失败分类/告警判定/JSONL执行日志
│   ├── scripts/             #   自动化脚本（preamble/监控/推送）
│   ├── data/                #   数据文件（portfolio/行情/池）
│   └── automations/         #   自动化记忆（per-automation）
├── src/claw/debate/          # L4: 委派层 Delegation Layer (7专家框架)
│   ├── run_debate.py
│   ├── debate_engine.py
│   └── expert_prompts.py
├── scripts/                  #   主脚本入口
├── tests/                    #   测试套件
└── output/                   #   输出产物
```

## L1 记忆层 — 对齐文章规范
- **architecture.rules** (NEW): 架构索引 + 不可违反铁律总入口，映射文章 `architecture.rules/global.md/project.md` 三元组
- **MEMORY.md**: FACT层（铁律+技术决策），80行，>120行才蒸馏
- **SCHEMA.md**: 行为规律（用户决策风格/技术债优先级/协作模式）
- **INTENT.md**: 前瞻意图与待决看板
- **CHRONICLE.md**: 编年史（重大决策时间线）
- **日日志**: 15天滚动，>30天蒸馏进分层
- **检索协议**: L1-L3零Token（Grep→Read→数据三层）

## L2 知识层 — 下沉完成 + 模板补全 + 三级分类
- **用户级保留** (~25): find-skills, humanizer, excel-xlsx, pdf-analyzer, obsidian 等通用skill
- **项目级 symlink** (38): a-stock-data, automation-*, backtest-*, breakout_*, earnings-*, feishu-*, market_*, trading-*, wind-*, westockdata 等 Claw 专属
- **templates/** (NEW): morning_report.md / watchdog_alert_card.md / architecture_doc.md / **automation_contract.md** — 统一输出格式锚点
- **三级分类** (对齐文章模块1): 原子(通用积木) / 流程(专用场景) / 角色(岗位边界) — 详见 `memory/architecture.rules` 第五节

## L3 护栏层 — 新建 ✅
| Hook | Gate | 功能 | 自检 |
|------|------|------|------|
| pre-task.sh | G1 | RRULE 多值 BYHOUR 拦截 | ✅ |
| pre-task.sh | G2 | automation-ID 前缀校验 | ✅ |
| pre-task.sh | G3 | 非交易日交易类跳过 | ✅ |
| pre-task.sh | G4 | memwatch 高危窗口警告 | ✅ |
| post-task.sh | - | 失败分类(HARD_KILL/REFUSAL/TIMEOUT) | ✅ |
| post-task.sh | - | 关键自动化告警判定 | ✅ |
| post-task.sh | - | JSONL 执行日志 | ✅ |

## L4 委派层 — 待规范化 (P2)
- 当前: `src/claw/debate/` (run_debate.py + 7专家)
- 目标: 迁移至 `.workbuddy/experts/` + reviewer.yaml 定义

## L5 分发层 — 暂缓 (P3)
- SkillPack/manifest.json 按需创建

## 铁律索引
- 🔴 RRULE 单条禁多BYHOUR → 拆多条单BYHOUR
- 🔴 删文件须先复制验证再删源+确认
- 🔴 实时价走腾讯 qt.gtimg.cn
- 🔴 自动化调LLM走本地代理 :9999
- 🛡️ Hooks 层为最后防线（不替代人工审查）

---

## 附录：文章「WorkBuddy 高阶玩法」9 模块对齐

> 来源：公众号「器用之间」《WorkBuddy 的高阶玩法》(2026-07-10)。以下为 Claw 对文章方法论的吸收度自检。

| # | 文章模块 | Claw 落地 | 状态 |
|---|---------|----------|------|
| 1 | Skill 分层（原子/流程/角色） | `architecture.rules` 第五节三级标准 + ARCHITECTURE L2 标注 | ✅ 本次补 |
| 2 | 自建 MCP（受控工牌） | 已接微信/通达信/腾讯自选股/飞书/westock；凭证走 `.env` 不进提示词 | ✅ |
| 3 | 多 Agent 边界（3-5 个） | 7 专家辩论 + 边界写死 + 统一输出 | ✅ |
| 4 | 自动化矩阵（流程图/失败保护/有效期） | `templates/automation_contract.md` 五要素契约 | ✅ 本次补 |
| 5 | 专家接力 + 交接标准 | manifest.yaml 七专家 + 统一 JSON + synthesis 汇总 | ✅ |
| 6 | 代码/数据交付链路（Ask→Plan→Craft） | 已用；增量开发+测试 | ✅ |
| 7 | Computer Use 安全区 | Claw 无桌面操作需求，不适用 | ⏭️ 标注 |
| 8 | 权限三层治理 + 定期审查 | 铁律 + `architecture.rules` 第六节审查清单 | ✅ 本次补 |
| 9 | 系统思维（人设计/AI执行/人验收） | L1-L5 全架构建模此范式 | ✅ |

**本次优化吸收**：模块1（Skill 三级）、模块4（自动化契约）、模块8（权限审查）三项原缺失，已补进 `architecture.rules` + `ARCHITECTURE.md` + 新模板。
