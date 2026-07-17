#!/usr/bin/env python3
"""
fetch_yupen_rss.py — 鱼盆数据 RSS 直连抓取器（替代失效的 WebSearch 方案）

背景：
  原 🐟 鱼盆数据提取 自动化用 WebSearch 搜猫笔叨文章，几乎搜不到带鱼盆表格的那篇，
  导致每天写 no_data。实际鱼盆表格是「图片」形式嵌在猫笔刀系列公众号文章里，需走 RSS
  直连抓取文章 + 图片，再 OCR/视觉识别。

本脚本职责（自动化内执行，无需人工）：
  1. 通过 wx_rss_auth 直连 RSS（带重试/退避，应对后端间歇性 404）
  2. 猫笔叨每天都会发鱼盆表，鱼盆表几乎总是各账号「最新」那篇文章。
     因此优先取每个账号最新文章，抓取详情并下载表格图片；
     评分（正文强/弱信号 + 标题关键词 + 图片数）仅用于验证该文「确实含鱼盆表」，不作为主选逻辑。
  3. 通过单篇接口（/api/article）取 images 列表，下载鱼盆表格截图到 output/yupen/
  4. 写 yupen_<date>_raw.json：含 date / source / article_title / article_id(url)
     / image_path / images / fetch_time / status='pending_ocr'
  5. 同时写 yupen_<date>_no_data.json（仅当各账号最新文章均未含鱼盆表时）

注意：表格是图片，结构化识别由下游 OCR/视觉步骤完成（或后续接入 OCR）。
      本脚本只负责可靠地把「最新鱼盆图片」落盘，供下游消费。

用法：
  python3 fetch_yupen_rss.py                 # 抓取今日鱼盆图片
  python3 fetch_yupen_rss.py --date 2026-07-14
  python3 fetch_yupen_rss.py --article-id https://mp.weixin.qq.com/s/xxxx  # 指定漏抓文章补抓

注意：
  - 本脚本下载的图片以「抓取日期」命名（yupen_<date>_<i>.png），对应的 raw 文件也是 yupen_<date>_raw.json。
  - 下游 LLM 视觉 OCR 生成的结构化文件以「数据表头日期」命名（yupen_<data_date>_sector_rotation.json
    / yupen_<data_date>_yupen_trend.json）。两者日期可能不一致（如 7/17 抓取的文章表头日期为 7/16），
    这是正常设计，不是 OCR 卡住或缺失。
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wx_rss_auth as rss

OUT_DIR = Path("/Users/guan/WorkBuddy/Claw/output/yupen")

# 猫笔刀系列账号昵称优先级
MBD_NICK_HINTS = ["猫笔刀", "猫笔叨", "猫哥"]

# 鱼盆数据帖标题关键词（title 命中权重最高）
YUPEN_TITLE_KWS = ["鱼盆", "板块轮动", "关注目标", "明天", "下周", "模型", "回测"]

# 鱼盆数据帖正文强信号（命中即高度疑似鱼盆数据帖）
YUPEN_BODY_STRONG = ["鱼盆模型回测数据", "贴下最新鱼盆", "鱼盆回测模型", "最新鱼盆", "鱼盆模型"]

# 鱼盆数据帖正文弱信号（累计加分）
YUPEN_BODY_WEAK = ["板块", "轮动", "偏离", "MA20", "历史回测", "区间涨幅", "量比", "排名", "临界值", "No区域", "转No"]


def _now_beijing():
    return datetime.now(UTC) + timedelta(hours=8)


def _fetch_with_retry(fakeid, limit=15, max_retry=6):
    """带退避重试拉取文章列表（后端间歇性 404/SSL 抖动）"""
    for attempt in range(max_retry):
        try:
            arts, ok = rss.fetch_all_articles(since=0, limit=limit, fakeid=fakeid)
            if ok and arts:
                return arts, True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(3 + attempt * 2)
    return [], False


def _find_mbd_fakeids():
    """返回按优先级排序的猫笔刀系列 fakeid 列表"""
    subs = rss.get_subscriptions().get("subscriptions", [])
    ranked = []
    for s in subs:
        nick = s.get("nickname", "") or ""
        for prio, hint in enumerate(MBD_NICK_HINTS):
            if hint in nick:
                ranked.append((prio, s["fakeid"], nick))
                break
    ranked.sort(key=lambda x: x[0])
    seen = set()
    out = []
    for _, fid, nick in ranked:
        if fid not in seen:
            seen.add(fid)
            out.append((fid, nick))
    return out


def _get_article_detail(art_id):
    """通过单篇接口取完整数据：含 images / plain_content / content（带重试）"""
    import requests
    url = art_id if str(art_id).startswith("http") else art_id
    for attempt in range(8):
        try:
            resp = requests.post(
                f"{rss.WX_RSS_API_BASE}/api/article",
                headers={**rss._headers(), "Content-Type": "application/json"},
                json={"url": url},
                timeout=15,
                verify=False,  # noqa: S501  # nosec 本地RSS自签证书，禁用校验保障抓取(公开文章无敏感凭证)
            )
            data = resp.json()
            if data.get("success") and isinstance(data.get("data"), dict):
                return data["data"]
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2 + attempt * 2)
    return {}


def _score(body, title):
    """评分：强信号 +30/个；弱信号 +3/命中；标题关键词 +10/命中"""
    s = 0
    for h in YUPEN_BODY_STRONG:
        if h in body:
            s += 30
    for h in YUPEN_BODY_WEAK:
        s += body.count(h) * 3
    for h in YUPEN_TITLE_KWS:
        if h in title:
            s += 10
    # 图片多（表截图）加权
    return s


def _download_image(url, dest):
    """下载图片到 dest，成功返回 True"""
    try:
        import requests
        resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        return dest.stat().st_size > 1000
    except Exception:  # noqa: BLE001
        return False


def _write_no_data(target_date, note):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": target_date,
        "status": "no_data",
        "note": note,
        "checked_at": datetime.now(UTC).isoformat(),
    }
    p = OUT_DIR / f"yupen_{target_date}_no_data.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"📝 no_data 已写: {p.name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="数据日期 YYYY-MM-DD（默认今天）")
    ap.add_argument("--article-id", default=None,
                    help="直接指定文章 URL 或 id 进行补抓（跳过 RSS 列表，只下载该文图片）")
    args = ap.parse_args()

    target_date = args.date or _now_beijing().strftime("%Y-%m-%d")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── 补抓模式：指定 article_id 直接下载 ───────────────────────
    if args.article_id:
        art_id = args.article_id
        print(f"🔄 补抓模式：指定 article_id={art_id}")
        detail = _get_article_detail(art_id)
        if not detail:
            print("⚠️ 无法获取指定文章详情，放弃")
            _write_no_data(target_date, f"补抓 article_id={art_id} 详情失败")
            return

        title = detail.get("title", "")
        body = detail.get("plain_content") or detail.get("content") or ""
        imgs = detail.get("images") or []
        s = _score(body, title) + min(len(imgs), 5) * 5
        if s < 30:
            print(f"⚠️ 指定文章不含鱼盆表（评分 {s} < 30），放弃")
            _write_no_data(target_date, f"指定文章不含鱼盆表，评分 {s}")
            return

        source = "猫笔叨的读后感专区"
        img_paths = []
        for i, url in enumerate(imgs[:8]):
            dest = OUT_DIR / f"yupen_{target_date}_{i}.png"
            if _download_image(url, dest):
                img_paths.append(str(dest))
                print(f"  🖼️ 图片已存: {dest.name}")

        raw = {
            "date": target_date,
            "source": source,
            "article_title": title,
            "article_id": art_id,
            "has_text_table": any(k in body for k in YUPEN_BODY_STRONG),
            "images": imgs,
            "image_paths": img_paths,
            "fetch_time": datetime.now(UTC).isoformat(),
            "status": "pending_ocr" if img_paths else "no_image",
            "backfilled": True,
        }
        raw_path = OUT_DIR / f"yupen_{target_date}_raw.json"
        raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✅ raw 已写: {raw_path.name} (status={raw['status']}, images={len(img_paths)})")
        return

    # ── 正常模式：按账号最新文章抓取 ───────────────────────────
    mbd = _find_mbd_fakeids()
    if not mbd:
        print("⚠️ 未找到猫笔刀系列订阅，检查 RSS 订阅列表")
        _write_no_data(target_date, "未找到猫笔刀订阅")
        return

    print(f"✅ 找到猫笔刀系列账号: {[n for _, n in mbd]}")

    all_candidates = []
    for fid, nick in mbd:
        arts, ok = _fetch_with_retry(fid, limit=15)
        if not ok or not arts:
            print(f"  ⚠️ {nick} 拉取失败/空，尝试下一个")
            continue
        for a in arts:
            a["_nick"] = nick
        all_candidates.extend(arts)

    if not all_candidates:
        print("⚠️ 所有猫笔刀账号均无法拉取文章")
        _write_no_data(target_date, "RSS 拉取失败")
        return

    # 猫笔叨每天都会发鱼盆表，鱼盆表几乎总是各账号「最新」那篇文章。
    # 因此优先取每个账号的最新文章作为首选候选，评分仅用于「验证该文确实含鱼盆表」，
    # 不作为主选逻辑（避免 RSS 抖动导致详情抓取失败时旧数据反超）。
    newest_by_nick = {}
    for a in all_candidates:
        nick = a.get("_nick", "")
        pt = a.get("publish_time", 0)
        if nick not in newest_by_nick or pt > newest_by_nick[nick].get("publish_time", 0):
            newest_by_nick[nick] = a

    if not newest_by_nick:
        print("⚠️ 所有猫笔刀账号均无可识别文章")
        _write_no_data(target_date, "RSS 无可识别文章")
        return

    # 优先抓取各账号最新文章的详情（带重试应对 RSS 抖动）
    primary = []
    for nick, na in newest_by_nick.items():
        detail = _get_article_detail(na.get("id", ""))
        na["_detail"] = detail
        body = detail.get("plain_content") or detail.get("content") or ""
        imgs = detail.get("images") or []
        # 评分：正文强/弱信号 + 标题关键词 + 图片数（仅用于验证是否含鱼盆表）
        s = _score(body, na.get("title", "")) + min(len(imgs), 5) * 5
        primary.append((s, na, body, imgs))
        print(f"  📊 [最新] {na.get('title')} [{nick}] 评分={s} (imgs={len(imgs)})")

    # 选评分最高的「最新文章」（所有候选本身都是各账号最新文，不存在旧数据反超问题）
    primary.sort(key=lambda x: x[0], reverse=True)

    best = primary[0]
    if best[0] < 30:
        print("⚠️ 各账号最新文章均未识别到鱼盆表（最高分 <30），写 no_data")
        _write_no_data(target_date, f"最新文章均未含鱼盆表，最高分 {best[0]}")
        return

    chosen = best[1]
    chosen_nick = chosen.get("_nick", "猫笔刀")
    body = best[2]
    images = best[3]

    title = chosen.get("title", "")
    art_id = chosen.get("id", "")
    print(f"📄 选定文章 [{chosen_nick}]: {title}  (评分 {best[0]})")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    img_paths = []
    if images:
        for i, url in enumerate(images[:8]):
            dest = OUT_DIR / f"yupen_{target_date}_{i}.png"
            if _download_image(url, dest):
                img_paths.append(str(dest))
                print(f"  🖼️ 图片已存: {dest.name}")
    else:
        print("  ⚠️ 该文章无 images 字段，无法下载鱼盆图片")

    raw = {
        "date": target_date,
        "source": chosen_nick,
        "article_title": title,
        "article_id": art_id,
        "has_text_table": any(k in body for k in YUPEN_BODY_STRONG),
        "images": images,
        "image_paths": img_paths,
        "fetch_time": datetime.now(UTC).isoformat(),
        "status": "pending_ocr" if img_paths else "no_image",
    }
    raw_path = OUT_DIR / f"yupen_{target_date}_raw.json"
    raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ raw 已写: {raw_path.name} (status={raw['status']}, images={len(img_paths)})")


if __name__ == "__main__":
    main()
