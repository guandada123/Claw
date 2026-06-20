#!/usr/bin/env python3
"""
全系统统一健康检查
检查 Docker容器 / HTTP端点 / Tailscale / Marvis Bridge / 系统资源 / 自动化
输出: JSON + 终端彩色输出
"""
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

OUTPUT_DIR = Path(os.environ.get("CLAW_OUTPUT_DIR", "/Users/guan/WorkBuddy/Claw/output"))
HOME_DIR = Path.home()


def run_cmd(cmd: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    """运行系统命令，超时返回空结果"""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return subprocess.CompletedProcess(cmd, -1, "", "timeout/not found")


# ─── 1. Docker Containers ───
def check_docker() -> dict:
    result = run_cmd(["docker", "ps", "--format", "{{.Names}}|{{.Status}}|{{.Image}}"], timeout=15)
    containers = []
    for line in result.stdout.strip().split("\n"):
        if not line or "|" not in line:
            continue
        parts = line.split("|", 2)
        name = parts[0]
        status = parts[1]
        image = parts[2] if len(parts) > 2 else ""
        is_up = "Up" in status
        has_health = "healthy" in status
        containers.append({
            "name": name, "status": status.strip(),
            "up": is_up, "healthy": is_up and (has_health or True),
            "image": image.split("/")[-1] if "/" in image else image,
        })
    healthy = sum(1 for c in containers if c["healthy"])
    return {"total": len(containers), "healthy": healthy, "containers": containers}


# ─── 2. HTTP Endpoints ───
def check_http() -> dict:
    endpoints = {
        "PMF 监控面板": "http://localhost:8000",
        "Quant Dashboard": "http://localhost:3000",
        "We-MP-RSS 订阅": "http://localhost:18001",
    }
    checks = {}
    ok = 0
    for name, url in endpoints.items():
        try:
            code = subprocess.run(
                ["curl", "-sL", "-o", "/dev/null", "-w", "%{http_code}",
                 "--connect-timeout", "5", "--max-time", "10", url],
                capture_output=True, text=True, timeout=15
            )
            status_code = code.stdout.strip()
            is_ok = status_code not in ("", "000") and int(status_code) < 500
            if is_ok:
                ok += 1
            checks[name] = {"url": url, "status_code": int(status_code) if status_code.isdigit() else 0, "ok": is_ok}
        except Exception:
            checks[name] = {"url": url, "status_code": 0, "ok": False}
    return {"total": len(endpoints), "passing": ok, "checks": checks}


# ─── 3. Tailscale ───
def check_tailscale() -> dict:
    ts = run_cmd(["tailscale", "--socket=/Users/guan/Library/Caches/tailscale/tailscaled.sock", "status"], timeout=5)
    if ts.returncode != 0:
        return {"status": "stopped", "ip": "", "devices": 0, "peers": []}

    peers = []
    self_ip = ""
    for line in ts.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) >= 2:
            ip = parts[0]
            name = parts[1].rstrip(".")
            online = "online" in ts.stdout.lower() or len(parts) > 3
            if ip.startswith("100."):
                if not self_ip:
                    self_ip = ip
                peers.append({"name": name, "ip": ip, "online": online})

    return {
        "status": "connected" if self_ip else "unknown",
        "ip": self_ip,
        "devices": len(peers),
        "peers": [p for p in peers if p["name"] != peers[0]["name"]][:10] if peers else [],
    }


# ─── 4. Marvis Bridge ───
def check_bridge() -> dict:
    bridge_file = Path("/Users/guan/workbuddy_marvis_bridge/status/bridge.json")
    mode = "unknown"
    recent_tasks = 0
    if bridge_file.exists():
        try:
            data = json.loads(bridge_file.read_text())
            mode = data.get("mode", "unknown")
        except Exception:
            pass

    watchers = 0
    try:
        r = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True, timeout=5
        )
        for kw in ["file_watcher", "bridge_monitor", "workbuddy_poller", "fswatch"]:
            watchers += r.stdout.count(kw)
    except Exception:
        pass

    return {"status": mode, "watchers": watchers}


