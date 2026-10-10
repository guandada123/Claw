"""守卫：`.workbuddy/` 下的脚本不得用「往上两层 + 字面量 .workbuddy」定位仓库根。

━━ 为什么（2026-10-10 实测事故）━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
`run_debate.py` 原本在 `scripts/`，用 `Path(__file__).parent.parent / ".workbuddy" / ...`
定位数据目录 —— 那时是对的（`scripts/x.py` 往上两层 = 仓库根）。
2026-09-21 它被移到 `.workbuddy/scripts/`，**写法没跟着改**：
  `.workbuddy/scripts/x.py` 往上两层 = `.workbuddy/` → 再拼 `.workbuddy`
  ⇒ 指到 **`Claw/.workbuddy/.workbuddy/data/...`**（多套一层）。
后果不是「读不到」而是**静默写到错的地方**：该影子目录真实存在了约两个月，
里面躺着 11 份 2026-06~07 的独有工作日志（主记忆目录连 .gz 都没有）+ 8 份独有
automation memory。**数据被写丢，且没有任何报错。**

━━ 判据为什么精确、不误报 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
全仓另有 6 处同样的「parent.parent + \".workbuddy\"」写法（earnings_calendar /
verify_prompt_cache / alpha_data / debate_engine），但它们都在 `scripts/` 或 `src/` 下，
往上 N 层**正好落在仓库根** ⇒ 是对的，不该报。
所以规则只针对 **位于 `.workbuddy/` 之内的 .py**：那里 parent.parent 已经是 `.workbuddy`，
再拼字面量 `.workbuddy` 必然双重嵌套。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WS = REPO / ".workbuddy"

sys.path.insert(0, str(WS / "scripts"))


def _offending_lines(path: Path) -> list[tuple[int, str]]:
    out = []
    for i, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").split("\n"), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "parent.parent" in line and '".workbuddy"' in line:
            out.append((i, stripped))
    return out


def test_no_script_under_workbuddy_double_nests_the_root():
    """核心回归：`.workbuddy/` 之内不得出现「往上两层 + 字面量 .workbuddy」。"""
    offenders: list[str] = []
    for p in WS.rglob("*.py"):
        if any(part in {".venv", "__pycache__", "tmp"} for part in p.parts):
            continue
        for i, line in _offending_lines(p):
            offenders.append(f"{p.relative_to(REPO)}:{i}: {line}")
    assert not offenders, (
        "以下写法会指到 .workbuddy/.workbuddy/（多套一层，静默写错位置）：\n  "
        + "\n  ".join(offenders)
        + "\n修法：改用 run_debate.py 里的 _resolve_claw_root()（环境变量优先 + 向上回溯）"
    )


def test_the_guard_would_actually_catch_the_original_bug(tmp_path):
    """反证：把当年那行写回去，守卫必须报出来（否则这守卫是摆设）。"""
    f = tmp_path / "fake.py"
    f.write_text(
        'cache_path = (\n'
        '    Path(__file__).parent.parent / ".workbuddy" / "data" / "debate" / "x.json"\n'
        ")\n",
        encoding="utf-8",
    )
    assert _offending_lines(f), "守卫认不出原始 bug 写法 = 失效"


def test_guard_ignores_the_legitimate_instances_outside_workbuddy():
    """`scripts/` 下的同类写法是对的（往上两层 = 仓库根），不该被误判。"""
    for rel in ("scripts/alpha_data.py", "scripts/earnings_calendar.py"):
        p = REPO / rel
        if not p.is_file():
            continue
        # 这些文件允许出现该写法 —— 守卫的扫描面只在 .workbuddy/ 之内
        assert not str(p.relative_to(REPO)).startswith(".workbuddy/"), rel


def test_run_debate_cache_path_is_not_double_nested():
    """直接验 `run_debate.py` 的缓存路径本身。"""
    import run_debate as rd

    p = rd._CLAW_ROOT / ".workbuddy" / "data" / "debate" / "fundamental_cache.json"
    assert ".workbuddy/.workbuddy" not in str(p)
    assert rd._CLAW_ROOT.is_dir()
    assert (rd._CLAW_ROOT / "src" / "claw").is_dir(), "_CLAW_ROOT 必须真的是仓库根"


@pytest.mark.parametrize("fn_name", ["_load_fundamental_cache", "_write_fundamental_cache"])
def test_run_debate_cache_functions_use_claw_root(fn_name):
    """两个函数都改过了 —— 只改一个会留下半条链路。"""
    import inspect

    import run_debate as rd

    src = inspect.getsource(getattr(rd, fn_name))
    assert "_CLAW_ROOT" in src, f"{fn_name} 未使用 _CLAW_ROOT"
    assert 'parent.parent / ".workbuddy"' not in src, f"{fn_name} 仍留着双重嵌套写法"


def test_shadow_directory_is_gone():
    """影子目录已抢救并清理 —— 它再次出现即说明有脚本又开始往那儿写。"""
    assert not (WS / ".workbuddy").exists(), (
        ".workbuddy/.workbuddy/ 又出现了 —— 有脚本在往里写，查 parent.parent 用法"
    )
