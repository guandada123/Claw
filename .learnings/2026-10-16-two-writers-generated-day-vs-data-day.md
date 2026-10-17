# ★升级候选 —— 「盘中第二个写入方 + 生成日≠数据日」：只 gate `report_date==today` 的消费方会把**昨日 K 线**当今日用

**日期**：2026-10-16（板块强弱层盘后刷新自动化 94465816 的例行巡检顺带定案）
**等级**：P1（**静默陈旧换形态**——「陈旧 32 天」已被消灭，但同一份产物被盘中和盘后两个写入方先后覆盖；
盘中那次天然是 `report_date=今天 / data_date=昨天`，而消费方的闸门只校验生成日 → **通过校验但吃到昨日数据**）
**类型**：data-integrity / 多写入方 + silent-stale（日期标签与数据日分离，闸门口径落后于标签语义）
**状态**：本轮只取证定案，**未改代码**（自动化职责=只刷新）。处置建议见 §5，待用户定夺。

---

## 1. 现象

`sector_strength.json` 的**盘前快照**长这样（刷新前抓取，2026-10-16）：

```
mtime      = 2026-10-16 09:16:53      ← 盘中写的
report_date= 2026-10-16               ← 「生成日」= 今天（看着很新鲜）
data_date  = 2026-10-15               ← 「数据日」= 昨天（K 线只到昨天，今天还没收盘）
```

而消费方 `advisor_rules._sector_layer_info()` 的闸门是（`advisor_rules.py:1016-1018`）：

```python
if cache_path.exists():
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    if data.get("report_date") == date.today().isoformat():   # ← 只校验生成日
        ... return 行内值 ...
```

→ **`report_date` 通过，`data_date` 没人看**。于是盘中/盘前的任何调用都会「校验通过、
拿昨日板块强弱当今日结论」。这正是本自动化当初要消灭的「静默陈旧」，只是换了一层皮。

**更隐蔽的副作用**：盘后 15:20 本自动化的**失败会发现性被削弱**——即便 15:20 那次彻底没跑，
盘前那份文件仍带着 `report_date=今天`，闸门照过，无人报警。

## 2. 取证（为什么昨天把写入方归错了）

| 时点 | 事件 | 证据 |
|---|---|---|
| 09:07:25–09:12:53 | 📊【盘中】助理实盘监控 运行（automation-1784039316540） | `logs/automation.log` run start/finished 事件对 |
| **09:16:53** | **产物被改写**（report_date=今天 / data_date=昨天，9262B） | `stat` mtime + 抓取到的 JSON 字段 |
| 09:15:53–09:31:53 | 📈【投顾操盘】智能选股 运行（automation-1780738597945） | 同上事件对；写入时刻 = 其 run start **+60s** |

排除法：
- **实盘监控不可能是本次写入方**——其 prompt 调的是 `advisor_rules.py diagnose`，
  而 `advisor_rules` 对快照**只读**（`_sector_layer_info` 仅 `read_text`）；
  且其实例窗口（–09:12:53）早于写入时刻 4 分钟。
- 昨天怀疑的 `drill_assistant_monitor.py:150-151`（F4 块里 `SECTOR_LAYER_SCRIPT "--quiet"`）
  确是仓库内**唯一**以子进程调用该脚本的地方 —— 但**没有任何自动化 prompt 引用它**
  （全库 70 条 prompt 关键词扫描：`drill_assistant` 零命中），它是演练脚本，不在生产链上。
- 代码层只有 `main()` 落盘（`sector_strength_layer.py:308-310`），
  `compute() / get_sector_label() / format_scan_focus_prompt()` **全部只读**。
- **真凶在文档里**：`STRATEGY.md:155-158`（智能选股 PHASE 1 必读的三份文件之一）写着——

  > ### 运行时机（每天输出当前最强板块 Top3）
  > - **选股流程第一步**：先刷新层再扫描。在选股 prompt 开头执行：
  >   `source $CLAW/.workbuddy/scripts/automation_preamble.sh && $PYTHON $CLAW/scripts/sector_strength_layer.py --quiet`

  即：**选股流程被要求「先刷新层再扫描」**。智能选股 LLM agent 照做 → 09:16:53 落盘。
  写入时刻随 agent 步进（调度 jitter）漂移，所以 10-15 是 09:27、10-16 是 09:16 —— 与「每天同一秒」无关。
  （佐证：该会话 `changes-index` 里**唯一**的文件变更只有它自己的 memory.md → 写入来自 Bash 子进程，不是 Write 工具。）

## 3. 判据（可复用）

> **① 一份产物若有多个写入方，必须按「谁最晚写」判断新鲜度，而不是「谁最新鲜」判断谁写的。**
> **② 任何 `report_date/generated_at/data_date` 分离的产物，消费方闸门必须同时校验两者。**
> 只 gate 生成日的闸门，在「任何时点都能写」的生产者面前等价于**没有闸门**。
>
> 最小动作：`report_date == data_date == today` 之外，再看一眼 **mtime 是否落在收盘后**。

同类已知：上游失败→下游静默产出旧结论；无 TTL 缓存→下游静默产出旧结论。本例是第三种：
**上游"太勤快"→下游拿到半旧数据**。

## 4. 为什么之前没被发现

- 10-11 立本自动化时，前提写的是「`sector_strength.json` **没有任何自动化调度**，陈旧 32 天」。
  这个前提**只在下线后成立**：`STRATEGY.md` 那条指令早于「陈旧 32 天」窗口，
  只是那段时间选股链路把它写坏了 / 没写，于是看起来像「无人调度」。
- 昨天的归因（实盘监控 / drill_assistant_monitor）只取了「代码里有调用」这一半证据，
  没有交叉核对**自动化 prompt 实际调的是什么脚本** → 结论看起来自洽，实则张冠李戴。

## 5. 处置建议（三选，按改动量升序）

| # | 动作 | 收益 | 成本/风险 |
|---|---|---|---|
| ① | `STRATEGY.md:156-158` 的调用加 `--no-write`（盘中只读刷新，落盘权归 15:20 自动化） | 写入方收敛为 1 个；盘中仍有实时 compute 兜底 | 改一行文档；`--no-write` 已在脚本里实现并有测试（`test_trading_day_gate.py:115`） |
| ② | `advisor_rules._sector_layer_info` 闸门改为 `report_date == data_date == today` | 即便再出现盘中写入，也只会**回退实时 compute**，不会吃到昨日数据 | 改一行代码 + 补一条测试 |
| ③ | 本自动化增加「刷新前快照探针」：若刷新前 `report_date` 已是今天，记一行日志说明存在第二写入方 | 下次再出问题无需取证一轮 | 改自动化 prompt |

**建议 ① + ②**：①消除根因（多写入方），②是纵深防御（口径）。③可省，因为①落地后前提重新成立。
