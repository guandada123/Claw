# ★升级候选 —— 「共享包变更只同步了 1/3 个消费者」：`shared/auth.py` 改了 PyJWT，两个服务镜像却还停在 python-jose

**日期**：2026-10-16（由统一巡检中枢告警盲区反查时发现，与同批的 self_heal 假成功互为因果）
**等级**：P0（**服务级不可用**：`quant-execution` 崩溃循环、累计重启 115 次、持续约 3 天）
**类型**：依赖清单多真源 + 共享包消费者未重建 + "假健康"（未 import 到坏模块所以看着正常）
**状态**：**已修复并验证**（`QTS@35e9570b`）；两个 constraints 已改锁 PyJWT，两镜像已重建

---

## 1. 现象

`quant-execution`：`Restarting`、`RestartCount=115`、`ExitCode=1`。日志：

```
File "/app/execution-service/main.py", line 25, in <module>
    from shared.auth import get_current_user
File "/app/execution-service/shared/auth.py", line 34, in <module>
    import jwt
ModuleNotFoundError: No module named 'jwt'
```

镜像内实测：`python-jose 3.5.0` 在，`PyJWT` **不在**。

## 2. 根因链

1. **10-13 的安全迁移**（`36d91f95`）把 `python-jose` 换成 `PyJWT`——`jose` 已归档无补丁。
   该提交**只改了 2 个文件**：`docker-compose.yml` + `strategy-service/requirements.pinned.txt`。
2. 但"依赖真源"在这个仓里是**三份**（每个服务一份 constraints/pinned）：
   - `strategy-service/requirements.pinned.txt` ← 改了 ✓
   - `execution-service/constraints.txt:45` ← **漏**，仍锁 `python-jose==3.5.0`
   - `ai-scheduler/constraints.txt:37` ← **漏**，同上
3. `shared/auth.py` 的新版用的是 **PyJWT 的 API**（`jwt.encode/decode`、`jwt.PyJWTError`），
   而 `python-jose` 的异常是 `jose.JWTError` —— 两者不兼容，**只能靠 PyJWT 才能 import 成功**。
4. 两个漏网服务的**镜像都构建于 10-11**（早于迁移）→ 镜像里只有 python-jose。
   `execution-service` 运行期 `import shared.auth` → 立刻崩。
5. `ai-scheduler` 同样缺 PyJWT，但它**不引用 `shared.auth`**（全仓仅 execution 的测试引用）
   → 一直"健康"，属**同类潜伏雷**：一旦哪天用到 auth，或在下次重建时才会暴露。

**为什么"改了一个文件就等于改完了"的想法会成立**：迁移者看到的是 `shared/auth.py` 一个文件，
而真正的契约横跨 3 份依赖清单 + 3 个镜像。

## 3. 两个可复用的教训

**(a) 共享包（`shared/`）的变更，其"消费者清单"必须显式枚举并逐个重建。**
本仓 `shared/` 被 3 个服务以 `-e ".[<svc>,shared]"` 安装（`execution-service/shared` 还是
`../shared` 的符号链接 → 同一 inode，改一处三处都"看到"源码，但**镜像里的依赖不会自动更新**）。
目前**没有任何机制**提醒"shared 变了要重建谁" —— 这是本次 3 天盲区的结构性原因。

**(b) "假健康"比"不健康"更危险。**`ai-scheduler` 因未 import 坏模块而全程 healthy，
掩盖了它同样缺依赖的事实。**判断依赖是否齐备，不能只看服务是否活着，要看它的 import 图。**

## 4. 修复

两处 constraints 的 `python-jose==3.5.0` → `PyJWT==2.15.1`（与 strategy 侧锁**同版本**，
避免重建时自由解析造成新漂移），重建两个镜像并重建容器。

验证：
- `quant-execution`：PyJWT 2.15.1、`running`、重启计数归 0、Uvicorn 就绪、
  `/health` 200、`/openapi.json` 200、同源 `/openapi/execution.json` 200（原 404）
- `quant-ai-scheduler`：healthy，且它自带的健康监控输出
  `{'strategy-service': True, 'execution-service': True, 'ai-scheduler': True}`
  —— 修复前 `execution-service` 必然为 False（**系统自证**，比人工验更硬的证据）

回滚：两 constraints 恢复 `python-jose==3.5.0` + 重建（旧镜像仍在本地）。

## 5. 升级建议（待定夺）

1. **依赖清单应单一真源**：三份 constraints/pinned 是漂移温床。建议合并为一份
   `constraints/<svc>.txt` 由同一脚本生成，或在 CI 加"三份清单中同名包版本必须一致"的校验。
2. **`shared/` 变更 → 自动列出受影响的镜像**：可在 CI 加一条：`shared/**` 有改动时，
   输出所有 `COPY shared/` 的 Dockerfile 及其服务名，并在 PR 里提示"需重建并验证以下服务"。
3. **残留的 `python-jose` 应全面清扫**：本仓已确认 3 处（2 处已修 + strategy 已迁），
   但 `.venv` 与其它历史文件仍可能残留；建议 rag 一次全仓扫描。
