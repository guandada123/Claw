# Learnings INDEX — Claw

> ⚠️ **本文件由 `learnings_index.py` 自动生成，请勿手改**（改了会在下次生成时被覆盖）。
> 权威内容是 `.learnings/*.md` 正文；本文件只是**派生视图**。
> ⚠️ 手工维护的 `LEARNINGS.md` 里的**浓缩条目不在本表内**（它已停更，见其抬头说明）。
> 生成时间：2026-10-16 03:40　｜　条目数：**120**

- **按类型**：correction×5　blind-spot×4　取证口径×2　false-positive×2　engineering_practice×2　分析口径错误×2　归因错误×2　blind-spot / invariant-erased-the-dimension-it-broke　+　destructive-fixer writes parser failure×1　forensics-caliber×1　silent-degradation / 计量缺口×1　multi-session-push-interference / contract-change-missed-test-sync×1　silent-degradation / path-split-brain×1　verification-scope-blindspot×1　destructive-op sequencing / verify-vacuous×1　取证口径错误 / canonical-path blindspot×1　engineering_practice / guard-interaction×1　forensics-scope-blindspot×1　data-integrity / silent-stale-downstream×1　process / data-integrity×1　machine-maintenance / correction×1　incomplete-consumer-sweep×1　工具语义误用×1　self-checker-blind×1　silent-dead-mechanism×1　取证口径错误×1　time-dependent test×1　environment-drift / silent-fallback×1　silent-failure / false-success×1　silent-pass×1　false_positive_generator / counting_trap×1　best_practice×1　knowledge_gap×1　根因定案×1　（另有 75 条未标注该字段）
- **按等级**：P1×31　P2×10　P0×5　P0 级风险 × 手动取证险些失手×1　🔴 高×1　✅已升级(2026-09-06) ×2×1　（另有 71 条未标注该字段）

