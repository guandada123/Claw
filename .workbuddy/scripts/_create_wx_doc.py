import json
import subprocess
import sys
from pathlib import Path


def _claw_root() -> Path:
    """位置无关定位仓库根（向上找含 .workbuddy 的目录）。2026-10-12 P1-12 硬编码治理时加入。"""
    p = Path(__file__).resolve().parent
    for cand in (p, *p.parents):
        if (cand / ".workbuddy").is_dir():
            return cand
    raise RuntimeError("找不到含 .workbuddy 的仓库根")


path = f"{_claw_root()}/output/wx_reports/20260714_morning.md"
with open(path, encoding="utf-8") as f:
    content = f.read()
title = "📊微信早报 — 2026-07-14（周二）"
cli = str(
    Path.home() / ".workbuddy" / "binaries" / "node", "versions", "22.22.2", "bin", "lark-cli"
)

for identity in ["user", "bot"]:
    args = [
        cli,
        "docs",
        "+create",
        "--as",
        identity,
        "--doc-format",
        "markdown",
        "--title",
        title,
        "--content",
        content,
    ]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=120)
        out = r.stdout.strip()
        try:
            res = json.loads(out)
        except Exception:
            print(f"[{identity}] raw:", out[:500], file=sys.stderr)
            print(f"[{identity}] stderr:", r.stderr[:500], file=sys.stderr)
            continue
        if res.get("ok"):
            d = res["data"]["document"]
            print("OK", identity, d.get("document_id"), d.get("url"))
            sys.exit(0)
        else:
            print(
                f"[{identity}] not ok:", json.dumps(res, ensure_ascii=False)[:400], file=sys.stderr
            )
    except Exception as e:
        print(f"[{identity}] exc:", e, file=sys.stderr)
print("FAILED_ALL")
sys.exit(1)
