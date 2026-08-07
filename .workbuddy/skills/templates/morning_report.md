# 📊 微信早报 — {{DATE}}（{{WEEKDAY}}）

数据基准：行情={{PREV_CLOSE_DATE}} 收盘；鱼盆={{YUPEN_DATE}}；公众号={{WX_STATUS}}
核心原则：分析 > 罗列，从「鱼盆数据 × 公众号」交叉验证中找机会。
颜色规则：🔴 强势/看多 ｜ 🟢 弱势/看空 ｜ 🟡 中性/观望（A股红涨绿跌）

---

## 🩺 今日风险（高/中/低分层）

- 🔴 [高] {{RISK_HIGH}}
- 🟡 [中] {{RISK_MID}}
- 🟢 [低] {{RISK_LOW}}

**🧠 情绪温度计**：🔴/🟡/🟢 **{{SENTIMENT_LABEL}}**（{{SENTIMENT_BASIS}}）
- 依据：{{SENTIMENT_EVIDENCE}}
- 指引：{{SENTIMENT_GUIDE}}

📋 **昨晚报动作回顾**：{{YESTERDAY_ACTION_REVIEW}}

---

## 二、公众号汇总（含权重标注）★ 交叉验证核心

{{WX_SECTION}}

---

## 三、鱼盆数据驱动机会

{{YUPEN_OPPORTUNITY}}

---

## 四、今日操作预案

{{TODAY_PLAN}}

---

## ⚠️ 宏观评分铁律（防回归）

- 宏观综合评分**已由 assemble 在「五、今日宏观数据」段自动计算注入**（如 `**宏观综合评分**：+5（中性）｜依据：...`）。
- **直接转述 assemble 注入的评分**，禁止自行调用/编造 `macro_score` 函数。
- **严禁输出**：`None`、`macro_score 函数返回 None`、任何把废弃函数名当占位符的文案。
- 若 assemble 未注入评分（极端异常），输出「暂无可量化指标，按定性研判」，不得写 None。
