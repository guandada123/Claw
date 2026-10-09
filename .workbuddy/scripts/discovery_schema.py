#!/usr/bin/env python3
"""discovery_schema.py — 统一巡检中枢 v1.10「发现回路」共享口径与工具

用途：把「发现回路」的三个新契约集中到**一处单一真源**，避免各脚本各写一份枚举：
  1) 双轨（track）与项目锚点（target_project）的**允许值** —— 防「新增一个项目名，五个脚本各判一次」
  2) 收益度量（success_metric / impact）的**字段默认值与合法态**
  3) 去重键（dedup_key）的**唯一算法** —— 防「不同脚本算出不同 key，去重形同虚设」

铁律：
  - 纯确定性、零网络、零 LLM（发现回路的判断交给自动化里的 agent，本模块只提供口径与工具）
  - 只提供**函数**，不在 import 期做任何 I/O
  - 不写死本机绝对路径：CLAW 由调用方脚本的 __file__ 推导，项目根由 registry 数据（discovery_policy）提供
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------- 枚举（单一真源）
# 真实项目锚点：发现必须能落到这些项目，否则「优化真实项目」无法兑现
REAL_PROJECTS: tuple[str, ...] = ("Claw", "QTS", "StockInsight")
# meta = 优化对象是助手自己的技能库（元层）；none = 没锚点（等价于「不知道优化什么」→ 不准落地）
META_PROJECT = "meta"
NONE_PROJECT = "none"
TARGET_PROJECTS: tuple[str, ...] = REAL_PROJECTS + (META_PROJECT, NONE_PROJECT)

# 双轨：外部趋势 / 项目自身痛点信号 / 技能卫生
TRACKS: tuple[str, ...] = ("external_trend", "project_signal", "hygiene")

# 技术雷达四环（周度排序用；发现节奏 ≠ 采纳节奏）
RINGS: tuple[str, ...] = ("Adopt", "Trial", "Assess", "Caution")

# 强度/风险闸口径（与 apply_doc_candidates.quality_gate 默认值保持一致）
VALUE_HIGH: tuple[str, ...] = ("high", "medium-high")
RISK_OK: tuple[str, ...] = ("low",)

# 收益度量结论
IMPACTS: tuple[str, ...] = ("achieved", "partial", "none", "unmeasured")

# 候选的 v1.10 新字段默认值（迁移脚本与新候选共用，保证形状一致）
CANDIDATE_V110_DEFAULTS: dict = {
    "track": "external_trend",
    "target_project": NONE_PROJECT,
    "success_metric": None,        # 机器可判定/可复核的指标描述；空 = 未证真
    "check_cmd": None,             # 可选：退出码 0 = achieved 的确定性命令（等价于 CI 门禁）
    "metric_baseline": None,       # 落地时的基线取值（供「相对基线提升了吗」对照，而非「看起来更好了吗」）
    "recheck_after_days": 7,       # 落地后 N 天回查
    "landed_at": None,
    "impact": None,                # null（未到期/未查）| achieved | partial | none | unmeasured
    "impact_checked_at": None,
    "impact_note": None,
    "ring": None,                  # Adopt | Trial | Assess | Caution（周度排序写入）
    "ring_prev": None,
}

# 顶层新键（doc_contract 的 table_keys_expected 会校验 SKILL.md 必须登记，勿私增）
POLICY_KEY = "discovery_policy"
RADAR_KEY = "evolution_radar"
SCOREBOARD_KEY = "value_scoreboard"

# 去重索引落盘位置（相对 CLAW）
DEDUP_INDEX_REL = ".workbuddy/inspection_hub/discovery_archive/dedup_index.json"


# ---------------------------------------------------------------- 时间
def today8() -> str:
    return datetime.datetime.now().strftime("%Y%m%d")


def now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def today_iso() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d")


def days_between(d_from: str | None, d_to: str | None = None) -> int | None:
    """两个 YYYY-MM-DD 之间的天数；任一不可解析返回 None（不猜）。"""
    try:
        a = datetime.date.fromisoformat(str(d_from)[:10])
        b = datetime.date.fromisoformat(str(d_to)[:10]) if d_to else datetime.date.today()
        return (b - a).days
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- 路径
def expand(p: str) -> Path:
    """展开 ~ 与环境变量。项目根来自 registry 数据，避免脚本里写死本机绝对路径。"""
    return Path(os.path.expandvars(os.path.expanduser(str(p))))


def claw_root(script_file: str) -> Path:
    """由脚本自身位置推导 Claw 根（.workbuddy/scripts/x.py -> Claw）。"""
    return Path(script_file).resolve().parents[2]


# ---------------------------------------------------------------- registry I/O
def load_registry(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        return {"_error": f"registry 读取失败: {e}"}


def backup_registry(path: Path, tag: str) -> Path | None:
    """写前必备份：copy → 校验（可解析）→ 保留。绝不 move/删源。"""
    try:
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        dst = path.with_name(f"{path.name}.bak.{tag}-{ts}")
        shutil.copy2(path, dst)
        json.loads(dst.read_text(encoding="utf-8"))  # 备份可解析才算成功
        return dst
    except Exception as e:  # noqa: BLE001
        print(f"[schema] warn: registry 备份失败: {e}", file=sys.stderr)
        return None


def save_registry_atomic(path: Path, reg: dict) -> bool:
    """原子写：先写 .tmp 再 os.replace。写后回读校验（日志成功 ≠ 数据落库）。"""
    try:
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(reg, f, ensure_ascii=False, indent=2)
        json.loads(Path(tmp).read_text(encoding="utf-8"))  # 回读：能解析才落盘
        os.replace(tmp, path)
        json.loads(path.read_text(encoding="utf-8"))  # 落盘后二次回读
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[schema] warn: registry 回写失败: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------- 候选口径
def is_real_project(target: str | None) -> bool:
    return (target or "") in REAL_PROJECTS


def has_metric(c: dict) -> bool:
    return bool(str(c.get("success_metric") or "").strip()) or bool(str(c.get("check_cmd") or "").strip())


def apply_defaults(c: dict) -> dict:
    """补齐 v1.10 字段（不改已有值）——新候选与迁移共用，保证形状一致。"""
    for k, v in CANDIDATE_V110_DEFAULTS.items():
        c.setdefault(k, v)
    return c


_URL_PATH_RE = re.compile(r"^[a-z]+://")


def _norm_url(url: str | None) -> str:
    """把 URL 归一到可去重的形态：去掉协议差异与 query/fragment，仅留 host+path。"""
    u = str(url or "").strip()
    if not u:
        return ""
    if u.startswith("file://"):
        return u
    m = re.match(r"^[a-z]+://([^/?#]+)([^?#]*)", u)
    if m:
        return f"{m.group(1).lower()}{m.group(2).rstrip('/').lower()}"
    return u.lower()


def _norm_text(s: str | None, n: int = 120) -> str:
    """把建议文本归一到「前 n 个去空白字符」——用于识别"换了个措辞的同一件事"。"""
    t = re.sub(r"\s+", "", str(s or ""))
    return t[:n]


def dedup_key(c: dict) -> str:
    """候选去重键（唯一算法）。

    语义：同一个「优化对象 + 来源 + 主张」只应存在一条活跃候选。
    组成：target_project | skill | 归一 URL | 归一建议前缀 → sha1 前 16 位。
    """
    raw = "|".join(
        [
            str(c.get("target_project") or NONE_PROJECT),
            str(c.get("skill") or ""),
            _norm_url(c.get("url")),
            _norm_text(c.get("suggestion")),
        ]
    )
    return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def validate_candidate(c: dict) -> list[str]:
    """返回问题清单（空 = 合法）。用于 migrate / 新候选自检，避免脏数据静默入队。"""
    probs: list[str] = []
    if not str(c.get("id") or "").strip():
        probs.append("缺 id")
    if not str(c.get("skill") or "").strip() and not is_real_project(c.get("target_project")):
        probs.append("既无 skill 也无真实项目锚点")
    tp = c.get("target_project")
    if tp is not None and tp not in TARGET_PROJECTS:
        probs.append(f"target_project 非法: {tp}")
    tr = c.get("track")
    if tr is not None and tr not in TRACKS:
        probs.append(f"track 非法: {tr}")
    rg = c.get("ring")
    if rg is not None and rg not in RINGS:
        probs.append(f"ring 非法: {rg}")
    im = c.get("impact")
    if im is not None and im not in IMPACTS:
        probs.append(f"impact 非法: {im}")
    return probs


def real_project_share(cands: list[dict]) -> dict:
    """真实项目占比（发现面是否真的盯项目）——报告用。"""
    total = len(cands)
    real = sum(1 for c in cands if is_real_project(c.get("target_project")))
    meta = sum(1 for c in cands if c.get("target_project") == META_PROJECT)
    return {
        "total": total,
        "real_project": real,
        "meta": meta,
        "none": sum(1 for c in cands if (c.get("target_project") in (None, "", NONE_PROJECT))),
        "real_project_share": round(real / total, 4) if total else 0.0,
    }


def load_dedup_index(claw: Path) -> dict:
    p = claw / DEDUP_INDEX_REL
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"version": 1, "keys": {}, "updated_at": None}


def save_dedup_index(claw: Path, idx: dict) -> bool:
    p = claw / DEDUP_INDEX_REL
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        idx["updated_at"] = now_iso()
        tmp = str(p) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False, indent=2)
        json.loads(Path(tmp).read_text(encoding="utf-8"))
        os.replace(tmp, p)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[schema] warn: 去重索引写入失败: {e}", file=sys.stderr)
        return False
