# 2026-08-30 回读验证先确认 JSON 路径层级，勿因取值路径错而误判"未落库"

✅已升级(2026-08-30)

## 事实经过
统一巡检中枢跑完（EXIT=0）后做 readback 验证，读状态锚：

```python
d["monitoring"]["unified_ops_center"]   # 取到 {}，误判"状态锚未写入"
```

同时看到顶层 `updated_at` 停在 2026-08-28（两天前），进一步加强了"写回没生效"的误判。
实际正确路径多一层：

```python
d["monitoring"]["global"]["unified_ops_center"]
# last_run.ts = 2026-08-30T09:28:15，status=silent_green，心跳正常
```

`updated_at` 陈旧属**正常**——写回函数只改 `monitoring.global.unified_ops_center` 子节点，
按设计不刷顶层时间戳。差点把"我自己读错"报成"系统缺陷"。

## 根因
1. 凭字段名（unified_ops_center）直觉猜层级，没有先读写入方代码确认路径。
2. 拿一个**不相关字段**（updated_at）当旁证，制造了自我强化的错误证据链。
3. 结论方向是"发现缺陷"，兴奋度高于怀疑度，没有先自证读数正确。

## 反模式 / 处置规则
1. **回读前先 grep 写入方代码**确认完整路径（`grep -n "<key>" write_script.py`），
   不要凭字段名猜嵌套层级；JSON 回读取值务必按写入方 setdefault 链逐层对齐。
2. **旁证字段必须先确认因果**：用 X 佐证 Y 之前，先确认 X 会被写入 Y 的同一段代码更新；
   否则"陈旧时间戳"这类旁证会把错误结论钉死。
3. **"发现缺陷"方向的结论，怀疑阈值要翻倍**：报缺陷前先做一次自证——
   换一条独立路径读同一数据（如直接 grep 文件文本 / 用另一脚本读），两路一致才定性。
4. 与既有铁律"日志成功≠数据落库须 readback"互补：readback 本身也会出错，
   **readback 的路径准确性是 readback 有效性的前提**。

## 追加（2026-08-30 16:53 第 17 次巡检）—— 同类再犯两次，扩为"文件路径 + 脏数据判据"两条
同一次回读里又踩了两个**同族**坑，说明规则 1 的覆盖面不够（只讲了 JSON 内的 key 层级）：

1. **文件路径本身猜错**：按历史记忆直觉去 `Claw/.workbuddy/state/cross_project_state.json` 读状态锚 → `FileNotFoundError`。
   实际写入方 `unified_ops_center.py:1552` 硬编码 `CROSS_STATE_PATH = Path.home()/".workbuddy"/"cross_project_state.json"`（**全局区，不在任何项目仓内**）。
   → 规则扩展：grep 写入方时**连 `CROSS_STATE_PATH` / `Xxx_PATH =` 常量一起 grep**，先定文件绝对路径，再定 key 层级。
2. **脏数据判据设错导致误报**：检查 `.ops_alerted.json` 的 key 污染时写了 `"@" in k`，而**合法 key 本身就含 `@`**（格式 `通道@细节`），结果把唯一的合法 key 报成"非法"。
   真实污染特征是 `@` 后跟**长数字时间戳**（如 `xxx@1787973816`），判据须用 `re.search(r'@\d{6,}', k)`。
   → 规则扩展：**写"异常检测"前先拿一个已知正常的样本跑一遍判据**（正向样例自检），判据误伤正常数据 = 假阳性，比漏检更误导。
3. 另附解析坑：`wechat-download-api` 的 `/api/admin/status` 返回 **`data.data` 双层**，`expireTime` 是 **ms 级 int**（不是 ISO 字符串），
   直接 `.get("expireTime").replace("Z","")` 会 `AttributeError`。同族接口回读前先 `print(json.dumps(d)[:300])` 看真实结构再取值。

## 关联
- 铁律：日志成功≠数据落库，凡写库须 readback（08-13）
- 铁律：诊断纪律——先抓证据日志再下结论，禁止脑补根因（08-11）
- learning：verify-side-effect-state-pollution（2026-08-30，验证带副作用函数须隔离）
- 自检口诀：**回读三问 —— ① 文件在哪？② key 在哪层？③ 判据会不会误伤正常值？** 三问不答完不写结论。
