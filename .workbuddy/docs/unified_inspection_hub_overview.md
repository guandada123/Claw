# 统一巡检中枢（Unified Inspection Hub）— 总览

> 把「健康巡检/自愈/监控」与「同类项目发现/技能演进」合并为**单中枢**，统一发现、统一管理。
> 方法论扩展自 `unified-ops-center`（6 层）＋ 新增「发现维度」＋ 中央注册表。

## 中心组成
- **中枢方法论（meta-skill）**：`~/.workbuddy/skills/unified-inspection-hub/SKILL.md`
- **中央注册表（统一管理核心）**：`$CLAW/.workbuddy/inspection_hub/registry.json` —— 单一事实源，所有中枢自动化读写它
- **中枢自动化（全部已建 ACTIVE）**：`🛡️ 统一发现-每日扫描`(每日07:30) / `🧬 技能演进-周度`(周六10:00) / `🧹 技能卫生审计-月度`(每月1号) / `🐴 空间+技能巡检-季度`(majiu) / `💓 中枢存活看门狗`(每2h, QTS)；详见下方节奏表

## 统一发现（每日脉冲做什么）
1. **A 内部健康快照**：复用既有脚本 `--json`/`--dry-run`（缺失则降级跳过）
2. **B 外部同类研究**：对 `evolution_watch_skills`（majiu-management、wb-finance-skill）各跑 1 条 curated WebSearch，捕获新版本/竞品/新技法
3. **比对**：外部发现 × 本地 skill → 可优化点，按 价值↑/风险↓ 取 top≤3
4. **写回 registry**：`discovery_candidates[]` 状态 pending
5. **静默判定**：无高价值 → SILENT；有 → 飞书卡片推候选（附真实 URL，不臆造）

## 统一管理（registry 管什么）
- `automations[]`：本中枢全部自动化（id / rrule / status / last_run / next_run）
- `evolution_watch_skills[]`：纳入演进观察的 skill 子集
- `discovery_candidates[]`：每日发现队列（pending→approved→applied/rejected）
- `runbook_whitelist[]`：已注册安全动作
- `authorization_tiers`：分级授权边界
- `forbidden_zones[]`：majiu 禁区

## 分级授权（已确认：文档自动 / 核心 prompt 待审）
- 自动应用：`references/*.md`、`README.md`、`CHANGELOG.md`、registry.json
- 提案待审：`SKILL.md` 主流程、脚本 `*.py`/`*.sh`、自动化 prompt

## 演进节奏（registry 中状态）
| 频率 | 自动化 | 状态 |
|---|---|---|
| 每日 07:30 | 🛡️ 统一发现-每日扫描 | ✅ ACTIVE（已建） |
| 每周六 10:00 | 🧬 技能演进-周度 | ✅ ACTIVE（id `0dd41dcb-b1b3-4cfd-bd73-75fd7cbf12c4`）读 pending→文档类应用/核心类提案 |
| 每月 1 号 09:00 | 🧹 技能卫生审计 | ✅ ACTIVE（id `a0b1b435-225f-43eb-8e9b-a7bb82fde97a`）复用 skill-hygiene（缺失降级只读盘点） |
| 每季 1 号 09:00 | 🐴 空间+技能巡检 | ✅ ACTIVE（majiu 季度巡检 id 52db25b4-…） |
| 每 2h | 💓 中枢存活看门狗 | ✅ ACTIVE（id `337116da-e9b3-46f6-8993-a00b35ad7e86`，cwds=QTS 符合禁托管新规）独立调度 |

## 铁律
- 禁区（majiu）：automations/、scripts/*.db、.git、.venv、symlink 目标、memory 层 —— 绝不触碰
- 非破坏性：先备份 *.vN.bak，验证通过再保留，可回滚
- 防幻觉：外部发现须附真实 URL；不臆造功能/版本/数据
- 状态锚闭环：运行态写回 `cross_project_state.json` 的 `monitoring.global.inspection_hub`
- Claw 禁托管：纯运维/自愈类优先托管 QTS 工作区；助手侧「发现/演进」类可留 Claw

## 已知约束
- RSS 断供 37 天、anysearch 降级 → 发现层暂靠 WebSearch/WebFetch/GitHub 直连
- 内部健康脚本（automation_health.py 等）部分缺失 → 每日脉冲降级跳过并注明，待补齐
