# 修复「重启早于最后一次写文件」+ 三类静默失效（2026-08-31 统一巡检中枢 run#42）

★升级候选 ×3

## 背景

run#41（21:00）修了 QTS 16 处裸 SQL 未包 `text()`（回测日报 46 天零落库），
21:16:10 提交。run#42（22:18）按「验收查产物不查日志」纪律复检，发现修复**没真正上线**。

## 教训 1 🔴★ 重启必须发生在「最后一次写文件」之后（顺序竞态）

实测时间线：

| 时刻 | 事件 |
|---|---|
| 21:14:47 | 容器重启（run#41 记录为「重启生效，14 job 恢复注册」） |
| 21:15:44 | 4 个目标文件**最后一次**被写入 |
| 21:16:10 | git commit |

重启早于最后一次写文件 **57 秒** → 进程加载的不是最终代码。
而「14 job 恢复注册」这个观测**是真的**，但它只证明调度器初始化成功，**完全不能证明 SQL 修复被加载**。

**判据**：`docker inspect -f '{{.State.StartedAt}}'`（注意是 **UTC**）vs `stat` 各目标文件 mtime。
只有 `进程启动 > 所有目标文件 mtime` 才能断言代码已生效。

**固化动作**：改完代码 → 先 commit → **再重启** → 再验证。绝不在提交前重启。
（另：容器无 `--reload`，volume mount 只更新文件层，不更新已导入的模块。）

## 教训 2 🔴★ `.pyc` 跨环境比对必须对齐解释器版本

首轮判定「main.py 仍是旧代码」——**误判**。

- 容器 Python **3.12**，宿主机跑测试的是 **3.13**
- 二者写不同文件：`main.cpython-312.pyc` / `main.cpython-313.pyc`
- 我读的是 313（宿主机测试的产物，8 月 2 日），容器自己的 312 pyc 时间是重启时刻

**判据**：列目录看**全部** `.pyc` 再选，别取第一个匹配项；或干脆放弃 pyc 比对，
改用「进程启动时间 vs 源码 mtime」这个更本质的判据（bind mount 下 100% 可靠）。

## 教训 3 🔴 `docker exec` 不加 `-i` 时 stdin 被丢弃，且不报错

```bash
docker exec quant-postgres psql ... <<'SQL'   # ❌ 静默不执行，输出为空
docker exec -i quant-postgres psql ... <<'SQL' # ✅
```

危害：跑了「建表」却一行没建，输出干净得像成功。复验时才暴露。
**凡是靠 heredoc 喂 stdin 的容器命令，必须有 `-i`，且执行后必须复验。**

## 教训 4 🔴★ PostgreSQL 只报「第一个」缺失对象，掩盖后续缺失

`SELECT ma20, rsi14, turnover_rate FROM daily_quote` 三列全缺，
PG 只报 `column "ma20" does not exist` → run#41 只记录了 ma20 一列。

**修完一个必须重跑到全绿**，不能修完报出来的那个就收工。
同理 `index_snapshots` 建好后 `daily_snapshots`/`alert_rules`/`alerts` 仍缺——报一个漏三个。

## 教训 5 🔴 降级兜底让失效完全不可见：HTTP 200 + 合理数据 ≠ 真实数据

`api/alerts.py` 结构：先定义硬编码 `DEFAULT_RULES` → `try` 查库 → 失败/空则 `except` 返回默认值。

`alert_rules` 表根本不存在，但 `GET /alerts/rules` 稳定返回 **200 + 4 条看起来很合理的规则**。
run#41 记为「alerts API 从未可用」，实际更危险：**它一直可用，只是永远返回假数据**。

**判据**：看到 200 + 顺滑的数据，要问「这是查库来的还是兜底来的」——
去库里核对行数/内容，或临时把兜底分支打日志。

## 教训 6 ⚠️ 同一 session 中前一句失败会污染后续全部检查

```python
with get_db_session() as db:
    for label, sql in probes: db.execute(text(sql))  # ❌
```

第一个 `UndefinedTable` 让事务进入 aborted，后续全部报
`InFailedSqlTransaction: current transaction is aborted`——看起来像 4 个独立问题，实则 1 个。
**多个独立探测必须各用独立 session（或每句前 rollback）。**

## 教训 7 ⚠️ 探针/断言失败先检查探针本身（第 3 次复现）

E2E 探针报 `KeyError: 'backtest_count'`，差点定性为「修复不完整」。
实为我的探针 dict 漏字段（`report_service.py:380` 真实报告有该 key）。
修正后落库 `True`，DB 回读确认，探针已 DELETE，DB 恢复 1 行原状。

## 工具备忘

- `lark-cli im +messages-mget --message-ids om_xxx,om_yyy` —— **逗号分隔字符串**，不是 JSON 数组
  （传 `["om_xxx"]` 报 `must start with om_`）。无 `+messages-get` 子命令。
- 容器内无 `ps`：读 `/proc/1/cmdline`（`tr '\0' ' '`）看启动参数（如确认有无 `--reload`）。
- PG 凭据在 compose 里是 `quant_user/quant_pass`（不是 `quant`）。
- 加可空列对 303 万行是元数据操作，瞬时完成，不必畏惧。

## 本次修复清单（QTS `288efd6c`，已 push）

1. 建 4 表：`index_snapshots` / `daily_snapshots` / `alert_rules` / `alerts`（含索引）
2. 加 2 列：`daily_quote.ma20` / `rsi14`（可空）
3. 改列名笔误：`turnover_rate` → `turnover_ratio`（同 run#41 的 `pct_chg→pct_change` 类）
4. 改 SQL 方言：`datetime('now','-7 days')`（SQLite）→ `NOW() - INTERVAL '7 days'`（PG）
5. DDL 同步写入 `docs/init.sql`，保证重建可复现

> 遗留（交用户）：`ma20`/`rsi14` 已建但无值，需回填任务（3M 行）才出真实指标，
> 当前前端按代码逻辑显示 `N/A`（代码已处理 None），属诚实降级。
