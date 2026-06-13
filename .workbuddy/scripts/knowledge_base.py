#!/usr/bin/env python3
# 注意: 如遇到导入错误请使用系统 Python3:
#   /usr/bin/python3 knowledge_base.py <command>
"""
投资知识库 — 文章归档 + 向量搜索 + 信号溯源 + 研报索引

用法:
  # 索引新文章
  python3 knowledge_base.py index --input ./archive/articles/

  # 语义搜索
  python3 knowledge_base.py search "最近一周半导体板块推荐"

  # 信号溯源：评估公众号推荐准确率
  python3 knowledge_base.py trace --days 30

  # 统计摘要
  python3 knowledge_base.py stats

依赖:
  pip install chromadb sentence-transformers pdfplumber
  或使用 OpenAI embedding API: pip install chromadb openai
"""

import hashlib
import json
import os
import sys
from pathlib import Path

# 加载 Claw 公共库
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
import argparse
from datetime import datetime, timedelta
from pathlib import Path

from error_handler import atomic_write_json

# --- 路径配置 ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ARCHIVE_DIR = PROJECT_ROOT / "archive" / "articles"
DB_DIR = PROJECT_ROOT / ".workbuddy" / "data"
REPORTS_DIR = PROJECT_ROOT / ".workbuddy" / "data" / "reports"
REPORTS_ANALYSIS_DIR = PROJECT_ROOT / ".workbuddy" / "reports"
SIGNALS_FILE = DB_DIR / "article_signals.json"
VECTOR_DB_DIR = Path.home() / ".workbuddy" / "cache" / "knowledge_vectors"

# --- 嵌入模型选择 ---
# 首选本地模型（免费），备选 OpenAI API
EMBEDDING_BACKEND = os.environ.get("KB_EMBEDDING_BACKEND", "local")  # local | openai


def get_embedding_model():
    """延迟加载嵌入模型"""
    if EMBEDDING_BACKEND == "openai":
        try:
            from openai import OpenAI

            client = OpenAI()
            return lambda texts: [
                r.embedding
                for r in client.embeddings.create(model="text-embedding-3-small", input=texts).data
            ]
        except ImportError:
            print("⚠️ openai 未安装，回退到本地模型", file=sys.stderr)
        except Exception as e:
            print(f"⚠️ OpenAI API 错误: {e}，回退到本地模型", file=sys.stderr)

    # 本地模型（默认）
    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
        return lambda texts: model.encode(texts).tolist()
    except ImportError:
        print(
            "❌ 未安装 sentence-transformers，请执行: pip install sentence-transformers",
            file=sys.stderr,
        )
        sys.exit(1)


def get_vector_db():
    """获取 ChromaDB 集合"""
    try:
        import chromadb
    except ImportError:
        print("❌ 未安装 chromadb，请执行: pip install chromadb", file=sys.stderr)
        sys.exit(1)

    client = chromadb.PersistentClient(path=str(VECTOR_DB_DIR))
    return client.get_or_create_collection(
        name="investment_articles",
        metadata={"description": "投资相关公众号文章知识库"},
    )


# --- 文章索引 ---


def index_articles(input_dir: str = None, glob_pattern: str = "*.md"):
    """索引文章到向量数据库"""
    if input_dir is None:
        input_dir = str(ARCHIVE_DIR)

    input_path = Path(input_dir)
    if not input_path.exists():
        print(f"❌ 目录不存在: {input_dir}", file=sys.stderr)
        return 0

    # 收集文章文件
    files = list(input_path.rglob(glob_pattern))
    if not files:
        print(f"📭 无匹配文件: {input_dir}/{glob_pattern}")
        return 0

    print(f"📚 发现 {len(files)} 篇文章，开始索引...")

    # 加载嵌入模型
    embed_fn = get_embedding_model()
    collection = get_vector_db()

    indexed = 0
    for filepath in files:
        try:
            content = filepath.read_text(encoding="utf-8")
            if len(content) < 100:
                continue  # 跳过过短文件

            # 生成唯一 ID
            doc_id = hashlib.md5(str(filepath).encode()).hexdigest()[:12]

            # 提取元数据
            title = filepath.stem
            first_line = content.split("\n")[0] if content else ""
            if first_line.startswith("# "):
                title = first_line[2:].strip()

            # 尝试从路径提取公众号名
            account = "未知"
            parts = filepath.parts
            for p in parts:
                if p in ["投资明见", "恩哥箴言", "丹木说", "好运侠客", "猫笔叨"]:
                    account = p
                    break

            # 截取前 2000 字符做嵌入（控制嵌入维度）
            text_for_embed = content[:2000]

            # 存入向量库
            collection.upsert(
                ids=[doc_id],
                embeddings=embed_fn([text_for_embed]),
                metadatas=[
                    {
                        "title": title,
                        "account": account,
                        "filepath": str(filepath),
                        "char_count": len(content),
                        "indexed_at": datetime.now().isoformat(),
                    }
                ],
                documents=[content[:5000]],  # 存前 5000 字符
            )

            indexed += 1
            if indexed % 10 == 0:
                print(f"  ... {indexed}/{len(files)}")

        except Exception as e:
            print(f"  ⚠️ 跳过 {filepath.name}: {e}")

    print(f"✅ 索引完成: {indexed}/{len(files)} 篇")
    return indexed


