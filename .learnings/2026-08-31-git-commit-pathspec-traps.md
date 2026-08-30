# git 提交命令的三个参数陷阱（2026-08-31）

★升级候选

## 触发场景
在 shell 中一次性提交单个文件，带多行说明。连续三次失败，每次报错都不同、都指向"文件不存在"，
极易误判为「文件没 add 成功 / 路径写错 / 仓库损坏」。

## 三个陷阱（按踩的顺序）

### 1. 双引号里的反引号 → shell 命令替换
```bash
git commit -- path -m "第3条：\`通道@服务\` 是合法 key"
```
反引号在**双引号内仍会被执行**为命令替换，消息被截断，后续参数（含 pathspec）被吞掉，
报 `pathspec '...' did not match any file(s) known to git`。
**修法**：提交消息里一律不用反引号，改用中文引号「」或普通引号；或写 -F 文件。

### 2. `git commit --only` 之后所有参数都是 pathspec
```bash
git commit --only -- path -m "msg"     # ✗ -m 被当成文件名
```
`--only` 会让其后**全部**内容按 pathspec 解析，`-m` 失效。
**修法**：不写 `--only`；用 `-m msg -- path` 的顺序。

### 3. `-F` 文件必须在仓库内
```bash
git commit -F /tmp/msg.txt
# fatal: '/tmp/msg.txt' is outside repository at '/Volumes/ZHITAI/WorkBuddy/Claw'
```
工作区在 `/Volumes/ZHITAI/...`（与 `/Users/guan/...` 同 inode 别名），git 认的是 `/Volumes/...` 路径，
`/tmp` 在仓库外被拒。
**修法**：消息文件写到仓库内再删，或直接用 `-m`。

## 固化写法
```bash
cd /Users/guan/WorkBuddy/Claw
git add -- <精确路径>
git commit -m "单行标题" -m "多行正文，不含反引号" -- <精确路径>
```
验证回读：`git log --oneline -2` + `git status --porcelain -- <目录>`。

## 关联
- run#21 学习：`共享基建未提交 ≠ 普通 WIP` → 完成后应 pathspec 精确提交。本次即按此规则提交
  `unified_ops_center_readback.py`（`d5b0272`）。
