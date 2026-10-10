#!/usr/bin/env python3
"""audit_locks.py — 锁文件依赖漏洞门禁（**只挡「可处置的」**）

━━ 为什么不用 `pip-audit -r` ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1) `pip-audit -r <req>` 会先**建一个 venv（ensurepip）**去解析依赖 —— 本机该路径
   直接失败（可移动卷 + 无 ensurepip，2026-10-10 实测），CI 上也是多余的脆弱点。
2) 同一条 CVE 常以 **GHSA-*** 与 **PYSEC-*** 两个 ID 同时返回（实测 requests 那例），
   不去重就会把 1 条漏洞报成 2 条 —— 噪音翻倍，正是「告警频率不匹配可处置性」。
故直接查 **OSV**（pip-audit 的默认同源数据），只读 uv.lock，**不建 venv、不跑 pip**。

━━ 判据（刻意如此，见门禁设计原则）━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  · **有修复版本**（OSV `affected[].ranges[].events[].fixed` 非空）→ **FAIL**
        —— 这件事你能做，就该做。
  · **无修复版本** → **WARN，不挡**
        —— 上游还没修，红 CI 也变不出补丁；只会训练人忽略红灯。
  · 命中 allowlist 且未过期 → 跳过；**已过期 → FAIL**（逼复审，防「永久豁免」）。
  · 查询失败（网络/API）→ **rc=2 FAIL**
        —— 判不了就说判不了，**不静默放过**。

━━ 用法 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  python3 audit_locks.py                      # 扫仓库内全部 uv.lock
  python3 audit_locks.py --json
  python3 audit_locks.py --allowlist <path>

退出码：0=通过（可含 WARN）/ 1=有可处置漏洞 / 2=无法判定
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import tomllib

OSV_BATCH = "https://api.osv.dev/v1/querybatch"
OSV_VULN = "https://api.osv.dev/v1/vulns/{}"
DEFAULT_ALLOWLIST = ".workbuddy/lock_audit_allowlist.json"
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".ruff_cache", ".mypy_cache"}


# ── 读取锁文件 ────────────────────────────────────────────────────


def find_locks(root: Path) -> list[Path]:
    out = []
    for p in root.rglob("uv.lock"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        out.append(p)
    return sorted(out)


def read_lock_packages(lock: Path) -> dict[str, str]:
    """从 uv.lock 取 {包名: 版本}（跳过本仓库自身的 editable 包）。"""
    data = tomllib.loads(lock.read_text(encoding="utf-8"))
    pkgs: dict[str, str] = {}
    for item in data.get("package") or []:
        name, ver = item.get("name"), item.get("version")
        if not name or not ver:
            continue
        src = item.get("source") or {}
        if "editable" in src or "virtual" in src:
            continue
        pkgs[str(name)] = str(ver)
    return pkgs


# ── OSV ───────────────────────────────────────────────────────────


def _urlopen_json(url: str, body: dict | None, timeout: int, attempts: int = 3) -> dict:
    """带**有界重试**的 JSON 请求。

    为什么要重试：单次网络抖动不该让 CI 变红（那会把门禁变成噪音源）；
    但重试**必须带判据** —— 只重试「可能自愈」的失败（连接错误/超时/5xx/429），
    其他 4xx（如 404 漏洞 ID 不存在）立刻抛出，重试也没用。
    重试耗尽后仍然抛出 → 上层转成 rc=2「判不了就说判不了」，不静默放过。
    """
    last: Exception | None = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(  # noqa: S310 — 固定 https 常量，非用户输入
                url,
                data=json.dumps(body).encode() if body is not None else None,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
                return json.load(r)
        except urllib.error.HTTPError as e:
            last = e
            if e.code < 500 and e.code != 429:
                raise  # 4xx（非 429）= 请求本身有问题，重试无意义
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
            last = e
        if i < attempts - 1:
            time.sleep(0.8 * (2**i))
    raise last if last else RuntimeError("未知失败")


def query_osv(packages: dict[str, str], timeout: int = 45) -> dict[str, list[str]]:
    """批量查询，返回 {包名: [vuln_id...]}。空 dict 表示全部干净。"""
    if not packages:
        return {}
    names = sorted(packages)
    body = {
        "queries": [
            {"package": {"name": n, "ecosystem": "PyPI"}, "version": packages[n]} for n in names
        ]
    }
    payload = _urlopen_json(OSV_BATCH, body, timeout)
    out: dict[str, list[str]] = {}
    for name, res in zip(names, payload.get("results") or []):
        ids = [v["id"] for v in (res.get("vulns") or []) if v.get("id")]
        if ids:
            out[name] = ids
    return out


def fetch_vuln(vid: str, timeout: int = 30) -> dict:
    return _urlopen_json(OSV_VULN.format(vid), None, timeout)


# ── 归并与判定（纯函数，便于离线测试）────────────────────────────


def group_vulns(ids: list[str], details: dict[str, dict]) -> list[dict]:
    """把「同一条 CVE 的多个 ID」归并成一组 —— 别名共享即并查集合并。

    实测：requests 的 GHSA-gc5v-m9x4-r6x2 与 PYSEC-2026-2275 共享别名
    CVE-2026-25645 → 必须合成 1 条，否则同一漏洞报两遍。
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for vid in ids:
        find(vid)
        for alias in details.get(vid, {}).get("aliases") or []:
            union(vid, alias)

    groups: dict[str, set[str]] = {}
    for vid in ids:
        groups.setdefault(find(vid), set()).add(vid)
    return [{"members": sorted(m), "canonical": sorted(m)[0]} for m in groups.values()]