# --- 语义搜索 ---


def search_articles(query: str, top_k: int = 5) -> list:
    """语义搜索文章"""
    embed_fn = get_embedding_model()
    collection = get_vector_db()

    query_embedding = embed_fn([query])
    results = collection.query(
        query_embeddings=query_embedding,
        n_results=top_k,
    )

    output = []
    if results["ids"] and results["ids"][0]:
        for i in range(len(results["ids"][0])):
            meta = results["metadatas"][0][i] if results["metadatas"] else {}
            doc = results["documents"][0][i] if results["documents"] else ""
            distance = results["distances"][0][i] if results.get("distances") else 0
            output.append(
                {
                    "id": results["ids"][0][i],
                    "title": meta.get("title", "未知"),
                    "account": meta.get("account", "未知"),
                    "relevance": round(1 - distance, 4) if distance else 0,
                    "snippet": doc[:200] + "..." if len(doc) > 200 else doc,
                }
            )

    return output


# --- 信号溯源 ---


def load_signals() -> list:
    """加载信号记录"""
    if SIGNALS_FILE.exists():
        return json.loads(SIGNALS_FILE.read_text())
    return []


def save_signals(signals: list):
    """保存信号记录"""
    atomic_write_json(SIGNALS_FILE, signals)


def record_signal(
    article_id: str,
    account: str,
    title: str,
    stock_code: str,
    stock_name: str,
    signal: str,  # bullish / bearish / neutral
    target_price: float = None,
    confidence: int = 5,
):
    """记录一篇文章的选股信号"""
    signals = load_signals()

    signals.append(
        {
            "article_id": article_id,
            "account": account,
            "title": title,
            "stock_code": stock_code,
            "stock_name": stock_name,
            "signal": signal,
            "target_price": target_price,
            "confidence": confidence,
            "recorded_at": datetime.now().strftime("%Y-%m-%d"),
            "verified": False,
            "hit_target": None,
            "hit_stop": None,
            "final_return_pct": None,
        }
    )

    save_signals(signals)
    print(f"  📝 记录信号: [{account}] {stock_name}({stock_code}) → {signal}")


def trace_signals(days: int = 30) -> dict:
    """信号溯源：统计公众号推荐准确率"""
    signals = load_signals()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    # 筛选时间范围内的信号
    recent = [s for s in signals if s.get("recorded_at", "") >= cutoff]

    if not recent:
        return {"message": f"过去 {days} 天内无信号记录", "accounts": []}

    # 按公众号统计
    account_stats = {}
    for s in recent:
        acc = s["account"]
        if acc not in account_stats:
            account_stats[acc] = {
                "total": 0,
                "bullish": 0,
                "bearish": 0,
                "neutral": 0,
                "verified": 0,
                "hit_target": 0,
                "hit_stop": 0,
            }
        stats = account_stats[acc]
        stats["total"] += 1
        stats[s["signal"]] = stats.get(s["signal"], 0) + 1
        if s.get("verified"):
            stats["verified"] += 1
            if s.get("hit_target"):
                stats["hit_target"] += 1
            if s.get("hit_stop"):
                stats["hit_stop"] += 1

    # 计算准确率
    result = []
    for acc, stats in sorted(account_stats.items(), key=lambda x: -x[1]["total"]):
        accuracy = round(stats["hit_target"] / max(stats["verified"], 1) * 100, 1)
        result.append(
            {
                "account": acc,
                "total_signals": stats["total"],
                "bullish": stats["bullish"],
                "bearish": stats["bearish"],
                "neutral": stats["neutral"],
                "verified_signals": stats["verified"],
                "hit_target": stats["hit_target"],
                "hit_stop": stats["hit_stop"],
                "accuracy_pct": accuracy,
            }
        )

    return {
        "period": f"过去 {days} 天",
        "total_signals": len(recent),
        "accounts": result,
    }


def cmd_index(args):
    """CLI: 索引入库"""
    count = index_articles(input_dir=args.input)
    if count == 0:
        sys.exit(0)


def cmd_search(args):
    """CLI: 语义搜索"""
    results = search_articles(args.query, top_k=args.top_k)
    if not results:
        print("📭 无匹配结果")
        return

    print(f"🔍 搜索: {args.query}\n")
    for i, r in enumerate(results, 1):
        print(f"{i}. [{r['account']}] {r['title']}")
        print(f"   相关度: {r['relevance']:.2%}")
        print(f"   摘要: {r['snippet']}")
        print()


def cmd_trace(args):
    """CLI: 信号溯源"""
    report = trace_signals(days=args.days)
    if "message" in report:
        print(report["message"])
        return

    print(f"📊 公众号信号溯源 ({report['period']})")
    print(f"   总信号数: {report['total_signals']}\n")

    if not report["accounts"]:
        print("   无信号记录")
        return

    print(
        f"{'公众号':<12} {'总信号':<8} {'看多':<6} {'看空':<6} {'已验证':<8} {'命中':<6} {'准确率':<8}"
    )
    print("-" * 65)
    for a in report["accounts"]:
        print(
            f"{a['account']:<12} {a['total_signals']:<8} {a['bullish']:<6} "
            f"{a['bearish']:<6} {a['verified_signals']:<8} {a['hit_target']:<6} "
            f"{a['accuracy_pct']}%"
        )


