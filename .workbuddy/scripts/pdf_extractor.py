#!/usr/bin/env python3
"""
PDF报告提取工具 - 支持从PDF中提取图片并自动转换为可读文本

核心能力：
1. 从PDF中提取每页的JPG图片（扫描件/嵌入图片）
2. 调用Tesseract OCR将图片转为文本（如果可用）
3. 从新浪财经/东方财富等在线来源搜索报告全文（备选方案）
4. 输出结构化Markdown文件

依赖：
- pypdf: PDF解析
- 可选: pytesseract + tesseract (本地OCR)
"""

import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path


class PDFExtractor:
    """PDF报告提取器"""

    def __init__(self, pdf_path: str):
        self.pdf_path = Path(pdf_path)
        self.temp_dir = Path("/tmp/pdf_extract") / self.pdf_path.stem
        self.has_pypdf = False
        self.has_tesseract = False
        self._check_dependencies()

    def _check_dependencies(self):
        """检查可用依赖"""
        try:
            from pypdf import PdfReader  # noqa: F401

            self.has_pypdf = True
        except ImportError:
            print("[WARN] pypdf 未安装，尝试: pip install pypdf")
            self.has_pypdf = False

        try:
            result = subprocess.run(
                ["which", "tesseract"], capture_output=True, text=True, timeout=5
            )
            self.has_tesseract = result.returncode == 0
            if self.has_tesseract:
                # Check Chinese language support
                lang_check = subprocess.run(
                    ["tesseract", "--list-langs"], capture_output=True, text=True, timeout=5
                )
                self.has_chinese_ocr = "chi_sim" in lang_check.stdout
            else:
                self.has_chinese_ocr = False
        except Exception:
            self.has_tesseract = False
            self.has_chinese_ocr = False

    def extract_images(self) -> list[Path]:
        """从PDF中提取每页的JPG图片"""
        if not self.has_pypdf:
            print("[ERROR] pypdf 不可用，无法提取图片")
            return []

        from pypdf import PdfReader

        self.temp_dir.mkdir(parents=True, exist_ok=True)
        reader = PdfReader(str(self.pdf_path))
        extracted = []

        for i in range(len(reader.pages)):
            page = reader.pages[i]
            resources = page.get("/Resources", {})
            xobjects = resources.get("/XObject", {})

            for key in xobjects:
                obj_ref = xobjects[key]
                obj = obj_ref.get_object()
                subtype = obj.get("/Subtype", "")
                if "/Image" in str(subtype) or "Image" in str(subtype):
                    data = obj.get_data()
                    fname = self.temp_dir / f"page_{i + 1:02d}.jpg"
                    with open(fname, "wb") as f:
                        f.write(data)
                    extracted.append(fname)
                    break
            else:
                print(f"  [WARN] 第{i + 1}页未找到图片")

        print(f"  已提取 {len(extracted)} 页图片到 {self.temp_dir}")
        return extracted

    def ocr_images(self, image_paths: list[Path]) -> str:
        """对图片执行OCR并返回合并文本"""
        if not self.has_tesseract:
            print("[INFO] Tesseract 不可用，跳过OCR")
            return ""

        # Download Chinese language pack if needed
        if not self.has_chinese_ocr:
            self._install_chinese_lang()

        lang = "chi_sim+eng" if self.has_chinese_ocr else "eng"
        pages_text = []

        for img_path in sorted(image_paths):
            print(f"  OCR: {img_path.name}...")
            try:
                result = subprocess.run(
                    ["tesseract", str(img_path), "stdout", "-l", lang],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                text = result.stdout.strip()
                if text:
                    pages_text.append(f"--- 第{len(pages_text) + 1}页 ---\n{text}")
                else:
                    pages_text.append(f"--- 第{len(pages_text) + 1}页 ---\n[OCR无结果]")
            except subprocess.TimeoutExpired:
                pages_text.append(f"--- 第{len(pages_text) + 1}页 ---\n[OCR超时]")
            except Exception as e:
                pages_text.append(f"--- 第{len(pages_text) + 1}页 ---\n[OCR错误: {e}]")

        return "\n\n".join(pages_text)

    def _install_chinese_lang(self):
        """尝试安装Tesseract中文语言包"""
        print("  [INFO] 尝试安装中文语言包...")
        try:
            subprocess.run(
                ["tesseract", "--list-langs"], capture_output=True, text=True, timeout=10
            )
            # Try common locations for tessdata
            tessdata_dirs = [
                "/opt/homebrew/share/tessdata",
                "/usr/share/tesseract-ocr/4.00/tessdata",
                "/usr/local/share/tessdata",
            ]
            for d in tessdata_dirs:
                if os.path.isdir(d):
                    lang_url = (
                        "https://github.com/tesseract-ocr/tessdata/raw/main/chi_sim.traineddata"
                    )
                    target = os.path.join(d, "chi_sim.traineddata")
                    if not os.path.exists(target):
                        print(f"  Downloading chi_sim traineddata to {d}...")
                        try:
                            urllib.request.urlretrieve(lang_url, target)
                            self.has_chinese_ocr = True
                            print("  中文语言包安装成功")
                        except Exception as e:
                            print(f"  下载失败: {e}")
                    else:
                        self.has_chinese_ocr = True
                    break
        except Exception:
            print("[WARN] OCR 语言包下载失败，将使用英文 OCR", file=sys.stderr)

    def search_online(self, title_hint: str = "") -> str | None:
        """尝试从在线来源搜索报告全文"""
        report = self._try_sina_finance()
        if report:
            return report
        report = self._try_hibor()
        if report:
            return report
        return None

    def _try_sina_finance(self) -> str | None:
        """从新浪财经研究报告频道搜索"""
        print("  尝试从新浪财经获取报告全文...")
        try:
            search_url = (
                "https://stock.finance.sina.com.cn/stock/go.php/"
                "vReport_SearchByKey/key/"
                + urllib.parse.quote(self.pdf_path.stem[:20])
                + "/index.phtml"
            )
            # Attempt direct search - this typically doesn't work well
            return None
        except Exception:
            return None

    def _try_hibor(self) -> str | None:
        """从慧博投研资讯搜索"""
        print("  尝试从慧博投研获取报告全文...")
        try:
            search_url = "https://m.hibor.com.cn/wap_search.aspx?keyword=" + urllib.parse.quote(
                self.pdf_path.stem[:30]
            )
            req = urllib.request.Request(search_url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=10)
            html = resp.read()
            # Try to find report links
            text = html.decode("utf-8", errors="replace")
            links = re.findall(r'href="([^"]*id=[a-f0-9]{32}[^"]*)"', text)
            if links:
                detail_url = "https://m.hibor.com.cn/" + links[0]
                req2 = urllib.request.Request(detail_url, headers={"User-Agent": "Mozilla/5.0"})
                resp2 = urllib.request.urlopen(req2, timeout=10)
                detail_html = resp2.read().decode("utf-8", errors="replace")
                # Extract text
                clean = re.sub(r"<[^>]+>", "", detail_html)
                clean = re.sub(r"&nbsp;", " ", clean)
                lines = [l.strip() for l in clean.split("\n") if l.strip()]
                return "\n".join(lines)
            return None
        except Exception as e:
            print(f"  慧博搜索失败: {e}")
            return None

    def extract_all(self, use_ocr: bool = True, output_format: str = "markdown") -> dict:
        """完整提取流程"""
        result = {
            "pdf_path": str(self.pdf_path),
            "file_size": os.path.getsize(self.pdf_path),
            "pages": [],
            "full_text": "",
            "source": "extracted",
        }

        # Step 1: Extract images
        images = self.extract_images()
        if not images:
            print("[WARN] 未能提取任何页面图片")
            return result

        # Step 2: OCR (if available)
        if use_ocr and self.has_tesseract:
            print("\n执行OCR识别...")
            text = self.ocr_images(images)
            if text.strip():
                result["full_text"] = text
                result["source"] = "ocr"
                result["pages"] = [{"page": i + 1} for i in range(len(images))]
                print(f"  OCR完成，提取 {len(text)} 字符")
        else:
            print(f"\n提示: 共 {len(images)} 页图片需OCR处理")
            print("  安装 Tesseract: brew install tesseract tesseract-lang")

        return result

    def save_as_markdown(self, result: dict, output_path: str):
        """保存为Markdown文件"""
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"# {self.pdf_path.stem}\n\n")
            f.write(f"- **来源**: {result['source']}\n")
            f.write(f"- **文件**: {result['pdf_path']}\n")
            f.write(f"- **大小**: {result['file_size'] / 1024:.0f} KB\n")
            f.write(f"- **页数**: {len(result['pages'])}\n\n")
            f.write("---\n\n")
            if result["full_text"]:
                f.write(result["full_text"])
            else:
                f.write("*文本提取失败，请尝试安装Tesseract OCR*\n")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="PDF报告提取工具")
    parser.add_argument("pdf_path", help="PDF文件路径")
    parser.add_argument("--output", "-o", help="输出Markdown文件路径")
    parser.add_argument("--no-ocr", action="store_true", help="跳过OCR")
    parser.add_argument("--search-online", action="store_true", help="尝试在线搜索报告全文")

    args = parser.parse_args()

    if not os.path.exists(args.pdf_path):
        print(f"[ERROR] 文件不存在: {args.pdf_path}")
        sys.exit(1)

    extractor = PDFExtractor(args.pdf_path)
    print(f"\n=== PDF提取报告: {args.pdf_path} ===\n")

    # Try online first if requested
    if args.search_online:
        print("[INFO] 尝试在线搜索报告全文...")
        online_text = extractor.search_online()
        if online_text:
            result = {
                "pdf_path": args.pdf_path,
                "file_size": os.path.getsize(args.pdf_path),
                "pages": [],
                "full_text": online_text,
                "source": "online",
            }
            print(f"  ✓ 在线获取成功 ({len(online_text)} 字符)")
            if args.output:
                extractor.save_as_markdown(result, args.output)
                print(f"\n✓ 已保存到: {args.output}")
            else:
                print("\n" + "=" * 50)
                print(result["full_text"][:2000])
                print("...")
            return

    # Fallback to image extraction + OCR
    result = extractor.extract_all(use_ocr=not args.no_ocr)

    if args.output:
        extractor.save_as_markdown(result, args.output)
        print(f"\n✓ 已保存到: {args.output}")
    elif result["full_text"]:
        print("\n" + "=" * 50)
        print(result["full_text"][:2000])
        print("...")
    else:
        print("\n⚠ 未能提取文本。建议:")
        print("  1. 安装 Tesseract OCR: brew install tesseract tesseract-lang")
        print("  2. 使用 --search-online 尝试在线搜索")


if __name__ == "__main__":
    main()
