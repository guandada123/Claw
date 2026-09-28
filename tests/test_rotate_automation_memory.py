"""滚动归档的「不丢内容」回归测试（2026-09-28 建立）。

这类脚本最危险的失效不是"没生效"，而是**悄悄吃掉历史** —— 所以核心断言是
**行数守恒**：归档文件 + 保留文件（去掉标记行/表头）= 原始文件，逐行可比。
另外覆盖两种真实结构：块结构（`## 时间戳` + `- 正文`）与行日志（每行一个时间戳）。
"""
import datetime
import importlib.util
import pathlib

import pytest

SC = pathlib.Path("/Users/guan/WorkBuddy/Claw/.workbuddy/scripts/rotate_automation_memory.py")
spec = importlib.util.spec_from_file_location("rot", SC)
rot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rot)

CUTOFF = datetime.date(2026, 9, 14)


def _payload(p: pathlib.Path) -> list[str]:
    """去掉归档表头与保留文件里的标记注释后的纯内容行。"""
    return [ln for ln in p.read_text(encoding="utf-8").splitlines() if not ln.startswith("<!--")]


def _blob(p: pathlib.Path) -> str:
    """内容行拼成的文本（做子串断言用 —— 注意 `in <list>` 是元素相等，不是子串）。"""
    return "\n".join(_payload(p))


def test_block_structure_no_loss(tmp_path):
    """块结构：归档的必须是**整块**（标题+正文一起走），且行数守恒。"""
    src = tmp_path / "memory.md"
    src.write_text(
        "# 助理实盘监控 memory\n\n"
        "## 2026-09-10T09:07 [ALERT] 旧块\n- 正文 A\n- 正文 B\n\n"
        "## 2026-09-28T09:07 [ALERT] 新块\n- 正文 C\n- 正文 D\n",
        encoding="utf-8")
    original = src.read_text(encoding="utf-8").splitlines()
    r = rot.rotate_file(src, CUTOFF, dry_run=False)

    assert r["skipped"] is None and r["archived_lines"] == 4   # 标题+2 正文+空行
    kept, arch = _blob(src), _blob(pathlib.Path(r["archive"]))
    assert "正文 A" in arch and "正文 A" not in kept            # 整块搬走，不劈半
    assert "正文 C" in kept and "正文 C" not in arch
    assert "2026-09-10T09:07" in arch and "2026-09-28T09:07" in kept
    assert len(_payload(src)) + len(_payload(pathlib.Path(r["archive"]))) == len(original)
    assert _payload(src)[0] == "# 助理实盘监控 memory"           # 抬头永久保留


def test_line_log_structure_no_loss(tmp_path):
    """行日志：每行自成一块，行为与按行归档一致。"""
    src = tmp_path / "memory.md"
    body = "".join(f"2026-09-{d:02d}T03:00 [SILENT] 第 {d} 条\n" for d in (10, 11, 12, 20, 28))
    src.write_text(body, encoding="utf-8")
    r = rot.rotate_file(src, CUTOFF, dry_run=False)
    kept, arch = _blob(src), _blob(pathlib.Path(r["archive"]))
    for d in (10, 11, 12):
        assert f"第 {d} 条" in arch and f"第 {d} 条" not in kept
    for d in (20, 28):
        assert f"第 {d} 条" in kept and f"第 {d} 条" not in arch
    assert len(_payload(src)) + len(_payload(pathlib.Path(r["archive"]))) == 5


def test_nothing_older_is_noop(tmp_path):
    src = tmp_path / "memory.md"
    src.write_text("2026-09-28T03:00 只有新条目\n", encoding="utf-8")
    r = rot.rotate_file(src, CUTOFF, dry_run=False)
    assert r["skipped"] == "nothing_older_than_cutoff" and r["archived_lines"] == 0
    assert src.read_text(encoding="utf-8") == "2026-09-28T03:00 只有新条目\n"


def test_no_timestamps_is_skipped_not_trimmed(tmp_path):
    """解析不出时间戳 → **跳过**（宁可不动，不许按行数硬砍）。"""
    src = tmp_path / "memory.md"
    src.write_text("没有时间戳的正文\n" * 20, encoding="utf-8")
    before = src.read_text(encoding="utf-8")
    r = rot.rotate_file(src, CUTOFF, dry_run=False)
    assert r["skipped"] == "no_timestamps"
    assert src.read_text(encoding="utf-8") == before


def test_dry_run_changes_nothing(tmp_path):
    src = tmp_path / "memory.md"
    src.write_text("2026-09-10T03:00 旧\n2026-09-28T03:00 新\n", encoding="utf-8")
    before = src.read_text(encoding="utf-8")
    r = rot.rotate_file(src, CUTOFF, dry_run=True)
    assert r["archived_lines"] == 1
    assert src.read_text(encoding="utf-8") == before
    assert not pathlib.Path(r["archive"]).exists()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "--no-header", "-p", "no:cacheprovider"]))
