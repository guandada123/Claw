# 文档类候选落地 · 校准报告 2026-10-09 19:49

- 模式：**live**（calibrate=只读零写入；live=产出待执行计划，由 agent 落地）
- 过闸并入选：3 条（top_n 限流后）
- 被滤除：56 条

## 本会改什么（入选）
1. `disc-20261004-03` · a-stock-data · 价值=medium-high / 风险=low
   - 目标锚点：**meta** · 轨道=external_trend
   - 目标文件：`references/realtime-l2-tick.md`
   - 成功指标：a-stock-data/references/realtime-l2-tick.md 已落地，且含『mootdx』与『腾讯』两个零鉴权 L2 数据源小节
   - 来源：https://www.toolify.ai/openclaw-skills/a-share-real-time-data-18701
   - 依据：对标本地 a-stock-data 数据层扩展：在 references/ 增补 realtime-l2-tick.md，记录 mootdx(TDX L2盘口+tick，零鉴权但依赖公共服稳定性) 与 stock-price(腾讯免费 curl，零鉴权，GBK解码) 两个可借鉴源；文档类 auto_apply，不改动核心 SKILL.md。（注：L2/逐笔是日内交易与做T的关键深度数据，本地 a-stock-data 现有源未覆盖；mootdx 公共服曾停服见 disc-20260926-01，须作备用而非唯一源。）
2. `disc-20261006-02` · a-stock-data · 价值=high / 风险=low
   - 目标锚点：**meta** · 轨道=external_trend
   - 目标文件：`references/a-share-sentiment-moneyflow.md`
   - 成功指标：a-stock-data/references/a-share-sentiment-moneyflow.md 已落地，且含『情绪』与『资金流』两个维度小节
   - 来源：https://github.com/very99/stock-mcp
   - 依据：补本地 a-stock-data 缺的『新闻情绪 + 资金流向』维度（既有 MCP 候选仅覆盖行情/板块数据）：写 references/a-share-sentiment-moneyflow.md 能力对照（多源校验降级 + 情绪 + 资金流），文档类 auto_apply；MCP 交付与本地 Skill 互补，不改动核心
3. `disc-20261006-03` · majiu-management · 价值=high / 风险=low
   - 目标锚点：**meta** · 轨道=external_trend
   - 目标文件：`references/skill-routing-dispatch.md`
   - 成功指标：majiu-management/references/skill-routing-dispatch.md 已落地，且含『置信度』触发与『降级』链两节
   - 来源：https://github.com/paulpas/agent-skill-router
   - 依据：为 majiu-management 补『技能路由分发』治理维度（既有候选覆盖生命周期/安全/市场/注册表，未覆盖路由）：落地 references/skill-routing-dispatch.md（置信度触发匹配 + skills-index + 降级链 + 路由事件日志），文档类 auto_apply；与本地 skills 索引/自动加载协同

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
- `disc-20261005-01` — status=applied 不在允许集
- `disc-20261005-02` — status=rejected 不在允许集
- `disc-20261005-03` — status=rejected 不在允许集
- `disc-20261006-01` — status=rejected 不在允许集
- `disc-20261007-01` — status=rejected 不在允许集
- `disc-20261007-02` — status=rejected 不在允许集
- `disc-20261007-03` — status=rejected 不在允许集
- `disc-20261008-01` — status=rejected 不在允许集
- `disc-20261008-02` — status=rejected 不在允许集
- `disc-20261009-01` — status=rejected 不在允许集
- `disc-20261009-03` — status=rejected 不在允许集
- `disc-20261009-04` — status=applied 不在允许集
- `disc-20261009-05` — status=applied 不在允许集
- `disc-20261009-06` — 承载方缺失: (空)
- `disc-20261008-03` — top_n 限流（本批配额 3）
- `disc-20261009-02` — top_n 限流（本批配额 3）

## v1.10 指标闸（新）
- 因**无目标锚点**被拦：0 条
- 因**无可判定指标**被拦：0 条
- 药方：在「🛡️ 统一发现-每日扫描」里给候选补 `target_project` + `success_metric`（可判定、可复核）后才进本通道；
  补不出指标的 → 在周度排序里落到 `Assess`（先评估）或 `rejected`，**不再默认落地**。

## 说明
- 校准期（calibrate）本脚本**不写任何 skill 文档**，仅供对拍「本会改什么」。
- 生效期（live）由「📥 每日·文档类落地」自动化里的 agent 合成文档，每批 = 1 commit + 1 行 CHANGELOG + 1 个回滚点，top≤3。
- 核心类（SKILL.md 主流程 / 脚本 / 自动化 prompt）**永不**进本通道，恒走周度审。
