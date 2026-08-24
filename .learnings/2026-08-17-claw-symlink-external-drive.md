# ✅已升级(2026-08-23)：Claw 路径为外置 SSD 符号链接，自动化存在静默失败风险

**发现时间**：2026-08-17 16:21（巡检中枢存活看门狗 automation-1786000625371 运行时）
**严重度**：P1（影响监控类自动化可靠性，非数据损坏）

## 现象（已取证）
- `ls /Users/guan/WorkBuddy/Claw/.workbuddy/scripts/ops_center_liveness_watchdog.py` 与
  `automation_preamble.sh` 在运行初期均报 **不存在**；`find /Users/guan -name ops_center_liveness_watchdog.py` 返回空。
- `git rev-parse --show-toplevel` → `/Volumes/ZHITAI/WorkBuddy/Claw`；
  `readlink -f /Users/guan/WorkBuddy/Claw` → `/Volumes/ZHITAI/WorkBuddy/Claw`（同 inode 8408，确认是符号链接）。
- `mount | grep zhitai` 后续显示盘已挂载，且此时两文件在真实路径 `/Volumes/ZHITAI/WorkBuddy/Claw/.workbuddy/scripts/` 均正常存在（watchdog 4076B / preamble 16347B）。
- 结论：运行起始时刻外置 SSD **尚未完成自动挂载**，符号链接解析失败 → 两个被引用脚本"消失"；盘挂载后恢复。

## 根因
macOS 外置 APFS 卷（ZHITAI）非始终在线，存在自动挂载时序窗口。任何以
`/Users/guan/WorkBuddy/Claw/...` 为硬编码路径、且在外置盘未挂载时触发的自动化，
都会因文件不可达而失败。本看门狗任务正文即 `python3 /Users/guan/WorkBuddy/Claw/.workbuddy/scripts/ops_center_liveness_watchdog.py`
——若其自身触发时盘未挂载，看门狗直接报错、不读 last_ok_ts、不推送，**形成监控盲区**。

## 影响范围
- 所有引用 `/Users/guan/WorkBuddy/Claw`（= ZHITAI 外置盘）的定时自动化：巡检看门狗、各类 Claw 定时任务。
- 本次未造成实际失联漏报（盘在真实执行时已挂载，看门狗正常读到 last_ok_ts=15:50:10、间隔 31.1min、SILENT）。

## 处置建议（待升级为铁律/护栏）
1. **路径去符号链接化**：自动化任务内统一用 `realpath` 或直写真实挂载路径 `/Volumes/ZHITAI/WorkBuddy/Claw/...`，避免依赖符号链接在挂载窗口内的解析。
2. **挂载自检护栏**：在 preamble 或任务入口加 `mount | grep -q zhitai || diskutil mount ZHITAI`（或等价），盘未挂载则显式告警而非静默失败。
3. **看门狗自身需被看门狗**：存活看门狗若因盘未挂载失败，没有任何二次兜底层能发现——建议给本自动化本身加"运行即写心跳"的轻量探针，外部（如 QTS 侧或 WorkBuddy 自身失败扫描）可据此发现它没跑。

## 经验闭环
- 同类不反复犯：诊断"文件缺失"必须先 `git ls-files` + `readlink` 确认是真实删除还是挂载/符号链接时序问题，禁止一见"文件不存在"就当作源码丢失去重建。
- 本次误判风险已规避：初期看似"源码被删"，实为外置盘未挂载，git HEAD 仍完整持有两文件，无需恢复。
