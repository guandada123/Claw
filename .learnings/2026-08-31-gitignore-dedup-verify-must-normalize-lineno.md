# 2026-08-31 gitignore 去重验证必须归一化行号 + 文件系统/glob 与 git status 的差集是盲区探针

## 现象

统一巡检中枢连续多轮 git 漂移「36 项持平」，回读器也只报计数。本轮两项动作各踩到一个方法论坑、
各收获一个可复用探针。

## 一、探针：`find`/glob 计数 vs `git status` 计数的差集会暴露隐形文件

磁盘 glob `.learnings/2026-08-30-*.md` 数出 **11 个**，`git status --porcelain` 只报 **10 个 `??`**。

差的那 1 个（`2026-08-30-shared-infra-uncommitted-fix-risk.md`）查下来是**已 tracked 且无改动**，
所以 git status 不显示 —— 虚惊一场。但这个差集本身是有价值的探针：

| 差集方向 | 可能含义 |
|---|---|
| glob 多、`git status` 少 | ①已 tracked 且 clean（无害）②**被 `.gitignore` 静默拦截**（真盲区，run#32 同类） |
| `git status` 多、glob 少 | glob 模式写漏（如大小写、目录深度） |

**做法**：对任何"计数持平/偏离"的目录，都用 `find/glob 总数` 减 `git status 计数`，
差集逐个 `git check-ignore -v` + `git ls-files --error-unmatch` 定性，不要凭计数下结论。
（与 run#34「计数持平掩盖组成变化」同源，本次是它的补集方向。）

## 二、坑：gitignore 去重后直接 diff `check-ignore` 输出必然报差异（行号偏移假阳性）

删除 4 条重复 pattern 后做等价性验证：

```
diff gs_before.txt gs_after.txt   → ❌ 有差异！
  < .gitignore:137:.agents/
  > .gitignore:133:.agents/
```

**这不是语义变化，是行号偏移**。删了 4 行，其后所有规则的 `check-ignore -v` 输出行号整体 -4，
703 行 git status、618 行 check-ignore 几乎全行"变化"，直接 diff 会把一次完全安全的清理
误判为"改变了忽略语义"，从而不敢动（或更糟：不验证硬上）。

### 正确验证姿势（两层）

**第一层 · 删前断言**：每条待删行必须在前文存在**字节完全相同**的副本，否则拒绝删除。

```python
for i in sorted(drop):
    assert any(orig[j] == orig[i-1] for j in range(i-1)), f"行{i} 前文无相同内容"
```

**第二层 · 删后归一化 diff**：抹掉 `.gitignore:NNN` 行号 + 排除被改文件自身的条目，再比对。

```python
line = re.sub(r'\.gitignore:\d+:', '.gitignore:N:', line)
if re.match(r'^\s*M\s+\.gitignore\s*$', line): continue   # 自身改动是预期内
```

本轮结果：归一化后 git status 703 行、check-ignore 618 行**逐行完全一致**，重复行复扫清零 → 等价性得证。

### 通用化

凡是「输出里含源文件行号」的验证工具（`check-ignore -v`、`ruff --output-format`、
`grep -n`、pytest 的 `file.py:NN`），做**删行类改动的前后比对**时必须先归一化行号，
否则拿到的一定是假阳性差异。与 run#34「功效验证须复现故障条件」同源：
**验证器自身的输出格式也会制造假象。**

## 三、gitignore 重复 pattern 的危害（为什么要清）

```
86:  .workbuddy/automations/*/memory.md
94:  .workbuddy/automations/*/.backups/
121: .workbuddy/automations/*/.backups/      ← 重复
```

重复 pattern 在 git 里语义无害（同一 pattern 多次出现，后匹配优先但结果相同），
但对**人**是陷阱：维护者改了一处以为生效，另一处仍在 —— 属于典型的静默失效温床。
本仓 155 行里查出 4 处重复（含 1 条 `!` 白名单）。

## 四、顺带确认的两条状态（未动手，仅记录）

1. `.gitignore:86` 忽略 `.workbuddy/automations/*/memory.md`，但历史上有 **22 个 memory.md 已 tracked**
   —— gitignore 对已跟踪文件无效，属历史遗留不一致。**不动**：动了会让 22 个文件立刻变成持续 churn。
2. `.workbuddy/automations/automation- 1782137216020`（**目录名含空格**）是空目录，脚本命名 bug 产物。
   git 不跟踪空目录，无害。未删（删目录走 safety 流程，收益不抵风险）。

## 处置

- `46c75b8` 补交 10 篇 08-30 learnings（磁盘 29 / 入库 19，本批漏了 6 轮；均已核验无 TODO/截断）
- `c0db95f` gitignore 去重 4 条（155 → 151 行），双层验证等价
- 全仓漂移 36 → 26；`.workbuddy/scripts/` 内仍 3 项（长期 WIP，不动）

## 通用化

巡检类任务的「计数持平」必须通过**差集 + 集合比对**升级为「组成比对」：
- 对目录：glob 总数 vs git status 计数，差集逐个定性
- 对结果：归一化掉工具自身的行号/时间戳噪音后再 diff
