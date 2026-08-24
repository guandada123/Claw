### 2026-08-24 automation refusal 集群第二族：-32603 上游 500（insight → ★升级候选）

- **类型**: insight（故障分族 / 诊断纪律补全）
- **现象**: 08-24 16:22 统一巡检中枢、16:30【自动补跑】漏报直接补推 连续 2 条
  `Automation prompt stopped before completion: refusal: {...}` 失败，间隔 8 分钟。
  watchdog 归"次要"（无关键关键词）→ SILENT，行为正确。
- **根因**(DB 实锤 + 探测实锤): 与 08-20 的 429 配额族**不同源**。
  - 本例 `code=-32603, message="Internal error", data.details="500 internal server error (<trace_id>/<conv_id>)"`
    → **上游模型服务端 500**，非配额、非内容策略、非本地代理。
  - 本地代理 :9999 同期健康：`nc -z` 通、`/tmp/proxy-deepseek.stdout.log` 无报错、
    `billing model=deepseek-v4-flash track=points` 正常计费、stderr 空。
  - 恢复验证：17:11 直接打 `POST 127.0.0.1:9999/v1/chat/completions` → `HTTP=200 / 0.72s`
    → 上游已自愈，故障窗口约 16:22–16:30（≈8min 瞬时窗）。
- **连带影响核查（关键，容易漏）**: 失败的 16:30 那条正是"漏报补推"检查员——它挂了就没人核对当日产物。
  人工代跑其 STEP1：`/tmp/claw_closing_report_2026-08-24.txt`(15:59) ✅、
  `output/wx_reports/20260824_morning.md`(09:03, 19KB) ✅、
  `claw_lock_助理实盘监控_20260824` / `claw_lock_投顾策略执行_20260824` ✅
  → **今日无遗漏可补，无需补推**。副作用提示：`claw_lock_自动补跑_20260824` 已生成，
  即使该自动化重试也会被 `check_schedule` 判"被锁"跳过 —— 本次无害（无缺失），
  但若真有缺失，锁会掩盖补跑机会。
- **防复犯（诊断纪律）**:
  1. refusal 必须按 `code` 分族，**至少三族**：`-32003`=429 配额（等 reset 自愈）、
     `-32603`+`500 internal server error`=上游服务端故障（等上游自愈/换路由）、
     内容策略拒绝（罕见，须单任务孤立才成立）。禁用"refusal"字面归因。
  2. 判上游 vs 本地：先 `nc -z 127.0.0.1 9999` + tail proxy stdout/stderr；
     代理无错 + 计费正常 ⇒ 责任在上游，不要改本地配置。
  3. 确认恢复必须**主动探测**（curl 打一次 chat/completions 看 HTTP 码），
     不能只等下一次自动化运行结果。
  4. **"补跑/校验类"自动化自身失败 = 双重故障**，须人工代跑其检查项确认无真实遗漏，
     并检查其 schedule 锁是否已被占用。
- **去重**: 与 `2026-08-20-automation-429-quota-cluster.md` 同为 refusal 集群但**不同 code 族**（-32603 vs -32003）；
  与 08-11/08-13/08-17 `THINK_BUDGET=high` thinking 注入（content 空）无关——本例上游直接 500，未进入生成阶段。
- **★升级候选**: 给 `automation_failure_watchdog.py::classify()` 增加 refusal **code 分族标注**
  （digest 文案输出 `429配额` / `上游500` / `内容拒绝`，而非笼统 "refusal: {"code"`），
  并对「补跑/校验类自动化自身失败」单独提级为"需人工代核"提示（当前静默易漏连带影响）。
