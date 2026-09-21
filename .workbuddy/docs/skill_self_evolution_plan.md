# Skill 自我升级迭代计划（Skill Self-Evolution Plan）

> 目标：把"今日优化 majiu-management v2"做成可每日自运行的闭环——
> 感知同类项目 → 评估差距 → 演进 skill → 验证 → 沉淀，
> 全程受 majiu 治理 + L3 护栏约束，非破坏性、可回滚、分级授权。

---

## 一、定位

- **不是**给所有 80 个 skill 每天做全量研究（成本爆炸）。
- **是**聚焦用户高频迭代的 skill 子集（如 `majiu-management`、`wb-finance-skill`、投顾相关），
  每天轻量"探雷"，每周深做 1–2 个，每月全量卫生审计，每季整合巡检。
- 本质：让工作空间的 skill 像软件一样有版本、有 CHANGELOG、可回滚、持续演进。

## 二、演进闭环（5 阶段）

1. **感知 Discover**：全网搜同类项目 / 新版本 / 新技法（WebSearch、GitHub trending、技能市场、HackerNews、arxiv、博客）。
2. **评估 Assess**：与本地 skill 比对，找差距 / 重复 / 过期 / 可借鉴点，打分优先级。
3. **演进 Evolve**：改 `SKILL.md` / `references/` / 脚本，写版本号 + CHANGELOG。
4. **验证 Validate**：`bash -n` + dry-run + 自测（如 `workspace_scan.sh` 实跑），确认无回归。
5. **沉淀 Record**：更新 CHANGELOG + memory + STALL_LOG 台账 + evidence_log 留痕。

## 三、计划表（核心）

| 频率 | 任务 | 输入源 | 动作 | 产出 | 护栏 |
|---|---|---|---|---|---|
| 每日 07:30 | 轻量扫描 | WebSearch / GitHub trending / 技能市场 | 对演进子集每 skill 跑 1 条 curated query，捕获新版本 / 竞品 | 候选清单(URL+摘要+相关skill) | 仅扫描零改动；token 上限封顶 |
| 每日 20:00 | 差异比对 | 候选清单 + 本地 skills | 匹配可优化点，按价值/风险打分 | 优化建议 top≤3 | 仅提案不改写 |
| 每周六 10:00 | 深度演进 | 当周 top1–2 建议 | 改 SKILL/references，跑自测 | skill vN+1 + CHANGELOG | dry-run + 回读；核心逻辑先提案待审 |
| 每月 1 号 09:00 | 技能卫生审计 | 全量 skills(80) | 重复 / 坏链 / 未用 / prompt-injection 扫描 | 审计报告 + 清理建议 | 禁区不碰；非破坏性 |
| 每季 1 号 09:00 | 空间+技能巡检 | majiu 季度巡检 | 整合技能健康度 | 治理报告 | 非破坏性铁律 |

## 四、安全与授权边界

- **禁区（majiu）**：`automations/`、`scripts/*.db`、`.git`、`.venv`、symlink 目标、memory 层 —— 自检循环绝不触碰。
- **非破坏性**：先复制→验证→再删源；默认 dry-run；外置 SSD 回收站而非 `rm`。
- **分级授权**：
  - 文档 / 参考类（`references/*.md`、`README`、`CHANGELOG`）→ 可自动应用。
  - 核心 prompt / 逻辑类（`SKILL.md` 主流程、脚本）→ 先提案，待人工审阅或在授权窗口应用。
- **版本化**：每次改动写 CHANGELOG + 语义版本号，可一键回滚（保留上一版 `*.vN.bak`）。
- **防幻觉**：引用真实来源 URL；不臆造功能 / 数据；`anysearch` / RSS 当前降级，改用 WebSearch / WebFetch / GitHub 直连。

## 五、与现有体系衔接

- **majiu-management v2**：治理 + 禁区 + STALL_LOG 台账（每次演进建一个"栏位"）。
- **skill-hygiene.md**：月度审计脚本化复用。
- **L3 护栏 + evidence_log**：每次演进留痕、失败可溯。
- **memory 蒸馏**：演进时顺带压缩 `MEMORY.md`(65KB🔴) / `watchdog mem`(107KB🔴) 超限记忆。
- **今日 majiu v2 优化** = 该闭环的第 1 个手工实例，下一步把它"自动化"。

## 六、当前已知约束（影响落地）

- RSS 断供第 37 天（自 08-19），`anysearch` 模块级降级 → 发现层暂靠 WebSearch / WebFetch / GitHub。
- 实时行情接口（`fetch_holdings_quotes.py --sim`）今日返回空 → 投顾类 skill 演进需留意数据源可用性。
- 4 个脚本缺失（`is_trading_day.py` / `cost_tracker.py` / `run_debate.py` / `calc_rsi.py`）→ 先补齐再让相关自动化健康自跑。

## 七、落地建议（可立即建）

1. **automation「skill自我演进-每日扫描」**：每日 07:30 扫描 + 20:00 比对，卡片推候选清单（默认静默，有高价值才推）。
2. **automation「skill自我演进-周度演进」**：WEEKLY 周六 10:00，跑深度演进 + dry-run 自测，产出 vN+1 与 CHANGELOG，核心改动先提案。
3. **可选 meta-skill「skill-evolver」**：封装 感知→评估→演进→验证 标准剧本与 prompt，供上述自动化调用。

> 待确认：演进改动是否全授权自动应用，还是核心 prompt 类保留人工审阅？（建议后者——文档类自动，逻辑类提案待审。）
