# `ruff format` 显式点名 `.json` 会给它加尾逗号 → 严格 JSON 立刻失效（★ 升级候选）

**日期**：2026-10-12 ｜ **严重度**：中（会静默产出坏文件，但下游 `json.loads` 会立刻报错，不是无声故障）
**状态**：已复现、已记录、已在门禁 docstring 留下警告；**未**给 ruff 提 issue（是否算 bug 取决于它是否
把 `.json` 当 jsonc 处理，见下）

## 现象（可复现）

```bash
python3 -c "import json;json.load(open('tests/hardcoded_paths_baseline.json'))"   # 合法
ruff format tests/hardcoded_paths_baseline.json                                   # → "1 file reformatted"
python3 -c "import json;json.load(open('tests/hardcoded_paths_baseline.json'))"
#   json.decoder.JSONDecodeError: Expecting property name enclosed in double quotes: line 40 column 5
```

实测环境：`ruff 0.15.17`（`~/.workbuddy/binaries/python/envs/default/bin/ruff`）。

发生了什么（逐行对比）：
1. 缩进 2 空格 → **4 空格**；
2. 对象最后一个成员后面**补了尾逗号**：`"verify_yupen_rssfree.py": 1` → `"verify_yupen_rssfree.py": 1,`。

第 2 条是致命的：**尾逗号在 JSONC 合法、在 JSON 非法**。所以 ruff 输出的是「jsonc 风格」，
而所有严格 JSON 消费方（`json.loads`、CI 里的校验、其他语言解析）都会拒绝。

## 爆炸半径（实测过，别扩大化）

- **显式点名** `.json` → 会被改写（危险）；
- **目录式** `ruff format .` → **跳过** `.json`（实测 `md5` 不变）→ 日常习惯用法安全；
- 本仓 `make lint` / `make format` 的目标是 `.workbuddy/scripts/ .workbuddy/lib/`（目录）→ 安全。

即：危险只出现在「有人显式把某个 .json 写进 ruff 命令」时 —— 而那恰好是**改完基线数字顺手格式化**的动作。

## 本次代价

我在更新 `tests/hardcoded_paths_baseline.json`（`health_check.py: 5 → 1`）时顺手把它加进了
`ruff format` 的文件列表 → 文件被改坏 → **P1-12 硬编码门禁直接以 `JSONDecodeError` 崩掉**。
好在是崩得响（不是静默通过），排查成本约几轮：错误信息只说"第 40 行第 5 列"，不说"文件被格式化过"。

## 已做的防护（按「改动最小」）

1. 该 .json 用 **文本替换**（不是 `json.dumps`，更不是 ruff）修改数字，改完立刻 `json.loads` 验一次；
2. 在**消费它的门禁**（`tests/test_no_new_hardcoded_paths.py`）的 docstring 里写明这条陷阱
   —— 那里是"要改基线数字的人"最可能读到的地方，且 `.py` 是 ruff 的正确对象（警告本身不会被格式化）。

## 可复用的判据

> 用「格式化/排版」类工具批量处理**配置文件**之前，先确认它对该扩展名的语义
> —— 格式化器可能按**更宽松**的方言（jsonc/yaml-flow/单引号）产出，而下游是**严格**解析器。
> 判据：改完立刻用**真实消费方**（不是肉眼看缩进）验一次。

关联：同一天的 `.learnings/2026-10-12-*` 系列都属于同一个母题 ——
**"工具/产物看起来对" ≠ "下游真的能消费"**。
