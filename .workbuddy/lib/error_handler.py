"""
安全调用工具：装饰器 + 辅助函数。

提供：
    - safe_call: 自动重试 + 结构化日志的装饰器
    - atomic_read_json: 原子读 JSON（带校验）
    - atomic_write_json: 原子写 JSON（先 tmp → rename）
"""

import functools
import json
import os
import shutil
import threading
import time
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

try:
    from .errors import DataValidationError, NetworkError, NonRetryableError
except ImportError:
    from errors import DataValidationError, NetworkError, NonRetryableError

F = TypeVar("F", bound=Callable)


# ---------- 日志 ----------


def _get_logger():
    """获取 logger（兼容无 loguru 环境）。"""
    try:
        from loguru import logger

        return logger
    except ImportError:
        import logging

        return logging.getLogger("claw")


logger = _get_logger()


# ---------- safe_call 装饰器 ----------


def safe_call(
    max_retries: int = 3,
    retry_delay: float = 1.0,
    retry_on: tuple = (NetworkError,),
    fallback_value: Any = None,
    reraise: bool = True,
    log_level: str = "warning",
) -> Callable[[F], F]:
    """
    通用安全调用装饰器。

    Args:
        max_retries: 最大重试次数（不含首次）
        retry_delay: 重试初始延迟（秒），每次翻倍（指数退避）
        retry_on: 触发重试的异常类型
        fallback_value: 重试耗尽后返回的默认值
        reraise: 重试耗尽后是否抛出最后一个异常
        log_level: 日志级别

    Example:
        @safe_call(max_retries=2, retry_on=(NetworkError,))
        def fetch_data():
            ...
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except NonRetryableError:
                    raise
                except retry_on as e:
                    last_exc = e
                    if attempt < max_retries:
                        getattr(logger, log_level)(
                            "Retry %d/%d for %s: %s",
                            attempt + 1,
                            max_retries,
                            func.__name__,
                            e,
                        )
                        time.sleep(retry_delay * (2**attempt))
                    else:
                        logger.error(
                            "All retries exhausted for %s: %s\n%s",
                            func.__name__,
                            e,
                            traceback.format_exc(),
                        )
                except Exception as e:
                    logger.error(
                        "Unexpected error in %s: %s\n%s",
                        func.__name__,
                        e,
                        traceback.format_exc(),
                    )
                    raise

            if reraise and last_exc:
                raise last_exc
            return fallback_value

        return wrapper  # type: ignore[return-value]

    return decorator


# ---------- 原子 JSON 读写 ----------


class AtomicJSONWriter:
    """
    原子写入 JSON 文件。

    流程：备份原文件 → 写入临时文件 → 校验 → rename 替换。
    写入失败不会污染原文件。
    """

    def __init__(
        self,
        filepath: Path,
        schema: dict | None = None,
        backup_dir: Path | None = None,
        max_backups: int = 10,
    ):
        self.filepath = Path(filepath)
        self.schema = schema
        self.backup_dir = Path(backup_dir) if backup_dir else self.filepath.parent / ".backups"
        self.max_backups = max_backups
        self._lock = threading.Lock()

    def write(self, data: dict) -> None:
        """线程安全的原子写入。"""
        if self.schema:
            self._validate(data)

        with self._lock:
            self._backup()

            # 使用线程ID确保并发时临时文件不冲突
            tid = threading.get_ident()
            tmp_path = self.filepath.with_suffix(f".{tid}.tmp")
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False, default=str)
                # 验证写入内容可读
                with open(tmp_path, encoding="utf-8") as f:
                    json.load(f)
                # 原子 rename
                os.replace(tmp_path, self.filepath)
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise

    def read(self) -> dict:
        """读取并校验现有数据。"""
        if not self.filepath.exists():
            return {}
        with open(self.filepath, encoding="utf-8") as f:
            data = json.load(f)
        if self.schema:
            self._validate(data)
        return data

    def _validate(self, data: dict) -> None:
        """JSON Schema 校验。"""
        try:
            import jsonschema

            jsonschema.validate(instance=data, schema=self.schema)
        except ImportError:
            logger.warning("jsonschema 未安装，跳过 Schema 校验")
        except jsonschema.ValidationError as e:
            raise DataValidationError(f"JSON Schema 校验失败: {e.message}") from e

    def _backup(self) -> None:
        if not self.filepath.exists():
            return
        if self.max_backups <= 0:
            return
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        backup_path = self.backup_dir / f"{self.filepath.stem}_{timestamp}{self.filepath.suffix}"
        shutil.copy2(self.filepath, backup_path)
        # 清理旧备份
        backups = sorted(self.backup_dir.glob(f"{self.filepath.stem}_*{self.filepath.suffix}"))
        for old in backups[: -self.max_backups]:
            old.unlink()


# ---------- 便捷函数 ----------


def atomic_write_json(
    filepath: Path,
    data: dict,
    backup: bool = True,
    max_backups: int = 10,
) -> None:
    """便捷函数：原子写入 JSON 文件。"""
    writer = AtomicJSONWriter(filepath, max_backups=max_backups if backup else 0)
    writer.write(data)


def atomic_read_json(filepath: Path) -> dict:
    """便捷函数：读取 JSON 文件。"""
    writer = AtomicJSONWriter(filepath)
    return writer.read()


# ---------- 从 errors 重导出（方便单 import） ----------

try:
    from .errors import (  # noqa: F401
        ClawError,
        ConfigError,
        DataCorruptionError,
        DataError,
        DataValidationError,
        NetworkError,
        NonRetryableError,
        StrategyError,
    )
except ImportError:
    from errors import (  # noqa: F401
        ClawError,
        ConfigError,
        DataCorruptionError,
        DataError,
        DataValidationError,
        NetworkError,
        NonRetryableError,
        StrategyError,
    )
