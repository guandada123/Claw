# 巡检脚本继承 cwd → `pytest tests/` 跑成了被巡检项目的测试 → 每小时自造 14 条 401

- 日期: 2026-09-01 11:20（统一巡检中枢 run#53）
- 级别: P1（自污染 + 告警噪音 + 误导归因）
- 状态: 已修复并验证（Claw 仓提交）

## 一、现象

巡检中枢新增的 `check_qts_api_auth()`（第 16 项）持续报「QTS API 鉴权失败 200+ 次/24h」，
Top 接口固定是 14 个：`/api/v1/stocks/index/realtime`、`/api/v1/stocks/realtime/600519.SH`、
`/api/v1/stocks/realtime/999999.XZ`、`POST /api/v1/backtest/run`×3、`/api/v1/strategies/`、
`/api/v1/signals/`、`/api/v1/backtest/results`、`/api/v1/stocks/realtime/600519`、
`/api/v1/stocks/pool`、`/api/v1/account/summary`。来源恒为 `172.18.0.1`（宿主机）。

## 二、归因过程（两次纠正，值得记）

### 第 1 次结论（run#51 08:30）：「生产调用链漏配 X-API-Key」——错
时间量级证伪：401 只有 1 天历史，而 `backtest_reports` 停了 46 天，不可能互为因果。

### 第 2 次结论（run#51 08:55）：「是我（agent）回读验证时自拼 curl 探活」——也错
证据是「每批 401 都落在巡检中枢自己的运行窗口内」，这一点**方向正确**（确实自污染），
但**层判错了**：把「agent 手工行为」当成了原因，实际是**脚本固定行为**。

### 第 3 次（run#53，终局实锤）

关键反例：**逐项 import 调用 16 个检查函数 → 0 条 401；整脚本 subprocess 跑 → 14 条 401。**
两者唯一差异是 **cwd**：

| 运行方式 | cwd | `pytest tests/` 实际跑的是 | 401 |
|---|---|---|---|
| `import module; main()` | Claw | Claw 的 485 项单测 | 0 |
| `python unified_ops_center.py` | **QTS 仓**（本自动化 cwds） | **QTS 的 `tests/`** | **14** |

QTS 的 `tests/test_e2e.py` + `tests/contracts/test_strategy_api.py` 直接对
`http://localhost:8000` 发请求且**不带 `X-API-Key`**，其中 `test_strategy_stock_data_available`
正好遍历 `/api/v1/stocks/realtime/600519`、`/api/v1/stocks/pool`、`/api/v1/account/summary`
—— 与 401 Top 接口清单逐条吻合。

时间线自洽：本自动化 08-31 09:13 起开始做「回读验证」，但真正的起点是
**08-31 把中枢的调度 cwd 固定到 QTS 仓**；之前 87 次无 401 是因为 cwd 还在 Claw。

## 三、修复

```python
# run_cmd 增加 cwd 参数（原本继承调用方）
def run_cmd(cmd, timeout=90, capture=True, env=None, cwd=None):
    return subprocess.run(cmd, capture_output=capture, text=True,
                          timeout=timeout, env=env, cwd=cwd)

# check_code_quality：绝对路径 + 显式锁 cwd
run_cmd([sys.executable, "-m", "pytest", str(CLAW_ROOT / "tests"), "-q"],
        timeout=300, env={**os.environ, "PYTHONPATH": str(CLAW_ROOT)},
        cwd=str(CLAW_ROOT))
```

验证：以 QTS 仓为 cwd 跑完整脚本 → **新增 401 = 0**；pytest 输出 `485 passed`（确为 Claw 测试）。
备份：`/tmp/unified_ops_center_pre_run53_20260901_111926.py`。

## 四、连带修掉的去重击穿（同源：易变数字）

`_dedup_key()` 只切中文标点，告警文案里的**计数**每次运行都变：
`鉴权失败 202 次/24h` → `216` → `230`，于是同一根因每小时生成新键，
**飞书被同一个问题连推 3 次**（11:11 / 11:15 各一次）。

修法：归一化时把数字串折叠成 `#`（计数是诊断补充，不是身份；身份是「哪个检查项 + 什么问题」）。

```python
s = re.sub(r"\d+", "#", s)   # 加在截断之前
```

`_load_alerted()` 本就会把历史键重跑一遍 `_dedup_key`，故存量键自动迁移、无需手工清理。

## 五、★ 升级候选（教训）

1. **★ 子进程调用必须显式锁 cwd。** `subprocess.run` 默认继承调用方 cwd，
   于是「同一份巡检代码在不同工作目录下语义完全不同」—— 一个只读检查能悄悄变成
   对生产服务的写/压测。**凡巡检/运维脚本，所有子进程调用都该带 `cwd=`**。
2. **★ 判断"是不是自己造成的"，别停在"是自己的运行窗口"这一层。**
   窗口吻合只能证明"自己人干的"，不能区分「agent 手工动作」和「脚本固定逻辑」。
   要分辨两者，用**对照实验**：手工逐项调用 vs 整脚本跑，差异项就是变量（本次 = cwd）。
3. **★ 恒定的异常数量是"脚本行为"的指纹。** 每批恰好 14 条、接口清单逐条一致、
   间隔严格跟随调度 —— 这种**机械的规整性**不可能是人工探活（人工次数和顺序都会变）。
   看到规整到反常的异常，先找自动化的固定代码路径。
4. **★ 告警去重键绝不能含易变数字。** 否则"降频"设计被架空，变成每小时轰炸；
   而且**新增检查项时最容易踩** —— 新检查为了可诊断性往往把计数写进文案。
   去重键的归一口径应是"身份"，不是"快照"。
5. **★ 新增检查项首次告警时，反例比正例更有价值。**
   「跑了但没产生该异常」的运行轮次，是定位变量的最小对照样本
   （本次：import 调用无 401 / 整脚本有 401 → 变量是 cwd，不是"agent 做了什么"）。
6. **测试套件本身可能是生产流量的来源。** 写「集成/e2e 测试直连生产服务」时，
   必须问：谁会跑它、多久跑一次、带不带凭据。本次是巡检中枢每小时替 QTS 跑了一遍
   e2e 测试，而 QTS 的 `tests/` 里没有任何鉴权。
