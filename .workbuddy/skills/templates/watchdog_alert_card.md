# 🛡️ 自动化巡检卡模板

> 用于 watchdog / self_heal / unified_ops_center 等巡检类自动化推送飞书 interactive 卡片的标准结构。

---

## 卡片标题
`🛡️ {巡检名} {HH:MM} 巡检 — {状态}`

## 状态判定
- ✅ 全绿 SILENT：无关键失败/无重启/无告警 → 不推送
- ⚠️ 异常：有非关键告警 → 结构化卡推送
- 🔴 严重：有关键硬杀(HARD_KILL)/容器重启失败 → 立即推送 + @所有人

## 卡片字段（interactive）
```json
{
  "msg_type": "interactive",
  "card": {
    "header": {"title": {"tag": "plain_text", "content": "🛡️ {巡检名} {时间} 巡检"}},
    "elements": [
      {"tag": "div", "text": {"tag": "lark_md", "content": "**步骤①** {step1_name}\n{step1_result}"}},
      {"tag": "div", "text": {"tag": "lark_md", "content": "**步骤②** {step2_name}\n{step2_result}"}},
      {"tag": "hr"},
      {"tag": "div", "text": {"tag": "lark_md", "content": "**飞书推送** {push_status}"}}
    ]
  }
}
```

## 表格视图（用于记忆/日志）
| 步骤 | 结果 | 详情 |
|------|------|------|
| ① {step1} | {status1} | {detail1} |
| ② {step2} | {status2} | {detail2} |
| 推送 | {push} | {push_detail} |