# ─── 5. System Resources ───
def check_system() -> dict:
    # Disk
    disk = run_cmd(["df", "-h", "/"])
    disk_used = disk_avail = disk_pct = "?"
    for line in disk.stdout.split("\n"):
        if line.startswith("/dev/"):
            parts = line.split()
            if len(parts) >= 5:
                disk_used = f"{parts[2]}/{parts[1]}"
                disk_pct = parts[4]

    # Uptime
    uptime = run_cmd(["uptime"])
    uptime_str = sys_info = load = ""
    if uptime.returncode == 0:
        parts = uptime.stdout.split("\n")[0]
        # Extract uptime
        if "up" in parts:
            uptime_str = parts.split("up", 1)[1].split(",")[0].strip()
        # Extract load
        if "load averages:" in parts:
            load = parts.split("load averages:")[1].strip()
        elif "load average:" in parts:
            load = parts.split("load average:")[1].strip()

    # Memory
    mem = run_cmd(["vm_stat"])
    mem_used = mem_total = mem_pct = "?"
    try:
        r = run_cmd(["sysctl", "-n", "hw.memsize"])
        mem_total_gb = round(int(r.stdout.strip()) / 1024 / 1024 / 1024, 1)
        mem_pages = {}
        for line in mem.stdout.split("\n"):
            if ":" in line:
                k = line.split(":")[0].strip()
                v = line.split(":")[1].strip().rstrip(".")
                if v.isdigit():
                    mem_pages[k] = int(v)
        active = mem_pages.get("Pages active", 0)
        wired = mem_pages.get("Pages wired down", 0)
        occupied = mem_pages.get("Pages occupied by compressor", 0)
        page_size = 16384  # macOS arm64
        used_gb = round((active + wired + occupied) * page_size / 1024 / 1024 / 1024, 1)
        mem_used = f"{used_gb}G"
        mem_total = f"{mem_total_gb}G"
        mem_pct = f"{round(used_gb / mem_total_gb * 100)}%"
    except Exception:
        pass

    return {
        "disk": disk_used,
        "disk_pct": disk_pct,
        "uptime": uptime_str,
        "load": load,
        "memory": {"used": mem_used, "total": mem_total, "pct": mem_pct},
    }


# ─── 6. CI Failure Alerting ───
def check_ci_failures() -> dict:
    repos = {
        "QuantTradingSystem": "/Users/guan/WorkBuddy/QuantTradingSystem",
        "MarvisBridge": "/Users/guan/workbuddy_marvis_bridge",
    }
    results = []
    three_days_ago = (datetime.now() - timedelta(days=3)).isoformat()
    for name, path in repos.items():
        if not (Path(path) / ".git").exists():
            continue
        try:
            r = subprocess.run(
                ["gh", "run", "list", "--repo", f"guandada123/{name}",
                 "--limit", "10", "--json", "conclusion,createdAt,workflowName,headBranch"],
                capture_output=True, text=True, timeout=15, cwd=path,
            )
            if r.returncode != 0:
                continue
            import json as _json
            runs = _json.loads(r.stdout)
            failures = [x for x in runs
                        if x.get("conclusion") == "failure" and x.get("createdAt", "") > three_days_ago]
            from collections import Counter
            branch_failures = Counter(x.get("headBranch", "unknown") for x in failures)
            alerts = [{"branch": b, "failures": c} for b, c in branch_failures.items() if c >= 3]
            if alerts:
                results.append({"repo": name, "alerts": alerts})
        except Exception:
            pass
    return {"problems": results}


# ─── 7. Repo Health Check ───
def check_repo_health() -> dict:
    repos = {
        "Claw": "/Users/guan/WorkBuddy/Claw",
        "QuantTradingSystem": "/Users/guan/WorkBuddy/QuantTradingSystem",
        "StockInsight": "/Users/guan/WorkBuddy/stock-insight",
        "MarvisBridge": "/Users/guan/workbuddy_marvis_bridge",
    }
    problems = []
    for name, path in repos.items():
        p = Path(path)
        if not p.exists():
            problems.append({"repo": name, "issue": "目录不存在"})
            continue
        files = [x for x in p.iterdir() if not x.name.startswith(".")]
        if not files:
            problems.append({"repo": name, "issue": "目录为空"})
    return {"problems": problems}


# ─── 8. WorkBuddy Automations ───
def check_automations() -> dict:
    db_path = HOME_DIR / ".workbuddy" / "workbuddy.db"
    active = 0
    failures_24h = 0
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            c = conn.cursor()
            c.execute('SELECT COUNT(*) FROM automations WHERE status="ACTIVE"')
            active = c.fetchone()[0] or 0
            c.execute("""
                SELECT COUNT(*) FROM automation_runs
                WHERE status="failed" AND created_at > datetime("now", "-24 hours")
            """)
            failures_24h = c.fetchone()[0] or 0
            conn.close()
        except Exception:
            pass
    return {"active": active, "failures_24h": failures_24h}


