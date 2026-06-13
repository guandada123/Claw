# Claw — A股投顾自动化助手

自动化 A 股投资分析与模拟交易系统，集成微信读书信息源、AKShare/通达信行情、多策略回测与飞书推送。

## 功能模块

| 模块 | 说明 |
|------|------|
| `sim_trade` | 模拟交易引擎（止盈/追踪止损/仓位管理） |
| `backtest` | 均线交叉 & 突破策略回测 |
| `star_signal` | 星级信号适配器 |
| `expert_team_analyst` | AI 多智能体分析 |
| `market_data` | AKShare/通达信行情数据 |
| `macro_data` | 宏观经济数据采集 |
| `weread_fetch` | 微信读书投资文章抓取 |
| `generate_daily_report` | 每日复盘报告生成 |
| `cron_monitor` | 自动化任务健康监控 |
| `knowledge_base` | 知识库管理 |

## 快速开始

```bash
# 安装依赖
make setup

# 运行测试
make test

# 运行 lint
make lint

# 完整 CI 检查
make ci
```

## 项目结构

```
Claw/
├── .workbuddy/
│   ├── scripts/       # 核心业务脚本
│   ├── lib/           # 共享库 (error_handler, errors)
│   ├── tests/         # 测试 (89个)
│   ├── data/          # 数据文件
│   ├── automations/   # 自动化配置
│   └── reports/       # 生成的报告
├── .github/workflows/ # CI 流水线
├── Makefile           # 开发工具链
├── ruff.toml          # Lint 配置
└── pyproject.toml     # 项目配置
```

## 测试

```bash
make test          # 全部测试 (89个)
make test-unit     # 仅单元测试
make test-integration  # 仅集成测试
```

覆盖模块：backtest / sim_trade / error_handler / atomic_writer / portfolio / integration

## 技术栈

- **Python 3.12** — 核心运行时
- **AKShare** — A股行情数据
- **ruff** — 代码检查与格式化
- **pytest** — 测试框架
- **pre-commit** — Git 钩子 (lint + conventional commits)
- **GitHub Actions** — CI (lint → test → security)

## License

Private
