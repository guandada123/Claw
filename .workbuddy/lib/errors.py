"""
Claw 项目异常层次结构。

使用规范：
    1. 网络/API 异常 → NetworkError
    2. 数据读写/校验异常 → DataError
    3. 配置异常 → ConfigError
    4. 策略执行异常 → StrategyError
    5. 不可重试的逻辑错误 → NonRetryableError

禁止：
    - raise Exception("...") → 使用具体异常类型
    - except: pass → 必须记录日志或重新抛出
    - except Exception: pass → 至少 logger.warning(...)
"""


class ClawError(Exception):
    """所有 Claw 异常的基类。"""


class DataError(ClawError):
    """数据层异常（读/写/校验失败）。"""


class DataCorruptionError(DataError):
    """数据损坏异常，需要人工介入。"""


class DataValidationError(DataError):
    """数据校验失败。"""


class NetworkError(ClawError):
    """网络请求异常（API 超时、连接失败、HTTP 错误）。"""


class ConfigError(ClawError):
    """配置异常（缺失必填配置、格式错误）。"""


class StrategyError(ClawError):
    """策略执行异常。"""


class NonRetryableError(ClawError):
    """不可重试的错误（逻辑错误，重试无益）。"""
