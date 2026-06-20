"""log_setup.py — Claw 统一日志工厂"""
import logging
import logging.handlers
from pathlib import Path

LOG_DIR = Path.home() / ".claw" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def get_logger(name: str) -> logging.Logger:
    """
    返回配置好的 logger：
    - DEBUG+ 写入滚动文件（每 10MB 轮转，保留 5 份）
    - WARNING+ 同时输出控制台（保留 emoji 友好格式）
    """
    logger = logging.getLogger(name)
    if logger.handlers:          # 防重复初始化
        return logger

    logger.setLevel(logging.DEBUG)

    # 文件 Handler — RotatingFileHandler
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / f"{name}.log",
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))

    # 控制台 Handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter("%(message)s"))

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger
