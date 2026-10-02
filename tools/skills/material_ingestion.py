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

try:  # 双导入路径兼容（源码脚本式 / tools 包式）
    from workspace import resolve_workspace_root
except ImportError:  # pragma: no cover
    from tools.workspace import resolve_workspace_root
from datetime import datetime

try:  # 双导入路径兼容（项目同时存在 tools.X 与 X 两种导入方式）
    from ky_io import atomic_write_text, read_text_fallback  # noqa: E402
except ImportError:  # pragma: no cover
    from tools.ky_io import atomic_write_text, read_text_fallback  # noqa: E402

try:  # [C3 题源溯源] 渲染侧与解析侧共用同一题干提取口径（见 question_source 模块）
    from skills.question_source import backfill_markdown_text  # noqa: E402
except ImportError:  # pragma: no cover
    from tools.skills.question_source import backfill_markdown_text  # noqa: E402

try:  # [P0 题库身份链] 整份入库材料的内容寻址身份
    from skills.paper_registry import PaperRegistry, paper_id_for_content
except ImportError:  # pragma: no cover
    from tools.skills.paper_registry import PaperRegistry, paper_id_for_content

ROOT = resolve_workspace_root(__file__)

# 尝试加载 Rust 极速题目切片扩展，失败时透明降级为纯 Python
try:
    import ky_rust_ext as _rust
    _HAS_RUST_EXT = True
except ImportError:
    _rust = None
    _HAS_RUST_EXT = False

#: 单次 ingest 的 LLM 采分点补全默认上限。
#: [P2 修复·LLM 预算控制] 旧实现逐题静默调用（每题超时 10s、无上限/无提示），
#: 三沙箱实测：17 题 → 3m04s、≈17 次 API 尝试且学员毫无感知。默认封顶 10 次，
#: 调用前显式提示次数，可用 --llm-budget=N 调整 / --no-llm 全部跳过。
DEFAULT_LLM_ENRICH_BUDGET = 10


@dataclass
class QuestionChunk:
    """切片题目数据模型"""
    number: int
    q_type: str                         # choice / blank / essay / term / short / discuss / analysis_calc ...
    score: float                        # 题目分值（[UT4 修复·INGEST-2] 支持小数，如 1.5）
    stem: str                           # 题干
    options: List[str] = field(default_factory=list)      # 选项 [A. ..., B. ...]
    answer: str = ""                    # 参考答案
    analysis: str = ""                  # 试题解析
    rubric: List[str] = field(default_factory=list)        # 步骤采分点清单
    points: List[str] = field(default_factory=list)        # 涉及核心考点
    source: str = ""                    # 题源出处
    subject: str = ""                    # 冻结的入库科目键
    subject_provenance: str = "user_specified"  # 科目来源：user_specified/filename/section


# 文科题型判定规则：(q_type, 关键词, 无显式分值时的默认分)
# [文科适配] 自命题文科卷（名词解释/简答/论述）此前全落进 essay → 统一按
# "综合应用与解答题（10 分）"入库。默认分仅为无分值标注时的回退，题面自带
# "（本题满分 X 分）/（X 分）"时以显式分值为准。
# [UT4 修复·INGEST-1] 追加理工科题型段（分析计算/综合/证明）：实测
# 「三、分析计算题（每题 15 分，共 45 分）」整段不匹配任何题型词，段内题目
# 静默继承上一分段（简答题 10 分）—— 15 分计算题被入库成 10 分简答题。
# 注意：裸「计算题」段不进关键词表 —— 「综合计算题」等组合段名被既有回归
# 钉住判文科阴性（走 essay 兜底），其分值由分段声明保证，仅展示名泛化。
_WENKE_TYPE_RULES = (
    ("term", ("名词解释",), 5),
    ("short", ("简答", "简述"), 10),
    ("discuss", ("论述", "材料分析", "辨析"), 15),
    ("analysis_calc", ("分析计算", "计算分析", "解答计算"), 10),
    ("comprehensive", ("综合题",), 10),
    ("proof", ("证明题",), 10),
)

_WENKE_TYPE_NAMES = {
    "term": "名词解释题",
    "short": "简答题",
    "discuss": "论述题",
    "analysis_calc": "分析计算题",
    "comprehensive": "综合题",
    "proof": "证明题",
}


# [UT4 修复·INGEST-6] 考点兜底词表：题面学科特征词 → 学科名（高置信、可扩展）。
# 实测白名单回忆版题卡「【考查考点】核心综合考点」九卡同文 —— 无任何信息量。
# 兜底优先级：LLM/题面关键词点 > 本表学科推断 > 通用占位（见 _fallback_points）。
# 只在题干**明确命中**特征词时使用（学科名直接来自题面词汇，不虚构考点）。
_SUBJECT_HINT_KEYWORDS = (
    ("生理学", ("静息电位", "动作电位", "微循环", "内环境", "肾素", "心动周期")),
    ("生物化学", ("糖酵解", "三羧酸循环", "限速酶", "核酸", "转录", "翻译")),
    ("病理学", ("炎细胞", "炎症", "坏死", "肿瘤", "变性")),
    ("内科学", ("高血压", "心力衰竭", "贫血", "糖尿病", "肺炎")),
    ("外科学", ("阑尾炎", "骨折", "疝", "肠梗阻")),
    ("信号与系统", ("傅里叶", "拉普拉斯", "卷积", "奈奎斯特", "频谱",
                    "冲激函数", "冲激响应", "采样", "抽样")),
    ("马克思主义基本原理", ("唯物辩证", "对立统一", "实践是检验真理", "真理的相对性")),
    ("数据结构", ("二叉树", "图的遍历", "快速排序", "哈夫曼")),
    ("操作系统", ("虚拟内存", "分页存储", "进程调度", "死锁")),
    ("计算机网络", ("三次握手", "TCP", "路由", "子网掩码")),
)


# [缺陷修复·题干混入 Markdown 标题片段] 源资料里章节标题常与上一题**同行粘连**
# （"4. 案例分析：…分析乙的行为性质。## 综合课（498）"），切片器把标题片段
# 并进上一题题干，污染随切片文件一路传导到组卷（实测组卷第 2 题题干带
# "## 综合课（498）"）。此处在**切片渲染的统一出口**清洗，Rust / Python 两条
# 路径的 chunks 都流经它，checksum 随干净题干重算，全链路自洽
# （不在 exam_composer 侧洗：那里的题干与卡片里已算好的题源校验和会不一致，
# 触发 source_tampered 后被防篡改闸门整批排除、题量缩水）。
# 只认连续 ≥2 个 #，避免误伤 "C#" / "#1" 这类正文。
_MD_HEADING_FRAGMENT = re.compile(r"#{2,}[^\n]*")


def _strip_md_heading(text: str) -> str:
    """剥离题干里混入的 Markdown 标题片段（连续 ≥2 个 ``#`` 起至行尾）。"""
    if not text or "#" not in text:
        return text
    out = []
    for line in str(text).split("\n"):
        cleaned = _MD_HEADING_FRAGMENT.sub("", line).rstrip()
        # 整行就是标题片段的，剥离后直接删行，不留空行
        if not cleaned.strip() and _MD_HEADING_FRAGMENT.search(line):
            continue
        out.append(cleaned)
    return "\n".join(out)


def _detect_wenke_type(hint_text: str):
    """从分段标题/题干首行判定文科题型，返回 (q_type, 默认分)，无命中返回 (None, 0)。"""
    hint = str(hint_text or "")
    for q_type, keywords, default_score in _WENKE_TYPE_RULES:
        if any(kw in hint for kw in keywords):
            return q_type, default_score
    return None, 0


#: 分段标题里「每题分值」的三种常见声明格式（按优先级尝试）。
#: 例：`（每题 8 分）` / `（5 × 8 = 40 分）` / `（8 分 × 5 题）`
#: [UT4 修复·INGEST-2] 分值支持小数：西医综合「每题 1.5 分」此前 `(\d+)`
#: 只能抓到 "1"，`\s*分` 跟不上 → 整条声明失效，回退逐题分数误读（1.5→5）。
_SEC_PER_Q_PATTERNS = (
    re.compile(r"每[题小空]{1,2}\s*(\d+(?:\.\d+)?)\s*分"),                  # 每题/每空/每小题 8 分（支持 1.5 分）
    re.compile(r"(\d+)\s*[×xX*＊]\s*(\d+(?:\.\d+)?)\s*=\s*(\d+(?:\.\d+)?)\s*分"),  # 5 题 × 8 分 = 40 分
    re.compile(r"(\d+(?:\.\d+)?)\s*分\s*[×xX*＊]\s*(\d+)\s*题"),            # 8 分 × 5 题
)

#: 分值格式化：整数分值去掉小数尾巴（1.0 → 1），小数原样保留（1.5 → 1.5）
def _fmt_score(val) -> str:
    try:
        f = float(val)
    except (TypeError, ValueError):
        return str(val)
    return str(int(f)) if f.is_integer() else str(f)


