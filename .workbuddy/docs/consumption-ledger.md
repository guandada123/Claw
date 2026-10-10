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
| 三源对齐报告 | `.workbuddy/scripts/credit_vs_token.py` → `.workbuddy/reports/credit-vs-token.md` | A×B×C | ✅ 活（唯一报告入口） |
| 自记数据层 | `.workbuddy/scripts/cost_tracker.py`（v2.1，¥400/月、¥350 Flash 锁、¥25/日告警） | 自建 JSONL | ⚠️ 半活：仍在被 `read_wx_articles.py` 等插桩写入，但**只看得到被插桩的调用** |
| 自记报告层 | `scripts/cost_monitor.py`（daily/monthly/summary/dashboard 四子命令） | cost_tracker | ❌ 死：无 ACTIVE 自动化调用 |
| 自记可视化 | `scripts/cost_dashboard.py`（899 行 HTML）+ `cost_dashboard_feishu.py` | cost_tracker | ❌ 死：产物最新停在 2026-06-30（`cost_report_*.json`）；飞书推送自动化 1782002819199 已于 09-23 软删 |
| 预算拦截器 | `.workbuddy/scripts/budget_guard.py`（¥350 触发 Flash 锁定） | cost_tracker | ⚠️ 仅被 `router.py` 引用；当前无 ACTIVE 自动化走 router |
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

**唯一需要用户决定的**：`budget_guard`（¥400/月预算 + ¥350 触发 Flash 锁定）——
它诞生于「自付 API 账单」时代，而你现在走积分，它的计价基础已不存在。二选一：
- **① 退役**：删掉预算拦截与 ¥ 告警口径（它当前无 ACTIVE 调用方，退役零风险）；
- **② 改口径**：预算改成**积分/月**，判据从 `cost_tracker` 换成 B 源（官方积分），
  这样它才管得住真钱。

## 维护约定

- 任何新的「花了多少」，先在本文件认领源；跨源比较必须显式写单位与口径。
- 三源里只有 B（积分）是实付。**永远不要把 C 的 ¥ 直接说成"花了多少钱"**。
- 会腐烂的事实（倍率、免费窗口、活动期）以现场为准，见 skill `workbuddy-credit-offpeak-audit`。
