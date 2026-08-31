# 派生字段的「落盘路径 ≠ 验证路径」——修复被自己的验收方式掩盖（run#49, 2026-09-01）

★升级候选 ×5

## 现象

统一巡检中枢 run#48 定位并「修复」了模拟盘收盘价断流：用官方入口
`sim_trade.py batch-update` 刷了 `current_price`，并用 `sim_trade.py perf`
验证「`total_asset=53580.95 = cash 35223.95 + 持仓 18357.00` 恒等式成立」，
结论写为「✅ 已修复」。

run#49 读 `portfolio.json` 原始文件，发现落盘字段与 perf 口径**差 ¥4,163**：

| 口径 | total_assets | 来源 |
|---|---|---|
| `perf` 命令输出（现算） | 53,580.95 | `calc_total_asset()` L317 |
| 文件落盘 `total_assets` | 57,743.95 | `save_portfolio()` L201-203 |
| 文件落盘 `total_market_value` | 22,494.00 | 无生产者 |
| `summary.total_assets` | 53,373.33 | 一次性脚本，08-28 后停更 |

## 根因

`save_portfolio()` 用**存储字段** `positions[*].market_value` 求和算 `total_assets`：

```python
mkt = sum(float(v.get("market_value", 0)) for v in pf.get("positions", {}).values())
pf["total_assets"] = round(mkt + float(pf.get("cash", 0)), 2)
```

而 `market_value` **没有任何稳定生产者**：
- `cmd_sell` 改 `shares` / `total_cost` / `avg_cost`，**不写** `market_value`
- `cmd_update_all_prices`（batch-update 底层）只写 `current_price` / `highest_price`，**不写** `market_value`
- 只有 `cmd_snapshot` / `cmd_report` 会写，两者都不在日常调度里

→ `market_value` 永久停在首次建仓时的值。实测 601668 中国建筑 08-31 卖出减半
（2000→1000 股），`market_value` 仍为 2000 股估值 **8920.00**，真实应为 4330.00，
单只虚高 ¥4,590。

**同一份逻辑存在两份实现**：`calc_total_asset()`（L317，`shares × current_price`，正确）
和 `save_portfolio()` 内联版（L201，读存储字段，错误）。`perf` 走前者，落盘走后者。

## 为什么 run#48 的验收没抓到（关键教训）

`perf` 是**只读现算视图**，它不读落盘的 `total_assets`，而是用 `calc_total_asset()`
重算一遍。**用一个现算视图去验收一个存储字段的修复，必然给出假阳性** ——
两者根本不在同一条数据通路上。

这条 bug 因此隐身了整整数轮巡检。

## 修复

1. `save_portfolio()` 改为与 `calc_total_asset()` 同口径（`shares × current_price` 现算），
   并顺带回写每个持仓的 `market_value` 与顶层 `total_market_value`，保持文件自洽。
   选 `save_portfolio()` 作修复点是因为它是**所有写路径的公共出口**，改一处全链路生效。
2. 加 2 条回归测试，直接断言「落盘 `total_assets` == `calc_total_asset(pf)`」，
   即**把两条路径的等价性钉死**，而不只是测某个具体数值。
3. **反证测试有效性**：用修复前的旧实现注入，确认两条断言都会被违反
   （旧实现 52013.95 vs 正确 47577.95，差 4436）→ 护栏真实有效，不是重言式。

验证：`total_assets=53580.95 == perf`，`cash`/`current_price`/交易记录全部未变，
485 项测试全绿，ruff check + format 通过。

## 通用写法（★）

1. **验收存储字段的修复，必须读落盘文件本身，不能只读命令输出。**
   命令输出可能是现算视图，与落盘值是两套代码。修复前先问：
   「我用来验收的这个数字，是从我要修的那个字段里读出来的吗？」
2. **派生字段要么「只现算不落盘」，要么「落盘就必须每个写路径都同步」。**
   最忌半吊子：落盘了但只有部分路径更新 → 字段进入永久不一致状态。
   修在**公共写出口**（save/write 类函数）比逐个命令打补丁可靠得多。
3. **发现同一逻辑有两份实现时，先怀疑它们会分叉。**
   `calc_total_asset` vs `save_portfolio` 内联版就是典型。grep 函数名看调用方，
   若两处调用方不同（一处在只读路径、一处在写路径），分叉只是时间问题。
4. **回归测试要断言「两条路径等价」，而不是断言某个魔法数字。**
   断言 `persisted == computed` 能防住未来任何一处口径改动；
   断言 `== 53580.95` 下次调价就失效。
5. **加完回归测试必须反证一次**：用修复前的逻辑跑，确认测试真的变红。
   没做过反证的测试无法证明它拦得住这个 bug。

## 附带发现（同轮）

- `sync_claw_to_qts_portfolio.py` 日志打 `config.initial_capital`（30000 基准值），
  与收益口径用的**有效本金**（50000 = 30000 + capital_additions 20000）不一致，
  排查时极易误判成「本金未同步待办未修」。已改为同时打印
  `base=30000.0, effective=50000.0`。
  → **日志里打印的字段必须标注它是否是派生/汇总口径**，裸字段名会骗人。
