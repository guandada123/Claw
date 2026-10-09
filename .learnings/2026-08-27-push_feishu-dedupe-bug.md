# ★升级候选：push_feishu 去重键透传失效（P1·系统性）

**日期**: 2026-08-27 | **自动化**: automation-1785123941471 (助理实盘监控-10:00)
**根因(实证非推断)**: `automation_preamble.sh` 的 `push_feishu()` 函数只转发标题+正文(`bash push_feishu.sh "$1" "$2"`)，**丢弃第3参数去重键**；且 `push_feishu.sh` 本身也只 `TITLE="$1"; CONTENT="$2"`，从不读 `$3`。导致 `push_card.py --dedupe-key` 从未被传入，文件级6h去重彻底失效。

**影响面**: 所有经 `push_feishu` 函数传3参数(标题/正文/去重键)的告警类自动化——日志里记录的"去重抑制(6h内已发)"**全部是误报**，实际每个窗口都重复推送。历史 evidence_log 中"去重抑制"条目不可信。

**修复**: 
1. `automation_preamble.sh` `push_feishu()` 增加 `$# -ge 3` 分支，位置转发 `bash push_feishu.sh "$1" "$2" "$3"`
2. `push_feishu.sh` 增加 `DEDUPE_KEY="$3"` 并在 ARGS 末尾追加 `--dedupe-key "$DEDUPE_KEY"`(仅当非空)
3. 受控实测：今日去重键存在→正确输出「⚠️ 去重」+exit 0；全新键→正常发送+建文件

**验证回读铁律关联**: 之前误信"exit 0 + 去重提示"却从未实测去重是否真生效——本次用空壳去重文件实测才暴露函数层丢弃参数，符合「观察≠transition」「没看到日志实锤前不得下结论」纪律。

**升级为铁律建议**: 任何"静默/去重"类逻辑，必须在函数/脚本两端(调用方+被调方)都实测透传，不能只信一端日志。
