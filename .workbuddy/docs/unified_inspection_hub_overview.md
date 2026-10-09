# 统一巡检中枢（Unified Inspection Hub）— 总览

> 把「健康巡检/自愈/监控」与「同类项目发现/技能演进」合并为**单中枢**，统一发现、统一管理。
> 方法论扩展自 `unified-ops-center`（6 层）＋ 新增「发现维度」＋ 中央注册表。

## 中心组成
- **中枢方法论（meta-skill）**：`~/.workbuddy/skills/unified-inspection-hub/SKILL.md`
- **中央注册表（统一管理核心）**：`$CLAW/.workbuddy/inspection_hub/registry.json` —— 单一事实源，所有中枢自动化读写它
- **中枢自动化（全部已建 ACTIVE）**：`🛡️ 统一发现-每日扫描`(每日 00:20) / `📥 每日·文档类落地（校准/生效自动）`(每日 00:40) / `🧬 技能演进-周度`(周六 23:22) / `🧹 技能卫生审计-月度`(每月1号 23:44) / `🐴 空间+技能巡检-季度`(majiu, 季首 23:49) / `💓 中枢存活看门狗`(每2h, QTS)；详见下方节奏表
- **调度时段纪律**：中枢治理类自动化一律排在 **Hy4 夜间免费窗口（23:00 → 次日 08:00）**，错峰避开 23:0x 起始潮；发现 00:20 → 落地 00:40（同夜闭环，且与周六 23:22 的技能演进**不同时段**，避免「发现写 registry / 演进读 registry」竞态）

## 统一发现（每日脉冲做什么 · v1.10 双轨）
**双轨发现**（v1.10 起；旧版只搜外网趋势，导致 57 条候选 100% 落在技能元层、真实项目零触达）：
1. **Track 2 · 项目自身痛点**（新）：`project_signal_collect.py` 扫 Claw/QTS/StockInsight 的 `.learnings` 事故、TODO/FIXME、测试失败 → 候选（target_project 必为真实项目）
2. **Track 1 · 内部健康快照**：复用既有脚本 `--json`/`--dry-run`（缺失则降级跳过）
3. **Track 1 · 外部同类研究**：对 `evolution_watch_skills` 各跑 1 条 curated WebSearch；另对真实项目各补 1 条定向检索
4. **比对 → 候选**：每条**必须**带 `target_project`（Claw\|QTS\|StockInsight\|meta\|none）+ `success_metric`（可判定指标；可选 `check_cmd`＝退出码 0 即 achieved）
5. **去重闸**：`discovery_dedup.py` 归一 URL/主张算键，重复项丢弃（已实测同一条重复落地 3 次）
6. **写回 registry**：`discovery_candidates[]` 状态 pending
7. **静默判定**：无高价值 → SILENT；**高价值绕过周期**（high+low+真实项目+有指标）→ 当日必推（对照 Renovate 安全补丁绕排期）

## 统一**排序**（周度做什么 · v1.10）
周度**只排序，不批量落地**（对照技术雷达：发现节奏 ≠ 采纳节奏；对照 AutoResearch：每次只改一处）：
1. `project_signal_collect.py --days 7` 回看本周项目痛点
2. `weekly_radar_sort.py --apply` → 四环 **Adopt/Trial/Assess/Caution** + 记**升降环** + 产出价值结论式周报
3. 按环处置：Adopt→落地通道 / Trial→文档类 / **Assess→补 `success_metric` 或 rejected** / Caution→仅提案待审
4. `impact_recheck.py --apply` → 收益回查（有 `check_cmd` 机器判定；无则按指标找库外证据裁决，**不许自己打勾**）

## 统一管理（registry 管什么）
- `automations[]`：本中枢全部自动化（**只放声明**：id / rrule / status / note —— 运行时间读 DB `automation_runs`）
- `evolution_watch_skills[]`：纳入演进观察的 skill 子集
- `discovery_candidates[]`：每日发现队列（pending→approved→applied/rejected）＋ v1.10 的 `track`/`target_project`/`success_metric`/`impact`/`ring`
- `discovery_policy`（v1.10）：双轨定义 / `project_watch.anchors[]` 真实项目锚点 / `dedup` / `metric_policy`（指标闸）/ `high_value_bypass` / `impact_recheck`
- `evolution_radar`（v1.10）：四环状态 + 升降环历史
- `value_scoreboard`（v1.10）：收益记分板（by_impact / by_project / real_project_share / legacy_unmeasured）
- `runbook_whitelist[]`：已注册安全动作
- `authorization_tiers`：分级授权边界
- `forbidden_zones[]`：majiu 禁区

## 分级授权（已确认：文档自动 / 核心 prompt 待审）
- 自动应用：`references/*.md`、`README.md`、`CHANGELOG.md`、registry.json
- 提案待审：`SKILL.md` 主流程、脚本 `*.py`/`*.sh`、自动化 prompt

## 演进节奏（registry 中状态）
| 频率 | 自动化 | 状态 |
|---|---|---|
| 每日 00:20（免费窗口） | 🛡️ 统一发现-每日扫描 | ✅ ACTIVE v1.3（id `2a404a97-c262-4cad-8a43-3c5a69febfbb`）**双轨**：项目痛点 + 内部健康 + 外部研究 → 带锚点与指标的候选 → 去重闸 |
| 每日 00:40（免费窗口） | 📥 每日·文档类落地（校准/生效自动） | ✅ ACTIVE v1.5（id `1fd52465-899f-4525-a82d-4cc5df01155c`）质量闸 top≤3 + **指标闸**（无 `success_metric` 不落地）；mode=live |
| 每周六 23:22 | 🧬 技能演进-周度 | ✅ ACTIVE v1.3（id `0dd41dcb-b1b3-4cfd-bd73-75fd7cbf12c4`）**统一排序**（四环+升降环）+ 收益回查 |
| 每月 1 号 23:44 | 🧹 技能卫生审计 | ✅ ACTIVE（id `a0b1b435-225f-43eb-8e9b-a7bb82fde97a`）复用 skill-hygiene（缺失降级只读盘点） |
| 每季首日 23:49 | 🐴 空间+技能巡检 | ✅ ACTIVE（majiu 季度巡检 id 52db25b4-…） |
| 每 2h | 💓 中枢存活看门狗 | ✅ ACTIVE（id `337116da-e9b3-46f6-8993-a00b35ad7e86`，cwds=QTS 符合禁托管新规）独立调度 |

> 时间列以 **scheduler DB 为唯一真值**；本文档与 registry.json 均为镜像，发现漂移时以 DB 校正镜像。

## 铁律
- 禁区（majiu）：automations/、scripts/*.db、.git、.venv、symlink 目标、memory 层 —— 绝不触碰
- 非破坏性：先备份 *.vN.bak，验证通过再保留，可回滚
- 防幻觉：外部发现须附真实 URL；不臆造功能/版本/数据
- 状态锚闭环：运行态写回 `cross_project_state.json` 的 `monitoring.global.inspection_hub`
- Claw 禁托管：纯运维/自愈类优先托管 QTS 工作区；助手侧「发现/演进」类可留 Claw

## 已知约束
- RSS 断供 37 天、anysearch 降级 → 发现层暂靠 WebSearch/WebFetch/GitHub 直连
- 内部健康脚本（automation_health.py 等）部分缺失 → 每日脉冲降级跳过并注明，待补齐
