#!/usr/bin/env python3
"""
transcribe_yupen_image.py — 将鱼盆截图 OCR 转录为结构化 JSON

前置：
  1. 安装 tesseract 引擎（macOS: brew install tesseract tesseract-lang）
  2. 安装 Python 包：pip install pytesseract Pillow

流程：
  1. 读 output/yupen/yupen_<date>_raw.json
  2. 用 tesseract OCR 每张图片
  3. 识别「板块轮动历史回测数据」→ sector_rotation
  4. 识别「鱼盆趋势模型历史回测数据」→ yupen_trend
  5. 用正则尽量抽取表格行，写 JSON 文件
  6. 若识别失败，保留 OCR 原始文本在 raw 的 ocr_text 字段，供人工/视觉模型复核

用法：
  python3 transcribe_yupen_image.py
  python3 transcribe_yupen_image.py --date 2026-07-14
"""
import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

OUT_DIR = Path("/Users/guan/WorkBuddy/Claw/output/yupen")

# 列模式：按顺序解析
SECTOR_COLS = [
    "rank",
    "code",
    "name",
    "change_pct",
    "price",
    "ma20",
    "deviation_pct",
    "volume_ratio",
    "state_date",
    "interval_change_pct",
    "rank_change",
]
TREND_COLS = SECTOR_COLS  # 列名相同


def _read_raw(target_date):
    raw_path = OUT_DIR / f"yupen_{target_date}_raw.json"
    if not raw_path.exists():
        return None
    return json.loads(raw_path.read_text(encoding="utf-8"))


def _ocr_image(img_path, tesseract_cmd=None):
    """对图片 OCR，返回文本（优先中文）"""
    try:
        import pytesseract
        from PIL import Image

        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        im = Image.open(img_path)
        # 中文+英文混合
        text = pytesseract.image_to_string(im, lang="chi_sim+eng")
        return text or ""
    except Exception as e:  # noqa: BLE001
        return f"[OCR_ERROR: {e}]"


def _detect_table_type(text):
    """根据关键字判断图片属于哪种表"""
    if "板块轮动" in text or "14" in text or "CS创新药" in text or "半导体" in text:
        return "sector_rotation"
    if "趋势模型" in text or "科创50" in text or "恒生科技" in text:
        return "yupen_trend"
    # 兜底：看出现哪个表特有名词
    sector_score = sum(text.count(k) for k in ["板块", "轮动", "CS创新药", "证券公司", "房地产"])
    trend_score = sum(text.count(k) for k in ["科创50", "恒生科技", "标普500", "纳指100"])
    if sector_score >= trend_score:
        return "sector_rotation"
    return "yupen_trend"


def _parse_rows(text, table_type):
    """从 OCR 文本中解析表格行，返回 list[dict] 或 None"""
    # 预处理：统一空格、去除空行
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    rows = []
    # 简单行解析：寻找类似 数字 代码 名称 百分比 数字 ... 的行
    # 这个正则非常脆弱，仅用于演示；更可靠的做法是视觉模型
    for line in lines:
        # 匹配行首：排名 + 代码 + 名称 + 涨幅% + 现价 + MA20 + 偏离率 + 量比 + 日期 + 区间涨幅 + 排名变化
        # 尝试较松散的匹配
        m = re.match(
            r"^\s*(\d{1,2})\s+([A-Za-z0-9]{3,8})\s+([^0-9\-]{2,6})\s+([\-+]?\d+\.\d+)%\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+([\-+]?\d+\.\d+)%\s+([\d\-]+\.?\d*)\s+(\d{2}\.\d{2}\.\d{2})\s+([\-+]?\d+\.\d+)%\s+([\-+]?\d+)",
            line,
        )
        if m:
            g = m.groups()
            row = {
                "rank": int(g[0]),
                "code": g[1],
                "name": g[2].strip(),
                "change_pct": float(g[3]),
                "price": float(g[4]),
                "ma20": float(g[5]),
                "deviation_pct": float(g[6]),
                "volume_ratio": None if g[7] == "-" else float(g[7]),
                "state_date": g[8],
                "interval_change_pct": float(g[9]),
                "rank_change": int(g[10]),
            }
            rows.append(row)
    return rows if rows else None


def _extract_date(text):
    """从表头提取数据日期：2026.07.10 等"""
    m = re.search(r"(\d{4})\.(\d{2})\.(\d{2})", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def _write_json(data_date, table_type, rows, source, article_title, article_id):
    payload = {
        "date": data_date,
        "source": source,
        "data_type": "板块轮动历史回测数据" if table_type == "sector_rotation" else "鱼盆趋势模型历史回测数据",
        "article_title": article_title,
        "article_id": article_id,
        "fetch_time": datetime.now(UTC).isoformat(),
        "sectors": rows,
    }
    suffix = "sector_rotation" if table_type == "sector_rotation" else "yupen_trend"
    p = OUT_DIR / f"yupen_{data_date}_{suffix}.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 已写 {p.name} ({len(rows)} 行)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="数据日期 YYYY-MM-DD（默认今天）")
    ap.add_argument("--tesseract-cmd", default=None, help="tesseract 二进制路径")
    args = ap.parse_args()

    target_date = args.date or datetime.now(UTC).strftime("%Y-%m-%d")
    raw = _read_raw(target_date)
    if not raw:
        print(f"⚠️ 未找到 {OUT_DIR}/yupen_{target_date}_raw.json")
        return

    if raw.get("status") != "pending_ocr":
        print(f"📝 状态为 {raw.get('status')}，无需 OCR")
        return

    images = raw.get("image_paths") or []
    if not images:
        print("⚠️ raw 中无图片路径")
        return

    source = raw.get("source", "猫笔叨")
    article_title = raw.get("article_title", "")
    article_id = raw.get("article_id", "")

    ocr_texts = {}
    parsed = {"sector_rotation": None, "yupen_trend": None}
    for img in images:
        img_path = Path(img)
        if not img_path.exists():
            continue
        text = _ocr_image(str(img_path), tesseract_cmd=args.tesseract_cmd)
        ocr_texts[img_path.name] = text
        table_type = _detect_table_type(text)
        data_date = _extract_date(text) or target_date
        rows = _parse_rows(text, table_type)
        if rows:
            parsed[table_type] = (data_date, rows)
            _write_json(data_date, table_type, rows, source, article_title, article_id)

    # 更新 raw 文件，追加 OCR 结果
    raw["ocr_texts"] = ocr_texts
    raw["ocr_status"] = "parsed" if any(parsed.values()) else "manual_required"
    raw_path = OUT_DIR / f"yupen_{target_date}_raw.json"
    raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    if not any(parsed.values()):
        print("⚠️ OCR 未能自动解析表格，ocr_texts 已写入 raw，需人工/视觉模型复核")
    else:
        print(f"✅ OCR 完成：{ {k: ('已解析' if v else '未解析') for k,v in parsed.items()} }")


if __name__ == "__main__":
    main()
