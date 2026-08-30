# 2026-08-31 · `source $CLAW/...` 自举死锁导致 L3 护栏被静默跳过

★升级候选

## 现象
统一巡检中枢（automation-1785982929477）本轮执行：
```
(eval):source:1: no such file or directory: /.workbuddy/scripts/automation_preamble.sh
(eval):1: command not found: run_l3_pre
(eval):1: command not found: run_l3_post
```
- `unified_ops_center.py` 用绝对路径调用，**自身跑完 15 项检查、退出码 0、SILENT 正常**；
- 但 `run_l3_pre` / `run_l3_post` **一次都没执行** —— 交易日校验、memwatch 校验、运行记录、失败分类、超时告警全部丢失；
- 外层 Bash 退出码 127（最后一个命令找不到）。

## 根因：自举死锁（bootstrap deadlock）
`automation_preamble.sh` 第 11 行确实 `export CLAW=/Users/guan/WorkBuddy/Claw`，但**所有自动化的调用写法是 `source $CLAW/.workbuddy/scripts/automation_preamble.sh`** ——
调用方在**展开 `$CLAW` 时就需要它**，而 `CLAW` 只有 source 成功之后才会被定义。

- `CLAW` 从未写入 `~/.zshrc` / `~/.zprofile`，一直靠 runner 环境顺带提供；
- runner 未提供时（本轮即如此），`$CLAW` 空 → 路径展开成 `/.workbuddy/...`（根目录）→ source 失败 → **护栏层静默消失**。

**危害性**：主脚本有绝对路径兜底所以"看起来一切正常"，只有 stderr 里两行 command not found 暴露问题，极易被忽略——护栏是在"没人发现"的情况下失效的。

## 修法（已执行）
在 shell 启动层兜底，而不是改调用写法（调用写法散落在几十个自动化 prompt 里，改不动）：
```bash
# ~/.zshrc 与 ~/.zprofile 各加一条（幂等，不覆盖外部传入值）
[ -z "$CLAW" ] && export CLAW=/Users/guan/WorkBuddy/Claw
```
备份：`/tmp/zshrc.bak.20260831-065543`、`/tmp/zprofile.bak.2026...`

## 三个必须记住的坑
1. **Bash 工具的环境在会话初始化时快照一次**。改完 `~/.zshrc` 后，本会话后续 Bash 调用仍看不到新变量（`BUN_INSTALL` 有值、`CLAW` 无值即证据）。验证需显式 `source ~/.zshrc`，或等下一个会话。判定"配置是否生效"时别把"本次会话没生效"误判成"改错了"。
2. **`source` 失败不会中断后续命令**。必须用 `type run_l3_pre` 自检，不能只看主脚本输出——主脚本成功 ≠ 护栏在跑。护栏 OK 的判据是 `GUARDRAIL_OK`，不是脚本退出码 0。
3. **退出码 127 是唯一自动化可见的强信号**。若发现某轮自动化整体 rc=127，第一嫌疑是 preamble 路径没解析出来，而不是脚本本身故障。

## 通用化规则
> 任何"`source $X/...` 而 `$X` 由该文件自己定义"的写法都是自举死锁。`$X` 必须在**调用方环境**里已存在，最省事的做法是写进 `~/.zshrc` + `~/.zprofile`（幂等守卫形式），而非依赖 runner 环境变量。
