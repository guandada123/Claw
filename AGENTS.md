# AGENTS.md — Claw 项目说明书（给 AI 读）

> WorkBuddy 在会话开始时自动加载工作目录下的 `AGENTS.md`（CLI bundle 里确认过 `SubdirectoryMemoryLoader` 找的就是这个名字，另有 `AGENTS.local.md` 本地覆盖、单文件上限 4 万字符）。
> **本文件是入口和红线，不是知识副本。** 细节一律去权威文件查，不在这里复制第二份；和权威文件冲突时，以权威文件为准。

## 0. 这是什么

A股投顾自动化系统：模拟炒股 / 实盘助理 / 美股监控三套数据互不相通，外加早报晚报、统一巡检中枢、知识库。
跑的是真金白银的账户。**宁可少做事，不可做错事**；不确定就停下来问。

## 1. 硬红线（违反即事故）

- **删/移任何文件**：先复制 → 验证 → 再删源，删前必须用户确认。
- **实时价只走腾讯 `qt.gtimg.cn`**；Wind 只用于基本面/板块，且积分会耗尽（耗尽时是「降级」不是「无数据」，别编）。
- **自动化调 LLM 必须走本地代理 `127.0.0.1:9999`**（provider 不能直接用 deepseek/catrouter）。
- **一条 RRULE 只能有一个 BYHOUR**；多时段必须拆成多条自动化（多值只触发第一个，其余静默丢失）。
- **报告/选股/持仓里出现的任何股价必须过 `price_sanity` 三闸门**。
- **投资类推送**走飞书群 `oc_9ee5303497f5e0e71666b610d6bdc346`，卡片化（禁 `--text` 降级）；维护类默认静默，仅 ⚠️/🔴 推。
- **`data/` 字段改动先确认**；总本金授权值不可推翻；三套系统数据不得混。

> 以上是 `.workbuddy/memory/MEMORY.md` **规则层**的摘要。完整 62 条 + 逐条证据：
> `grep -n "^## 🔴 不可违反铁律" .workbuddy/memory/MEMORY.md`，规则层在行 24–88，证据在文末「铁律证据库」。

## 2. 解释器与常用命令（均实测过）

**解释器不统一，先用对再动手：**

| 解释器 | 版本 | 有什么 |
| --- | --- | --- |
| `python3`（默认，`~/.workbuddy/binaries/python/...`） | 3.13.12 | 没有 chromadb / sentence-transformers |
| `/usr/bin/python3`（系统） | 3.9.6 | **有** chromadb / sentence-transformers |

→ 知识库、向量检索一律用 `/usr/bin/python3`。

```bash
# 交易日判定（交易类自动化第一道门禁）
python3 .workbuddy/scripts/is_trading_day.py 2026-10-08     # → 「交易日 ✅」/ rc≠0

# 技能库体检（重复/坏链/未用脚本/注入）
python3 .workbuddy/scripts/skill_hygiene.py

# 统一巡检中枢对账 D1–D9（默认只读；加 --fix 会以 DB 真值回写 registry 收敛 D1）
python3 .workbuddy/scripts/hub_reconcile.py

# 文档落地对拍（切 live 的机器判据）
python3 .workbuddy/scripts/doc_apply_parity.py

# 知识库统计
/usr/bin/python3 .workbuddy/scripts/knowledge_base.py stats

# 开发工具链
make lint / test / test-unit / test-cov / type-check / ci
ruff check --config ruff.toml <path>          # line-length=100，E501 忽略
```

**脚本双副本规则**：`scripts/xxx.py` 是自动生成的**转发薄壳**，真身在 `.workbuddy/scripts/xxx.py`。
**只改真身**，不要改薄壳，也不要新建第三份（`dual-copy-audit` 技能会扫出来）。

护栏在 `.workbuddy/hooks/pre-task.sh` / `post-task.sh`，由自动化 prompt 里 source，**不是平台钩子**（`settings.json` 的 `hooks` 为空）——所以它只是最后防线，不能替代判断。

## 3. 常踩的坑（都是真踩过的，别再踩）

