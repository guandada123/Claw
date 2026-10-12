# Learnings INDEX — Claw

> ⚠️ **本文件由 `learnings_index.py` 自动生成，请勿手改**（改了会在下次生成时被覆盖）。
> 权威内容是 `.learnings/*.md` 正文；本文件只是**派生视图**。
> ⚠️ 手工维护的 `LEARNINGS.md` 里的**浓缩条目不在本表内**（它已停更，见其抬头说明）。
> 生成时间：2026-10-12 20:12　｜　条目数：**102**

- **按类型**：correction×5　blind-spot×3　取证口径×2　false-positive×2　分析口径错误×2　归因错误×2　process / data-integrity×1　machine-maintenance / correction×1　incomplete-consumer-sweep×1　工具语义误用×1　self-checker-blind×1　silent-dead-mechanism×1　取证口径错误×1　time-dependent test×1　environment-drift / silent-fallback×1　silent-failure / false-success×1　silent-pass×1　false_positive_generator / counting_trap×1　engineering_practice×1　best_practice×1　knowledge_gap×1　根因定案×1　（另有 70 条未标注该字段）
- **按等级**：P1×21　P2×8　P0×4　P0 级风险 × 手动取证险些失手×1　🔴 高×1　✅已升级(2026-09-06) ×2×1　（另有 66 条未标注该字段）

| 日期 | 标题 | 等级 | 类型 | 状态 | 文件 |
|---|---|---|---|---|---|
| 2026-10-12 | ★教训：单位口径差会造出**假警报**，而基于假警报的"修复"比不修更坏 | P2（无实际损失；但**差一步就把完好的数据改坏**） | 取证口径（单位/编码未对齐） | 已识别并挡住，无需修改 | `2026-10-12-unit-mismatch-fake-alarm.md` |
| 2026-10-12 | ★教训：扫描类检查必须锚定「**实际执行行**」—— 我差点被"修复说明的散文"骗到改坏 3 条自动化 | P1（若照第一反应动手，会对**3 条今天要跑的自动化**做无谓改动 —— 白改 + 引入风险） | 取证口径（正则扫到了散文而非命令） | 已在动手前自行拦下（导出 prompt 后 grep 才发现已修） | `2026-10-12-scan-prose-not-command-line.md` |
| 2026-10-12 | `ruff format` 显式点名 `.json` 会给它加尾逗号 → 严格 JSON 立刻失效（★ 升级候选） | — | — | 已复现、已记录、已在门禁 docstring 留下警告；**未**给 ruff 提 issue（是否算 bug 取决于它是否 | `2026-10-12-ruff-format-destroys-json.md` |
| 2026-10-12 | ★升级候选：恢复「积压消费通道」时，先给消费端补「陈旧输入」防护，再让它首跑 | P1 | process / data-integrity | 已落地（首跑前补丁 + 一次性核验在挂） | `2026-10-12-preflight-stale-guard-for-backlog-consumer.md` |
| 2026-10-12 | ★升级候选：闸门的两处「缺一类」—— 新建文档类候选被整类误杀 + 登记端与落地端口径不一致 | P2（不误伤数据；但**让一整类候选永远进不了落地通道**，且两条闸门各说各话时无人发现） | false-positive（判据缺状态）+ 口径不一致（登记端松、落地端严） | 缺陷①已修（`7b5ff78`）；缺陷②已记录 + 数据侧补齐，口径统一留作提案 | `2026-10-12-gate-missing-new-file-state-and-two-calibers.md` |
| 2026-10-12 | 易变值的「自由文本副本」：加判据治不了，删掉副本才是解法 —— 索引头里一对过期数字，让读者信任了**已经失效的可见边界** | P2（不造成数据丢失，但**误导读者对「记忆可见范围」的判断**，且历经 6 次同类复发仍未被任何检查覆盖） | blind-spot（声明腐烂 · 检查只覆盖枚举格式，散文形态永远在覆盖之外） | 已修复（重写索引头第 6 行，去 4 个易变值），2026-10-12 | `2026-10-12-free-text-copy-of-volatile-value-will-rot.md` |
| 2026-10-12 | 兜底源「返空」与「返全市场」长得一样：一只 ETF 名下凭空多出 9 行别家公司的财报披露 | P1（**误报/误归因**类：他方数据被挂到本持仓名下，若字段名命中的话会直接产出「某持仓今日披露」的假告警） | blind-spot（降级路径未做归属校验 · 「缺键 ≠ 空集」同族） | 已修复并验证，2026-10-12 | `2026-10-12-fallback-source-must-verify-attribution.md` |
| 2026-10-12 | ★升级候选：退役/停用「常驻机制」的三条硬动作（cron 留因注释 / launchd 双动作 / mail spool 出口） | P1 | machine-maintenance / correction | 已落地（马维斯桥接退役时执行） | `2026-10-12-decommission-hard-actions.md` |
| 2026-10-12 | ★ 凭据轮换必须枚举「全部持有者」，且按文件清单而非目录猜测（2026-10-12） | — | — | — | `2026-10-12-credential-rotation-must-enumerate-all-holders.md` |
| 2026-10-12 | ★候选铁律 —— 「凭据轮换」漏掉 CLI 的**最高优先级**配置源，导致 19 个交易日静默空产 | P0（主数据源连续 19 个交易日产出空壳，早报板块轮动一直用 3 周前的旧数据） | incomplete-consumer-sweep（轮换覆盖不全 / 声明式「已修复」≠ 真实链路已修复） | 已修复（全局配置 + connector token 两处同步为在用值，端到端复测通过） | `2026-10-12-credential-rotation-missed-cli-config.md` |
| 2026-10-12 | 2026-10-12 · `bash <python脚本>` 当门禁 = 必然非 0 = 静默跳过 ★升级候选 | — | — | — | `2026-10-12-bash-on-python-gate-is-always-nonzero.md` |
| 2026-10-12 | ★升级候选：`automation_update` 的 prompt 是**全量覆盖**，不是补丁 —— 我差点把一份 2370 字节的 prompt 抹成 1 行 | P1（若未及时发现，一个 ACTIVE 自动化的**全部作业指令会被一行频率说明替换**， | 工具语义误用（字段是全量替换，被我当成增量修改） | 1 分钟内发现并**已完全恢复**（逐字节 diff 校验通过） | `2026-10-12-automation-update-prompt-is-full-replace.md` |
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
