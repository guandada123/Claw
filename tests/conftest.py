import sys
from pathlib import Path

# 让 tests/ 能直接 `import` scripts/ 下的模块
# （scripts/ 非包，无 __init__.py，故将其加入 sys.path）
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

# 共享 helper（qts_client / is_trading_day 等）单源在 .workbuddy/scripts/：
# 2026-09-23 双副本去重后 scripts/ 副本已退役，须一并加入 sys.path 否则 import 失败。
_HELPER_SCRIPTS = Path(__file__).resolve().parent.parent / ".workbuddy" / "scripts"
if str(_HELPER_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_HELPER_SCRIPTS))
