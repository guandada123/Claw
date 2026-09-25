# 发现归档：wb-finance-skill 相关候选（待决）

> 状态：**deferred — 目标 skill `wb-finance-skill` 本机未安装**
> 归档时间：2026-09-23
> 说明：统一巡检中枢的 discovery_candidates 中 4 条指向 `wb-finance-skill`，但本机
> `~/.workbuddy/skills/` 与 `Claw/.workbuddy/skills/`（含 ZHITAI 卷）均未找到该 skill。
> 现有金融类 skill 为：`a-stock-data`、`tushare-finance`、`westockdata`、`wind-find-finance-skill`、`stockinsight-test-patterns`。
> 因此这些发现**无法在本地 skill 上落地**，先归档于此，待决策：
> （A）安装/创建 `wb-finance-skill` 后再应用；或
> （B）将相关能力映射到现有 `a-stock-data` / `tushare-finance` / `westockdata`。

---

## disc-20260922-01（value=high, risk=low）— UZI-Skill 量化评分维度
- URL：<https://github.com/wbh604/UZI-Skill>（发现源 bittide.aicompass.dev）
- 摘要：A股/港股/美股一体化分析流程，22 维数据 + 180 条量化规则 + 龙虎榜/游资专项，个股初筛→风险排查双段式。
- 原建议：为 wb-finance-skill 增加 A 股专项量化评分维度（参考 22 维 + 180 规则），强化个股初筛与风险排查双段式；可落到 references/ 自动应用。

## disc-20260922-02（value=medium-high, risk=medium）— TradingAgents 多空辩论框架
- URL：<https://github.com/TauricResearch/TradingAgents>
- 摘要：多智能体投研辩论框架（基本面/情绪/新闻/技术 分析师 + 多空辩论 + 五档评级）。
- 原建议：对标本地 expert_team_analyst.py 7 专家框架，评估是否缺「多空辩论+五档评级」闭环；若纳入编排属核心流程改动→仅提案待审。

## disc-20260923-02（value=high, risk=low）— a-stock-data 数据能力矩阵
- URL：<https://www.aiexpress.news/89847.html>（核查 2026-09-17）
- 摘要：a-stock-data：A股全栈数据工具包，13 源·28 端点·零鉴权；同榜 TradingAgents-Astock、finance-quant-skills(akshare/tushare/聚宽/QMT)、tickflow-stock-panel。
- 原建议：本地已装 a-stock-data；对标其 13 源 28 端点零鉴权广度，评估 wb-finance-skill 数据层是否需补源/补端点；将竞品盘点写入 references/ 数据能力矩阵（文档类 auto_apply）。

## disc-20260923-03（value=medium-high, risk=medium）— TradingAgents-Astock A股生产化变体
- URL：<https://github.com/hsliuping/TradingAgents-CN> + <https://www.aiexpress.news/89847.html>
- 摘要：TradingAgents 的 A股本土化 fork，接入 A股数据源+中文 LLM，自带 FastAPI 后端 + Vue3 前端；7 位分析师多空辩论+五档评级，Apache-2.0。
- 原建议：对标 disc-20260922-02 上游的 A股生产化变体，评估 expert_team_analyst.py 是否补「多空辩论+五档评级+生产后端」闭环；若纳入编排属核心流程改动→仅提案待审。

---

## 处置决定（2026-09-23，agent 自主判断 "你来判断"）

结论：**不创建虚拟的 `wb-finance-skill`**（该 skill 本机不存在，且其建议中对比的本地基线 `expert_team_analyst.py` / `run_debate.py` 经全盘检索亦不存在 → 这 4 条本质是"新建能力"提案，非"增强现有"）。

按"有真实归属才落地"原则分流：

- **已落地（重定向至已装 a-stock-data / 文档类 auto_apply）**：
  - disc-20260923-02 → `a-stock-data/references/data-capability-matrix.md`（原建议即指向 a-stock-data）
  - disc-20260922-01 → `a-stock-data/references/quant-screening-framework.md`（UZI-Skill 22维+180规则 初筛→风险排查双段式参考）
- **留档为净新建能力提案（proposal_new_capability）**：
  - disc-20260922-02（TradingAgents 多空辩论+五档评级）
  - disc-20260923-03（TradingAgents-Astock A股生产化变体）
  - 二者目标 skill 与本地基线均不存在，属未来若建辩论/评分框架时采纳的提案；registry 状态已置 `proposal_new_capability`。

> 若日后安装 wb-finance-skill，可将两条 proposal 的参考内容迁入；否则其价值已通过 a-stock-data 的两条 references 部分兑现。

## 补充：观察列表修正（2026-09-23，agent 判断"不需要安装"）

- 判断：**不安装 wb-finance-skill**。理由：A股数据/分析已被 a-stock-data + tushare-finance + westockdata + Wind 家族覆盖，无真实缺口；安装等于引入未验证第三方代码且与 a-stock-data 冗余；4 条发现本就是"新建能力"提案非增强。
- 根因是 `evolution_watch_skills` 里一条过时登记（含不存在的 wb-finance-skill），导致发现持续归因到空目标、产生悬空候选。
- 已修：从 `evolution_watch_skills` 移除 `wb-finance-skill`，以真实 A股 中枢 `a-stock-data` 取代 → `['majiu-management', 'a-stock-data']`。未来 A股 能力发现将落到真实 skill。registry JSON 校验通过。
