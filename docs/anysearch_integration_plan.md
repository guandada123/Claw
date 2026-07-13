# AnySearch Skill 接入方案（Claw 项目）

> 生成日期：2026-07-13
> 技能版本：anysearch-skill v2.1.0（已安装至 `~/.workbuddy/skills/anysearch/`）
> 安全审计：✅ 通过（无 P0/P1，仅第三方端点依赖）
> Entry Test：✅ 匿名模式全部通过（finance.quote/fundamental/calendar 验证有效）

---

## 一、AnySearch 能替代什么

| 能力 | AnySearch 子域 | 现有 Claw 实现 | 替代价值 |
|------|---------------|---------------|---------|
| A股实时/历史行情 | `finance.quote` (cn_code=600XXX.SH) | `qt.gtimg.cn` (fetch_us_market.py, market_data.py) | 统一接口，返回结构化 JSON（含 PE/PB/换手率/市值） |
| A股财务指标 | `finance.fundamental` (type=indicator/income/holder) | 无独立实现，依赖 westock MCP | **填补空白**：ROE/毛利率/负债率/股东结构 |
| 财报日历 | `finance.calendar` (type=earnings/dividends) | 无（早报"研报段"常标缺失） | **填补空白**：A股披露日预告 |
| 宏观数据 | `finance.macro` (gdp/cpi/lpr/shibor) | `macro_data.py` (AKShare) | 回退层：AKShare 失败时用 |
| 财经新闻 | `finance.news` (flash/announcement) | 公众号 RSS (wx_rss_auth.py) | 互补：公众号覆盖不了的全市场快讯/公告 |
| 网页提取 | `extract` | scrapling-article-fetch | 回退层：小红书等强反爬站 scrapling 失败时 |

**结论**：AnySearch 不是完全替代现有免费源，而是作为**统一回退层 + 填补数据空白**（财务/财报日历/宏观）。

---

## 二、迁移优先级

### 🔴 P0 — 立即迁移（填补现有空白，零风险）

**1. 财报日历 → 早报/晚报"研报段"**
- 现状：早报第八段、晚报第十段"研报"常因 westock MCP 未连而标 `[缺失]`
- 方案：改用 `finance.calendar` + `finance.news`(announcement) 填充
- 命令：
  ```bash
  python3 ~/.workbuddy/skills/anysearch/scripts/anysearch_cli.py search "财报日历" \
    --domain finance --sub_domain finance.calendar \
    --sub_domain_params '{"type":"earnings"}'
  ```

**2. A股财务指标 → 持仓诊断段补充**
- 现状：持仓诊断只有 RSI + 浮亏，无基本面
- 方案：对实盘 3 只票加 `finance.fundamental`(type=indicator) 取 ROE/负债率
- 匿名即可用，无需 key

### 🟡 P1 — 逐步迁移（替代不稳定源）

**3. 宏观数据回退层**
- 现状：`macro_data.py` 依赖 AKShare，偶发限流
- 方案：AKShare 失败时自动调用 `finance.macro` (lpr/shibor/cpi)
- 改动：在 `macro_data.py` 加 try/except 回退

**4. 财经快讯补充**
- 现状：早报公众号汇总只覆盖 19 个订阅号
- 方案：早报 Step 1 后加 `finance.news`(flash, src=10jqka/eastmoney) 抓全市场异动
- 注意：公众号仍是主源，anysearch 仅作"遗漏补抓"

### 🟢 P2 — 观察评估（不急于迁移）

**5. 实时行情替代 qt.gtimg.cn**
- 现状：`qt.gtimg.cn` 免费稳定，已跑通
- 方案：**暂不改**。AnySearch 匿名限额低（实测 4409ms/次），高频调用不如本地源
- 仅当 gtimg 故障时作为回退

**6. 网页提取替代 scrapling**
- 现状：scrapling 对普通站有效，小红书等强反爬失效
- 方案：scrapling 失败时调用 `extract` 作为二级回退

---

## 三、具体改动清单

### 文件改动

| 文件 | 改动 | 优先级 |
|------|------|--------|
| `.workbuddy/templates/morning_report_template.md` | 第八段"研报"加 anysearch 财报日历命令 | P0 |
| `.workbuddy/templates/evening_report_template.md` | 第十段"研报"加 anysearch 财报日历命令 | P0 |
| `scripts/macro_data.py` | 加 AnySearch `finance.macro` 回退 | P1 |
| `automation-1782741941693` (早报 prompt) | Step 5 改调用 anysearch 而非 westock | P0 |
| `automation-1782817769722` (晚报 prompt) | PHASE 2 第十段改调用 anysearch | P0 |
| 新建 `scripts/anysearch_helper.py` | 封装常用调用（quote/fundamental/calendar/news） | P0 |

