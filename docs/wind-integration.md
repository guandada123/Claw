# Wind 万得数据源接入文档（v2.1 · 2026-07-11）

> 本文档说明 Claw 项目中 Wind AIFin Market 数据源的集成方式、优先级策略、积分注意事项及高级分析工具的使用方法。

---

## 一、架构总览

```
业务层 (strategies / cli / scripts)
    ↓ fetch_kline / fetch_realtime / fetch_fundamentals
feeds/data_sources.py — DataSourceChain（容灾链）
    ↓ DataSource 子类
WindKlineSource ──┐    SinaKlineSource ──┐
WindRealtimeSource│──→ SinaRealtimeSource│──→ ...（免费降级）
WindFundamentals  │    AKShareFundamentals│
    ↓                                  ↓
Wind CLI (node)                   HTTP API
(~/.agents/skills/wind-mcp-skill/)  (新浪/腾讯等)

feeds/wind_analytics.py — 高级投研工具
    WindAnalytics（新闻/公告/技术/宏观/选股...）
```

**设计原则**：Wind 作为最高优先级付费源，不可用时自动降级到新浪/腾讯等免费源，系统不中断。

---

## 二、数据源优先级

### K线链

| 优先级 | 源 | 类型 | 说明 |
|--------|---|------|------|
| 1 | WindKlineSource | 付费（积分） | 万得同源数据，质量最高 |
| 2 | SinaKlineSource | 免费 | 约150次/120s 限流 |
| 3 | TencentKlineSource | 免费 | 不限流 |
| 4 | BaostockKlineSource | 免费 | 前复权，完全免费 |
| 5 | ADataKlineSource | 免费 | 聚合多源 |
| 6 | TushareKlineSource | 免费（需Token） | 备选 |

### 实时行情链

| 优先级 | 源 | 说明 |
|--------|---|------|
| 1 | WindRealtimeSource | 万得同源，支持单只查询（最多20只/次保护积分） |
| 2 | SinaRealtimeSource | 免费，批量查询不限 |

### 基本面链

| 优先级 | 源 | 说明 |
|--------|---|------|
| 1 | WindFundamentalsSource | PE/ROE/每股收益等 |
| 2 | AKShareFundamentalsSource | 同花顺财务摘要 |

---

## 三、集成代码

### 3.1 数据源类

`claw/feeds/data_sources.py` 中新增的类：

| 类 | 方法 | Wind CLI 工具 |
|----|------|-------------|
| `WindKlineSource` | `fetch_kline(code, days)` | `stock_data.get_stock_kline` |
| `WindRealtimeSource` | `fetch_realtime(codes)` | `stock_data.get_stock_price_indicators` |
| `WindFundamentalsSource` | `fetch_fundamentals(code)` | `stock_data.get_stock_fundamentals` |

代码映射：裸6位码自动转为 Wind 标准码（例：`600519` → `600519.SH`，`000001` → `000001.SZ`）。

### 3.2 高级分析工具

`claw/feeds/wind_analytics.py` — `WindAnalytics` 类：

```python
from claw.feeds.wind_analytics import WindAnalytics
wa = WindAnalytics()

# 财经新闻（query 不含空格）
news = wa.get_news("中天科技", top_k=3)
# → [{"title": "...", "content": "...", "date": "2026-07-09", ...}]

# 公司公告
ann = wa.get_announcements("6005192024年年报", top_k=3)

# 技术指标
tech = wa.get_technicals("600522", "近60日MACD走势")
# → [{"Wind代码": "600522.SH", "近60日每日MACD指数平滑移动平均": 0.4479, ...}]

# 前十大股东
shareholders = wa.get_shareholders("600522")

# 公司事件
events = wa.get_events("600522", "增发和并购事件")

# 风险指标
risk = wa.get_risk_metrics("600522", "过去1年Beta和波动率")

# 指数基本面
idx = wa.get_index_fundamentals("沪深300")
# → {"近一年每日市盈率": 14.35, "近一年每��市净率": 1.45, ...}

# 宏观经济指标（EDB）
macro = wa.get_macro_data("中国CPI同比", observation="12")
# → [{"指标": "中国:CPI:当月同比", "单位": "%", "日期": "20260731", "值": 0.0}, ...]

# 选股
stocks = wa.search_stocks("沪深市场市值超500亿且连续5日上涨的股票")
```

所有方法返回 `list[dict]` 或 `None`。调用前检查 `wa.available`。

### 3.3 注意事项

- **`query` 字段不含空格**（Wind CLI 约束），代码已自动 `strip().replace(" ", "")`
- **单标的一次调用**：Wind 限制每只股票单独查询，代码自动循环
- **积分保护**：实时行情上限 20 只/次，K线超时 20s

---

## 四、积分管理

| 项目 | 说明 |
|------|------|
| 每日免费额度 | 1000 积分 |
| 基本行情查询 | ~1-5 积分/次 |
| 选股/分析类 | ~10-50 积分/次 |
| 领取方式 | 登录 https://aifinmarket.wind.com.cn 自动到账 |
| 充值 | 同上页面可充值 |
| Key 有效期 | 永久，除非手动重置 |

建议：每天登录一次领积分，日常分析 1000 积分够用。

---

## 五、故障处理

### 降级机制

