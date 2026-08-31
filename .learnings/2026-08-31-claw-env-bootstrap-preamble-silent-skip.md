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

---

## ⚠️ 08-31 08:00 追加修正：上面的"修法"不完整，`.zshrc` 选错了文件

### 反例实证
run#29 写入 `.zshrc` / `.zprofile` 后，**07:45 的巡检存活看门狗实测仍为 `CLAW=[UNSET]`**；
08:00 本轮巡检会话里 `CLAW` 又**有值**。→ 间歇性失效，`.zshrc` 兜底对自动化 runner **不成立**。

### 根因：三类启动文件的读取时机完全不同
| 文件 | 交互式 | 登录式 | 非交互非登录（自动化 runner 典型形态） |
|---|---|---|---|
| `~/.zshenv` | ✅ | ✅ | ✅ **每次 zsh 调用都读** |
| `~/.zprofile` | ❌ | ✅ | ❌ |
| `~/.zshrc` | ✅ | ❌ | ❌ |

自动化 runner 多为**非交互非登录** shell → 只认 `.zshenv`。
实测：`env -u CLAW zsh -c 'echo $CLAW'` 在只写 `.zshrc` 时为 UNSET，写入 `.zshenv` 后立即有值。

> **规则：给"任何调用方式都要生效"的环境变量兜底，唯一正确的落点是 `~/.zshenv`。**

### 但 `.zshenv` 也只覆盖 zsh
实测 `env -u CLAW bash -c 'echo $CLAW'` 仍为 UNSET（bash 不读 `.zshenv`，且非交互 bash 也不读 `.bashrc`）。
宿主环境不可控时，**不能指望任何 rc 文件**。

### 最终修法：两层防御
**第一层（环境层）** — `~/.zshenv` 幂等兜底，覆盖 zsh runner：
```bash
[ -z "$CLAW" ] && export CLAW=/Users/guan/WorkBuddy/Claw
```

**第二层（调用层，关键）** — prompt 里用「文件存在性校验 + 兜底绝对路径」，与 shell 无关：
```bash
[ -f "${CLAW:-}/.workbuddy/scripts/automation_preamble.sh" ] || export CLAW=/Users/guan/WorkBuddy/Claw
source "$CLAW/.workbuddy/scripts/automation_preamble.sh"
```

### 为什么用 `-f` 校验而不是 `${CLAW:-默认值}`
| 写法 | CLAW 未定义 | CLAW 为空串 | CLAW 指向错误路径 |
|---|---|---|---|
| `source "$CLAW/..."`（原写法） | ❌ 解析到根目录 | ❌ | ❌ |
| `source "${CLAW:-默认}/..."` | ✅ | ✅ | ❌ `:-` 只在 unset/empty 生效 |
| `[ -f ... ] \|\| export CLAW=默认`（采用） | ✅ | ✅ | ✅ |

实测 7 组场景全过（未定义/空串/错误路径/正确值 × zsh/bash/sh + 幂等连跑两次），均 `GUARDRAIL_OK`。

### 三个连带教训
1. **修完必须验证"故障条件真的被解除了"，不是验证"正常路径没坏"**。run#29 验证的是"source 后能用"，而故障条件是"CLAW 没有" —— 方向反了，所以漏掉了 `.zshrc` 不生效。正确姿势：`env -u CLAW <shell> -c '<原文照抄的引导片段>'`。
2. **rc 文件选择要按"目标进程是什么 shell 形态"来定，不能凭直觉**。交互式验证通过 ≠ 非交互式通过。
3. **改 rc 文件属于"跨会话生效"的改动，本会话验证有天然盲区**（Bash 工具环境在会话初始化时快照一次）。要么显式 `source`，要么用 `env -u` 起全新子进程测，别在当前 shell 里直接看。

### 待办
其余 ~19 个自动化的 prompt 仍是裸 `source $CLAW/...`，同样暴露在该风险下。
建议模板（已在本巡检中枢 prompt 落地）：
```bash
[ -f "${CLAW:-}/.workbuddy/scripts/automation_preamble.sh" ] || export CLAW=/Users/guan/WorkBuddy/Claw
source "$CLAW/.workbuddy/scripts/automation_preamble.sh"
```
批量改写属跨自动化配置变更，本轮**未擅自执行**，交用户决定。
