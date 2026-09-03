# -*- coding: utf-8 -*-
"""
考研参考书与历年真题抽取技能 (PDF & Document Extractor Skill)
功能：
  1. 扫描数学、英语、政治、专业课的「参考资料/」目录
  2. 提取 PDF、Markdown、文本资料中的题目、章节与真题
  3. 支持免开阅读器，直接在终端中调取题干或知识点
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

try:
    import pypdf
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False

def list_materials():
    """列出四科参考资料库中的所有文献与试卷"""
    res = {}
    for s in ("01-数学", "02-英语", "03-思想政治理论", "04-专业课"):
        ref_dir = ROOT / s / "参考资料"
        if ref_dir.exists():
            files = [f.name for f in ref_dir.iterdir() if f.is_file() and f.name != "README.md"]
            res[s] = files
    return res

def search_text_in_materials(keyword):
    """在参考资料文本或 Markdown 中进行关键词搜索"""
    matches = []
    for s in ("01-数学", "02-英语", "03-思想政治理论", "04-专业课"):
        ref_dir = ROOT / s / "参考资料"
        if not ref_dir.exists():
            continue
        for f in ref_dir.glob("*.*"):
            if f.suffix.lower() in (".txt", ".md"):
                try:
                    text = f.read_text(encoding="utf-8", errors="ignore")
                    for idx, line in enumerate(text.split("\n"), 1):
                        if keyword.lower() in line.lower():
                            matches.append(f"[{s}/{f.name}:L{idx}] {line.strip()[:100]}")
                except Exception:
                    continue
    return matches

def extract_pdf_page(pdf_path, page_num=1):
    """提取指定 PDF 文件的某页文本"""
    p = Path(pdf_path)
    if not p.exists():
        return f"未找到文件: {pdf_path}"
    
    if not HAS_PYPDF:
        return (
            f"检测到文件: {p.name} (大小: {p.stat().st_size // 1024} KB)\n"
            "⚠️ 当前 Python 环境未安装 `pypdf`，无法直接读取二进制 PDF。\n"
            "建议在终端运行：`pip install pypdf` 激活 PDF 纯文本抽取能力！"
        )
    
    try:
        reader = pypdf.PdfReader(str(p))
        if page_num > len(reader.pages) or page_num < 1:
            return f"页码超出范围，该 PDF 共有 {len(reader.pages)} 页。"
        text = reader.pages[page_num - 1].extract_text()
        return f"=== [{p.name}] 第 {page_num} 页 ===\n\n{text}"
    except Exception as e:
        return f"读取 PDF 异常: {e}"
