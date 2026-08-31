# 2026-08-30 08:20 · 验证带副作用的状态函数时误传参数污染真实状态文件

## 分类
数据污染 / 验证方法缺陷 — 自引入（非系统故障），已修复

## 事实经过
在 readback 验证 `unified_ops_center.py` 的 72h 降频 TTL（前次运行引入的改动）时，
直接 `import` 模块后调用 `is_alert_duplicated(key, 72)`，误以为第二参是 TTL。
实际签名为 `is_alert_duplicated(check_name, reason_key) -> bool`，且**函数有写副作用**
（命中/未命中都会 `d[key]=now` 并 `_save_alerted(d)` 落盘）。

结果：真实 `.ops_alerted.json` 被写入非法 key
`微信公众号通道@wechat-download-api 登录过期(isExpired=true)，需扫码重登@72`。

## 根因
1. **未读函数签名就调用** — 只 grep 到函数名，未确认参数语义。
2. **在真实状态文件上跑验证** — 该模块的状态路径是模块级常量，未隔离即执行。
3. 返回值误读：返回 `False` 本应是"参数传错"的信号，却只当成一个数据点看过去。

## 修复
- 备份 → 过滤 `endswith("@72")` 的非法 key → 写回 → 回读确认仅剩 1 个合法 key。
- 备份：`/tmp/.ops_alerted.json.bak.20260830_082007`

## ✅已升级(2026-08-30)：验证「带写副作用函数」的三条硬约束
1. **先读签名再调用**：`inspect.signature(fn)` 或读源码，禁止靠函数名猜参数。
2. **状态隔离**：验证前把模块级状态路径常量（如 `m.ALERT_DEDUP_STATE`）monkeypatch 到
   `tempfile.mkdtemp()` 下的临时文件，禁止直接打真实文件。
3. **返回值自证**：探针调用后的返回值必须对照期望断言，不一致即停（本次返回 False 而期望 True，
   本应立刻暴露参数错位）。

## 通用化
适用于所有"读起来像查询、实际会写盘"的函数：`is_alert_duplicated` / `mark_xxx` /
`ensure_xxx` / `get_or_create` 类。验证脚本一律走 `monkeypatch 状态路径 + 临时目录`。
