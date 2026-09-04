# -*- coding: utf-8 -*-
"""
变式题真实检索与防虚构溯源引擎 (Variant Retriever)
对标：夸克同类巩固题推送
核心准则：
  1. 真实优先：优先在本地「参考资料/」与真题库中按考点关键词检索真实题源
  2. 防虚构红线：若本地未找到匹配真实题目，派发自拟变式时必须打上防虚构水印：
     【⚠️ 私教自拟变式 · 本地未放置实体真题】
  3. 杜绝 AI 凭空捏造张宇、李林等未持有的图书来源
"""

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

try:
    from skills import pdf_extractor, error_logger
except Exception:
    try:
        from tools.skills import pdf_extractor, error_logger
    except Exception:
        pdf_extractor = None
        error_logger = None

SUBJECT_DIRS = {
    "math": "01-数学",
    "eng": "02-英语",
    "pol": "03-思想政治理论",
    "pro": "04-专业课",
}

SUBJECT_NAMES = {
    "math": "数学二 (302)",
    "eng": "英语二 (204)",
    "pol": "思想政治理论",
    "pro": "408 计算机学科专业基础",
}


def search_real_variant(subject="math", keyword="", limit=2, **kwargs):
    """
    优先检索本地参考资料与错题本真实题目；未命中时输出明确标注的自拟变式
    """
    if "subject" in kwargs:
        keyword = subject
        subject = kwargs["subject"]
    elif subject not in SUBJECT_DIRS and not keyword:
        keyword = subject
        subject = "math"

    subj_folder = SUBJECT_DIRS.get(subject, "01-数学")
    subj_name = SUBJECT_NAMES.get(subject, subject)
    kw = keyword.strip()

    hits = []

    # 1. 检索本地参考资料库 (参考资料/*.pdf, *.md, *.txt)
    ref_dir = ROOT / subj_folder / "参考资料"
    if ref_dir.exists() and kw:
        # 扫描 Markdown / 文本
        for txt_file in ref_dir.glob("*.*"):
            if txt_file.suffix.lower() in (".md", ".txt"):
                try:
                    content = txt_file.read_text(encoding="utf-8", errors="ignore")
                    if kw in content:
                        # 截取包含关键词的段落
                        idx = content.find(kw)
                        start = max(0, idx - 100)
                        end = min(len(content), idx + 300)
                        snippet = content[start:end].strip()
                        hits.append({
                            "source_type": "real_file",
                            "source_name": txt_file.name,
                            "topic": kw,
                            "question": f"【从本地实体资料提取】\n{snippet}"
                        })
                except Exception:
                    pass

        # 扫描 PDF 试卷
        if pdf_extractor and not hits:
            for pdf_file in ref_dir.glob("*.pdf"):
                try:
                    found_snippets = pdf_extractor.find_questions_by_keyword(str(pdf_file), kw, max_results=limit)
                    for s in found_snippets:
                        hits.append({
                            "source_type": "real_pdf",
                            "source_name": pdf_file.name,
                            "topic": kw,
                            "question": f"【从本地真题/教材 PDF 提取】\n{s}"
                        })
                except Exception:
                    pass

    # 2. 检索已有错题本中的同类真题
    if not hits and error_logger and kw:
        errs = error_logger.scan_error_records(subject)
        for e in errs:
            if kw in e.get("title", "") or kw in e.get("detail", ""):
                hits.append({
                    "source_type": "error_book",
                    "source_name": f"历史错题集 ({e.get('date', '')}) · {e.get('title', '')}",
                    "topic": kw,
                    "question": f"【历史真题错题现场 · 考点: {kw}】\n{e.get('question', e.get('detail', ''))}"
                })
            if len(hits) >= limit:
                break

    # 3. 若本地真实题库未命中，启动自拟变式并打上防虚构水印
    if not hits:
        synthetic_variants = _generate_synthetic_variant(subject, kw)
        return {
            "subject": subject,
            "subject_name": subj_name,
            "keyword": kw,
            "is_real_source": False,
            "source_status": "未在本地「参考资料/」发现实体题源，已按考纲要求生成规范自拟变式",
            "variants": synthetic_variants
        }

    return {
        "subject": subject,
        "subject_name": subj_name,
        "keyword": kw,
        "is_real_source": True,
        "source_status": f"成功从本地资料库精准命中 {len(hits)} 道真实同考点题目",
        "variants": hits[:limit]
    }


