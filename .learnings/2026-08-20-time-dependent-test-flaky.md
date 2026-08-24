# 时间相关 flaky 测试:sim_trade 止盈测试每月 20 号后必红

日期: 2026-08-20
影响: Claw CI Unit Tests 红灯(08-20 首次暴露,实际 08-19 起已潜伏)

## 现象
`tests/test_sim_trade.py::TestCheckTakeProfit::test_not_enough_profit`:
`assert True is False` — 构造 +5% 盈利的持仓,期望不触发止盈,实际触发了。
**仅每月 20 号后失败**,19 号前 CI 一直绿。

## 根因(证据链)
1. `sim_trade._is_sprint_period()`: `day >= 20 or (6月14后)` → 08-20 为冲刺期
2. `_get_take_profit_levels()` 冲刺期首级阈值 = **+5%**(sell_ratio 1/3)
3. 测试用例固定 `current_price=105/avg_cost=100` = 恰好 +5% → 触发冲刺期止盈
4. 测试**未固定模式**: 同类的 `test_level1_take_profit` 08-19 已适配双模式(动态取 levels),漏了 `test_not_enough_profit`

## 教训
1. **测试含运行时模式判定(时间/环境)必须 patch 固定**,否则产生月度/周期 flaky
2. 排查 CI 红:先本地复现 + 读实现再下结论,别先假设是代码回归(08-20 差点误判为 sim_trade 回归)
3. 双模式功能改动的配套测试要逐用例适配,不能只改一个

## 修法
`with patch("sim_trade._is_sprint_period", return_value=False)` 固定正常期
(+5% < 正常期首级 15%/18%),与已适配用例对齐。验证: test_sim_trade 34 passed / .workbuddy/tests 全量 185 passed。

## 预防
- 新增测试若依赖"今天/本月/模式",一律 patch 时间或模式判定函数
- CI 红灯排查 checklist 增加「时间相关 flaky」筛查项(先查 _is_sprint_period / date.today / 月份分支)

✅已升级(2026-08-23): 本项目 CI 红排查 SOP 应含"时间相关 flaky"先验检查
