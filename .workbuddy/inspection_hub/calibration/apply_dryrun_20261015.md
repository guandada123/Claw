# 文档类候选落地 · 校准报告 2026-10-15 00:40

- 模式：**live**（calibrate=只读零写入；live=产出待执行计划，由 agent 落地）
- 过闸并入选：2 条（top_n 限流后）
- 被滤除：81 条

## 本会改什么（入选）
1. `disc-20261014-03` · unified-inspection-hub · 价值=high / 风险=low
   - 目标锚点：**Claw** · 轨道=project_signal
   - 目标文件：`references/canonical_path_staleness.md`
   - 成功指标：references/canonical_path_staleness.md 新增『判文件陈旧前必须先定 canonical 路径』小节，且被 unified-inspection_hub SKILL.md 或 staleness 判据脚本引用
   - 来源：file:///Users/guan/WorkBuddy/Claw/.learnings/2026-10-13-canonical-path-before-staleness-verdict.md
   - 依据：Claw 学习（2026-10-13，P1）：『停滞 20 天』是路径混淆造的假警报——判『文件陈旧』前没先 resolve canonical 路径，符号链接/双份布局让判据打到了错误的文件。外部静默失效研究（clawdbytes：『探针跑出的数字不能直接当结论』）佐证：任何『陈旧/健康』判断必须锚定到单一真源路径。将『canonical 路径先于陈旧裁决』沉淀为 references/canonical_path_staleness.md（落在 unified-inspection-hub 治理技能下），并提示 staleness 判据脚本在裁决前先 resolve canonical。
2. `disc-20261015-03` · a-stock-data · 价值=medium-high / 风险=low
   - 目标锚点：**meta** · 轨道=external_trend
   - 目标文件：`references/a_stock_data_landscape.md`
   - 成功指标：references/a_stock_data_landscape.md 新增『ArriveAstral/a-share-skill 全栈套件（a-share-data + 模拟舱 + 选股/短线策略 skill）』条目并被 SKILL 索引引用
   - 来源：https://github.com/ArriveAstral/a-share-skill
   - 依据：新增 A股量化竞品套件 ArriveAstral/a-share-skill：零配置装、内置多源自动切换，且自带 a-share-paper-trading 模拟舱（限价/市价单、T+1/涨跌停校验、净值快照）与多个选股/短线策略 skill。本地 QTS 模拟盘逻辑可对照其交易规则约束实现；纳入趋势观察库。

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
- `disc-20261010-01` — status=applied 不在允许集
- `disc-20261010-02` — status=rejected 不在允许集
- `disc-20261010-03` — status=applied 不在允许集
- `disc-20261009-09` — status=applied 不在允许集
- `disc-20261010-04` — status=applied 不在允许集
- `disc-20261011-01` — status=applied 不在允许集
- `disc-20261011-02` — status=applied 不在允许集
- `disc-20261011-03` — status=applied 不在允许集
- `disc-20261011-04` — status=applied 不在允许集
- `disc-20261012-01` — status=applied 不在允许集
- `disc-20261012-02` — risk=medium 高于阈
- `disc-20261012-03` — status=applied 不在允许集
- `disc-20261013-01` — status=applied 不在允许集
- `disc-20261013-02` — risk=medium 高于阈
- `disc-20261013-03` — status=applied 不在允许集
- `disc-20261013-04` — 无可判定指标(success_metric) — 优化无法证真
- `disc-20261014-01` — status=applied 不在允许集
- `disc-20261014-02` — status=applied 不在允许集
- `disc-20261015-01` — 承载方缺失: (空)
- `disc-20261015-02` — 承载方缺失: (空)

## v1.10 指标闸（新）
- 因**无目标锚点**被拦：0 条
- 因**无可判定指标**被拦：1 条
- 因**check_cmd 路径不存在**（指标无法证真）被拦：0 条
- 因**性能类缺数值基线**被拦：0 条
- 药方：在「🛡️ 统一发现-每日扫描」里给候选补 `target_project` + `success_metric`（可判定、可复核）后才进本通道；
  补不出指标的 → 在周度排序里落到 `Assess`（先评估）或 `rejected`，**不再默认落地**。
- check_cmd 请用**实际落地根**（`$HOME/.workbuddy/skills/...`）而非 Claw 侧路径；
  Claw 侧只对部分 skill 建了 symlink，写 Claw 路径会让命令恒失败、指标永远到期不了。
- 以**量化性能目标**（如「耗时下降 ≥50%」「<30s」）作验收的候选，必须自带 `baseline`
  （当前值 + 测量方法，**要带数字**）—— 没有对照值，既不能判断该不该改也不能验收。

## ⚠️ 弱退出码告警（仅上报，不拦截） —— 0 条
check_cmd 的契约是「退出码 0 = achieved」。以 `| head` / `| wc` / `|| true` 收尾时
退出码不再由判据决定 → 指标形同虚设（实例：`grep -rl … | head -1` 对不存在的字符串也 rc=0）。

## 说明
- 校准期（calibrate）本脚本**不写任何 skill 文档**，仅供对拍「本会改什么」。
- 生效期（live）由「📥 每日·文档类落地」自动化里的 agent 合成文档，每批 = 1 commit + 1 行 CHANGELOG + 1 个回滚点，top≤3。
- 核心类（SKILL.md 主流程 / 脚本 / 自动化 prompt）**永不**进本通道，恒走周度审。
