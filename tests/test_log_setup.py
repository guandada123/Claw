"""test_log_setup.py — 日志工厂的轻量测试。"""

import logging

import log_setup


def test_get_logger_returns_logger():
    logger = log_setup.get_logger("test_module")
    assert isinstance(logger, logging.Logger)
    assert logger.level == logging.DEBUG


def test_get_logger_idempotent():
    """重复调用返回同一 logger，handle 不重复追加"""
    a = log_setup.get_logger("test_module_2")
    handler_count_a = len(a.handlers)
    b = log_setup.get_logger("test_module_2")
    assert len(b.handlers) == handler_count_a
    assert a is b
