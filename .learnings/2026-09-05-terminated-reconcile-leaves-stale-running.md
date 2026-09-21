# terminated 终止路径 reconcile 漏清 stale running 标记（2026-09-05，✅已升级(2026-09-06)）

## 现象

信号溯源 05:00（automation-1780964240621）今晨 05:05 触发后 82.8 分钟被
宿主 terminated（thread_title: "Automation session ended with status: terminated"）。
terminated 后调度器**每 30s 一条 skip 刷屏**（192 条/1.3h），状态卡死：

```
skip due automation automation-1780964240621 (...): running=1, lastRunAt=..., nextRunAt=<今天05:05 不再推进>
```

## 根因（平台 reconcile 双路径不一致）

terminated 同一秒（06:27:51）日志出现 `reconciled 1/1 in-progress automation run records`，
但 reconcile 只把 **automation_runs 表**该 run 标记为
`reconciledFromInProgress:true / resultState:failed`，**漏清
automation_runtime_state 的 running=1 / running_started_at / running_conversation_id**。

对照组（关键）：09-04 的 **Run timed out** 终止后 running 被清干净
（0 条 skip），05:05 才得以正常触发。→ **timed out 路径清 running，terminated 路径漏清**，
平台 bug。

## 影响

- stale running=1 期间调度器持续 skip，**nextRunAt 停在已过期档位不再推进**；
- 会挡**下一次到点触发**（对比佐证：能正常触发的那次，触发前 running 已被清为 0）。
- 修正旧结论「running=1 不阻止新 run 触发」—— 旧例能触发是因为残留恰好在
  触发前被某次 reconcile 清掉，不代表 running=1 无害。

## 处置（止血，可回滚）

1. 备份原值 → `/tmp/runtime_state_backup_1780964240621.txt`
2. `UPDATE automation_runtime_state SET running=0, running_started_at=NULL,
   running_conversation_id=NULL WHERE automation_id='<id>'`
3. 效果：skip 停止 → 调度器**立即补跑**过期档 → 63s success（非交易日门禁跳过，
   v10.0 首个验证点达成）→ next_run_at 恢复推进到次日 05:05。

## 通用教训

**① 调度器触发语义**：nextRunAt 到点 **且 running=0** 才触发；
stale running=1 会把档位永久卡死（skip 循环 + nextRunAt 不推进）。
排查"某自动化不触发了"，先查 `automation_runtime_state.running` 是否 stale，
别只盯 automation.log 的 run start 事件。

**② 判终止路径要区分 "Run timed out" 与 "session terminated"**：
同一 reconcile 机制两条路径，清理行为不同。terminated 后若出现 skip 刷屏
= reconcile 漏清的信号，直接查 runtime_state 即可实锤。

**③ 会话复用（conversationId 5774cc91 连续 5 天复用）是放大器**：
09-01~09-05 五次 run 共用同一 conversationId，LLM 上下文跨天累积，
可能是旧版周六门禁没拦住的诱因之一。平台侧问题，记档观察。

**④ 单行 DB 止血安全范式**：备份原值到 /tmp → UPDATE 只动目标列 →
立即验证（skip 停 / next_run_at 推进）→ 记录到 memory。

## 上游归因补充（2026-09-05 追查"为什么最近 terminated 频发"）

**宿主会话中断（Conversation ended / session terminated）与 hy4-preview 运行期完全同现同灭**
（教科书级对照，中枢 6 次失败全部反查会话实际 model 验证）：

| 中枢运行期 | 会话 model（sessions 表实测） | Conversation ended / terminated |
|---|---|---|
| 08-06~08-29（hy3 时代） | hy3 | 0 |
| 08-30~09-02（08-29 晚 51 个 hy3→hy4-preview 全量切换后） | **hy4-preview** | **5 次**（4×Conversation ended + 1×terminated） |
| 09-03 起（已回 hy3） | hy3 | 0 |

- "Conversation ended before automation request completed" 全历史 9 次（日志覆盖 8/29 21:46 起）
  **全部在切换后**，首现 08-30 11:27 = 切后第一个上午。
- 机制自洽：hy4-preview 官方承认"慢热/长思考"（08-29 切换时已在记忆预警）→
  单步 LLM 调用变慢 → 宿主会话生命周期 watchdog 回收"长时未完成"会话。
- 中枢 08-31 取证哨兵（hy3 跑）标注唯一观察项 = 中枢尾延迟 p90=526.6s ≈ hy3 的 2.4 倍，
  与归因一致；其"当天 0 失败"结论有截断盲区（15:30 统计截止，15:49 两次失败未被覆盖）。
- **教训 ⑤：归因"宿主层错误"先反查 sessions 表实际 model**（automations.model_id 是
  意图值，sessions.model 才是该 run 真实跑的模型），按 model 分段统计失败即可一锤定音。
- **教训 ⑥：观测者须与被观测对象隔离** —— 取证/告警类任务应固定最稳模型（hy3），
  勿随大流切新模型（08-29 已立此规矩，哨兵都是 hy3，本次归因才可信）。
