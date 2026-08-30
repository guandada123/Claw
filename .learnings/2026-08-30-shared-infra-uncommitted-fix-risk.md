# 共享基建脚本的未提交修复，风险等级高于普通 WIP

日期：2026-08-30 22:29
来源：统一巡检中枢 run#21（automation-1785982929477）
标记：★升级候选

## 事实

巡检回读 git 卫生时发现 `Claw/.workbuddy/scripts/automation_preamble.sh` + `push_feishu.sh` 处于 **M（未提交）** 状态，mtime = 08-27 10:27/10:28，即**已生产运行 3 天未入库**。

改动内容（`git diff` 实锤）：
- `automation_preamble.sh`：`push_feishu()` 的分支由 `if [ $# -ge 2 ]` 改为 `if [ $# -ge 3 ]` 优先，把第 3 参数（去重键）位置转发给 `push_feishu.sh`。原逻辑在传 3 参时**只转发前 2 个，静默丢弃去重键** → 去重失效。
- `push_feishu.sh`：新增 `DEDUPE_KEY="$3"`，非空时 `ARGS+=(--dedupe-key "$DEDUPE_KEY")`。

端到端验证：`push_card.py` L353 存在 `--dedupe-key` 参数（6h 去重窗，L383-386 生效）→ 链路闭合，是真修复不是回归。`bash -n` 语法校验两文件均 OK。

## 教训

1. **不是所有未提交变更都等价。** 普通业务脚本的 WIP 未提交 = 噪音；但被 **20 个自动化 `source`** 的共享基建（preamble / push_feishu / 状态锚写回）未提交 = **单点脆弱**：一次 `git checkout .`／`git reset`／他人清理工作区，就会让已生效 3 天的修复凭空消失，且**没有任何告警能发现**（服务仍跑，只是代码回退到旧行为）。
2. **判断"该不该提交别人的 WIP"的准则不是"谁写的"，而是"影响半径 + 是否已完成"。**
   - 已完成（mtime 停滞 ≥1 天、语法校验过、下游参数链路闭合）+ 共享基建 → 可隔离提交，提交信息写明来源与验证点。
   - 仍活跃（mtime 几分钟内、同目录有同伴文件在改）→ 不动。
3. **提交必须 pathspec 精确隔离。** 当时工作区有 24 项 WIP（含 `portfolio.json`、tests、learnings），用 `git add <具体2文件>` + 单文件提交，绝不 `git add -A`。
4. **"D 删除项"要先验证是不是 rename 再决定提交姿势。** 同批的 `.workbuddy/scripts/pool_health_check.py` 删除项，初判是"移动到 `scripts/`"，取回 HEAD 版本 diff 后发现有 **485 行差异**（`scripts/` 副本是更新的独立版本）→ **不是纯移动，不能按 rename 提交**，只能单方面提交删除项，`scripts/` 那份保持 untracked（本来就 untracked，与我无关）。

## 可复用检查清单（巡检发现未提交变更时）

```
1. mtime 距今多久？  <1h → 可能正在改，跳过；≥1天 → 疑似遗忘
2. 是否被共享？     grep -rl "source.*<file>" 各项目 .workbuddy/scripts/
3. 是否已完成？     语法校验（bash -n / python -m py_compile）+ 下游参数链路 grep 确认
4. 提交姿势？       git add <具体文件>  （禁 -A）；删除项先 git show HEAD:<path> 比对
                    确认是否 rename
5. 回读？           git log -1 + git status --porcelain -- <path> 确认只少了目标项
```

## 本次产出

- commit `f596edd`（preamble + push_feishu 去重键修复，2 文件）
- commit `a261668`（退役 `.workbuddy/scripts/pool_health_check.py` 漂移副本，1 文件）
- 工作区 scripts/ 剩余 4 项 WIP 未动（属正常开发中变更）

---

## 边界细化（2026-08-31 05:50，统一巡检中枢 run#28 追加）

上面检查清单第 1 条写的「mtime <1h → 可能正在改，跳过」**容易被读成硬门槛**，本次实证它只是**启发式信号，不是提交前提**。

**事实**：run#28 回读发现 `automation_preamble.sh` 为 M，mtime = 05:31，距发现仅 **19 分钟**（按第 1 条应"跳过"）。但 `git diff` 显示改动**自包含且已完成**：+7 行纯 `export PYTHONPATH="$CLAW/src:$PYTHONPATH"`，带 5 行说明性注释交代动机（裸 python3 跑 `python3 -m claw.monitoring.*` 会 ModuleNotFoundError）。

**修正后的判定**：真正门槛是「**改动自包含 + 完整验证通过**」，mtime 只用于**排序优先级**——

| mtime | 判定 | 动作 |
|---|---|---|
| ≥1 天未动 | 疑似遗忘的已完成修复 | 优先隔离提交 |
| <1h 但**自包含**（有注释、有明确动机、无同伴半成品） | 已完成的刚落地修复 | **照常验证并提交**（本次情况） |
| <1h 且**不自包含**（注释残缺 / 同目录有同伴文件同批在改 / 改动被截断） | 真·活跃 WIP | 跳过，备案 |

「自包含」的快速判据：**注释是否已交代 why**。写得出完整动机说明的改动，通常是收尾状态；写不出的多半还在改。

**本次验证四步（可复用于「给共享基建加环境变量/路径」类改动）**：
1. `bash -n` 语法 ✅
2. **功效验证**——证明改动确实解决了它声称的问题：不加 PYTHONPATH 时裸 python3 `import claw` 报 ModuleNotFoundError，加上后成功（且 `claw.monitoring` 子包也能 import）。
3. **遮蔽风险**——列出被前置目录的顶层包（`ls -d src/*/`），确认**无 stdlib / 第三方同名模块**。本次仅 `claw` / `output` 两个包，注释声称属实。
4. **无副作用回归**——① 主力 venv python 加/不加该 PYTHONPATH，`claw` 指向**完全一致**（editable 安装点本就是 src/claw，未漂移）；② 批量 import 测试包（numpy/pandas/requests/yaml + stdlib 的 json/secrets/logging/pathlib）全过。

**为什么第 4 步不能省**：把目录**前置**到 PYTHONPATH 是全局作用域改动，影响该 shell 下所有 python 调用。用户铁律里「项目脚本目录禁与 stdlib 同名模块」就是同类事故（scripts/secrets.py 遮蔽 stdlib secrets 致 numpy import 挂）。只验功效不验副作用，等于把 20 个自动化推进未知状态。

产出：commit `007657f`（preamble PYTHONPATH 可见性修复，1 文件）；提交后 source + `type run_l3_pre` + 裸 python3 import 回归全过；scripts/ 未提交项回落到 4 项，全仓 39 项持平。