Wind 源自带 `is_available()` 检查 + `CircuitBreaker` 熔断器：
- **CLI 未安装** → `is_available()` 返回 False → 跳过到免费源
- **API Key 未配置** → 同上
- **连续 3 次失败** → 熔断 5 分钟，期间跳过
- **网络/超时错误** → 熔断器触发，自动降级

### 日志定位

```
logger="datasource" 级别 DEBUG：
  "Wind CLI 不可用: 未安装 wind-mcp-skill"
  "Wind CLI[stock_data.get_stock_kline] 异常: ..."
  "Wind 实时[600519] 解析失败: ..."
```

### 常见故障

| 现象 | 原因 | 处理 |
|------|------|------|
| 所有 Wind 数据不可用，但免费源正常 | CLI 未安装或 Key 未配置 | 检查 `~/.wind-aifinmarket/config` 和 `~/.agents/skills/wind-mcp-skill/` |
| Wind 实时行情返回空 | 指标名不匹配 | 日志查看解析失败信息，对照 `references/indicators.md` 修正 |
| 积分耗尽 | 查询量过大 | 登录 AIFin Market 查看积分余额，考虑充值 |

---

## 六、维护

### 6.1 Wind CLI 升级

Wind Skills 通过 Git 仓库分发，升级方式：

```bash
# 1. 进入 Wind CLI 安装目录
cd ~/.agents/skills/wind-mcp-skill

# 2. 拉取最新版本
git pull

# 3. 验证升级后是否正常
node scripts/cli.mjs call stock_data get_stock_price_indicators \
  '{"windcode":"600519.SH","indexes":"最新成交价,涨跌幅"}'
```

升级频率建议：**每月一次**，或看到 AIFin Market 公告有重大更新时。

如果 `git pull` 有冲突，说明本地手动改过文件，先备份再解决：

```bash
# 备份本地修改
git stash
git pull
git stash pop  # 恢复本地修改，处理冲突
```

### 6.2 indicator/tool-contract 升级

升级 CLI 后，同步更新 Claw 中的字段映射：

```bash
# 查看新版 indicators
cat ~/.agents/skills/wind-mcp-skill/references/indicators.md

# 查看新版 tool contracts
cat ~/.agents/skills/wind-mcp-skill/references/tool-contracts.md
```

如果 indexes 列名有变化，同步更新 `data_sources.py` 中 Wind 数据源的 `indexes` 字符串和列名映射字典。

### 6.3 数据一致性验证

升级后建议跑一次验证：

```bash
python -c "
from claw.feeds.data_sources import fetch_realtime, fetch_kline, fetch_fundamentals
r = fetch_realtime(['600519'])
print('实时:', r.get('600519', {}).get('最新价'))
df = fetch_kline('600519', days=3)
print('K线:', df['收盘'].tolist() if not df.empty else '空')
fin = fetch_fundamentals('600519')
print('基本面PE:', fin.get('市盈率'))
"
```

---

## 七、策略使用

### 7.1 监控脚本

```bash
# 全量运行（技术+新闻+风险）
python scripts/wind_monitor.py

# 仅技术指标
python scripts/wind_monitor.py --technical

# 仅新闻
python scripts/wind_monitor.py --news

# 条件选股
python scripts/wind_monitor.py --screening
```

### 7.2 策略模块

```python
from claw.strategies.wind_strategy import WindStrategy, check_overbought_oversold

# 个股分析
ws = WindStrategy("600522", "中天科技")
sig = ws.technical_signals()
# → {"macd_trend": "↓", "rsi": 54.1, "rsi_signal": "正常", "summary": "MACD↓ | RSI=54.1(正常)"}

news = ws.news_brief(top_k=3)
# → [{"title": "中天科技Wind ESG评级...", "date": "2026-07-09"}, ...]

risk = ws.risk_snapshot()
# → {"beta": 0.97, "volatility": 16.04}

# 批量超买超卖扫描
alerts = check_overbought_oversold(
    ["600522", "600206", "000021"],
    {"600522": "中天科技", "600206": "有研新材", "000021": "深科技"},
)
# → [{"code": "600522", "rsi": 54.1, "signal": "正常"}, ...]
```

### 7.3 直接调用高级分析

```python
from claw.feeds.wind_analytics import WindAnalytics

wa = WindAnalytics()
if wa.available:
    stocks = wa.search_stocks("沪深市场MACD金叉且市值超100亿")
    macro = wa.get_macro_data("中国CPI同比", observation="12")
    news = wa.get_news("中天科技", top_k=3)
```

---

## 八、文件清单

| 文件 | 说明 |
|------|------|
| `src/claw/feeds/data_sources.py` | WindKlineSource / WindRealtimeSource / WindFundamentalsSource + 容灾链 |
| `src/claw/feeds/wind_analytics.py` | WindAnalytics 高级分析工具 |
| `src/claw/feeds/__init__.py` | 导出新类 |
| `src/claw/feeds/wind_analytics.py` | WindAnalytics 高级分析工具（9 方法） |
| `src/claw/strategies/wind_strategy.py` | WindStrategy 策略辅助模块 |
| `src/claw/monitoring/wind_monitor.py` | Wind 监控模块（技术/新闻/风险） |
| `scripts/wind_monitor.py` | 监控自动化入口脚本 |
| `.env` | WIND_API_KEY 配置 |
| `~/.wind-aifinmarket/config` | 全局 API Key 配置（与 .env 互通） |
| `~/.agents/skills/wind-mcp-skill/` | Wind CLI 安装目录 |
