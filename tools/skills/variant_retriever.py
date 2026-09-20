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

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

# [C2 修复·惰性导入] 此前用 ``from skills import pdf_extractor, error_logger``
# 取兄弟模块。该写法会在本模块导入期把 pdf_extractor **重新绑定进包命名空间**，
# 从而连带拉起 pypdf + cryptography，使包级惰性导入（skills/__init__.py）失效 ——
# 实测 `import skills` 仍会付约 100ms 的 pypdf 导入代价。
# 改为直接引用兄弟模块（``from . import xxx``），包命名空间不再被污染；
# 用到 pdf_extractor 的地方改走惰性获取函数。
from . import error_logger

try:
    from . import get_subject_name
except Exception:                                   # 循环导入兜底
    def get_subject_name(s, d=None):
        return SUBJECT_NAMES.get(s, d if d is not None else s)


def _pdf_extractor():
    """惰性获取 PDF 抽取技能；不可用时返回 None，PDF 功能单独降级。"""
    try:
        from . import pdf_extractor
        return pdf_extractor
    except Exception:
        return None

SUBJECT_DIRS = {
    "math": "01-数学",
    "eng": "02-英语",
    "pol": "03-思想政治理论",
    "pro": "04-专业课",
}

# 仅作 config 缺失时的中性回退；实际科目名以 ky_config.json 的 study_plan 为准
_SUBJECT_NAME_FALLBACK = {
    "math": "数学",
    "eng": "英语",
    "pol": "思想政治理论",
    "pro": "专业课",
}
SUBJECT_NAMES = dict(_SUBJECT_NAME_FALLBACK)


# 408 统考专属考点关键词（仅当科目确为 408 计算机时才允许套用其算法模板）
_CS_KEYWORDS = ("树", "二叉树", "遍历", "AVL", "红黑树", "森林", "图", "最短路径",
                "Dijkstra", "拓扑", "关键路径", "最小生成树", "排序", "查找", "散列", "哈希")


def suggest_keyword(subject="pro"):
    """从本科目「考试大纲.md」中抽取一个真实考点作为默认关键词。

    杜绝硬编码他科考点（例如给 814 信号与系统 默认推荐「二叉树」）。
    取不到时返回空串，由调用方回退到科目名。
    """
    subj_folder = SUBJECT_DIRS.get(subject, "01-数学")
    sy = ROOT / subj_folder / "考试大纲.md"
    if sy.exists():
        try:
            skip_section = False
            for line in sy.read_text(encoding="utf-8", errors="ignore").splitlines():
                s = line.strip()
                if s.startswith("#"):
                    # 跳过「试卷结构与分值」等非考点章节
                    skip_section = bool(re.search(r"试卷结构|分值|题型分布|参考书目", s))
                    continue
                if skip_section:
                    continue
                m = re.match(r"^-\s+\*\*(.+?)\*\*\s*[：:]\s*(.*)$", s)
                if m:
                    head, rest = m.group(1).strip(), m.group(2).strip()
                    # 数学等科目格式为「- **掌握**：极限的性质与四则运算法则…」
                    # 加粗部分是掌握等级而非考点，需取冒号后的首个语义片段
                    if head in ("掌握", "理解", "熟练应用", "应用能力", "了解", "熟练"):
                        kw = re.split(r"[、，,；;（(]", rest)[0].strip()
                    else:
                        kw = head
                    if 2 <= len(kw) <= 24:
                        return kw
        except Exception:
            pass
    return ""


def _text_bigrams(text: str) -> list:
    """中文字符 2-gram（-letter/digit 按空白切词），零依赖，供 BM25 用。"""
    t = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", " ", str(text or "").lower())
    out = []
    for frag in t.split():
        if re.search(r"[\u4e00-\u9fff]", frag):
            if len(frag) == 1:
                out.append(frag)
            else:
                out.extend(frag[i:i + 2] for i in range(len(frag) - 1))
        elif frag:
            out.append(frag)
    return out