### anysearch_helper.py 设计

```python
# scripts/anysearch_helper.py
# 封装 AnySearch CLI 调用，统一返回 Python dict
import subprocess, json, os

SKILL = os.path.expanduser("~/.workbuddy/skills/anysearch/scripts/anysearch_cli.py")
PY = "/Users/guan/.workbuddy/binaries/python/versions/3.13.12/bin/python3"

def _run(args):
    r = subprocess.run([PY, SKILL] + args, capture_output=True, text=True, timeout=40)
    return r.stdout

def a_stock_quote(cn_code: str) -> dict:
    """A股实时行情，返回最新日线 dict"""
    out = _run(["search", cn_code, "--domain", "finance",
                "--sub_domain", "finance.quote",
                "--sub_domain_params", json.dumps({"type":"stock","cn_code":cn_code,"symbol":""})])
    # 解析第一个 JSON 块
    ...

def a_stock_indicator(cn_code: str) -> dict:
    """ROE/毛利率/负债率"""
    ...

def earnings_calendar(days=7) -> list:
    """未来 N 天财报披露"""
    ...

def finance_news(src="10jqka", period="1d") -> list:
    """全市场快讯"""
    ...
```

---

## 四、回退策略（关键）

**原则：AnySearch 是增强层，不是唯一依赖。**

```
数据获取流程：
1. 优先本地源（qt.gtimg.cn / AKShare / 公众号RSS）
2. 本地源失败 → 调用 AnySearch（匿名）
3. AnySearch 也失败 → 标 [数据缺失]，不阻断报告生成
```

**匿名限额处理**：
- AnySearch 匿名限额较低（实测单次 ~4s）
- 早报/晚报各调用 ≤3 次（quote/fundamental/calendar）
- 不用于高频实时拉价（止损检查仍用 qt.gtimg.cn）

**API Key 可选**：
- 如需更高限额，用户可提供邮箱自动注册（anysearch 注册 API 免验证码）
- Key 存 `.env`，不进 git，不进聊天

---

## 五、风险提示

| 风险 | 等级 | 缓解 |
|------|------|------|
| 第三方服务中断 | 中 | 多级回退，不阻断报告 |
| 匿名限额耗尽 | 低 | 控制调用次数 ≤3/次 |
| 数据延迟（非实时） | 中 | 实时价仍用 gtimg，anysearch 仅基本面/日历 |
| 隐私（查询发第三方） | 低 | 不查持仓成本/个人账户，仅公开行情 |

---

## 六、验收标准

- [x] `scripts/anysearch_helper.py` 封装完成（quote/indicator/calendar/news/macro_indicator 5 函数），测试通过
- [x] 早报第七·五段「🌐宏观景气」+ 第八段「财报日历&基本面」接入 anysearch 双源（westock优先+AnySearch降级）
- [x] 晚报九·五段「🌐宏观景气」+ 十段「财报日历&基本面」同步
- [x] `macro_data.py` 加 `_with_fallback()` 后，gdp/cpi/money_supply/lpr/shibor 5类 AKShare 失败时自动补 AnySearch（`_meta.anysearch_fallback` 标注回退指标）
- [x] `earnings_calendar.py` 统一到 `anysearch_helper.earnings_calendar()`（消除重复逻辑）
- [x] 早报 prompt 加 Step 4.5 宏观景气；晚报 prompt PHASE1 加宏观采集 + PHASE2 段清单补九·五
- [x] 所有改动不增加报告生成失败率（回退到位；源标注 akshare/anysearch/[缺失] 透明）
- [x] 修复隐藏 bug：`_run_cli` 的 `timeout=TIMEOUT`→`TIMEOUT_ANYSEARCH`

### 实测结论（2026-07-13）
- AnySearch `finance.macro` 仅 gdp/cpi/money_supply/lpr/shibor 5类返回结构化 JSON；pmi/social_financing 被维基/可汗学院/网页噪音污染无数据 → 回退层不覆盖后两类
- LPR 经 AnySearch 返回干净（1y=3.0/5y=3.5），而 AKShare LPR 接口首行含 1991 历史脏数据 → 回退在 AKShare 脏数据时反而是更优源
- 匿名限额实测低（高频易触限流），故行情(P2)维持 qt.gtimg.cn 不动，anysearch 仅作降级兜底

---

## 七、下一步

1. 用户确认方案 → 我先写 `scripts/anysearch_helper.py`
2. 改两个模板的"研报段"
3. 改两个自动化 prompt 的 Step 5 / PHASE 2
4. 灰度运行 1 天，对比报告完整性
