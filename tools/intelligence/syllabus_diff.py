# -*- coding: utf-8 -*-
"""
KaoYan Intelligence · 官方大纲与自命题考点版本比对引擎 (Syllabus Diff Generator)

核心功能：
  1. 支持两份官方大纲（如 2026 vs 2027 考纲、旧大纲 vs 新简章）的逐级 AST 结构化解析
  2. 精确比对考点增删与考查级别变更：
     - [+] ADDED: 新增考点（红色高危预警，重点攻坚，必须补充真题与变式训练）
     - [-] REMOVED: 删减考点（绿色减负提示，彻底划掉，避免无谓时间消耗）
     - [~] MODIFIED: 考查要求调整（如从“了解”上升为“掌握”，或描述细化）
  3. 量化考纲变动率与稳定性指数 (Volatility Index)
  4. 自动生成考纲异动深度研报 (Markdown / 终端高亮展示)，指导后续派题与错题攻坚
"""

import re
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Tuple, Set
from pathlib import Path

try:  # 双导入路径兼容（源码脚本式 / tools 包式）
    from workspace import resolve_workspace_root
except ImportError:  # pragma: no cover
    from tools.workspace import resolve_workspace_root
from datetime import datetime

try:  # 双导入路径兼容（项目同时存在 tools.X 与 X 两种导入方式）
    from ky_io import atomic_write_text  # noqa: E402
except ImportError:  # pragma: no cover
    from tools.ky_io import atomic_write_text  # noqa: E402

ROOT = resolve_workspace_root(__file__)

#: [W12 P1-5] 科目目录 → 显示名映射（公共课考纲与具体学校无关，命名标「全国统考」）
_SUBJECT_DIR_LABELS = {
    "01-数学": "数学",
    "02-英语": "英语",
    "03-思想政治理论": "思想政治理论",
    "04-专业课": "专业课",
}


def infer_diff_naming(old_path=None, new_path=None):
    """[W12 P1-5] 按「名随实」从考纲路径推导报告命名。

    此前 TUI/CLI 做 Diff 时命名恒取全局 config 志愿 —— 对英语考纲比对却落盘
    「考纲变动分析_目标院校_目标专业 (专业代码)...」（实测张冠李戴）。本函数按
    old/new 路径所属科目目录推导：

    - 公共课目录（01-数学 / 02-英语 / 03-思想政治理论）→ ``("全国统考", 科目名)``
    - 专业课目录（04-专业课）→ ``(None, None)``（专业课与志愿绑定，交调用方回退 config）
    - 路径不含科目目录 → ``(None, None)``

    Returns: ``(school, major)``；推导不出时为 ``(None, None)``，
    调用方按「显式参数 > 本函数推导 > config 回退」的优先级消费。
    """
    for p in (new_path, old_path):
        if not p:
            continue
        try:
            parts = Path(p).parts
        except (TypeError, ValueError):
            continue
        for seg in parts:
            if seg == "04-专业课":
                return None, None
            subj = _SUBJECT_DIR_LABELS.get(seg)
            if subj:
                return "全国统考", subj
    return None, None


@dataclass
class SyllabusPoint:
    """原子考点对象"""
    module: str             # 所属模块 (如: 高等数学 / 数据结构)
    chapter: str            # 所属章节 (如: 1. 函数、极限、连续)
    requirement: str        # 考查要求 (掌握 / 理解 / 了解 / 熟练应用 / 灵活运用)
    text: str               # 考点具体文本
    raw_line: str = ""      # 原始大纲文本行


@dataclass
class DiffItem:
    """考点变动条目"""
    change_type: str        # ADDED (+), REMOVED (-), MODIFIED (~), UNCHANGED (=)
    point_new: Optional[SyllabusPoint] = None
    point_old: Optional[SyllabusPoint] = None
    detail: str = ""        # 变更说明 (如: 考查要求由 [了解] 提高为 [掌握])


#: [P1 修复·2026-10-08 K1 非登记要求词] 括号配对表：切分考点时必须跳过这些配对符号
#: **内部**的分隔符。实测 math1 大纲 148 点中 24 个断片全部源于在
#: 「（零点定理、介值定理、最值定理）」这类括号内按「、」切碎。
_BRACKET_PAIRS = {
    "（": "）", "(": ")", "【": "】", "[": "]", "｛": "｝", "{": "}",
    "《": "》", "「": "」", "『": "』",
}
_BRACKET_CLOSERS = set(_BRACKET_PAIRS.values())

#: [P1 修复·2026-10-08 K1 非登记要求词] 要求词前缀（未登记但形态明确的要求表述，如
#: 「熟悉」）。命中者不得被静默记成「掌握」——如实把词本身作为考查要求；
#: 未命中的冒号头（如「词汇基准」「进程与线程」）是考点名，维持既有行为。
_REQ_LIKE_PREFIXES = ("熟练", "灵活", "熟悉", "运用", "会用")


def _split_outside_brackets(text: str, separators: str) -> List[str]:
    """按 ``separators`` 逐字符切分，但跳过括号配对内部的分隔符（K1 修复）。

    修复前 ``re.split(r"[、,，]+", ...)`` 无括号感知：``闭区间上连续函数的性质
    （零点定理、介值定理、最值定理）`` 被切成 3 个断片（含未配对括号），
    math1 实测 24 个断片。本函数只在一层未闭合括号之外切分；未配对的左括号
    保守地把其后内容视为括号内（宁可不切，不产出断片），孤立的右括号忽略。
    """
    parts: List[str] = []
    buf: List[str] = []
    depth = 0
    for ch in text:
        if ch in _BRACKET_PAIRS:
            depth += 1
        elif ch in _BRACKET_CLOSERS and depth > 0:
            depth -= 1
        if ch in separators and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