| 日期 | 标题 | 等级 | 类型 | 状态 | 文件 |
|---|---|---|---|---|---|
| 2026-10-16 | ★升级候选 —— 「不变式」擦掉了被改动的那个维度：一条批量排印改写吃掉 76 个 bullet，而内容不变式全程绿灯 | P1（**盲区 + 破坏性**：一次本应"零语义"的批量改写把全部规则行的 bullet 语法改坏， | blind-spot / invariant-erased-the-dimension-it-broke　+　destructive-fixer writes parser failure | 已修复（从备份逐字节还原后重做），并给写盘型修复器装上合理性闸门 + 证伪实验。 | `2026-10-16-invariant-erased-the-dimension-it-broke.md` |
| 2026-10-15 | 判据的「口径」也会说谎：把「恰好贴边（余量 0B）」报成「已经溢出 2B」 | P1（**假失败**：检查器连续两轮报告一个不存在的事实，会诱导「去修不存在的溢出」或「学会忽略这条红」） | forensics-caliber（判据的度量口径与它所回答的问题不同口径 · false-positive） | 已修复（口径修正 + 证伪实验），2026-10-15；规则层余量 0B 的**结构性处置**待用户决定 | `2026-10-15-predicate-caliber-renders-touching-as-overflow.md` |
| 2026-10-15 | ★升级候选 —— 「降级可见」≠「降级被计量」：61/300 只代码静默回退 T-1 旧K线，评估仍报"正常" | P1（**结论可信度**——20% 面板缺当日 K 线，而报告/退出码/达标数全部显示正常；本次 kept 结论未被翻转，但机制可复现） | silent-degradation / 计量缺口（降级有 WARNING 但未进入健康判据） | 本次未改代码；缓存已由复测自然重写（现 293/300 终点为 10-15）。规则缺口待固化。 | `2026-10-15-degraded-fallback-visible-but-unmetered.md` |
| 2026-10-14 | ★升级候选 —— 「未 push」的兄弟提交会骑乘你的 push 进 CI：契约变更必须同步搜测试依赖点 | P2（CI 红 + 一次跨会话返工；无生产影响——功能改动本身是合理的） | multi-session-push-interference / contract-change-missed-test-sync | 已修复（测试补 patch LOG_DIRS，commit `2d7feaa`）；本仓多会话并行是设计常态，此类连锁属系统性风险点。 | `2026-10-14-unpushed-sibling-rides-your-push.md` |
| 2026-10-14 | 检查器查的是「旧副本」：用户级记忆有两份，被注入的那份从没被检查过 —— 连报 6 天「零增长 🟢」，实际余量已是 −2B | P1（**假绿灯**：唯一守护「记忆尾部不可见」的检查，6 天来一直在查一份没人读的副本； | blind-spot（检查对象错位 · split-brain · false-green） | 已修复（检查器纳入被注入副本 + 声明回写 + 证据库重编号），2026-10-14；A/B 合并待用户决定 | `2026-10-14-checker-watched-the-wrong-copy.md` |
| 2026-10-13 | ★升级候选 —— 写读异径：`parent.parent` 缺陷留下的三处「静默降级」，直到排名陈旧三周才被看见 | P1（三处功能静默失效：早报排名板块陈旧三周 / 验证报告输入从未读到 / Wind 实时价从未真正调用——全部表现为"正常运行"） | silent-degradation / path-split-brain（写入方与读取方目录口径不一致） | 三脚本已修复（commit `ed2de0f`），数据已迁移，端到端验证通过。 | `2026-10-13-write-read-dir-split-brain-silent-degradations.md` |
| 2026-10-13 | ★候选铁律 —— 「凭据有效」≠「额度够用」：把耗竭型资源当成常量写进了验收判据 | P1（不造成数据错误，但会让**正确**的修复被误判为回退，进而被回滚或重复排查） | verification-scope-blindspot（验收判据漏掉时变前置条件 / 修复与资源是两个维度） | 无需改代码；待用户充值。本文只修正**归因口径**。 | `2026-10-13-credential-validity-is-not-quota.md` |
| 2026-10-13 | ★升级候选 —— 复合语句里的「复制→校验→删源」有覆盖竞态：我弄丢了一个死文件 | P2（**协议执行失误**——丢的是全仓零引用的死文件【12KB 旧版成本库】，功能影响为零；但失误机制可复现到**有引用文件**上，届时就是 P0） | destructive-op sequencing / verify-vacuous（三步压缩进一条命令 + 自比对空校验） | 文件不可恢复（恢复路径穷尽，见下）；机制教训固化于本文。 | `2026-10-13-cleanup-sequencing-lost-a-dead-file.md` |
| 2026-10-13 | ★升级候选 —— 「停滞 20 天」是路径混淆造的假警报：判「文件陈旧」必须先定 canonical 路径 | P1（**排查方向被带偏**——审计据此把「产物停更」立为 P0/P1 专项，自动化 LLM 会话据此进入「调试模式」而不执行管线；幸未造成数据损坏） | 取证口径错误 / canonical-path blindspot（多路径并存时按错误路径的 mtime 下结论） | 混淆源（旧路径残档）已归档；两版自动化提示词已加正典路径标注；本仓 `data/` 与 `.workbuddy/data/` 双目录并存问题**待收敛**（建议项）。 | `2026-10-13-canonical-path-before-staleness-verdict.md` |
| 2026-10-13 | ★升级候选 —— 新加的「缓存清扫」一次删 1861 个文件，撞上 safe-delete 护栏把整条评估链打断 | P1（当日自动化直接失败、无报告产出；护栏本身工作正常，是**新代码没适配护栏**） | engineering_practice / guard-interaction（批量删除撞本仓护栏） | 本次已绕过（存量已由自然运行消化）；**建议改代码**——清扫必须分片。 | `2026-10-13-bulk-delete-trips-safe-delete-guard.md` |
| 2026-10-13 | ★升级候选 —— 「未执行」的判决必须查后台任务事件；且先读项目自己的日日志 | P1（**取证误判**——把一次**成功**的后台执行判成「未执行」，虚假根因写入两版自动化提示词（已更正）、审计报告（已更正），并触发一次冗余管线重跑） | forensics-scope-blindspot（日志取证只覆盖前台事件）+ record-order（未先读项目自身记录） | 误判已全部更正（提示词/报告/本 learnings 留档）；真值锚：taskId=KLOSra。 | `2026-10-13-background-task-blindspot-in-forensics.md` |
| 2026-10-13 | ★候选铁律 —— 「连续19天零差异」不是稳定，是缓存冻住了：无 TTL 缓存让下游静默跑了两月旧数据 | P0（**数据正确性**——两个月的因子评估/因子清单建立在两月前的 OHLCV 上，且被误读为"稳定"） | data-integrity / silent-stale-downstream（无 TTL 缓存 → 下游静默产出旧结论） | 代码已由 10-13 的 TTL 补丁修复；**本文补充的是"误读口径"与监控缺口**。 | `2026-10-13-alpha-cache-no-ttl-froze-two-months.md` |
| 2026-10-12 | ★教训：单位口径差会造出**假警报**，而基于假警报的"修复"比不修更坏 | P2（无实际损失；但**差一步就把完好的数据改坏**） | 取证口径（单位/编码未对齐） | 已识别并挡住，无需修改 | `2026-10-12-unit-mismatch-fake-alarm.md` |
| 2026-10-12 | 2026-10-12 · 「红」不一定是自己写坏的：两种最常见的测试过期形态 ★升级候选 | — | — | — | `2026-10-12-time-bomb-tests-and-stale-fixtures.md` |
| 2026-10-12 | ★教训：扫描类检查必须锚定「**实际执行行**」—— 我差点被"修复说明的散文"骗到改坏 3 条自动化 | P1（若照第一反应动手，会对**3 条今天要跑的自动化**做无谓改动 —— 白改 + 引入风险） | 取证口径（正则扫到了散文而非命令） | 已在动手前自行拦下（导出 prompt 后 grep 才发现已修） | `2026-10-12-scan-prose-not-command-line.md` |
| 2026-10-12 | `ruff format` 显式点名 `.json` 会给它加尾逗号 → 严格 JSON 立刻失效（★ 升级候选） | — | — | 已复现、已记录、已在门禁 docstring 留下警告；**未**给 ruff 提 issue（是否算 bug 取决于它是否 | `2026-10-12-ruff-format-destroys-json.md` |
| 2026-10-12 | ★升级候选：恢复「积压消费通道」时，先给消费端补「陈旧输入」防护，再让它首跑 | P1 | process / data-integrity | 已落地（首跑前补丁 + 一次性核验在挂） | `2026-10-12-preflight-stale-guard-for-backlog-consumer.md` |
| 2026-10-12 | 2026-10-12 · 让测试"真跑"，是发现率最高的一个开关 ★升级候选 | — | — | — | `2026-10-12-make-tests-really-run.md` |
| 2026-10-12 | 2026-10-12 · 去硬编码先取证"用途"，再决定"替换形态" ★升级候选 | — | — | — | `2026-10-12-hardcode-replacement-needs-usage-evidence.md` |
| 2026-10-12 | ★升级候选：闸门的两处「缺一类」—— 新建文档类候选被整类误杀 + 登记端与落地端口径不一致 | P2（不误伤数据；但**让一整类候选永远进不了落地通道**，且两条闸门各说各话时无人发现） | false-positive（判据缺状态）+ 口径不一致（登记端松、落地端严） | 缺陷①已修（`7b5ff78`）；缺陷②已记录 + 数据侧补齐，口径统一留作提案 | `2026-10-12-gate-missing-new-file-state-and-two-calibers.md` |
| 2026-10-12 | 2026-10-12 · 全量对账 ≫ 逐个撞 —— 一次修齐整片「代码先行、schema 没到」 ★升级候选 | — | — | — | `2026-10-12-full-reconciliation-beats-whack-a-mole.md` |
| 2026-10-12 | 易变值的「自由文本副本」：加判据治不了，删掉副本才是解法 —— 索引头里一对过期数字，让读者信任了**已经失效的可见边界** | P2（不造成数据丢失，但**误导读者对「记忆可见范围」的判断**，且历经 6 次同类复发仍未被任何检查覆盖） | blind-spot（声明腐烂 · 检查只覆盖枚举格式，散文形态永远在覆盖之外） | 已修复（重写索引头第 6 行，去 4 个易变值），2026-10-12 | `2026-10-12-free-text-copy-of-volatile-value-will-rot.md` |
| 2026-10-12 | 兜底源「返空」与「返全市场」长得一样：一只 ETF 名下凭空多出 9 行别家公司的财报披露 | P1（**误报/误归因**类：他方数据被挂到本持仓名下，若字段名命中的话会直接产出「某持仓今日披露」的假告警） | blind-spot（降级路径未做归属校验 · 「缺键 ≠ 空集」同族） | 已修复并验证，2026-10-12 | `2026-10-12-fallback-source-must-verify-attribution.md` |
| 2026-10-12 | ★升级候选：退役/停用「常驻机制」的三条硬动作（cron 留因注释 / launchd 双动作 / mail spool 出口） | P1 | machine-maintenance / correction | 已落地（马维斯桥接退役时执行） | `2026-10-12-decommission-hard-actions.md` |
| 2026-10-12 | ★ 凭据轮换必须枚举「全部持有者」，且按文件清单而非目录猜测（2026-10-12） | — | — | — | `2026-10-12-credential-rotation-must-enumerate-all-holders.md` |
| 2026-10-12 | ★候选铁律 —— 「凭据轮换」漏掉 CLI 的**最高优先级**配置源，导致 19 个交易日静默空产 | P0（主数据源连续 19 个交易日产出空壳，早报板块轮动一直用 3 周前的旧数据） | incomplete-consumer-sweep（轮换覆盖不全 / 声明式「已修复」≠ 真实链路已修复） | 已修复（全局配置 + connector token 两处同步为在用值，端到端复测通过） | `2026-10-12-credential-rotation-missed-cli-config.md` |
| 2026-10-12 | ⚠️待升级 —— `.gitignore` 只防「文件名」，防不住「内容」：技能库基线快照里的**真实** API Key 存活 3 天（P1·系统性） | P1（**在用凭据**进入版本控制；一旦 push 即公开，且事后难溯源） | engineering_practice（覆盖面缺口 + 机器守卫缺位） | 工作树已修并提交；**历史仍含该值 → 推送保持阻断**；根因（守卫未接线）待落地 | `2026-10-12-content-secret-survives-name-level-fix.md` |
| 2026-10-12 | 2026-10-12 · `bash <python脚本>` 当门禁 = 必然非 0 = 静默跳过 ★升级候选 | — | — | — | `2026-10-12-bash-on-python-gate-is-always-nonzero.md` |
| 2026-10-12 | ★升级候选：`automation_update` 的 prompt 是**全量覆盖**，不是补丁 —— 我差点把一份 2370 字节的 prompt 抹成 1 行 | P1（若未及时发现，一个 ACTIVE 自动化的**全部作业指令会被一行频率说明替换**， | 工具语义误用（字段是全量替换，被我当成增量修改） | 1 分钟内发现并**已完全恢复**（逐字节 diff 校验通过） | `2026-10-12-automation-update-prompt-is-full-replace.md` |
| 2026-10-12 | 2026-10-12 · 归档必须校验条目数 —— 否则"归档成功"是假的 ★升级候选 | — | — | — | `2026-10-12-archive-must-be-verified-before-delete.md` |
| 2026-10-11 | ★教训：周期性自审的「本周改动」必须**先查 created_at 与派发次数**，再判「有没有用」 | — | 分析口径错误（不是系统故障） | — | `2026-10-11-weekly-self-audit-must-check-created-at-before-verdict.md` |
| 2026-10-11 | ★教训：审计别人的系统前，先读**既有的审计代码**（一次自我假警报的复盘） | — | 分析口径错误（不是系统故障；**是审计者自己制造的故障**） | — | `2026-10-11-self-audit-hit-documented-soft-delete-trap.md` |
| 2026-10-11 | ✅已升级(2026-10-11)→R75 —— 「没有规则的规则文件」会**静默关闭全部检测** —— CI 密钥扫描空跑 68 天 | P0（安全检测能力实际为零，且 CI 显示绿灯） | self-checker-blind（检测器自身失效 / 假性安心） | 已修复（`.gitleaks.toml` 补 `[extend] useDefault = true` + 自定义规则 + 6 例防回归测试） | `2026-10-11-rulesless-config-disables-all-detection.md` |
| 2026-10-11 | ✅已升级(2026-10-11)→R75 —— 判据被复用，盲区就会成对出现 —— 同一正则漏一种时间戳格式，该档位**同时**逃过「体检」与「归档」 | P1（不造成数据丢失，但**让 40% 的档位永久脱离治理**，且全程无告警） | blind-spot（判据复用 + 退出码语义污染） | 已全部修复（解析器 + 退出码 + 判据阈值），2026-10-11 | `2026-10-11-reused-predicate-reuses-blindspot.md` |
| 2026-10-11 | ✅已升级(2026-10-11)→R31 —— 状态**只读不写** → 触发器**结构性死亡**；而它的症状被误归因成"才过一天" | P1（T2 触发器**从未、也不可能**触发；无报错、无告警，脚本一直"看起来在跑"） | silent-dead-mechanism（状态不落盘） | 已修复（Claw `93e53bf`） | `2026-10-11-read-only-state-kills-accumulator.md` |
| 2026-10-11 | ✅已升级(2026-10-11)→R50 —— 分支删了，`gh run list` 的 failure 记录**不会消失** —— 一条**永久假红**常驻巡检 | P1（不误伤生产，但让一条**永远无人能处置**的红灯常驻 → 真红灯混在同一栏里失去分辨力） | false-positive（取证口径漏了"分支已不存在"这一态） | 已修复（`qts_pmf_health_guard.scan_ci` 加孤儿分支豁免） | `2026-10-11-orphan-branch-red-persists-after-delete.md` |
| 2026-10-11 | ★ 别在共享 checkout 里用 commit 做实验（2026-10-11） | — | — | — | `2026-10-11-no-experiment-commits-in-shared-checkout.md` |
| 2026-10-11 | ✅已升级(2026-10-11)→R73 —— 比对哈希**差点判反** —— 算法与粒度错一个，结论就反了（「在用凭据在公开仓」被判成"已轮换"） | P0 级风险 × 手动取证险些失手（结论错 = 把一个**公开仓里放着的有效凭据**判成"安全"） | 取证口径错误（readback 读错） | 已闭环（凭据已轮换）；已加 `--compare-env` 把口径固化 | `2026-10-11-hash-compare-caliber-nearly-inverted.md` |
| 2026-10-11 | ★教训：写死日期的测试会「**本地恒绿、CI 恒红**」—— 比"8 小时窗口必红"更坏的一种时区坑 | P2（不造成数据问题，但**只在 CI 暴露的失败**会让本地验证完全失效） | time-dependent test（恒红形态） | 已修（`a631c5e`），并把**验证方式**一并改掉 | `2026-10-11-hardcoded-date-assertion-green-local-red-ci.md` |
| 2026-10-11 | ✅已升级(2026-10-11)→R75 —— `.gitignore` 白名单漏掉**静态权威输入** → CI/新克隆静默降级 | P1（不是测试问题：**生产逻辑在 CI / 任何新环境里静默降级**） | environment-drift / silent-fallback | 已修复（日历入库 + 用例与外部文件解耦 + 临时移走文件验证 927 passed） | `2026-10-11-gitignore-whitelist-missed-static-input.md` |
| 2026-10-11 | ✅已升级(2026-10-11)→R75 —— 去重标记「先写后发」= 告警静默丢失 + 假成功 | P1（告警链路可靠性；失败会被 6 小时冷却吞掉，且调用方拿到 rc=0） | silent-failure / false-success | 已修复（去重标记改为**发送成功后**落盘，+3 例防回归测试） | `2026-10-11-dedupe-before-send-swallows-alerts.md` |
| 2026-10-11 | ★教训：CI 红了先做**归属判定**（按用例名），别默认是自己的改动 —— 一天撞上两次 | P2（不改代码，但**误判归属会导致修错东西**：要么白修自己没坏的代码，要么漏掉真问题） | 归因错误（CI 红是**聚合状态**） | 两次都正确定位到"不是我" | `2026-10-11-ci-red-attribution-before-fixing.md` |
| 2026-10-11 | ✅已升级(2026-10-11)→R75 —— 「取不到证」被渲染成「通过」—— 巡检里 4 个检查同款，且**探针跑出的数字不能直接当结论** | P1（不报错、不告警，但**该发现的都没发现**：外置盘掉了、401 风暴、写入连续失败都可能被 ✅ 掩盖） | silent-pass（取证失败 ≠ 通过） | 已修复 4 个检查 + 已加结构性守卫（`c2955d3`） | `2026-10-11-cannot-determine-rendered-as-pass.md` |
| 2026-10-10 | ✅已升级(2026-10-11)→R45 —— 判据带「隐含前提」时，必须在接口层表达出来 —— 否则任何跨档位复用必然自造假红 | P2（产生"每天 22/24 小时必然报红"的假警，并**差点被记成"索引静默失败"**） | correction（接口设计缺陷，非逻辑错误） | 已修复并复验 | `2026-10-10-implicit-precondition-must-be-a-flag.md` |
| 2026-10-09 | 修上游 ≠ 修链路：一次「一条链路三层静默失效」的取证 | — | — | — | `2026-10-09-upstream-fix-is-not-link-fix.md` |
| 2026-10-09 | ✅已升级(2026-10-11)→R38 —— 软删除语义 —— 判「自动化是否活着」必须 `status` 与 `deleted_at` 一起看，只看其一必错 | P1（我据错误口径编造出一个**不存在的问题**，并把它上报给用户要求人工处置） | correction（取数口径错误，非逻辑错误） | 已修正脚本 + 已撤回错误结论；规则候选 | `2026-10-09-soft-delete-status-vs-deleted-at.md` |
| 2026-10-09 | ★升级候选：⛔不升级(2026-10-10 已自我推翻, 真相见§6 外部自动修复) —— 不要用记忆断言自己的动作时序 —— `file-history` 快照 mtime 是「检查点时刻」，不是「写入时刻」 | P2（不造成故障，但会导致**自查结论凭空虚构**；本条即是把"疑似外部篡改"证伪的过程） | correction（工具语义误读 + 局部样本推整体） | 已定案关闭 | `2026-10-09-snapshot-mtime-is-checkpoint-not-write-time.md` |
| 2026-10-09 | 采集器配额被第三方产物吃光 + 三个「数出来的是假的」计数陷阱 | P1（发现面长期产出假信号，真实项目的痛一条都扫不到） | false_positive_generator / counting_trap | 已修复 + 已补 4 项回归测试 | `2026-10-09-signal-quota-starved-by-venv-and-three-counting-traps.md` |
| 2026-10-09 | ✅已升级(2026-10-11)→R74 —— 产物型 git 仓库初始化时，`.gitignore` 必须显式排除凭据（P1·系统性） | P1（凭据进入版本控制且**全程无任何告警**；泄漏只发生在 clone/push/bundle 之后） | engineering_practice（可复用 SOP + 机器守卫） | 已修复并复验；守卫已落地 | `2026-10-09-product-repo-git-init-must-exclude-credentials.md` |
| 2026-10-09 | ✅已升级(2026-10-11)→R18 —— 新建自动化的「飞行前检查」—— prompt 里的每条 Bash 命令必须逐字实跑 | P1（一条 prompt 里抓到 **4 个缺陷**，其中 3 个会让任务**静默失败或静默误报**） | best_practice（可复用 SOP） | 已修复并复验通过 | `2026-10-09-preflight-run-every-command-before-scheduling.md` |
| 2026-10-09 | ✅已升级(2026-10-11)→R18 —— 手动跑通过 ≠ 定时环境通过 —— launchd 中的 `python3` 读不了可移动卷，守卫首次实跑即误报 CRIT | P1（守护逻辑在真实运行环境下误报最高级别 + 写下告警标记，属"守卫自己先坏"） | correction（验证口径错误） | 已修复 + 已复验；规则候选 | `2026-10-09-manual-run-pass-is-not-scheduler-run-pass.md` |
| 2026-10-09 | ✅已升级(2026-10-11)→R22 —— 已有铁律「静态快照须有刷新脚本+过期告警+备份」未落地到 memory_index.db —— 该库静默停更 111 天 | P1（本机语义检索通道长期返回陈旧数据，无任何告警） | knowledge_gap（铁律已存在，但未覆盖到该数据源） | 已修复 + 已补自动化；规则候选 | `2026-10-09-index-db-had-no-refresh-task-at-all.md` |
| 2026-10-09 | ✅已升级(2026-10-11)→R10 —— `ls` / `Glob` / `find` 的「无输出」不是「不存在」的证据 | P1（差点把「本机最大的记忆资产」判成「不存在」，并在正式文档里写下相反结论） | correction（诊断口径错误） | 已定性 + 已修正文档；规则候选 | `2026-10-09-empty-output-is-not-evidence-of-absence.md` |
| 2026-10-08 | 「累计净差」被当成「当前并发」：一条每周必报 12 天的调度器超发告警 | — | — | — | `2026-10-08-accumulated-net-diff-read-as-live-count.md` |
| 2026-09-29 | 存活探针打了根路径：把「静态页 200」当成「API 已恢复」，误判修复 3 天 | P1（导致连续多日给出与事实相反的「已恢复」结论，掩盖真实故障） | — | 已定性（故障本体=云 RSS 凭证 401，待用户处置） | `2026-09-29-liveness-probe-must-hit-real-endpoint-not-root.md` |
| 2026-09-28 | 「服务还能不能重建」是没人查的盲点 —— 容器照跑，而部署能力已经死了 | — | — | — | `2026-09-28-image-rebuild-capability-is-unmonitored.md` |
| 2026-09-28 | CI 红 4 周的真因是「两个 job 各自红」+「红了会掩盖下游 job」+「测试写死本机路径」 | — | — | — | `2026-09-28-ci-red-masks-downstream-job-and-tests-pin-local-paths.md` |
| 2026-09-23 | ✅已升级(2026-10-11)→R2-ev —— 双副本去重后，scripts/ 残留调用方裸导入 helper 全断（P1·系统性） | — | — | — | `2026-09-23-dedup-helper-import-break.md` |
| 2026-09-09 | 聚合型 alert 的「条目数」被误读成「问题数」，制造虚假康复信号 | — | — | — | `2026-09-09-aggregated-alert-count-misleads-as-recovery.md` |
| 2026-09-08 | 固定回溯窗口会「老过」证据，导致判据静默退化且永不自愈 | — | — | 已修复并验证 | `2026-09-08-fixed-lookback-window-ages-past-evidence.md` |
| 2026-09-06 | Wind 接口会返回「合法但残缺」结果：非空校验拦不住，必须做字段完整性校验 | — | — | — | `2026-09-06-wind-partial-column-return.md` |
| 2026-09-06 | 2026-09-06 · 自愈周报 total=0 的第二层含义：Runbook 从未被执行过（覆盖率盲区） | — | — | — | `2026-09-06-runbook-never-executed-zero-is-not-proof.md` |
| 2026-09-05 | terminated 终止路径 reconcile 漏清 stale running 标记（2026-09-05，✅已升级(2026-09-06)） | — | — | — | `2026-09-05-terminated-reconcile-leaves-stale-running.md` |
| 2026-09-05 | LLM 逐步编排纯脚本任务 = 把执行时间暴露给队列，拥堵时撞硬超时 | P0（三个数据产物停更 73 小时，选股/策略消费陈旧数据） | — | 已修复并验证 | `2026-09-05-llm-orchestrated-script-pipeline-hits-hard-timeout.md` |
| 2026-09-04 | 聚合告警字符串掩盖影响面 30 倍 —— 「alerts 条数 ≠ 受影响对象数」 | — | — | — | `2026-09-04-aggregated-alert-string-hides-blast-radius-30x.md` |
| 2026-09-02 | QUEUED 积压 = 派发卡死，不是「并发槽位耗尽」✅已升级(2026-09-06) | — | 归因错误（推断被全样本统计推翻）+ 哨兵盲区 | — | `2026-09-02-queued-backlog-is-dispatch-stall-not-slot-exhaustion.md` |
| 2026-09-02 | 「90min 硬超时」是名义值 —— 实测占位 2~5.4 小时，被饿死的运行本身就是拥堵放大器 | — | — | — | `2026-09-02-hard-timeout-90min-is-nominal-real-slot-hold-4h.md` |
| 2026-09-02 | DB status 不是真相：`concurrency=3` 是装饰性的，超发→饿死→90min 硬超时 ✅已升级(2026-09-06) | — | 根因定案（推翻 run#66 的两条子结论）+ 测量伪影 + 哨兵盲区 | — | `2026-09-02-db-status-lies-scheduler-concurrency-unenforced.md` |
| 2026-09-02 | 审计日志被单一高频项灌满：93.5% 是同一条死通道告警 | P1（审计留痕失去回溯价值，真实告警被淹没） | — | 已修复并验证 | `2026-09-02-audit-log-drowned-by-single-noisy-check.md` |
| 2026-09-02 | 告警文案滞后于实现：写着「告警零出口」，实际中枢已在兜底推送 | P2（不影响功能，但把处置引向错误路径） | — | 已修复并验证 | `2026-09-02-alert-text-must-follow-implementation.md` |
| 2026-09-02 | 平台 status=ACCEPTED 掩盖「运行被中断」：真故障被降级成模糊的历史错误 | P0（任务白跑、产物未落盘，但巡检显示 🟡 而非 🔴） | — | 已修复并验证（检测侧）；根因在宿主侧，待观察 | `2026-09-02-accepted-status-masks-interrupted-runs.md` |
| 2026-09-01 | 巡检产出物本身也要被验证：幽灵待办 + 差点误报 P0 | — | — | — | `2026-09-01-stale-todo-and-frozen-field-false-alarm.md` |
| 2026-09-01 | 2026-09-01 模拟盘价格无生产者 & deleted_at 与 status 不一致 | — | — | — | `2026-09-01-stale-price-no-producer-and-deleted-at-vs-status.md` |
| 2026-09-01 | naive datetime + 容器 TZ=UTC = 跨日查询系统性漏数据（2026-09-01 巡检 run#45） | — | — | — | `2026-09-01-naive-utc-breaks-date-queries.md` |
| 2026-09-01 | 缺表 ≠ 缺功能：先查写入方再建表（2026-09-01 巡检 run#45） | — | — | — | `2026-09-01-missing-table-vs-missing-writer.md` |
| 2026-09-01 | 观测者效应：巡检自己的探活请求，被自己的新检查项报成了 P1 故障 | — | — | — | `2026-09-01-health-green-masks-401-api-blindspot.md` |
| 2026-09-01 | 修复验证必须等到"下一个自然触发窗口"，且正面闭环也要走告警通道 | — | — | — | `2026-09-01-fix-verification-needs-next-natural-trigger-window.md` |
| 2026-09-01 | 派生字段的「落盘路径 ≠ 验证路径」——修复被自己的验收方式掩盖（run#49, 2026-09-01） | — | — | — | `2026-09-01-derived-field-persist-vs-compute-paths.md` |
| 2026-09-01 | 告警去重键归一 × 滚动窗口计数 = 哨兵报完故障即失明 | P1（监控系统自身失效，影响所有后续告警的可信度） | — | 已修复并验证 | `2026-09-01-dedup-key-times-rolling-window-blinds-sentinel.md` |
| 2026-09-01 | 2026-09-01 巡检中枢自身审计：15 项检查里 4 项是死检查，note 通道零出口 | — | — | — | `2026-09-01-dead-checks-in-ops-center.md` |
| 2026-09-01 | 判定「告警项是否为废弃死通道」的四维实锤法 | — | — | — | `2026-09-01-dead-channel-four-axis-verification.md` |
| 2026-09-01 | 巡检脚本继承 cwd → `pytest tests/` 跑成了被巡检项目的测试 → 每小时自造 14 条 401 | — | — | — | `2026-09-01-cwd-inherited-pytest-pollutes-monitored-api.md` |
| 2026-09-01 | 验收指标的前提：目标表 0 行不等于故障，先查上游有没有数据 | — | — | — | `2026-09-01-acceptance-metric-needs-upstream-nonempty.md` |
| 2026-08-31 | 一圈排查挖出 3 个「错误信息本身不可诊断」的既存缺陷 | — | — | — | `2026-08-31-undiagnosable-error-messages-three-latent-defects.md` |
| 2026-08-31 | 2026-08-31 模拟盘收盘价回写断流 —— 巡检「数据新鲜度」三重盲区 | — | — | — | `2026-08-31-sim-portfolio-close-price-stale.md` |
| 2026-08-31 | 2026-08-31 signal_verify 15:00挂死根治 — 串行网络×超时撞看门狗 + 多源兜底 | — | — | — | `2026-08-31-signal-verify-hang-parallel-sina-fallback.md` |
| 2026-08-31 | 修复「重启早于最后一次写文件」+ 三类静默失效（2026-08-31 统一巡检中枢 run#42） | — | — | — | `2026-08-31-restart-before-final-write-and-silent-degradation.md` |
| 2026-08-31 | last_run_at 平台字段对 ACCEPTED 运行不回写 —— 自动化健康监控基线整体失真 | — | — | — | `2026-08-31-last-run-at-not-written-back-for-accepted-runs.md` |
| 2026-08-31 | 2026-08-31 gitignore 去重验证必须归一化行号 + 文件系统/glob 与 git status 的差集是盲区探针 | — | — | — | `2026-08-31-gitignore-dedup-verify-must-normalize-lineno.md` |
| 2026-08-31 | git status 默认折叠未跟踪目录 → 漂移计数严重低估（run#39） | — | — | — | `2026-08-31-git-status-collapses-untracked-dirs.md` |
| 2026-08-31 | git 提交命令的三个参数陷阱（2026-08-31） | — | — | — | `2026-08-31-git-commit-pathspec-traps.md` |
| 2026-08-31 | 调度框架报「成功」≠ 任务真的成功：验收必须查产物，不能查调度日志 | — | — | — | `2026-08-31-framework-success-signal-is-not-actual-output.md` |
| 2026-08-31 | 漂移「计数持平」会掩盖「组成变化」，且修复动作自身的改动最易漏入库 | — | — | — | `2026-08-31-drift-count-stable-masks-composition-change.md` |
| 2026-08-31 | 去重键不得用"完整告警文案"——改文案会静默重置去重窗口 | 🔴 高（静默失效，且自我掩盖——越优化文案越容易触发） | — | 已修复并验证（24 项断言全过，ruff 通过，生产实跑 4 键→3 键合并成功） | `2026-08-31-dedup-key-must-not-be-volatile-alert-text.md` |
| 2026-08-31 | 容器时区 ≠ 应用时区：看到 UTC 环境不能推断"调度错位" | ✅已升级(2026-09-06) ×2 | — | — | `2026-08-31-container-tz-is-not-app-tz.md` |
| 2026-08-31 | 2026-08-31 · `source $CLAW/...` 自举死锁导致 L3 护栏被静默跳过 | — | — | — | `2026-08-31-claw-env-bootstrap-preamble-silent-skip.md` |
| 2026-08-31 | 回读「未跟踪(??)」项不等于无害：在跑自动化的依赖可能根本没入库，且带首日崩溃 bug | — | — | — | `2026-08-31-active-automation-dep-untracked-and-latent-crash.md` |
| 2026-08-30 | 2026-08-30 08:20 · 验证带副作用的状态函数时误传参数污染真实状态文件 | — | — | — | `2026-08-30-verify-side-effect-state-pollution.md` |
| 2026-08-30 | 共享基建脚本的未提交修复，风险等级高于普通 WIP | — | — | — | `2026-08-30-shared-infra-uncommitted-fix-risk.md` |
| 2026-08-30 | 2026-08-30 回读验证先确认 JSON 路径层级，勿因取值路径错而误判"未落库" | — | — | — | `2026-08-30-readback-path-level-misdiagnosis.md` |
| 2026-08-30 | ✅已升级(2026-08-30) · 纯人工处置类告警按 24h 日推 → 告警疲劳（已修） | — | — | — | `2026-08-30-manual-only-alert-dedup-fatigue.md` |
| 2026-08-30 | docker logs 按流分流：只读 stdout 会让日志检测静默失效 | — | — | — | `2026-08-30-docker-logs-stdout-stderr-split.md` |
| 2026-08-30 | 删除类变更的危害面核验：grep 文件名 ≠ grep import | — | — | — | `2026-08-30-deletion-impact-check-grep-import-not-filename.md` |
| 2026-08-30 | 2026-08-30 — 磁盘巡检脚本 glob 命名不匹配导致静默漏扫 | — | — | — | `2026-08-30-cleanup-script-glob-silent-noop.md` |
| 2026-08-30 | 基线统计必须按调度周期分层（不同 RRULE 不可混算 p50/p90） | — | — | — | `2026-08-30-baseline-must-stratify-by-schedule.md` |
| 2026-08-30 | 2026-08-30 审计日志滚动窗口被不可自愈项淹没 | — | — | — | `2026-08-30-audit-log-window-flooded-by-noise.md` |
| 2026-08-30 | ✅已升级(2026-08-30) · 告警「症状报对了、药方开错了」——remediation 建议必须实证验证 | — | — | — | `2026-08-30-alert-remediation-must-be-verified.md` |
| 2026-08-30 | 2026-08-30 看门狗误把「告警日志」当「心跳日志」推断漏跑（近失，已自查纠正） | — | — | — | `2026-08-30-alert-log-is-not-heartbeat-log.md` |
| 2026-08-27 | ✅已升级(2026-10-11)→R48 —— push_feishu 去重键透传失效（P1·系统性） | — | — | — | `2026-08-27-push_feishu-dedupe-bug.md` |
| 2026-08-24 | 2026-08-24 signal_verify Wind 日限刷屏 — _check_query_limit 去重修复 | — | — | — | `2026-08-24-signal-verify-wind-quota-spam.md` |
| 2026-08-24 | automation_update 缺 ID 前缀静默新建影子记录，PAUSE 假成功 | — | — | — | `2026-08-24-automation-update-id-prefix-shadow-record.md` |
| 2026-08-24 | automation-refusal-32603-upstream-500 | — | — | — | `2026-08-24-automation-refusal-32603-upstream-500.md` |
| 2026-08-24 | 自动化健康误报:工作日(HOURLY+BYDAY=MO-FR)自动化周末被误判 critical | — | — | — | `2026-08-24-automation-health-weekday-stale-false-positive.md` |
| 2026-08-21 | 2026-08-21 Wind 配额耗尽致辩论降级 — 数据链路去 Wind 化修复 | — | — | — | `2026-08-21-debate-wind-quota-degradation.md` |
| 2026-08-20 | 时间相关 flaky 测试:sim_trade 止盈测试每月 20 号后必红 | — | — | — | `2026-08-20-time-dependent-test-flaky.md` |
| 2026-08-20 | automation-429-quota-cluster | — | — | — | `2026-08-20-automation-429-quota-cluster.md` |
| 2026-08-19 | 2026-08-19 鱼盆 RSS 服务认证失效（HTTP 401）诊断记录 | — | — | — | `2026-08-19-wx-rss-401-auth-failure.md` |
| 2026-08-17 | ✅已升级(2026-08-23)：Claw 路径为外置 SSD 符号链接，自动化存在静默失败风险 | — | — | — | `2026-08-17-claw-symlink-external-drive.md` |
| 2026-08-12 | 2026-08-12 WorkBuddy UI 历史对话消失：根因是云端列表接口 404（correction+insight → ✅已升级(2026-08-16)） | — | — | — | `2026-08-12-wb-history-conversation-404.md` |
| 2026-08-12 | ✅已升级(2026-08-16) · 统一巡检中枢整窗失联（~5.3h） | — | — | — | `2026-08-12-unified-ops-center-outage.md` |
