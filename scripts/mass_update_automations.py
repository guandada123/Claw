#!/usr/bin/env python3
"""
mass_update_automations.py — 批量更新 WorkBuddy 自动化模型分配

作用：
  根据 model-assignment-optimization.md 的推荐方案，
  批量更新 22 个 WorkBuddy 自动化的 modelId。

用法：
  python mass_update_automations.py [--dry-run] [--apply]
  
  --dry-run   预览变更（默认模式，不实际修改）
  --apply     实际执行变更（需要 WorkBuddy environment）

输出：
  - JSON 格式的变更清单（可被 automation_update 工具读取）
  - 变更汇总表
"""

import json
import sys

# ============================================================
# 变更定义：{ automation_id: (current_model, new_model, reason) }
# ============================================================
CHANGES = {
    # ---- A股盘中监控 ----
    "automation-1780614267086": {  # 财报预警
        "name": "📊 财报预警",
        "from": "hy3-preview",
        "to": "deepseek-v4-flash",
        "reason": "模板化财报分析，Flash足够",
    },
    # ---- A股盘前/收盘 ----
    "automation-1780362908045": {  # 盘前分析
        "name": "📊 盘前分析",
        "from": "glm-5.0-turbo",
        "to": "deepseek-v4-flash",
        "reason": "纯信息聚合+格式化输出，Flash足够",
    },
    "automation-1780362957821": {  # 收盘回顾
        "name": "📊 收盘回顾",
        "from": "glm-5.0-turbo",
        "to": "deepseek-v4-flash",
        "reason": "数据汇总+标准模板，Flash够用",
    },
    "automation-1780651521899": {  # 公众号投资早报
        "name": "📊 公众号投资早报",
        "from": "deepseek-v4-pro",
        "to": "deepseek-v4-flash",
        "reason": "模板化摘要+格式化输出，无需Pro",
    },
    # ---- A股周期总结 ----
    "automation-1780651521944": {  # 股票池技术体检
        "name": "📊 股票池技术体检",
        "from": "glm-5.1",
        "to": "deepseek-v4-flash",
        "reason": "技术指标分析模板化，Flash可替代GLM",
    },
    "automation-1780651521929": {  # 宏观数据周报
        "name": "📊 宏观数据周报",
        "from": "hy3-preview",
        "to": "deepseek-v4-flash",
        "reason": "数据汇总+格式化，无需推理",
    },
    # ---- 美股监控 ----
    "automation-1780615006148": {  # 美股盘前分析
        "name": "🇺🇸 美股盘前分析",
        "from": "deepseek-v4-pro",
        "to": "deepseek-v4-flash",
        "reason": "期货行情+新闻摘要，Flash足够",
    },
    # ---- 投顾操盘 ----
    "automation-1780632805530": {  # 每日复盘
        "name": "📈 每日复盘",
        "from": "glm-5.0-turbo",
        "to": "deepseek-v4-flash",
        "reason": "收盘数据+K线截图交叉验证，模板化输出",
    },
    # 每周总结和月度总结：GLM-5.1 (¥3.5/万) 比 Pro (¥4/万) 更便宜，保留不动
    # ---- 系统维护 ----
    "automation-1780964240589": {  # 文章归档索引
        "name": "📚 文章归档索引",
        "from": "deepseek-reasoner",
        "to": "deepseek-v4-flash",
        "reason": "纯向量化入库，无需Reasoner推理能力",
    },
}


# ============================================================
# 成本估算
# ============================================================
MODEL_PRICES = {
    "deepseek-v4-flash":     0.5,
    "deepseek-v4-pro":       4.0,
    "glm-5.0-turbo":         3.0,
    "glm-5.1":               3.5,
    "kimi-k2.6":             8.0,
    "hy3-preview":           2.0,
    "deepseek-reasoner":     8.0,
}

