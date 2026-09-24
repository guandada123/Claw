#!/usr/bin/env python3
"""hub_reconcile.py — 统一巡检中枢 · 声明式收敛检查器（observe → diff → report/act）

背景（2026-09-24 审计 + 全网对标结论）：
  "A reconciler only ever covers what you remembered to declare."
  中枢把调度真值放在 scheduler DB（automations 表），把意图/清单放在 registry.json。
  两者一旦分家就会**默认漂移**，而没有任何东西会报告它。
  本脚本就是那个"报告它"的东西：把 registry 当 declared state，把 DB 当 observed state，
  diff 出漂移并按可逆性分类处置。

漂移分类（可逆性决定处置权）：
  D1 镜像漂移  declared.rrule/status ≠ DB            → **可自动收敛**（以 DB 为准回写 registry）
  D2 声明悬空  registry 声明的 id 在 DB 不存在/已软删  → 需人审（声明本身过期）
  D3 未纳管    DB 中活跃且命中中枢 scope，但未登记      → 需人审（清单不完整）
  D4 心跳超时  declared 且节奏 ≤48h 的项，距最近一次运行超阈值 → 需人审（该跑没跑 / 平台停摆）
  D5 文档漂移  skill 文档里的**时段/键名/自动化 id** 与真值不一致 → 需人审（文档是"会腐烂的声明"）
  D6 待办逾期  registry.pending_actions[] 里 due 已过但未完结      → 需人审（"没人知道它过期了"才是问题）
  D7 契约盲区  **声明本身**空了/指向不存在的东西 → 该维度"看着在查、其实什么都没查" → 需人审
              （三次审计新增：这是"死守卫"家族在**检查器自己**身上的那一例。
                D1–D6 检查的是"世界有没有漂移"；D7 检查的是"我这份声明还能不能查出漂移"。
                例：scope 被清空 → D3 恒 0 且报 clean；doc_contract.docs 为空 → D5 恒 0；
                    守卫的校验器路径不存在/依赖 cwd → 自动切换的前置条件根本求不了值。）

退出码：0=全净；10=仅 D1（可 --fix 自愈）；20=存在 D2/D3/D4/D5/D6/D7（需人介入）
铁律：--fix **只写 registry（镜像）**，绝不写 DB；不碰 skill 文档；不推送（推送交给调用方）
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve()
CLAW = SCRIPT.parents[2]
DEFAULT_REGISTRY = CLAW / ".workbuddy" / "inspection_hub" / "registry.json"
DEFAULT_DB = Path(os.environ.get("HOME", "/Users/guan")) / ".workbuddy" / "workbuddy.db"

# D4 只覆盖"高频项"（节奏 ≤ 此小时数）；周/月/季项的漏跑由各自周期审阅覆盖，避免噪音
HEARTBEAT_MAX_CADENCE_H = 48


def now() -> datetime.datetime:
    return datetime.datetime.now()


def load_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"_error": str(e)}


def cadence_hours(rrule: str) -> float | None:
    """从 rrule 估算单次间隔（小时）；无法判定返回 None。"""
    rr = rrule or ""
    m = re.search(r"FREQ=HOURLY;INTERVAL=(\d+)", rr)
    if m:
        return float(m.group(1))
    if "FREQ=HOURLY" in rr:
        return 1.0
    if "FREQ=DAILY" in rr:
        return 24.0
    if "FREQ=WEEKLY" in rr:
        return 24 * 7.0
    if "FREQ=MONTHLY" in rr:
        return 24 * 31.0
    if "FREQ=YEARLY" in rr:
        return 24 * 366.0
    return None


def stale_threshold_h(cadence: float) -> float:
    """节奏 + 宽限（每日类给 6h、小时类给 3h），既灵敏又不误报。"""
    return cadence + max(3.0, cadence * 0.25)


def in_scope(name: str, scope: dict) -> bool:
    inc = scope.get("include_name_patterns") or []
    exc = scope.get("exclude_name_patterns") or []
    if any(x and x in name for x in exc):
        return False
    return any(x and x in name for x in inc)


# ---------------------------------------------------------------- D5 文档漂移
TIME_RE = re.compile(r"\b([01]\d|2[0-3]):([0-5]\d)\b")
AUTO_ID_RE = re.compile(r"\bautomation-\d{6,}\b")
UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
TABLE_KEY_RE = re.compile(r"^\|\s*`([A-Za-z_][A-Za-z0-9_]*(?:\[\])?)`\s*\|")


def rrule_times(rrule: str) -> list[str]:
    """rrule → ["HH:MM"]（多 BYHOUR×BYMINUTE 时取笛卡尔积的字符串形式）。"""
    rr = rrule or ""
    hs = re.findall(r"BYHOUR=([\d,]+)", rr)
    ms = re.findall(r"BYMINUTE=([\d,]+)", rr)
    if not hs:
        return []
    hours = [h for h in hs[0].split(",") if h.strip()]
    mins = [m for m in (ms[0].split(",") if ms else ["0"]) if m.strip()]
    out = []
    for h in hours:
        for m in mins:
            try:
                out.append(f"{int(h):02d}:{int(m):02d}")
            except ValueError:
                pass
    return out


def doc_section_keys(text: str, header: str = "## 中央注册表") -> list[str]:
    """抽取某标题下首列为反引号标识符的表格键。"""
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith(header):
            start = i
            break
    if start is None:
        return []
    keys = []
    for ln in lines[start + 1:]:
        if ln.startswith("## "):
            break
        m = TABLE_KEY_RE.match(ln)
        if m:
            keys.append(m.group(1))
    return keys


def check_doc_drift(reg: dict, live: dict, all_ids: set, now_dt: datetime.datetime,
                    docs_override: list[str] | None = None) -> list[dict]:
    """D5：文档里的时段/键名/自动化 id 是否与真值一致。只读，不改任何文档。

    契约支持多文档：doc_contract.docs[] 每项可为字符串（继承顶层默认）或对象（可逐项覆盖）。
    ⚠️ 铁律：锚点的 source_id 若不在 DB 里 → 必须**报出来**，绝不静默跳过
       （静默跳过 = 死守卫，锚点看着在、其实什么都没查 —— 与「只 check 不 done」同族）。
    """
    contract = reg.get("doc_contract") or {}
    defaults = {
        "key_table_header": contract.get("key_table_header", "## 中央注册表"),
        "must_mention_keys": contract.get("must_mention_keys", []),
        "time_anchors": contract.get("time_anchors", []),
        "table_keys_expected": contract.get("table_keys_expected", False),
        "table_keys_expected_exclude": contract.get("table_keys_expected_exclude", []),
    }
    # 归一化：字符串项继承顶层默认；对象项缺省字段同样继承
    def norm(d):
        return {"path": d, **defaults} if isinstance(d, str) else {**defaults, **d}

    contract_docs = [norm(d) for d in (contract.get("docs") or [])]
    if docs_override:
        # ⚠️ 修正(2026-09-24 二次审计)：覆盖必须**沿用主文档(第一份)的完整契约设置**，
        #    否则锚点/键名清单全丢 → 这条取证通道"看着在跑、其实什么都没查"（死守卫第四例）。
        base = dict(contract_docs[0]) if contract_docs else dict(defaults)
        docs = [{**base, "path": p} for p in docs_override]
    else:
        docs = contract_docs
    if not docs:
        return []

    reg_keys = set(reg.keys())
    findings: list[dict] = []

    for spec in docs:
        raw = spec["path"]
        p = Path(os.path.expanduser(raw))
        if not p.exists():
            findings.append({"kind": "doc_missing", "doc": raw, "detail": "契约声明的文档不存在"})
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()

        # K5 版本对（可选，逐文档开关）：文档 frontmatter version 必须 = registry.version
        if spec.get("version_check"):
            m = re.search(r"^version:\s*([0-9][\w.\-]*)", text, re.M)
            doc_v = m.group(1) if m else None
            reg_v = str(reg.get("version", ""))
            if doc_v is None:
                findings.append({"kind": "version_missing", "doc": raw,
                                 "detail": "开启了 version_check 但文档 frontmatter 没有 `version:`"})
            elif doc_v != reg_v:
                findings.append({"kind": "version_mismatch", "doc": raw,
                                 "doc_version": doc_v, "registry_version": reg_v,
                                 "detail": (f"文档 version={doc_v} 与 registry.version={reg_v} 不一致"
                                            f"（两份声明要一起动，否则其中一份在说谎）")})

        # K1 时段漂移：label 所在行必须出现真值时刻（真值现算，不写死在契约里）
        for a in spec.get("time_anchors", []) or []:
            label = a.get("label")
            sid = a.get("source_id")
            row = live.get(sid)
            if row is None:
                findings.append({
                    "kind": "anchor_dangling", "doc": raw, "label": label, "source_id": sid,
                    "detail": (f"时段锚点「{label}」指向的 {sid} 不在活跃自动化里"
                               f"（{'已软删' if sid in all_ids else 'DB 中不存在'}）→ 锚点**静默失效**，"
                               f"必须改指向或删除，否则这条契约什么都没在查"),
                })
                continue
            times = rrule_times(row["rrule"])
            if len(times) > 1:
                # 多槽 rrule（BYHOUR=0,12）时 K1 只能校第一个时刻 —— 静默只校一半 = 少查了还不知道。
                # 排程铁律本就禁止多槽（要重复请用 --interval-hours 建独立自动化），故直接报盲区。
                findings.append({
                    "kind": "anchor_multi_slot", "doc": raw, "label": label, "source_id": sid,
                    "times": times,
                    "detail": (f"「{label}」对应自动化有 {len(times)} 个时刻 {times}，"
                               f"K1 只能校第一个 → 契约覆盖不全（多槽排程须拆成独立自动化）"),
                })
            truth = a.get("expect_override") or (times[:1] or [None])[0]
            if not label or not truth:
                findings.append({"kind": "anchor_unverifiable", "doc": raw, "label": label,
                                 "source_id": sid,
                                 "detail": f"锚点「{label}」无法算出真值（rrule={row['rrule']} 无 BYHOUR/BYMINUTE）"})
                continue
            if any(label in ln and truth in ln for ln in lines):
                continue
            near = sorted({f"{h}:{m}" for ln in lines if label in ln for h, m in TIME_RE.findall(ln)})
            findings.append({
                "kind": "time_mismatch", "doc": raw, "label": label, "source_id": sid,
                "truth": truth, "doc_times": near,
                "detail": (f"「{label}」真值 {truth}，但文档中没有该时刻" +
                           (f"（文档同类行出现 {', '.join(near)}）" if near else "（文档完全未提该时段）")),
            })

        # K2 契约键缺失
        for k in spec.get("must_mention_keys", []) or []:
            if k not in text:
                findings.append({"kind": "key_omitted", "doc": raw, "key": k,
                                 "detail": f"契约要求提及的 registry 键 `{k}` 在文档中找不到"})

        # K3 文档表格列出的键不存在于 registry / registry 有键但表格漏列
        listed = doc_section_keys(text, spec.get("key_table_header", "## 中央注册表"))
        listed_norm = {k[:-2] if k.endswith("[]") else k for k in listed}
        for k in listed_norm:
            if k not in reg_keys:
                findings.append({"kind": "key_unknown", "doc": raw, "key": k,
                                 "detail": f"文档注册表列出了 `{k}`，但 registry.json 里没有这个键"})
        if listed_norm and spec.get("table_keys_expected"):
            excl = set(spec.get("table_keys_expected_exclude", []) or [])
            expected = {k for k in reg_keys if k not in excl}
            for k in sorted(expected - listed_norm):
                findings.append({"kind": "key_missing_from_table", "doc": raw, "key": k,
                                 "detail": f"registry 有键 `{k}`，但文档中央注册表表格漏列了（表=registry 的索引，会一起腐烂）"})

        # K4 引用了 DB 中不存在的自动化 id（跳过含 URL 的行 —— 曾把 bittide 文章号误判为 dangling id）
        for i, ln in enumerate(lines, 1):
            if "http" in ln:
                continue
            for m in list(AUTO_ID_RE.findall(ln)) + list(UUID_RE.findall(ln)):
                if m not in all_ids:
                    findings.append({"kind": "id_dangling", "doc": raw, "id": m, "line": i,
                                     "detail": f"第 {i} 行引用了不存在的自动化 id {m}"})

    return findings


DONE_STATES = {"done", "completed", "cancelled", "canceled", "closed", "skipped"}
IF_NO_ACTION_ENUM = {"auto_switch_to_live", "keep_calibrate", "keep_as_is", "escalate"}
# 前置条件表达式里的标识符（用于"契约 ↔ 校验器"是否已脱节的机械核对）
COND_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
COND_STOPWORDS = {"true", "false", "True", "False", "and", "or", "not", "None"}


def check_contract_blind(reg: dict, root: Path) -> list[dict]:
    """D7：契约盲区 —— 声明本身是否还"能查出东西"。

    存在理由（三次审计）：D1–D6 的每一次扫，都建立在"我的声明是完整、且它指向的东西还在"这个假设上。
    假设一旦破了，检查器**不是报错，而是报 clean** —— 空声明和零漂移在输出上长得一模一样。
    这正是"死守卫"家族（任何需要输入的机制都必须自证输入还在）在检查器自己身上的那一例。
    """
    out: list[dict] = []

    def blind(key: str, detail: str) -> None:
        out.append({"kind": "contract_blind", "key": key, "detail": detail})

    # ---- 1) 各维度的输入声明是否非空（空声明 = 该维度永远空转，却仍计入 clean）----
    if not (reg.get("automation_scope") or {}).get("include_name_patterns"):
        blind("automation_scope.include_name_patterns",
              "scope 的 include_name_patterns 为空 → D3「未纳管」永远查不到东西（空声明 ≠ 无漂移）")
    if not (reg.get("automations") or []):
        blind("automations", "自动化声明清单为空 → D1/D2/D4 全部空转")
    docs = (reg.get("doc_contract") or {}).get("docs") or []
    if not docs:
        blind("doc_contract.docs", "文档契约未登记任何文档 → D5「文档漂移」永远查不到东西")
    for d in docs:
        spec = d if isinstance(d, dict) else {"path": d}
        merged = {**(reg.get("doc_contract") or {}), **spec}
        if not (merged.get("time_anchors") or merged.get("must_mention_keys")
                or merged.get("table_keys_expected")):
            blind(f"doc_contract.docs[{spec.get('path')}]",
                  f"文档「{spec.get('path')}」已登记，但锚点/必提键/表检查三者皆空 → 这条契约什么都没在查")
    if not (reg.get("autonomy_methods") or {}).get("ladder"):
        blind("autonomy_methods.ladder",
              "自主度阶梯未声明 → 「谁被允许自己动手」没有可对账的来源")

    # ---- 2) 切换守卫是否可验证（这是全系统唯一能自己改生产 mode 的机器开关）----
    sg = (reg.get("doc_apply") or {}).get("switch_guard") or {}
    ver = sg.get("precondition_verifier")
    if not ver:
        blind("doc_apply.switch_guard.precondition_verifier",
              "守卫声明了自动切换，却没声明用哪个校验器判定 → 前置条件不可验证")
    else:
        m = re.search(r"([\w$./\-]+\.py)", str(ver))
        raw = m.group(1) if m else None
        path = None
        if not raw:
            blind("doc_apply.switch_guard.precondition_verifier",
                  f"校验器字符串里找不到脚本路径：{ver!r}")
        elif raw.startswith("$CLAW/"):
            path = root / raw[len("$CLAW/"):]
        elif raw.startswith("/"):
            path = Path(raw)
        else:
            blind("doc_apply.switch_guard.precondition_verifier",
                  f"校验器用 **cwd 相对路径** {raw} → 换个工作目录执行就 No such file，"
                  f"守卫会在无人察觉时失效（一律写成 $CLAW/... 或绝对路径）")
        if path is not None:
            if not path.exists():
                blind("doc_apply.switch_guard.precondition_verifier",
                      f"校验器 {path} 不存在 → 自动切换的前置条件无法求值")
            else:
                vsrc = path.read_text(encoding="utf-8", errors="replace")
                for cond in sg.get("preconditions") or []:
                    for ident in COND_IDENT_RE.findall(str(cond)):
                        if ident in COND_STOPWORDS:
                            continue
                        leaf = ident.rsplit(".", 1)[-1]
                        if leaf not in vsrc:
                            blind(f"switch_guard.preconditions[{cond}]",
                                  f"前置条件里的 `{leaf}` 在校验器 {path.name} 里找不到 → "
                                  f"契约与校验器可能已脱节（改了输出键名而没改前置条件）")

    conds = [str(x) for x in (sg.get("preconditions") or [])]
    if conds and not any(re.match(r"^\s*[\w.]*\.?ok\s*==", c) for c in conds):
        blind("doc_apply.switch_guard.preconditions",
              "前置条件缺少 `ok == true` 兜底 → 校验器返回错误 payload（键整体缺失）时，"
              "缺键可能被当成空值/假值而判为通过 —— **错误 ≠ 通过**")

    # ---- 3) if_no_action 枚举 × auto_switch：消灭"到期不动会怎样"的自由文本副本 ----
    pa = reg.get("pending_actions") or []
    switch_flag = bool((reg.get("doc_apply") or {}).get("auto_switch"))
    for a in pa:
        v = a.get("if_no_action")
        pid = a.get("id")
        if v is None:
            blind(f"pending_actions[{pid}]",
                  "缺 `if_no_action`（枚举）：到期不动的后果没有机器可读声明，自由文本迟早与真值脱节")
        elif v not in IF_NO_ACTION_ENUM:
            blind(f"pending_actions[{pid}]",
                  f"`if_no_action={v}` 不在枚举 {sorted(IF_NO_ACTION_ENUM)} 内（枚举才能机器交叉核对）")
        elif v == "auto_switch_to_live" and not switch_flag:
            blind(f"pending_actions[{pid}]",
                  "声明「到期不动就自动切 live」，但 doc_apply.auto_switch=false —— 两处真值互相矛盾")
        elif v == "keep_calibrate" and switch_flag:
            blind(f"pending_actions[{pid}]",
                  "声明「到期不动就保持 calibrate」，但 doc_apply.auto_switch=true —— 两处真值互相矛盾")
    if switch_flag and not any(a.get("if_no_action") == "auto_switch_to_live" for a in pa):
        blind("doc_apply.auto_switch",
              "auto_switch=true，却没有任何待办声明 `if_no_action: auto_switch_to_live` → "
              "「自动切换」这件事没有可对账的待办条目")
    return out


def check_pending_due(reg: dict, now_dt: datetime.datetime) -> list[dict]:
    """D6：pending_actions[] 里 due 已过但仍未完结的待办。

    存在理由：PA-001（校准期→生效期）这类"有期限、默认不动也行"的待办，
    最容易在没人的时候悄悄过期 —— 过期本身不是故障，但**没人知道它过期了**才是。
    """
    out = []
    today = now_dt.date()
    for a in reg.get("pending_actions", []) or []:
        due = a.get("due")
        if not due:
            continue
        try:
            due_d = datetime.date.fromisoformat(str(due)[:10])
        except ValueError:
            out.append({"id": a.get("id"), "due": due, "kind": "bad_due",
                        "detail": f"`due` 不是 ISO 日期: {due}"})
            continue
        if str(a.get("status", "")).lower() in DONE_STATES:
            continue
        if due_d < today:
            out.append({"id": a.get("id"), "title": a.get("title"), "owner": a.get("owner"),
                        "due": str(due_d), "overdue_days": (today - due_d).days,
                        "status": a.get("status"),
                        "if_no_action": a.get("if_no_action"),
                        "detail": (f"{a.get('id')} 已逾期 {(today - due_d).days} 天（due {due_d}，"
                                   f"owner={a.get('owner')}，status={a.get('status')}，"
                                   f"不动则 {a.get('if_no_action')}）"
                                   f"：{a.get('title')}")})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--fix", action="store_true", help="仅收敛 D1（以 DB 真值回写 registry 镜像）")
    ap.add_argument("--doc", action="append", default=None,
                    help="覆盖 doc_contract.docs（可重复；供自测造错样本，不写回任何文件）")
    ap.add_argument("--no-doc", action="store_true", help="跳过 D5 文档漂移检查")
    ap.add_argument("--brief", action="store_true",
                    help="仅输出需人审项的一行式摘要（供外部看门狗做告警正文，避免调用方拼 JSON）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    reg_path = Path(args.registry)
    reg = load_json(reg_path)
    if "_error" in reg:
        print(json.dumps({"ok": False, "error": f"registry 读取失败: {reg['_error']}"}, ensure_ascii=False))
        return 20

    db_path = Path(args.db)
    if not db_path.exists():
        print(json.dumps({"ok": False, "error": f"调度库不存在: {db_path}"}, ensure_ascii=False))
        return 20

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "select id,name,rrule,status,deleted_at,created_at from automations"
        ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"调度库读取失败: {e}"}, ensure_ascii=False))
        return 20

    live = {r["id"]: r for r in rows if not r["deleted_at"]}
    all_ids = {r["id"] for r in rows}
    last_run: dict[str, int] = {}
    try:
        for r in conn.execute(
            "select automation_id, max(created_at) as t from automation_runs group by automation_id"
        ):
            if r["t"]:
                last_run[r["automation_id"]] = r["t"]
    except Exception:  # noqa: BLE001
        pass  # 运行表缺失 → D4 全部按"未知"处理（不误报）

    declared = reg.get("automations", []) or []
    scope = reg.get("automation_scope", {}) or {}
    now_dt = now()

    d1, d2, d3, d4 = [], [], [], []

    # ---- D1 / D2 ----
    for a in declared:
        i = a.get("id")
        row = live.get(i)
        if row is None:
            d2.append({"id": i, "name": a.get("name"),
                       "why": "已软删" if i in all_ids else "DB 中不存在",
                       "declared_rrule": a.get("rrule")})
            continue
        if row["rrule"] != a.get("rrule") or row["status"] != a.get("status"):
            d1.append({"id": i, "name": row["name"],
                       "field": "rrule" if row["rrule"] != a.get("rrule") else "status",
                       "declared": a.get("rrule") if row["rrule"] != a.get("rrule") else a.get("status"),
                       "observed": row["rrule"] if row["rrule"] != a.get("rrule") else row["status"]})

    # ---- D3 未纳管 ----
    declared_ids = {a.get("id") for a in declared}
    if scope.get("include_name_patterns"):
        for r in live.values():
            if r["id"] in declared_ids:
                continue
            if r["status"] != "ACTIVE":
                continue
            if in_scope(r["name"] or "", scope):
                d3.append({"id": r["id"], "name": r["name"], "rrule": r["rrule"], "status": r["status"]})

    # ---- D4 心跳 ----
    for a in declared:
        i = a.get("id")
        row = live.get(i)
        if row is None:
            continue
        if row["status"] != "ACTIVE":
            continue          # ⚠️ 修正(二次审计)：PAUSED 的自动化"不跑"是预期行为，不该报心跳超时
        c = cadence_hours(row["rrule"])
        if c is None or c > HEARTBEAT_MAX_CADENCE_H:
            continue
        base_ts = last_run.get(i)
        if base_ts is None:
            base_ts = row["created_at"]          # 新自动化：以创建时刻计宽限
        if not base_ts:
            continue
        age_h = (now_dt - datetime.datetime.fromtimestamp(base_ts / 1000)).total_seconds() / 3600
        thr = stale_threshold_h(c)
        if age_h > thr:
            d4.append({"id": i, "name": row["name"], "rrule": row["rrule"],
                       "age_h": round(age_h, 1), "threshold_h": round(thr, 1),
                       "last_run": datetime.datetime.fromtimestamp(base_ts / 1000).strftime("%Y-%m-%d %H:%M")
                       if last_run.get(i) else "(从未运行，按创建时刻计)"})

    # ---- D5 文档漂移（只读；文档是"会腐烂的声明"）----
    d5 = [] if args.no_doc else check_doc_drift(reg, live, all_ids, now_dt, args.doc)

    # ---- D6 待办逾期（有期限的待办最容易在没人看着时悄悄过期）----
    d6 = check_pending_due(reg, now_dt)

    # ---- D7 契约盲区（检查器自查：我这份声明还查得出东西吗）----
    d7 = check_contract_blind(reg, CLAW)

    # ---- 收敛（仅 D1，且仅在 --fix）----
    fixed = []
    if args.fix and d1:
        by_id = {a.get("id"): a for a in declared}
        for d in d1:
            a = by_id.get(d["id"])
            row = live.get(d["id"])
            if not a or not row:
                continue
            a["rrule"], a["status"] = row["rrule"], row["status"]
            fixed.append(d["id"])
        if fixed:
            reg.setdefault("_state_sync_notes", []).append(
                f"{now():%Y-%m-%d %H:%M} hub_reconcile --fix 以 DB 真值收敛镜像 D1 {len(fixed)} 处: " + ", ".join(fixed)
            )
            reg["updated_at"] = now().strftime("%Y-%m-%dT%H:%M:%S")
            tmp = str(reg_path) + ".tmp"
            Path(tmp).write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, reg_path)

    out = {
        "ok": True,
        "declared": len(declared),
        "observed_live": len(live),
        "scope": {"include": scope.get("include_name_patterns", []), "exclude": scope.get("exclude_name_patterns", [])},
        "D1_mirror_drift": d1,
        "D2_declared_missing": d2,
        "D3_unregistered": d3,
        "D4_heartbeat_stale": d4,
        "D5_doc_drift": d5,
        "D6_pending_overdue": d6,
        "D7_contract_blind": d7,
        "fixed": fixed,
        "clean": not (d1 or d2 or d3 or d4 or d5 or d6 or d7),
        "needs_human": bool(d2 or d3 or d4 or d5 or d6 or d7),
    }
    rc = 0 if out["clean"] else (10 if (d1 and not (d2 or d3 or d4 or d5 or d6 or d7)) else 20)

    if args.brief:
        # 供告警正文：只列"需人审"的项，天然带 6h 冷却由调用方控制
        for x in d4:
            print(f"[心跳超时] {x['name']} 上次 {x['last_run']} 已 {x['age_h']}h（阈 {x['threshold_h']}h）")
        for x in d2:
            print(f"[声明悬空] {x.get('name')} — {x.get('why')}")
        for x in d3:
            print(f"[未纳管] {x.get('name')} {x.get('rrule')}")
        for x in d5:
            print(f"[文档腐烂] {Path(os.path.expanduser(x['doc'])).name} — {x['detail']}")
        for x in d6:
            print(f"[待办逾期] {x.get('detail')}")
        for x in d7:
            print(f"[契约盲区] {x.get('key')} — {x.get('detail')}")
        return rc

    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(f"[reconcile] declared={len(declared)} observed_live={len(live)} clean={out['clean']}")
        for k, label in (("D1_mirror_drift", "D1 镜像漂移(可自动收敛)"),
                         ("D2_declared_missing", "D2 声明悬空(需人审)"),
                         ("D3_unregistered", "D3 未纳管(需人审)"),
                         ("D4_heartbeat_stale", "D4 心跳超时(需人审)"),
                         ("D5_doc_drift", "D5 文档漂移(需人审)"),
                         ("D6_pending_overdue", "D6 待办逾期(需人审)"),
                         ("D7_contract_blind", "D7 契约盲区(检查器自己空转，需人审)")):
            items = out[k]
            if items:
                print(f"  {label}: {len(items)}")
                for it in items[:8]:
                    if k == "D1_mirror_drift":
                        print(f"    · {it['name'][:30]} {it['field']}: {it['declared']} -> {it['observed']}")
                    elif k == "D4_heartbeat_stale":
                        print(f"    · {it['name'][:30]} 上次 {it['last_run']} 已 {it['age_h']}h (阈 {it['threshold_h']}h)")
                    elif k == "D5_doc_drift":
                        print(f"    · [{it.get('kind')}] {it['detail']}")
                    elif k == "D6_pending_overdue":
                        print(f"    · {it.get('detail')}")
                    elif k == "D7_contract_blind":
                        print(f"    · [{it.get('key')}] {it.get('detail')}")
                    else:
                        print(f"    · {it.get('name')} {it.get('rrule','')} {it.get('why','')}")
        if fixed:
            print(f"  ✅ 已收敛 D1 {len(fixed)} 处（DB → registry 镜像）")
        print(f"[reconcile] rc={rc}" + ("（可 --fix 自愈）" if rc == 10 else ""))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