def _per_question_score_from_section(sec_title: str) -> float:
    """从分段标题提取「每题分值」声明；无声明返回 0。

    [P2 修复·满分字段不准] 试卷分段标题自带的分值声明（如
    `二、简答题（5 × 8 = 40 分）`）是**试卷自身的权威口径**：旧实现既不读它，
    还被 Rust 块边界把下一分段标题吸收进末题（`（5 × 8 = 40 分）` 的总分 40
    被当成末题满分，实测 5 道名词解释标成 10/10/10/10/40）。本函数只解析
    「每题分值」；仅有「共 40 分」这类总分声明的返回 0（无法推算单题分）。
    [UT4 修复·INGEST-2] 返回值放宽为 float（支持「每题 1.5 分」）。
    """
    title = str(sec_title or "")
    m = _SEC_PER_Q_PATTERNS[0].search(title)
    if m:
        return float(m.group(1))
    m = _SEC_PER_Q_PATTERNS[1].search(title)
    if m and abs(float(m.group(1)) * float(m.group(2)) - float(m.group(3))) < 1e-6:
        # `A × B = C` 按中文试卷惯例 = 题数 × 每题分 = 总分；乘积校验防误读
        return float(m.group(2))
    m = _SEC_PER_Q_PATTERNS[2].search(title)
    if m:
        return float(m.group(1))
    return 0


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
        """映射科目至工作区目录。

        * **未知科目键 → raise ValueError**（信息含支持的科目键列表）——
          旧实现静默回落到 04-专业课，会把数学/英语材料错档进专业课目录
          且毫无提示；
        * 已知科目但目录不存在 → 仍回落 04-专业课，并打印 ``[warn]`` 提示。
        """
        sub_clean = subject.strip().lower()
        if sub_clean not in self.SUBJECT_MAP:
            raise ValueError(
                f"未知科目: {subject!r}，无法定位入库目录；"
                f"支持的科目键: {', '.join(sorted(self.SUBJECT_MAP))}")
        target = self.workspace_root / self.SUBJECT_MAP[sub_clean]
        if not target.exists():
            print(f"  [warn] 目标目录不存在，已回落 04-专业课（subject={sub_clean}）")
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
        # [修复·单行紧凑选项] 先把同一行内的多个选项拆成每选项一行，再交给
        # Rust / Python 任一切片路径（两者都要求选项字母前是行首/空白）。
        text_for_regex = self._split_compact_options(text_for_regex)

        # [P1 修复·回忆版/答案区结构路由] Rust 加速器对「题目标记（**题目（回忆版）**）」
        # 与「答案区小节（答案要点/我的答案…）」的结构识别滞后于 Python 参考实现
        # （无标记切分、无答案区跳过：实测 ext 环境 wenke 样张 4 题切出 6 张、
        # 801 回忆版 4 题切出 17 张解答碎片）。命中这些结构时改走 Python 参考路径，
        # 与 _reclassify_by_sections 对 Rust 分段词表滞后的兜底同属「加速器可滞后、
        # 产物必须正确」原则。
        # [P2 修复·文科分段默认分双路径漂移] Rust 对文科题型（名词解释/简答/论述）
        # 只给通用默认分 10，Python 参考实现按题型给 5/10/15；分段声明的「每题 X 分」
        # 虽由 _reclassify_by_sections 统一覆盖，但**无声明**时两条路径产物仍漂移
        # （实测「材料分析题（共 20 分）」末题：Rust 10 vs Python 15）。命中文科
        # 分段标题即路由 Python，与上述原则同源。
        _py_only_structure = bool(
            self._QUESTION_MARKER_PATTERN.search(text_for_regex)
            or self._ANSWER_SEC_PATTERN.search(text_for_regex)
            # [UT4 修复·INGEST-4] B 型题「6-10. 备选答案」题组结构 Rust 不识别，
            # 命中即路由 Python 参考实现（与题目标记/答案区同原则）。
            or self._SHARED_OPT_HEAD_RE.search(text_for_regex)
            or any(_detect_wenke_type(m.group("sec_title"))[0]
                   for m in self._SEC_LINE_PATTERN.finditer(text_for_regex)))
        if getattr(self, "_force_python", False) or not _HAS_RUST_EXT or _py_only_structure:
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
        # [UT4 修复·INGEST-3] 尾部「## 参考答案」区按题号回填各题 standard_answer
        # （答案区文本由 _chunk_text_python 截断时暂存于 _last_answer_section）。
        self._apply_answer_section(chunks)
        # [UT4 修复·INGEST-5] 题号全局唯一化：不同大题分段各自从 1 编号时
        # （实测同一文档出现 3 个【题号 1】），引用与定位无法歧义消除 ——
        # 统一重编为全局递增。答案回填在上一步已按**原文题号**完成，不受影响。
        for _idx, _c in enumerate(chunks, 1):
            _c.number = _idx

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
        # [缺陷修复·题干混入 Markdown 标题片段] 统一出口清洗：Rust / Python
        # 两条路径产出的 chunk 都在这里过一遍，题干干净后 checksum 才自洽。
        for c in chunks:
            if c.stem:
                c.stem = _strip_md_heading(c.stem)
        return chunks

    # ── 单行紧凑选项规范化 ────────────────────────────────────────────────
    # [修复·单行紧凑选项丢选项] 试卷转录常把四个选项压在同一行
    # （`A. x  B. y  C. z  D. w`）。Rust 与 Python 两条切片路径的选项识别
    # 都要求「选项字母前是行首/空白」，上一选项内容会把与下一选项之间的
    # 空白一起吞掉，实测只留 A/C、丢 B/D，并传导进组卷后的试卷。
    # 处置：在进入切片前把这类行的选项边界规范化为换行（纯文本规范化，
    # 不改语义），使两条路径共用同一份正确输入；行内选项标记不足 2 个的
    # 普通行（含标准多行格式）原样保留，不受影响。
    _COMPACT_OPT_MARK = re.compile(r"(?:^|[ \t]|[）)])([A-D][\.、．)）])(?=\s*\S)")
    _COMPACT_OPT_BOUNDARY = re.compile(r"[ \t]+(?=[A-D][\.、．)）]\s*\S)")
    _COMPACT_OPT_TIGHT = re.compile(r"(?<=[）)])(?=[A-D][\.、．)）]\s*\S)")

    def _split_compact_options(self, text: str) -> str:
        """把「同一行多个选项」的行拆成每选项一行；其余行原样保留。"""
        if not text or ("B" not in text and "C" not in text and "D" not in text):
            return text
        lines = text.split("\n")
        changed = False
        for i, ln in enumerate(lines):
            if self._TABLE_ROW.match(ln):
                continue
            has_tight = bool(self._COMPACT_OPT_TIGHT.search(ln))
            if len(self._COMPACT_OPT_MARK.findall(ln)) < 2 and not has_tight:
                continue
            new_ln = self._COMPACT_OPT_BOUNDARY.sub("\n", ln)
            new_ln = self._COMPACT_OPT_TIGHT.sub("\n", new_ln)
            if new_ln != ln:
                lines[i] = new_ln
                changed = True
        return "\n".join(lines) if changed else text

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
    # [UT4 修复·INGEST-1] 「分析题」放宽为「分析(?:计算)?题」（覆盖「分析计算题」，
    # 该段名此前整段漏配）；追加「[ABX] 型题」（西医综合 A/B/X 型选择题分段）。
    _WENKE_SEC_ALTS = r"名词解释|论述题|材料分析题|辨析题"
    _SEC_LINE_PATTERN = re.compile(
        r'^[#*\s]*(?:第?[一二三四五六七八九十]+[部分题大题]*[、\.\s]*)?'
        r'(?P<sec_title>(?:单[项]?选择题|多[项]?选择题|不定项选择题|选择题|填空题|判断题|解答题|'
        r'综合(?:应用|计算|分析|论述)?题|计算(?:分析)?题|证明题|算法(?:设计)?题|简(?:答|述)题|分析(?:计算)?题|应用题|大题|'
        r'[ABX]\s*型题|'
        + _WENKE_SEC_ALTS + r')[^\n]*)$',
        re.MULTILINE
    )

    #: [UT4 修复·INGEST-1/2] 选择题型分段判定（单源）：「选择」族 + A/B/X 型题。
    _CHOICE_SEC_RE = re.compile(r"选择|[ABX]\s*型题")
    #: [UT4 修复·INGEST-4] B 型题「6-10. 备选答案：」题组头（共享备选项边界）。
    _SHARED_OPT_HEAD_RE = re.compile(
        r"^\s*(?P<lo>\d+)\s*[-–—~]\s*(?P<hi>\d+)\s*[\.、]\s*备选答案\s*[：:]?", re.MULTILINE)

    # ── 题目标记与答案区结构（P1 修复·回忆版真题）───────────────────────────
    # [P1 修复·回忆版真题解答步骤被当题号] 真题回忆文档以「**题目（回忆版）**：」
    # 标注题干、解答步骤以「1. 2. …」行首编号；旧逐题正则只认编号，实测 4 题被
    # 切成 17 张解答碎片卡（题干为「惯性矩 Iz = …」）。两类结构由 Python 参考
    # 实现识别：题目标记作为切分边界；答案区小节（含「答案要点」族）内的编号行
    # 一律不作为题目起点。Rust 加速器对这两类结构的识别滞后，分发器命中即路由
    # 到 Python（见 chunk_text）。
    _ANSWER_SEC_KEYS = ("我的答案", "参考答案", "答案解析", "试题解析", "答案与解析", "答案要点")
    #: 答案区小节必须形如「整行标题」：KEY（可带 ** / 冒号 / 单个括注如「（每题5分）」）。
    #: 内联写法（「**答案要点**：正文…」）不算小节 —— 否则其后的下一道编号题会被
    #: 误判为「答案区内的步骤」而整题丢失（实测 2 题只剩 1 题）。
    _ANSWER_SEC_PATTERN = re.compile(
        r'^[#*\s]*(?P<sec_title>[^\n\d]{0,8}?(?:'
        + "|".join(_ANSWER_SEC_KEYS) + r')'
        r'(?:\*\*)?\s*[：:]?\s*(?:[（(【\[][^\n）)】\]]{0,24}[）)】\]][：:]?)?\s*)$',
        re.MULTILINE
    )
    _QUESTION_MARKER_PATTERN = re.compile(
        r'^[#*\s]*(?:\*\*)?(?:题目|真题题目|题干|问题)(?:（回忆版）|\(回忆版\))?(?:\*\*)?\s*[：:]',
        re.MULTILINE
    )
    #: [UT4 修复·INGEST-3] **文档级**答案区（Markdown 标题形态，如
    #: 「## 参考答案（回忆版）」「### 我的答案」）。与 _ANSWER_SEC_PATTERN 的
    #: 区别：后者还会命中题块内联的「**回忆答案要点**：」标记（既有回归钉住
    #: 其内容必须保留在题块答案正文中），不能作为整段摘除边界 —— 只有
    #: 标题形态的答案区才是文档级结构，其内容按题号回填后从切片文本摘除。
    _DOC_ANSWER_SEC_RE = re.compile(
        r'^#{2,}[ \t]*[^\n]*?(?:' + "|".join(_ANSWER_SEC_KEYS) + r')[^\n]*$',
        re.MULTILINE
    )
    #: 答案区标记的共享正则（题块内切分 / 答案正文前缀清洗同源）
    _ANSWER_POINT_MARKER_RE = r"(?:\*\*)?(?:回忆|参考)?答案要点(?:\*\*)?\s*[：:]"
    #: 题块内「题干 | 答案/解析」的切分标记（_parse_single_block 与答案区跨度
    #: 判定共用，避免两处词表漂移）
    #: [UT4 修复·INGEST-4] 「答案[：:]」加负向断言排除「备选答案：」（B 型题组头
    #: 是选项区不是答案区，实测被误切进上一题 standard_answer 并编成 Rubric）；
    #: 「参考答案：」由更前的专属分支命中，不受断言影响。
    _ANSWER_SPLIT_MARKERS = (
        r"【答案】", r"【参考答案】", r"参考答案[：:]", r"【解】", r"解[：:]", r"(?<![参选])答案[：:]",
        r"【解析】", r"解析[：:]",
        _ANSWER_POINT_MARKER_RE,
    )
    _ANSWER_SPLIT_PATTERN = re.compile("(" + "|".join(_ANSWER_SPLIT_MARKERS) + ")")

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
        # [审计 2026-09-30 · PF-5] 逐字符 append 改为「列表推导 + 单条正则去空白」：
        # 1M 字符实测约 306ms/83MB → 约 1/3 时间、一半内存，索引语义逐字节不变。
        # 等价性依据：``re`` 的 ``\s`` 与 ``str.isspace()`` 在全部 Unicode 码点上
        # 逐点相同（CPython 两者同用 Py_UNICODE_ISSPACE；测试有逐码点断言）。
        norm_idx = [i for i, ch in enumerate(raw_text) if not ch.isspace()]
        norm_text = re.sub(r"\s+", "", raw_text)

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
            # [UT4 修复·INGEST-1/2] 选择题型分段判定走单源 _CHOICE_SEC_RE：
            # 覆盖「选择」族与 A/B/X 型题（西医综合实测 A/B/X 三段 9 题
            # 此前全被判为大题）。
            if self._CHOICE_SEC_RE.search(sec_title):
                c.q_type = "choice"
            elif "填空" in sec_title:
                c.q_type = "blank"
            else:
                _wt, _ws = _detect_wenke_type(sec_title)
                c.q_type = _wt if _wt else "essay"
                if _wt and not c.score:
                    c.score = _ws
            # [P2 修复·满分字段不准] 分段标题声明的「每题 X 分」优先于
            # 类型默认分与块内松散提取 —— Rust 块边界会把下一分段标题
            # （`（5 × 8 = 40 分）`）吸收进本段末题，其总分被误当满分
            # （实测末题标 40/50 分）；类型默认分也常与实际不符
            # （简答默认 10 / 实际每题 8）。试卷自身声明是权威口径，
            # 仅在标题明确给出「每题分值」时覆盖，不碰总分声明型分段。
            _sec_score = _per_question_score_from_section(sec_title)
            if _sec_score and c.score != _sec_score:
                c.score = _sec_score
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
        # [UT4 修复·INGEST-1] 词表与 _SEC_LINE_PATTERN 同步：分析(?:计算)?题 + A/B/X 型题
        sec_pattern = re.compile(
            r'^[#*\s]*(?:第?[一二三四五六七八九十]+[部分题大题]*[、\.\s]*)?(?P<sec_title>(?:单[项]?选择题|多[项]?选择题|不定项选择题|选择题|填空题|判断题|解答题|综合(?:应用|计算|分析|论述)?题|计算(?:分析)?题|证明题|算法(?:设计)?题|简(?:答|述)题|分析(?:计算)?题|应用题|大题|[ABX]\s*型题|名词解释|论述题|材料分析题|辨析题)[^\n]*)$',
            re.MULTILINE
        )

        # [UT4 修复·INGEST-3] 答案区整体摘除后再切片：
        # 旧实现答案区行不是题目边界，末题块一直延伸到文末，答案行碎片
        # （「B. 2. D 3. A …」）被当成选项混进题卡（实测题 12 选项被污染），
        # 且答案本体从未按题号归位。摘除后：题块不再吸收答案区；答案区文本
        # 暂存 _last_answer_section，由 chunk_text 主流程的 _apply_answer_section
        # 按**原文题号**回填各题 standard_answer。
        # 摘除终点：答案区之后若出现新的大题分段（混排文档「## 答案解析」在中部、
        # 其后仍有题目的形态），只截到该分段前，保留后续题目。
        self._last_answer_section = ""
        _m_ans = self._DOC_ANSWER_SEC_RE.search(text)
        if _m_ans:
            _tail = text[_m_ans.start():]
            _m_resume = sec_pattern.search(_tail)
            if _m_resume:
                self._last_answer_section = _tail[:_m_resume.start()]
                text = text[:_m_ans.start()] + _tail[_m_resume.start():]
            else:
                self._last_answer_section = _tail
                text = text[:_m_ans.start()]
        # [文科适配·答案区误切] "我的答案/参考答案"小节本身不是题型分段，其下编号行
        # 此前继承上一分段被当成新题切出（实测 12 题变 18 题）。将其识别为特殊分段，
        # 下游按 sec_hint 直接跳过。要求整行标题，避免误伤块内"【答案】"标记。
        # [P1 修复·答案要点族] 词表扩至「答案要点」（覆盖「回忆答案要点」等前缀
        # 写法），正则与跳过判定统一取类属性 _ANSWER_SEC_PATTERN / _ANSWER_SEC_KEYS。
        sections = sorted(
            list(sec_pattern.finditer(text)) + list(self._ANSWER_SEC_PATTERN.finditer(text)),
            key=lambda m: m.start())

        # 主题目编号行首严格匹配: 1. 或 1、 或 【1】 或 [题1] 等 (不误伤解答题内的 (1) 或 (2))
        # [UT4 修复·INGEST-4] 追加「N-M.」范围题号（B 型题「6-10. 备选答案：」题组头），
        # 使其成为切片边界 —— 此前题组头不被识别，整段被吸收进上一题块。
        q_pattern = re.compile(
            r'(?:^|\n)[ \t]*(?:(?P<num>\d+)[\.、][ \t]*'
            r'|(?P<num_range>\d+\s*[-–—~]\s*\d+)\s*[\.、][ \t]*'
            r'|[【\[](?:第|题|Q)?(?P<num2>\d+)[】\]题][ \t]*'
            # 网上复制的真题常用 ``**第1题（4分）**：``；旧正则只认
            # ``1.``/``[1]``，会把整份材料压成一张题卡并把下一题污染进
            # 上一题答案。把题号标签本身消费掉，题干从冒号后开始。
            r'|\*{0,2}第\s*(?P<num3>\d+)\s*题'
            r'(?:[（(][^\)\n]{0,20}[）)])?\s*\*{0,2}\s*'
            r'(?:[：:.、)]\s*)?)',
            re.MULTILINE
        )
        matches = list(q_pattern.finditer(text))
        marker_matches = list(self._QUESTION_MARKER_PATTERN.finditer(text))

        if not matches and not marker_matches:
            if len(text.strip()) > 20:
                c = self._parse_single_block(text.strip(), 1, default_source)
                chunks.append(c)
            return chunks

        # [P1 修复·回忆版真题解答步骤被当题号] 文档含「**题目（回忆版）**：」类标记
        # 时，标记即题目边界：落在标记块内的编号行（解答步骤）不再作为题目起点；
        # 标记块之外的编号行（混排文档）仍按原逻辑处理。
        # [文科适配·答案区误切] "我的答案/参考答案"小节里的编号行（如"1. 我的理解…"）
        # 此前会被当成新题切出（实测 12 题变 18 题，多出的 6 条来自答案区）。
        # [P1 修复·回忆版] 现在进一步：答案区内的编号行**既不是题目起点、也不作为
        # 上一题的块边界** —— 使解答步骤完整保留在上一题的答案正文中（此前被切断，
        # 上一题答案为空、步骤另成碎片）。例外：若该编号行在下一条编号行之前紧跟
        # 答案标记（如「2. 下一题\n**答案要点**：…」），则它是下一道题而非步骤 ——
        # 否则该题会被整题吞进上一题答案。
        numbered_entries: List[tuple] = []
        for _idx, m in enumerate(matches):
            _hint = ""
            for s in sections:
                if s.start() <= m.start():
                    _hint = s.group("sec_title")
            if any(k in _hint for k in self._ANSWER_SEC_KEYS):
                _next_pos = (matches[_idx + 1].start()
                             if (_idx + 1 < len(matches)) else len(text))
                if not self._ANSWER_SPLIT_PATTERN.search(text[m.end():_next_pos]):
                    continue
            numbered_entries.append((m.start(), m))

        # [P1 修复·回忆版真题解答步骤被当题号] 文档含「**题目（回忆版）**：」类标记
        # 时，标记即题目边界：落在标记块内的编号行（解答步骤）不再作为题目起点；
        # 标记块之外的编号行（混排文档）仍按原逻辑处理。
        entries: List[tuple] = [(mm.start(), None) for mm in marker_matches]
        if marker_matches:
            _marker_spans = [
                (mm.start(),
                 marker_matches[i + 1].start() if (i + 1 < len(marker_matches)) else len(text))
                for i, mm in enumerate(marker_matches)
            ]
            entries += [(p, m) for (p, m) in numbered_entries
                        if not any(s <= p < e for s, e in _marker_spans)]
        else:
            entries += numbered_entries
        entries.sort(key=lambda t: t[0])

        # [UT4 修复·INGEST-4] 待挂接的 B 型题组共享备选项：(sec_hint, options)
        pending_shared: tuple = ("", [])

        for i, (start_pos, qm) in enumerate(entries):
            end_pos = entries[i + 1][0] if (i + 1 < len(entries)) else len(text)
            block = text[start_pos:end_pos].strip()

            # 确定当前题目所在的大题分段
            if qm is None:
                # 题目标记块：取最近的非答案区小节作题型语境（最近小节可能是
                # 上一题的答案区，对题型判定是噪声）
                sec_hint = ""
                for s in sections:
                    if s.start() <= start_pos and not any(
                            k in s.group("sec_title") for k in self._ANSWER_SEC_KEYS):
                        sec_hint = s.group("sec_title")
                num_val = i + 1
            else:
                sec_hint = ""
                for s in sections:
                    if s.start() <= start_pos:
                        sec_hint = s.group("sec_title")
                num_val = int(qm.group("num") or qm.group("num2") or
                              qm.group("num3") or (i + 1))

            # [UT4 修复·INGEST-4] B 型题「6-10. 备选答案：」题组头：解析共享备选项
            # 后跳过（不生成题卡），并挂到同分段后续无自身选项的题目上。
            # 此前该段被「答案[：:]」误切进上一题的 standard_answer、备选项被
            # 编成上一题的 Rubric 步骤（实测题 5 被污染）。
            if self._SHARED_OPT_HEAD_RE.match(block):
                pending_shared = (sec_hint, self._extract_shared_options(block))
                continue
            if pending_shared[1] and sec_hint != pending_shared[0]:
                pending_shared = ("", [])

            chunk = self._parse_single_block(block, num_val, default_source, sec_hint=sec_hint)
            if pending_shared[1] and not chunk.options and sec_hint == pending_shared[0]:
                chunk.options = list(pending_shared[1])
            if chunk.stem:
                chunks.append(chunk)

        return chunks

    def _extract_shared_options(self, block: str) -> List[str]:
        """[UT4 修复·INGEST-4] 从「N-M. 备选答案」题组块提取 A-D 共享备选项。"""
        opts: List[str] = []
        for om in re.finditer(
                r"(?:\n|^|\s)([A-D])(?:\s*[\.、．)）]\s*|\s+)([^\n\r]+?)(?=\s+[A-D][\.、．)）]|\n|$)",
                block):
            opts.append(f"{om.group(1).strip()}. {om.group(2).strip()}")
        return opts

    def _apply_answer_section(self, chunks: List[QuestionChunk]) -> None:
        """[UT4 修复·INGEST-3] 把摘除的「## 参考答案」区按**原文题号**回填各题答案。

        支持「1. B  2. D  3. A」「11. ABD」等回忆版常见写法；答案区里的
        文字解析行不含「题号. 字母」形态，自动跳过。题号在切片结果里出现
        多次（跨题型重复编号）时映射有歧义，保守跳过不写入。
        """
        sec = getattr(self, "_last_answer_section", "") or ""
        if not sec or not chunks:
            return
        answers: Dict[int, str] = {}
        for m in re.finditer(r"(\d{1,3})\s*[\.、]\s*([A-Da-d]{1,8})(?![A-Za-z0-9])", sec):
            answers[int(m.group(1))] = m.group(2).upper()
        if not answers:
            return
        num_count: Dict[int, int] = {}
        for c in chunks:
            num_count[c.number] = num_count.get(c.number, 0) + 1
        for c in chunks:
            ans = answers.get(c.number)
            if ans and num_count.get(c.number, 0) == 1 and not (c.answer or "").strip():
                c.answer = ans

    def _parse_single_block(self, block: str, num: int, source: str, sec_hint: str = "") -> QuestionChunk:
        """解析单个题块"""
        # 剥离题块末尾可能粘连的下一个大题标题
        # [UT4 修复·INGEST-1] 剥离词表与分段词表同步（分析(?:计算)?题 + A/B/X 型题）
        block = re.sub(r"\n+[#*\s]*(?:第?[一二三四五六七八九十]+[部分题大题]*[、\.\s]*)?(?:单[项]?选择题|多[项]?选择题|不定项选择题|选择题|填空题|判断题|解答题|综合(?:应用|计算|分析|论述)?题|计算(?:分析)?题|证明题|算法(?:设计)?题|简(?:答|述)题|分析(?:计算)?题|应用题|大题|[ABX]\s*型题|名词解释|论述题|材料分析题|辨析题)[^\n]*$", "", block).strip()
        # [P1 修复·回忆版] 块尾若粘连下一个大节标题（如「## 真题回忆 2：…」），
        # 同样剥离，避免混入上一题答案正文（连续 ≥2 个 # 才算标题，单 # 是正文）。
        block = re.sub(r"(?:\n+#{2,}\s[^\n]*)+$", "", block).strip()

        # 提取分值：如 (本题满分 10 分) / (12分) / [5分]
        # [UT4 修复·INGEST-2] 支持小数（「（1.5 分）」此前 `(\d+)分` 只抓到 "5"，
        # 实测 1.5 分题被标成满分 5 分）
        score = 0.0
        m_score = re.search(r"(?:本题满分|满分|分值|共)?\s*(\d+(?:\.\d+)?)\s*分", block)
        if m_score:
            score = float(m_score.group(1))

        # 分割【题干】与【答案/解析】
        answer = ""
        analysis = ""
        rubric_list: List[str] = []

        # 常见切分标记（类常量单源：见 _ANSWER_SPLIT_MARKERS）
        split_pat = self._ANSWER_SPLIT_PATTERN
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
                answer = re.sub(
                    r"^(?:【答案】|【参考答案】|参考答案[：:]|【解】|解[：:]|答案[：:]|"
                    + self._ANSWER_POINT_MARKER_RE + r")\s*", "", m_ana[0]).strip()
                analysis = m_ana[1].strip()
            else:
                answer = ans_and_ana.strip()
                # [P1 修复·回忆版] 答案区标记前缀不进入答案正文
                answer = re.sub("^" + self._ANSWER_POINT_MARKER_RE + r"\s*", "", answer).strip()

        # 仅当首行包含明确的大题分段词时才剔除大题段标题
        # [UT4 修复·INGEST-1] 剥离词表与分段词表同步
        stem_clean = re.sub(
            r"^[#*\s]*(?:第?[一二三四五六七八九十]+[部分题大题]*[、\.\s]*)?(?:单[项]?选择题|多[项]?选择题|不定项选择题|选择题|填空题|判断题|解答题|综合(?:应用|计算|分析|论述)?题|计算(?:分析)?题|证明题|算法(?:设计)?题|简(?:答|述)题|分析(?:计算)?题|应用题|大题|[ABX]\s*型题)[^\n]*\n+",
            "",
            raw_stem
        ).strip()
        stem_clean = re.sub(r"^(?:\d+[\.、\s]+|[【\[](?:题|Q)?\d+[】\]])", "", stem_clean).strip()
        # [UT4 修复·INGEST-2] 分值前缀剥离支持小数（「（1.5 分）」）
        stem_clean = re.sub(r"^(?:（\d+(?:\.\d+)?\s*分）|\(\d+(?:\.\d+)?\s*分\)|\[\d+(?:\.\d+)?\s*分\]|【\d+(?:\.\d+)?\s*分】)\s*", "", stem_clean).strip()
        # [P1 修复·回忆版] 剥离题目标记前缀（**题目（回忆版）**：/ 题干：/ 问题：）
        stem_clean = re.sub(
            r"^[#*\s]*(?:\*\*)?(?:题目|真题题目|题干|问题)(?:（回忆版）|\(回忆版\))?(?:\*\*)?\s*[：:]\s*",
            "", stem_clean
        ).strip()

        # 识别选项 (A. ... B. ... C. ... D. ...)
        # [修复·单行紧凑选项丢选项] 旧正则内容段 `([^\n\rA-D]+)` 贪婪吞掉选项
        # 间空白，导致「A. x  B. y  C. z  D. w」单行格式只能匹配到 A/C
        # （B/D 前已无空白可供前缀消费），实测 4 选项切片后只剩 2 项并传导进试卷。
        # 改为「内容 + 前瞻边界」分割：内容不跨行，遇到下一个「空白 + 选项字母 +
        # 标点」边界即停；多行格式（每选项一行）行为不变。
        options: List[str] = []
        opt_matches = list(re.finditer(
            r"(?:\n|^|\s)([A-D])(?:\s*[\.、．)）]\s*|\s+)([^\n\r]+?)(?=\s+[A-D][\.、．)）]|\n|$)",
            stem_clean))
        is_essay_sec = any(k in sec_hint for k in ["综合", "解答", "计算", "应用", "简答", "证明", "设计", "算法"])
        # [UT4 修复·INGEST-1/2] 选择题型分段判定走单源 _CHOICE_SEC_RE（含 A/B/X 型题）
        is_choice_sec = bool(self._CHOICE_SEC_RE.search(sec_hint))
        q_type: Optional[str] = None

        if len(opt_matches) >= 2 or (is_choice_sec and len(opt_matches) >= 1):
            q_type = "choice"
            if not score:
                score = 2 if is_choice_sec else (5 if "408" in source or "专业" in source else 2)
            for om in opt_matches:
                opt_letter = om.group(1).strip()
                opt_content = om.group(2).strip()
                options.append(f"{opt_letter}. {opt_content}")
            # 提取题干主体（去掉末尾选项行）
            if opt_matches:
                first_opt_idx = opt_matches[0].start()
                stem_clean = stem_clean[:first_opt_idx].strip()
        elif is_choice_sec:
            # [UT4 修复·INGEST-4] B 型题题干无自身选项（共享备选项在「6-10. 备选答案」
            # 题组头，由外层挂接），按题型分段直接定 choice，不再落到大题兜底
            q_type = "choice"
            if not score:
                score = 2
        # [文科适配] 先判文科题型（分段标题优先，题干首行兜底），再走理科 essay 逻辑
        # [UT4 修复·INGEST-1] 题型分段声明优先：分段已是 choice/essay 等明确题型时，
        # 题干首行关键词（「简述」等）不再覆盖 —— 与 _reclassify_by_sections 的
        # 「试卷自身声明是权威口径」同源
        _wenke_hint = f"{sec_hint}\n{stem_clean.splitlines()[0] if stem_clean else ''}"
        _wenke_type, _wenke_score = _detect_wenke_type(_wenke_hint)
        if q_type is None and _wenke_type:
            q_type = _wenke_type
            if not score:
                score = _wenke_score
        elif q_type is None and is_essay_sec:
            q_type = "essay"
            if not score:
                score = 10 if "408" in source or "专业" in source else 10
        elif q_type is None and ("____" in stem_clean or "填空" in block or "填空" in sec_hint or re.search(r"（\s*）|\(\s*\)", stem_clean)):
            q_type = "blank"
            if not score:
                score = 5
        elif q_type is None:
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
                            # [UT4 修复·INGEST-2] 分值可为小数：整数分值去掉
                            # 「1.0」式小数尾巴再进 Rubric 文本
                            rubric_list.append(f"[+{_fmt_score(this_score)}分] 步骤{s_idx}：{sl[:60]}...")
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

    def _llm_configured(self) -> bool:
        """当前工作区是否已配置可用的 LLM（探测失败一律按未配置处理）。"""
        try:
            try:
                from tools.llm_client import is_llm_configured
            except ImportError:
                from llm_client import is_llm_configured
            return bool(is_llm_configured(workspace_root=self.workspace_root))
        except Exception:
            return False

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

    def _fallback_points(self, chunk: QuestionChunk) -> List[str]:
        """[UT4 修复·INGEST-6] 考点字段兜底：LLM/题面关键词均未命中时，
        按学科特征词表从题干推断；无法判定才回落通用占位。只影响展示，
        不写入 chunk.points（避免占住 LLM 补全的判定槽）。"""
        if chunk.points:
            return list(chunk.points)
        stem = chunk.stem or ""
        for subject, kws in _SUBJECT_HINT_KEYWORDS:
            if any(kw in stem for kw in kws):
                return [subject]
        return ["核心综合考点"]

    def format_question_card(self, chunk: QuestionChunk, subject: str = "pro",
                             *, llm_enrich: Optional[bool] = None) -> str:
        """
        生成规范的考研白名单题目 Markdown 卡片

        ``llm_enrich``：None（默认，兼容既有直接调用方）= 缺采分点时逐卡触发
        LLM 补全；False = 关闭逐卡触发（ingest 批量路径专用：补全已由
        :meth:`ingest_text` 按预算统一执行，此处必须关闸防止超预算重试）。
        """
        if (llm_enrich is None or llm_enrich) \
                and chunk.score >= 5 and (not chunk.rubric or not chunk.points):
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

        # [UT4 修复·INGEST-6] 考点展示兜底：points 为空时按题面学科关键词推断
        # （不占 LLM 补全槽 —— 兜底只影响展示，enrich 仍按 points 为空判定），
        # 无法判定才用通用占位。
        points_str = "、".join(self._fallback_points(chunk))

        # [K3] 元信息区补充「科目归属」标记：科目键 → 归档目录（未知键原样展示），
        # 便于人工核对入库位置与下游按科目筛查；不参与题干/题源身份校验。
        _subj_key = (chunk.subject or subject or "").strip().lower()
        _subj_label = self.SUBJECT_MAP.get(_subj_key, subject)
        lines = [
            f"### 【题号 {chunk.number}】{t_name}（满分: {_fmt_score(chunk.score)} 分）",
            f"- **【题源出处】**：`{chunk.source or '外部导入题库'}`",
            f"- **【考查考点】**：`{points_str}`",
            f"- **【白名单认证】**：{auth_status}",
            f"- **【科目归属】**：`{_subj_label}`（来源: {chunk.subject_provenance or 'unknown'}）",
            "",
            "#### 1. 试题原题",
            # [缺陷修复·题干混入 Markdown 标题片段] 渲染出口再兜一层，
            # 使绕过 chunk_text 直接构造的 chunk 也不会把标题片段写进卡片
            _strip_md_heading(chunk.stem),
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

        card_text = "\n".join(lines)
        # [C3 题源溯源] 注入「题源ID + 题源校验和」两行。题干提取走
        # question_source 的共享函数（与 exam_composer 解析侧**同一正则**）——
        # 两处若各自实现，渲染侧算出的 checksum 必然在解析侧校验失败。
        # 注：来源认证戳（[VERIFIED] / [USER_IMPORTED]）由上方各行承担，
        # 解析侧从卡片文本读回，本注入只负责身份两行。
        card_text, _ = backfill_markdown_text(card_text, kind="whitelist")
        return card_text

    def ingest_text(
        self,
        text_content: str,
        subject: str = "pro",
        source_name: str = "导入真题集",
        target_path: Optional[Path] = None,
        llm_enrich: Optional[bool] = None,
        llm_budget: Optional[int] = None,
        source_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """
        将纯文本/Markdown 题目批量切片并入库落盘

        ``llm_enrich=False`` 关闭 LLM 采分点补全（``ky ingest --no-llm``）；
        ``llm_budget`` 为本次最多补全的题数（None=默认 ``DEFAULT_LLM_ENRICH_BUDGET``，
        负数=不限）。补全前会打印候选题数与本次上限，避免静默产生大量 API 调用。
        """
        chunks = self.chunk_text(text_content, default_source=source_name)
        if not chunks:
            return {
                "success": False,
                "msg": "未能从输入文本中切分出有效题目，请检查题号格式 (如 1. / 2.)",
                "count": 0
            }

        try:
            sub_dir = self._resolve_subject_dir(subject)
        except ValueError as e:
            # [K3] 未知科目：转为可读失败结果（CLI / REPL / GUI / Agent 工具
            # 四个入口都消费本函数的 msg 字段，不会裸崩）
            return {"success": False, "msg": str(e), "count": 0}

        # [K9] 科目在入口一次确定并冻结；后续分段重分类只处理题型/顺序，
        # 不允许把题卡迁移到另一科目。保留来源字段，便于人工审计入库结果。
        subject_key = str(subject or "").strip().lower()
        for _chunk in chunks:
            _chunk.subject = subject_key
            _chunk.subject_provenance = "user_specified"
        ref_dir = sub_dir / "参考资料"
        ref_dir.mkdir(parents=True, exist_ok=True)

        paper_id = paper_id_for_content(text_content)
        registry = PaperRegistry(self.workspace_root)

        safe_src = re.sub(r'[\\/:*?"<>|]+', '_', source_name)
        now_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
        caller_supplied_target = target_path is not None
        if not target_path:
            target_path = ref_dir / f"题库切片_{safe_src}_{now_tag}.md"
        else:
            target_path = Path(target_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)

        card_mds = []
        card_mds.append(f"# 📚 考研白名单题库切片集 · {source_name}\n")
        card_mds.append(f"<!-- SOURCE_PAPER_ID: {paper_id} -->\n")
        # [缺陷修复·措辞过强] 此前标题栏一律宣称「认证状态: [白名单已收录]」，
        # 容易被读成"已核验的权威题源"。实际含义只是"文件已放进本地参考资料目录"。
        # 改为如实描述，并把逐题核验状态交由每题卡片的【白名单认证】字段呈现。
        card_mds.append(
            f"> **入库时间**: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}` | "
            f"**试题总量**: `{len(chunks)} 道` | **收录状态**: `[已入本地资料库 · 逐题核验状态见下]`\n")
        card_mds.append("---\n")

        # [P2 修复·LLM 预算控制] 旧实现由 format_question_card 逐卡静默触发
        # LLM 补采分点：无上限/无提示/无确认（三沙箱实测 17 题 → 3m04s、
        # ≈17 次 API 尝试）。现在把补全从渲染路径上移到这里统一执行：
        # 先统计候选题数 → 打印次数与本次上限 → 按预算逐题补全 →
        # 渲染阶段传 llm_enrich=False 关掉逐卡触发，保证一次 ingest 的
        # 调用次数严格 ≤ 预算（补全失败的题不会在渲染时被无界重试）。
        if llm_enrich is None:
            llm_enrich = True
        _budget = DEFAULT_LLM_ENRICH_BUDGET if llm_budget is None else int(llm_budget)
        _candidates = [c for c in chunks
                       if c.score >= 5 and (not c.rubric or not c.points)]
        _llm_attempted = 0
        if _candidates:
            if not llm_enrich:
                print(f"  [i] {len(_candidates)} 道题缺少步骤采分点；已按 --no-llm 跳过 LLM 补全。")
            elif not self._llm_configured():
                print(f"  [i] {len(_candidates)} 道题缺少步骤采分点；未配置 LLM API，跳过补全。")
            elif _budget == 0:
                print(f"  [i] {len(_candidates)} 道题缺少步骤采分点；--llm-budget=0，跳过补全。")
            else:
                _cap = len(_candidates) if _budget < 0 else min(len(_candidates), _budget)
                _tail = (f"；其余 {len(_candidates) - _cap} 道未补全（可用 --llm-budget=N 调整上限）"
                         if len(_candidates) > _cap else "")
                print(f"  [i] {len(_candidates)} 道题缺少步骤采分点，本次补全前 {_cap} 道"
                      f"（每题最长约 10s，按 API 调用计费）{_tail}...")
                for _c in _candidates[:_cap]:
                    self.enrich_rubric_with_llm(_c, subject=subject)
                    _llm_attempted += 1

        for c in chunks:
            card_mds.append(self.format_question_card(c, subject=subject, llm_enrich=False))

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

        if not caller_supplied_target or not Path(target_path).exists():
            _fp_new = _ingest_fingerprint(full_output)
            for _cand in sorted(ref_dir.glob(f"题库切片_{safe_src}_*.md")):
                try:
                    if _ingest_fingerprint(_cand.read_text(encoding="utf-8", errors="ignore")) == _fp_new:
                        registry_record = registry.register_paper(
                            text_content,
                            subject=subject_key,
                            source_name=source_name,
                            source_path=source_path or "",
                            card_path=_cand,
                        )
                        return {
                            "success": True,
                            "paper_id": paper_id,
                            "count": len(chunks),
                            "choices": sum(1 for c in chunks if c.q_type == "choice"),
                            "blanks": sum(1 for c in chunks if c.q_type == "blank"),
                            # [P2 修复·大题统计漏计] 大题口径 = 选择/填空之外的全部主观题
                            # （文科卷含名词解释/简答/论述；旧实现只数 q_type=="essay"，
                            # 实测 14 题文卷只报「大题: 2」，三项相加与总数对不上）。
                            "essays": sum(1 for c in chunks if c.q_type not in ("choice", "blank")),
                            "target_path": str(_cand),
                            "chunks": chunks,
                            "reused_existing": True,
                            "paper_record": registry_record,
                            "summary": f"题源《{source_name}》内容与既有切片一致，已复用 {_cand.name}（未重复入库）"
                        }
                except Exception:
                    continue

        atomic_write_text(target_path, full_output)

        registry_record = registry.register_paper(
            text_content,
            subject=subject_key,
            source_name=source_name,
            source_path=source_path or "",
            card_path=target_path,
        )

        choices = sum(1 for c in chunks if c.q_type == "choice")
        blanks = sum(1 for c in chunks if c.q_type == "blank")
        # [P2 修复·大题统计漏计] 大题口径 = 选择/填空之外的全部主观题（与复用分支同源）
        essays = sum(1 for c in chunks if c.q_type not in ("choice", "blank"))

        return {
            "success": True,
            "paper_id": paper_id,
            "paper_record": registry_record,
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
        target_path: Optional[Path] = None,
        llm_enrich: Optional[bool] = None,
        llm_budget: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        读取文件并执行入库切片（``llm_enrich`` / ``llm_budget`` 透传
        :meth:`ingest_text` 的 LLM 预算控制，见其说明）
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
            target_path=target_path,
            llm_enrich=llm_enrich,
            llm_budget=llm_budget,
            source_path=p,
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


def health_check() -> dict:
    """[B4] 结构化健康自检：``{"status": READY/DEGRADED/UNAVAILABLE, "reason": str}``。

    切片入库的核心输入是 PDF 试题：pypdf 缺失时仍能处理 Markdown/TXT 文本，
    因此是 DEGRADED 而不是 UNAVAILABLE（旧硬编码"已就绪"会让用户拿着 PDF
    进来才发现提不出文本）。Rust 加速扩展缺失只影响性能、不影响功能，不降档。
    只做 find_spec 轻量探测，不真正 import pypdf。
    """
    def _has(mod: str) -> bool:
        try:
            import importlib.util
            return importlib.util.find_spec(mod) is not None
        except Exception:
            return False

    if _has("pypdf"):
        return {"status": "READY",
                "reason": "pypdf 可用，支持 PDF/Markdown/TXT 试题切片入库"
                          + ("（含 Rust 加速）" if _HAS_RUST_EXT else "（纯 Python 切片）")}
    return {"status": "DEGRADED",
            "reason": "未安装 pypdf，PDF 试题无法解析；当前仅支持 Markdown/TXT 文本切片（pip install pypdf 解锁）"}


