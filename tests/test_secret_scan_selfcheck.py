"""test_secret_scan_selfcheck.py — 防「检查器被自己的模式串打红」（自指假阳性）

## 为什么需要它（2026-10-11 实踩）
Claw CI 的 Secret scan 是**朴素 grep**：
    grep -rn "<PEM 私钥标记>" .workbuddy/scripts/ .workbuddy/lib/
它不区分「真密钥」与「检测用的模式字符串」。当天新增的历史密钥扫描器
`scan_git_history_secrets.py` 里恰好写了一个完整的 PEM 私钥头**字面量**作为检测模式
→ CI 把「检测器自己的模式串」当成密钥 → **Claw CI 的 Security Scan 判红**。

处置：把该字面量**拆开拼接**（`"PRIVATE" + " KEY-----"`），grep 不再命中、检测能力不变。
本文件把这件事钉住：
  ① 那两个目录下不得出现完整的 PEM 标记字面量（含注释、文档字符串——grep 不看上下文）；
  ② 扫描器的检测能力必须仍然有效（防"为了让门禁变绿而删掉检测"）。

⚠️ 本文件自己也不能出现完整字面量 —— 故所有标记都用拼接构造。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCANNED_DIRS = [REPO / ".workbuddy" / "scripts", REPO / ".workbuddy" / "lib"]
SCANNER = REPO / ".workbuddy" / "scripts" / "scan_git_history_secrets.py"

# 与 CI 那条 grep 等价的标记（拼接构造，避免本文件自己命中）
MARKERS = ["PRIVATE" + " KEY", "-----BEGIN " + "RSA", "-----BEGIN " + "OPENSSH"]


def _text_files():
    for d in SCANNED_DIRS:
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*")):
            if p.is_file() and p.suffix.lower() in {".py", ".sh", ".md", ".txt", ".json"}:
                yield p


def test_ci_secret_markers_not_present_in_scripts():
    """CI 的朴素 grep 覆盖的目录里，不得出现完整 PEM 标记（否则它自己会红）。"""
    offenders = []
    for p in _text_files():
        txt = p.read_text(encoding="utf-8", errors="ignore")
        hit = [m for m in MARKERS if m in txt]
        if hit:
            offenders.append(f"{p.relative_to(REPO)}（命中 {hit}）")
    assert not offenders, (
        "以下文件含有 CI Secret scan 会误报的 PEM 标记字面量；"
        "请像 scan_git_history_secrets.py 那样**拆开拼接**，或把该文件排除在检查范围外：\n  - "
        + "\n  - ".join(offenders)
    )


@pytest.mark.parametrize(
    "sample,expect",
    [
        ("-----BEGIN X " + "PRIVATE" + " KEY-----\nMIIabc", "private_key"),
        ("ak_" + "a" * 32, "wind_token"),
        ("sk-" + "b" * 32, "openai_like"),
        ("AKIA" + "C" * 16, "aws_akid"),
    ],
)
def test_scanner_patterns_still_detect(sample, expect):
    """扫描器的检测能力必须还在（防「为了让 CI 变绿而删检测」）。"""
    if not SCANNER.is_file():
        pytest.skip("scan_git_history_secrets.py 不存在（可能已改名/删除）")
    spec = importlib.util.spec_from_file_location("scan_git_history_secrets", SCANNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    names = [name for name, rx in mod.PATTERNS if rx.search(sample)]
    assert expect in names, f"模式 {expect} 未能命中合成样本（命中={names}）"
