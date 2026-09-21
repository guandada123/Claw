# 平台 status=ACCEPTED 掩盖「运行被中断」：真故障被降级成模糊的历史错误

**日期**：2026-09-02（统一巡检中枢 核查）
**等级**：P0（任务白跑、产物未落盘，但巡检显示 🟡 而非 🔴）
**状态**：已修复并验证（检测侧）；根因在宿主侧，待观察
**✅已升级(2026-09-06)**：是（「平台终态口径 ≠ 业务成功」是可复用的判据原则）

---

## 1. 现象

`automation_health.py` 报 6 条 🟡，文案形如：

```
🟡 🐟 鱼盆主生成（Wind+东财自建）: last_run_at字段滞后96h, 历史错误: [CANCELLED] Automation prompt interrupte
```

两个问题叠在一起：

1. **`last_run_at字段滞后96h` 是纯噪音**（该字段系统性不同步，见下）
2. **`历史错误: [CANCELLED]...` 把真故障降级成了模糊表述**

## 2. 取证：真故障藏在 metadata 里，不在 status 里

查 `automation_runs` 最近一次运行：

| 自动化 | status | result_success | metadata |
|---|---|---|---|
| 鱼盆主生成 | ACCEPTED | **0** | `interrupted=True` / `resultState=partial_delivered` / `resultEvidence=none` |
| 鱼盆数据提取 | ACCEPTED | **0** | 同上 |
| 信号溯源+CSV(05:00) | ACCEPTED | **0** | 同上 |
| 工程质量报告 | ACCEPTED | **0** | 同上 |
| 全网挖掘+文章精读 | ACCEPTED | **0** | 同上 |

**决定性证据**：这 5 条共享同一个 `conversationId = 5774cc91-c120-45ba-83f5-04b34db4670f`，
且 `finishedAt` **时间戳完全相同**（1788298900094 = 09-02 05:41:40；另一批 09-01 09:36）。
→ 不是各自失败，是**宿主会话级批量中断**，在飞任务被一起杀掉，产物未落盘。

## 3. 根因（两层）

**层一 · 检测漏检**：平台把被中断的 run 仍记为 `status=ACCEPTED`（非 failed/timeout），
而 `check_health` 只判 `last_status in (failed, timeout)` → 完全抓不到，
只能落到兜底分支 `if last_error: health = "🟡"` 显示为「历史错误」。
**看 status 判定成功与否，是对平台口径的误信。**

**层二 · 字段不同步放大噪音**：`automations.last_run_at` 冻结在 08-28/08-29，
而 `automation_runtime_state.last_run_at` 是正确的 09-01/09-02。
实测 **56 个自动化**都有 72~168h 滞后（滞后量恰为各自调度周期整数倍）——
平台对 ACCEPTED 类 run 不回写该字段。
原实现把「滞后 Nh」无条件塞进 issues，于是 6 个正常任务被标假异常，
真故障（运行被中断）混在其中同级展示 → **噪音掩盖信号**。

## 4. 修复

`automation_health.py`：

1. **新增中断检测**：读 `metadata_json.interrupted` / `resultState`，
   命中 `interrupted=True` 或 `partial_delivered` → **🔴「最近运行被中断(产物未落盘)」**
   （判据取 metadata 而非 status）
2. **降级滞后标注**：`last_run_at滞后` 仅当 >168h 才进文案；
   日常滞后写入 JSON 的 `field_lag_h` 诊断字段，不进告警
3. **同源去重**：已判中断的，`runtime.last_error`（内容即该次中断）不再重复计一条

## 5. 验证

| 指标 | 修复前 | 修复后 |
|---|---|---|
| 健康分布 | 🟢55 🟡9 🔴0 | 🟢55 🟡3 **🔴6** |
| 假噪音 | 6 条「滞后96h」 | 0 |
| 真故障可见性 | 模糊「历史错误」 | 🔴 明确「产物未落盘」 |

中枢侧 `check_automation_health` 已能带出完整 6 项 🔴 明细
（修复前曾退化为「退出码 1: 」空壳）。

## 6. 通用教训

**① 平台终态字段不等于业务成功。**
`status=ACCEPTED` 只说明「调度已接受」，不代表跑完、更不代表有产物。
判断成败要找**业务侧证据**：`result_success`、`metadata.resultState`、
`resultEvidence`、产物文件 mtime。

**② 同时中断 = 宿主问题，不是任务问题。**
判据：共享 `conversationId` + `finishedAt` 完全相同 + 跨业务线。
此时逐个改任务是白费力气，该查宿主（应用重启/会话回收）。

**③ 噪音与信号同级展示时，噪音赢。**
一个字段的系统性滞后（56 个任务都中招）足以淹没 5 个真故障。
凡「每个对象都会有」的标注，一律不进告警文案。
