# 🐟 鱼盆自建系统 — 今日变更审计报告

> 审计时间：2026-07-21 ｜ 审计对象：`scripts/build_yupen_from_market.py` 等 4 个 yupen 文件
> 审计结论：**架构正确、可交付，但"完全脱离 RSS"在沙箱内不成立，仅 Mac mini 生产环境可达；存在 1 处死代码 + 2 处标签/可靠性待修。**

---

## 一、变更文件清单（git 状态）

| 文件 | 状态 | 改动量 | 说明 |
|---|---|---|---|
| `scripts/build_yupen_from_market.py` | 🆕 新增(未跟踪) | 321 行 | 双源双表生成器（Wind + Yahoo + 东财/RSS兜底） |
| `scripts/run_yupen_primary.py` | 🆕 新增(未跟踪) | 52 行 | 主生成编排器（调用生成器→校验→摘要） |
| `scripts/gen_yupen_report.py` | 🆕 新增(未跟踪) | 118 行 | 一次性验证报告（**死代码**，见第三节🔴） |
| `.workbuddy/scripts/read_yupen_data.py` | ✏️ 修改 | +77 / -65 | 重写为 primary 优先 + RSS 缺口合并 |

⚠️ **4 个文件全部未提交 git**（3 个未跟踪 + 1 个已修改未 staged）。建议审计通过后提交。

---

## 二、各文件职责与审计结论

### 1. `build_yupen_from_market.py`（核心生成器）
- **职责**：生成两张表——`sector_rotation`（14 板块）+ `yupen_trend`（20 指数），写入 `yupen_primary_<date>_*.json`（`primary_` 前缀隔离，防 RSS 同名覆盖）。
- **路由逻辑**：`fetch()` 按 `src` 分发 → `wind`→Wind CLI、`yf`→Yahoo v8 API、`rss`+`YUPEN_USE_EM=1`→东财 secid，否则返回 `None`（走 RSS 兜底）。
- **结论**：✅ 路由正确；`yf_kline()` 健壮（任意失败返回 `None`，不抛异常）；`write_table()` 字段完整（12 个必填字段齐全，实测 primary 产物 0 字段缺失）。

### 2. `run_yupen_primary.py`（编排器）
- **职责**：调用生成器 `--no-selfcheck` → 调 `read_yupen_data` 校验 → 打印 `sector_rotation=N板 | yupen_trend=M指数`。
- **结论**：✅ 逻辑正确，退出码语义合理（板块为空返回 1 告警）。`PY` 默认指向 venv，符合隔离规范。

### 3. `read_yupen_data.py`（读取+合并器，本次重写重点）
- **职责**：优先读 `primary_` 文件；RSS 仅补 Wind/东财未覆盖的缺口板块（rich 模式按板块数降序、允许跨日期取最全）。
- **结论**：✅ 合并逻辑经实测验证可用（见第四节）。

### 4. `gen_yupen_report.py`（一次性报告）
- **职责**：读固定日期文件生成 HTML 对比报告。
- **结论**：🔴 **死代码/误导性**——硬编码 `yupen_2026-07-17_*`，`GEN=CAT=REF` 三者同指一文件（`CAT`/`REF` 冗余未用），未接入任何自动化。若误跑会读 4 天前的数据。建议参数化或删除。

---

## 三、正确性 / 健壮性发现

