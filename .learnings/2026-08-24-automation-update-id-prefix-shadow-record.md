# automation_update 缺 ID 前缀静默新建影子记录，PAUSE 假成功

✅已升级(2026-08-30)

- 日期：2026-08-24
- 项目：Claw
- 严重度：P1（状态变更假成功 → 以为已停用实则仍在跑，冗余自动化持续空跑/可能误执行）

## 现象

将 `📈【盘中】投顾策略执行`（v1，冗余）PAUSE。`automation_update` 连续两次返回
`success: true, message: "Automation updated", status: "PAUSED"`，但数小时后核查发现该自动化
**仍是 ACTIVE 且持续按 HOURLY 触发**（17:29 仍有 last_run，next_run=18:28）。

## 根因

传入的 `id` 缺少 `automation-` 前缀（用了 `1784039339114` 而非 `automation-1784039339114`）。

平台行为：**ID 不带前缀时不报错，而是静默新建一条独立的影子记录**，并把本次
update 的 PAUSED 写入影子记录；真实调度记录完全没被触碰。

DB 实锤（`WHERE name LIKE '%投顾策略执行%'`）：

| id | status | cwds |
|---|---|---|
| `automation-1784039339114`（真实） | ACTIVE | `/Users/guan/WorkBuddy/Claw` |
| `1784039339114`（影子） | PAUSED | `/Users/guan/WorkBuddy/automation-2026-08-24-13-36-53` |

## 识破线索（可复用）

1. **工具返回的 `cwds` 与 DB 中该记录的 `cwds` 不一致** → 说明操作对象不是同一条记录。
   本例返回临时目录 `automation-2026-08-24-13-36-53`，而真实记录是 `/Users/guan/WorkBuddy/Claw`。
2. `mode=view` 传不带前缀的 ID 报 `not found`，但 `mode=update` 却"成功" → 前后不一致即危险信号。
3. 按 `name LIKE` 而非 id 查询，能立刻暴露同名双记录。

## 修复

1. 用完整 ID `automation-1784039339114` 重新 `update status=PAUSED`。
2. **readback DB 校验** `status=PAUSED`（updated_at 同步前进）才算成功。
3. `delete` 影子记录 `1784039339114`（走工具，不碰 DB）。
4. 核验替代链无缺口：v2.2 五窗口 1784506600526/634/523/665/706 今日 09:15/10:14/11:16/13:12/14:25
   全部 ACTIVE 正常执行。

## 反模式 → 正模式

- ❌ 凭 `automation_update` 返回 `success: true` 判定状态已变更。
- ❌ 传裸数字 ID（`1784039339114`）。
- ✅ ID 一律带 `automation-` 前缀（与查 `automation_runs` 表同一规则）。
- ✅ 任何 status/rrule 变更后 **readback DB**：
  `SELECT id,status,updated_at FROM automations WHERE id='automation-<x>'`。
- ✅ 校验工具返回的 `cwds`/字段是否与预期记录吻合，不吻合即怀疑操作错位。
- ✅ 停用某自动化前，先确认其替代链（本例五窗口）确实 ACTIVE 且有 last_run，避免留下功能缺口。

## 关联铁律

与既有铁律同源，互为补强：
- 「查 automation_runs 必须用带 `automation-` 前缀 ID」→ 本次证明 **写操作同样适用**。
- 「日志成功≠数据落库，凡写库须 readback」→ 本次是该铁律在自动化管理面的又一实例。
