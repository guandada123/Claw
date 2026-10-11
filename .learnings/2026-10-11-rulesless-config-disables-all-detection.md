# ★升级候选：「没有规则的规则文件」会**静默关闭全部检测** —— CI 密钥扫描空跑 68 天

**日期**：2026-10-11（全项目审计）
**等级**：P0（安全检测能力实际为零，且 CI 显示绿灯）
**类型**：self-checker-blind（检测器自身失效 / 假性安心）
**状态**：已修复（`.gitleaks.toml` 补 `[extend] useDefault = true` + 自定义规则 + 6 例防回归测试）

---

## 1. 现象

Claw 的 Secret Detection (gitleaks) job **一直绿**，但 `macro_data.py` 里明文硬编码的
Wind MCP Bearer token 在**公开仓库**里躺了 73 天没被任何扫描拦住。

## 2. 根因

`.gitleaks.toml` 只有 `[allowlist]`，**没有 `[[rules]]`、也没有 `[extend]`**。
而 gitleaks 会**自动发现**仓库根的同名配置文件；一旦使用了自定义 config，默认规则集
**不会**被加载（除非显式 `[extend] useDefault = true`）。

→ 该文件等价于「**规则集为空**」→ 扫描永远 0 命中 → 绿灯是假性安心。

## 3. 实测证据（同目录、同文件，A/B/C 对照）

| 组 | 配置 | 结果 |
|---|---|---|
| A | 无 `-c`（自动发现 rules-less config） | **0 命中** |
| B | `-c` rules-less config | **0 命中** |
| C | `-c` 带 `useDefault = true` | **2 命中** |

另证：默认规则**不覆盖** `Bearer ak_...` 形态（纯默认规则对含该 token 的文件 0 命中），
故补了自定义规则 `wind-mcp-bearer-token`。

## 4. 判据与动作

- 判据：**「扫描绿灯」必须能被证伪** —— 拿一个已知含密钥的样本试扫，若 0 命中则检测器失效。
- 动作：`[extend] useDefault` 常驻 + 自定义规则 + 全历史复扫（575 commits → no leaks）
  + 6 例防回归测试（其中 2 例直接断言 config 结构，防有人「清理配置」时又删掉 useDefault）。

## 5. 同族与推广

- 同族教训：`self-checker-audit`（检查器自身失效）、「无输出≠不存在」、「脚本存在≠被调度」。
- **推广**：任何「自定义配置外挂到检测工具」的场景（linter / 扫描器 / 门禁）都要回答一句：
  **「这份配置是替换默认规则，还是叠加？」** 替换型配置必须先证明「默认规则还在」。
- 关联：`.learnings/2026-10-09-index-db-had-no-refresh-task-at-all.md`（同类：机制在、驱动没了）。
