# 大类资产配置宏观因子数据手册

> Marvis 研究输出 | 2026-06-11 | 目标交付：WorkBuddy 策略引擎

---

## 一、宏观因子数据源（akShare 全免费）

### 1.1 增长因子 — 工业增加值（月频）

| 属性 | 值 |
|------|-----|
| 接口 | `ak.macro_china_gyzjz()` |
| 频率 | 月频 |
| 时间范围 | 2008年至今 |
| 字段 | 月份、同比增长(%)、累计增长(%) |

```python
import akshare as ak

# 工业增加值（月频GDP代理）
df = ak.macro_china_gyzjz()
print(df.tail())
#      月份  同比增长  累计增长
# 0  2026-04  5.8   6.1
```

### 1.2 通胀因子 — CPI / PPI（月频）

| 属性 | 值 |
|------|-----|
| CPI接口 | `ak.macro_china_cpi_monthly()` |
| PPI接口 | `ak.macro_china_ppi_yearly()` |
| 频率 | 月频 |
| 时间范围 | CPI: 1996年至今 / PPI: 1995年至今 |

```python
cpi = ak.macro_china_cpi_monthly()
ppi = ak.macro_china_ppi_yearly()
```

### 1.3 流动性因子 — M2 / 社融（月频）

| 属性 | 值 |
|------|-----|
| M2接口 | `ak.macro_china_m2_yearly()` |
| 频率 | 月频 |
| 来源 | 金十数据中心 |

```python
m2 = ak.macro_china_m2_yearly()
# 社融数据需从东方财富接口补充
# ak.macro_china_new_financial_credit() — 新增信贷
```

### 1.4 利率因子 — 实际利率

**计算方式**：实际利率 = 10年期国债收益率 - CPI同比

| 组件 | 接口 | 说明 |
|------|------|------|
| 10年国债 | `ak.bond_zh_us_rate()` | 中国国债收益率10年列 |
| CPI | `ak.macro_china_cpi_monthly()` | CPI月率，需转换为同比 |

```python
# 中美利差 + 中国10年期国债
bond = ak.bond_zh_us_rate(start_date="20200101")
china_10y = bond[['日期', '中国国债收益率10年']]

# 备选：完整收益率曲线（跨度为1年，需循环拉取）
# curve = ak.bond_china_yield(start_date="20250101", end_date="20251231")
# 过滤 '中债国债收益率曲线' 取 '10年' 列
```

### 1.5 信用因子 — 信用利差

**计算方式**：AA企业债收益率 - 同期限国债收益率

**方案一（推荐）**：akShare 中债收益率曲线
```python
# bond_china_yield 返回包含多种债券类型的收益率曲线
# 过滤 '中债企业债收益率曲线(AA)' 和 '中债国债收益率曲线'
# 差值即为信用利差，跨度限制1年，需循环拉取
curve = ak.bond_china_yield(start_date="20250101", end_date="20250611")
aa_bond = curve[curve['曲线名称'] == '中债企业债收益率曲线(AA)']
gov_bond = curve[curve['曲线名称'] == '中债国债收益率曲线']
# credit_spread = aa_bond['5年'] - gov_bond['5年']
```

**方案二**：Tushare Pro（需token）
```python
# pro.yc_cb(ts_code='', curve_type='0', date='')
```

---

## 二、Fama-French 三因子（中国A股版）

### 2.1 权威公开数据源

**北京大学光华管理学院**（免费，推荐）
- 地址：`https://www.gsm.pku.edu.cn/finvc/info/1027/1147.htm`
- 文件：`factors_monthly_2023.xlsx`
- 覆盖：2000年1月 - 2023年12月（月频）
- 包含：MKT、SMB-FF3、HML-FF3（三因子）+ SMB-FF5、HML-FF5、RMW、CMA（五因子）+ MOM（动量）
- 技术文档：Technical Document_Factors.pdf 详细描述构建流程

