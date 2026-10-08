# CI 红 4 周的真因是「两个 job 各自红」+「红了会掩盖下游 job」+「测试写死本机路径」

日期：2026-09-28（用户转来 🔧 多仓库周报 → 处置 P0「Claw main CI 连续第 4 周红」）
级别：P0（CI 连续 4 周失去守门能力；下游测试 job 被静默跳过 4 周）
✅已升级(2026-10-04)（三条已升铁律）

## 现象

周报只给了一句「CI 连续第 4 周红」，并附历史根因猜测（ruff noqa:S608→nosec B608）。
实际去拉日志后，**红的是两个互不相干的 job**，而且**第三个 job 根本没跑过**。

## 真因（三层，逐层剥开）

### 1. 两个独立红灯，各自需要不同的修法
| job | 报错 | 真因 |
|---|---|---|
| Pre-commit Hooks | exit 1 + 一屏 diff | `ruff format --check` **33 文件欠账** + `ruff check` 12 条 |
| Security Scan | exit 1 | bandit 1 条 MEDIUM **B608** —— bandit **只认 `# nosec`**，不认 ruff 的 `# noqa: S608` |

「CI 红」是一个**聚合状态**，而聚合状态不能指导修复 —— 必须拆到 job 粒度。
（同族：`len(alerts)` 式健康度打印，见 2026-09-09 那条 learnings。）

### 2. 红了会**掩盖下游 job**：`needs: lint` 让 Unit Tests 静默跳过 4 周
`claw-ci.yml` 里 `test` job 声明 `needs: lint`。lint 自 09-21 起恒红 → **Unit Tests job 一次都没跑**。
于是"CI 红"的表象下，藏着一个**从没被验证过的测试 job**。
一旦把 lint 修绿，下游立刻暴雷（本次：exit 2，517 个测试一个没跑）。

> 可复用判据：**修好上游 job 时，必须预期下游 job 会首次暴露问题** ——
> 别把「lint 变绿」当成修完了；要逐个 job 至少成功跑过一次。

### 3. 下游那个暴雷的真因：测试**写死了本机绝对路径**
3 个新测试把脚本路径写成 `/Users/guan/WorkBuddy/Claw/.workbuddy/scripts/*.py`：
- 本机存在 → 本地 100% 绿；CI runner 上没有 `/Users/guan` → **collection 阶段** `FileNotFoundError`，
  整个 job exit 2（**连别的测试都没跑**，不是"几条失败"）。
- 更坏的一层：同批还有 3 处把本机路径传给 `_find_project_dir()` 并断言 `"Claw" in result` ——
  该函数在找不到项目根时会**退而求其次返回入参**，于是断言在 CI 上**恒真**。
  **断言一个常量 ≠ 验证一个行为。**

## 处置
- 清 12 条 ruff + 33 文件 format + `# nosec B608`；SIM118 保留 `in row.keys()` 并写明理由
  （sqlite3.Row 的 `in` 查的是**值**，`key in row` 不报错、恒 False —— 静默判成"键不存在"）。
- 3 个测试改 `Path(__file__).resolve().parent.parent` 定位仓库根；3 处恒真断言改成
  相对定位 + 断言解析结果 == 仓库根，并新增 tmp_path 自建树直接验「向上查找」。
- `push.paths` 原为 `[".workbuddy/**", ".github/workflows/**", "ruff.toml", ".pre-commit-config.yaml"]`
  → 修 tests 的那个提交**根本没触发 CI**（改了没跑）。已补 `tests/**` 与 `pyproject.toml`。

## 第四层：测试 job 真跑起来之后，暴露的是**生产代码**的问题（同日第二轮）

collection 修好后 Unit Tests **6 failed / 537 passed**，全在 `test_data_freshness.py`，
报 `FileNotFoundError: .workbuddy/data/astock_holidays.json` + `SystemExit: 2`。
真因不在测试：**`is_trading_day.load_holidays()` 在日历文件缺失/JSON 坏时直接 `sys.exit(2)`**，
而 **SystemExit 继承 BaseException、不是 Exception** → 调用侧的 `except Exception` 接不住 →
**整个中枢进程被带走**。本机有那个文件（且它被 .gitignore 排除）所以 4 周不暴露。

> 可复用判据：**「写了降级路径」≠「降级路径能走到」**。
> 任何 `except Exception` 都要问一句：**上游最可能的失败，是 Exception 还是 SystemExit/KeyboardInterrupt？**
> 库里调 `sys.exit` 的函数（`load_*` / `ensure_*` / 各厂 CLI 的 helper）全部属高危。

## 🔴 方法论：本地全绿 ≠ CI 会绿 —— 用 `git clone` 造一份「只有 tracked 文件」的树

本轮两次"本地全绿却 CI 红"，根因都是**环境差异**（CI 没有 /Users/guan、没有 gitignore 的数据文件）。
所以推送前的验证姿势改为：

```bash
# 注意：clone 目标必须在同一卷（本仓在 /Volumes/ZHITAI，clone 到 /tmp 会 Cross-device link 失败）
git clone --local --quiet /Volumes/ZHITAI/WorkBuddy/Claw /Volumes/ZHITAI/WorkBuddy/.ci-sim-claw
cd /Volumes/ZHITAI/WorkBuddy/.ci-sim-claw
python -m pytest tests/ -q          # 克隆里只有 tracked 文件 = CI 的文件面
```

本轮克隆里 root tests **545 passed** / `.workbuddy/tests` **185 passed** 之后才推，CI 才一次过。
用完**删掉**克隆（`rm -rf`）—— 留在盘上会腐烂，并会被后续"多副本审计"当成第二个仓（本仓已有此类纪律）。


## 顺带抓到 / 未处置
- F821：`unified_ops_center.check_data_freshness` 注解写 `pathlib.Path`，模块只 import 了 `Path`。
  `from __future__ import annotations` 把它变成惰性字符串 → 不报错，但一取 type_hints 就 NameError。
- QTS/StockInsight 的 "behind 持续扩大" = **远端被 Dependabot 自动合并推着走，而本地从不 pull**；
  不是分叉，是单向落后。已 `pull --rebase` 后推送（两仓均 ff，无冲突）。
- 未做：`tests/` 不在 CI 的 ruff 扫描面内（`ruff check .workbuddy/`），
  该目录现存 9 条 S108/N802/F401 属**未门禁**区 —— 要么把 tests/ 纳入 ruff 范围，要么明确声明"不扫"。
