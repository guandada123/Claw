# INTENT.md — 前瞻意图与待决看板（L6）

> 进行中 / 待决 / 待验证事项的统一视图。每条带状态，完成即划除或移 SCHEMA/MEMORY。
> 更新：每次会话结束前同步状态。

---

## 🟡 进行中
- **智能选股 v10.4 四大师首跑验证（08-04 09:10）**：08-03 改版后首次实跑，核对🎓段出数+降级标注；08-03 15:50辩论曾走"LLM不可用简单多数"降级，关注是否复现
- **QTS WF 信号稀疏问题（08-04 修复后遗留）**：wf_passed 条件反转已修，但 WF 测试窗口(30天)信号0-2笔致 stability 天然低 → 需评估调 train/test 窗口或提升信号频率
- **stateful backoff 全面接入（T3）**：check_backoff/record_backoff_fail 已入 preamble(08-04)，已接 RSS 同步；待接 anysearch/Wind 类自动化
- **StockInsight vite-rebase→main 合并**：#30 已 MERGED(08-04)；vite-rebase 领先 main 2提交(含 c214537 cli修复)，自动化跑本地分支已生效；合 main 涉及远程 push 待用户确认
- **选股池增量补全自动化**：1785309382755@08:30 交易日静默跑；08-04 池规模4439→4440(日增1)，观察是否稳定(预期 0~50)

## ⚪ 待决（需用户决策）
- **StockInsight vite-rebase 是否合入 main**（c214537 cli修复 + d68e33b routine，落后main 20提交）
- **QTS WF 参数是否调整**（30天测试窗口信号稀疏致 stability 低）

## ✅ 近期已闭环
- 08-04 全局执行：regime误报修复(data_points 0→383)/signal_verify os._exit根治/WF条件反转/QTS 358行/recorded_at 72条校正/验证回读4/4/backoff统一入口/RSS接入
- StockInsight #30 已 MERGED(react 19.2.8, 08-04)
- 选股池定时增量补全（refill_scan_pool.py + 自动化，07-29）
- cli.py ImportError 修复已推远程 vite-rebase(c214537, 07-29)
- react/react-dom 版本不匹配(#30) 根因修复，351 tests 绿(07-29)
- 实时行情价优先级反转（Wind→腾讯优先，07-29）
- 创业板 300/301 放开 + 单独 -15% 止损(07-29)
- 鱼盆双推根治 v5.1(07-23)｜proxy 看门狗部署(07-26)
- **记忆系统对齐 Hy-Memory（L1-L6）+ 历史日志蒸馏(07-29 16:18)**：7个超大日志(07-13~23共207.7KB)蒸馏归档至 .backups/distill-2026-07-29/，主目录仅留近30天+结构文件

## 📋 待验证/观察
- refill_scan_pool.py 每日增量：监控是否误拉退市代码（阈值 >500 才告警）
- proxy-deepseek 看门狗：Colima/WB 重启后是否自动 load 回（靠健康巡检兜底）→已修（07-30: plist KeepAlive=true→false, 脚本 launchctl load→bootstrap）
- 自动化排程铁律：单 BYHOUR 拆分后各槽是否都触发（1785284629106 等已拆）