def extract_fixes(vuln: dict, package: str) -> list[str]:
    """取该包在该漏洞下所有「已修复版本」。"""
    fixes: set[str] = set()
    for aff in vuln.get("affected") or []:
        pkg = (aff.get("package") or {}).get("name") or ""
        if pkg and pkg.lower() != package.lower():
            continue
        for rg in aff.get("ranges") or []:
            for ev in rg.get("events") or []:
                if ev.get("fixed"):
                    fixes.add(str(ev["fixed"]))
    return sorted(fixes)


def extract_severity(vuln: dict) -> str:
    """尽力取一个可读的严重度标签（GHSA 给 MODERATE/HIGH…，其余给 CVSS 向量）。"""
    ds = (vuln.get("database_specific") or {}).get("severity")
    if isinstance(ds, str) and ds:
        return ds.upper()
    sev = vuln.get("severity")
    if isinstance(sev, list) and sev:
        return str(sev[0].get("score") or "UNKNOWN")
    return "UNKNOWN"


def classify(group: dict, package: str, version: str, details: dict[str, dict]) -> dict:
    """决定这条发现是 blocking（有修复版本）还是 warn（无修复版本）。"""
    fixes: set[str] = set()
    severity = "UNKNOWN"
    summary = ""
    alias_set: set[str] = set(group["members"])
    for vid in group["members"]:
        v = details.get(vid) or {}
        fixes |= set(extract_fixes(v, package))
        if summary == "" and v.get("summary"):
            summary = str(v["summary"])
        if severity == "UNKNOWN":
            severity = extract_severity(v)
        alias_set.update(str(x) for x in (v.get("aliases") or []))
    # 展示用 ID 优先取 CVE 号。**必须连 aliases 一起看**：实测 GHSA 那条的 members
    # 是 GHSA-*，CVE 号只出现在 aliases 里 —— 只看 members 会退回显示 GHSA 号。
    ordered_ids = sorted(alias_set)
    return {
        "package": package,
        "version": version,
        "ids": group["members"],
        "display_id": next((i for i in ordered_ids if i.startswith("CVE-")), group["canonical"]),
        "aliases": sorted(alias_set),
        "severity": severity,
        "summary": summary[:160],
        "fix_versions": sorted(fixes),
        "actionable": bool(fixes),
    }


# ── allowlist ────────────────────────────────────────────────────


