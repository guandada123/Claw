#!/usr/bin/env python3
"""
merge_signal_ranking.py — 合并 RSS 账号权重 + 外部发现账号 → 统一排名

读取:
  - signal_verify_report.json  (RSS 账号历史胜率)
  - discovered_accounts.json   (红狐发现的新候选)
输出:
  - data/signal_ranking.json   (统一排名，供早报引用)

用法:
  python3 scripts/merge_signal_ranking.py
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_VERIFY_REPORT = _PROJECT_ROOT / ".workbuddy" / "data" / "signal_verify_report.json"
_DISCOVERED = _PROJECT_ROOT / "data" / "discovered_accounts.json"
_OUTPUT = _PROJECT_ROOT / "data" / "signal_ranking.json"


def merge() -> dict:
    now = datetime.now()

    # RSS 历史数据
    rss_ranking = []
    if _VERIFY_REPORT.exists():
        report = json.loads(_VERIFY_REPORT.read_text(encoding="utf-8"))
        for row in report.get("ranking", []):
            wr = row.get("win_rate")
            rss_ranking.append({
                "name": row["account"],
                "source": "RSS付费订阅",
                "signals": row.get("total", 0),
                "verified": row.get("verified", 0),
                "win_rate": wr,
                "avg_return": row.get("avg_return"),
                "weighted_score": (wr or 0) * row.get("total", 0),  # 胜率×信号数 = 综合权重
            })

    # 外部发现（v2: 含初步命中率 hit_rate）
    discovered = []
    if _DISCOVERED.exists():
        data = json.loads(_DISCOVERED.read_text(encoding="utf-8"))
        for r in data.get("candidates", [])[:30]:
            ext_hr = r.get("hit_rate")  # 红狐 v2 计算的初步命中率
            ext_verified = r.get("stocks_verified", 0)
            discovered.append({
                "name": r["name"],
                "source": "红狐发现",
                "signals": ext_verified,  # 已验证的股票提及数
                "verified": ext_verified,
                "win_rate": ext_hr,  # 初步命中率（与 RSS 胜率同口径对比）
                "avg_return": None,
                "articles": r.get("articles", 0),
                "weighted_score": (ext_hr or 0) * ext_verified,
                "keywords": r.get("keywords", []),
            })

    # 合并 + 排序：按胜率×信号量加权分，有胜率的排前面
    all_ranking = rss_ranking + discovered
    # 排序优先级: 有胜率>无胜率; 加权分高>低
    all_ranking.sort(key=lambda x: (
        -(x["win_rate"] is not None and x["win_rate"] > 0),
        -x["weighted_score"],
        -x["signals"],
    ))

    output = {
        "generated_at": now.isoformat(),
        "rss_count": len(rss_ranking),
        "discovered_count": len(discovered),
        "total": len(all_ranking),
        "ranking": all_ranking,
    }

    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


if __name__ == "__main__":
    r = merge()
    top = r["ranking"][:20]
    print(f"合并排名 ({r['rss_count']} RSS + {r['discovered_count']} 发现 = {r['total']}个)")
    print(f"{'排名':<4s} {'':3s} {'名称':20s} {'来源':10s} {'胜率':>8s} {'信号':>5s} {'文章':>5s}")
    print("-" * 65)
    for i, item in enumerate(top):
        wr = f"{item['win_rate']:.1f}%" if item['win_rate'] else "N/A"
        icon = "⭐" if item['win_rate'] and item['win_rate'] >= 60 else ("✅" if item['win_rate'] and item['win_rate'] >= 40 else "⚪")
        src = item['source'][:10]
        arts = item.get('articles', 0)
        print(f"  {i+1:<3d} {icon:1s} {item['name']:<20s} {src:<10s} {wr:>8s} {item['signals']:>5d} {arts:>5d}")

    # 对比：外部高命中 vs RSS 低命中
    print(f"\n{'='*60}")
    print("📊 对比建议：外部号命中率 >= RSS 最低命中号 → 推荐接入")
    rss_with_hr = [x for x in r['ranking'] if x['source'] == 'RSS付费订阅' and x['win_rate']]
    ext_with_hr = [x for x in r['ranking'] if x['source'] == '红狐发现' and x['win_rate'] and x['win_rate'] >= 30]

    if rss_with_hr:
        rss_min = min(rss_with_hr, key=lambda x: x['win_rate'])
        print(f"  RSS 最低命中号: {rss_min['name']} ({rss_min['win_rate']}%)")
        if ext_with_hr:
            better = [x for x in ext_with_hr if x['win_rate'] and x['win_rate'] >= rss_min['win_rate']]
            if better:
                print(f"  ✅ 优于最低RSS的外部号 ({len(better)}个):")
                for b in sorted(better, key=lambda x: -(x['win_rate'] or 0))[:5]:
                    print(f"     {b['name']} ({b['win_rate']}%) — 建议接入RSS")
            else:
                print("  ⚪ 暂无外部号达到RSS最低门槛")
