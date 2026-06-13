# ADR-002: 统一的错误处理标准

## 状态
已采纳（2026-06-12）

## 背景
Claw 项目存在多处裸 `except:` 和 `except: pass` 模式，异常被静默吞没，排查困难。

## 决策
1. **异常层次结构**：定义 `ClawError` 基类及 6 个子类（`DataError`, `NetworkError`, `ConfigError`, `StrategyError`, `NonRetryableError`, `DataValidationError`）
2. **安全调用装饰器**：`safe_call()` 提供自动重试 + 结构化日志，未知异常不吞没
3. **零容忍规则**：`except: pass` 禁止出现；至少包含 `logger.error()` 或 `print(..., file=sys.stderr)`

## 后果
- 正向：异常可追踪，排错效率大幅提升
- 正向：重试逻辑标准化（指数退避），网络抖动自动恢复
- 负向：旧代码迁移时需要逐个检查所有 `except` 块
