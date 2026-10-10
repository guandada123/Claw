# 文档类候选落地 · 校准报告 2026-10-11 00:41

- 模式：**live**（calibrate=只读零写入；live=产出待执行计划，由 agent 落地）
- 过闸并入选：3 条（top_n 限流后）
- 被滤除：66 条

## 本会改什么（入选）
1. `disc-20261010-01` · unified-inspection-hub · 价值=high / 风险=low
   - 目标锚点：**Claw** · 轨道=external_trend
   - 目标文件：`references/silent_failure_guard.md`
   - 成功指标：Claw 新增 references/silent_failure_guard.md 且该文件被 unified-inspection-hub SKILL.md 通过链接引用其中『下界异常(0.1x)静默死亡检测 + wrapper 记录退出码 + 独立看门狗』三节
   - 来源：https://kaxo.io/insights/ai-agent-observability-practitioner-guide
   - 依据：借鉴 2026 静默失效防护范式（dead-man's-switch + wrapper 记录退出码 + 下界成本/吞吐异常 0.1x 静默死亡检测 + 对『结果』而非『进程』做心跳），落成 Claw references 文档并被 SKILL.md 引用。直击 Claw 头号故障模式（10-09 多条 .learnings：上游修≠链路修、累计净差被当并发、L2/L3 静默失败）——当前 automation_failure_watchdog.py(549行) 无 lower/cost/throughput/anomaly/silent/dead/heartbeat 任何检测。
2. `disc-20261011-01` · unified-inspection-hub · 价值=high / 风险=low
   - 目标锚点：**Claw** · 轨道=project_signal
   - 目标文件：`references/work_bearing_heartbeat.md`
   - 成功指标：references/work_bearing_heartbeat.md 新增『工作型心跳』小节（心跳须实际调用≥1真实端点/读状态文件/做判断，而非根路径 ping），且 unified_ops_center.py 存活看门狗不再以根路径 HTTP 200 单独判健康；grep 命中 work_bearing 且 tests 覆盖『根路径200但下游死』用例
   - 来源：file:///Users/guan/WorkBuddy/Claw/.learnings/2026-09-29-liveness-probe-must-hit-real-endpoint-not-root.md
   - 依据：把 09-29 已记录的『存活探针打根路径误判修复3天』事故 + 外部 work-bearing heartbeat 范式（moltbook：liveness≠usefulness，心跳必须旋转执行真实任务）落地为可复核防护：存活看门狗每个周期实际调用≥1真实端点并断言返回，根路径 200 不得单独判健康。外部佐证 pykero/glassflow/dev.to 同指向『exit=0 不等于成功、空输出≠健康』。
3. `disc-20261011-03` · majiu-management · 价值=high / 风险=low
   - 目标锚点：**meta** · 轨道=external_trend
   - 目标文件：`references/skill_authoring_standard.md`
   - 成功指标：references/skill_authoring_standard.md 新增『注册三元组』小节（语义描述+参数契约+实现代码 缺一不可），且被 skill-creator 或 majiu-management SKILL.md 引用；grep 命中『注册三元组』即视为落地
   - 来源：https://blog.csdn.net/tsh2005974tsh/article/details/162915358
   - 依据：外部实证（CSDN tsh2005974tsh）：skill 注册三元组齐全→误调率4%+参数错率2%，远优于裸工具18%+23%；运维1人/周换2.6次/月事故避免。把『语义描述(路由)+参数契约(校验)+实现代码(载体)缺一不可』写入技能编写标准，作为 skill-creator 的准入校验基线。

