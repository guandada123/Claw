# 2026-10-12 · 全量对账 ≫ 逐个撞 —— 一次修齐整片「代码先行、schema 没到」 ★升级候选

## 事故链（本夜真实发生）
契约测试从"跳过/假红"改成**真跑**后，先撞出 `GET /api/v1/positions` → 500
（`UndefinedColumn: account_id`）。**修好这一个**，下一个接口 `/orders/summary/daily` 又 500
（`UndefinedColumn: profit_loss`）——如果继续逐个撞，还会有第 3、第 4 个。

**第二个 500 出现时立刻停手**，改做「**模型 vs 库**」全量对账：
解析 `strategy-service/models/models.py` 的 `__tablename__` / `Column(...)`
↔ 数据库 `information_schema.columns`，逐表比列。
→ **一次暴露整片缺口**：5 表 10 列 + **1 张整表缺失**
（orders/trades 缺 `account_id`、trades 缺 `profit_loss`、stock_info 缺 5 个估值列、
accounts 缺 `currency`、backtest_results 缺压缩列、`walk_forward_results` 整表没有）。

## 判据（下次直接照用）
**同一类缺口连续出现 2 次 → 立即停止逐个修，改用"声明侧 vs 实际侧"的全量对账。**

| 场景 | 声明侧（代码期望） | 实际侧（运行环境） |
|---|---|---|
| ORM 模型 vs 数据库 | `__tablename__` / `Column` | `information_schema.columns` |
| 配置项 vs 环境 | 代码读取的 key 清单 | 实际 env / 配置文件 |
| 依赖 vs 锁文件 | 代码 import 的包 | 已安装清单 |

三条纪律：
1. **对账产物要入库**（本次落成 `docs/migrations/2026-10-12_align_code_and_schema.sql`，含逐条回滚）
   —— 否则别人重建库会**再踩一次**；
2. **类型从声明侧解析**，不凭猜（本次类型全部从模型文本映射得到）；
3. **追加式 DDL + 先备份**（全库 498M 备份 + 单表备份），保证可回滚。

## 同族铁律
与「数据完整性检查基准必须来自库外（库内自比答不了"缺什么"）」同源：
**要发现"缺什么"，必须拿一个独立于被测对象的声明侧来比。**
