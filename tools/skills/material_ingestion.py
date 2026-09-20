# -*- coding: utf-8 -*-
"""
KaoYan AI Study Chain · 试题与备考资料智能切片入库管道 (Material Ingestion Pipeline)

核心功能：
  1. 支持外部真题/资料 (Markdown, TXT, PDF) 的自动切片与题目分块 (Question Chunker)
  2. 智能识别题型：单选/多选 (choice)、填空题 (blank)、综合解答/大题 (essay)
  3. 提取分值与参考解答，自动构造步骤级采分点标注 (Rubric Parser, 如 [+2分]、[+4分])
  4. 自动标注考点标签与白名单题源认证，生成规范的 Markdown 题目卡片并入库归档
"""

import re
import hashlib
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from pathlib import Path
from datetime import datetime

try:  # 双导入路径兼容（项目同时存在 tools.X 与 X 两种导入方式）
    from ky_io import atomic_write_text, read_text_fallback  # noqa: E402
except ImportError:  # pragma: no cover
    from tools.ky_io import atomic_write_text, read_text_fallback  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent

# 尝试加载 Rust 极速题目切片扩展，失败时透明降级为纯 Python
try:
    import ky_rust_ext as _rust
    _HAS_RUST_EXT = True
except ImportError:
    _rust = None
    _HAS_RUST_EXT = False


@dataclass
class QuestionChunk:
    """切片题目数据模型"""
    number: int
    q_type: str                         # choice / blank / essay / term / short / discuss
    score: int                          # 题目分值
    stem: str                           # 题干
    options: List[str] = field(default_factory=list)      # 选项 [A. ..., B. ...]
    answer: str = ""                    # 参考答案
    analysis: str = ""                  # 试题解析
    rubric: List[str] = field(default_factory=list)        # 步骤采分点清单
    points: List[str] = field(default_factory=list)        # 涉及核心考点
    source: str = ""                    # 题源出处


# 文科题型判定规则：(q_type, 关键词, 无显式分值时的默认分)
# [文科适配] 自命题文科卷（名词解释/简答/论述）此前全落进 essay → 统一按
# "综合应用与解答题（10 分）"入库。默认分仅为无分值标注时的回退，题面自带
# "（本题满分 X 分）/（X 分）"时以显式分值为准。
_WENKE_TYPE_RULES = (
    ("term", ("名词解释",), 5),
    ("short", ("简答", "简述"), 10),
    ("discuss", ("论述", "材料分析", "辨析"), 15),
)

_WENKE_TYPE_NAMES = {
    "term": "名词解释题",
    "short": "简答题",
    "discuss": "论述题",
}


def _detect_wenke_type(hint_text: str):
    """从分段标题/题干首行判定文科题型，返回 (q_type, 默认分)，无命中返回 (None, 0)。"""
    hint = str(hint_text or "")
    for q_type, keywords, default_score in _WENKE_TYPE_RULES:
        if any(kw in hint for kw in keywords):
            return q_type, default_score
    return None, 0


