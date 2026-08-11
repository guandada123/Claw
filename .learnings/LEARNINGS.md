# LEARNINGS (Claw)

Corrections, insights, and knowledge gaps captured during development.\n\n**Categories**: correction | insight | best_practice | knowledge_gap

---
### 2026-08-11 辩论降级根因脑补误判（correction → ★升级候选）
- **类型**: correction
- **现象**: 09:10 投顾槽位辩论 valuation 专家 reasoning-only 失败降级 HOLD。我先脑补"路由到推理模型/代理挂了"，被用户两次追问才查日志。
- **根因**(日志实锤): `com.workbuddy.proxy-deepseek.plist` 设 `THINK_BUDGET=high` → 代理对 `deepseek-v4-flash` 注入 `extra_body.thinking` → 返回主 content 空仅 reasoning_content 有值 → `debate_engine._call_llm` content 空即抛错。日志铁证：`🧠 Thinking mode injected: budget=high` + `track=points is_direct=false`（全走积分池）。
- **处置**: 修复 A — `_call_llm` content 空回退 reasoning_content；加 `_is_noise_response()` 过滤服务端垃圾 token 重试。
- **防复犯**: 全局 MEMORY.md 🔴铁律新增诊断纪律 + `automation-llm-local-proxy` skill 补「故障诊断流程」节 + 本 incident-triage skill。
- **去重**: 首次

### 2026-08-11 诊断纪律：先取证禁脑补（best_practice → ★升级候选）
- **类型**: best_practice
- **现象**: 用户指出"老自己脑补结论，不是第一次犯"。
- **根因**: 故障排查未执行"先看日志再定性"纪律，凭表面现象猜根因。
- **处置**: 固化三步——①`tail`代理日志+`nc -z`探存活 ②区分三类故障(连不上/reasoning-only/internal error) ③没日志实锤前不抛推断结论。
- **防复犯**: 写入全局 🔴铁律 + incident-triage skill Step1。
- **去重**: 首次

### 2026-08-12 双自动化并发修改同一脚本致 WB 停机 5h11m（best_practice → ★升级候选）
- **类型**: best_practice
- **现象**: 08-11 23:44 memwatch 正确触发重启(修复一生效, 8500阈值+5s采样抢在系统OOM前), 但 do_restart 清理残留后正要 open 拉起时, 23:45:44 统一巡检中枢 Runbook#1(memwatch_bump) 因"阈值8500<10000且90min内有重启"执行 unload/load 打断重启流程 → open 未执行 → WB 停机至 04:56 用户手动打开。
- **根因**(日志实锤): ①两个自动化(memwatch 守护 + unified_ops_center 巡检)并发操作同一目标(脚本文件+launchd), 无互斥协调 ②巡检中枢"识别根因"把正确的保护性重启误判为"误杀盘前自动化" ③memwatch_bump 直接 sed 脚本文件改阈值, 与 memwatch 自身 do_restart 冲突。
- **处置**: ①RSS_RESTART_MB 恢复 8500(修复二误改回10000) ②memwatch 强杀识别升级"告警+自动拉起" ③unified_ops_center MEMWATCH_TARGET_MB 10000→8500 消除拉锯 ④bump 前检查 2min 内"触发重启"日志(do_restart 进行中)即跳过, 防打断。
- **防复犯**: 单一配置源(参数进 launchd plist EnvironmentVariables, 脚本只读逻辑) + 巡检中枢改 plist 而非 sed 脚本 + 写前 2min 护栏 + 调参先看 memwatch 日志实锤。
- **去重**: 首次
