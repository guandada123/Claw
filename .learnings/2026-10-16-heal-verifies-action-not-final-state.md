# ★升级候选 —— 「自愈只验动作返回码、不验终态」：`docker restart` 返回 0 即报 healed，崩溃循环静默存活 3 天

**日期**：2026-10-16（由 QTS 仪表盘全页实测顺带反查发现）
**等级**：P0（**告警价值归零**——中枢每小时巡检、报告 `result=success`、飞书零告警，
而服务全程不可用；属"红灯被涂成绿灯"型失效）
**类型**：fail-safe 反模式 / 假成功（action-issued ≠ state-recovered）+ 振荡防护窗口与故障节奏错配
**状态**：**已修复并验证**（`Claw@66845d6`），修复为新增 `verify_running()` 终态复核

---

## 1. 现象

QTS `quant-execution` 容器处于 `Restarting`、`RestartCount=115`，根因是运行期
`ModuleNotFoundError: No module named 'jwt'`（`shared/auth.py:34 import jwt`）。

怪点在于：**统一巡检中枢每小时都在跑，且日志里明明写着它处理过这个容器**：

```
2026-10-16T02:17:11 | self_heal | success | 容器崩溃自动重启
2026-10-16T05:21:59 | self_heal | success | 容器崩溃自动重启
2026-10-16T11:34:39 | self_heal | success | 容器崩溃自动重启
2026-10-16T14:40:41 | self_heal | success | 容器崩溃自动重启
2026-10-16T17:44:54 | self_heal | success | 容器崩溃自动重启
```

5 次"自愈成功"，服务一次都没好。**报告是绿的，服务是死的。**

## 2. 根因（两层，缺一不可）

**(a) 假成功：只验动作，不验终态**

`self_heal.py` 原实现：

```python
if status != "running":
    ok = docker_restart(c)          # ← 只看 docker 的返回码
    ...
    if ok:
        restarted.append(c)         # ← 动作被受理 = 计入自愈成功
```

上游 `unified_ops_center.check_docker_self_heal()` 再把 `restarted` **无条件**映射为
`healed` + `result="success"`：

```python
healed = [{"action":"container_restart", "target":c,
           "reason":"容器非running自动重启（Runbook#3）", "result":"success"} for c in restarted]
...
return {"ok": len(alerts) == 0, ...}      # ← restarted 不进 alerts，故 ok=True
```

而 `docker restart` 的语义只是"重启动作被受理"。对崩溃循环容器，它起来 → 立即退出 →
Docker 的 `restart:unless-stopped` 再拉起 → 无限循环。**没有任何一步验证它是否真的活了。**

**(b) 振荡防护窗口与故障节奏错配 → 二次兜底也失效**

`OSC_WINDOW_MIN = 60`、`OSC_LIMIT = 2`：同容器 60min 内重启 >2 次才"停手仅告警"。
但本故障的重启间隔约 **3 小时**（02:17→05:21→11:34...），每次都是窗口内第 1 次
→ 永远不触发振荡告警。**兜底网按"高频抖动"设计，而故障是"低频长跑"。**

## 3. 为什么这次才被发现

不是巡检没跑，而是**巡检自己的语义错了**。这条与既有铁律同族：
- 「🔴 日志成功≠数据落库，凡写库须 readback」
- 「🔴 上游「已修复」≠我链路已修复」

自愈脚本属于"写"的一方：**它是唯一知道动作是否被受理的，但没人要求它证明终态**。
外部只看到 `success`。

发现路径值得记下：**我是从"QTS 仪表盘 10 个页面逐页浏览器实测"出发的**
（api-docs 404 → `/openapi/execution.json` 404 → 查 8001 → 发现容器 Restarting）
——即**不是从告警出发，而是从页面行为出发**。说明"端到端实测"能兜住"告警层已失效"的盲区。

## 4. 修复（Claw@66845d6）

新增 `verify_running()`，重启后按 `3 次 × 3s`（单容器 ≤9s）复核，**仅当回到 `running`
才计入 `restarted`**，否则升格为：

```python
alerts.append({"container": c, "issue": "restart-ineffective-still-down",
               "status": vstatus, "health": vhealth, "pre_status": status})
```

口径取 `status == "running"`（healthcheck 的 `starting` 属重启后正常态；长期
`unhealthy` 由既有 `unhealthy-but-running` 分支负责）——**刻意不用 `healthy` 作判据，
避免对带 healthcheck/start_period 的服务产生误报**。`self_heal_log` 增记
`verified / verify_status / verify_health`，让"动作"与"终态"在日志里也可区分。

## 5. 验证方式（可复用的手法）

**隔离端到端 + 假 docker 可执行 + 假 HOME**，驱动真实代码路径、不碰真实容器：

```bash
mkdir -p /tmp/sh_test/{home/.workbuddy,bin}
# bin/docker: 按 mode 文件返回 inspect 结果；restart 时 touch marker
HOME=/tmp/sh_test/home PATH=/tmp/sh_test/bin:$PATH python3 self_heal.py
```

| 用例 | 期望 | 结果 |
|------|------|------|
| 健康容器 | 不动作 | ✓ checked=[c] restarted=[] alerts=[] |
| 重启后恢复 | 计入 restarted | ✓ |
| 重启后仍崩（复现事故） | **升格为 alert** | ✓ restart-ineffective-still-down |
| **反证：修复前同一用例** | 应误报成功 | ✓ restarted=[c] alerts=[]（缺陷成立） |

> **反证这一步不能省**：它证明"我的测试能击破这个缺陷"，否则测试可能只是恰好通过。
> （对应铁律：检测必须可证伪、绿灯须能被已知正样本击破。）

真实环境空跑：`ok=true、checked=9、restarted=[]、alerts=[]` → 无回归。

## 6. 升级建议（待定夺）

1. **同类"只验动作"的自愈动作应逐个排查**：重启/清缓存/重拉/换 token 类动作，
   凡有"下发即算成功"语义的，都应补终态复核。建议按此模板做一次普查。
2. **振荡防护应增加"窗口外的慢性循环"维度**：现在是"60min 内 >2 次"，
   建议叠加"同容器 24h 内累计重启 ≥N 次且每次都 non-running"→ 直接告警升级，
   否则低频长跑型故障永远落在窗口缝隙里。
3. **`RestartCount` 应纳入巡检**：`docker inspect .RestartCount` 单调递增即可暴露
   崩溃循环，且不依赖"我们是否重启过它"——是一条独立的旁路证据。
   （本次正是靠它（115 次）才确认故障已持续多日。）
4. 本例的触发根因（PyJWT 迁移只同步了 1/3 个服务）另有独立教训：
   **`shared/` 变更后必须重建所有依赖它的服务**，目前无自动检测。
