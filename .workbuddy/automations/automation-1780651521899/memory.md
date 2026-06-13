# 微信文章早报 - 执行记录与 Prompt

## 优化版 Prompt（v2, 2026-06-09 更新）

### 执行流程
1. **搜索文章**: 使用 `wechat-article-search` 搜索关键词 `A股 投资 推荐 股票 金股`，时间范围 24h，count=20
2. **批量抓取**: 对搜索结果中的 mp.weixin.qq.com 链接，使用批量模式抓取：
   ```bash
   python3 /Users/guan/.workbuddy/skills/wechat-article-fetcher/scripts/fetch_wx_article.py \
     --batch-file /tmp/wx_urls.json -o /tmp/wx_articles.json
   ```
3. **AI 分析**: 对抓取结果进行 AI 分析，提取：
   - 股票代码和名称
   - 买卖观点（看多/看空/中性）
   - 目标价位
   - 逻辑链条
4. **更新股票池**: 将有效信号（至少2篇独立来源确认）更新到 `data/stock_pool.json`
5. **推送飞书**: 标题 `📊【炒股助理】微信文章早报`，推送内容含摘要+信号

### 错误处理
- 批量抓取失败 → 降级到单篇抓取（最多3篇关键文章）
- 所有抓取失败 → 使用搜索结果摘要生成简要分析
- 搜索无结果 → 扩大时间窗口到48h，或静默退出
- 记录失败原因到本 memory.md

### 输出格式
- 飞书群推送（直接推送）
- 更新 stock_pool.json 时附带 source 字段标记文章来源

---

## 2026-06-12 执行
- ✅ 通道一（we-mp-rss API）成功连接（Docker正常运行），但目标公众号无新文章（周五/周末无更新）
- ❌ 通道二（RedFox API）不可用：环境变量 REDFOX_API_KEY 未配置
- ✅ 通道三（搜索摘要降级）执行成功：微信搜索 + WebSearch 获取到多篇6月券商策略
- ✅ 深度分析来源：长城证券6月金股、十大券商6月策略、人形机器人/电力/PCB板块分析
- ✅ 飞书推送成功（bot身份，message_id: om_x100b6d80eb11eca4b2434407258ae2a）
- 📌 核心信号（多源交叉验证）：AI硬件链（鼎通科技/路维光电/PCB）、电力/电网（华电国际）、人形机器人（宇树科技上会催化）
