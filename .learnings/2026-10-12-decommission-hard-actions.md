# ★升级候选：退役/停用「常驻机制」的三条硬动作（cron 留因注释 / launchd 双动作 / mail spool 出口）

**日期**：2026-10-12
**等级**：P1
**类型**：machine-maintenance / correction
**状态**：已落地（马维斯桥接退役时执行）

## 发生了什么
清理马维斯桥接僵尸时发现两件"静默失败"标本：
- **两个 cron 条目每分钟失败、已发 35.6 万封错误邮件（262MB），4 个月无人察觉**
  （`/var/mail/guan` 自 3-22 累积；`openclaw_watchdog` 18.7 万封 + `feishu_qa_bot_watchdog` 17 万封）。
- **8-22 的「僵尸 launchd 清理」已失效**——当时只做了 bootout，三个 agent 全回来了
  （重启/重登录会重载 plist，**bootout 只是会话级**）。

## 三条硬动作（可复用）
1. **停 cron = 注释 + 留原因**（原命令保留在注释内）：直接删除=无法回滚，也无人知道当初为何移除。
2. **停 launchd = `bootout` + `disable` 双动作**：disable 持久（`launchctl print-disabled` 可查）；
   只 bootout 会被下一次登录/重启复活。
3. **清 `/var/mail/<user>` 前先 `cp` + `cmp` 归档**；且**定期看一眼这个文件**——
   它是「静默失败最后会说话的地方」（cron 输出/错误都进这里；不看 ≠ 没有）。

## 判据
- 每分钟级 watchdog 只允许在「**被看护对象真的重要** 且 **命令真的存在**」时存在
  （本次两例都不满足：openclaw 二进制已不在；QA bot 已死 4 个月）。
- 退役动作完成后要**实证增长停止**（不是「应该停了」）：跨时间边界对比文件大小/时间戳
  （本次跨 2 个分钟边界 +0B 实锤）。
- cron 执行**外置盘/符号链路径**上的脚本可被系统拒绝（`Operation not permitted`；
  文件权限、卷 noexec、交互执行均正常也不代表 cron 能跑）——cron 任务尽量走「python3 读脚本」而非直接 exec 脚本。
