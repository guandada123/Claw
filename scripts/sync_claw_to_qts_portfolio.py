#!/usr/bin/env python3
"""sync_claw_to_qts_portfolio.py — 将 Claw 模拟盘 portfolio 镜像到 QTS 共享目录

根治 P2「同步断流」：Claw 模拟盘演进后，QTS shared/claw_data/portfolio.json 长期停更
（2026-06-25），导致 QTS 实盘风控 / 飞书日报拿旧数据。本脚本在收盘后把 Claw 最新
模拟盘扁平 portfolio 同步过去。

- 仅覆盖模拟盘部分；QTS 实盘 live 数据来自独立链路（国金 QMT 同步），不在本脚本范围。
- 带回滚备份（/tmp，保留最近 5 份）+ JSON 合法性校验 + 内容复验。
- 幂等：源与目标一致时跳过写，不产生无谓备份。
- 目标缺失（首次同步/被清理）时跳过备份而非崩溃，回滚时按「有备份还原 / 无备份删除」恢复。

注意：QTS execution-service 消费方（daily_risk_monitor / feishu_daily_report）已改为
兼容扁平 positions 结构（2026-08-26）。若 QTS 以 Docker 运行，源码改动需重建镜像生效；
但本数据同步写入 shared 挂载目录，立即对容器内可见。
"""
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

SRC = Path("/Users/guan/WorkBuddy/Claw/.workbuddy/data/simulation/portfolio.json")
DST = Path("/Users/guan/WorkBuddy/QuantTradingSystem/shared/claw_data/portfolio.json")
BACKUP_DIR = Path("/tmp/qts_portfolio_sync_backup")
KEEP_BACKUPS = 5


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}")


def main() -> int:
    if not SRC.exists():
        log(f"[ERROR] 源文件不存在: {SRC}")
        return 1
    try:
        src_data = json.loads(SRC.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"[ERROR] 源 JSON 解析失败: {e}")
        return 1

    pos = src_data.get("positions", {})
    cfg = src_data.get("config", {})
    log(
        f"源: {len(pos)} 只持仓, "
        f"initial_capital={cfg.get('initial_capital')}, "
        f"total_assets={src_data.get('total_assets')}"
    )

    # 幂等：目标存在且内容一致则跳过
    if DST.exists():
        try:
            if json.loads(DST.read_text(encoding="utf-8")) == src_data:
                log("✅ 目标已是最新，跳过写入")
                return 0
        except Exception:
            pass  # 目标损坏，继续覆盖

    # 备份目标（写前）。目标缺失（首次同步 / 被清理）时不得崩 —— 原实现直接 copy2 会
    # 抛 FileNotFoundError，自动化按「退出码非 0 即告警」判失败，属于首日误报。
    bak = None
    if DST.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = BACKUP_DIR / f"portfolio_{ts}.json.bak"
        shutil.copy2(DST, bak)
        # 清理旧备份，保留最近 KEEP_BACKUPS 份
        olds = sorted(
            BACKUP_DIR.glob("portfolio_*.json.bak"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in olds[KEEP_BACKUPS:]:
            old.unlink()
    else:
        log(f"[WARN] 目标不存在，跳过备份（首次同步或目标被清理）: {DST}")

    def _rollback(reason: str) -> int:
        """恢复写入前状态：有备份则还原，无备份（原本不存在）则删除，不留半成品。"""
        log(f"[ERROR] {reason}，回滚")
        if bak is not None:
            shutil.copy2(bak, DST)
        elif DST.exists():
            DST.unlink()
        return 1

    # 写入（确保合法）
    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(json.dumps(src_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 复验
    try:
        reread = json.loads(DST.read_text(encoding="utf-8"))
    except Exception as e:
        return _rollback(f"复验读取失败: {e}")
    if reread != src_data:
        return _rollback("复验内容不一致")

    log(f"✅ 已同步到 {DST}（备份: {bak}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