class MaterialIngestionPipeline:
    """试题与备考资料结构化入库管道"""

    SUBJECT_MAP = {
        "math": "01-数学",
        "math1": "01-数学",
        "math2": "01-数学",
        "math3": "01-数学",
        "数": "01-数学",
        "eng": "02-英语",
        "eng1": "02-英语",
        "eng2": "02-英语",
        "英": "02-英语",
        "pol": "03-思想政治理论",
        "政": "03-思想政治理论",
        "pro": "04-专业课",
        "408": "04-专业课",
        "专": "04-专业课",
    }

    def __init__(self, workspace_root: Optional[Path] = None):
        self.workspace_root = Path(workspace_root) if workspace_root else ROOT
        # [缺陷修复·虚假认证] 默认**不**认定来源为官方。只有调用方显式置 True
        # （即学员能证明该材料确为官方/统考原题）时，题目卡才会盖 VERIFIED 戳。
        self.source_is_official = False

    def _resolve_subject_dir(self, subject: str) -> Path:
        """映射科目至工作区目录"""
        sub_clean = subject.strip().lower()
        folder_name = self.SUBJECT_MAP.get(sub_clean, "04-专业课")
        target = self.workspace_root / folder_name
        if not target.exists():
            target = self.workspace_root / "04-专业课"
        return target

    def chunk_text(self, raw_text: str, default_source: str = "外部真题资料") -> List[QuestionChunk]:
        """
        从原文本中智能分块切片出题目 (支持 Rust 加速与纯 Python 自动降级)
        """
        # [缺陷修复·表格型真题] 真题转录/题库常以 Markdown 表格承载
        # （`| 题号 | 题目 | 答案 | 考点 |`）。逐题正则分块器不认表格结构：
        # 实测一份含 10 选择 + 10 填空 + 4 计算的真实真题，被切成
        # 「选择 0 / 填空 13 / 大题 5」，且"试题原题"取到的是被拼起来的**考点列碎片**。
        # 处置：先剔除表格行再交给常规分块器（表格交由下面的专用抽取器处理），
        # 从源头杜绝碎片，避免事后用启发式去猜哪些是垃圾。
        raw_text = raw_text or ""
        text_for_regex = "\n".join(
            ln for ln in raw_text.splitlines() if not self._TABLE_ROW.match(ln))
        if len(text_for_regex.strip()) < 40:      # 整篇几乎都是表格时退回原文
            text_for_regex = raw_text

        if getattr(self, "_force_python", False) or not _HAS_RUST_EXT:
            chunks = self._chunk_text_python(text_for_regex, default_source)
        else:
            try:
                rust_chunks = _rust.chunk_text(text_for_regex, default_source)
                if rust_chunks:
                    chunks = [self._rust_to_chunk(rc) for rc in rust_chunks]
                else:
                    chunks = self._chunk_text_python(text_for_regex, default_source)
            except Exception:
                chunks = self._chunk_text_python(text_for_regex, default_source)
        table_chunks = self._extract_table_chunks(raw_text, default_source)
        chunks = self._merge_table_chunks(chunks, table_chunks)
        chunks = self._reclassify_by_sections(chunks, raw_text)

        # [缺陷修复·碎片清洗] 当本篇确为"表格型题库"（表格里成规模地承载了 Q&A）时，
        # 选择题/填空题必然自带「答案」列；因此**无答案的非大题分块**只可能是
        # 表格打散后的碎片，或被压扁记录的考点清单（如
        # `1. 频谱系数　2. 傅里叶变换　3. 连续时间复信号的奇分量…`），
        # 它们既不能自动判分、题干也不成立，统一剔除以免污染题库。
        # 非表格文档不受影响（走原逻辑），避免误删正常的无答案题。
        if len(table_chunks) >= 3:
            chunks = [c for c in chunks
                      if c.q_type == "essay" or (c.answer or "").strip()]
            for idx, c in enumerate(chunks, 1):
                c.number = idx
        return chunks

    # ── Markdown 表格型题库抽取 ───────────────────────────────────────────
    _TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")

    def _merge_table_chunks(self, chunks, table_chunks):
        """并入按表格列精确抽取的题目，并统一重新编号 + 按题干去重。

        调用前，raw_text 中的表格行已从常规分块器的输入中剔除，
        因此此处无需再用启发式判断"哪些是表格碎片"。
        """
        if not table_chunks:
            return chunks

        kept = list(chunks)
        seen = {re.sub(r"\s+", "", (c.stem or ""))[:30] for c in kept}
        for tc in table_chunks:
            key = re.sub(r"\s+", "", tc.stem or "")[:30]
            if key in seen:
                continue
            seen.add(key)
            kept.append(tc)
        for idx, c in enumerate(kept, 1):
            c.number = idx
        return kept

    def _extract_table_chunks(self, raw_text: str, default_source: str) -> List[QuestionChunk]:
        """从 Markdown 表格行中抽取题目。

        识别条件：表格表头同时含「题目」列，并含「答案」或「考点」列（顺序不限）。
        题型判定：答案形如单个字母（A/B/C/D）→ choice；否则 → blank。
        """
        lines = (raw_text or "").splitlines()
        out: List[QuestionChunk] = []
        i, n = 0, len(lines)
        while i < n:
            row = self._TABLE_ROW.match(lines[i])
            if not row:
                i += 1
                continue
            header_cells = [c.strip() for c in row.group(1).split("|")]
            # 表头必须含「题目」；且需有答案或考点列
            def _find(keys):
                for idx, c in enumerate(header_cells):
                    if any(k in c for k in keys):
                        return idx
                return -1

            col_stem = _find(("题目", "题干", "试题", "设问"))
            col_ans = _find(("答案", "参考答案"))
            col_point = _find(("考点", "知识点", "考查"))
            col_no = _find(("题号", "#", "编号"))
            if col_stem < 0 or (col_ans < 0 and col_point < 0):
                i += 1
                continue
            # 跳过表头下的分隔行 |---|---|
            j = i + 1
            if j < n and re.match(r"^\s*\|[\s:\-|]+\|\s*$", lines[j]):
                j += 1
            while j < n:
                r2 = self._TABLE_ROW.match(lines[j])
                if not r2:
                    break
                cells = [c.strip() for c in r2.group(1).split("|")]
                if len(cells) <= max(col_stem, col_ans, col_point, col_no):
                    j += 1
                    continue
                stem = re.sub(r"\*\*|`", "", cells[col_stem]).strip()
                if not stem or len(stem) < 4:
                    j += 1
                    continue
                ans = re.sub(r"\*\*|`", "", cells[col_ans]).strip() if col_ans >= 0 else ""
                point = cells[col_point].strip() if col_point >= 0 else ""
                no_txt = cells[col_no].strip() if col_no >= 0 else ""
                # 答案形如「B」「B 线性时变」「B. 线性时变」→ 选择题；其余按填空处理
                q_type = "choice" if re.match(r"^[A-Da-d]\s*($|[、.,，。：:\-]|\s)", ans) else "blank"
                try:
                    num = int(re.sub(r"\D", "", no_txt) or 0)
                except Exception:
                    num = 0
                out.append(QuestionChunk(
                    number=num or (len(out) + 1),
                    q_type=q_type,
                    score=2 if q_type == "choice" else 5,
                    stem=stem,
                    answer=ans,
                    points=[p for p in re.split(r"[、,，/]", point) if p][:4],
                    source=default_source,
                ))
                j += 1
            i = j
        return out

    # 修正版大题分段词表：覆盖「综合计算题」「计算分析题」「算法设计题」等组合写法
    # [文科适配] 追加名词解释 / 论述 / 材料分析 / 辨析分段，否则文科卷整节落到
    # else 分支被判为"综合应用与解答题（10 分）"（名词解释 6 题变 10 分大题）。
    _WENKE_SEC_ALTS = r"名词解释|论述题|材料分析题|辨析题"
    _SEC_LINE_PATTERN = re.compile(
        r'^[#*\s]*(?:第?[一二三四五六七八九十]+[部分题大题]*[、\.\s]*)?'
        r'(?P<sec_title>(?:单[项]?选择题|多[项]?选择题|不定项选择题|选择题|填空题|判断题|解答题|'
        r'综合(?:应用|计算|分析|论述)?题|计算(?:分析)?题|证明题|算法(?:设计)?题|简(?:答|述)题|分析题|应用题|大题|'
        + _WENKE_SEC_ALTS + r')[^\n]*)$',
        re.MULTILINE
    )

    def _reclassify_by_sections(self, chunks: List[QuestionChunk], raw_text: str) -> List[QuestionChunk]:
        """按修正后的分段词表在原文上重新定位大题分段，并据此重判每道题的题型。

        背景：Rust 扩展与旧版 Python 的分段词表覆盖不全（如「三、综合计算题」），
        导致大题被静默沿用上一分段类型（误判为填空）。此步骤保证题型统计与真实试卷一致。
        """
        if not chunks or not raw_text:
            return chunks
        sections = [(m.start(), m.group("sec_title").strip())
                    for m in self._SEC_LINE_PATTERN.finditer(raw_text)]
        if not sections:
            return chunks

        # 构建去空白原文与 索引映射，用于把题干前缀定位回原文位置
        norm_chars, norm_idx = [], []
        for i, ch in enumerate(raw_text):
            if not ch.isspace():
                norm_chars.append(ch)
                norm_idx.append(i)
        norm_text = "".join(norm_chars)

        cursor = 0
        for c in chunks:
            probe = re.sub(r"\s+", "", c.stem or "")[:40]
            pos = -1
            if probe:
                found = norm_text.find(probe, cursor)
                if found < 0:
                    found = norm_text.find(probe)
                if found >= 0:
                    pos = norm_idx[found]
                    cursor = found + 1
            if pos < 0:
                continue
            sec_title = ""
            for s_pos, s_title in sections:
                if s_pos <= pos:
                    sec_title = s_title
            if not sec_title:
                continue
            if "选择" in sec_title:
                c.q_type = "choice"
            elif "填空" in sec_title:
                c.q_type = "blank"
            else:
                _wt, _ws = _detect_wenke_type(sec_title)
                c.q_type = _wt if _wt else "essay"
                if _wt and not c.score:
                    c.score = _ws
        return chunks

    def _rust_to_chunk(self, d: dict) -> QuestionChunk:
        """将 Rust 字典转换为 QuestionChunk 对象"""
        return QuestionChunk(
            number=int(d.get("number", 0)),
            q_type=str(d.get("q_type", "essay")),
            score=int(d.get("score", 10)),
            stem=str(d.get("stem", "")),
            options=list(d.get("options", [])),
            answer=str(d.get("answer", "")),
            analysis=str(d.get("analysis", "")),
            rubric=list(d.get("rubric", [])),
            points=list(d.get("points", [])),
            source=str(d.get("source", "")),
        )

    def _chunk_text_python(self, raw_text: str, default_source: str = "外部真题资料") -> List[QuestionChunk]:
        """纯 Python 题目切片实现 (降级兼容基准)"""
        chunks: List[QuestionChunk] = []
        if not raw_text or not raw_text.strip():
            return chunks

        text = raw_text.replace("\r\n", "\n").replace("\r", "\n")

        # 识别大题分段 (Sections, 如 一、单项选择题；文科如 一、名词解释)
        sec_pattern = re.compile(
            r'^[#*\s]*(?:第?[一二三四五六七八九十]+[部分题大题]*[、\.\s]*)?(?P<sec_title>(?:单[项]?选择题|多[项]?选择题|不定项选择题|选择题|填空题|判断题|解答题|综合(?:应用|计算|分析|论述)?题|计算(?:分析)?题|证明题|算法(?:设计)?题|简(?:答|述)题|分析题|应用题|大题|名词解释|论述题|材料分析题|辨析题)[^\n]*)$',
            re.MULTILINE
        )
        # [文科适配·答案区误切] "我的答案/参考答案"小节本身不是题型分段，其下编号行
        # 此前继承上一分段被当成新题切出（实测 12 题变 18 题）。将其识别为特殊分段，
        # 下游按 sec_hint 直接跳过。要求整行标题，避免误伤块内"【答案】"标记。
        answer_sec_pattern = re.compile(
            r'^[#*\s]*(?P<sec_title>(?:我的答案|参考答案|答案解析|试题解析|答案与解析))[^\n]*$',
            re.MULTILINE
        )
        sections = sorted(
            list(sec_pattern.finditer(text)) + list(answer_sec_pattern.finditer(text)),
            key=lambda m: m.start())

        # 主题目编号行首严格匹配: 1. 或 1、 或 【1】 或 [题1] 等 (不误伤解答题内的 (1) 或 (2))
        q_pattern = re.compile(
            r'(?:^|\n)[ \t]*(?:(?P<num>\d+)[\.、][ \t]*|[【\[](?:第|题|Q)?(?P<num2>\d+)[】\]题][ \t]*)',
            re.MULTILINE
        )
        matches = list(q_pattern.finditer(text))

        if not matches:
            if len(text.strip()) > 20:
                c = self._parse_single_block(text.strip(), 1, default_source)
                chunks.append(c)
            return chunks

        for i in range(len(matches)):
            m = matches[i]
            start_pos = m.start()
            end_pos = matches[i + 1].start() if (i + 1 < len(matches)) else len(text)
            block = text[start_pos:end_pos].strip()

            # 确定当前题目所在的大题分段
            sec_hint = ""
            for s in sections:
                if s.start() <= start_pos:
                    sec_hint = s.group("sec_title")

            # [文科适配·答案区误切] "我的答案/参考答案"小节里的编号行（如"1. 我的理解…"）
            # 此前会被当成新题切出（实测 12 题变 18 题，多出的 6 条来自答案区）。
            if any(k in sec_hint for k in ("参考答案", "我的答案", "答案解析", "试题解析", "答案与解析")):
                continue

            num_val = int(m.group("num") or m.group("num2") or (i + 1))
            chunk = self._parse_single_block(block, num_val, default_source, sec_hint=sec_hint)
            if chunk.stem:
                chunks.append(chunk)

        return chunks

    def _parse_single_block(self, block: str, num: int, source: str, sec_hint: str = "") -> QuestionChunk:
        """解析单个题块"""
        # 剥离题块末尾可能粘连的下一个大题标题
        block = re.sub(r"\n+[#*\s]*(?:第?[一二三四五六七八九十]+[部分题大题]*[、\.\s]*)?(?:单[项]?选择题|多[项]?选择题|不定项选择题|选择题|填空题|判断题|解答题|综合(?:应用|计算|分析|论述)?题|计算(?:分析)?题|证明题|算法(?:设计)?题|简(?:答|述)题|分析题|应用题|大题|名词解释|论述题|材料分析题|辨析题)[^\n]*$", "", block).strip()

        # 提取分值：如 (本题满分 10 分) / (12分) / [5分]
        score = 0
        m_score = re.search(r"(?:本题满分|满分|分值|共)?\s*(\d+)\s*分", block)
        if m_score:
            score = int(m_score.group(1))

        # 分割【题干】与【答案/解析】
        answer = ""
        analysis = ""
        rubric_list: List[str] = []

        # 常见切分标记
        split_markers = [
            r"【答案】", r"【参考答案】", r"参考答案[：:]", r"【解】", r"解[：:]", r"答案[：:]", r"【解析】", r"解析[：:]"
        ]
        split_pat = re.compile(f"({'|'.join(split_markers)})")
        parts = split_pat.split(block, maxsplit=1)

        raw_stem = parts[0].strip()

        # 过滤书籍/材料目录行 (TOC line: 章节名 ... 页码)
        if re.search(r"(?:\.{4,}|…{2,}|·{4,}|—{4,})\s*\d+\s*$", raw_stem.strip()) or (
            "目录" in raw_stem and re.search(r"\d+\s*$", raw_stem.strip())
        ):
            return QuestionChunk(
                number=num, q_type="essay", stem="", options=[], answer="",
                analysis="", rubric=[], points=[], score=0, source=source
            )

        ans_and_ana = ""
        if len(parts) >= 3:
            ans_and_ana = parts[1] + parts[2]
        elif len(parts) == 2:
            ans_and_ana = parts[1]

        # 进一步拆解答案与解析
        if ans_and_ana:
            # 查找解析标记
            m_ana = re.split(r"(?:【解析】|解析[：:]|【评析】)", ans_and_ana, maxsplit=1)
            if len(m_ana) > 1:
                answer = re.sub(r"^(?:【答案】|【参考答案】|参考答案[：:]|【解】|解[：:]|答案[：:])\s*", "", m_ana[0]).strip()
                analysis = m_ana[1].strip()
            else:
                answer = ans_and_ana.strip()

        # 仅当首行包含明确的大题分段词时才剔除大题段标题
        stem_clean = re.sub(
            r"^[#*\s]*(?:第?[一二三四五六七八九十]+[部分题大题]*[、\.\s]*)?(?:单[项]?选择题|多[项]?选择题|不定项选择题|选择题|填空题|判断题|解答题|综合(?:应用|计算|分析|论述)?题|计算(?:分析)?题|证明题|算法(?:设计)?题|简(?:答|述)题|分析题|应用题|大题)[^\n]*\n+",
            "",
            raw_stem
        ).strip()
        stem_clean = re.sub(r"^(?:\d+[\.、\s]+|[【\[](?:题|Q)?\d+[】\]])", "", stem_clean).strip()
        stem_clean = re.sub(r"^(?:（\d+分）|\(\d+分\)|\[\d+分\]|【\d+分】)\s*", "", stem_clean).strip()

        # 识别选项 (A. ... B. ... C. ... D. ...)
        options: List[str] = []
        opt_matches = list(re.finditer(r"(?:\n|^|\s+)([A-D])[\.、\s]+([^\n\rA-D]+)", stem_clean))
        is_essay_sec = any(k in sec_hint for k in ["综合", "解答", "计算", "应用", "简答", "证明", "设计", "算法"])

        if len(opt_matches) >= 2 or ("选择" in sec_hint and len(opt_matches) >= 1):
            q_type = "choice"
            if not score:
                score = 2 if "选择" in sec_hint else (5 if "408" in source or "专业" in source else 2)
            for om in opt_matches:
                opt_letter = om.group(1).strip()
                opt_content = om.group(2).strip()
                options.append(f"{opt_letter}. {opt_content}")
            # 提取题干主体（去掉末尾选项行）
            if opt_matches:
                first_opt_idx = opt_matches[0].start()
                stem_clean = stem_clean[:first_opt_idx].strip()
        # [文科适配] 先判文科题型（分段标题优先，题干首行兜底），再走理科 essay 逻辑
        _wenke_hint = f"{sec_hint}\n{stem_clean.splitlines()[0] if stem_clean else ''}"
        _wenke_type, _wenke_score = _detect_wenke_type(_wenke_hint)
        if _wenke_type:
            q_type = _wenke_type
            if not score:
                score = _wenke_score
        elif is_essay_sec:
            q_type = "essay"
            if not score:
                score = 10 if "408" in source or "专业" in source else 10
        elif "____" in stem_clean or "填空" in block or "填空" in sec_hint or re.search(r"（\s*）|\(\s*\)", stem_clean):
            q_type = "blank"
            if not score:
                score = 5
        else:
            q_type = "essay"
            if not score:
                score = 10

        # 智能提取步骤采分点 (Rubric)
        if analysis or answer:
            full_ans = (answer + "\n" + analysis).strip()
            # 1. 优先提取显式采分标记 [+X分] (支持行首、行中与行末)
            for l in full_ans.splitlines():
                l_str = l.strip()
                if not l_str:
                    continue
                m_rub = re.search(r"\[([\+＋]?\d+分)\]", l_str)
                if m_rub:
                    pts = m_rub.group(1).replace("＋", "+")
                    if not pts.startswith("+"):
                        pts = "+" + pts
                    desc = re.sub(r"\[[\+＋]?\d+分\]", "", l_str).strip()
                    rubric_list.append(f"[{pts}] {desc}")

            # 2. 隐式采分点启发式拆分：按解题步骤 (1)、(2)、或者分步换行
            if not rubric_list:
                step_lines = [l.strip() for l in full_ans.splitlines() if l.strip()]
                allocated = 0
                step_val = max(1, score // max(1, len(step_lines)))
                for s_idx, sl in enumerate(step_lines[:4], 1):
                    if len(sl) >= 6:
                        this_score = step_val if (allocated + step_val <= score) else (score - allocated)
                        if this_score > 0:
                            rubric_list.append(f"[+{this_score}分] 步骤{s_idx}：{sl[:60]}...")
                            allocated += this_score

        # 提炼知识点关键词
        points: List[str] = []
        kw_candidates = [
            "二叉树", "平衡二叉树", "图的遍历", "Dijkstra", "快速排序", "分页存储", "虚拟内存", "TCP", "三次握手",
            "中值定理", "洛必达法则", "定积分", "二重积分", "特征值", "特征向量", "正定二次型", "马原", "毛中特"
        ]
        combined_text = stem_clean + " " + answer + " " + analysis
        for kw in kw_candidates:
            if kw in combined_text and kw not in points:
                points.append(kw)

        return QuestionChunk(
            number=num,
            q_type=q_type,
            score=score,
            stem=stem_clean,
            options=options,
            answer=answer,
            analysis=analysis,
            rubric=rubric_list,
            points=points,
            source=source
        )

    def enrich_rubric_with_llm(self, chunk: QuestionChunk, subject: str = "pro") -> None:
        """若配置了 LLM 且试题缺失详细采分点或考点时，调用大模型推演采分点与核心考点"""
        if chunk.rubric and chunk.points and chunk.analysis:
            return
        try:
            try:
                from tools.llm_client import is_llm_configured, chat_completion
            except ImportError:
                from llm_client import is_llm_configured, chat_completion

            if is_llm_configured(workspace_root=self.workspace_root):
                stem = chunk.stem[:300]
                ans = (chunk.answer or "")[:300]
                prompt = (
                    f"你是一位考研阅卷专家。请针对以下试题（满分 {chunk.score} 分）分析并补充步骤采分点与核心考点：\n"
                    f"【试题】：{stem}\n"
                    f"【参考解答】：{ans}\n\n"
                    f"请以合法 JSON 格式输出：\n"
                    f'{{\n  "rubric": ["[+2分] 步骤1", "[+3分] 步骤2"],\n  "points": ["考点1", "考点2"],\n  "analysis": "简明解析"\n}}'
                )
                res = chat_completion(prompt, workspace_root=self.workspace_root, timeout=10.0)
                if res:
                    raw_json = re.sub(r"^```(?:json)?\s*", "", res.strip(), flags=re.IGNORECASE)
                    raw_json = re.sub(r"\s*```$", "", raw_json)
                    import json
                    data = json.loads(raw_json)
                    if isinstance(data, dict):
                        if not chunk.rubric and isinstance(data.get("rubric"), list):
                            chunk.rubric = [str(x) for x in data["rubric"] if x]
                        if not chunk.points and isinstance(data.get("points"), list):
                            chunk.points = [str(x) for x in data["points"] if x]
                        if not chunk.analysis and data.get("analysis"):
                            chunk.analysis = str(data["analysis"])
        except Exception:
            pass

    def format_question_card(self, chunk: QuestionChunk, subject: str = "pro") -> str:
        """
        生成规范的考研白名单题目 Markdown 卡片
        """
        if chunk.score >= 5 and (not chunk.rubric or not chunk.points):
            self.enrich_rubric_with_llm(chunk, subject=subject)

        type_names = {"choice": "单项选择题", "blank": "填空题", "essay": "综合应用与解答题",
                      **_WENKE_TYPE_NAMES}
        t_name = type_names.get(chunk.q_type, "综合题")
        # [缺陷修复·虚假认证] 旧实现仅凭**文件名**是否含「真题/统考/大纲/官方/教育部」
        # 就盖上 `[VERIFIED 官方考纲真题/统考原题]`。实测一份名为「…真题逐题转录」的
        # **回忆版**（文件自述"手写答案是考生自己标的，不是官方答案"）被判为 VERIFIED，
        # 而其内容恰是被误解析的表格碎片 —— 对未核验材料授予权威认证，
        # 与本项目「防幻觉」目标直接冲突。
        # 现改为：默认一律「待核验」；仅当调用方显式声明来源为官方时才认证；
        # 且文件名含"回忆/转录/整理/笔记/手抄"等特征时强制降级。
        _src = chunk.source or ""
        _looks_transcribed = any(kw in _src for kw in ("回忆", "转录", "整理", "笔记", "手抄"))
        if getattr(self, "source_is_official", False) and not _looks_transcribed:
            auth_status = "✅ `[VERIFIED 官方考纲真题/统考原题]`"
        else:
            auth_status = "📥 `[USER_IMPORTED 外部自导入试题 · 待核验]`"

        points_str = "、".join(chunk.points) if chunk.points else "核心综合考点"

        lines = [
            f"### 【题号 {chunk.number}】{t_name}（满分: {chunk.score} 分）",
            f"- **【题源出处】**：`{chunk.source or '外部导入题库'}`",
            f"- **【考查考点】**：`{points_str}`",
            f"- **【白名单认证】**：{auth_status}",
            "",
            "#### 1. 试题原题",
            chunk.stem,
        ]

        if chunk.options:
            lines.append("")
            for opt in chunk.options:
                lines.append(f"- **{opt}**")

        if chunk.answer:
            lines.extend([
                "",
                "#### 2. 标准答案",
                f"> {chunk.answer}"
            ])

        if chunk.rubric:
            lines.extend([
                "",
                "#### 3. 步骤级采分点标注 (Rubric)",
                "| 采分步骤 | 赋分要求 | 步骤推导重点与易错点 |",
                "|---|---|---|"
            ])
            for r in chunk.rubric:
                lines.append(f"| `{r[:8]}` | {r[8:].strip()} | 规范步骤书写，禁止盲目跳步 |")

        if chunk.analysis:
            lines.extend([
                "",
                "#### 4. 命题人逻辑与私教解析",
                chunk.analysis
            ])

        lines.extend([
            "",
            "---",
            ""
        ])

        return "\n".join(lines)

    def ingest_text(
        self,
        text_content: str,
        subject: str = "pro",
        source_name: str = "导入真题集",
        target_path: Optional[Path] = None
    ) -> Dict[str, Any]:
        """
        将纯文本/Markdown 题目批量切片并入库落盘
        """
        chunks = self.chunk_text(text_content, default_source=source_name)
        if not chunks:
            return {
                "success": False,
                "msg": "未能从输入文本中切分出有效题目，请检查题号格式 (如 1. / 2.)",
                "count": 0
            }

        sub_dir = self._resolve_subject_dir(subject)
        ref_dir = sub_dir / "参考资料"
        ref_dir.mkdir(parents=True, exist_ok=True)

        safe_src = re.sub(r'[\\/:*?"<>|]+', '_', source_name)
        now_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
        if not target_path:
            target_path = ref_dir / f"题库切片_{safe_src}_{now_tag}.md"
        else:
            target_path = Path(target_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)

        card_mds = []
        card_mds.append(f"# 📚 考研白名单题库切片集 · {source_name}\n")
        # [缺陷修复·措辞过强] 此前标题栏一律宣称「认证状态: [白名单已收录]」，
        # 容易被读成"已核验的权威题源"。实际含义只是"文件已放进本地参考资料目录"。
        # 改为如实描述，并把逐题核验状态交由每题卡片的【白名单认证】字段呈现。
        card_mds.append(
            f"> **入库时间**: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}` | "
            f"**试题总量**: `{len(chunks)} 道` | **收录状态**: `[已入本地资料库 · 逐题核验状态见下]`\n")
        card_mds.append("---\n")

        for c in chunks:
            card_mds.append(self.format_question_card(c, subject=subject))

        full_output = "\n".join(card_mds)

        # [P3 修复·D11] 幂等去重：同一份真题经不同入口重复入库时，旧实现每次都
        # 以「题库切片_<源名>_<时间戳>.md」新增一份完全重复的切片文件。
        # 现按正文指纹（忽略入库时间行）判重：命中则直接复用既有文件，不再重复落盘。
        def _ingest_fingerprint(text: str) -> str:
            keep = []
            for ln in str(text or "").splitlines():
                s = ln.strip()
                if not s or "入库时间" in s:
                    continue
                keep.append(s)
            return hashlib.sha256("\n".join(keep).encode("utf-8")).hexdigest()

        if target_path is None or not Path(target_path).exists():
            _fp_new = _ingest_fingerprint(full_output)
            for _cand in sorted(ref_dir.glob(f"题库切片_{safe_src}_*.md")):
                try:
                    if _ingest_fingerprint(_cand.read_text(encoding="utf-8", errors="ignore")) == _fp_new:
                        return {
                            "success": True,
                            "count": len(chunks),
                            "choices": sum(1 for c in chunks if c.q_type == "choice"),
                            "blanks": sum(1 for c in chunks if c.q_type == "blank"),
                            "essays": sum(1 for c in chunks if c.q_type == "essay"),
                            "target_path": str(_cand),
                            "chunks": chunks,
                            "reused_existing": True,
                            "summary": f"题源《{source_name}》内容与既有切片一致，已复用 {_cand.name}（未重复入库）"
                        }
                except Exception:
                    continue

        atomic_write_text(target_path, full_output)

        choices = sum(1 for c in chunks if c.q_type == "choice")
        blanks = sum(1 for c in chunks if c.q_type == "blank")
        essays = sum(1 for c in chunks if c.q_type == "essay")

        return {
            "success": True,
            "count": len(chunks),
            "choices": choices,
            "blanks": blanks,
            "essays": essays,
            "target_path": str(target_path),
            "chunks": chunks,
            "summary": f"成功切片入库 {len(chunks)} 道试题 (选择: {choices}，填空: {blanks}，大题: {essays})"
        }

    def ingest_file(
        self,
        file_path: Path,
        subject: str = "pro",
        source_name: str = "",
        target_path: Optional[Path] = None
    ) -> Dict[str, Any]:
        """
        读取文件并执行入库切片
        """
        p = Path(file_path)
        if not p.exists():
            return {"success": False, "msg": f"文件不存在: {file_path}", "count": 0}

        src_name = source_name or p.stem

        if p.suffix.lower() == ".pdf":
            # 尝试通过 pypdf 读取
            try:
                import pypdf
                reader = pypdf.PdfReader(str(p))
                pages = [page.extract_text() or "" for page in reader.pages]
                raw_text = "\n".join(pages)
                if not raw_text.strip() or len(raw_text.strip()) < 15:
                    return {
                        "success": False,
                        "msg": f"PDF 文件共 {len(reader.pages)} 页，但未提取出文本（该试卷可能为纯扫描图片版）。建议：使用 OCR 工具预提取文本或使用拍照批改功能。",
                        "count": 0
                    }
            except Exception as e:
                return {"success": False, "msg": f"读取 PDF 异常: {e} (若未安装 pypdf/cryptography 请运行 pip install pypdf cryptography)", "count": 0}
        else:
            # 原实现为 read_text(encoding="utf-8", errors="ignore")：GBK 编码的
            # .txt/.md 真题会让无法解码的中文字节被**静默丢弃**，而下游照常汇报
            # 「成功入库 N 道」—— 入库的是残缺内容。改为按候选编码显式解码，
            # 全部失败则明确报错并给出可操作建议，绝不静默丢数据。
            try:
                raw_text = read_text_fallback(p)
            except Exception as e:
                return {
                    "success": False,
                    "count": 0,
                    "msg": (f"文本编码无法识别（已尝试 UTF-8 / UTF-8-BOM / GBK）：{e}。"
                            f"请将《{src_name}》另存为 UTF-8 编码后重试，"
                            f"以免中文内容被静默丢弃。")
                }

        if not raw_text.strip():
            return {"success": False, "count": 0,
                    "msg": f"《{src_name}》解码后内容为空，未入库任何题目。"}

        return self.ingest_text(
            text_content=raw_text,
            subject=subject,
            source_name=src_name,
            target_path=target_path
        )


_ingestion_instance: Optional[MaterialIngestionPipeline] = None


def get_material_ingestion_pipeline() -> MaterialIngestionPipeline:
    global _ingestion_instance
    if _ingestion_instance is None:
        _ingestion_instance = MaterialIngestionPipeline()
    return _ingestion_instance


def chunk_text(raw_text: str, default_source: str = "外部真题资料") -> List[QuestionChunk]:
    """模块级便捷函数：从原文本中智能分块切片出题目 (支持 Rust 加速与纯 Python 降级)"""
    return get_material_ingestion_pipeline().chunk_text(raw_text, default_source)


def _chunk_text_python(raw_text: str, default_source: str = "外部真题资料") -> List[QuestionChunk]:
    """模块级降级函数：纯 Python 题目切片实现"""
    return get_material_ingestion_pipeline()._chunk_text_python(raw_text, default_source)


def extract_text_from_pdf(pdf_path: Any, max_chars: int = 3500) -> str:
    """从真题或参考资料 PDF 中安全抽取纯文本内容（优雅降级处理缺失依赖与纯扫描件）"""
    path = Path(pdf_path)
    if not path.exists():
        return f"[未找到文件: {path.name}]"

    # 1. 优先尝试 tools.skills.pdf_extractor 模块化抽取
    try:
        try:
            from .pdf_extractor import extract_pdf_pages
        except (ImportError, ValueError):
            try:
                from tools.skills.pdf_extractor import extract_pdf_pages
            except ImportError:
                from skills.pdf_extractor import extract_pdf_pages
        res = extract_pdf_pages(path, max_pages=8)
        if res.get("success"):
            full = "\n".join(p.get("text", "") for p in res.get("pages", []) if p.get("text"))
            extracted = full.strip()
            if extracted:
                return extracted[:max_chars]
    except Exception:
        pass

    # 2. 备选方案：直接尝试 pypdf.PdfReader
    try:
        import pypdf
        reader = pypdf.PdfReader(str(path))
        texts = [p.extract_text() or "" for p in reader.pages[:8]]
        extracted = "\n".join(texts).strip()
        if extracted:
            return extracted[:max_chars]
    except Exception:
        pass

    # 3. 备选方案：直接尝试 PyMuPDF (fitz)
    try:
        import fitz
        doc = fitz.open(str(path))
        try:
            texts = [page.get_text() or "" for page in doc[:8]]
        finally:
            doc.close()
        extracted = "\n".join(texts).strip()
        if extracted:
            return extracted[:max_chars]
    except Exception:
        pass

    return f"[PDF 文件: {path.name}, 提取结果: 未能提取到文本（可能为纯扫描图片版）]"


