#!/usr/bin/env python3
"""Claw早报汇总脚本 —— 集成summarize技能，对公众号文章自动摘要"""
import json
import logging
import re
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

SKILL_DIR = Path.home() / ".workbuddy" / "skills" / "summarize"
SUMMARIZE = SKILL_DIR / "summarize.py"
# 公众号文章存放目录（相对于 Claw 项目根目录）
INPUT_DIR = (Path(__file__).resolve().parent.parent / "output" / "wx_articles")

def summarize_text(text: str, length: str = "short") -> str:
    """调用summarize.py对单篇文章做摘要"""
    result = subprocess.run(
        ["python3", str(SUMMARIZE), "--length", length, "--output", "json", text],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode == 0:
        data = json.loads(result.stdout)
        return data.get("summary", text[:100] + "...")
    logger.warning(f"summarize skill 调用失败 (rc={result.returncode}): {result.stderr[:100]}")
    return text[:100] + "..."

def summarize_file(filepath: str) -> dict:
    """对单篇文章文件做摘要"""
    result = subprocess.run(
        ["python3", str(SUMMARIZE), "--file", filepath, "--length", "short", "--output", "json"],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode == 0:
        return json.loads(result.stdout)
    return {"summary": f"[解析失败: {filepath}]"}

def batch_summarize(dir_path: str, limit: int = 10) -> list:
    """批量摘要目录下的文章"""
    results = []
    target_dir = Path(dir_path)
    if not target_dir.exists():
        print(f"[summarize] 目录不存在: {dir_path}")
        return results
    files = sorted(target_dir.glob("*.txt"), key=lambda f: f.stat().st_mtime, reverse=True)[:limit]
    for f in files:
        try:
            r = summarize_file(str(f))
            r["file"] = f.name
            results.append(r)
            print(f"  ✓ {f.name}")
        except Exception as e:
            print(f"  ✗ {f.name}: {e}")
    return results

def summarize_article_content(article: dict) -> str:
    """为单篇文章生成 200 字摘要（供 wx_morning_report.py 导入使用）

    Args:
        article: 包含 'content' 键的 dict（兼容 wx_morning_report 文章格式）

    Returns:
        200 字以内的摘要字符串
    """
    content = article.get("content", "")
    if not content or content == "（仅有标题，无完整正文）":
        title = article.get("title", "")[:60]
        return f"（标题：{title}）"

    # 截取前 2000 字做摘要（避免噪音 + 提升速度）
    text = content[:2000]
    try:
        r = summarize_text(text, length="short")
        # 限制 200 字
        if len(r) > 200:
            r = r[:197] + "..."
        return r
    except Exception:
        logger.warning(f"摘要生成异常，降级为前两句提取 (len={len(text)})", exc_info=True)
        # fallback：取前 2 句
        sentences = re.split(r'[。！？.!?]', text)
        first_two = [s.strip() for s in sentences[:2] if s.strip()]
        return ("".join(first_two)[:200] + "...") if first_two else text[:100]


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else str(INPUT_DIR)
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    results = []

    print(f"[summarize] 扫描: {target}, 最多 {limit} 篇")

    if Path(target).is_file():
        r = summarize_file(target)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        results = [r]
    else:
        results = batch_summarize(target, limit)
        for r in results:
            print(f"\n📄 {r.get('file','')}")
            print(f"   摘要: {r.get('summary','?')}")

    print(f"\n[summarize] 完成: {len(results)} 篇")
