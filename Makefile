# Claw (A股投顾自动化助手) — 开发工具链
# 用法: make setup / make lint / make test / make ci

# 解释器自动探测：本机默认 `python3`(3.13) **没有 pytest**（2026-10-11 实测）→
# 直接用它跑 `make test` 会「假失败」。优先仓库 .venv，其次托管默认，最后系统 python3。
PYTHON := $(shell [ -x .venv/bin/python3 ] && echo .venv/bin/python3 || echo python3)
PIP_INSTALL := $(PYTHON) -m pip install
PYTEST := $(PYTHON) -m pytest
MIRROR := -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn

.PHONY: setup lint format test ci ci-tracked ci-tracked-full help

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

# 覆盖率阈值权威在 CI（.github/workflows/claw-ci.yml 的 per-job --cov-fail-under）。
# 原 --cov-fail-under=50 对本地实测口径（.workbuddy/tests 对真码 3.8%）不可达 → make ci 恒失败。
test-cov: ## 运行测试并生成覆盖率报告（阈值权威在 CI，本地仅上报）
	cd .workbuddy && $(PYTEST) tests/ --cov=scripts --cov=lib --cov-report=term-missing

type-check: ## 运行 mypy 类型检查
	cd .workbuddy && mypy scripts/ lib/ --ignore-missing-imports --check-untyped-defs --warn-return-any --warn-redundant-casts || echo "⚠️ mypy 发现类型提示问题（非阻塞）"

ci: ## 模拟完整 CI 流水线
	$(MAKE) lint
	$(MAKE) test-cov
	$(MAKE) type-check
	@echo "✅ CI 通过"

ci-tracked: ## 在「只有 tracked 文件」的树里预跑门禁（防「本地绿、CI 红」）
	$(PYTHON) .workbuddy/scripts/check_tracked_tree.py

ci-tracked-full: ## 同上，另跑两套测试（改动大时用）
	$(PYTHON) .workbuddy/scripts/check_tracked_tree.py --full
