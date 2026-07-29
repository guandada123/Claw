"""test_backup_batch.py — 批量覆盖 backup_data + heartbeat + mass_update。"""


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
    result = bd._find_project_dir("/Volumes/ZHITAI/WorkBuddy/Claw/scripts")
    assert "Claw" in result
    assert result is not None


def test_find_project_at_root_fallback():
    result = bd._find_project_dir("/")
    assert result == "/"
