"""test_silent_stubs.py — 防「被调用却什么都不做」的桩函数门禁（结构性）。

## 为什么需要它（2026-10-10/11 实踩两次，跨两个项目）
QTS `execution-service/services/advanced_risk_rules.py` 的 `_check_new_high_after_sell`：
docstring + 一行 `# TODO: 接入行情数据后实现`，而调用方 `self._check_new_high_after_sell(...)`
**把返回值丢掉了** → 配置项 `sell_cooldown_new_high_days` **从未生效**，
但通读代码完全看不出（发现回路为它**报了两遍**才发现）。

这类缺陷比崩溃危险得多：**崩溃会响，它不会** —— 功能"在跑"，只是什么都不做。

## 判据（与 `silent_stub_scan.py` 一致）
函数体（去 docstring 后）为空 / 只剩 `pass` / 只剩 `...` / 只剩一行裸 `# TODO`
→ 记为 trivial；其中 **被调用 + 体是裸注释** 属最高危（🔴），本门禁**只拦 🔴**。

为什么只拦 🔴：🟠/🟡（被调用但体为空、或无人调用）有大量**合法**形态 ——
空对象 `NoneCache.close()`、模板方法基类的 `raise NotImplementedError`、
迁移脚本的空 `downgrade()`。全拦必然误报，然后被人用 `# noqa` 整体关掉。
需要有意保留的桩，就在函数块里写 `# stub-scan: ok` 显式豁免。

## 本门禁自身被验证过
拿**修复前**的 `advanced_risk_rules.py`（`git show 4aef3b75~1:…`）回归：
扫描器正确报出 🔴 `_check_new_high_after_sell` @505。回归中还当场抓出扫描器自己的两个 bug
（`self.xxx(` 形式的调用漏计 → 高危级降级；尾随注释不在 AST 的 `end_lineno` 内 →
`bare_comment` 误判成 `empty`），均已修 + 在 `tests/test_silent_stub_scan.py` 钉住。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCANNER = REPO / ".workbuddy" / "scripts" / "silent_stub_scan.py"

_spec = importlib.util.spec_from_file_location("silent_stub_scan", SCANNER)
ss = importlib.util.module_from_spec(_spec)
sys.modules["silent_stub_scan"] = ss
_spec.loader.exec_module(ss)


def test_no_silent_stubs_in_repo():
    """仓库内不得存在「被调用 + 体是裸注释」的桩（🟠/🟡 只记录、不拦）。"""
    found = ss.scan_repo(REPO)
    reds = [i for i in found if i["severity"] == "🔴" and not i["is_test"]]
    detail = "\n".join(
        f"  {Path(i['file']).relative_to(REPO)}:{i['line']}  {i['name']}()  calls={i['calls']}"
        for i in reds
    )
    assert not reds, (
        "发现「被调用 + 体是裸注释」的桩函数（在跑却什么都不做）：\n"
        f"{detail}\n"
        "处置：① 实现它；或 ② 若是有意为之，在函数块里写 `# stub-scan: ok` 显式豁免并说明理由。"
    )


def test_scanner_rc_semantics(tmp_path):
    """rc 语义必须与"判不了"区分：路径不存在 → 2（**不许当成"扫完没事"**）。"""
    assert ss.main(["x", "--root", str(tmp_path / "nope")]) == 2


def test_scanner_reports_known_defect_shape(tmp_path):
    """自证：把已知缺陷形态喂进去必须报 🔴 —— 否则本门禁是「看着在跑其实没查」。"""
    (tmp_path / "svc.py").write_text(
        "class C:\n"
        "    def _stub(self):\n"
        '        """doc"""\n'
        "        # TODO: 以后实现\n"
        "\n"
        "    def run(self):\n"
        "        self._stub()\n",
        encoding="utf-8",
    )
    reds = [i for i in ss.scan_repo(tmp_path) if i["severity"] == "🔴"]
    assert len(reds) == 1 and reds[0]["name"] == "_stub"
