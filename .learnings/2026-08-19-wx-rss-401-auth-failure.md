# 2026-08-19 鱼盆 RSS 服务认证失效（HTTP 401）诊断记录

## 症状
`fetch_yupen_rss.py` 秒级失败并报「⚠️ 未找到猫笔刀系列订阅」，写 no_data（note=未找到猫笔刀订阅）。
之前 4 天（08-13~08-18）的失败模式是「三账号能列出但 detail 抓不到图」（RSS 被微信风控），
今天连订阅列表都拿不到，耗时数秒（无重试），明显是新故障模式。

## 根因（证据链）
1. `GET https://wechatrss.waytomaster.com/api/subscriptions` 直连 → **HTTP 401 `{"detail":"无效的认证凭证"}`**
2. `POST /api/article`（detail，真实文章 URL）→ **同样 HTTP 401**
3. 凭证文件 `~/.workbuddy/auth/wx_rss_api.sh` 完好（token 长度 117，2026-06-30 创建 Basic 套餐）→ **服务端 token 认证整体失效**（套餐到期或 token 被吊销），非本地配置问题
4. 二次坑：`wx_rss_auth.get_subscriptions()` 对非 200 响应静默吞成空列表（`data.get("subscriptions",[])`），
   上游 `_find_mbd_fakeids` 匹配不到昵称 → 误报「未找到订阅」，掩盖了 401 真因

## 修复（已落地，低风险）
`wx_rss_auth.py` 两处可观测性增强：
- `get_subscriptions()`：非 200 时显式打印 HTTP 状态码+响应体（不再静默吞空列表）
- `fetch_article_content_ex()`：遇 401 直接返回 `auth_401:<detail>`，不空转重试

验证：py_compile 通过 + 实测打印「⚠️ 订阅列表接口 HTTP 401: {"detail":"无效的认证凭证"}」。

## 决策/待办
- 鱼盆/早报/公众号同步的 RSS 上游（wechatrss.waytomaster.com）当前不可用
- 14:00 档若仍 401 → 确认套餐到期，需用户联系服务方续费/换 token
- 后备方案：本地 wechat-download-api（08-13 时 isExpired=true 需重扫码；轮询器卡 07-20 需先恢复）
- Wind primary 鱼盆数据链正常（08-19 已构建），早报板块轮动不受影响

## 复用规则（★升级候选）
- 诊断 RSS/外部 API 故障：先直连 curl 看 HTTP 状态码，再查本地凭证，禁止只看上游脚本报错文案
- 数据层函数对非 200 响应禁止静默返回默认值（如 `data.get("subscriptions",[])`），必须打印状态码
