# -*- coding: utf-8 -*-
"""
考研参考书与历年真题抽取技能 (PDF & Document Extractor Skill)

``pypdf`` 是可选依赖，仅在实际读取 PDF 时导入。普通 CLI 启动因此不会
加载 pypdf/cryptography，也不会被第三方依赖的弃用警告阻断。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent.parent
SUBJECT_DIRS = ("01-数学", "02-英语", "03-思想政治理论", "04-专业课")

_UNSET = object()
_PYPDF: Any = _UNSET


def _ensure_pypdf():
    """惰性加载可选依赖；导入链异常时让 PDF 功能单独降级。"""
    global _PYPDF
    if _PYPDF is _UNSET:
        try:
            import pypdf as module
        except Exception:
            # 可选包内部可能因版本不兼容或被升级为异常的警告而失败；
            # 这不应阻断与 PDF 无关的学习、搜索和 CLI 功能。
            _PYPDF = None
        else:
            _PYPDF = module
    return _PYPDF


def _missing_dependency_message() -> str:
    return (
        "当前 Python 环境无法加载 `pypdf`，无法直接读取二进制 PDF。"
        "请运行 `pip install pypdf`，并检查 pypdf/cryptography 的版本兼容性。"
    )


def list_materials():
    """列出四科参考资料库中的所有文献与试卷。"""
    result = {}
    for subject_dir in SUBJECT_DIRS:
        ref_dir = ROOT / subject_dir / "参考资料"
        if ref_dir.exists():
            result[subject_dir] = [
                path.name
                for path in ref_dir.iterdir()
                if path.is_file() and path.name != "README.md"
            ]
    return result


def search_text_in_materials(keyword):
    """在参考资料文本或 Markdown 中进行关键词搜索。"""
    matches = []
    needle = str(keyword).lower()
    for subject_dir in SUBJECT_DIRS:
        ref_dir = ROOT / subject_dir / "参考资料"
        if not ref_dir.exists():
            continue
        for path in ref_dir.glob("*.*"):
            if path.suffix.lower() not in (".txt", ".md"):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for line_number, line in enumerate(text.splitlines(), 1):
                if needle in line.lower():
                    matches.append(
                        f"[{subject_dir}/{path.name}:L{line_number}] {line.strip()[:100]}"
                    )
    return matches


def extract_pdf_page(pdf_path, page_num=1):
    """提取指定 PDF 文件的某页文本。"""
    path = Path(pdf_path)
    if not path.exists():
        return f"未找到文件: {pdf_path}"

    pypdf = _ensure_pypdf()
    if pypdf is None:
        return (
            f"检测到文件: {path.name} (大小: {path.stat().st_size // 1024} KB)\n"
            f"⚠️ {_missing_dependency_message()}"
        )

    try:
        reader = pypdf.PdfReader(str(path))
        if page_num > len(reader.pages) or page_num < 1:
            return f"页码超出范围，该 PDF 共有 {len(reader.pages)} 页。"
        text = reader.pages[page_num - 1].extract_text()
        return f"=== [{path.name}] 第 {page_num} 页 ===\n\n{text}"
    except Exception as exc:
        return f"读取 PDF 异常: {exc}"


def extract_pdf_pages(pdf_path, max_pages=8):
    """批量提取 PDF 前 N 页，返回结构化结果。"""
    path = Path(pdf_path)
    result = {
        "success": False,
        "file_name": path.name,
        "total_pages": 0,
        "pages": [],
    }

    if not path.exists():
        result["error"] = f"未找到文件: {pdf_path}"
        return result

    pypdf = _ensure_pypdf()
    if pypdf is None:
        result["error"] = _missing_dependency_message()
        return result

    try:
        reader = pypdf.PdfReader(str(path))
        total = len(reader.pages)
        result["total_pages"] = total
        cap = min(max_pages, total) if max_pages else total
        for index in range(cap):
            try:
                text = reader.pages[index].extract_text() or ""
            except Exception as page_error:
                text = f"[第 {index + 1} 页提取失败: {page_error}]"
            result["pages"].append({"page": index + 1, "text": text})
        result["success"] = True
    except Exception as exc:
        result["error"] = f"读取 PDF 异常: {exc}"
    return result


def find_questions_by_keyword(pdf_path, keyword, max_results=3):
    """在指定 PDF 中检索关键词，返回相关试题片段。"""
    path = Path(pdf_path)
    pypdf = _ensure_pypdf()
    if not path.exists() or pypdf is None:
        return []

    results = []
    needle = str(keyword).lower()
    # [缺陷修复] max_results<=0 时，此前是「先 append 再判断」，会返回 1 条而非 0 条。
    if max_results is not None and max_results <= 0:
        return []
    try:
        reader = pypdf.PdfReader(str(path))
        for page_number, page in enumerate(reader.pages, 1):
            try:
                page_text = page.extract_text() or ""
            except Exception:
                continue
            if needle not in page_text.lower():
                continue
            lines = page_text.splitlines()
            for line_index, line in enumerate(lines):
                if needle not in line.lower():
                    continue
                start = max(0, line_index - 2)
                end = min(len(lines), line_index + 6)
                snippet = "\n".join(lines[start:end]).strip()
                results.append(f"[第 {page_number} 页 / 考点相关片段]:\n{snippet}")
                if len(results) >= max_results:
                    return results
    except Exception:
        return results
    return results


__all__ = [
    "extract_pdf_page",
    "extract_pdf_pages",
    "find_questions_by_keyword",
    "list_materials",
    "search_text_in_materials",
]
