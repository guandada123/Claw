# 助理全主板选股器落地（B方案）

## 做了什么
把 📊炒股助理 从「纯持仓监控」升级为「全主板主动选股器」，复用投顾 v2.0 的 QTS COMBO 信号体系，每天收盘后自动扫全主板并向用户推新股票建议。

## 关键成果
1. **基础设施复用**：发现 QTS `daily_quote` 已有 3521 只全市场日线（到 7/13），跳过 westock 批量回填，直接 SQL 取数。
2. **基准池构建**：主板(8前缀) + 近20日均成交额≥3亿 + 非ST/退 → **1076 只**（`mainboard_scan_pool.json`）。
3. **扫描脚本**：`scan_mainboard_full.py` 容器内运行，复用 QTS COMBO=VWM(0.6)+BBR(0.4)，ADX≥25过滤，RSI>80拦截，COMBO≥0.2买入，输出完整建议格式（代码+价位+手数+止损+周期+风险）。
4. **实测结论（7/14）**：全主板 1067 只有效扫描，**0 只买入候选**——市场系统性回调后死水期，VWM/BBR 全 0，与投顾扩大池扫描自洽。
5. **收盘自动化**：`automation-1784013251841`，每个交易日 15:30 跑全主板扫描并推飞书（📊炒股助理前缀）。
6. **规则固化**：写入 MEMORY.md「助理全主板选股规则」段。

## 交付文件
- `/Users/guan/WorkBuddy/Claw/.workbuddy/scripts/scan_mainboard_full.py` — 全主板扫描脚本
- `/Users/guan/WorkBuddy/Claw/.workbuddy/scripts/mainboard_scan_pool.json` — 1076只基准池
- `/Users/guan/WorkBuddy/Claw/.workbuddy/scripts/astock_code_name.json` — 全市场代码名称(ST映射)
- `/Users/guan/WorkBuddy/Claw/.workbuddy/scripts/mainboard_liq_pool.json` — 流动性初筛池(1084只)

## 下一步
- 等市场 COMBO 翻正（死水期结束）后，自动化会在收盘后自动推买入候选
- 鱼盆板块数据当前 STALE(7/8)，恢复后可增强板块共振权重
