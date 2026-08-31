# last_run_at 平台字段对 ACCEPTED 运行不回写 —— 自动化健康监控基线整体失真

> 发现日期：2026-08-31 15:53（统一巡检中枢第 37 次运行）
> 严重度：P1（不报警时无感，一旦触发就是成片误报）
> ★升级候选

## 现象

巡检中枢报 🔴「Claw→QTS 模拟盘 portfolio 同步 … 48h未运行」。

查 SQLite `~/.workbuddy/workbuddy.db`，**59 个 ACTIVE 自动化里 52 个（88%）的 `last_run_at` 滞后**，
滞后量精确等于各自调度周期的整数倍：

| 调度周期 | 滞后量 | 例 |
|---|---|---|
| 日频（每日） | 47.9–48.0h | 记忆维护 / Dependabot / 盘中监控 |
| 日频（MO-FR） | 71.9–72.0h | 策略执行-09:10 / 鱼盆 / 财报日历 |
| 周频 | 167.9–168.0h | 多仓库周报 / 池检 / 跨项目经验升级 |
| 6h | 42.2–42.5h | 巡检中枢 / watchdog / 数据备份 |

且这些自动化**最近一次运行的状态全部是 `ACCEPTED`**；最后被回写 `last_run_at` 的那次运行状态是 `PENDING_REVIEW`。

## 根因

平台**只在 run 走到 `PENDING_REVIEW` 等终态时才回写 `automations.last_run_at`**；
托管自动执行（`ACCEPTED`，无需人工复核）的运行不回写。08-29 起运行全部转为 ACCEPTED，
于是 `last_run_at` 集体冻结在 08-28/08-29。

`automation_health.py` 的静默失败判定直接用该字段算 `hours_ago` → gap 基线整体偏大。

## 为什么只炸了 1 个而不是 52 个

判定代码里有一道短路（line 190-192）：

```python
if next_run and next_run > datetime.now():
    stale = False   # 已正确排期，跳过静默失败判定
```

绝大多数自动化 `next_run_at` 在未来 → 短路跳过 → 不报。
只有「`next_run_at` 已过期 / 缺失」的才走 gap 回退，此时拿滞后的 `last_run_at`
算出 48.8h > 48h 阈值 → 🔴。

**即：真正触发 🔴 的条件是「排期元数据没刷新」，而不是「任务真的没运行」。**
这是把 A 现象误判成 B 故障。

## 修复

`automation_health.py::check_health()`：

```python
platform_last_ts = _to_ms(auto.get("last_run_at"))
newest_run_ts    = _to_ms(runs[0].get("created_at")) if runs else 0
last_run_ts      = max(platform_last_ts, newest_run_ts)
```

`automation_runs` 表**每次派发都会落记录**（无条件写入），是权威运行时间。
另加诊断标注 `last_run_at字段滞后Nh`，不改变健康色。

## 验证（功效验证须复现故障条件）

写 `/tmp/verify_ah_fix.py`，用真实 DB 数据做新旧对照：

- **故障条件**（把所有 `next_run_at` 置为 1 小时前，强制走 gap 回退）：
  **24 项健康色改善 —— 5 个 🔴→🟢、18 个 🟡→🟢、1 个 🟡→🔵，零恶化。**
- **回归检查**（`next_run_at` 在未来，正常路径）：**差异 0 项。**
- ruff 通过；实跑 crit/warn 由 0/8 → 0/7，无退化。

复现旧行为的手法：深拷后把 `recent_runs` 里每项的 `created_at` 键删掉，
`newest_run_ts` 归零 → 新逻辑退化为旧逻辑，其余判据保持不变，对照干净。

## 残余盲区（未修，记录在案）

若「已派发但 agent 未启动」（`automation_runs` 有记录、脚本却没执行），
新逻辑会视为已运行而漏报。要堵需引入**执行侧心跳/产物锚**，非本次范围。

## 通用教训（★）

1. **监控脚本不要只信平台提供的「便利字段」，要找「无条件写入」的锚。**
   本例中 `last_run_at` 是条件写入（终态才回写），`automation_runs` 是无条件写入。
   与既有铁律同源：08-12「单链路日志 ≠ 全链路」、08-30「条件触发型日志不能反推没运行」、
   「文件名含 log 不等于全量日志」—— **判定前先问：这个字段在什么条件下才写？**
2. **健康判定里的「短路分支」要审它屏蔽的是真信号还是噪声。**
   本例 `next_run_at > now → stale=False` 屏蔽了 52 个潜在误报，是有效的；
   但反过来说，它也掩盖了字段失真这个底层问题长达若干天。
3. **改监控判据的验证模板**：必须同时跑「故障条件」与「正常路径」双向对照，
   只看实跑输出不够 —— 实跑时短路分支生效，改动可能根本没被执行到（本次实跑只差 1 项 🟡，
   而故障条件下差 24 项）。
