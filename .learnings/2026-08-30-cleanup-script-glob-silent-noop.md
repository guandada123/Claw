# 2026-08-30 — 磁盘巡检脚本 glob 命名不匹配导致静默漏扫

## 现象
周日磁盘巡检自动化（automation-1786001867730）中 `db_backup_rotate.py --apply`
报告「备份总数: 0 / 无过期备份，无需清理」，看似健康。

## 根因（取证确认，非推断）
`ls ~/.workbuddy | grep workbuddy.db` 显示实际存在 4 个备份（约 23MB）：
- `workbuddy.db.bak.20260707_124641`
- `workbuddy.db.bak.20260707_180255`
- `workbuddy.db.bak.20260707_221017`
- `workbuddy.db.bak.20260813_204747`

实际命名是 **点号** `workbuddy.db.bak.<YYYYMMDD_HHMMSS>`，
而脚本 `BACKUP_PREFIX = "workbuddy.db.bak-"` 用的是 **连字符** →
`DB_DIR.glob("workbuddy.db.bak-*")` 恒空 → 长期静默 no-op。

## 教训 ✅已升级(2026-08-30)
**"总数 0 / 无需清理"这类健康报告必须先反证再采信。**
守护类脚本（清理/巡检/对账）输出 0 命中时，默认的正确动作是
**先手工列目录/查库确认"是真的 0 还是匹配器错了"**，而不是直接当作正常上报。
0 命中 = 可能是健康，也可能是匹配器失效 —— 二者对外表现完全一致。

推论：清理类脚本应内置 **自检/告警**：若 glob 模式在目标目录存在疑似同类
文件名却 0 命中，应显式告警而非静默成功。

## 修复（已落地 Claw/.workbuddy/scripts/db_backup_rotate.py）
1. `BACKUP_PATTERNS = ("workbuddy.db.bak-*", "workbuddy.db.bak.*")` 双命名兼容，
   并排除 `-wal` / `-shm` / `-journal` 防误伤主库附属文件。
2. `_to_trash()` osascript 超时 30s → 90s：首个调用需冷启动 Finder，
   30s 不够会回退 `os.remove`，**绕过废纸篓变成不可逆删除**。

## 副作用记录
- 修复后首次 apply：4 个过期备份全部清理（保留 7 天，mtime 均早于 2026-08-23）。
- 删除前已复制兜底到 `/tmp/workbuddy/db_bak_safety_20260830/` 并 md5 校验一致。
- 首个文件因超时走 `rm-fallback` 未进废纸篓，其余 3 个走 trash。
- `~/.Trash` 无法用 shell 验证（macOS TCC: Operation not permitted），
  只能凭 osascript 退出码 0 判断 —— 属于"验证手段受限"，非"删除失败"。

## 关联铁律
- 🔴 日志成功≠数据落库，凡写库须 readback（此处延伸：凡删除须先兜底副本 + 回读验证）
- 🔴 诊断纪律：先抓证据日志/实况再下结论，禁止脑补根因
