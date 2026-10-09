"""scan_repo_credentials 的守卫测试。

核心回归点（2026-10-09 实证的失效模式）：
凭据被 `git rm --cached` 移出索引后，`git ls-files` 已经看不到它了 ——
只看索引的检查会报「干净」，而它仍在对象库/历史里，clone/push 时照样泄漏。
本测试专门构造这个状态，断言 L2（对象库）能抓到。

全部离线：临时目录里现造 git 仓库，不碰任何真实仓库。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / ".workbuddy" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import scan_repo_credentials as src  # noqa: E402


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", ".")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")


def _commit(repo: Path, msg: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg)


def _layers(report: dict) -> set[tuple[str, str]]:
    return {(f["layer"], f["path"]) for f in report["findings"]}


def test_clean_repo_has_no_findings(tmp_path):
    repo = tmp_path / "clean"
    _init(repo)
    (repo / "README.md").write_text("# hi\n", encoding="utf-8")
    (repo / ".env.example").write_text("KEY=\n", encoding="utf-8")
    _commit(repo, "init")
    assert src.scan(repo)["findings"] == []


def test_template_and_doc_suffixes_are_not_flagged(tmp_path):
    """模板/文档不该误报（`.env.example`、讨论 token 的 .md）。"""
    repo = tmp_path / "tpl"
    _init(repo)
    for name in (".env.example", ".env.template", "notes-about-token.md", "api-keys.txt"):
        (repo / name).write_text("placeholder\n", encoding="utf-8")
    _commit(repo, "init")
    assert src.scan(repo)["findings"] == []


def test_tracked_env_is_flagged_at_l1(tmp_path):
    repo = tmp_path / "dirty"
    _init(repo)
    (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")
    _commit(repo, "init")
    assert ("L1-index", ".env") in _layers(src.scan(repo))


def test_token_removed_from_index_is_still_caught_in_history(tmp_path):
    """核心回归点：移出索引 ≠ 干净。

    构造与 2026-10-09 真实事故一致的两步：
      ① 先把凭据提交进去（既有事实）
      ② 补 .gitignore + git rm --cached 把它移出索引（表面已修）
    此时 `git ls-files` 看不到它了，但对象库里仍在 —— 只有 L2 能抓到。
    """
    repo = tmp_path / "history"
    _init(repo)
    (repo / ".neodata_token").write_text('{"token": "x"}\n', encoding="utf-8")
    (repo / "README.md").write_text("# hi\n", encoding="utf-8")
    _commit(repo, "init")

    (repo / ".gitignore").write_text(".neodata_token\n", encoding="utf-8")
    _git(repo, "rm", "-q", "--cached", ".neodata_token")
    _commit(repo, "ignore + rm --cached")

    report = src.scan(repo)
    layers = _layers(report)
    assert ("L1-index", ".neodata_token") not in layers, "索引里确实没了"
    assert ("L2-objectdb", ".neodata_token") in layers, "历史里必须被抓到"


def test_gitignore_coverage_is_reported(tmp_path):
    repo = tmp_path / "cov"
    _init(repo)
    (repo / ".gitignore").write_text(".neodata_token\n*.pem\n", encoding="utf-8")
    _commit(repo, "init")
    covered = src.scan(repo)["ignored_patterns"]
    assert ".neodata_token" in covered and "*.pem" in covered


def test_non_git_path_is_reported_not_raised(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    report = src.scan(plain)
    assert report["ok"] is False
    assert "不是 git 仓库" in report["error"]


def test_main_returns_1_on_findings_and_2_on_missing_path(tmp_path, monkeypatch, capsys):
    dirty = tmp_path / "dirty2"
    _init(dirty)
    (dirty / ".env").write_text("SECRET=1\n", encoding="utf-8")
    _commit(dirty, "init")

    monkeypatch.setattr(sys, "argv", ["scan_repo_credentials", str(dirty)])
    assert src.main() == 1

    monkeypatch.setattr(sys, "argv", ["scan_repo_credentials", str(tmp_path / "nope")])
    assert src.main() == 2
    assert "路径不存在" in capsys.readouterr().err