# ─── Main ───
def main():
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    output_json = "--json" in sys.argv

    # Run all checks
    docker = check_docker()
    http = check_http()
    tailscale = check_tailscale()
    bridge = check_bridge()
    system = check_system()
    automations = check_automations()
    ci_failures = check_ci_failures()
    repo_health = check_repo_health()

    # Calculate overall
    total_failures = docker["unhealthy"] = docker["total"] - docker["healthy"]
    http_fails = http["failing"] = http["total"] - http["passing"]
    tailscale_fail = 1 if tailscale["status"] != "connected" else 0

    # Component status
    components = {
        "docker": "healthy" if total_failures == 0 else ("degraded" if total_failures < docker["total"] else "critical"),
        "http": "healthy" if http_fails == 0 else ("degraded" if http_fails < http["total"] else "critical"),
        "tailscale": "healthy" if tailscale["status"] == "connected" else "degraded",
        "bridge": "healthy" if bridge["watchers"] >= 3 else ("degraded" if bridge["watchers"] > 0 else "critical"),
        "automations": "healthy" if automations["failures_24h"] == 0 else ("degraded" if automations["failures_24h"] < 5 else "critical"),
        "ci_health": "healthy" if len(ci_failures.get("problems", [])) == 0 else "degraded",
        "repo_health": "healthy" if len(repo_health.get("problems", [])) == 0 else "degraded",
    }

    healthy_count = sum(1 for v in components.values() if v == "healthy")
    degraded_count = sum(1 for v in components.values() if v == "degraded")
    critical_count = sum(1 for v in components.values() if v == "critical")

    if critical_count > 0:
        overall = "critical"
        icon = "🔴"
    elif degraded_count > 1 or total_failures > 0:
        overall = "degraded"
        icon = "🟡"
    else:
        overall = "healthy"
        icon = "🟢"

    now = datetime.now()
    result = {
        "timestamp": int(time.time()),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "overall": {"status": overall, "icon": icon, "total_failures": total_failures + http_fails + tailscale_fail},
        "components": components,
        "docker": docker,
        "http": http,
        "tailscale": tailscale,
        "bridge": bridge,
        "system": system,
        "automations": automations,
        "ci_failures": ci_failures,
        "repo_health": repo_health,
    }

    # Write output files
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    json_str = json.dumps(result, ensure_ascii=False, indent=2)
    (OUTPUT_DIR / "health_status.json").write_text(json_str)
    (OUTPUT_DIR / "health_status_latest.json").write_text(json_str)

    if not output_json:
        # Terminal display
        print(f"{'='*50}")
        print(f"  全系统健康检查  {icon}  {result['date']} {result['time']}")
        print(f"{'='*50}")
        print("")
        print(f"📦 Docker ({docker['healthy']}/{docker['total']}): {components['docker']}")
        for c in docker["containers"]:
            status = "🟢" if c["healthy"] else "🔴"
            print(f"  {status} {c['name']:25s} {c['status'][:30]}")
        print("")
        print(f"🌐 HTTP ({http['passing']}/{http['total']}): {components['http']}")
        for name, c in http["checks"].items():
            status = "🟢" if c["ok"] else "🔴"
            print(f"  {status} {name:20s} (HTTP {c['status_code']})")
        print("")
        print(f"🔗 Tailscale: {tailscale['status']}  ({tailscale['ip']}, {tailscale['devices']} peers)")
        print(f"🔗 Marvis Bridge: {bridge['status']}  (watchers: {bridge['watchers']})")
        print(f"⚙️  Automations: {automations['active']} active, {automations['failures_24h']}/24h failures")
        print("")
        print(f"💻 System: {system['disk']} ({system['disk_pct']}) | "
              f"mem: {system['memory']['used']}/{system['memory']['total']} | "
              f"load: {system['load']}")
        print(f"   Uptime: {system['uptime']}")
        print("")
        print(f"📊 组件: 🟢{healthy_count}  🟡{degraded_count}  🔴{critical_count}")
        print(f"{'='*50}")
    else:
        print(json_str)

    # Return exit code for alerting
    sys.exit(0 if overall == "healthy" else 1)


if __name__ == "__main__":
    main()
