# ✅已升级(2026-10-11)→R75 —— 「取不到证」被渲染成「通过」—— 巡检里 4 个检查同款，且**探针跑出的数字不能直接当结论**

**日期**：2026-10-11（统一巡检中枢排查）
**等级**：P1（不报错、不告警，但**该发现的都没发现**：外置盘掉了、401 风暴、写入连续失败都可能被 ✅ 掩盖）
**类型**：silent-pass（取证失败 ≠ 通过）
**状态**：已修复 4 个检查 + 已加结构性守卫（`c2955d3`）

---

## 1. 现象

统一巡检中枢（`unified_ops_center.py`，20 项检查）里有一族检查的取证方式是
**「从命令输出里找坏消息」**（`df` 看使用率、`docker logs` 找 401/写入失败、`bandit` 看高危数）。
一旦**命令本身拿不到输出**，"没找到坏消息"就等价于"通过"：

| 检查 | 失败情形 | 原行为 |
|---|---|---|
| `check_disk` | df 失败 / **路径不存在** / 输出不可解析 | 报"磁盘正常" |
| `check_qts_api_auth` | docker logs 失败 | 报"没找到 401" = 通过 |
| `check_qts_job_write_health` | docker logs 失败；**无日志输出** | 前者静默；后者**显式写着 ok=True** |
| `check_code_quality` | bandit 没跑起来 | 只记一条软 note（"可能未装"）→ 仍 ✅ |

最要紧的是 `check_disk`：

```python
for path in ["/Volumes/ZHITAI"]:
    if not os.path.exists(path):
        continue                     # ← 外置盘不在，直接跳过
    r = run_cmd(["df", "-P", path])
    if r.returncode == 0:            # ← 失败没有 else
        ...解析...
return {"ok": len(alerts) == 0}      # → 报"磁盘正常"
```

`/Volumes/ZHITAI` 是**三个项目所在的外置盘** —— 它不在是件大事。

## 2. 探针：让所有外部命令失败，看谁还敢报 ✅

```python
u.run_cmd = lambda *a, **k: _Fail()   # rc=127、stdout 空
for name in <所有 check_*>: print(name, getattr(u, name)().get("ok"))
```

首轮：**10/20 报 ok=True**。⚠️ **但这个数字不能直接当结论** ——

## 3. 关键一步：先核定探针的范围，再判「按设计」还是缺陷

1. **核范围**：探针只 patch 了 `run_cmd`，那些**直读 DB/文件**的检查根本没被影响。
   用 AST 取每个 `check_*` 的函数体数 `run_cmd(` 出现次数 → 真正依赖的只有 **14/20**，
   其余 6 个报 True **与探针无关**。
2. **带上下文判**：`check_automation_failures` 的 ✅ **是按设计** —— 它的 docstring 写明
   它只做"趋势可见性"、绝不返回会触发中枢推送的 alert（避免与唯一推送方
   `automation_failure_watchdog` 双推）。**判一个行为是不是缺陷，要读它自己的理由。**

净得 4 个真缺陷。

## 4. 修法与守卫

- 4 处统一改为 `ok=None` + `取不到证：<具体原因>`（与中枢既有口径一致：跳过不计入 ✅）。
- **结构性守卫**（`tests/test_ops_center_core.py`）：

  ```
  test_no_check_claims_pass_when_every_command_fails
    ① 用 AST 找出所有"函数体里调用过 run_cmd"的 check_*
    ② 把这些命令统一弄失败
    ③ 断言除白名单外不得返回 ok=True（白名单须逐条写明理由）
  ```

  用 AST 而非硬编码名单：**将来新加的检查自动纳入**，守卫不会随代码腐烂。
  另配 `test_guard_would_catch_a_regression` 自证守卫不是"看着在跑其实没查"。

- **双向验证**：失败向 → 4 个都 `ok=None`；正常向 → 真实环境四个仍 ✅，巡检总账未变。

## 5. 可复用判据

> **只要一个检查的结论来自"没找到坏消息"，就必须回答：那"找不到"是因为真的没有，
> 还是因为我根本没看到？** 后者一律 `ok=None`（取不到证），不许渲染成通过。

同族已知实例：`测试: ? passed`（拿不到 pytest 统计行时落成问号，渲染出来像绿）。
