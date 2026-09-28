"""test_backup_batch.py — 批量覆盖 backup_data + heartbeat + mass_update。"""

from pathlib import Path

import backup_data as bd
import mass_update_automations as mua


# ── mass_update_automations ──
def test_changes_all_have_fields():
    for aid, c in mua.CHANGES.items():
        assert "from" in c and "to" in c and "reason" in c


def test_changes_count():
    assert len(mua.CHANGES) > 5


# ── backup_data ──
def test_find_project_from_scripts_dir():
    # 相对本文件定位仓库根（不写死本机绝对路径，CI runner 上没有 /Volumes、/Users）
    root = Path(__file__).resolve().parent.parent
    result = bd._find_project_dir(str(root / "scripts"))
    assert Path(result).resolve() == root


def test_find_project_at_root_fallback():
    result = bd._find_project_dir("/")
    assert result == "/"