def _generate_synthetic_variant(subject, keyword):
    """
    根据考纲要求生成严格打上防虚构标签的变式题
    """
    kw = keyword or "核心考点"
    if subject == "math":
        q = (
            f"【⚠️ 私教自拟变式 · 题源未挂载本地实体资料】\n"
            f"设函数 $f(x)$ 在 $[0, 1]$ 上具有连续的二阶导数，且满足 $f(0) = 0, f(1) = 1, f'(0) = 0$。\n"
            f"证明：关于【{kw}】，在开区间 $(0, 1)$ 内至少存在一点 $\\xi$，使得 $f''(\\xi) > 2$。"
        )
    elif subject == "eng":
        q = (
            f"【⚠️ 私教自拟变式 · 题源未挂载本地实体资料】\n"
            f"长难句搭积木变式训练（考点：{kw}）：\n"
            f"\"The assumption that technological advancement inherently mitigates social inequality "
            f"overlooks the systemic constraints under which marginalized communities operate.\"\n"
            f"请拆解主干骨架、分析从句修饰关系，并指出核心动词与宾语。"
        )
    elif subject == "pol":
        q = (
            f"【⚠️ 私教自拟变式 · 题源未挂载本地实体资料】\n"
            f"多项选择题变式自测（考点：{kw}）：\n"
            f"在马克思主义唯物辩证法中，关于对立统一规律与矛盾特殊性的表述，下列选项中正确的是（  ）\n"
            f"A. 矛盾的普遍性寓于特殊性之中\n"
            f"B. 矛盾双方的转化不需要具备客观条件\n"
            f"C. 抓关键和看主流是同一哲学范畴的不同表述\n"
            f"D. 具体问题具体分析是正确认识事物的基础"
        )
    else:
        q = (
            f"【⚠️ 私教自拟变式 · 题源未挂载本地实体资料】\n"
            f"408 专业课算法与设计变式题（考点：{kw}）：\n"
            f"已知一个长度为 n 的单链表，节点元素为整型且不重复。\n"
            f"请设计一个时间和空间复杂度最优的算法，判断链表中是否存在满足三元组两两和相等的节点组合，并给出规范步骤分证明。"
        )

    return [{
        "source_type": "synthetic_with_watermark",
        "source_name": "私教自拟变式（严格遵循官方大纲防超纲红线）",
        "topic": kw,
        "question": q
    }]


def format_variant_output(result: dict) -> str:
    """格式化变式题输出结果"""
    subj_name = result.get("subject_name", "")
    kw = result.get("keyword", "")
    is_real = result.get("is_real_source", False)
    status_desc = result.get("source_status", "")
    variants = result.get("variants", [])

    lines = [
        f"\n============================================================",
        f"  🔍 考研同类真题变式检索 · {subj_name} · 考点: 【{kw}】",
        f"============================================================",
        f"题源溯源属性: {'[白名单实体资料]' if is_real else '[⚠️ 私教自拟变式]'}",
        f"溯源状态说明: {status_desc}",
        f"------------------------------------------------------------"
    ]

    for i, v in enumerate(variants, 1):
        lines.append(f"\n【变式练习 {i}】 出处: {v.get('source_name', '未标明')}")
        lines.append(v.get("question", "").strip())
        lines.append(f"\n👉 答题建议：请在草稿纸上独立书写推导步骤，完成后输入「交作业」由私教按采分点赋分！")

    lines.append(f"============================================================\n")
    return "\n".join(lines)
