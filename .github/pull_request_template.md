# Code Review Checklist

## 错误处理
- [ ] 没有 `except:` 裸捕获（ruff E722）
- [ ] 没有 `except: pass` 吞异常
- [ ] 每个 `except` 块至少包含 `logger.error/warning` 或 `raise`
- [ ] 网络/IO 操作有超时设置（requests timeout, asyncio timeout）
- [ ] 重试逻辑使用指数退避

## 数据完整性
- [ ] 文件写入使用原子操作（`atomic_write_json`）
- [ ] JSON/数据库写入前有 Schema 校验
- [ ] 关键数据变更前有自动备份
- [ ] 读取失败不覆盖原始文件

## 配置与安全
- [ ] 新增配置项在 `.env.example` / Helm values 中有文档
- [ ] 无硬编码密钥、密码、Token
- [ ] K8s Secret 值不为空（生产环境）
- [ ] 环境变量变更同时更新 Helm Chart 和 `.env.example`

## 测试
- [ ] 新增/修改的代码有对应测试
- [ ] 涉及数据持久化时覆盖损坏数据场景
- [ ] 涉及 API 调用时有超时/Mock
- [ ] CI 通过（ruff + mypy + pytest）

## 可观测性
- [ ] 关键路径有结构化日志（非 print）
- [ ] 异常有 traceback 记录
- [ ] QTS: 新 API 端点有 Prometheus metrics