class SyllabusDiffGenerator:
    """大纲考点版本比对引擎"""

    # 考查级别重要性排序
    # [P1 修复·2026-10-08 K1 非登记要求词] 补登项目内置大纲实际使用的两个要求词：
    # 「应用能力」（math1/math2）与「熟练计算」（math2）此前未登记，
    # 其整行被 m_plain 分支记成「掌握」且不切分（实测 math1 两处）。
    # 要求词识别一律以本表为单一真源（含下方正则片段的构造）。
    REQUIREMENT_LEVELS = {
        "掌握": 3,
        "熟练应用": 3,
        "熟练掌握": 3,
        "熟练求解": 3,
        "熟练计算": 3,
        "应用能力": 3,
        "灵活运用": 3,
        "理解": 2,
        "了解": 1,
        "会": 2,
        "能": 2,
    }

    #: [P1 修复·2026-10-08 K1] 要求词正则片段（单一真源 = REQUIREMENT_LEVELS 键）。
    #: 按长度降序排列，避免「掌握」先于「熟练掌握」命中的部分匹配。
    _REQ_ALT = "|".join(
        re.escape(_w) for _w in sorted(REQUIREMENT_LEVELS, key=len, reverse=True))

    def __init__(self):
        pass

    def clean_text(self, text: str) -> str:
        """清洗文本，统一标点与空白，剥离 Markdown 强调符与行尾掌握等级标签"""
        if not text:
            return ""
        t = text.strip()
        t = re.sub(r"\s+", " ", t)
        t = t.replace(";", "；").replace(",", "，")
        # 剥离 Markdown 强调/代码符号
        t = t.replace("**", "").replace("`", "")
        # 剥离行尾掌握等级标签（如「…… [掌握]」），等级由 requirement 字段承载
        # [P1 修复·2026-10-08 K1] 等级词集合改为登记表驱动（新增「应用能力/熟练计算」自动生效）
        t = re.sub(rf"\s*[\[【]({self._REQ_ALT})[\]】]\s*$", "", t)
        return t.strip()

    def _append_atomic_points(self, points: List[SyllabusPoint], module: str,
                              chapter: str, requirement: str, body: str,
                              raw_line: str) -> None:
        """把一条要求词行的正文切分为原子考点并追加（m_point / 非登记要求词共用）。

        [P1 修复·2026-10-08 K1 断片] 切分改用 :func:`_split_outside_brackets`：只在括号
        配对**之外**按「；;。」与「、,，」切分。修复前在「（零点定理、介值定理、
        最值定理）」括号内切碎，math1 实测 148 点中 24 个断片。
        """
        for sub in _split_outside_brackets(body, "；;。"):
            sub = sub.strip()
            if not sub:
                continue
            # [说明文字误切] 修订说明不是考点
            _sub_head = re.split(r"[：:]", sub, maxsplit=1)[0].strip()
            if _sub_head in ("新增", "剔除", "调整", "变动", "修订", "删除", "变化"):
                continue
            if sub in ("无", "无。"):
                continue
            terms = _split_outside_brackets(sub, "、,，")
            if len(terms) <= 1:
                points.append(SyllabusPoint(
                    module=module,
                    chapter=chapter,
                    requirement=requirement,
                    text=self.clean_text(sub),
                    raw_line=raw_line
                ))
            else:
                for term in terms:
                    term_clean = self.clean_text(term)
                    if len(term_clean) >= 2:
                        points.append(SyllabusPoint(
                            module=module,
                            chapter=chapter,
                            requirement=requirement,
                            text=term_clean,
                            raw_line=raw_line
                        ))

    def parse_syllabus(self, content: str) -> List[SyllabusPoint]:
        """
        将大纲 Markdown 文本解析为原子考点列表
        """
        points: List[SyllabusPoint] = []
        current_module = "核心考点"
        current_chapter = "未分类章节"
        # 负面清单章节（「绝不超纲」「不考 XXX」）不是正式考点，整段跳过
        skip_section = False
        # [P0-10] 表格行状态：上一行是否为表格行（表格首行=表头，分隔行后为数据区）
        _prev_in_table = False

        lines = content.splitlines()
        for line in lines:
            line_str = line.strip()
            if not line_str:
                _prev_in_table = False
                continue
            _is_table_line = line_str.startswith("|") and line_str.count("|") >= 3
            _was_in_table = _prev_in_table
            _prev_in_table = _is_table_line

            # 匹配一级/二级模块标题: ## 一、高等数学 或 ## 线性代数
            m_module = re.match(r"^#{1,2}\s+(?:[一二三四五六七八九十]+[、\.\s]*)?([^#]+)$", line_str)
            if m_module and not line_str.startswith("###"):
                candidate = m_module.group(1).strip()
                # 负面清单 / 提示性标题：进入跳过模式，直到下一个有效标题为止
                # [说明文字误切] "三、2027届调整"这类考纲修订说明小节不是知识章节，
                # 其下"新增：…/剔除：无。"会被误切成考点，故一并跳过。
                if any(k in candidate for k in ["说明", "红线", "准则", "背景", "范围", "不考", "超纲", "参考书目",
                                                "调整", "新增", "剔除", "变动", "修订", "变化说明"]):
                    skip_section = True
                    continue
                skip_section = False
                current_module = re.sub(r"\(.*?\)|（.*?）", "", candidate).strip()
                current_chapter = "未分类章节"
                continue

            # 匹配三级/四级章节标题: ### 1. 函数、极限、连续
            m_chap = re.match(r"^#{3,4}\s+(?:第?[0-9一二三四五六七八九十]+[章讲节、\.\s]*)?([^#]+)$", line_str)
            if m_chap:
                candidate = m_chap.group(1).strip()
                if any(k in candidate for k in ["说明", "红线", "准则", "不考", "超纲",
                                                "调整", "新增", "剔除", "变动", "修订"]):
                    skip_section = True
                    continue
                skip_section = False
                current_chapter = candidate
                continue

            # ── [P0-10 修复·表格型大纲 0 考点] Markdown 表格行解析 ────────
            # 此前解析器只认列表行，表格型大纲（如英语一官方大纲
            # 「题型模块 | 考查形式 | 题量与分值 | 核心考查目标」整表）
            # 解析 0 考点 → 0/0 谎报「保持平稳」。口径：表格首行（表头）
            # 与分隔行跳过；每个数据行合成 1 个考点（单元格以「 · 」连接），
            # 单元格内等级词（独立成格或「**掌握**：…」）提取为考查要求。
            if _is_table_line:
                if not _was_in_table:
                    continue  # 表格首行 = 表头
                if skip_section:
                    continue
                _cells = [c.strip() for c in line_str.strip("|").split("|")]
                if all((not c) or re.fullmatch(r":?-+:?", c) for c in _cells):
                    continue  # 分隔行 |---|---|
                _req = "掌握"
                _body_parts: List[str] = []
                for _c in _cells:
                    if not _c:
                        continue
                    _c_bare = _c.replace("**", "").strip()
                    if _c_bare in self.REQUIREMENT_LEVELS:
                        _req = _c_bare
                        continue
                    _m_cell = re.match(
                        rf"^\*{{0,2}}({self._REQ_ALT})\*{{0,2}}\s*[：:]\s*(.+)$",
                        _c)
                    if _m_cell:
                        _req = _m_cell.group(1)
                        _body_parts.append(_m_cell.group(2).strip())
                        continue
                    _body_parts.append(_c)
                _body = " · ".join(p for p in _body_parts if p)
                if not _body:
                    continue
                _meta_head = re.split(r"[：:]", _body, maxsplit=1)[0].replace("*", "").strip()
                if _meta_head in ("新增", "剔除", "调整", "变动", "修订", "删除", "变化"):
                    continue
                points.append(SyllabusPoint(
                    module=current_module,
                    chapter=current_chapter,
                    requirement=_req,
                    text=self.clean_text(_body),
                    raw_line=line_str
                ))
                continue

            # 处于负面清单章节内：整行跳过
            if skip_section:
                continue

            # 匹配考点行: - **掌握**：... 或 * **掌握**：... 或 1. 掌握：...
            # [P1 修复·2026-10-08 K1] 要求词集合改为登记表驱动（不再硬编码清单）
            m_point = re.match(rf"^[-*0-9\.\s]*\*{{0,2}}({self._REQ_ALT})\*{{0,2}}\s*[：:]\s*(.+)$", line_str)
            if m_point:
                req = m_point.group(1).strip()
                body = m_point.group(2).strip()
                # [P1 修复·2026-10-08 K1] 切分逻辑收敛到 _append_atomic_points：
                # 分隔符切分改为括号配对保护（断片修复），本分支与新分支共用。
                self._append_atomic_points(points, current_module, current_chapter,
                                           req, body, line_str)
            else:
                # 兼容普通无前缀但属于列表的知识点行
                # [P0-10 修复·编号行 0 考点] 有序编号行（如英语一
                # 「1. **词汇基准**：…」）此前只认 - / * 前缀被整行跳过；
                # 现扩展编号前缀，后续处理链（元词过滤/等级标签/冒号头判定）完全复用。
                m_plain = re.match(r"^(?:[-*]|\d+[\.、])\s+(.+)$", line_str)
                if m_plain and not line_str.startswith("<!--"):
                    raw_text = m_plain.group(1).strip()
                    # [说明文字误切] "新增：现代新儒家…""剔除：无。"是修订说明，
                    # 不是考点（此前被当成"掌握"级考点计入动荡率）。冒号头为元词
                    # 或正文仅"无"时整行丢弃。
                    _meta_head = re.split(r"[：:]", raw_text, maxsplit=1)[0].replace("*", "").strip()
                    if _meta_head in ("新增", "剔除", "调整", "变动", "修订", "删除", "变化"):
                        continue
                    if raw_text.strip() in ("无", "无。", "无变化", "略"):
                        continue
                    # 行尾 [掌握]/[理解] 等等级标签优先作为考查要求
                    req_tag = None
                    tag_m = re.search(rf"[\[【]({self._REQ_ALT})[\]】]\s*$", raw_text)
                    if tag_m:
                        req_tag = tag_m.group(1)
                        raw_text = raw_text[:tag_m.start()].strip()
                    if "：" in raw_text or ":" in raw_text:
                        parts = re.split(r"[：:]", raw_text, maxsplit=1)
                        maybe_req = parts[0].replace("*", "").strip()
                        maybe_body = parts[1].strip()
                        if maybe_req in self.REQUIREMENT_LEVELS:
                            # [P1 修复·2026-10-08 K1] 与 m_point 分支同款原子切分（括号保护）
                            self._append_atomic_points(
                                points, current_module, current_chapter,
                                maybe_req, maybe_body, line_str)
                            continue
                        # [P1 修复·2026-10-08 K1 非登记要求词] 「熟悉/运用」这类未登记但形态
                        # 明确的要求词，旧实现一律记成「掌握」且整行不切分 —— 会
                        # 掩盖新旧大纲的要求升降级（如 熟悉→掌握 本应报 MODIFIED）。
                        # 现如实把词本身作为考查要求，正文按原子考点切分；
                        # 未命中前缀的冒号头（「词汇基准」「进程与线程」）是考点名，
                        # 维持既有行为（等级取行尾标签，文本保留「考点名：内容」）。
                        if maybe_req.startswith(_REQ_LIKE_PREFIXES):
                            self._append_atomic_points(
                                points, current_module, current_chapter,
                                maybe_req, maybe_body, line_str)
                            continue
                        # 加粗头是考点名而非等级：等级取行尾标签，文本保留「考点名：内容」
                        p = SyllabusPoint(
                            module=current_module,
                            chapter=current_chapter,
                            requirement=req_tag or "掌握",
                            text=self.clean_text(raw_text),
                            raw_line=line_str
                        )
                        points.append(p)
                        continue

                    p = SyllabusPoint(
                        module=current_module,
                        chapter=current_chapter,
                        requirement=req_tag or "掌握",
                        text=self.clean_text(raw_text),
                        raw_line=line_str
                    )
                    points.append(p)

        return points

    def _normalize_key(self, text: str) -> str:
        """归一化考点识别键 (去除标点、符号、LaTeX等以进行鲁棒匹配)"""
        t = re.sub(r"[\$\\\{\}\(\)（）\s、，。；;：:]+", "", text.lower())
        return t

    def compare_points(
        self,
        old_points: List[SyllabusPoint],
        new_points: List[SyllabusPoint]
    ) -> Tuple[List[DiffItem], Dict[str, Any]]:
        """
        对比两组原子考点并生成变动清单
        """
        diff_items: List[DiffItem] = []

        old_map: Dict[str, SyllabusPoint] = {}
        for p in old_points:
            k = self._normalize_key(p.text)
            if k and k not in old_map:
                old_map[k] = p

        new_map: Dict[str, SyllabusPoint] = {}
        for p in new_points:
            k = self._normalize_key(p.text)
            if k and k not in new_map:
                new_map[k] = p

        matched_old_keys: Set[str] = set()

        # 遍历新大纲，判定 ADDED / MODIFIED / UNCHANGED
        for k_new, p_new in new_map.items():
            if k_new in old_map:
                p_old = old_map[k_new]
                matched_old_keys.add(k_new)
                # 检查考查级别是否有变化
                old_level = self.REQUIREMENT_LEVELS.get(p_old.requirement, 2)
                new_level = self.REQUIREMENT_LEVELS.get(p_new.requirement, 2)

                if p_old.requirement != p_new.requirement:
                    # [P2 修复·2026-10-08 同级误标放宽] 此前 `new_level > old_level`
                    # 不成立一律标「放宽」—— 同级要求词变更（如 掌握→熟练掌握、
                    # 理解→会，两侧登记级别相同）被方向性谎报为降级。现三向判定：
                    # 提升 / 放宽 / 同级调整。
                    if new_level > old_level:
                        direction = "提升"
                    elif new_level < old_level:
                        direction = "放宽"
                    else:
                        direction = "同级调整"
                    diff_items.append(DiffItem(
                        change_type="MODIFIED",
                        point_new=p_new,
                        point_old=p_old,
                        detail=f"考查要求从【{p_old.requirement}】{direction}为【{p_new.requirement}】"
                    ))
                else:
                    diff_items.append(DiffItem(
                        change_type="UNCHANGED",
                        point_new=p_new,
                        point_old=p_old,
                        detail="考点要求与表述基本一致"
                    ))
            else:
                # 模糊子串匹配，防止由于微小字词扩充误报为全量新增
                fuzzy_match = None
                for k_old, p_old in old_map.items():
                    if k_old in matched_old_keys:
                        continue
                    if (len(k_old) >= 4 and k_old in k_new) or (len(k_new) >= 4 and k_new in k_old):
                        fuzzy_match = (k_old, p_old)
                        break

                if fuzzy_match:
                    k_old, p_old = fuzzy_match
                    matched_old_keys.add(k_old)
                    diff_items.append(DiffItem(
                        change_type="MODIFIED",
                        point_new=p_new,
                        point_old=p_old,
                        detail=f"考点表述调整: 原【{p_old.text}】-> 现【{p_new.text}】(要求: {p_new.requirement})"
                    ))
                else:
                    diff_items.append(DiffItem(
                        change_type="ADDED",
                        point_new=p_new,
                        point_old=None,
                        detail=f"【新增核心考点】要求: {p_new.requirement}，所属章节: {p_new.chapter}"
                    ))

        # 遍历旧大纲中未匹配的项，判定 REMOVED
        for k_old, p_old in old_map.items():
            if k_old not in matched_old_keys:
                diff_items.append(DiffItem(
                    change_type="REMOVED",
                    point_new=None,
                    point_old=p_old,
                    detail=f"【大纲已剔除】原要求: {p_old.requirement}，所属章节: {p_old.chapter}"
                ))

        # 计算统计量
        added_count = sum(1 for d in diff_items if d.change_type == "ADDED")
        removed_count = sum(1 for d in diff_items if d.change_type == "REMOVED")
        modified_count = sum(1 for d in diff_items if d.change_type == "MODIFIED")
        unchanged_count = sum(1 for d in diff_items if d.change_type == "UNCHANGED")
        # [P2 修复·2026-10-08 指标不守恒] 总数改用**去重后**的口径（与明细同源）：
        # old_map/new_map 才是实际比对基准（同文本去重、空归一键剔除），此前
        # total 取原始列表长度 —— 大纲里同一考点出现两次（或同文本分属两个
        # 要求级别）时，看板「总考点数」大于 added+modified+unchanged，
        # 学生看到的分类数对不上总数（实测 4 vs 3、3 vs 2）。
        # 现恒守恒：total_new == added + modified + unchanged；
        #           total_old == removed + modified + unchanged。
        total_old = len(old_map)
        total_new = len(new_map)

        if total_new == 0 and total_old == 0:
            volatility = 0.0
        elif total_new == 0 and total_old > 0:
            volatility = 100.0
        else:
            denominator = max(total_old, total_new, 1)
            volatility = min(100.0, round((added_count + removed_count + modified_count) / denominator * 100, 1))

        # [P0-10 修复·0 点谎报] 任一侧解析到 0 个考点时，「稳健微调」/「重大重构」
        # 都是把「无法解析」包装成确定性结论（实测：英语一官方大纲表格型
        # 978 字符 → 0 考点 → 谎报「保持平稳」；0 vs 有内容 → 谎报「重大重构」）。
        # 等级降级为「无法判定」，详细警示由 compare_texts 的 parse_warning 承载。
        if total_old == 0 or total_new == 0:
            stability_grade = "无法判定"
        else:
            stability_grade = "稳健微调" if volatility < 10 else ("中度改版" if volatility < 30 else "重大重构")

        metrics = {
            "total_old": total_old,
            "total_new": total_new,
            "added_count": added_count,
            "removed_count": removed_count,
            "modified_count": modified_count,
            "unchanged_count": unchanged_count,
            "volatility_percentage": volatility,
            "stability_grade": stability_grade
        }

        return diff_items, metrics

    def compare_texts(
        self,
        old_text: str,
        new_text: str,
        school: str = "全国统考/自命题",
        major: str = "目标科目",
        year_old: int = 2026,
        year_new: int = 2027,
        subject_name: str = ""
    ) -> Dict[str, Any]:
        """
        比对两份大纲文本并输出全套结构化研报数据
        """
        old_points = self.parse_syllabus(old_text)
        new_points = self.parse_syllabus(new_text)

        diff_items, metrics = self.compare_points(old_points, new_points)

        report_data = {
            "school": school,
            "major": major,
            "subject_name": subject_name or major,
            "year_old": year_old,
            "year_new": year_new,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "metrics": metrics,
            "diff_items": diff_items,
            "old_points": old_points,
            "new_points": new_points
        }

        # [F13 修复·占位基准不设防] 基准文本为【待自填】占位模板时如实标注
        # baseline_warning（消费方：CLI/REPL 渲染器 render_syllabus_diff、
        # format_diff_markdown 头部 blockquote、TUI）。检测放在 compare_texts，
        # 使无文件路径的直调方（ky fetch diff 无参、REPL /diff、Agent
        # diff_syllabus 工具）同样受警示；同文件自我对照需路径信息，仍由
        # compare_files 检测（其警示语义更具体，覆盖优先级更高）。
        baseline_warning = ""
        _placeholder_marker = ""
        try:
            try:
                from tools.syllabus_manager import PRO_PLACEHOLDER_MARKER as _pm
            except ImportError:  # pragma: no cover - 直接脚本上下文
                from syllabus_manager import PRO_PLACEHOLDER_MARKER as _pm
            _placeholder_marker = str(_pm or "")
        except Exception:  # pragma: no cover - 兜底不阻断比对
            _placeholder_marker = ""
        if "【待自填" in old_text or (
                _placeholder_marker and _placeholder_marker in old_text):
            baseline_warning = (
                "基准（旧）大纲为【待自填】占位模板，比对不代表真实考纲变动；"
                "请先导入真实旧版大纲")
        if baseline_warning:
            report_data["baseline_warning"] = baseline_warning

        # [P0-10 修复·0 点谎报] 任一侧解析到 0 个考点 → 结果不可信警示
        # （消费方：CLI/REPL 渲染器 render_syllabus_diff、format_diff_markdown、
        # GUI/TUI、Agent diff_syllabus 工具；与 baseline_warning 同款通道）。
        _total_old = metrics.get("total_old", 0)
        _total_new = metrics.get("total_new", 0)
        if _total_old == 0 or _total_new == 0:
            _empty_sides = []
            if _total_old == 0:
                _empty_sides.append("基准（旧）大纲")
            if _total_new == 0:
                _empty_sides.append("最新（新）大纲")
            report_data["parse_warning"] = (
                "、".join(_empty_sides) + "解析到 0 个考点，比对结果不可信"
                "（可能是表格/编号型大纲未解析成功或文本非大纲内容），"
                "请检查文本格式。")

        return report_data

    def compare_files(
        self,
        old_file: Path,
        new_file: Path,
        school: str = "",
        major: str = "",
        year_old: int = 2026,
        year_new: int = 2027
    ) -> Dict[str, Any]:
        """
        比对两个大纲文件
        """
        p_old = Path(old_file)
        p_new = Path(new_file)
        if not p_old.exists():
            raise FileNotFoundError(f"基准大纲文件不存在: {old_file}")
        if not p_new.exists():
            raise FileNotFoundError(f"最新大纲文件不存在: {new_file}")

        text_old = p_old.read_text(encoding="utf-8", errors="ignore")
        text_new = p_new.read_text(encoding="utf-8", errors="ignore")

        # [F13 修复·自我对照不设防] 同文件自我对照照常产出「0.0% 稳定」报告，
        # 看似真实比对结论（C-P6 观察；map 侧有 D9 占位识别，diff 此前没有）。
        # 此处如实标注 baseline_warning；【待自填】占位基准的检测已下沉
        # compare_texts（覆盖无路径直调方），此处警示语义更具体、优先级更高。
        baseline_warning = ""
        try:
            if p_old.resolve() == p_new.resolve():
                baseline_warning = (
                    f"新旧大纲为同一文件（{p_new.name}），自我对照无考纲变动意义")
        except OSError:  # pragma: no cover - 极端文件系统错误
            pass

        report = self.compare_texts(
            text_old,
            text_new,
            school=school or "目标院校",
            major=major or p_new.stem,
            year_old=year_old,
            year_new=year_new
        )
        if baseline_warning:
            report["baseline_warning"] = baseline_warning
        return report

    @staticmethod
    def added_prescription(requirement: str) -> str:
        """[P2 修复·2026-10-08 处方列全量同一句] 新增考点的「私教应试处方」按
        考查级别分层：高危（掌握类，3 级）/ 理解类（2 级）/ 了解类（1 级）。
        修复前整列固定「首年新增大概率出选择或基础大题，严防概念漏洞」，
        不同级别的新增考点拿到同一句处方，处方列失去区分度。
        未登记要求词按「理解」档（与 compare_points 的缺省级别一致）。
        """
        level = SyllabusDiffGenerator.REQUIREMENT_LEVELS.get(requirement, 2)
        if level >= 3:
            return "**高危**：优先补 3 道基础变式，严防概念漏洞"
        if level >= 2:
            return "先抓定义与辨析要点，配 2 道客观题再认"
        return "一轮速览记忆即可，不做深挖"

    def generate_llm_advice(self, report_data: Dict[str, Any]) -> Optional[str]:
        """LLM 战术建议生成（显式降级：失败打印告警并返回 None，不再静默）。

        [P2 修复·2026-10-08 内嵌 LLM 调用] 从 :meth:`format_diff_markdown` 抽出
        的独立入口：调用方可先调本方法、再把结果经 ``strategic_advice=`` 注入
        格式化（纯离线场景注入 ``""`` 即可完全避免网络副作用）；默认路径仍调用
        本方法以保持向后兼容。

        边界（沿用 P0-10 口径）：``parse_warning``（任一侧 0 考点）直接返回
        None —— 不给空数据编「战术建议」，也不产生真实计费调用。
        LLM 未配置属正常离线态（静默走通用模板）；已配置但调用失败 / 返回过短
        属异常降级，打印一次性黄色告警（修复前为 ``except: pass`` 静默）。
        """
        if str(report_data.get("parse_warning") or "").strip():
            return None
        try:
            try:
                from tools.llm_client import is_llm_configured, chat_completion
            except ImportError:
                from llm_client import is_llm_configured, chat_completion
            if not is_llm_configured(workspace_root=ROOT):
                return None
            diff_items: List[DiffItem] = report_data.get("diff_items", []) or []
            added_items = [d for d in diff_items if d.change_type == "ADDED"]
            removed_items = [d for d in diff_items if d.change_type == "REMOVED"]
            modified_items = [d for d in diff_items if d.change_type == "MODIFIED"]
            added_summary = "、".join([it.point_new.text for it in added_items[:5]]) or "无新增"
            removed_summary = "、".join([it.point_old.text for it in removed_items[:5]]) or "无剔除"
            modified_summary = "、".join([f"{it.point_new.text}({it.detail})" for it in modified_items[:5]]) or "无微调"
            school = report_data.get("school", "目标院校")
            major = report_data.get("major", "专业")
            prompt = (
                f"你是一位考研命题研究总教练。\n"
                f"目标院校专业：{school} - {major}。\n"
                f"大纲变动情况：\n"
                f"- 新增考点：{added_summary}\n"
                f"- 剔除考点：{removed_summary}\n"
                f"- 级别微调考点：{modified_summary}\n"
                f"请结合以上具体的考点变动，为考生输出 3-4 条极具战术针对性的备考执行建议（包括时间分配、变式练兵、规避无谓消耗与题型防范）。\n"
                f"以编号列表形式输出，每条标出加粗核心观点，语气严谨专业，直接返回列表文字。"
            )
            llm_advice = chat_completion(prompt, workspace_root=ROOT, timeout=12.0)
            if llm_advice and len(llm_advice.strip()) > 30:
                return llm_advice.strip()
            print("\033[93m[考纲Diff] LLM 战术建议生成失败（返回为空或过短），"
                  "本次研报已降级为通用模板建议\033[0m")
            return None
        except Exception as e:  # noqa: BLE001 - 建议生成失败绝不阻断研报
            # 显式降级（修复前静默吞掉）：只报异常类型，不回显可能含响应体的消息。
            print(f"\033[93m[考纲Diff] LLM 战术建议生成失败（{type(e).__name__}），"
                  f"本次研报已降级为通用模板建议\033[0m")
            return None

    def format_diff_markdown(self, report_data: Dict[str, Any],
                             strategic_advice: Optional[str] = None) -> str:
        """
        将比对结果格式化为高可读性的 Markdown 深度研报

        [P2 修复·2026-10-08 内嵌 LLM 副作用] ``strategic_advice`` 为调用方注入的
        战术建议（``""`` = 显式声明不生成、直接走通用模板，纯离线可用）。
        默认 ``None`` 时向后兼容：内部经 :meth:`generate_llm_advice` 生成
        （LLM 已配置且数据可信时），失败不再静默 —— 打印告警并如实回落通用模板。
        生成成功的建议会缓存到 ``report_data["strategic_advice"]``，同一份报告
        再次格式化（如 format 后紧跟 save）不会重复调用 LLM。
        """
        m = report_data.get("metrics") or {
            "stability_grade": report_data.get("summary", "稳定"),
            "volatility_percentage": round(float(report_data.get("volatility", 0.0)) * 100, 1),
            "total_old": len(report_data.get("items", [])),
            "total_new": len(report_data.get("items", [])),
            "added_count": len(report_data.get("added", [])),
            "removed_count": len(report_data.get("removed", [])),
            "modified_count": len(report_data.get("modified", [])),
            "unchanged_count": len(report_data.get("items", [])),
        }
        diff_items: List[DiffItem] = report_data.get("diff_items", [])
        school = report_data.get("school", "全国统考")
        major = report_data.get("major", "专业课")
        y_old = report_data.get("year_old", 2026)
        y_new = report_data.get("year_new", 2027)

        added_items = [d for d in diff_items if d.change_type == "ADDED"]
        removed_items = [d for d in diff_items if d.change_type == "REMOVED"]
        modified_items = [d for d in diff_items if d.change_type == "MODIFIED"]

        lines = [
            f"# 📊 考研大纲考点版本比对研报 · {school} ({y_old} vs {y_new})",
            "",
            f"> **报告生成时间**: `{report_data.get('timestamp')}` | **对标科目**: `{major}` | **大纲变动定性**: `{m['stability_grade']} (波动率: {m['volatility_percentage']}%)`",
            "",
            "---",
            "",
            "## 一、核心变动量化全景看板",
            "",
            f"| 指标项 | {y_old}基准版 | {y_new}最新版 | 异动幅度 | 考情研判 |",
            "|---|---|---|---|---|",
            f"| **总考点数** | `{m['total_old']}` | `{m['total_new']}` | `{m['total_new'] - m['total_old']:+d}` | 大纲知识体量基本盘 |",
            f"| 🚨 **新增考点 (+)** | - | `{m['added_count']}` 处 | `{m['added_count']}` 项 | **今年必考高危预警，需专项补强** |",
            f"| 🍃 **剔除考点 (-)** | `{m['removed_count']}` 处 | - | `-{m['removed_count']}` 项 | **已划出考查范围，立即停止复习** |",
            f"| ⚠️ **要求调整 (~)** | - | `{m['modified_count']}` 处 | `{m['modified_count']}` 项 | **掌握级别或提法变更，需注意考法** |",
            f"| 🔒 **不变考点 (=)** | - | `{m['unchanged_count']}` 处 | 稳定盘 | 历年核心重点，维持常规进度 |",
            "",
            "---",
            "",
            "## 二、高危必看：新增考点清单与专项突破处方 (Added Points)",
            ""
        ]

        # [F13 修复·占位/自我对照不设防] 基准警示以 blockquote 置于头部元信息行
        # 之后，避免考生把「0.0% 稳定」当成真实考纲结论（insert(3) 即元信息行后）。
        _baseline_warning = str(report_data.get("baseline_warning") or "").strip()
        if _baseline_warning:
            lines.insert(3, f"> ⚠️ {_baseline_warning}")
        # [P0-10 修复·0 点谎报] 解析到 0 考点的警示与基准警示同通道展示
        _parse_warning = str(report_data.get("parse_warning") or "").strip()
        if _parse_warning:
            lines.insert(3, f"> ⚠️ {_parse_warning}")

        if added_items:
            lines.append("| 序号 | 所属模块 | 章节定位 | 考查级别 | 新增考点内容 | 私教应试处方与真题变式要求 |")
            lines.append("|---|---|---|---|---|---|")
            for idx, item in enumerate(added_items, 1):
                p = item.point_new
                lines.append(f"| {idx} | **{p.module}** | {p.chapter} | `<font color=red>**{p.requirement}**</font>` | **{p.text}** | {self.added_prescription(p.requirement)} |")
        else:
            if _parse_warning:
                # [P0-10] 0 点输入时不得给「保持平稳」这类确定性结论
                lines.append("⚠️ **比对结果不可信：解析到 0 个考点，无法判定是否存在新增。**")
            else:
                lines.append("🎉 **本次考纲未见新增知识点，复习范围保持平稳！**")

        lines.extend([
            "",
            "---",
            "",
            "## 三、减负必看：剔除删减考点清单 (Removed Points)",
            ""
        ])

        if removed_items:
            lines.append("| 序号 | 原所属模块 | 原章节定位 | 原要求 | 剔除考点内容 | 备考避坑提示 |")
            lines.append("|---|---|---|---|---|---|")
            for idx, item in enumerate(removed_items, 1):
                p = item.point_old
                lines.append(f"| {idx} | ~{p.module}~ | ~{p.chapter}~ | ~{p.requirement}~ | ~~{p.text}~~ | **已彻底移出考纲，严禁在刷题中纠结** |")
        else:
            lines.append("📌 **本次考纲未剔除既有知识点，无考点瘦身。**")

        lines.extend([
            "",
            "---",
            "",
            "## 四、考法微调：考查要求变更清单 (Modified Points)",
            ""
        ])

        if modified_items:
            lines.append("| 序号 | 所属模块与章节 | 原要求与考点 | 新版要求与考点 | 考查等级异动与题型倾向 |")
            lines.append("|---|---|---|---|---|")
            for idx, item in enumerate(modified_items, 1):
                p_o = item.point_old
                p_n = item.point_new
                lines.append(f"| {idx} | **{p_n.module}** / {p_n.chapter} | [{p_o.requirement}] {p_o.text} | [{p_n.requirement}] **{p_n.text}** | {item.detail} |")
        else:
            lines.append("📌 **无考查要求升降级变动。**")

        # 生成第五部分：指导建议（优先大模型动态深度研判）
        # [P2 修复·2026-10-08 内嵌 LLM 副作用] 建议解析顺序：显式注入 >
        # 报告内缓存 > LLM 生成（generate_llm_advice，失败显式降级）> 通用模板。
        # 0 点数据（parse_warning）不给空数据编「战术建议」，也不调 LLM
        # （P0-10 口径由 generate_llm_advice 内部兜底）。
        custom_advice = None
        if strategic_advice is not None:
            custom_advice = str(strategic_advice).strip() or None
        else:
            _cached_advice = report_data.get("strategic_advice")
            if isinstance(_cached_advice, str):
                custom_advice = _cached_advice.strip() or None
            elif not _parse_warning:
                custom_advice = self.generate_llm_advice(report_data)
                if custom_advice:
                    # 缓存进报告：同一报告后续 format/save 复用，杜绝重复 12s 调用
                    report_data["strategic_advice"] = custom_advice

        lines.extend([
            "",
            "---",
            "",
            "## 五、考研总教练战役执行指导建议 (Strategic Advice)",
            "",
        ])

        if custom_advice:
            lines.append(custom_advice)
        else:
            lines.extend([
                "1. **新增考点零遗漏**：大纲首次出现的新考点，命题组有极高概率在当年试卷中以客观题或送分小题的形式考察（以示大纲修订价值），必须本周内调用 `ky variant <考点>` 完成 3 道基础变式题练兵。",
                "2. **剔除考点立即止损**：在题集或错题本中遇到被剔除的考点，坚决不做、不背、不纠结，将节省出的宝贵时间倾斜至核心薄弱盘。",
                "3. **级别提升重点防范**：凡由“了解”上升为“掌握”的考点，题型极可能由选择题升格为推导证明或综合解答大题，需规范书写推导步骤。",
            ])

        lines.extend([
            "",
            "---",
            "*本研报由 考研学习链 (kaoyan_chain) · Syllabus Diff Generator 全自动比对生成，杜绝 AI 编造，严守官方大纲。*"
        ])

        return "\n".join(lines)

    def save_diff_report(
        self,
        report_data: Dict[str, Any],
        output_path: Optional[Path] = None
    ) -> Path:
        """将比对报告落盘为 Markdown 文件"""
        # [P1 修复·文件名不一致] 此前 school/major 只把空格换成下划线，
        # GUI 传入的 major 含全角括号（如「085400 医学电子信息工程（085400-01）」），
        # 生成的 md 文件名既过长又含全角符号，与 CLI（专业名取「814 信号与系统」）
        # 完全对不上，同一份考纲在两端口径下产出两个不同文件。
        # 现统一委托 ky_io.safe_filename 做安全化 + 截断，保证三端命名规则一致。
        try:
            from ky_io import safe_filename as _safe_fn
        except ImportError:  # pragma: no cover
            from tools.ky_io import safe_filename as _safe_fn
        school = _safe_fn(str(report_data.get("school", "全国统考")).strip() or "全国统考",
                          max_length=40)
        major = _safe_fn(str(report_data.get("major", "专业课")).strip() or "专业课",
                         max_length=60)
        # 进一步把全角括号内的方向后缀（如「085400-01」）与多余空格压平，
        # 避免同校同专业因写作「085400 医学电子信息工程（085400-01）」
        # 而生成超长且与专业代码口径不一致的文件名。
        major = re.sub(r"[（(][^）)]*[）)]", "", major)   # 去括号内容
        major = re.sub(r"\s+", "_", major).strip("_") or "专业课"
        y_new = report_data.get("year_new", 2027)

        # [P2 修复·演示产物污染正式研报] 无真实新考纲时 CLI/REPL 会跑内置演示样例，
        # 此前与正式研报同名同路径落盘（考纲变动分析_<校>_<专业>_<年>.md），
        # 演示数据（含计算机演示考点）会污染 04-专业课 备考资料并进入后续图谱/Diff 基准。
        # 现改为：演示模式强制写入独立目录「04-专业课/演示样例/」，文件名加显式前缀，
        # 且正文首行插入免责标注，确保与官方研报物理隔离、不可混淆。
        is_demo = bool(report_data.get("is_demo") or report_data.get("demo"))

        if not output_path:
            if is_demo:
                target_dir = ROOT / "04-专业课" / "演示样例"
            else:
                target_dir = ROOT / "04-专业课"
            if not target_dir.exists():
                target_dir.mkdir(parents=True, exist_ok=True)
            prefix = "[演示样例·非官方]" if is_demo else ""
            output_path = target_dir / f"{prefix}考纲变动分析_{school}_{major}_{y_new}.md"
        else:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

        md_content = self.format_diff_markdown(report_data)
        if is_demo:
            md_content = (
                "> ⚠️ **本文件为功能演示样例，非官方考纲，严禁作为备考依据。**\n"
                "> 数据来源：工具内置演示数据（未提供真实新考纲文件）。\n\n"
            ) + md_content
        atomic_write_text(output_path, md_content)
        return output_path


# 单例工厂
_generator_instance: Optional[SyllabusDiffGenerator] = None


def get_syllabus_diff_generator() -> SyllabusDiffGenerator:
    global _generator_instance
    if _generator_instance is None:
        _generator_instance = SyllabusDiffGenerator()
    return _generator_instance