def load_allowlist(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 — 豁免表自身坏了必须被看见
        raise ValueError(f"allowlist 解析失败 {path}: {e}") from e
    return list(data.get("entries") or [])


def is_allowed(finding: dict, entries: list[dict], today: dt.date) -> tuple[bool, str]:
    """命中且未过期才豁免；过期返回 (False, 原因) 以逼复审。"""
    for e in entries:
        eid = str(e.get("id") or "")
        if not eid:
            continue
        if eid not in finding["ids"] and eid != finding["display_id"]:
            continue
        if e.get("package") and str(e["package"]).lower() != finding["package"].lower():
            continue
        exp = str(e.get("expires") or "")
        if not exp:
            return False, f"豁免条目 {eid} 缺少 expires（必须写到期日，防永久豁免）"
        try:
            exp_d = dt.date.fromisoformat(exp)
        except ValueError:
            return False, f"豁免条目 {eid} 的 expires 不是 ISO 日期: {exp}"
        if exp_d < today:
            return False, f"豁免已过期（{exp}），须复审后决定「升级」或「续期并写明理由」"
        return True, f"已豁免至 {exp}：{e.get('reason') or '(未写理由)'}"
    return False, ""


# ── 主流程 ───────────────────────────────────────────────────────


def audit(root: Path, allowlist_path: Path, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    locks = find_locks(root)
    report: dict = {
        "locks": [str(p.relative_to(root)) for p in locks],
        "blocking": [],
        "warnings": [],
        "allowed": [],
        "errors": [],
    }
    if not locks:
        report["errors"].append("未找到任何 uv.lock")
        return report

    details_cache: dict[str, dict] = {}
    for lock in locks:
        rel = str(lock.relative_to(root))
        try:
            packages = read_lock_packages(lock)
        except Exception as e:  # noqa: BLE001
            report["errors"].append(f"{rel}: 解析失败 {e.__class__.__name__}: {e}")
            continue
        try:
            hits = query_osv(packages)
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
            report["errors"].append(f"{rel}: OSV 查询失败 {e.__class__.__name__}: {e}")
            continue

        for pkg, ids in hits.items():
            missing = [i for i in ids if i not in details_cache]
            for vid in missing:
                try:
                    details_cache[vid] = fetch_vuln(vid)
                except Exception as e:  # noqa: BLE001
                    report["errors"].append(f"{rel}: 取 {vid} 详情失败 {e.__class__.__name__}")
        if report["errors"]:
            continue

        for pkg, ids in hits.items():
            known = [i for i in ids if i in details_cache]
            for group in group_vulns(known, details_cache):
                f = classify(group, pkg, packages[pkg], details_cache)
                f["lock"] = rel
                ok, why = is_allowed(f, load_allowlist(allowlist_path), today)
                if ok:
                    report["allowed"].append({**f, "allow_reason": why})
                elif f["actionable"]:
                    report["blocking"].append({**f, "note": why})
                else:
                    report["warnings"].append(f)
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".", help="仓库根（默认当前目录）")
    ap.add_argument("--allowlist", default=DEFAULT_ALLOWLIST)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    try:
        report = audit(root, (root / args.allowlist).resolve())
    except ValueError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"扫描锁文件: {', '.join(report['locks']) or '(无)'}")
        for a in report["allowed"]:
            print(
                f"  ⚪ 已豁免 {a['display_id']} {a['package']}@{a['version']} — {a['allow_reason']}"
            )
        for w in report["warnings"]:
            print(
                f"  ⚠️  {w['display_id']} {w['package']}@{w['version']}（{w['severity']}）"
                f" **无修复版本** → 只提示不挡: {w['summary']}"
            )
        for b in report["blocking"]:
            print(
                f"  🔴 {b['display_id']} {b['package']}@{b['version']}（{b['severity']}）"
                f" → 升级到 {b['fix_versions']} | {b['summary']}"
            )
            if b.get("note"):
                print(f"       ↳ {b['note']}")
        if report["errors"]:
            for e in report["errors"]:
                print(f"  🔴 {e}", file=sys.stderr)
            print(
                "\n[ERROR] 无法完成判定 —— 按设计**不静默放过**（rc=2）。"
                "网络抖动可重跑；持续失败需查 OSV 可用性。",
                file=sys.stderr,
            )
            return 2
        print(
            f"\n合计: 可处置(挡) {len(report['blocking'])} | 不可处置(提示) {len(report['warnings'])} "
            f"| 已豁免 {len(report['allowed'])}"
        )
        if not report["blocking"]:
            print("✅ 无可处置漏洞")

    if report["errors"]:
        return 2
    return 1 if report["blocking"] else 0


if __name__ == "__main__":
    sys.exit(main())