## 被滤除（质量闸）
- `disc-20260922-01` — status=applied 不在允许集
- `disc-20260922-02` — status=rejected 不在允许集
- `disc-20260922-03` — status=applied 不在允许集
- `disc-20260923-01` — status=applied 不在允许集
- `disc-20260923-02` — status=applied 不在允许集
- `disc-20260923-03` — status=rejected 不在允许集
- `disc-20260924-01` — status=applied 不在允许集
- `disc-20260924-02` — status=applied 不在允许集
- `disc-20260924-03` — status=applied 不在允许集
- `disc-20260925-01` — status=applied 不在允许集
- `disc-20260925-02` — status=applied 不在允许集
- `disc-20260925-03` — status=applied 不在允许集
- `disc-20260925-04` — status=applied 不在允许集
- `disc-20260925-05` — status=applied 不在允许集
- `disc-20260926-01` — status=applied 不在允许集
- `disc-20260926-02` — status=applied 不在允许集
- `disc-20260926-03` — status=applied 不在允许集
- `disc-20260927-01` — status=applied 不在允许集
- `disc-20260927-02` — status=applied 不在允许集
- `disc-20260927-03` — status=applied 不在允许集
- `disc-20260928-01` — status=applied 不在允许集
- `disc-20260928-02` — status=applied 不在允许集
- `disc-20260928-03` — status=applied 不在允许集
- `disc-20260929-01` — status=applied 不在允许集
- `disc-20260929-02` — status=applied 不在允许集
- `disc-20260929-03` — status=applied 不在允许集
- `disc-20260930-01` — status=applied 不在允许集
- `disc-20260930-02` — status=superseded 不在允许集
- `disc-20260930-03` — status=applied 不在允许集
- `disc-20261001-01` — status=superseded 不在允许集
- `disc-20261001-02` — status=applied 不在允许集
- `disc-20261001-03` — status=applied 不在允许集
- `disc-20261002-01` — status=applied 不在允许集
- `disc-20261002-02` — status=applied 不在允许集
- `disc-20261002-03` — status=applied 不在允许集
- `disc-20261003-01` — status=applied 不在允许集
- `disc-20261003-02` — status=applied 不在允许集
- `disc-20261003-03` — status=applied 不在允许集
- `disc-20261004-01` — status=applied 不在允许集
- `disc-20261004-02` — status=applied 不在允许集
- `disc-20261004-03` — status=applied 不在允许集
- `disc-20261005-01` — status=applied 不在允许集
- `disc-20261005-02` — status=rejected 不在允许集
- `disc-20261005-03` — status=rejected 不在允许集
- `disc-20261006-01` — status=rejected 不在允许集
- `disc-20261006-02` — status=applied 不在允许集
- `disc-20261006-03` — status=applied 不在允许集
- `disc-20261007-01` — status=rejected 不在允许集
- `disc-20261007-02` — status=rejected 不在允许集
- `disc-20261007-03` — status=rejected 不在允许集
- `disc-20261008-01` — status=rejected 不在允许集
- `disc-20261008-02` — status=rejected 不在允许集
- `disc-20261008-03` — status=applied 不在允许集
- `disc-20261009-01` — status=rejected 不在允许集
- `disc-20261009-02` — status=applied 不在允许集
- `disc-20261009-03` — status=rejected 不在允许集
- `disc-20261009-04` — status=applied 不在允许集
- `disc-20261009-05` — status=applied 不在允许集
- `disc-20261009-06` — status=applied 不在允许集
- `disc-20261009-07` — status=applied 不在允许集
- `disc-20261009-08` — status=applied 不在允许集
- `disc-20261010-02` — status=proposed 不在允许集
- `disc-20261010-03` — status=proposed 不在允许集
- `disc-20261009-09` — status=applied 不在允许集
- `disc-20261010-04` — status=applied 不在允许集
- `disc-20261011-02` — status=proposed 不在允许集

## v1.10 指标闸（新）
- 因**无目标锚点**被拦：0 条
- 因**无可判定指标**被拦：0 条
- 药方：在「🛡️ 统一发现-每日扫描」里给候选补 `target_project` + `success_metric`（可判定、可复核）后才进本通道；
  补不出指标的 → 在周度排序里落到 `Assess`（先评估）或 `rejected`，**不再默认落地**。

## 说明
- 校准期（calibrate）本脚本**不写任何 skill 文档**，仅供对拍「本会改什么」。
- 生效期（live）由「📥 每日·文档类落地」自动化里的 agent 合成文档，每批 = 1 commit + 1 行 CHANGELOG + 1 个回滚点，top≤3。
- 核心类（SKILL.md 主流程 / 脚本 / 自动化 prompt）**永不**进本通道，恒走周度审。