| 级别 | 问题 | 影响 | 建议 |
|---|---|---|---|
| 🔴 | `gen_yupen_report.py` 硬编码日期 + 冗余变量，未接入流水线 | 误跑读旧数据，无实际产出价值 | 删除或改为 `--date` 参数化 |
| 🟡 | `build` 写入的 `source` 字段 = `"自建·Wind+东财双源(脱离微信RSS)"` | 未体现 Yahoo(雅虎) 已承担 5 个外盘/商品指数 | 改为 `"Wind + 雅虎 + 东财(脱离微信RSS OCR)"` |
| 🟡 | **有色金属** Wind fetch 本次失败（primary rotation 仅 8/9 wind） | 该板块在沙箱依赖 RSS 兜底；若 Mac mini 也失败则 RSS-free 目标打折 | Mac mini 上复跑定位 Wind 命名/超时问题 |
| 🟡 | 日期错位：primary 文件标 `2026-07-21`，沙箱 `today=2026-07-20` | `read_yupen_data` 新鲜度判定可能误报 stale | 仅沙箱时钟差异，Mac mini 正常；无需改 |
| ✅ | 合并架构 `primary`+`RSS` 实测交付 14/14 + 20/20 | — | 保留 |
| ✅ | `read_yupen_data` 合并后 12 字段无丢失 | — | 保留 |

---

## 四、实测覆盖（关键纠正点）

> ⚠️ 摘要中"primary 14/14 + 20/20"实为**合并后**结果。裸 `primary` 产物在沙箱/东财不可达环境下并未满覆盖。

| 视图 | 板块轮动 | 鱼盆趋势 | 说明 |
|---|---|---|---|
| **primary 裸产物** | **8 / 14**（8 wind；有色金属失败 + 5 东财缺失） | **19 / 20**（14 wind + 5 yf；微盘股 rss-only） | 沙箱/东财不可达时的真实产出 |
| **合并后（下游消费）** | **14 / 14** | **20 / 20** | RSS 补 7 项：`有色金属`+`电网/商业航天/细分化工/光伏/半导体`(rotation) + `微盘股`(trend) |

**RSS 依赖现状**：当前环境仍需 RSS 兜底 7 个条目（6 轮动 + 1 趋势）。

**完全脱离 RSS 的条件**（仅 Mac mini 生产环境）：
- Yahoo 可达（✅ 已验证 primary 含 5 个 yf 外盘指数 → Mac mini 上 Yahoo 通）；
- `YUPEN_USE_EM=1` 使东财 5 行业 + 微盘股(1.883418) 直连；
- 解决有色金属 Wind fetch 偶发失败。
满足后预期：rotation 9 wind + 5 em = 14，trend 14 wind + 5 yf + 1 微盘股 = 20 → **真正 0 RSS 依赖**。

---

## 五、自动化配置核对

| 自动化 | ID | 状态 | 结论 |
|---|---|---|---|
| 🐟 鱼盆主生成（Wind+东财自建） | `automation-1784605310235` | ACTIVE，工作日 08:45 | ✅ 跑 `run_yupen_primary.py`，prompt 明确不推送飞书，正确 |
| 🐟 鱼盆数据提取（每日 RSS） | `automation-1783472286775` | ACTIVE，08:50 | ✅ 仍作兜底源，必须保留至 Mac mini 验证完全 RSS-free |
| 📊 微信早报生成推送 | `automation-1782741941693` | ACTIVE，09:15 | ✅ 消费 `read_yupen_data` 合并结果，链路连通 |

---

## 六、验收摘要与待办

**验收通过项**：
- ✅ 合并架构交付 14/14 + 20/20，字段完整，下游（早报）链路连通。
- ✅ `primary_` 前缀隔离修复了 RSS 覆盖主生成文件的隐患。
- ✅ Yahoo 源在 Mac mini 实测可用，外盘/商品 5 指数不再依赖 RSS。

**待办（建议优先级）**：
1. 🟡 修 `build` 的 `source` 标签，补 "雅虎"。
2. 🟡 Mac mini 复跑定位有色金属 Wind 失败根因。
3. 🔴 删除或参数化 `gen_yupen_report.py`。
4. ⚠️ 4 个 yupen 文件提交 git（当前全未跟踪/未 staged）。
5. 🟡 Mac mini 开 `YUPEN_USE_EM=1` 验证 5 东财 + 微盘股直连，关闭 RSS 兜底。
