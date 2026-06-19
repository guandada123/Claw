"""
错误处理基础设施测试
"""

import pytest


class TestClawErrors:
    """异常层次结构测试。"""

    def test_error_hierarchy(self):
        """所有自定义异常都是 ClawError 的子类。"""
        from error_handler import (
            ClawError,
            ConfigError,
            DataCorruptionError,
            DataError,
            DataValidationError,
            NetworkError,
            NonRetryableError,
            StrategyError,
        )

        errors = [
            DataError,
            DataCorruptionError,
            DataValidationError,
            NetworkError,
            ConfigError,
            StrategyError,
            NonRetryableError,
        ]

        for err_cls in errors:
            assert issubclass(err_cls, ClawError)

    def test_error_can_be_raised(self):
        """异常可以正常抛出和捕获。"""
        from errors import DataError, NetworkError

        with pytest.raises(DataError):
            raise DataError("测试数据异常")

        with pytest.raises(NetworkError):
            raise NetworkError("API 超时")


class TestSafeCall:
    """safe_call 装饰器测试。"""

    def test_normal_return(self):
        """正常函数返回原值。"""
        from error_handler import safe_call

        @safe_call(max_retries=1)
        def add(a, b):
            return a + b

        assert add(1, 2) == 3

    def test_retry_on_network_error(self):
        """网络异常触发重试。"""
        from error_handler import NetworkError, safe_call

        call_count = [0]

        @safe_call(max_retries=2, retry_delay=0.01)
        def flaky():
            call_count[0] += 1
            if call_count[0] < 3:
                raise NetworkError("临时故障")
            return "ok"

        result = flaky()
        assert result == "ok"
        assert call_count[0] == 3  # 1 original + 2 retries

    def test_non_retryable_immediate_raise(self):
        """NonRetryableError 不重试，直接抛出。"""
        from error_handler import NonRetryableError, safe_call

        call_count = [0]

        @safe_call(max_retries=3, retry_delay=0.01)
        def doomed():
            call_count[0] += 1
            raise NonRetryableError("不可恢复")

        with pytest.raises(NonRetryableError):
            doomed()
        assert call_count[0] == 1  # 不重试

    def test_exhausted_returns_fallback(self):
        """重试耗尽后返回 fallback_value。"""
        from error_handler import NetworkError, safe_call

        @safe_call(max_retries=1, retry_delay=0.01, fallback_value="default", reraise=False)
        def always_fails():
            raise NetworkError("永续失败")

        result = always_fails()
        assert result == "default"

    def test_unknown_exception_not_suppressed(self):
        """未知异常（不在 retry_on 中）不吞没。"""
        from error_handler import safe_call

        @safe_call(max_retries=2)
        def boom():
            raise ValueError("意外异常")

        with pytest.raises(ValueError):
            boom()

    def test_preserves_function_metadata(self):
        """装饰器保留原函数的 __name__ 和 __doc__。"""
        from error_handler import safe_call

        @safe_call()
        def my_func():
            """文档字符串"""
            return 42

        assert my_func.__name__ == "my_func"
        assert my_func.__doc__ == "文档字符串"


class TestAtomicReadWrite:
    """便捷函数 atomic_read_json / atomic_write_json 测试。"""

    def test_roundtrip(self, temp_dir, sample_portfolio):
        """写入→读取往返完整保留数据。"""
        from error_handler import atomic_read_json, atomic_write_json

        f = temp_dir / "roundtrip.json"
        atomic_write_json(f, sample_portfolio)
        result = atomic_read_json(f)
        assert result == sample_portfolio

    def test_read_nonexistent(self, temp_dir):
        """不存在的文件返回空字典。"""
        from error_handler import atomic_read_json

        assert atomic_read_json(temp_dir / "noexist.json") == {}
