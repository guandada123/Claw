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
