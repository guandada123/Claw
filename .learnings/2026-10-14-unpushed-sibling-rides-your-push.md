# ★升级候选 —— 「未 push」的兄弟提交会骑乘你的 push 进 CI：契约变更必须同步搜测试依赖点

**日期**：2026-10-14（PA-022 提交 29e3999 触发的 CI 首曝失败复盘）
**等级**：P2（CI 红 + 一次跨会话返工；无生产影响——功能改动本身是合理的）
**类型**：multi-session-push-interference / contract-change-missed-test-sync
**状态**：已修复（测试补 patch LOG_DIRS，commit `2d7feaa`）；本仓多会话并行是设计常态，此类连锁属系统性风险点。

---

## 1. 事故链（逐环可复现）

1. **03:41 记忆维护会话**：改进 `memory_headroom_check.py` —— main() 的日日志扫描从
   「`[t.parent for t in TARGETS]` 派生」改为模块级 `LOG_DIRS`（改进本身合理：三份
   MEMORY.md 中两份父目录没有日日志，派生会多出两行「日日志 0 个」恒零噪音）；
2. 该变更**未同步更新**依赖旧契约的测试 `test_daily_logs_do_not_change_rc`
   （它只 patch 了 `TARGETS`，扫描目标不变 → temp 场景的异常永不触发）；
3. 会话以「未 push」标注提交（`10b9251`）留在本地 —— **未跑全量测试**；
4. **07:41 我的 PA-022 提交**（`29e3999`）被 push 时，`10b9251` 骑乘同批进入远端；
5. CI **首曝**：`1 failed, 1018 passed`。red 落在我的提交头上，实际是兄弟提交的未测变更。

## 2. 根因与教训（待周度升级为铁律）

1. 🔴 **改契约必搜测试依赖点**：任何「某模块级常量 X 改为 Y / 函数签名变化」的改动，
   提交前必须 `grep -n "TARGETS\|该函数名" tests/` 找齐依赖点同步更新——
   **只跑自己的测试 = 把回归债留给下一个 push**。（本例：功能合理、测试漏改，一步之遥。）
2. 🔴 **本仓 push 是「合流事件」不是「单人事件」**：多会话并行下，
   `git log origin/main..HEAD` 里常有非本会话提交。**push 前看一遍这段清单**，
   对每个非己提交自问「它进过 CI 吗」（commit message 里的「未 push」= 明确信号），
   预计会被 CI 检验的内容先本地跑一遍相关测试。
3. ⚠️ **「未 push」提交的规矩**：本仓惯例允许「提交但不 push」（防丢工作），
   但代价是把 CI 首曝风险转移给了下一个 push 者。建议：未 push 的提交若含
   **代码/测试改动**（非纯文档），要么先本地跑全量 `pytest tests/ -q`，
   要么在 commit message 里点明「需先跑 XX 测试」。
4. ⚠️ 修复方向总是**改测试跟随新契约**（不为迁就测试回退功能改动）——除非新契约本身
   有缺陷。本次判定：LOG_DIRS 显式化正确，测试错在漏改。

## 3. 已执行

- 测试补 `monkeypatch.setattr(mhc, "LOG_DIRS", [memfile.parent])`（含 4 行注释说明契约变更史）；
- 验证：单文件 19/19、memory 全组 46/46、ruff 干净；commit `2d7feaa`。