### 2.2 标准构建方法（摘自论文 + BigQuant实践）

```
Step 1: 每年6月底，按市值中位数分 S(小)/B(大)
Step 2: 按账面市值比(BM)的30%/70%分位点分 L(低)/M(中)/H(高)
Step 3: 交叉形成 6 个组合：SH, SM, SL, BH, BM, BL
Step 4: 每个组合市值加权计算月收益率

SMB = (SH+SM+SL)/3 - (BH+BM+BL)/3
HML = (SH+BH)/2 - (SL+BL)/2
MKT = 全市场市值加权收益率 - 无风险利率
```

**注意**：中国版建议剔除市值最小30%的股票（壳价值污染，Liu et al. 2018）

### 2.3 辅助数据源

| 来源 | 获取方式 | 成本 |
|------|----------|------|
| CSMAR因子研究系列 | API / WRDS | 付费（高校可申请） |
| RESSET | 数据库查询 | 付费 |
| 自建 | akShare A股日线 + 财务报表 | 免费（需写构建代码） |

---

## 三、大类资产行情数据（akShare 全覆盖）

### 3.1 股票指数

| 资产 | akShare 接口 | Symbol | 频率 | 来源 |
|------|-------------|--------|------|------|
| 沪深300 | `stock_zh_index_daily()` | `sh000300` | 日频 | 新浪 |
| 中证500 | `stock_zh_index_daily()` | `sh000905` | 日频 | 新浪 |
| 上证综指 | `stock_zh_index_daily()` | `sh000001` | 日频 | 新浪 |
| 恒生指数 | `stock_hk_index_daily_em()` | `HSI` | 日频 | 东方财富 |
| 标普500 | `index_us_stock_sina()` | `.INX` | 日频 | 新浪 |

```python
hs300 = ak.stock_zh_index_daily(symbol="sh000300")
zz500 = ak.stock_zh_index_daily(symbol="sh000905")
hsi   = ak.stock_hk_index_daily_em(symbol="HSI")
sp500 = ak.index_us_stock_sina(symbol=".INX")
```

### 3.2 债券指数

| 资产 | akShare 接口 | 说明 |
|------|-------------|------|
| 中证全债 | `bond_composite_index_cbond()` | 中债指数，需指定indicator='财富' |
| 中国全债替代 | 自建：中债国债总财富指数 | CBA00101 |

```python
# 中债综合指数（财富）
bond_idx = ak.bond_composite_index_cbond(indicator='财富', period='总值')
```

### 3.3 商品指数

| 资产 | 获取方式 | 说明 |
|------|----------|------|
| 南华商品指数 | `futures_index_nh()` | 南华期货指数系列 |
| COMEX黄金 | `futures_foreign_hist(symbol='GC')` | 国际期货，日频 |
| COMEX黄金备选 | `futures_foreign_commodity_realtime('黄金')` | 实时行情 |

```python
# 南华商品指数
nh = ak.futures_index_nh(symbol="NHCI")

# COMEX黄金期货
gold = ak.futures_foreign_hist(symbol="GC")
```

### 3.4 无风险利率

| 数据 | 接口 | 说明 |
|------|------|------|
| 美国3个月国债 | `bond_zh_us_rate()` | '美国国债收益率3月'列 |
| SHIBOR | `macro_china_shibor_all()` | 中国银行间拆借利率 |

---

## 四、Blyth 因子配置框架实现指南

### 4.1 原始论文

- **标题**：Flexible Indeterminate Factor-Based Asset Allocation (FIFAA)
- **作者**：Blyth, Szigety, Xia (2016)
- **来源**：Harvard Management Company (HMC) 实践，2015年9月后HMC全面转向因子化思维
- **中文解读**：广发证券《用宏观因子穿透资产》（2025-06-14）、兴业证券《系统化资产配置系列之八》

### 4.2 四步框架

