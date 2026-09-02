# DB status 不是真相：`concurrency=3` 是装饰性的，超发→饿死→90min 硬超时 ★升级候选

**日期**：2026-09-02（巡检中枢 run#67）
**类型**：根因定案（推翻 run#66 的两条子结论）+ 测量伪影 + 哨兵盲区
**关联**：`2026-09-02-queued-backlog-is-dispatch-stall-not-slot-exhaustion.md`（run#66）

## 现象

run#66 认定的「派发卡死」在 run#67 持续恶化：DB 里 QUEUED 从 3 条涨到 5 条，
IN_PROGRESS 3 条；**中枢自身排了 121min 才启动**（created 11:07:41 → started 13:08:31）。
同日出现 6 条 `[CANCELLED] Run timed out`，耗时全部精确落在 89.98–90.00min。

## 推翻 run#66 的两条子结论

### ① 「派发卡死 / 到点不发」→ 错，真相是**超发**

调度器日志 `~/.workbuddy/logs/automation.log` 里，5 条 DB 显示 QUEUED 的运行
**全都有 `run start:` 行**。它们不是没被派发，是派发后在会话层被饿死。
→ **`automation_runs.status` 不是真相源**。DB 停在 QUEUED/IN_PROGRESS 时，
运行可能早已启动、正在饿死、甚至已被 CANCELLED。任何只读 DB status 的哨兵
对这类故障结构性失明（中枢原 18 项检查全部只读 DB）。

### ② 「正常态创建即启动，零排队（p50/p90/max 全 0.00min）」→ 测量伪影

`startedAt - created_at` 在 509 条样本上**字节级恒等于 0**——因为 DB 写入时
`startedAt` 就是 `created_at` 的拷贝。这不是「零排队」的证据，这是一个恒真式
（tautology）。**差点据此第二次得出「排队正常」的错误结论。**

判据：任何差值指标若在**全样本**上取到完全一致的常数（尤其是 0），
先怀疑两个字段同源，再谈业务含义。

## 真因定案

```
[LocalAutomationScheduler] started tickMs=30000 activeTickMs=5000 concurrency=3
```

日志里 37 条 `enqueue due automation X: concurrency limit active=N, concurrency=3`
**每一条都在同一毫秒紧跟一条 `run start: id=X`**（37/37）。
→ 并发上限只打印不执行，`concurrency=3` 是装饰性的。

用 start/finish 事件对做扫描线，真实在飞并发**逐日攀升**：

| 日期 | 在飞峰值 |
|---|---|
| 08-29 | 1 |
| 08-30 | 4 |
| 08-31 | 4 |
| 09-01 | 8 |
| 09-02 | **13** |

因果链：48 个 ACTIVE 自动化密集到点 → 超发（无上限）→ 全部挤在同一个
`codebuddy --serve` 进程 + 同一模型配额（多数 hy4-preview）→ 单条运行被饿死
→ 撞 **90min 硬超时** → `[CANCELLED]`，产物全丢。

6 条今日超时：策略执行-09:10 / 智能选股 / 科技红利聚焦扫描 / 盘中实盘-10:00 /
策略执行-10:10 / 每日数据备份（5/6 为 hy4-preview）。

这也解释了 run#66 的「批量中断」：不是调度器被打坏，是耗时劣化传导为失败率劣化
（单跑拉长 → 在飞重叠变大 → 一次宿主中断/超时清理的爆炸半径从 1 变 3）。

## 修复（已落地）

第 18 项 `check_automation_queue_backlog()` 新增子检查④，改读**调度器日志**
而非 DB：

- `_scheduler_log_signals()`：正则解析 `concurrency=(\d+)` 声明值 +
  `run start` / `run finished` 配对做扫描线，返回 `{declared, current, peak, timeouts}`；
  **日志不可读返回 None → 静默跳过（fail-safe，不误报）**。
- 超发判据：当前在飞 ≥ 声明并发 × `SCHED_INFLIGHT_FACTOR(=2)` → 告警（3→阈值 6）。
- 硬超时判据：`Run timed out` 走**独立锚点 `timeout_seen_ts`**（不复用 `last_seen_ts`），
  近 36h 窗口内 `> 锚点` 才算新增。

锚点必须独立：沿用 run#63 教训——「状态推进」与「计数累计」混用一个变量会导致
低频持续故障失明。本轮三个锚点各管一路：`last_seen_ts`（中断聚类）/
`pending`（半径2累计）/ `timeout_seen_ts`（超时）。

## 反向验证（全部通过）

| 场景 | 期望 | 实测 |
|---|---|---|
| 真实数据（在飞10/声明3） | 告警 | ✅ 4 条告警 |
| 在飞 2、5（<6） | 静默 | ✅ 静默 |
| 在飞 6（=3×2） | 首次触发 | ✅ 告警 |
| 日志不可读 | 静默 | ✅ fail-safe |
| 锚点已推进后重跑 | 超时告警消失、超发告警仍在 | ✅ 两项独立，无 run#63 式失明 |

## 可复用规则

1. **状态字段不是真相，事件日志才是。** 业务 DB 的 status 是「调度侧意图」的快照，
   与「执行侧实际发生」可以长期不一致。定位调度/并发/超时类故障，必须去日志里
   按事件对（start/finish）重建时间线。
2. **全样本恒定的差值 = 伪影，不是发现。** 尤其 0。先查两字段是否同源。
3. **声明值 ≠ 生效值。** 配置里写 `concurrency=3`、日志里打印 `concurrency limit`，
   都不代表限流被执行。验证方式：找一条「命中限流」的日志，看它后面有没有
   紧跟启动 —— 有，则限流是装饰。
4. **耗时劣化会传导为失败率劣化。** 单跑变慢 → 在飞重叠变大 → 单次中断的爆炸半径
   变大。看到失败率上升先别找失败原因，先看耗时分布是否已劣化。
5. **超发类故障要看趋势不是看点。** 08-29 峰值 1 完全健康，09-02 峰值 13 已崩，
   中间没有任何单点告警会触发——按日算峰值序列才看得见斜率。
