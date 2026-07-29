# Wind MCP 全面集成改造清单

> 生成日期：2026-07-20 | 基于 Wind CLI 三维测试验证

---

## 🔴 第一优先级：生产路径 K 线

### 1️⃣ wind_quote.py — 共享模块增强

| 项 | 说明 | 难度 | 方案 |
|---|---|---|---|
| `_wind_code()` 扩展 | 支持美股 `.O`/`.N`、指数 `.GI`/`.HI` 后缀 | 🟢 低 | 加模式参数 `data_type="stock"`，区分股票/指数/美股 |
| `fetch_wind_quote()` 丰富字段 | 加 `pe`/`total_value` 字段（需新调 `get_stock_indicators`？或保留腾讯 fallback 获取） | 🟡 中 | Wind 行情不直接返回基本面，可考虑另加 `fetch_wind_indicators()` |
| ➕ 新增 `fetch_wind_kline()` | 调用 Wind CLI `get_stock_kline`，返回 DataFrame | 🟢 低 | 列映射见下方 |

**Wind K 线列映射（已验证）：**
```
Wind 9列 [TIME, OPEN, MATCH(=收盘), HIGH, LOW, TURNOVER(=成交额), VOLUME(=成交量), CHANGEHANDRATE(=换手率), AVPRICE(=均价)]
   ↓
pandas DataFrame: date, open, close, high, low, volume
```

**参数适配：** `days=N` → `begin_date/end_date` 格式 via Wind CLI

### 2️⃣ star_signal_adapter.py — K 线生产路径

| 项 | 说明 | 难度 | 方案 |
|---|---|---|---|
| `fetch_kline_df()` 改造 | 将腾讯 URL 调用替换为 `fetch_wind_kline()` | 🟢 低 | 共享模块已封装后，单函数替换 |
| 代码转换 | 需要 `_wind_code()` 将 6 位代码转为 `600519.SH` | 🟢 低 | 已有 `_wind_code()` 逻辑 |
| 降级方案 | Wind 失败时退回到腾讯 | 🟢 低 | 已有 `fetch_tencent_kline()` fallback |

**影响范围：** `get_star_signal()` / `get_dynamic_stop_loss()` / `get_technical_score()` / `generate_backtest_signals()` / `batch_scan_watchlist()` 全部依赖 `fetch_kline_df()`，**一改全改**。

---

## 🟡 第二优先级：实时行情改造

### 3️⃣ fetch_us_market.py — 美股行情

| 项 | 说明 | 难度 | 方案 |
|---|---|---|---|
| 指数行情 (`dow/nasdaq/sp500`) | 当前用 `usDJI`/`usIXIC`/`usINX` → 腾讯 | 🟡 中 | 改用 Wind `get_index_quote` + 指数归一化 |
| 个股行情 (`AAPL/NVDA/...`) | 当前用 `usAAPL` → 腾讯 | 🟢 低 | 改用 Wind `get_stock_quote` + `.O`/`.N` 后缀 |
| 韩股行情 (`kospi/kosdaq`) | 腾讯本身不支持，始终 none_match | ⚪ 不变 | 保持现状或移除 |
| 缓存的兼容性 | 缓存格式不变，只换数据源 | 🟢 低 | 字段对齐 |

**Wind 美股代码格式（已验证）：**
- 纳斯达克：`AAPL.O` → 成功返回
- 纽交所：`JPM.N`
- 美交所：`SPY.A`
- 道琼斯指数：`DJI` → 自动归一化为 `DJI.GI`
- 标普 500：`SPX` → `SPX.GI`
- 纳指：`IXIC` → `IXIC.GI`

**注意：** Wind 行情返回分钟级 tick 数据（今日每分钟快照），取 `rows[0]` 做当前快照。

### 4️⃣ expert_team_analyst.py — 丰富字段行情

| 项 | 说明 | 难度 | 方案 |
|---|---|---|---|
| `fetch_tencent_quote()` | 当前直接调腾讯，解析 ~45 个字段 | 🟡 中 | 替换为 `wind_quote.fetch_wind_quote()` |
| `pe` 字段 | 腾讯字段 39，Wind 股票行情不直接返回 | 🟡 中 | 可考虑：1) Wind `get_stock_indicators` 查 PE；2) 腾讯 fallback 查 PE；3) 保留腾讯 |
| `total_value` 字段 | 腾讯字段 45（总市值 亿） | 🟡 中 | 同上 |

**影响函数：** `analyze_value_investing()` / `analyze_macro()` / `analyze_capital_flow()` 均用到 `pe`/`total_value`

---

## 🟢 第三优先级：简单实时行情查询

### 5️⃣ local_combo_signal.py — `fetch_realtime()` 函数

| 项 | 说明 | 难度 | 方案 |
|---|---|---|---|
| L132-152 `fetch_realtime()` | 腾讯 `qt.gtimg.cn` → 单只股票行情 | 🟢 低 | 替换为 `wind_quote.fetch_wind_quote(code)` |
| 影响 | 仅 `--db` 模式下会用 | 🟢 低 | 无其他调用者 |

### 6️⃣ scan_broad_pool.py — `rt_quote()` 函数

> ⚠️ **已废弃 (2026-07-23)**：本脚本已被 `.workbuddy/scripts/scan_mainboard_local.py` 完全取代（后者扫全主板 1076 只，含原 7 只蓝筹+现仓，同源 COMBO 逻辑 + gtimg 兜底，无容器依赖）。脚本已删除，备份在 `.workbuddy/scripts/_archive/scan_broad_pool.py.2026-07-23.bak`。本项从 Phase 4 迁移清单中移除。

### 7️⃣ strategy_generator.py — `generate_stock_strategy()` 内部

| 项 | 说明 | 难度 | 方案 |
|---|---|---|---|
| L196-217 Tencent block | `qt.gtimg.cn` → `current_price` | 🟢 低 | 替换为 `wind_quote.fetch_wind_quote(symbol)` |

---

## 总体改造路线图

```
Phase 0 [当前]: wind_quote.py 共享模块增强
  ├─ _wind_code() 扩展支持美股/指数
  ├─ fetch_wind_quote() 丰富字段
  └─ + fetch_wind_kline() 新函数

Phase 1: star_signal_adapter.py 🔴
  └─ fetch_kline_df() → fetch_wind_kline()

Phase 2: fetch_us_market.py 🟡
  └─ 指数 + 个股 → Wind 行情

Phase 3: expert_team_analyst.py 🟡
  └─ fetch_tencent_quote() → fetch_wind_quote() + PE fallback

Phase 4: 两个简单脚本 🟢
  ├─ local_combo_signal.fetch_realtime()
  └─ strategy_generator.generate_stock_strategy()
  （scan_broad_pool.py 已废弃移除，见 §6️⃣）
```

**Wind CLI 调用格式参考：**
```bash
# 股票行情
node cli.mjs call stock_data get_stock_quote '{"windcode":"600519.SH"}'
# 指数行情
node cli.mjs call stock_data get_index_quote '{"windcode":"DJI.GI"}'
# K 线
node cli.mjs call stock_data get_stock_kline '{"windcode":"600519.SH","begin_date":"20260620","end_date":"20260720"}'
```
