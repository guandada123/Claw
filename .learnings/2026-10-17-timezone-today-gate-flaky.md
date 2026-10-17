# 时区型时间相关 flaky：休市闸门用本地时区的"今天"，CI(UTC) 与本地(+8) 差一天

**日期**: 2026-10-17 · **项目**: Claw · **级别**: 🔴（CI 红掩盖 + 错误结论差点入库）

## 现象
推送仅含「统一巡检新增哨兵」的提交（d9f9845，未触碰任何被测代码）后，Claw CI
Unit Tests 12 failed：`test_check_disposal_triggers` 全家 —— baseline.json 不生成、
告警全 0。本地同提交复现同红。

## 取证链（每步可复核）
1. **先排除自己**：`git worktree add /tmp/x 2268aa0`（前一提交，无本轮改动）→
   同样 11 failed → 与本轮无关（不 stash、不动工作区，昨天的教训）。
2. **单测 -s 抓退出点**：脚本打印 `⏸ 今日休市（非交易日）→ 跳过` —— main 的
   休市闸门（PA-014）把集成路径整个拦掉。
3. **机理**：闸门内联 `import is_trading_day` → `today_is_trading()` 用
   **本地时区的 today()**。CI 时钟是 UTC、本地是 +8，"今天"可能差一天：
   - 2268aa0 绿 = run 在 **UTC 周五** 23:34Z（放行）
   - d9f9845 红 = **UTC 周六** 01:25Z（拦截）
   - ca3b54d 绿 = **UTC 周六** 01:42Z（闸门已被本修复密封）→ 天然 A/B 实验
4. **假设被证伪一次**：最初推断"CI 缺 astock_holidays.json → 降级放行"，
   但 `git log -- data/astock_holidays.json` 证实 **10-11 已入库**（7570d29）——
   错误结论已写进 ca3b54d 的注释，随即以 20a2473 更正。教训：**把机理写进代码注释前
   必须用 git log / run 时间线验证**，否则毒害未来排查。

## 修复
`_fixed_calendar` 一并密封 `is_trading_day.today_is_trading = lambda: True`，
集成路径与"今天是星期几/在哪个时区"解耦。可证伪：同一 UTC 周六环境
11 failed → 29 passed。

## 可复用规则
1. **CI 的绿要问"它跑在哪个分支上"**：时间/环境相关的闸门，绿可能只是"条件不成立"。
2. **日期敏感测试必须注入"今天"**（mock now/today），否则 UTC 周五~周六的窗口、
   月末、节假日前后都会随机引爆。
3. **两个 runner 的时钟差一天时**，"同提交绿/红"优先查时区而不是查文件差异。
4. git worktree 是 CI-only-flaky 取证的安全工具。
