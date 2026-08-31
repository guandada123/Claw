# 删除类变更的危害面核验：grep 文件名 ≠ grep import

- 日期：2026-08-30
- 场景：统一巡检中枢 run#20 回读时，发现 Claw `.workbuddy/scripts/pool_health_check.py` 被删除，
  一度准备按「静默失效风险」上报，最终取证证明是**计划内的安全退役**。
- 状态：✅已升级(2026-08-30)（核验方法论）

## 触发
`git status` 出现 `D .workbuddy/scripts/pool_health_check.py`，距上次巡检仅 1 小时。
联想到 F7 教训（删/改导致检测静默失效），本能定性为「高风险」。

## 误判链（差点报错的每一步）
1. 审计文档 `audit_dupcopy_20260825.md` 写着：
   `validate_constraints.py:132 / fetch_holdings_quotes.py:79 / benchmark_helper.py:206` 依赖该模块。
   → 据此推断「删了会打断 3 个消费方」。
2. 实际 grep 全部 `*.py`：`pool_health` 仅命中**权威副本自身**（3 处，全是自己的 docstring/常量）。
3. 回看原文：那几行引用的是 `.get("holdings")`（portfolio.json 字段），
   **与 pool_health_check 模块毫无关系**，只是同一段文字里同时出现了两个文件名。
   → 行号引用被误读为 import 依赖。

## 正确核验姿势（删除/移动任何文件前，按顺序）
1. **grep 的是符号不是名字**：搜 `import X` / `from X import` / `runpy` / `subprocess.*X.py`，
   而不是搜文件名字符串。文件名在注释、审计文档、日志、git 历史里都会出现 → 全是假阳性。
2. **确认权威副本健在**：双副本场景下删的是漂移副本还是唯一副本？
   本例 `scripts/pool_health_check.py`（9787B）仍在，import 冒烟通过。
3. **import 冒烟必须用真实 sys.path 组合**：我第一次只把 `scripts/` 放进 sys.path，
   导致位于 `.workbuddy/scripts/` 的 3 个消费方报 ModuleNotFoundError —— **这是我的测试假象，不是真实故障**。
   冒烟前先确认被测文件所在目录，把该目录（而非"我以为"的目录）加进 path。
4. **查是否有回滚备份 + 是否已被授权**：本例 /tmp 有备份、且符合 08-26 用户「全自动退役」授权。

## 连带教训
- **文档里的 `file.py:NN` 行号引用，只证明"这段文字提到过这个位置"，不证明依赖关系。**
  凡看到行号引用，先打开该行看实际符号，再定性。
- **巡检发现"异常变更"时，先查「是否有计划/授权在先」**，再决定是否告警。
  本例若直接推送飞书，就是一次纯噪音误报（该动作早已被审计+授权）。
- 反向价值：这套核验也确认了「0 自动化按路径调用」这一审计结论仍成立，
  即该脚本确为孤儿资产 —— 支撑"建议摘除微信巡检项"的同类判断逻辑。
