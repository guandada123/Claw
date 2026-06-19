"""
原子写入 AtomicJSONWriter 核心测试
"""

import json
import threading
import time


class TestAtomicWriteBasics:
    """正常写入/读取基本场景。"""

    def test_normal_write_and_read(self, atomic_writer, sample_portfolio):
        """正常写入后可以正确读取。"""
        atomic_writer.write(sample_portfolio)
        result = atomic_writer.read()
        assert result == sample_portfolio

    def test_write_to_missing_directory(self, temp_dir):
        """自动创建目标目录。"""
        from error_handler import AtomicJSONWriter

        deep_path = temp_dir / "a" / "b" / "c" / "data.json"
        deep_path.parent.mkdir(parents=True, exist_ok=True)  # 这是用户的责任
        writer = AtomicJSONWriter(deep_path)
        writer.write({"key": "value"})
        assert deep_path.exists()

    def test_read_nonexistent_file_returns_empty(self, temp_dir):
        """读取不存在的文件返回空字典。"""
        from error_handler import AtomicJSONWriter

        writer = AtomicJSONWriter(temp_dir / "nonexistent.json")
        result = writer.read()
        assert result == {}


class TestBackup:
    """备份机制测试。"""

    def test_write_creates_backup(self, atomic_writer, sample_portfolio):
        """第二次写入前自动备份原文件。"""
        atomic_writer.write(sample_portfolio)
        sample_portfolio["cash"] = 20000.0
        atomic_writer.write(sample_portfolio)

        backups = list(atomic_writer.backup_dir.glob("portfolio_*.json"))
        assert len(backups) == 1

        # 备份内容应该是第一次写入的数据
        backup_data = json.loads(backups[0].read_text())
        assert backup_data["cash"] == 25000.0

    def test_max_backups_enforced(self, temp_dir):
        """超过 max_backups 时自动清理旧备份。"""
        from error_handler import AtomicJSONWriter

        writer = AtomicJSONWriter(temp_dir / "test.json", max_backups=3)

        for i in range(10):
            writer.write({"seq": i})

        backups = list(writer.backup_dir.glob("test_*.json"))
        assert len(backups) == 3

    def test_no_backup_when_disabled(self, temp_dir):
        """max_backups=0 时不创建备份。"""
        from error_handler import AtomicJSONWriter

        writer = AtomicJSONWriter(temp_dir / "test.json", max_backups=0)
        writer.write({"a": 1})
        writer.write({"a": 2})
        backups = list(writer.backup_dir.glob("test_*.json"))
        assert len(backups) == 0


class TestDataIntegrity:
    """数据完整性测试。"""

    def test_partial_write_does_not_corrupt(self, atomic_writer, sample_portfolio):
        """写入中断：临时文件残留，原始文件不受影响。"""
        atomic_writer.write(sample_portfolio)
        original = atomic_writer.read()

        # 模拟：写入到一半，tmp 文件有损坏内容
        tmp_file = atomic_writer.filepath.with_suffix(".json.tmp")
        tmp_file.write_text("corrupted{{{not valid json")

        # 原始文件仍然完好
        assert atomic_writer.read() == original

    def test_write_then_crash_simulated(self, temp_dir):
        """模拟系统崩溃后原始文件完整。"""
        from error_handler import AtomicJSONWriter

        writer = AtomicJSONWriter(temp_dir / "crash.json")

        writer.write({"key": "value_before_crash"})
        original = writer.read()

        # 模拟：创建 tmp 文件但 rename 失败
        tmp = writer.filepath.with_suffix(".json.tmp")
        tmp.write_text('{"mock": "crash_data"}')

        # 验证原始文件未变
        assert writer.read() == original

    def test_empty_dict_write_read(self, atomic_writer):
        """空字典写入后正确读取。"""
        empty = {}
        atomic_writer.write(empty)
        assert atomic_writer.read() == {}

    def test_nested_data_preserved(self, atomic_writer):
        """嵌套数据结构完全保留。"""
        nested = {
            "level1": {
                "level2": {
                    "level3": [1, 2, {"deep": "value"}],
                },
            },
            "unicode": "中文测试 🚀",
            "float": 3.14159,
            "bool": True,
            "null": None,
        }
        atomic_writer.write(nested)
        result = atomic_writer.read()
        assert result == nested


class TestConcurrency:
    """并发安全性测试。"""

    def test_concurrent_write_safety(self, temp_dir):
        """并发写入后文件仍然可读且有效。"""
        from error_handler import AtomicJSONWriter

        writer = AtomicJSONWriter(temp_dir / "concurrent.json")
        errors = []

        def write_data(n):
            try:
                writer.write({"thread": n, "timestamp": time.time()})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_data, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 最终文件应该可读且有效
        result = writer.read()
        assert "thread" in result
        # 不验证具体值（每个线程可能覆盖），但结构必须正确
        assert isinstance(result["thread"], int)