# 估算各自动化的日均输入 Token（基于上一轮统计）
DAILY_INPUT_TOKENS = {
    "automation-1780614267086": 2000,   # 财报预警
    "automation-1780362908045": 3000,   # 盘前分析
    "automation-1780362957821": 3000,   # 收盘回顾
    "automation-1780651521899": 2500,   # 公众号投资早报
    "automation-1780651521944": 2000,   # 股票池技术体检
    "automation-1780651521929": 2000,   # 宏观数据周报
    "automation-1780615006148": 2500,   # 美股盘前分析
    "automation-1780632805530": 3000,   # 每日复盘
    "automation-1780632815958": 3000,   # 每周总结
    "automation-1780632824878": 3000,   # 月度总结
    "automation-1780964240589": 1500,   # 文章归档索引
}

# 日均调用次数
DAILY_CALL_COUNT = {
    "automation-1780614267086": 2,    # 财报预警: 2次/天
    "automation-1780362908045": 1,    # 盘前分析
    "automation-1780362957821": 1,    # 收盘回顾
    "automation-1780651521899": 1,    # 公众号投资早报
    "automation-1780651521944": 0.14, # 股票池技术体检: 1次/周
    "automation-1780651521929": 0.14, # 宏观数据周报: 1次/周
    "automation-1780615006148": 1,    # 美股盘前分析
    "automation-1780632805530": 1,    # 每日复盘
    "automation-1780632815958": 0.14, # 每周总结: 1次/周
    "automation-1780632824878": 0.03, # 月度总结: 1次/月
    "automation-1780964240589": 1,    # 文章归档索引
}


def compute_savings(aid: str, change: dict) -> dict:
    """计算单个变更的节省"""
    tokens = DAILY_INPUT_TOKENS.get(aid, 2000)
    calls = DAILY_CALL_COUNT.get(aid, 1)
    old_price = MODEL_PRICES.get(change["from"], 4.0)
    new_price = MODEL_PRICES.get(change["to"], 0.5)

    old_daily = tokens * calls * old_price / 10000
    new_daily = tokens * calls * new_price / 10000
    monthly_save = (old_daily - new_daily) * 30

    return {
        "old_daily": round(old_daily, 4),
        "new_daily": round(new_daily, 4),
        "monthly_save": round(monthly_save, 2),
    }


def print_summary(dry_run: bool):
    """打印变更汇总"""
    mode = "🔍 DRY RUN (预览)" if dry_run else "⚡ 实际执行"
    print(f"\n{'=' * 60}")
    print(f"  批量更新自动化模型分配 — {mode}")
    print(f"  变更数量: {len(CHANGES)} 个")
    print(f"{'=' * 60}\n")

    total_monthly_save = 0
    for aid, change in sorted(CHANGES.items()):
        savings = compute_savings(aid, change)
        total_monthly_save += savings["monthly_save"]

        print(f"  [{change['name']}]")
        print(f"    ID: {aid}")
        print(f"    模型: {change['from']:>20} → {change['to']:<20}")
        print(f"    日省: ¥{savings['old_daily']:.4f} → ¥{savings['new_daily']:.4f}")
        print(f"    月省: ¥{savings['monthly_save']:.2f}")
        print(f"    理由: {change['reason']}")
        print()

    print(f"{'─' * 60}")
    print(f"  总计月节省: ¥{total_monthly_save:.2f}")
    print(f"{'─' * 60}\n")

    # 输出 JSON（供外部工具解析）
    output = {
        "mode": "dry_run" if dry_run else "apply",
        "total_changes": len(CHANGES),
        "total_monthly_save": total_monthly_save,
        "changes": [
            {
                "id": aid,
                "name": c["name"],
                "from": c["from"],
                "to": c["to"],
                "savings": compute_savings(aid, c),
            }
            for aid, c in sorted(CHANGES.items())
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


def generate_patch_commands():
    """生成可执行的手动更新命令列表（用于逐个调用 automation_update）"""
    print("\n📋 手动更新命令清单（供 automation_update 工具逐条执行）:\n")
    for aid, change in sorted(CHANGES.items()):
        print(f"  # {change['name']}: {change['from']} → {change['to']}")
        print(f"  # 理由: {change['reason']}")
        print(f"  automation_update(mode='update', id='{aid}', modelId='{change['to']}')")
        print()


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv or len(sys.argv) == 1
    apply_mode = "--apply" in sys.argv

    if apply_mode:
        print_summary(False)
    else:
        print_summary(True)
        generate_patch_commands()