def _bm25_block_scores(query: str, blocks: list) -> list:
    """[P0 修复·子串包含→BM25] 对切出的题块做 BM25 打分排序（k1=1.5, b=0.75，
    纯标准库）。此前 `kw in content` 命中即取第一段，无分词、无量化、无排序，
    且"搜泰勒展开没命中会硬塞一道中值定理题"。现返回与 blocks 等长的分值表。"""
    import math
    from collections import Counter
    k1, b = 1.5, 0.75
    q_terms = _text_bigrams(query)
    if not q_terms or not blocks:
        return [0.0] * len(blocks)
    doc_terms = [_text_bigrams(b) for b in blocks]
    avgdl = sum(len(d) for d in doc_terms) / len(doc_terms)
    n_docs = len(doc_terms)
    df: dict = {}
    for d in doc_terms:
        for term in set(d):
            df[term] = df.get(term, 0) + 1
    scores = []
    for d in doc_terms:
        tf = Counter(d)
        dl = len(d)
        s = 0.0
        for term in q_terms:
            f = tf.get(term, 0)
            if not f:
                continue
            n = df.get(term, 0)
            idf = math.log(1 + (n_docs - n + 0.5) / (n + 0.5))
            s += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / avgdl)) if avgdl else 0.0
        scores.append(s)
    return scores


def _split_question_blocks(content: str, cap: int = 200) -> list:
    """按题号标记把文件切成题块；无标记则整文件一块（上限 cap 块防爆）。"""
    marks = [m.start() for m in _Q_START_RE.finditer(content)]
    if len(marks) < 2:
        return [content.strip()] if content.strip() else []
    blocks = []
    for i, s in enumerate(marks):
        e = marks[i + 1] if i + 1 < len(marks) else min(len(content), s + 1500)
        blk = content[s:e].strip()
        if len(blk) >= 8:
            blocks.append(blk[:1500])
        if len(blocks) >= cap:
            break
    return blocks


# 题号/题首标记：`第N题` / `N.` / `N、` / `（N）` / `【N】` / `【题号 N】` / Markdown 标题
_Q_START_RE = re.compile(
    r"(?m)^(?:#{1,4}\s*)?(?:(?:第\s*[0-9一二三四五六七八九十]+\s*题)"
    r"|(?:[【\[](?:题号\s*)?\d+\s*[】\]])|(?:\d+\s*[.、])|(?:[(（]\d+[)）]))"
)


