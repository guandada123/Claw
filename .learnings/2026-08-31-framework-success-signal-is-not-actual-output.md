# 调度框架报「成功」≠ 任务真的成功：验收必须查产物，不能查调度日志

日期：2026-08-31　来源：统一巡检中枢 run#41（QTS 回测日报 46 天零落库）
等级：★升级候选（三重教训，含本人实犯两处）

---

## 一、现象

QTS 的回测日报 job 每天 15:35 运行，APScheduler 日志**每天**打印：

```
[ReportScheduler] 开始生成回测日报...
Job "回测日报" executed successfully
```

但 `backtest_reports` 表：`count=1`，`max(report_date)=2026-07-16`，`created_at=2026-07-16 11:38`。

**46 天，11 次「执行成功」，零行落库。** 每次跑了 20 分钟（回测真算完了），最后一步写库失败。

## 二、根因链

1. SQLAlchemy 2.x 不再接受 `session.execute("裸字符串")`，必须 `text()` 包装。
   遗留 16 处未包装 → 抛 `ObjectNotExecutableError: Textual SQL expression ... should be explicitly declared as text(...)`。
2. 调用点 `except Exception as e: logger.warning(...); return False` → **异常被吞，只留一条 warning**。
3. APScheduler 只看 job 函数是否正常返回。函数吞了异常正常返回 → **报 executed successfully**。

三处受害（同一根因）：

| 任务 | 频次 | 失败点 | 后果 |
|---|---|---|---|
| 回测日报 | 每日 15:35 | `INSERT INTO backtest_reports` | 46 天零落库 → Claw 侧信号源长期隔离 |
| 每日信号汇总 | 每日 15:30 | `SELECT ... FROM trading_signals` | 每日失败 |
| 大盘快照 | 每 30 min | `INSERT INTO index_snapshots` | 累计 **769 次**失败 |

## 三、★教训 1：验收查产物，不查日志

**`executed successfully` 是「函数没抛异常」，不是「活干完了」。**

凡 job 内部有 `try/except` 兜底并正常返回，调度器成功信号**系统性失效**。
正确验收 = 查**产物**：DB 行数、文件里的业务时间戳、下游是否真消费到。

> 与 run#40 同源：`check_data_freshness()` 用 mtime 判新鲜 → portfolio.json 每天被重写、业务价格却停 3 天前。
> **共同模式：监控指标（调度日志 / mtime / generated_at）与实际健康度（落库行数 / 业务日期）脱钩。**

## 四、★教训 2（本人实犯）：`with conn.begin()` 退出是 commit，不是 rollback

验证写操作想「回滚」，写了：

```python
with eng.connect() as conn:
    with conn.begin():
        conn.execute(text(sql), params)
# 退出 → COMMIT，不是 ROLLBACK
```

结果**测试脏数据落库**，污染了 `backtest_reports`（且会被 Claw 的 `pull_qts_signals.py` 当成最新报告拉走）。
正确写法：

```python
with eng.connect() as conn:
    try:
        conn.execute(text(sql), params)
    finally:
        conn.rollback()          # 显式回滚
```

补充：**DDL 不受 rollback 保护**（`CREATE TABLE` 照样提交，本次误建了 `stock_insight_scans` 空表）。

## 五、★教训 3（本人实犯）：静态检查全绿 ≠ 运行时正确

第一版批量包装写成 `db.execute(text(SQL, params))` —— **`text()` 只接受 1 个位置参数**，运行时直接
`TypeError: text() takes 1 positional argument but 2 were given`。正确是 `db.execute(text(SQL), params)`。

而当时**三层检查全绿**：

- AST 校验：SQL 文本字节级一致 ✅（我只比了字符串内容，没比参数位置）
- ruff：All checks passed ✅（语法合法）
- 容器内单独执行 SQL：11/16 通过 ✅（我测的是裸 SQL + params，**绕开了代码里的调用形式**）

**唯一抓到它的是单元测试：7 failed / 35**（mock session 真收到两个错位的参数）。

→ **改完代码必须跑真实代码路径**（单测 or 真实调用），只做静态检查和「把 SQL 抠出来单独跑」都是假验收。
→ 与 run#38 固化条同源：**断言失败先查断言本身**；本次反向补充：**断言全绿先怀疑断言覆盖得够不够**。

## 六、可复用方法：批量 API 迁移的正确姿势

1. **AST 定位 + 精确插入**（`args[0]` 的 lineno/col_offset → end），禁止正则盲改。
2. 插入点用 **SQL 节点边界**，不是整个 Call 边界（`text(SQL), params` vs `text(SQL, params)` 就差在这）。
3. 改前 `cp` 备份到 /tmp，改后**先跑单测再跑 ruff**，两者都过才算完成。
4. 提交后核对 `git diff --stat` 只含目标文件，避免卷入其余 WIP。

## 七、遗留（交用户决策，未擅自处理）

5 处因 schema 缺失仍会失败，加 `text()` 解决不了，需建表/加列 migration：

- 表缺失：`index_snapshots`、`daily_snapshots`、`alert_rules`、`alerts`
- 列缺失：`daily_quote.ma20 / rsi14 / turnover_rate`（现查 daily_quote，实际指标在 `technical_indicators` 表）

其中 `alert_rules` / `alerts` 两张表缺失意味着 **alerts API 整个模块从未可用**。
