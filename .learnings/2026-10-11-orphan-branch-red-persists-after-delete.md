# ★升级候选：分支删了，`gh run list` 的 failure 记录**不会消失** —— 一条**永久假红**常驻巡检

**日期**：2026-10-11（统一巡检中枢，CI 健康检查）
**等级**：P1（不误伤生产，但让一条**永远无人能处置**的红灯常驻 → 真红灯混在同一栏里失去分辨力）
**类型**：false-positive（取证口径漏了"分支已不存在"这一态）
**状态**：已修复（`qts_pmf_health_guard.scan_ci` 加孤儿分支豁免）

---

## 1. 现象

巡检 `check_qts_pmf_ci` 长期报 **`⚠️ QTS/pmf CI: 1 项异常`**。追下去：

```
QuantTradingSystem: reds=1
  {"workflow": "Security Scan", "conclusion": "failure",
   "branch": "test/lock-gate-negative-20261011"}      ← 这分支早被删了
```

那条分支是**做负向验证时临时建的**（验证锁门禁能否拦住"红灯"），验完即删。
但 **GitHub 的 run 记录不随分支删除而消失** → 它永远留在 `gh run list` 里。

## 2. 根因（判定口径漏了一态）

`scan_ci` 原口径：按 `headBranch` 分组，取每分支最近一次 run；非默认分支**只在 PR 已
`MERGED`/`CLOSED` 时豁免**。而一次性验证分支**没有 PR** → `_pr_state_for_branch` 返回
`UNKNOWN` → **直接计入红灯**。

即：**"分支没了"是第四态，原判据只有三态**。而这一态的特征是"永久无人能处置" ——
分支已删，没有任何动作能让它变绿。

## 3. 修法

```python
if br not in DEFAULT_BRANCHES:
    if pr_state in ("MERGED", "CLOSED"):
        continue
    # 新增：分支已从远端删除（无 PR 的一次性验证分支）→ 其红灯是历史，不报
    if _branch_exists(repo, br) is False:
        continue
```

**关键分寸**：`_branch_exists` 返回 `None`（网络/权限查不到）时**维持原判、不放过** ——
"判不了"不能当成"没问题"。修后四仓 `reds=0`，且补了反向对照测试
（分支还在、只是没开 PR → **仍须报**）。

## 4. 可复用判据

> **判"这条红灯还该不该红"时，必须把"产生它的东西还在不在"算进去。**
> 一个**没有任何动作能让它变绿**的告警，不是告警，是噪音 —— 而且它会稀释同一栏里真红灯的分辨力。

同族（同一天另一个实例）：`check_disk` 的"路径不存在 → 报磁盘正常"是**反向**的同一错误
（该报的没报）；本条是**不该报的报了**。两者都在"状态空间少了一态"。
