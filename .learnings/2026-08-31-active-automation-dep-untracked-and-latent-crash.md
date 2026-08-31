# 回读「未跟踪(??)」项不等于无害：在跑自动化的依赖可能根本没入库，且带首日崩溃 bug

- **日期**: 2026-08-31
- **项目**: Claw（统一巡检中枢 run#35 回读环节）
- **级别**: ★升级候选（回读/漂移类通用盲区；untracked ≠ 他人 WIP，可能是活跃依赖）

## 现象

巡检中枢的回读环节逐项列出 git 漂移，scripts 目录长期稳定为 3 项 ` M`（他人 WIP），
根级配置无变更 —— 按 run#34 教训逐文件名比对后确认**组成未变**，本可照例判定「无新增、不处置」。

但把视野从 `.workbuddy/scripts/` 挪到**全仓 untracked(??) 项**时，发现：

```
?? scripts/sync_claw_to_qts_portfolio.py    mtime 2026-08-27 18:02   3294 B
```

顺着查调用方：

- `automation-1787758708068`「Claw→QTS 模拟盘 portfolio 同步」**ACTIVE，RRULE 每日 15:10**，
  prompt 里写死的命令就是 `python3 /Users/guan/WorkBuddy/Claw/scripts/sync_claw_to_qts_portfolio.py`
- 而该文件在仓库里 `??`（untracked），**从未入库**，且全仓 grep 无任何 import / 调用方
  （唯一引用者就是自动化 prompt 里的绝对路径，不在仓库内）

## 为什么此前一直没发现

| 惯性认知 | 事实 |
|---|---|
| 回读盯 tracked 的 ` M` 就够了 | 活跃依赖可能是 `??`；`git checkout/reset` 删不掉，但 `git clean -fd` 会 |
| 「全仓 grep 无调用方」= 死代码 | 调用方可能在**自动化 prompt**（平台侧），不在仓库里 → grep 零命中 ≠ 无活依赖 |
| scripts/ 里 untracked 是项目惯例 | 实测 60 个 .py 中 **57 个已跟踪**，untracked 只有 3 个 —— 是例外不是惯例 |

## 连带发现：首日必崩的隐藏 bug

读代码时发现 `shutil.copy2(DST, bak)` 在**目标不存在**时会抛 `FileNotFoundError`：

```python
if DST.exists():            # 幂等判断，目标不存在则跳过
    ...
shutil.copy2(DST, bak)      # ← 未做存在性保护，首日/目标被清理时必崩
```

而自动化 prompt 约定「退出码非 0 或输出含 [ERROR] → 立即告警」，
即**首次部署或 QTS shared 目录被清理时，会用一个 traceback 误报告警**。
已跑 3 天没暴露，只因为目标文件恰好一直存在 —— 典型的「没炸过≠没问题」。

## 处置

1. **入库**（按 run#21 规则 pathspec 精确提交，不卷入其余 34 项 WIP）
2. **修 bug**：备份改为条件执行；回滚抽成 `_rollback()`，按
   「有备份 → 还原 / 无备份（原本不存在）→ 删除」恢复写入前状态，不留半成品
3. **docstring 同步**：补记目标缺失时的行为，避免注释与代码脱节（run#18 同类教训）

## 验证姿势（关键：复现故障条件，不是验证正常路径）

写 `verify_sync_fix.py`，把 SRC/DST/BACKUP_DIR 三个硬编码路径**重写到临时目录**后，
对**旧版备份**与**新版**跑同一组 4 个场景对照：

| 场景 | 旧版 | 新版 |
|---|---|---|
| A) DST 缺失（故障条件） | ❌ rc=1 `FileNotFoundError` | ✅ rc=0，目标已写入 |
| B) 内容不同 → 同步 | ✅ | ✅ |
| C) 内容一致 → 幂等跳过 | ✅ | ✅ |
| D) 源缺失 → rc=1 + [ERROR] | ✅ | ✅ |

**旧版只在 A 失败、新版 A 通过、B/C/D 两版一致** —— 这才是有效对照。
若只跑新版且全绿，无法证明「改动就是修了 A」（run#30/34 同源教训）。

验证后回读真实文件 mtime 未变化，确认临时目录重定向生效、未污染生产数据。

## 通用检查清单（回读 untracked 项时）

1. `??` 项**逐个查身份**：mtime 多旧？头部 docstring 说自己是干什么的？
2. **查活调用方要跨出仓库** —— 用 `automation_update mode=list` 找名字对得上的自动化，
   再看 prompt 里是否写死了该绝对路径；仓库内 grep 零命中**不能**证明无依赖
3. 判据：是「活跃自动化的唯一依赖」→ 属共享基建，按 run#21 规则提交，
   不适用「他人 WIP 不处置」的豁免
4. 例外：`legacy_secrets.py` 已被 `.gitignore:98` 显式忽略且密钥走 env → **不动**；
   `pool_health_check.py` 是 run#21 判定保留 untracked 的权威副本 → **不动**
5. 对要提交的脚本**顺手读一遍异常路径**：文件不存在 / 目录不存在 / 解析失败 三类最易漏保护

## 一句话

**回读看到 `??` 别默认「别人的临时文件」** —— 在跑的自动化可能正指着它，
而它既没进版本控制，还可能带着一个「没炸过」的隐藏 bug。