```
Step 1: 选择因子
  → 宏观因子（增长/通胀/利率/信用/流动性）
  → 或PCA提取公因子（增长/利率/通胀三大类可解释80%风险）

Step 2: 计算风险暴露（LASSO回归）
  → R_it = α_i + Σ β_ij × F_jt + ε_it
  → LASSO正则化压缩不显著系数：min ||R - AF||² + λ||A||₁

Step 3: 确定目标因子暴露
  → 方案A：因子风险平价（各因子RC相等）
  → 方案B：主观配置（根据宏观判断）

Step 4: 匹配目标暴露 → 反推资产权重
  → Blyth目标函数：
  → min_w [(w^T A - e^T) + γ(w^T A - e^T)P(A^T w - e) + w^T Q w]
  → A: 因子暴露矩阵, e: 目标暴露, P: 因子协方差, Q: 特异性风险
  → γ: 通常取0.99
```

### 4.3 LASSO 实现参数

```python
from sklearn.linear_model import LassoCV

# 关键参数
model = LassoCV(
    cv=5,              # 5折交叉验证
    max_iter=5000,     # 最大迭代
    fit_intercept=True,
    random_state=42,
    n_alphas=100,      # alpha搜索网格
    selection='cyclic'
)
model.fit(factor_returns, asset_returns)
# model.coef_ → 因子暴露向量
# model.alpha_ → 最优正则化参数
```

### 4.4 优化求解

```python
from scipy.optimize import minimize

def blyth_objective(w, A, e, P, Q, gamma=0.99):
    """Blyth因子配置目标函数"""
    factor_dev = w @ A - e
    factor_risk = factor_dev @ P @ factor_dev.T
    specific_risk = w @ Q @ w
    return np.sum(factor_dev**2) + gamma * factor_risk + specific_risk

constraints = [
    {'type': 'eq', 'fun': lambda w: np.sum(w) - 1},  # 全额投资
]
bounds = [(0, 1) for _ in range(n_assets)]  # 不允许卖空
result = minimize(blyth_objective, x0, args=(A, e, P, Q),
                  constraints=constraints, bounds=bounds)
```

---

## 五、与 WorkBuddy 现有模块的对接映射

| 数据需求 | 现有模块 | 对接方式 |
|----------|----------|----------|
| 宏观因子数据 | 新建 `macro_data.py` | 调用 akShare 接口，写入 `data/macro/` |
| Fama-French因子 | 下载北大xlsx → 转存JSON | 写入 `data/factors/ff3_china.json` |
| 大类资产行情 | `market_data.py` | 扩展指数列表（已含部分） |
| 信用利差 | `market_data.py` | 新增函数 `get_credit_spread()` |
| Blyth引擎 | `strategy_generator.py` | 新增 `blyth_factor_allocate()` |
| 回测框架 | `backtest.py` | 新增风险平价/MVO/因子配置模式 |

---

## 六、数据优先级与实施路线

| 优先级 | 数据 | 接口已确认 | 预估工时 |
|:---:|------|:---:|:---:|
| P0 | 10年国债收益率 | ✅ `bond_zh_us_rate` | 0.5h |
| P0 | 沪深300/中证500 | ✅ `stock_zh_index_daily` | 0.5h |
| P0 | 股债相关性计算 | 基于上述两项 | 1h |
| P1 | CPI/PPI/工业增加值 | ✅ | 1h |
| P1 | M2 | ✅ `macro_china_m2_yearly` | 0.5h |
| P1 | 中证全债/南华商品/COMEX黄金 | ✅ | 1h |
| P1 | Fama-French因子 | 下载+格式化 | 2h |
| P2 | 信用利差 | `bond_china_yield` 循环拉取 | 2h |
| P2 | 标普500/恒生 | ✅ | 1h |
| P3 | Blyth因子引擎 | 全部数据到位后 | 4h |

---

*研究日期：2026-06-11 | 数据截止有效性：2026-06-11*
