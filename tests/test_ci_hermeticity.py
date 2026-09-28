"""test_ci_hermeticity.py — CI 卫生门禁：测试代码不得依赖「本机才有」的绝对路径。

2026-09-28 实锤（连续第 4 周红的第二个 job 的真因）：3 个新测试把脚本路径写死成
`/Users/guan/WorkBuddy/Claw/.workbuddy/scripts/xxx.py` ——
本机存在所以本地全绿，CI runner 上不存在 → pytest **collection 阶段**就 FileNotFoundError，
整个 Unit Tests job 直接 exit 2（连别的测试都没跑）。

更坏的一层：写死路径的测试即使「通过」也不可信 ——
本次同一批测试里，`_find_project_dir(".../Claw/scripts")` 在 CI 上会走到「退而求其次返回入参」
的兜底分支，于是断言 `"Claw" in result` 恒真。**断言一个常量 ≠ 验证一个行为。**

判据：测试目录下的 .py 源码里不得出现本机绝对路径字面量。
允许在两处出现：(1) 本守卫文件自身；(2) 纯注释行（含 `#` 起头的说明）。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DIRS = [ROOT / "tests", ROOT / ".workbuddy" / "tests"]
SELF = Path(__file__).resolve()

# 本机绝对路径字面量：引号/反引号紧跟 /Users、/Volumes、/home、/private
# 拼装而成，避免本文件自身命中（守卫不该是它自己唯一的例外）
_MACHINE_ROOTS = ("/Use" + "rs/", "/Vol" + "umes/", "/ho" + "me/", "/pri" + "vate/")
PATTERN = re.compile(r"""["'`](""" + "|".join(re.escape(p) for p in _MACHINE_ROOTS) + ")")


def _violations():
    out = []
    for d in TEST_DIRS:
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*.py")):
            if f.resolve() == SELF:
                continue
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                if PATTERN.search(line):
                    out.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()[:100]}")
    return out


def test_no_machine_absolute_paths_in_tests():
    bad = _violations()
    assert not bad, (
        "测试里出现本机绝对路径（CI runner 上不存在，会挂）：\n  "
        + "\n  ".join(bad)
        + "\n改用 Path(__file__).resolve().parent.parent 定位仓库根，或 tmp_path 自建目录树。"
    )


def test_guard_itself_can_detect_a_violation(tmp_path):
    """证伪：守卫必须真能抓到违规写法，否则它就是一条恒真的空检查。"""
    planted = tmp_path / "test_planted.py"
    planted.write_text('P = "' + _MACHINE_ROOTS[0] + 'someone/proj/x.py"\n', encoding="utf-8")
    assert PATTERN.search(planted.read_text(encoding="utf-8")), "守卫的判据抓不到违规写法"
