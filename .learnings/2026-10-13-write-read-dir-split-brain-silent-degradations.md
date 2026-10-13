# ★升级候选 —— 写读异径：`parent.parent` 缺陷留下的三处「静默降级」，直到排名陈旧三周才被看见

**日期**：2026-10-13（双目录残档专项排查）
**等级**：P1（三处功能静默失效：早报排名板块陈旧三周 / 验证报告输入从未读到 / Wind 实时价从未真正调用——全部表现为"正常运行"）
**类型**：silent-degradation / path-split-brain（写入方与读取方目录口径不一致）
**状态**：三脚本已修复（commit `ed2de0f`），数据已迁移，端到端验证通过。

---

## 1. 现象（消费者视角）

**早报**「📡 外部发现（红狐API搜索，待验证胜率）」板块读到的排名**陈旧三周**（09-19），
而写入方最新数据（10-10）落在另一个目录。没有任何报错——只是"最近没有新发现"。

## 2. 取证

| 文件 | 写入方（修复前） | 读取方（修复前） | 结局 |
|---|---|---|---|
| signal_ranking.json | merge_signal_ranking → `.workbuddy/data/`（parent.parent 解析为 .workbuddy/） | wx_assembler 早报 → `data/` | **读旧写新，差 21 天** |
| discovered_accounts / subscribe_candidates | discover_gzh_accounts → `.workbuddy/data/` | merge / subscription_brief → 同目录（巧合一致） | 内部自洽，但与仓库口径分裂 |
| signal_verify_report.json | （写方在 .workbuddy/data/，正确） | merge 的引用 `_PROJECT_ROOT/.workbuddy/data/…` 在旧根下 = `.workbuddy/.workbuddy/data/…` **永不存在** | 该输入**从未被读到过**（静默丢失能力） |
| Wind 实时价 | — | discover 的 `sys.path` 指向 `.workbuddy/src`（不存在） | ImportError → **静默降级 no-op 兜底**，从未真正调用 |

**共同根因**：`Path(__file__).resolve().parent.parent` —— 在
`<repo>/.workbuddy/scripts/` 下该式为 `.workbuddy/`（而非仓库根）。
即 2026-10-12 P1-14 已立的同族缺陷（其修复注释原文：「parent.parent 该式**只在
scripts/ 下成立**；一旦搬到 .workbuddy/scripts/ 会算出 .workbuddy/.workbuddy/…」）。

## 3. 为什么长期无人发现

1. 三处都是**降级而非报错**：读空→跳过板块、导入失败→try/except 兜底、读旧→"没有新数据"；
2. split-brain 的假象是"功能正常但内容陈旧"——恰好落在**新鲜度**盲区（没有任何断言在盯它）；
3. 路径缺陷的验证方式是"跑一遍看有没有崩"——它不崩。

## 4. 教训（待周度升级为铁律）

1. 🔴 **「写入方目录」与「读取方目录」必须来自同一解析函数**（本仓既有 `_claw_root()`
   模式）。凡脚本自行拼 `parent.parent`，先假设它错了，用「写读对账」验证：
   跑一次写方，看读取方是否读到新内容（而非"是否崩溃"）。
2. 🔴 **try/except 兜底必须留痕**：凡是静默降级（no-op 兜底、读空跳过），
   首降级时必须打一条 WARNING——否则「从未通电」与「一直正常」不可区分
   （本例 Wind 导入从未成功过，兜底函数默默服务了全部历史）。
3. 🔴 **读不到输入 ≠ 此路不通**：`_VERIFY_REPORT` 型"永远不存在的路径"应加存在性断言
   （或启动日志打印实际解析路径），一次性暴露，而不是让它静默伴跑数月。
4. ⚠️ 对账口径：**新旧副本并存的目录对**（`data/` vs `.workbuddy/data/`），
   排查清单 = 「同名文件 × mtime 分叉 > 1 天」——本次 7 个分叉里揪出 1 组真 bug
   （本条已作为双目录专项的标准动作）。

## 5. 本轮已执行

- 三脚本改走 `_claw_root()` 向上搜索（与 alpha_data/heartbeat_local 同模式）；
- 数据迁移（10-10 副本 → `<Claw>/data/`，md5 校验）；旧副本归档
  `_archive/stale-migrated-files-20261013/`；
- 端到端验证：merge 产物落 `data/`（20:43）；verify report 输入复活——
  输出新增真实对比分析（「浙江经济广播 50.0% — 建议接入RSS」），证明此前该能力静默缺失。

## 6. 遗留

- 本仓仍有同类风险面：`is_trading_day.py` 等 `parent.parent` 用法**当前解析正确**
  （其 data 目录恰在 .workbuddy/ 下）——但脆弱（脚本一旦再移动即断）。
  建议后续统一收口为 `_claw_root()`（独立任务，逐脚本核对路径语义后替换）。
