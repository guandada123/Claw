# 自动化健康误报:工作日(HOURLY+BYDAY=MO-FR)自动化周末被误判 critical

日期: 2026-08-24
影响: 统一巡检中枢 `自动化健康` 项首轮运行触发「退出码1」critical 飞书告警(误报)
✅已升级(2026-08-30): 是(每周一必然复发的检测逻辑缺陷,应升为巡检 stale 判定铁律)

## 现象
`unified_ops_center.py` 的 `check_automation_health()` 复用 `automation_health.py --json`,
该脚本 `critical>0` 即 `sys.exit(1)`,被判定为异常告警。
本次 critical 项: 📈【盘中】投顾策略执行 → 🔴 48h未运行。
飞书已误推一条 red critical 卡片(后续已补 success 卡片纠正)。

## 根因(证据链)
1. `automation_health.py::check_health()` 对 `FREQ=HOURLY` 用**固定阈值 48h**
   (`else: threshold = 48`),未识别 rrule 的 `BYDAY=MO,TU,WE,TH,FR` 工作日限制
2. 该自动化仅工作日调度,周五 23:29 末次运行 → 周一 00:28 下次运行,
   缺口 ~49h 为**周末自然间隔**,调度器 `next_run_at`(未来)已证明排期正常
3. 48h 阈值被周末间隔越过 → 误判 🔴 critical
4. 此前多次巡检该项为绿,是因缺口恰好 <48h(周六日间),本次(周一 00:22)刚越阈值

## 教训
1. **静默失败/stale 判定必须尊重调度语义**:固定时间阈值对"仅工作日/周度/节假日"任务必然误报
2. 平台算出的 `next_run_at` 是"是否已正确排期"的**权威证据**,未来即健康,优先于 gap 启发式
3. 巡检类告警要先看 `next_run_at` 与 rrule 再定性,勿把"周末没跑"当"静默失败"

## 修法(`/Users/guan/WorkBuddy/Claw/.workbuddy/scripts/automation_health.py`)
- 新增 `_extract_byday(rrule)`: 解析 `BYDAY=MO,TU,...` → 集合
- `check_health()` 阈值段后增加:
  - BYDAY 仅工作日(MO-FR,不含 SA/SU)时 `threshold = max(threshold, 72)` 覆盖周五→周一
  - `next_run = _parse_unix(next_run_at)`;若 `next_run` 在未来 → `stale=False` 跳过 stale 判定
    (兼覆盖法定节假日等所有"排期但暂时不跑"场景)
- 单测: 真故障(next_run_at 过期 + 100h 缺口)仍报 🔴;周末用例(next_run_at 未来)报 🟢
- 验证: 重跑 `unified_ops_center.py` 15 项全绿(`自动化健康 0 异常`,退出码0)

## 预防
- 任何"Xh 未运行/stale/静默失败"类检测,一律先查 `next_run_at` 是否未来 + rrule 的 BYDAY/频率
- 新增调度健康检查项时,gap 阈值必须按 `MONTHLY/WEEKLY/BYDAY/once` 分级,禁用单一固定值
- 飞书已误推的 critical 必须补发纠正卡片,避免群内留误导信息

✅已升级建议(2026-08-24): 巡检 stale 判定铁律 = "next_run_at 未来即健康,固定 gap 阈值须按 BYDAY/频率分级"