def index_research_report(report_path: str = None):
    """索引券商研报到知识库

    支持从 reports/ (分析报告) 或 data/reports/ (原始内容) 目录索引
    """
    if report_path:
        files = [Path(report_path)]
    else:
        files = []
        if REPORTS_ANALYSIS_DIR.exists():
            files.extend(REPORTS_ANALYSIS_DIR.rglob("*.md"))
        if REPORTS_DIR.exists():
            files.extend(REPORTS_DIR.rglob("*.md"))
        # 去重
        seen = set()
        unique = []
        for f in files:
            stem = f.stem
            if stem not in seen:
                seen.add(stem)
                unique.append(f)
        files = unique

    if not files:
        print("📭 无研报文件")
        return 0

    print(f"📚 发现 {len(files)} 篇研报，开始索引...")

    embed_fn = get_embedding_model()
    collection = get_vector_db()
    indexed = 0

    for filepath in files:
        try:
            content = filepath.read_text(encoding="utf-8")
            if len(content) < 200:
                continue

            doc_id = f"report_{hashlib.md5(str(filepath).encode()).hexdigest()[:12]}"

            # 提取标题
            title = filepath.stem
            first_line = content.split("\n")[0] if content else ""
            if first_line.startswith("# "):
                title = first_line[2:].strip()

            # 提取机构信息
            institution = "券商研报"
            for line in content.split("\n"):
                if "机构" in line and ":" in line:
                    institution = line.split(":")[1].strip()
                    break

            text_for_embed = content[:2000]

            collection.upsert(
                ids=[doc_id],
                embeddings=embed_fn([text_for_embed]),
                metadatas=[
                    {
                        "title": title,
                        "account": institution,
                        "type": "research_report",
                        "filepath": str(filepath),
                        "char_count": len(content),
                        "indexed_at": datetime.now().isoformat(),
                    }
                ],
                documents=[content[:5000]],
            )

            indexed += 1
            print(f"  ✅ [{indexed}] {title}")

        except Exception as e:
            print(f"  ⚠️ 跳过 {filepath.name}: {e}")

    print(f"✅ 研报索引完成: {indexed} 篇")
    return indexed


def cmd_stats(args):
    """CLI: 知识库统计"""
    # 向量库统计
    try:
        collection = get_vector_db()
        doc_count = collection.count()
    except Exception:
        doc_count = 0

    # 信号统计
    signals = load_signals()
    signal_count = len(signals)

    # 归档文件统计
    archive_files = 0
    if ARCHIVE_DIR.exists():
        archive_files = len(list(ARCHIVE_DIR.rglob("*.md")))

    # 研报统计
    report_files = 0
    if REPORTS_ANALYSIS_DIR.exists():
        report_files += len(list(REPORTS_ANALYSIS_DIR.rglob("*.md")))
    if REPORTS_DIR.exists():
        report_files += len(list(REPORTS_DIR.rglob("*.md")))

    print("📚 知识库统计")
    print(f"   归档文章: {archive_files} 篇")
    print(f"   券商研报: {report_files} 篇")
    print(f"   向量索引: {doc_count} 篇")
    print(f"   信号记录: {signal_count} 条")
    print(f"   归档路径: {ARCHIVE_DIR}")
    print(f"   研报路径: {REPORTS_DIR}")
    print(f"   向量库路径: {VECTOR_DB_DIR}")


def main():
    parser = argparse.ArgumentParser(
        description="投资知识库 — 文章归档 + 向量搜索 + 信号溯源",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s index
  %(prog)s search "半导体 推荐"
  %(prog)s trace --days 30
  %(prog)s report-index
  %(prog)s stats
        """,
    )

    sub = parser.add_subparsers(dest="command")

    # index
    p_idx = sub.add_parser("index", help="索引入库")
    p_idx.add_argument("--input", help="文章目录", default=str(ARCHIVE_DIR))

    # search
    p_src = sub.add_parser("search", help="语义搜索")
    p_src.add_argument("query", help="搜索关键词")
    p_src.add_argument("--top-k", type=int, default=5, help="返回数量")

    # report-index
    p_rpt = sub.add_parser("report-index", help="索引券商研报")
    p_rpt.add_argument("--input", help="研报文件路径（可选）")

    # trace
    p_trc = sub.add_parser("trace", help="信号溯源")
    p_trc.add_argument("--days", type=int, default=30, help="统计天数")

    # stats
    sub.add_parser("stats", help="统计摘要")

    args = parser.parse_args()

    if args.command == "index":
        cmd_index(args)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "trace":
        cmd_trace(args)
    elif args.command == "report-index":
        index_research_report(report_path=args.input)
    elif args.command == "stats":
        cmd_stats(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
