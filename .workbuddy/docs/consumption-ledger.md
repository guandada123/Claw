# 消耗口径台账（Token / 积分 / 自记成本）

> 建立：2026-10-10。起因：工作区里先后出现多套「消耗统计」实现，口径互不相同，
> 已经出现过一次误读（把自记表价当实付，得出「本月超支 ¥166」）。本文件是**唯一的口径索引**：
> 新增任何「花了多少」的数字前，先在这里认领它属于哪个源。

## 一句话结论

**四个实现、三条数据链、只有一种单位是真的。** 不合并代码，做「一个度量 + 多视图」：
- 唯一真实扣减单位 = **积分**（官方计费）
- token = 消耗量（不是钱），当分母
- ¥ = **两套互相矛盾的估算**，都不是实付

| 源 | 单位 | 数据在哪 | 粒度 / 覆盖 | 是不是实付 |
| --- | --- | --- | --- | --- |
| A 平台日志 | token | `~/.workbuddy/projects/**/*.jsonl` | 请求级；全量（本机 170+ 天、9830 会话、162 亿 token） | 否（量） |
| B 官方计费 | **积分** | `workbuddy.db → session_usage.credit_json` | 会话级；只有**实际扣积分**的会话（约 27%） | **是** |
| C 自记插桩账 | ¥（表价估算） | `~/.ai_cost_log.jsonl`（`cost_tracker.log_call/ log_estimate` 写入） | 调用级；**仅 1924 次插桩，占全量 token 的 0.031%** | 否 |

实测：C 的 5,010,894 token 被其内置价目表算成 ¥2,149.92 → 约 **¥429/百万 token**；
公开牌价（DeepSeek 系）约 **¥2/百万输入、¥8/百万输出**。**同一批 token，两张表差约 200 倍。**

## 现有实现清单（谁在跑、谁已死）

| 实现 | 位置 | 数据源 | 状态 |
| --- | --- | --- | --- |
| Token 看板 | skill `token-dashboard`（上游 `fuyi-git/token-dashboard`）+ `.workbuddy/reports/token-dashboard.html` | A | ✅ 活（每日 06:30 自动刷新） |
| **积分读取单源** | `.workbuddy/scripts/credit_meter.py` | B | ✅ 活（**唯一允许解析 `credit_json` 的地方**，budget_guard 与 credit_vs_token 都消费它） |
| 三源对齐报告 | `.workbuddy/scripts/credit_vs_token.py` → `.workbuddy/reports/credit-vs-token.md` | A×B×C | ✅ 活（唯一报告入口） |
| 自记数据层 | `.workbuddy/scripts/cost_tracker.py`（v2.1） | 自建 JSONL | ⚠️ 半活：仍在被 `read_wx_articles.py` 等插桩写入（**只看得到被插桩的调用**）；2026-10-10 起**不再是任何预算判据**，文件头已加口径警示 |
| 自记报告层 | `scripts/cost_monitor.py`（daily/monthly/summary/dashboard 四子命令） | cost_tracker | ❌ 死：无 ACTIVE 自动化调用 |
| 自记可视化 | `scripts/cost_dashboard.py`（899 行 HTML）+ `cost_dashboard_feishu.py` | cost_tracker | ❌ 死：产物最新停在 2026-06-30（`cost_report_*.json`）；飞书推送自动化 1782002819199 已于 09-23 软删 |
| 预算拦截器 | `.workbuddy/scripts/budget_guard.py`（**v3.0 积分口径**：月额度 `WB_CREDIT_BUDGET`，默认 6000 积分；87.5% 触发 Flash 锁） | B（经 `credit_meter`） | ✅ 活；消费方：巡检中枢「积分预算」检查、`router.py`（当前无 ACTIVE 自动化走 router） |
| 积分错峰审计 | `~/.workbuddy/skills/workbuddy-credit-offpeak-audit` + `WorkBuddy/2026-09-23-21-55-37/` | 调度清单 + 免费窗口 | ✅ 活（每日 03:50，管的是**调度**不是用量） |

## 三条已确认的缺陷

1. **价目表错两个数量级**（C 单独背）：`cost_tracker.MODEL_PRICES` 以「¥/万 token」标注，
   但数值等于公开「¥/百万」价的 ~200 倍。它算出的一切 ¥ 都不能与看板、与积分并列比较。
2. **覆盖率 0.031%**：C 只统计显式插桩的调用，agent 会话本身完全不在其中。
   所以 **C 的月度数字既不是总量、也不是实付**，只能当「被插桩那部分」的样本。
   （`log_estimate()` 更弱：写的是 `AUTO_COST_ESTIMATES` 里的**手填估值**，不是实测 token。）
3. **成本链已断但残留声明**：监控可视化全链（自动化 06-21/07-12/09-23 依次软删）已死，
   唯一还在消费 C 的是巡检中枢的 `check_cost_anomaly`（已改文案为「委托已退役」）。
   `MEMORY.md` 里「cost_tracker/cost_monitor 被 cost_dashboard_feishu 依赖」是**历史事实**，
   不是当前活链。

## 判断：怎么「融合」

**不合并代码库、不合并看板**（合并只会把「谁的口径对」的争论固化进代码）。做两件事：

1. **收敛报告入口**：所有「消耗」问题只走 `.workbuddy/scripts/credit_vs_token.py`
   （三源对齐 + 覆盖 + 口径台账），要图就看 `token-dashboard.html`。两个产物、一个入口。
2. **退役重复的 ¥ 口径**：`cost_dashboard*.py` / `cost_monitor.py` 已是死链 → 不恢复；
   `cost_tracker` 保留为**插桩样本账**（它的价值是把「哪条自动化花了多少」标出来，
   而不是算钱），任何对外结论都必须带「自记口径、非全量、非实付」。

**已决定并落地（2026-10-10，PA-009 → done）**：`budget_guard` 改**积分口径**（方案②）。
- 单位：积分；数据源：`credit_meter`（单源读官方积分）；月额度 `WB_CREDIT_BUDGET`（默认 6000 积分
  ≈ 历史峰值月 06 月 6676 的九成，**应改成你的积分套餐额度**）；
  层级阈值沿用 50% / 70% / 87.5%，87.5% 触发 Flash 锁定；
- 失败语义：读数失败与「额度<=0」都 fail-closed 锁 Flash，但**文案区分「故障」与「超支」**（避免把读不到说成超支）；
- 单次调用上限折算为 30 积分（实测 2.68 积分/百万 token ≈ 1,120 万 token/次，护栏而非节流器）；
- **删掉了「日警告线」**（v2.x 的 ¥25/日）：积分侧只有会话级归属（按会话最后活动日整块计入），
  长会话会把几天的量全落在结束那天 → 日数字必然尖峰失真；要日粒度得走 token 侧请求级时间戳；
- 巡检中枢原「成本监控」检查改名为「积分预算」，输出形如 `积分(本月): 1669/6000（28%）`，
  不再报 ¥、不再报「委托链」。
