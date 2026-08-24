# 2026-08-12 WorkBuddy UI 历史对话消失：根因是云端列表接口 404（correction+insight → ✅已升级(2026-08-16)）

- **发现时间**: 2026-08-12 21:04（用户报"UI 上面的记录又没了"，第三次：08-05/08-12）
- **类型**: correction + insight
- **现象**: WorkBuddy UI 侧边栏历史对话列表为空。首轮排查我把根因归为"memwatch 14:45 重启清空 sessions.json"，随后用户追问、我深挖 edge-sync.log 发现主因是云端接口 404——**归因被修正，且修正依据是日志实锤**。

## 证据链（日志实锤，非推断）
1. `~/Library/Logs/2026-08-12/edge-sync.log`：每次启动 UI 拉全量会话列表走云端接口
   `GET /console/as/conversation-sync/conversations?type=local&sourceDeviceId=...&pageSize=100` → **持续 `HTTP 404 Not Found`**。
   时间线：08-08 起每次启动都 404（08-08/09/10/11/12 共 6 次启动全 404），**早于并独立于 memwatch 重启**。
2. 本地数据完好：`LOGIN_READY_LIST total=5718`（本地 5718 条会话，每天还在增长 5209→5718）；`projects/**/*.jsonl` 6255 个会话文件、78947 条消息全在。
3. `sessions.json` 只是"当前窗口恢复列表"（10 条），memwatch 14:45 重启确实清空过它（verify_conversations 检测到 0 并告警）——这是**次要**影响，非"历史对话全部消失"主因。
4. 兜底补充 `LIST_SUPPLEMENT: fetched=0 supplemented=0` 也失效，UI 无本地降级路径。

## 根因
**WorkBuddy 5.3.12 桌面端历史会话列表依赖云端接口 `/console/as/conversation-sync/conversations`，该接口对当前账号/设备持续返回 404**（服务端接口下线/变更或账号维度问题），且客户端无本地降级 → UI 历史列表恒空。属应用/服务端问题，本地无法直接修复。

## 处置
1. **归因修正**：向用户澄清 memwatch 非主因（有 08-08 起 404 日志为证），sessions.json 被清只是窗口恢复列表的次要影响。
2. **memwatch 防复犯加固**（已落地代码，`~/.local/bin/watch_workbuddy_mem.sh` 修复五）：
   - 新增 `backup_sessions()`：do_restart 优雅退出前备份 `sessions.json → sessions.json.bak`
   - `verify_conversations()` 增强：重启后检测到空时自动从 .bak 恢复，告警区分「已恢复/无备份」
   - 已建立基线备份；zsh -n 语法校验通过；launchd 重载生效
3. **本地兜底交付**：`~/.local/bin/wb_history_server.py`（Python 标准库只读服务，端口 8791）——直读 `projects/**/*.jsonl`，绕开 404 接口，列表+搜索+完整对话查看（思考/工具调用折叠）。另产出单页索引 `~/Desktop/WorkBuddy_历史会话索引.html`。

## 防复犯
- **教训 A（correction）**：首轮我凭"memwatch 日志有会话元数据为空"就下结论"重启清空索引是主因"——**部分正确但抓错了主因**。正确顺序：先看 UI 数据链路的完整日志（edge-sync.log 的 LIST_REQ/404），再关联 memwatch。诊断纪律要求"日志实锤"应覆盖**全链路**，不只单点日志。
- **教训 B（insight）**：WorkBuddy 桌面端 UI 列表 = 云端接口 + 本地 sessions.json（窗口级），**本地历史全文在 `projects/**/*.jsonl`**——任何"历史对话消失"先查 edge-sync.log 的 LIST 结果，再决定是否用离线查看器兜底。
- **已落地**：memwatch 备份/恢复逻辑 + wb_history_server.py 工具（~/.local/bin/）+ 本条目。
- **去重**: 首次
- **再次发生**: 2026-08-14（用户报"重启后 UI 历史没了"，UI 日志实锤 20:35/20:55 两次启动 `GET /console/as/conversation-sync/conversations?pageSize=100` 均 404，与 08-12 同根因；本地数据完好：db 6445 条、projects jsonl 6988 个、SessionStore 读到 6000 会话；`wb_history_server.py`(8791) 兜底存活）
- **08-14 复犯教训（correction, ✅已升级(2026-08-16)）**: 首轮我又把主因归为"memwatch HOME bug 导致 verify_conversations 误报清空索引"——HOME bug **真实存在**（launchctl 环境无 HOME，脚本所有 `$HOME` 路径失效，10:55/20:55 两次误报"会话索引为空/jsonl=0/恢复失败"，已修复：脚本头部加 HOME 兜底 `/Users/guan`）——但它是**次因**（只影响 memwatch 自检误报，不影响 UI 历史数据），主因仍是云端 404。**教训复现：单点日志（memwatch）≠全链路，必须先查 UI 侧 main.log 的 conversation-sync 结果再归因。** 修复记录：memwatch 脚本头部加 `if [ -z "${HOME:-}" ]; then HOME=/Users/guan; fi; export HOME`，launchctl 重载生效（PID 2409），env -i 模拟验证通过。
- **升级候选理由**: ①"UI 历史对话消失"已发生 ≥3 次（08-05/08-12/08-14），同类必再犯；②带日志实锤的高价值根因（WorkBuddy 数据链路结构：UI列表=云端404接口、全文=本地jsonl、窗口=session.json）；③memwatch 在 launchctl 下 HOME 缺失导致自检误报，需在 auto-promote-learnings 中强化"全链路取证"纪律。 ✅已升级(2026-08-16)：升为🔴铁律「单链路日志≠全链路, 禁脑补归因」。 ✅已升级(2026-08-16)：升为🔴铁律「单链路日志≠全链路, 禁脑补归因」。
