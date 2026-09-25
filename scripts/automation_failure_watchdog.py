# AUTO-GENERATED FORWARDER — 单源薄壳 (safe_dedup.py, rule: dual-copy-audit v1.1.0)
# 真实实现: .workbuddy/scripts/automation_failure_watchdog.py  (运行时向上搜索定位, 位置无关)
import importlib.util
import runpy
import sys
from pathlib import Path


def _find_real():
    here = Path(__file__).resolve().parent
    for cand in [here, *here.parents]:
        for rel in (Path(".workbuddy") / "scripts" / "automation_failure_watchdog.py",
                    Path("src") / "claw" / "automation_failure_watchdog.py"):
            p = cand / rel
            if p.is_file():
                return p
    raise RuntimeError("forwarder[automation_failure_watchdog]: 找不到权威副本, 已从 dual-copy-audit v1.1.0 中断链?")


_real = _find_real()
_auth_dir = str(_real.parent)
if _auth_dir not in sys.path:
    sys.path.insert(0, _auth_dir)   # 让真实模块的同级 import 可解析 (等同 CLI 从权威目录运行)
_spec = importlib.util.spec_from_file_location("automation_failure_watchdog", str(_real))
_mod = importlib.util.module_from_spec(_spec)
sys.modules["automation_failure_watchdog"] = _mod          # 让 `import automation_failure_watchdog` 直接拿到真实模块
_spec.loader.exec_module(_mod)

if __name__ == "__main__":
    runpy.run_path(str(_real), run_name="__main__")  # 兼容 `python scripts/automation_failure_watchdog.py`
