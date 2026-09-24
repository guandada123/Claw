# Changelog

## [2026-09-24] 统一巡检中枢 · 发现回路落地半程（Mode B P0）

### 新增
- `.workbuddy/scripts/apply_doc_candidates.py` — 文档类候选质量闸 + 校准账本（calibrate=只读对拍 / live=出计划，永不写 skill 文档）
- 自动化「📥 每日·文档类落地（校准期）」`1fd52465-899f-4525-a82d-4cc5df01155c`（DAILY 07:45，校准至 2026-10-08）
- `.workbuddy/inspection_hub/calibration/apply_dryrun_20260924.md` — 首份校准对拍报告

### 变更
- `registry.json` 新增 `doc_apply` 配置块（mode/top_n/quality_gate/batch_rule/switch_criteria）与 `_state_sync_notes` 漂移登记
- `registry.json` `automations[]` 补新条目；rrule/next_run 按 scheduler DB 真值校正（技能演进→SA 23:22、技能卫生→1日 23:44）
- 同步纪律确立：**DB=唯一真值，registry=只读镜像**

### 文档
- `output/discovery-loop-upgrade-2026-09-24.md` 增补 §八 P0 实施记录、§九 校准期→生效期切换门槛

## [2026-06-13] Phase 13: 配置模板

### 新增
- .env.example (行情/AI/飞书/交易参数/微信读书)

## [2026-06-13] Phase 11: CI 加固 + 类型安全 + 项目文档

### 项目文档
- 新增 README.md (功能模块/快速开始/项目结构/技术栈)

### 类型安全
- pyproject.toml: 添加 [tool.mypy] 渐进式类型检查配置
- CI: 添加 type-check (mypy) 步骤 (非阻塞)

### CI 加固
- 覆盖率阈值 30% → 50%
- CI 流水线: lint → type-check → test → security

### 开发体验
- Makefile: 添加 test-cov/type-check 目标
- pyproject.toml: 添加 [tool.coverage] 配置

## [2026-06-13] 全维度代码质量优化

### 安全修复
- backtest.py 空异常吞没 → 分类型日志记录
- expert_team_analyst.py 正则注入 → 非贪婪匹配

### 质量基础设施
- ruff 16组规则 + 582处自动修复
- pre-commit hooks (ruff lint/format + conventional commits)
- GitHub Actions CI (lint → test → security)
- Dependabot 依赖自动更新

### 测试
- 72个测试全部通过 (单元35 + 集成9 + 系统28)
- test_backtest.py: calc_ma/calc_highest/backtest_ma_cross/backtest_breakout/fetch_kline
- test_sim_trade.py: check_restricted/calc_commission/calc_stamp_tax/calc_total_asset/check_stop_loss
- test_integration.py: CLI端到端/买卖完整流程/止损触发

### Bug修复
- AtomicJSONWriter 并发写入竞态 → threading.Lock + 唯一临时文件名
- error_handler.py re-export 误删 → noqa:F401 保护

### 性能
- calc_ma: O(n²) → O(n) 滑动窗口
- calc_highest: O(n²) → O(n) 单调队列

### 开发体验
- Makefile: make setup/lint/test/ci 一键操作
- Python 3.12 标准化
