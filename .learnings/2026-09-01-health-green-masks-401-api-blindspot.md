# 健康检查全绿 ≠ 服务可用：/health 探活掩盖 200 次 401 业务调用

- 日期: 2026-09-01 08:30
- 项目: Claw / QTS（统一巡检中枢 unified_ops_center.py）
- 严重度: P1（46 天数据零落库的真实根因一直没被发现）
- 状态: 已定位根因 + 已补检查项（未修调用方，需用户拍板）

## 现象

`backtest_reports` 表长期只有 1 行，滞留在 2026-07-16。08-31 已修 SQLAlchemy
16 处裸 SQL 未包装 `text()`（CI 绿、commit 已合），但 09-01 复查发现**仍零落库**。

查 `quant-strategy` 容器日志近 24h：

| 状态码 | 次数 | 说明 |
|---|---|---|
| 200 (`/health`) | 2800+ | 探活全绿，容器 `healthy` |
| **401** | **201** | 业务接口全部被拒 |

401 明细（全部来自宿主机 `172.18.0.1`）：

```
POST /api/v1/backtest/run              42   ← 每批 3 连发（重试 3 次）
GET  /api/v1/stocks/index/realtime     28
GET  /api/v1/stocks/realtime/600519.SH 28
GET  /api/v1/strategies/               14
GET  /api/v1/stocks/realtime/999999.XZ 14
GET  /api/v1/stocks/realtime/600519    14
GET  /api/v1/stocks/pool               14
GET  /api/v1/signals/                  14
GET  /api/v1/backtest/results          14
GET  /api/v1/account/summary           14
```

## 根因（实测实锤）

```bash
curl -H "X-API-Key: quant-internal-key-2024" http://127.0.0.1:8000/api/v1/strategies/  # → 200
curl                                          http://127.0.0.1:8000/api/v1/strategies/  # → 401
```

- 服务端 `API_KEYS=quant-internal-key-2024`（容器内 printenv 实锤）
- `qts_client.py` 的 `QTS_API_KEY` 加载**正常**（`__file__` 绝对路径读 .env，不受 cwd 影响），
  其所有 `_api()` 调用都带 key → 不是它的问题
- 所以 401 来自**不走 `qts_client` 的动态调用**

## ★ 关键教训（升级候选）

### 1. `health 全绿` 是最容易骗人的健康信号

探活端点不鉴权、不碰业务库、不调外部依赖。一个服务可以 health 100% 绿，
同时 100% 的业务请求在 401/500。**判断"服务可用"必须看业务接口的实际状态码分布，
不能只看 health 和容器 `healthy` 标记。**

> 数字对比：24h 内 /health 被探 2826 次全绿，业务接口 201 次 401。
> 信噪比 14:1 —— 绿信号彻底淹没红信号。

### 2. grep 文件找不到调用方 ≠ 没有调用方

本次 grep 全 Claw/QTS/StockInsight 源码，`backtest/run` **0 命中**。
真实调用方是**自动化 agent 在运行时自拼的 curl/requests**——它只存在于
那一次执行的上下文里，磁盘上没有任何文件记录。

> ★ 推论：**"文件里搜不到" 不构成 "不存在" 的证据**。对于"谁在调我的接口"这类
> 问题，唯一可信的证据源是**服务端的访问日志**（源 IP + 路径 + 状态码 + 时间分布），
> 不是源码搜索。

### 3. 修复要验收到"落库"这一层，不能停在"代码改对了"

08-31 修的是 SQL 层（真 bug，该修）。但验收时只看了 CI 绿 + commit 合入，
没看 `backtest_reports` 有没有新行。**一次修复可能造成"已解决"的错觉，
而真正的阻塞点在链路更上游。**

> ★ 验收判据要选**最终副作用**（数据落库 / 文件生成 / 状态变更），
> 不要选中间产物（单测通过 / CI 绿 / 代码合入）。

### 4. 巡检的检查项清单本身就是盲区来源

中枢原有 15 项检查，涵盖容器/CI/磁盘/通道/成本，但**没有一项看 API 错误码**。
所以这个 46 天的问题，每天巡检都是绿的。

> ★ **每次发现"巡检没抓到的真实问题"，都要回头问：这是哪个维度的盲区？
> 然后把它固化成一项检查**。否则同一类问题会一直靠人工偶然发现。
> 本次已加 `check_qts_api_auth()`（第 16 项），阈值 401/403 ≥10 次/24h。

### 5. 时间分布可以反向定位"看不见的调用方"

401 时间戳（UTC）：01:14 / 02:23 / 03:26 / … / 21:31 / 22:57 —— 间隔 65~85min
不规则。拿去比 `automation_runs` 表（注意 `created_at` 是**毫秒 epoch 整数**，
要 `fromtimestamp(ms/1000)`），命中：

- 08-31 21:31Z = 北京 09-01 05:31 → `05:26 [QuantTradingSystem]` 自动化
- 08-31 22:57Z = 北京 09-01 06:57 → `06:57 [Claw] hourly watchdog`

> ★ 容器日志时间戳是 **UTC**（容器 TZ=UTC），`automation_runs.created_at` 转出来是
> **本地时间**。跨这两个源对时间必须换算，否则永远对不上。

## 处置

1. **已加检查项**：`unified_ops_center.py::check_qts_api_auth()` —— 扫描
   `docker logs quant-strategy --since 24h` 的状态码，统计 401/403/5xx，
   输出 Top 来源 IP + Top 接口。备份：`/tmp/unified_ops_center_pre_401check_20260901_0830.py`
   - ruff check 通过；冒烟实测 `ok=False`，检出 201 次（**反证有效**：能变红）
   - 注意 `ruff format --check` 报 reformat，但**改动前的文件同样报**（L930 历史代码），
     非本次引入，未动
2. **已推飞书**：`pushed=true`，去重锚点写入 `.ops_alerted.json` @ 09-01 08:30:08
3. **未修调用方**（待用户拍板，涉及自动化 prompt 改动）：
   - 方案 A：给相关自动化 prompt 显式写明 `curl -H "X-API-Key: $QTS_API_KEY"`
   - 方案 B：服务端对 `172.18.0.1`（bridge 网关=宿主机）来源免鉴权
   - 方案 C：提供 `qts_curl.sh` 封装，强制带 key，prompt 里只准用它

## 待观察

- 今天 15:30-15:45（回测日报调度窗口）后复查 `backtest_reports` 是否有新行。
  若仍为 1 行 → 401 确认为根因，需立刻走方案 A/B/C。
- 若 401 消失但表仍无新行 → 说明还有第三层阻塞，继续往下挖。
