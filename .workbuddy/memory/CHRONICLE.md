# Claw 项目编年史 — 2026年6月-7月

> 从日日志蒸馏出的关键决策和工程里程碑。Marvis Bridge Monitor 心跳日志已过滤。
> 日常细节保留备份中（`.workbuddy/memory/.backups/`），长效信息已并入 MEMORY.md。

---

## 06-05：项目奠基日

### 模拟炒股系统上线
- 创建 `scripts/sim_trade.py` + `scripts/sim_watchlist.py`
- 初始 ¥30,000 本金，AI 全权自主决策
- 首日建仓：紫光国微 200股 + 士兰微 400股
- 止损首次触发：杉杉股份 -9.35%、天娱数科 -10.33%
- AI 协作六原则写入 SOUL.md，投资想法模板 `templates/investment-idea.md` 创建

### 全量自动化排查
- 18 个自动化中 12 个 cwds 指向过期临时目录 → 统一修复
- 创建 cron_monitor.py 守护进程（后被自动化替代）
- 飞书/微信渠道分流确认

---

## 06-06：自动化全面检修

- 修复 18 处自动化问题（时间冲突×6、rrule缺失×8、分工声明×5、环境优化×5）
- 24 个自动化全部规范化配置
- 推送渠道统一：所有投资类 → 飞书群
- **微信公众号抓取技能** `wechat-article-fetcher` 创建（Playwright + 双策略降级）
- **multi-search-engine**（16引擎）集成到5个自动化+2个专家

---

## 06-07：模式策略确认

- Ask → Plan → Craft 七条协作原则定稿（第⑦条新增）
- 分步骤执行任务完成

---

## 06-08：P3 完成 + 记忆体系修正

- **关键决策**：工作区记忆重定向到 Claw 项目 `.workbuddy/memory/`
- P3 交付：定时任务调度器(APScheduler) + 策略市场后端(8 REST API) + 策略市场前端(ECharts) + CI/CD(.github/workflows)
- 31 个自动化全部 ACTIVE，删除 4 个僵尸任务
- 所有投资类推送改为「直接推送飞书群」，加分区标签 `[模拟炒股]`/`[投资助理]`/`[美股监控]`
- 模拟持仓价格实时刷新修复（库存价格虚增 ¥1,366）
- 积分消耗诊断：模型映射大幅降级，V4-Flash 覆盖日报/复盘/选股

---

## 06-09：自动止损 + QTS Docker 部署

- 首次自动止损触发：紫光国微 -8.95%、士兰微 -8.42%，均触发止损线
- 投顾操盘强化为「全权自主决策，无需询问用户确认」
- 智能选股 prompt 删除所有「等待确认」逻辑
- **QTS v2.0 Docker 部署完成**：13 容器全部启动验证通过

---

## 06-10：Marvis Bridge 搭建 + 全项目审计

- `~/workbuddy_marvis_bridge/` 桥接目录创建
- WorkBuddy = 大脑（调度），Marvis = 手脚（执行）
- Marvis Bridge Monitor 自动化上线（每3分钟扫描）
- **全项目审计**修复：P0 task_id格式不兼容(33条死信)、Shell注入、版本统一
- **直接数据对接**：截图→OCR切换为MCP/Skill直连API

---

## 06-11：行为边界 + PDF 研报分析

- **关键决策**：投顾是全权基金经理，用户是LP，绝对禁止向用户汇报操作问题
- **PDF研报分析系统**创建：pdf_extractor.py + pdf-analyzer skill + 研报分析模板
- **向量知识库**搭建：chromadb + paraphrase-multilingual-MiniLM-L12-v2，14篇报告索引入库
- 幂等性bug发现：idempotency_key年份粒度导致快照误判重复

---

## 06-12：士兰微止损 + 基础设施交付

- 士兰微触发 -8.49% 止损清仓（买入35.07→卖出32.15，实亏¥1,191）
- W23周收益-7.53%，止损规则新增L001-L003
- **多专家全部注册上线**（7位）
- `macro_data.py`(AKShare) + `market_data.py`(forex/commodity/bond-yield) + `automation_health.py`

---

## 06-13 ~ 06-15：稳定运行期

- 公众号信号溯源系统搭建（39篇缓存→53篇索引→43条信号记录）
- 系统稳定，无重大变更

---

## 06-16 ~ 06-17：全盘治理

- 全系统统一监控：`health_check.py` + `output/dashboard.html`(ApexCharts)
- **全盘项目清查**：6活跃+2备份，0遗漏
- **Dependabot全仓库配置**：StockInsight/Claw/QTS/PMF
- StockInsight CI 修复（Prettier/ruff/TSC/Build/Vitest 全部通过）

---

## 06-18：量化策略迭代

- **BBR参数优化**：period=20, std_mult=1.8, rsi(40/60)，21股平均+11.27%
- **COMBO组合策略**：VWM(0.6)+BBR(0.4)加权投票
- **ADX/DMI趋势策略**：adx>22确认信号，21股平均+4.76%
- **实盘信号管线**上线：live_pipeline.py → 腾讯行情 → Postgres → JSON
- StockInsight Pro Tauri 桌面端打包成功
- 系统健康巡检自动化上线（每小时）

---

## 06-19：冲刺模式审计

- STRATEGY.md v1.2(98行)→v2.1(360行)：信号管线/ATR止损/分级止盈/跨盘风控/再平衡/月尾冲刺
- 行业集中度 60%→40%，MAX_SECTOR_PCT=0.40
- P0发现：v2.1从未写入磁盘、资金利用率仅33%

---

## 06-20：成本分析 + 修复

- DeepSeek 成本分析 + Codex/PLUS 评估 → 结论：先切包年672元
- order_source 区分手动/自动下单
- 持仓路径修复：实盘策略信号 cwd 补 `.workbuddy/` 前缀

---

## 07-11：上下文健康度清理

- 备份14个旧日志(6/05-6/19~108KB)→蒸馏入MEMORY.md→删除
- MEMORY.md 7.3KB→13KB，覆盖全部长效信息
- 云记忆缓存10.9KB（非本地可治，需客户端删旧对话）
