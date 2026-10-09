# ★升级候选：双副本去重后，scripts/ 残留调用方裸导入 helper 全断（P1·系统性）

**日期**: 2026-09-23 | **自动化**: automation-1786666500469 (Alpha101 因子评估与 kept 清单刷新, 17:10)

## 现象
alpha_eval.py 直接崩溃：
```
File "scripts/alpha_data.py", line 23, in <module>
    import qts_client
ModuleNotFoundError: No module named 'qts_client'
```
评估/刷新全链断在导入阶段，kept 清单当日无法刷新。

## 根因（实证非推断）
09-23 14:43 的「双副本去重」把 scripts/ 下 21 个与 .workbuddy/scripts/ 字节一致的 helper 副本
退役（mv 至 /tmp/trash_dupcopy_20260921/，含 qts_client.py），单源保留 .workbuddy/scripts/。
但仍有 **单源留在 scripts/ 的调用方** 按裸模块名导入这些 helper：

- `scripts/alpha_data.py:23` `import qts_client`（硬断，评估入口）
- `tests/conftest.py` 只把 `scripts/` 注入 sys.path → 测试侧 `import qts_client` 全断
- 另有 7 个 scripts/ 单源脚本 + 1 个 src/ 模块同类裸导入

**判定失误点**：去重审计时用「文件名/字符串」判活调用方，未按**导入符号 + 调用方所在目录**做冒烟。
`import qts_client` 在 scripts/ 下能成立，仅因调用方与 helper 同目录——helper 一搬走即断，
而调用方本身并不在退役清单里，故「0 活调用方」结论对这批文件不成立。

## 影响面（已量化）
- 硬断（导入即失败）：`scripts/alpha_data.py`、`tests/conftest.py` 覆盖的 7 个契约测试（实测 7 failed）
- 同批隐性风险（路径执行到才断）：t0_daily_check / cost_dashboard / earnings_calendar /
  heartbeat_local / pull_qts_signals / stock_score_card / export_qts_regime / wind_monitor

## 修复（可逆、非破坏性）
统一模式：在各自既有路径注入处 **append** `<Claw>/.workbuddy/scripts` 作 fallback。
用 append 而非 insert(0)，保证 scripts/ 本地模块优先级不变，不引入同名遮蔽。

| 文件 | 改动 |
|---|---|
| scripts/alpha_data.py | import 前显式把 `.workbuddy/scripts` 加入 sys.path |
| tests/conftest.py | 追加 `_HELPER_SCRIPTS` 注入 |
| 上述 7 个 scripts/ 单源 + src/claw/monitoring/wind_monitor.py | 各 +4 行 fallback 块（含幂等标记） |

备份：`/tmp/patch_bak_20260923/`（8 文件）；补丁脚本 `/tmp/patch_helper_path_20260923.py`（幂等）。

## 验证
- `alpha_eval.py --sample 300` → rc=0，达标 3 因子，报告 22996 B 非空
- `pytest tests/test_pull_qts_signals_contract.py` → 7 failed → **7 passed**
- `pytest tests/` 全量 → **501 passed**
- 8 个补丁文件 `py_compile` OK + `sys.path[0]=scripts` 下逐个 import 冒烟全 OK

## 升级为铁律建议
1. **去重/搬移类改动，验收标准必须是「按符号的导入冒烟」，不是「文件名无引用」**：
   对每个被移动的模块名，在其**所有调用方所在目录**下实际 import 一次；同目录自洽的裸导入
   是假安全区（本次实犯，与 08-30 已记铁律同源，但那次是 grep 符号，这次连符号 grep 都漏了
   ——因为调用方与被移动文件同名目录，grep 命中后未逐目录复验）。
2. **搬移后立即全量跑一次测试**：`tests/conftest.py` 这类共享 sys.path 注入点是同批断点的
   放大器，测试全绿/全红是最廉价的哨兵（本次 7 failed 本可在 14:43 当场暴露）。