- `Path(__file__).resolve()` 会把 `~/.workbuddy` 解析成 `/Volumes/ZHITAI/workbuddy_data/...`（数据卷是软链），机器相关路径会随产物漂出去 → **展示用固定串，访问用 `.absolute()`**。
- `ruff check --config <绝对路径>` 会让 `per-file-ignores` 里的 `scripts/*.py` 失配，凭空冒出几十条 `T201`/`PLR2004` 假报错 → **在项目目录内用相对路径跑**。
- safe-delete 护栏会拦「一次删 >50 个文件」（含 `shutil.rmtree`）→ **用就地覆盖**；确需清理就走 `mv` 或显式 `--prune`，别硬删。
- SQLite 有 WAL：要读到最新提交必须同时快照 `db` + `-wal` + `-shm`；`mode=ro` 可能读到未 checkpoint 的旧页。
- 自动化**拆槽会静默重置** model / expert / 权限 / 推送（`automation_update` 不支持这几个字段）→ 拆完去 UI 对齐，D9 每天会盯。
- **声明会腐烂**：写进文档的「每次 X 时同步」这种承诺，必须挂到自动化上，否则 60 天没人执行也没人报警（本仓库已发生过：INTENT.md 声明「每次会话结束前同步状态」，实际停在 08-17）。
- 断言式「已验证」必须带可复现命令 + 回读到真实值；`rc=0` 不等于成功。
- **先定位真值来源，再下结论**（10-08 实测）：QTS 里同时躺着三份"账本"——仓库根 `quant_trading.db`（SQLite，**全表 0 行**）、
  `strategy-service/quant_trading.db`（SQLite，2026-06 的旧数据，且 schema 已与生产分叉：有 `trades.profit_loss`、PG 版没有）、
  真值是 docker 里的 **Postgres**（`localhost:15432`，脚本走 psycopg2）。判断 QTS 数据状态一律查 Postgres，别信仓库里的 `.db` 文件——
  照后者下结论会得出完全相反的答案。

## 4. 去哪查（别重复造，别复制第二份）

| 想知道 | 去哪 |
| --- | --- |
| 铁律 + 逐条证据 | `.workbuddy/memory/MEMORY.md`（规则层行 24–88 / 证据库文末） |
| 用户决策风格、协作心智模型 | `.workbuddy/memory/SCHEMA.md` |
| **待决事项**（含 owner/due/判据/if_no_action） | `.workbuddy/inspection_hub/registry.json` → `pending_actions` |
| 已知失效模式（含"不影响的边界"） | 同上 → `known_failure_modes` |
| 五层架构 / 各层落地状态 | `.workbuddy/ARCHITECTURE.md` |
| 模型路由与预算降级 | `.workbuddy/docs/routing-rules.md` |
| 一次事故怎么复盘的 | `.learnings/`（标 ★ 的是升级铁律候选） |
| 某天做了什么 | `.workbuddy/memory/YYYY-MM-DD.md`（>30 天应蒸馏进分层） |
| 自动化清单与契约 | `.workbuddy/docs/automation-inventory.md` |
| **Token / 积分花在哪了** | `.workbuddy/reports/token-dashboard.html`（用量看板）+ `credit-vs-token.md`（官方积分 × token 对齐、实测积分/百万 token）；两者每日 06:30 由「📊 Token 用量看板 + 积分对齐（每日刷新）」自动刷新，也可说一句"看看我的 token 用量" |
| 技能怎么发现的 | 直接看技能列表；重复/失效用 `skill-library-audit` |

**开工前先读** `registry.json` 的 `pending_actions` —— 那里是唯一活的待决看板。

## 5. 工作方式

对齐 Anthropic《The AI-native SDLC playbook》的四件套，在本仓库的落点是：

| playbook | 本仓库落点 | 状态 |
| --- | --- | --- |
| `intent.md` 需求单 | `.workbuddy/memory/INTENT.md` | ⚠️ **已停滞**（08-17 后未更新），职能实际被 `pending_actions` 接手 → 双账本，处置见 **PA-006**（due 2026-10-22） |
| `spec.md` 方案说明 | `.workbuddy/docs/adr/` 记已定决策；单次任务的方案写在当日日志 | ✅ / 无统一模板 |
| `plan.md` 执行计划 | `.workbuddy/inspection_hub/registry.json` → `pending_actions` | ✅ 比 playbook 更强（带判据与 if_no_action） |
| 项目说明书 | 本文件 | ✅ 新建（2026-10-08） |

**循环**：写代码 → 跑检查 → 修问题 → 再检查；AI 在反馈里反复完成，人只在三处拍板 —— 目标是否清楚、方案是否合理、结果是否过关。

**错误沉淀闭环**：真踩过的坑 → `.learnings/` 标 ★升级候选 → 周度自动化升铁律 → 进 `MEMORY.md` 规则层。
**别让同一类错犯第二次，也别等用户提醒才记。**

## 6. 维护本文件

- 只写「一进来就该知道、且不知道会浪费时间或出事故」的东西；能指路的就指路。
- §1 是 MEMORY.md 规则层的摘要，**冲突以 MEMORY.md 为准**；改规则层时顺手核对本节。
- §3 每新增一条，应当是「真踩过 + 有可复现现象 + 有正确做法」，别写空泛告诫。
- 单文件上限 4 万字符；超过就先删能指路的部分，不要靠删红线腾地方。
