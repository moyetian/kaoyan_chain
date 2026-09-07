# -*- coding: utf-8 -*-
"""
KaoYan AI Study Chain · 试题与备考资料智能切片入库管道 (Material Ingestion Pipeline)

核心功能：
  1. 支持外部真题/资料 (Markdown, TXT, PDF) 的自动切片与题目分块 (Question Chunker)
  2. 智能识别题型：单选/多选 (choice)、填空题 (blank)、综合解答/大题 (essay)
  3. 提取分值与参考解答，自动构造步骤级采分点标注 (Rubric Parser, 如 [+2分]、[+4分])
  4. 自动标注考点标签与白名单题源认证，生成规范的 Markdown 题目卡片并入库归档
"""

import os
import re
import json
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class QuestionChunk:
    """切片题目数据模型"""
    number: int
    q_type: str                         # choice / blank / essay
    score: int                          # 题目分值
    stem: str                           # 题干
    options: List[str] = field(default_factory=list)      # 选项 [A. ..., B. ...]
    answer: str = ""                    # 参考答案
    analysis: str = ""                  # 试题解析
    rubric: List[str] = field(default_factory=list)        # 步骤采分点清单
    points: List[str] = field(default_factory=list)        # 涉及核心考点
    source: str = ""                    # 题源出处


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

    def __init__(self):
        pass

    def _resolve_subject_dir(self, subject: str) -> Path:
        """映射科目至工作区目录"""
        sub_clean = subject.strip().lower()
        folder_name = self.SUBJECT_MAP.get(sub_clean, "04-专业课")
        target = ROOT / folder_name
        if not target.exists():
            target = ROOT / "04-专业课"
        return target

    def chunk_text(self, raw_text: str, default_source: str = "外部真题资料") -> List[QuestionChunk]:
        """
        从原文本中智能分块切片出题目
        """
        chunks: List[QuestionChunk] = []
        if not raw_text or not raw_text.strip():
            return chunks

        text = raw_text.replace("\r\n", "\n").replace("\r", "\n")

        # 识别大题分段 (Sections, 如 一、单项选择题)
        sec_pattern = re.compile(
            r'^[#*\s]*(?:第?[一二三四五六七八九十]+[部分题大题]*[、\.\s]*)?(?P<sec_title>(?:单项?选择题|多项?选择题|选择题|填空题|解答题|综合题|综合应用题|计算题|证明题|算法题|简答题)[^\n]*)$',
            re.MULTILINE
        )
        sections = list(sec_pattern.finditer(text))

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

            num_val = int(m.group("num") or m.group("num2") or (i + 1))
            chunk = self._parse_single_block(block, num_val, default_source, sec_hint=sec_hint)
            if chunk.stem:
                chunks.append(chunk)

        return chunks

    def _parse_single_block(self, block: str, num: int, source: str, sec_hint: str = "") -> QuestionChunk:
        """解析单个题块"""
        # 剥离题块末尾可能粘连的下一个大题标题
        block = re.sub(r"\n+[#*\s]*(?:第?[一二三四五六七八九十]+[部分题大题]*[、\.\s]*)?(?:单项?选择题|多项?选择题|选择题|填空题|解答题|综合题|综合应用题|计算题|证明题|算法题)[^\n]*$", "", block).strip()

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
            r"^[#*\s]*(?:第?[一二三四五六七八九十]+[部分题大题]*[、\.\s]*)?(?:单项?选择题|多项?选择题|选择题|填空题|解答题|综合题|综合应用题|计算题|证明题|算法题)[^\n]*\n+",
            "",
            raw_stem
        ).strip()
        stem_clean = re.sub(r"^(?:\d+[\.、\s]+|[【\[](?:题|Q)?\d+[】\]])", "", stem_clean).strip()
        stem_clean = re.sub(r"^(?:（\d+分）|\(\d+分\)|\[\d+分\]|【\d+分】)\s*", "", stem_clean).strip()

        # 识别选项 (A. ... B. ... C. ... D. ...)
        options: List[str] = []
        opt_matches = list(re.finditer(r"(?:\n|^|\s+)([A-D])[\.、\s]+([^\n\rA-D]+)", stem_clean))
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

    def format_question_card(self, chunk: QuestionChunk, subject: str = "pro") -> str:
        """
        生成规范的考研白名单题目 Markdown 卡片
        """
        type_names = {"choice": "单项选择题", "blank": "填空题", "essay": "综合应用与解答题"}
        t_name = type_names.get(chunk.q_type, "综合题")
        # 根据题源特征诚实标记认证状态，杜绝非官方试卷伪造 VERIFIED
        src_lower = (chunk.source or "").lower()
        if any(kw in src_lower for kw in ("统考", "真题", "大纲", "官方", "教育部")):
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
        card_mds.append(f"> **入库时间**: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}` | **试题总量**: `{len(chunks)} 道` | **认证状态**: `[白名单已收录]`\n")
        card_mds.append("---\n")

        for c in chunks:
            card_mds.append(self.format_question_card(c, subject=subject))

        full_output = "\n".join(card_mds)
        target_path.write_text(full_output, encoding="utf-8")

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
            except Exception as e:
                return {"success": False, "msg": f"读取 PDF 异常: {e} (若未安装 pypdf 请运行 pip install pypdf)", "count": 0}
        else:
            raw_text = p.read_text(encoding="utf-8", errors="ignore")

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
