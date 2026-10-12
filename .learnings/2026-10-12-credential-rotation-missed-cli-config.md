# ★候选铁律 —— 「凭据轮换」漏掉 CLI 的**最高优先级**配置源，导致 19 个交易日静默空产

**日期**：2026-10-12（鱼盆数据提取自动化晨间巡检）
**等级**：P0（主数据源连续 19 个交易日产出空壳，早报板块轮动一直用 3 周前的旧数据）
**类型**：incomplete-consumer-sweep（轮换覆盖不全 / 声明式「已修复」≠ 真实链路已修复）
**状态**：已修复（全局配置 + connector token 两处同步为在用值，端到端复测通过）

---

## 1. 现象

- 鱼盆主生成器 `1784605310235` 自 **2026-09-11** 起，连续 **19 个交易日**产物为：
  - `sector_rotation` **0/14** 行（632B 空壳）
  - `yupen_trend` **5/20** 行（只有雅虎外盘的日经/台湾加权/韩国综合/黄金/白银）
- 每天自行隔离到 `output/yupen/archive_failed/`，**从不报错**，只是"少了数据"。
- 同期 `read_yupen` 一直回落 2026-09-21 的旧数据，`stale_note` 文案却写"通常滞后1天"。

## 2. 根因（10-12 实测定位，与以往结论**不同**）

⚠️ 此前长期记为「Wind 账户积分耗尽，需充值」——**10-12 实测证明根因已变**：

```
$ node cli.mjs call index_data get_index_kline '{"windcode":"000300.SH","begin_date":"2026-10-09","end_date":"2026-10-12"}'
{ "ok": false, "code": "backend_error", "message": "认证失败：无效的apikey" }
```

对照 curl 直测 MCP 端点，返回 **HTTP 401**：

```
{"code":401,"message":"apiKey 无效或已失效，请前往官网个人中心重新生成apiKey"}
```

**真因**：`~/.wind-aifinmarket/config` 里仍是 **2026-07-11 写入的旧 key**（sha1_8=`23b56744`），
而 **PA-013（2026-10-11 16:36）的 token 轮换只覆盖了两处**：

| 消费方 | 轮换时是否更新 | 10-12 实测状态 |
|---|---|---|
| `Claw/.env`（`WIND_API_KEY`/`WIND_MCP_TOKEN`） | ✅ | 新值，**有效** |
| `~/.workbuddy/mcp.json`（5 个 Wind server） | ✅ | 新值，有效 |
| **`~/.wind-aifinmarket/config`** | ❌ **漏** | 旧值 → **401** |
| **`~/.workbuddy/connectors/*/tokens/wind_analytics_key.txt`** | ❌ **漏** | 旧值 → 待用即坏 |

而 `cli.mjs` 的凭据解析顺序是：

```
~/.wind-aifinmarket/config   ← 最高优先级（存在即 return，不再往下看）
  > <skill>/config.json
  > 环境变量 WIND_API_KEY
```

→ **`.env` 里明明是新 key，但 CLI 永远读不到**。改 `.env` 完全不影响 CLI。

❌ 而 PA-013 的 `resolution` 写的是「**本机完成更换与验证**」「凭据有效性实测 → HTTP 200 握手成功」——
那句验证是**用脚本/curl 直接拿 `.env` 的新值测的**，**没有走 CLI 的真实解析链**。
→ 「自报已修复」与「真实链路可用」之间隔着一个**没被枚举的消费方**。

## 3. 修法与证据

```bash
# ① 备份 + 校验（先复制→验证→再改）
cp -p ~/.wind-aifinmarket/config /tmp/wind_aifinmarket_config.bak-<ts>
# ② 同步在用值（原子写 + 保持 0600）→ 回读比对 sha1_8 一致
# ③ 复核全消费方：旧值残留 0，新值 7 处（含 /Volumes/ZHITAI 软链同源路径）
```

| 验证项 | 修前 | 修后 |
|---|---|---|
| CLI `index_data get_index_kline` | `backend_error 无效的apikey` | `isError: false`，返回真实 K 线 |
| 主生成器 `run_yupen_primary.py` | 板块 0/14 · 趋势 5/20 | **板块 14/14 · 趋势 20/20**，`freshness=today` |
| `read_yupen --freshness-only` | `stale` / 2026-09-21 | **`today` / 2026-10-12** |
| 活跃配置类文件持旧值数 | 2（aifinmarket config + connector token） | **0** |

## 4. 教训（待周度升级为铁律）

1. 🔴 **轮换凭据必须枚举「全部消费方」，且枚举依据是「程序读哪里」，不是「我记得改过哪里」**。
   `~/.wind-aifinmarket/config` 这类**第三方 CLI 的隐藏优先级配置**极易被漏。
2. 🔴 **验证必须走真实解析链，不能用「等价的直连」代替**。
   curl 拿 `.env` 的新值测通了 ≠ CLI 通 —— 判据是「跑一次真实的 CLI/SDK 调用」，不是「值本身有效」。
3. 🔴 **同一凭据 ≥2 个副本时，副本之间没有任何一致性约束 = 必然漂移**。
   修完要重扫：`旧值残留 == 0` 才算闭环（本次扫出 39 处命中，其中 37 处是历史快照/会话记录，
   **2 处是活跃消费方** —— 不区分「历史副本」与「活跃消费方」会把 2 个真问题淹在 37 个噪音里）。
4. 🔴 **「积分耗尽」这类既往结论会继续被复制**。根因变了而结论不变，就会让所有人往错方向修
   （本次之前 5 天里，每份日志都在劝用户去充值，而真正坏的是 key）。

## 5. 遗留

- 旧值（已失效）仍存在于：`mcp.json.bak-*`、`.workbuddy/file-history/*`、`projects/*.jsonl` 会话记录、
  以及本次处置的 `/tmp/*.bak-*`。均为**历史副本**，不构成活跃风险；是否清理待用户确认（不自行删）。
- `stale_note` 文案仍写"通常滞后1天"，与实际严重偏离（长期遗留，非本次引入）。
