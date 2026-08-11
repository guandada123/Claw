# ★升级候选 · 统一巡检中枢整窗失联（~5.3h）

- **发现时间**：2026-08-12 05:01（巡检中枢存活看门狗 automation-1786000625371 每2h 触发）
- **严重度**：高（中枢自身失联，所有依赖它的健康巡检/Docker自愈/CI红扫描在失联期间完全停摆）
- **是否真实告警**：是 — 看门狗历史上首次真实命中（此前 ~70 轮全部 SILENT）

## 证据链（先取证再下结论，禁脑补）
1. 状态锚 `monitoring.global.unified_ops_center.self_health.last_ok_ts = 2026-08-11T23:46:09`（已用 python 直接读 cross_project_state.json 核实，非脚本单侧）。
2. 看门狗算间隔 = 315.5 min > 阈值 180 min → 已推飞书主群告警。
3. 真正写心跳的中枢 = `automation-1785982929477`（🛡️ 统一巡检中枢，每小时，运行 unified_ops_center.py）。看门狗告警文案原误写为 1785506975961（那是 failure-scan watchdog，已修）。
4. 中枢运行时间线：22:47:29(success=1) → **23:44:29(success=0，title=`Run interrupted because the automation orchestrator restarted`)** → 之后 00:47~04:47 共 5 个整点档 **全部未触发** → 静默至 05:03。
5. 失败签名与近期 failure-scan 记录的「会话未拉起（排队/资源紧张）」同源 = orchestrator 重启/硬杀类根因。

## 根因
23:44 中枢运行被 orchestrator 重启打断（success=0），随后调度系统在夜间整窗未再触发该中枢的整点档（疑似 orchestrator 重启后调度回退/资源紧张丢档），导致 last_ok_ts 卡死约 5.3h。

## 处置
- 看门狗按设计正确检测并推飞书告警（独立于中枢的互相 watchdog 韧性结构生效）。
- 调度已自愈：failure-scan watchdog(1785506975961) 于 04:57:38 成功运行(success=1)，中枢应在下一整点档(~05:47)自动恢复；需回读 last_ok_ts 确认。
- 修 watchdog 告警文案错误 ID（1785506975961 → 1785982929477）。

## 待办 / 升级建议
- [ ] 次日回读确认 last_ok_ts 已刷新至 ~05:47 后，中枢恢复连续性。
- [ ] ★若此类「orchestrator 重启打断 + 夜间整窗丢档」反复出现，应升级为铁律：中枢重跑兜底（看门狗超阈值后除了告警，自动尝试触发中枢重跑 / 或直接本地执行 unified_ops_center.py --no-push 兜底刷新心跳）。
- [ ] 评估：中枢整窗失联期间，Docker 自愈/CI红扫描/push_feishu 自检等依赖项是否也同步停摆 → 是否需各自独立兜底。
