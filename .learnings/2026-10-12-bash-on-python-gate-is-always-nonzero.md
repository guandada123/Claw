# 2026-10-12 · `bash <python脚本>` 当门禁 = 必然非 0 = 静默跳过 ★升级候选

## 事故形态（我自己造的）
昨天新建的三条自动化（价格刷新 15:12 / 板块刷新 15:20 / 处置触发器 15:45），
交易日门禁写成：

```bash
Bash: [ "$(bash $CLAW/scripts/is_trading_day.py >/dev/null 2>&1; echo $?)" = "0" ] && echo "交易日" || { echo "[SILENT] 非交易日"; exit 0; }
```

`is_trading_day.py` 是 **Python** 文件，用 `bash` 执行它 = 让 bash 去解析 Python 源码 →
必然报错退出（实测 **rc=2**）→ 判断条件永远不成立 → 走 `||` 分支 →
**每个交易日都打印「[SILENT] 非交易日」并 exit 0**。

后果：三条自动化**每天静默跳过、且状态是 success** —— 从日志到结果全是"绿的"，
只有"该刷新的数据一直没刷新"这一个事实是错的。**这正是本系统最高频的事故类。**

## 为什么差点漏过去
- 写出这条命令时，我的注意力在"门禁语义"（rc=0 才算交易日）上，**没有验证"这个命令到底返回几"**；
- 同仓其它自动化的写法是 `python3 scripts/is_trading_day.py`（正确），我照抄了**结构**却把解释器写错；
- `>/dev/null 2>&1` 把 bash 的报错也吞了 → 看不到 "SyntaxError: ( unexpected"。

## 判据（可机器检出，已实跑）
```bash
# 扫全部 ACTIVE 自动化 prompt 的实际命令行：不得出现 `bash <某个 .py>`
python3 - <<'PY'
import sqlite3, os, re
con = sqlite3.connect('file:' + os.path.expanduser('~/.workbuddy/workbuddy.db') + '?mode=ro', uri=True)
bad = 0
for n, p in con.execute("SELECT name,prompt FROM automations WHERE deleted_at IS NULL AND status='ACTIVE'"):
    for l in (p or '').splitlines():
        if l.strip().startswith("Bash:") and re.search(r"bash\s+\S*\.py", l):
            print("❌", n, l[:90]); bad += 1
print("真实存在的 bash <py> 命令行:", bad, "（应为 0）")
PY
```
⚠️ 注意不要用 `prompt LIKE '%bash %is_trading_day.py%'` 这种粗匹配 ——
修复说明里会**引用原文**（"原写 bash …"），会把修复后的自动化误报为未修（实测踩过）。

## 铁律（拟升格）
1. **解释器必须与文件类型匹配**：`.py` 一律用 `$PYTHON`/`python3`；`bash` 只用于 `.sh`。
2. **任何"退出码门禁"必须先实测一次它的 rc**（尤其在被 `>/dev/null 2>&1` 吞掉输出时）：
   `cmd >/dev/null 2>&1; echo $?` —— 打印出来的数字才是判据，不能靠推断。
3. **静默跳过的自动化必须留"为什么跳过"的可核对痕迹**：本次三条每天打
   `[SILENT] 非交易日`，但没人核对"它到底是不是非交易日" → 建议在跳过分支里带上
   **判据值**（如 `rc=2`）而不是只写结论，让异常形状可被发现。

## 影响与处置
- 影响面：**3 条自动化**（全部为我 2026-10-11 新建），均**尚未执行过**（15:12/15:20/15:45 未到）→ 零实际损失。
- 处置：三条 prompt 的门禁改为 `$PYTHON $CLAW/scripts/is_trading_day.py`；
  实测 `python3 scripts/is_trading_day.py` 今日 rc=0（交易日）✅；三个目标脚本 `--help` 均可用 ✅。
- 检出：全库扫描确认 **ACTIVE 自动化里已无 `bash <py>` 命令行**（0 条）。
