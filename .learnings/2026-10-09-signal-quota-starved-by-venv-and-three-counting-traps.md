# 采集器配额被第三方产物吃光 + 三个「数出来的是假的」计数陷阱

**日期**：2026-10-09（统一巡检中枢 v1.10.2 自审）
**等级**：P1（发现面长期产出假信号，真实项目的痛一条都扫不到）
**类型**：false_positive_generator / counting_trap
**状态**：已修复 + 已补 4 项回归测试

---

## 1. 现象

`project_signal_collect.py`（v1.10 新增的「项目自身痛点」轨）输出：

```
QTS: 20 条信号  {"todo": 20}
```

看起来「QTS 有 20 个待办」。逐条看 —— **20/20 全部来自**
`QuantTradingSystem/.venv/lib/python3.13/site-packages/_pytest/*.py`。

真实项目文件只有 **279** 个 py；含 `.venv` 时 glob 命中 **14,343** 个。

## 2. 根因（三层，逐层剥开）

### 2.1 排除缺失
`collect_todos()` 只过滤「非文件 / 超 200KB」，**不排除** `.venv` / `site-packages` /
`node_modules` / `__pycache__` / `build` / `dist`。

### 2.2 排除发生在配额**之后**（真正的致命点）
`max_files` / `max_hits` 是「有界扫描」的配额。原实现先 `scanned += 1`、
再判断内容 —— 于是第三方文件**先占配额**。

### 2.3 `sorted()` 的字典序把 `.venv` 排在最前
`root.glob("**/*.py")` 后 `sorted()` → `.venv/...` 以点号开头，字典序最前
→ 20 条 `max_hits` 配额被 `_pytest` 的 `XXX/TODO` **全部吃光**，还没走到 `services/`。

**这与既有铁律同源**：🔴 检测/守护逻辑「静默失效」三坑之一 ——「裁剪窗口被单噪声源占满」。
反过来，数量**虚高**同样是口径坏了（不只骤降要怀疑口径）。

### 2.4 修法
```python
EXCLUDE_DIR_PARTS = frozenset({".venv","venv","env","site-packages","dist-packages",
    "node_modules",".git","__pycache__","build","dist",".tox",".nox",".eggs",
    ".mypy_cache",".pytest_cache",".ruff_cache",".idea",".vscode","vendor",
    "third_party","thirdparty","egg-info"})

def _is_excluded(p: Path) -> bool: ...
```
- 排除**前置于** `scanned`/`hits` 计数（关键）
- 输出新增 `per_project[].third_party_excluded` —— **「0 信号」与「全被排除」必须可分**
  （否则某项目 glob 写错根本扫不到时，报告长得跟「项目很健康」一模一样）

**修复后**：signals 31 → 12；QTS 20 → 1 条真实 TODO（`advanced_risk_rules.py:448`），
`third_party_excluded=9167`。

---

## 3. 同一批自审里撞到的另外两个「计数陷阱」

### 3.1 `ls <glob>` 无匹配时**回退列当前目录** → 假的「原地还剩 29 个」
归档脚本里：
```bash
b2=$(ls scripts/*.bak* | wc -l)     # 移动前 = 10
for f in scripts/*.bak*; do mv "$f" scripts/_archive/; done
a2=$(ls scripts/*.bak* | wc -l)     # 无匹配 → ls 退化为「列 cwd」→ 29 ！
```
`ls` 带不存在的 glob 时会当普通参数处理；参数一个都不存在时，**`ls` 直接列当前目录**。
于是「应该 0」变成「29」，看起来像移动失败。

**判据**：`find <dir> -maxdepth 1 -name PAT -type f | wc -l`（无回退行为），
或 `ls -1d PAT 2>/dev/null | wc -l`。**永远用 `-d` 或 find 计数。**

### 3.2 `git commit -m "..."` 双引号里的**反引号被命令替换**，静默丢字
提交消息里写了 `` `references/ 增补 xxx.md` ``、`` `(+N 条过闸但被 top_n 限流)` ``。
双引号内反引号 = 命令替换 → 执行后返回空串 → **消息里这两段文字凭空消失**，
`git commit` 仍然 rc=0、看起来「提交成功」。
只留下两条 stderr：`no such file or directory: references/`、`command not found: +N`。

**修法**：多行/含反引号的提交消息一律用 `git commit -F - <<'EOF' ... EOF`
（单引号 heredoc，不做任何展开）；提交后用 `git log -1 --pretty=%B` **回读校验**。
——「日志成功 ≠ 内容落库」的又一实例。

---

## 4. 规则候选（可升级为铁律）

1. **有界扫描的配额必须「先排除、再计数」**：排除逻辑若在计数之后，等于没排除。
2. **采集器必须输出「被排除数」**：只有「0 信号」与「全被排除」可分，才不会把
   「glob 没命中」误读成「项目很健康」。
3. **计数一律避免 `ls <glob>`**：无匹配会回退列 cwd，产生看起来合理的假数字。
   用 `find -maxdepth 1 -name PAT` 或 `ls -1d PAT 2>/dev/null`。
4. **含反引号/多行的 git 提交消息用 `-F -` + 单引号 heredoc，并回读校验**。

## 5. 影响与验证

- 修复后真实项目候选首次进入队列：`disc-20261009-05`（Claw，已落地）、
  `disc-20261009-06`（Claw，中债收益率静默空返，待审）
- 新增 4 项回归测试（`tests/test_discovery_v110.py`）：第三方目录排除、
  计数器语义（`third_party_excluded`）、显式 `doc_target` 优先、
  `top_n` 限流逐条展开 —— 共 22 项全绿
- 提交：Claw `7dcd0ae`（v1.10.2）
