# 微信读书早报 — 执行记录与 Prompt

## 优化版 Prompt（v2, 2026-06-09 更新）

### 执行流程
1. **Cookie 预检**: 
   ```bash
   python3 /Users/guan/WorkBuddy/Claw/.workbuddy/scripts/weread_fetch.py --check-only 2>&1
   ```
   如果返回 `COOKIE_EXPIRED`，直接跳过采集步骤，推送通知给用户

2. **采集文章**（Cookie 有效时）:
   ```bash
   python3 /Users/guan/WorkBuddy/Claw/.workbuddy/scripts/weread_fetch.py
   ```

3. **补充搜索**: 对采集结果中的文章标题，使用 `multi-search-engine` 搜索补充信息

4. **AI 摘要**: 生成摘要，提取看多/看空信号+置信度，格式：
   ```
   【公众号名称】文章标题
   观点: 看多/看空/中性 (置信度: X/10)
   核心逻辑: xxx
   涉及标的: 股票代码
   ```

5. **推送飞书**: 标题 `📊【炒股助理】微信读书早报`

### 降级策略
- Cookie 过期 → 先推送飞书通知，再用 `wechat-article-search` + `WebSearch` 多渠道搜索
- we-mp-rss 在线但微信授权过期 → 自动降级（2026-06-11 新增）
- 采集为空 → 扩大时间窗口到 48h
- 搜索失败 → 用 `WebSearch` 替代

### 目标公众号
| 公众号 | WeRead Book ID |
|--------|---------------|
| 投资明见 | MP_WXS_2394724034 |
| 恩哥箴言 | MP_WXS_3686248075 |
| 丹木说 | MP_WXS_3874969449 |
| 好运侠客 | MP_WXS_3640837602 |
| 猫笔叨 | MP_WXS_3905839574 |

---

## 2026-06-12 更新
- **三通道执行结果**:
  - 通道一（we-mp-rss）：服务在线但文章库为空（返回 null），且 sync 命令有中文URL编码bug → 降级
  - 通道二（WeRead）：Cookie 过期（COOKIE_EXPIRED）→ 推送飞书通知 → 降级
  - 通道三（搜索降级）：wechat-article-search 搜狗搜索仅猫笔叨有3月旧文
- **成功发现**: 通过 WebSearch 发现猫笔刀个人备份站 maobidao.cn，成功采集到 10 篇最新文章（6/2-6/11）
- **猫笔刀核心判断**: 确认慢熊市（年内中位数-13%），AI 结构性行情但追高风险极大
- **其他公众号**: 投资明见/恩哥箴言/丹木说/好运侠客 均无近期搜索到
- **推送状态**: ✅ 完整早报已推送飞书群

## 2026-06-11 更新
- **三通道降级流程投入使用**
- 通道一（we-mp-rss）：服务在线但微信 Cookie 过期（token 为空），文章库为空 → 降级
- 通道二（WeRead）：Cookie 过期（COOKIE_EXPIRED）→ 推送飞书通知 → 降级
- 通道三（wechat-article-search + WebSearch）：搜索降级模式
- 搜索结果：投资明见/恩哥箴言/好运侠客 无近期结果（均为70-100天前旧文），猫笔刀有一篇6月2日新浪转载文章
- 通过 WebSearch 补充：吴清6.6讲话、量化新规、6月A股四大主线、东方财富分析等
- 报告已推送飞书群，附带降级模式标注

## 2026-06-09 更新
- Prompt 升级到 v2：新增 Cookie 预检、降级策略
- weread_fetch.py 升级：添加 quick_cookie_check() 快速预检

## 2026-06-09 07:31
- **状态**: ⚠️ Cookie 已失效，未采集到文章
- **操作**: 已推送飞书通知用户重新扫码
- **详情**: weread_fetch.py 检测到浏览器未登录，agent-browser 首次安装（Chrome 149.0.7827.55），需用户手动扫码登录后保存 state
