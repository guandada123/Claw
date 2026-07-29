import sys
from pathlib import Path

# 让 tests/ 能直接 `import` scripts/ 下的模块
# （scripts/ 非包，无 __init__.py，故将其加入 sys.path）
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
