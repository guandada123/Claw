# Claw (A股投顾自动化助手) — 开发工具链
# 用法: make setup / make lint / make test / make ci

PYTHON := /opt/homebrew/bin/python3.12
PIP_INSTALL := $(PYTHON) -m pip install --break-system-packages
PYTEST := $(PYTHON) -m pytest
MIRROR := -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn

.PHONY: setup lint format test ci help

help: ## 显示帮助
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

setup: ## 初始化开发环境
	$(PIP_INSTALL) pre-commit ruff pytest $(MIRROR)
	pre-commit install --hook-type pre-commit --hook-type commit-msg
	@echo "✅ Claw 开发环境就绪 (Python 3.12)"

lint: ## 运行 lint (ruff)
	ruff check .workbuddy/scripts/ .workbuddy/lib/ --fix --config ruff.toml
	ruff format .workbuddy/scripts/ .workbuddy/lib/ --config ruff.toml

format: ## 格式化代码
	ruff format .workbuddy/scripts/ .workbuddy/lib/ --config ruff.toml

test: ## 运行所有测试
	cd .workbuddy && $(PYTEST) tests/ -v --tb=short

test-unit: ## 仅运行单元测试
	cd .workbuddy && $(PYTEST) tests/test_backtest.py tests/test_sim_trade.py -v

test-integration: ## 仅运行集成测试
	cd .workbuddy && $(PYTEST) tests/test_integration.py -v

test-cov: ## 运行测试并生成覆盖率报告
	cd .workbuddy && $(PYTEST) tests/ --cov=scripts --cov=lib --cov-report=term-missing --cov-fail-under=50

type-check: ## 运行 mypy 类型检查
	cd .workbuddy && mypy scripts/ lib/ --ignore-missing-imports --check-untyped-defs --warn-return-any --warn-redundant-casts || echo "⚠️ mypy 发现类型提示问题（非阻塞）"

ci: ## 模拟完整 CI 流水线
	$(MAKE) lint
	$(MAKE) test-cov
	$(MAKE) type-check
	@echo "✅ CI 通过"
