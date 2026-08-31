# 一圈排查挖出 3 个「错误信息本身不可诊断」的既存缺陷

> 发现日期：2026-08-31（统一巡检中枢第 37 次运行）
> 严重度：P2（不致命，但每次触发都要花几十分钟做考古）
> ★升级候选

## 背景

巡检中枢报「自动化健康 exit 1」，飞书卡片正文是 `automation_health 退出码 1: ` —— 冒号后面是空的。
往下挖，一路挖出 3 个各自独立的「错误信息自毁」缺陷。

## 缺陷清单

| # | 位置 | 现象 | 后果 |
|---|---|---|---|
| 1 | `unified_ops_center.check_automation_health` | 告警只取 `r.stderr[:200]`，而明细在 **stdout** 的 JSON 里 | 卡片正文空白，等于白推一次告警 |
| 2 | `unified_ops_center.check_automation_health` | `json.loads(out[out.rfind("{") :])` | 取到**最后一个内层**花括号，对任何嵌套 JSON 必然失败 → rc==0 分支永远走 raw 回退，脚本级 `alerts` **从未被读到**，被静默吞掉且判为健康 |
| 3 | `push_card.py --json-stdin` | `build_card` 用 `for t, b in sections` 解包 | 传 dict 时被解包成**字典键名** → `t='title'`/`b='body'` → 被占位符守卫拦下，报出莫名其妙的「第 1 个 section body 疑似占位符」 |

## 共性（★）

**三个缺陷都不是"功能坏了"，而是"坏了之后告诉你的话是错的/空的"。**
这类缺陷极难在测试里暴露——功能路径的断言照样全绿，只有真出事时才发现线索断了。

### 通用检查项（以后写/审这类代码时逐条过）

1. **子进程告警：stdout 和 stderr 都要取**。
   退出码非 0 时不要想当然"错误在 stderr" —— 结构化输出（JSON/表格）几乎都在 stdout。
   稳妥写法：先尝试解析 stdout 取结构化明细，失败再退回 stderr 截断。
2. **`rfind` / `find` 取 JSON 边界时，想清楚嵌套**。
   `out[out.find("{"):]` 取首个顶层对象起点；`rfind` 只会拿到最内层。
   更稳的做法：`json.JSONDecoder().raw_decode` 或直接约定脚本输出纯 JSON。
3. **凡用 `for a, b in x` 解包外部传入结构，先确认元素是序列而不是映射**。
   Python 对 dict 解包**不报错**，只给你键名 —— 静默错误，且下游的校验（本例的占位符守卫）
   会给出一个**指向完全错误方向**的报错信息，排查成本极高。
   入口处做归一化 + 对不支持的格式显式 `raise`，比让错误流到下游便宜得多。

## 修复

- 1、2：`unified_ops_center.py` — 解析 `by_category` 里的 🔴 项拼明细；`rfind` 改 `find`。
- 3：`push_card.py` — `--json-stdin` 入口归一化 dict/tuple 为二元组，不支持的格式显式报错。

## 验证

- 1、2：用 08-31 15:53 真实 crit=1 载荷新旧对照 —— 旧 `alert='automation_health 退出码 1: '`（25 字符空壳），
  新 `alert='自动化健康 1 项🔴: Claw→QTS 模拟盘 portfolio 同步（48h未运行）'`。全量重跑 15 项 RC=0。
- 3：同 payload 下，旧（dict）抛 `ValueError`、新（dict）与新（数组）均构建成功 `elements=12`。
- 三个文件 ruff 全部通过。
