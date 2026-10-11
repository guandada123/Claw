# ★ 别在共享 checkout 里用 commit 做实验（2026-10-11）

## 现象
为证伪新工具 `check_tracked_tree.py`，我在 Claw 仓库里做了一次「临时植入违规串 → `git commit` → 跑预检 → `git reset`
丢掉」的实验。结果两处翻车：

1. `git reset --hard HEAD~1` **连带丢掉了未提交的 `Makefile` 改动**（我本想只丢那个实验提交）→ 得重做一遍；
2. 更糟：**并发会话在我那个实验提交之上继续 commit 并 push** → 那个"临时"提交 `16d7b63`
   逃逸到了 `origin/main`，带着植入的违规串（`完整 999 条`）。它的 CI 因此判红
   —— 而且是**正确地**判红：串确实违反了我当天刚加的 `tests/test_doc_volatile_claims.py`。
3. 分支 tip 随后被并发会话的 merge 修复（`AGENTS.md` 回到正确内容），但历史里留下一个红的提交。

## 根因
- **共享工作区**：本机同一 checkout 上有另一个会话在提交/推送（2026-10-11 全天如此）。
  「本地临时提交」在并发提交面前**不是私有的**：一旦有人在你之上 commit，你的 `reset` 就不再指向你那个提交，
  而对方的 `push` 会把你的实验一起带上去（`origin/main` 当天与我本地差了好几个提交）。
- **`reset --hard` 的连带性**：它丢弃**所有**未提交的 tracked 改动，不只是你想丢的那部分。

## 正确做法
- **实验在 scratch clone 里做**（本仓现在就有现成手段：`check_tracked_tree.py` 的临时 clone、
  或 `git clone --local` 一个临时树），**不要在共享 checkout 里造提交**；
- 若必须动工作区：**先 commit/stash 好真实改动**再折腾实验；丢弃实验用 `git stash` 或对指定提交 `git revert`，
  **不要用 `reset --hard` 打底**；
- 动手前先看有没有并发会话：`git log --oneline -1 origin/main` 与自己最后一次 push 对比。

## 影响与处置
- 分支 tip 内容正确（`AGENTS.md` 已恢复），那条红 CI 属**历史噪音**；
  按铁律「不 force、不压历史」，**不改写历史**，只在此记录。
- 副产品：新工具**因此完成了双向验证** —— 干净树 `41 passed` 全绿；树里存在违规串时它**判红**
  （那次红的正是我自己植入的串，说明判据真的能抓）。
