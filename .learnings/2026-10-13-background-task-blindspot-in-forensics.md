# ★升级候选 —— 「未执行」的判决必须查后台任务事件；且先读项目自己的日日志

**日期**：2026-10-13（信号溯源自动化取证复盘）
**等级**：P1（**取证误判**——把一次**成功**的后台执行判成「未执行」，虚假根因写入两版自动化提示词（已更正）、审计报告（已更正），并触发一次冗余管线重跑）
**类型**：forensics-scope-blindspot（日志取证只覆盖前台事件）+ record-order（未先读项目自身记录）
**状态**：误判已全部更正（提示词/报告/本 learnings 留档）；真值锚：taskId=KLOSra。

---

## 1. 误判与真值

| | 我当时的结论 | 真值（事件证据） |
|---|---|---|
| 15:05 自动化 | 「**始终未执行管线**」，会话只做了调研 | **管线后台执行成功**：`executeInBackground` 15:05:59 → `background task created taskId=KLOSra` → `completed` 15:14:09 → `finalized status=completed evidence=exit-event elapsed=489890ms`（与日日志「489.8s, 7/7, exit0」逐字吻合） |
| 会话行为 | 「调试模式、不干活」 | 管线在后台跑，会话在**等待期并发**做了调研（token 浪费，但任务完成） |

## 2. 两个盲区（根因）

1. **日志取证口径只覆盖前台**：我的 grep 固定为 `foreground completed` 行——后台任务的事件类型（`executeInBackground` / `background task created|completed|finalized`）从未进入检索范围。「没查到」被等价为「没发生」——**取证口径决定结论，口径错则结论反**。
2. **未先读项目自己的记录**：真值一直躺在 `<Claw>/.workbuddy/memory/2026-10-13.md`（15:15 由该轮会话自己写入：「全流程脚本 489.8s(8.2min), 7/7 成功, exit0」）。我在取证时跳过了项目日日志，直接扎进原始 session log 做模式匹配——**项目自己的日志是「结论层」，session log 是「原始层」；先结论后原始，永远。**

## 3. 教训（待周度升级为铁律）

1. 🔴 **判「任务未执行/失败」前，日志检索必须覆盖全部事件类型**（至少：`execute start` /
   `foreground completed` / `executeInBackground` / `background task created|completed|finalized` /
   `elapsed=`）。只查前台 = 系统性遗漏后台路径。
   （同族既有铁律：「判自动化是否派发只认 run start/finished 事件对」——本次是把该纪律
   用错了范围：只认了自己设定的半个事件集。）
2. 🔴 **排查项目内任务，先读项目自己的日日志/memory**（结论层），再下钻 session log（原始层）。
   项目日志通常已记录成败与产物路径；跳过它 = 用最贵的方式重新回答已经回答过的问题。
3. 🔴 **修订文档/提示词时，凡写「实测/事实」必须附可复验的事件锚**（taskId / 时间戳 / 命令），
   防止把误判固化成"史实"。本次两版提示词的更正块已附 `taskId=KLOSra` 锚点。

## 4. 连带更正

- 两版信号溯源提示词（05:05 / 15:05）：「未执行管线」表述撤销，改为「后台执行成功」校准记录 +
  「等待期不做无关调研（省 token）」的软纪律；
- 审计报告：追加「更正二」章节；
- 遗留真问题不变：Wind 积分耗尽（需充值）、`.workbuddy/data/` 残档（已归档）、
  parent.parent 缺陷族 6 脚本（已修）——这些与本次误判无关，各自独立成立。