def _extract_question_block(content: str, idx: int, kw_len: int = 0) -> str:
    """[变式溯源粒度修复] 命中关键词后不再取前后 ±100/300 字整段（实测会从
    "里士多德；要求「掌握」"开始输出整块文档），而是向前找最近题号、向后找
    下一题号，切出单题块；找不到题号才回退固定窗口。上限 1200 字防整卷倾泻。"""
    start_probe = max(0, idx - 800)
    starts = [m.start() for m in _Q_START_RE.finditer(content, start_probe, idx)]
    start = starts[-1] if starts else max(0, idx - 200)
    fwd_from = idx + max(0, kw_len)
    ends = [m.start() for m in _Q_START_RE.finditer(content, fwd_from, fwd_from + 1200)]
    end = ends[0] if ends else min(len(content), fwd_from + 400)
    # 若块内不含关键词（标记错位），回退旧窗口，保证不空返回
    block = content[start:end].strip()
    if content[idx:idx + max(1, kw_len)] not in block:
        block = content[max(0, idx - 100):min(len(content), idx + 300)].strip()
    return block[:1200]


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
    subj_name = get_subject_name(subject, SUBJECT_NAMES.get(subject, subject))
    kw = keyword.strip()
    # 未指定考点时，从本科目真实考纲中推荐一个考点，绝不使用他科硬编码考点
    if not kw:
        kw = suggest_keyword(subject) or subj_name

    hits = []

    # 1. 检索本地参考资料库 (参考资料/*.pdf, *.md, *.txt)
    # [P0 修复] 按题块切分 + BM25 打分全局取 Top，替代"首个子串命中即取第一段"。
    ref_dir = ROOT / subj_folder / "参考资料"
    if ref_dir.exists() and kw:
        # 扫描 Markdown / 文本
        _candidates = []  # (block, source_name)
        for txt_file in sorted(ref_dir.glob("*.*")):
            if txt_file.suffix.lower() in (".md", ".txt"):
                try:
                    content = txt_file.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                for blk in _split_question_blocks(content):
                    if re.search(r"(\.{4,}|…{2,}|·{4,})\s*\d+", blk) or blk.startswith(("目 录", "目录")):
                        continue
                    _candidates.append((blk, txt_file.name))
        if _candidates:
            _scores = _bm25_block_scores(kw, [b for b, _ in _candidates])
            _ranked = sorted(
                ((s, i) for i, s in enumerate(_scores) if s > 0),
                key=lambda t: (-t[0], _candidates[t[1]][1], t[1]))
            for _s, _i in _ranked[:max(1, limit)]:
                _blk, _src = _candidates[_i]
                hits.append({
                    "source_type": "real_file",
                    "source_name": _src,
                    "topic": kw,
                    "question": f"【从本地实体资料提取】\n{_blk[:1200]}"
                })

        # 扫描 PDF 试卷
        pdf_extractor = _pdf_extractor()
        if pdf_extractor and not hits:
            for pdf_file in ref_dir.glob("*.pdf"):
                try:
                    found_snippets = pdf_extractor.find_questions_by_keyword(str(pdf_file), kw, max_results=limit)
                    for s in found_snippets:
                        if re.search(r"(\.{4,}|…{2,}|·{4,})\s*\d+", s) or s.strip().startswith(("目 录", "目录")):
                            continue
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

    # 若已配置大模型，优先调用 LLM 动态命制高质量同源变式试题
    try:
        try:
            from tools.llm_client import is_llm_configured, chat_completion
        except ImportError:
            from llm_client import is_llm_configured, chat_completion

        if is_llm_configured(workspace_root=ROOT):
            subj_title = get_subject_name(subject, SUBJECT_NAMES.get(subject, subject))
            prompt = (
                f"你是一位资深考研命题专家兼私人教练。\n"
                f"科目：{subj_title}。\n"
                f"核心考点：{kw}。\n"
                f"请围绕该考点命制一道高仿全国真题风格的经典同源变式题，要求题干完整、设问严谨有针对性。\n"
                f"请直接输出试题正文，不要输出多余客套话。"
            )
            llm_q = chat_completion(prompt, workspace_root=ROOT, timeout=12.0)
            if llm_q and len(llm_q.strip()) > 15:
                q_text = (
                    f"【⚠️ 私教自拟变式 · 题源未挂载本地实体资料】\n"
                    f"{subj_title} 同源变式练兵（考点：{kw}）：\n"
                    f"{llm_q.strip()}"
                )
                return [{
                    "source_type": "synthetic_with_watermark",
                    "source_name": "私教自拟变式（严格遵循官方大纲防超纲红线 · LLM 动态命制）",
                    "topic": kw,
                    "question": q_text,
                }]
    except Exception:
        pass

    _STAT_KEYS = ("假设", "检验", "方差", "回归", "估计", "置信", "显著", "t检验", "卡方", "ANOVA", "相关", "概率", "分布", "期望", "贝叶斯")
    if subject == "math" and any(k in kw for k in _STAT_KEYS):
        q = (
            f"【⚠️ 私教自拟变式 · 题源未挂载本地实体资料】\n"
            f"数学三概率统计变式题（考点：{kw}）：\n"
            f"设总体 X~N(μ,σ²)，(X1,…,Xn) 为容量 n=16 的简单随机样本，测得样本均值 x_bar=5.2，样本标准差 s=1.5。\n"
            f"围绕考点【{kw}】，请按“原假设—检验统计量(t/χ²)—拒绝域—P值结论”四段式完成显著性水平 α=0.05 下的规范推导，并说明两类错误含义。"
        )
    elif subject == "math":
        # [P0 修复·硬编码兜底题] 此前无论 kw 是什么都输出同一道中值定理题：
        # 搜"泰勒展开"没命中会拿到中值定理题，比"没找到"更伤信任。
        # 现改为考点驱动的开放式变式框架（仍明确标注自拟）。
        q = (
            f"【⚠️ 私教自拟变式 · 题源未挂载本地实体资料】\n"
            f"高等数学/线性代数变式题（考点：{kw}）：\n"
            f"围绕考点【{kw}】，请写出其核心定义与定理成立条件，完成一道典型题目的"
            f"规范推导，并列出该考点最常见的两个失分陷阱与规避方法。"
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
        pro_title = get_subject_name("pro", "专业课")
        # 只有当专业课确实是 408 计算机时才套用数据结构/算法模板，
        # 避免给「814 信号与系统」等自命题科目出「二叉树算法设计」这种跨科错误题。
        is_cs = ("408" in pro_title) or ("计算机" in pro_title)
        if is_cs and any(w in kw for w in ("树", "二叉树", "遍历", "AVL", "红黑树", "森林")):
            q = (
                f"【⚠️ 私教自拟变式 · 题源未挂载本地实体资料】\n"
                f"{pro_title} 数据结构算法变式题（考点：{kw}）：\n"
                f"设一棵非空二叉树 $T$ 采用二叉链表存储，请设计一个时间和空间复杂度均最优的算法，完成关于【{kw}】的核心计算与路径判定，并按采分点标准写出三段式解答（自然语言设计思想、核心算法代码与时空复杂度分析）。"
            )
        elif is_cs and any(w in kw for w in ("图", "最短路径", "Dijkstra", "拓扑", "关键路径", "最小生成树")):
            q = (
                f"【⚠️ 私教自拟变式 · 题源未挂载本地实体资料】\n"
                f"{pro_title} 图论算法变式题（考点：{kw}）：\n"
                f"设有向/无向带权图 $G=(V, E)$ 采用邻接表存储。请围绕考点【{kw}】设计算法并写出规范推导与证明步骤。"
            )
        elif any(w in kw for w in ("信号", "系统", "卷积", "傅里叶", "拉普拉斯", "Z变换", "z变换",
                                   "冲激", "采样", "滤波", "频谱", "变换", "响应", "差分", "离散")):
            q = (
                f"【⚠️ 私教自拟变式 · 题源未挂载本地实体资料】\n"
                f"{pro_title} 核心大题变式（考点：{kw}）：\n"
                f"已知连续时间线性时不变系统（LTI），其激励信号为 $x(t)$，系统冲激响应为 $h(t)$。\n"
                f"围绕考点【{kw}】，请列出系统微分方程或系统函数 $H(s)$，求解系统零状态响应，并判定系统的因果性与稳定性。"
            )
        elif is_cs and any(w in kw for w in ("排序", "查找", "散列", "哈希")):
            q = (
                f"【⚠️ 私教自拟变式 · 题源未挂载本地实体资料】\n"
                f"{pro_title} 查找与排序算法变式题（考点：{kw}）：\n"
                f"已知待处理数据规模为 $n$。请围绕考点【{kw}】设计最优处理方案，写出算法设计思想与最坏情况时空复杂度证明。"
            )
        else:
            q = (
                f"【⚠️ 私教自拟变式 · 题源未挂载本地实体资料】\n"
                f"{pro_title} 考纲核心变式题（考点：{kw}）：\n"
                f"请结合考纲对【{kw}】的重点掌握要求，写出该考点的核心定义公式、物理/数学意义与边界条件，并完成一道典型题目的规范步骤分推导。"
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
