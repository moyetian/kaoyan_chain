# -*- coding: utf-8 -*-
"""
考纲知识点图谱与动态掌握度映射引擎 (Knowledge Map Engine)
对标：小猿 AI 专属知识图谱、全科考点分值与掌握度大盘
核心功能：
  1. 结构化解析各科目「考试大纲.md」中的模块、章节与官方掌握要求 (掌握/理解/熟练应用)
  2. 智能交叉关联错题本、 FSRS 复测档位与「薄弱点雷达.md」
  3. 动态评定每个考点的掌握等级 (A 熟练掌握 / B 基本巩固 / C 易错生疏 / D 高危盲区)
  4. 输出全科知识图谱矩阵与终端可视化表格
"""

import re
import json
import importlib
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent.parent

try:
    from skills import error_logger
except Exception:
    try:
        from tools.skills import error_logger
    except Exception:
        pass

# ── 科目名解析 ────────────────────────────────────────────────────────────────
# [P8 修复·双导入退化] 原实现在**模块导入期**执行 `from skills import get_subject_name`。
# 本项目 tools 目录同样在 sys.path 上，于是 `skills` 与 `tools.skills` 会被当成两个
# 不同的包各导入一份子模块；而 `skills/__init__.py` 把 `get_subject_name` 定义在
# 子模块导入（含本模块）**之后**，本模块被先行导入时该名字尚不存在，ImportError
# 被 except 吞掉后静默退化为返回通用名「专业课」的 lambda。
# 后果：`ky map pro` 里 `"408" not in subj_name` 恒为真，408 考生被误报
# 「考纲一致性告警」（实测 get_subject_name.__name__ == '<lambda>'）。
#
# 现改为：**调用时**惰性解析（届时两个包均已加载完成），并按权威优先级取科目名
#   1) skills / tools.skills 的 get_subject_name（其内部读 ky_config.json 的 study_plan）
#   2) 直接读 study_plan["{subject}_name"]（同一权威来源，不依赖包导入状态）
#   3) 中性静态回退
# 这样无论导入顺序如何、无论哪一个副本被使用，结果都一致且正确。


#: 科目键别名归一（与 skills.get_subject_name 内部口径一致）
_SUBJECT_KEY_ALIASES = {
    "maths": "math", "数学": "math", "math1": "math", "math2": "math", "math3": "math",
    "english": "eng", "英语": "eng", "eng1": "eng", "eng2": "eng",
    "politics": "pol", "政治": "pol",
    "major": "pro", "专业课": "pro",
}


def _subject_name_from_plan(subject: str) -> Optional[str]:
    """直接从 ky_config.json 的 study_plan 读取科目全称（权威来源）。"""
    try:
        cfg_file = ROOT / "ky_config.json"
        if not cfg_file.exists():
            return None
        plan = (json.loads(cfg_file.read_text(encoding="utf-8")) or {}).get("study_plan") or {}
        key = str(subject).strip().lower()
        key = _SUBJECT_KEY_ALIASES.get(key, key)
        for cand in (f"{key}_name", f"{subject}_name"):
            name = str(plan.get(cand) or "").strip()
            if name:
                return name
        return None
    except Exception:
        return None


def _resolve_subject_name(subject: str, default: Optional[str] = None) -> str:
    """按「权威来源优先」的顺序解析科目全称，不受双导入影响。

    1) study_plan["{subject}_name"]（ky_config.json，报考科目全称的权威来源）
    2) skills / tools.skills 的 get_subject_name（内部同样读 study_plan，作为兜底）
    3) 中性静态回退
    """
    plan_name = _subject_name_from_plan(subject)
    if plan_name:
        return plan_name
    for mod_name in ("skills", "tools.skills"):
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        fn = getattr(mod, "get_subject_name", None)
        if callable(fn):
            try:
                resolved = fn(subject, default)
            except Exception:
                continue
            if resolved:
                return resolved
    if default is not None:
        return default
    return _SUBJECT_NAME_FALLBACK.get(subject, subject)


def get_subject_name(subject_key: str, default: Optional[str] = None) -> str:
    """兼容入口：惰性解析科目全称（见 _resolve_subject_name）。"""
    return _resolve_subject_name(subject_key, default)

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


# 剥离这些常见词尾后，长考点名才能与简短错题标题匹配上
#   例：「泰勒展开定理」→「泰勒展开」→ 命中错题「泰勒展开阶数匹配失误」
_TAIL_WORDS = (
    "定理", "性质", "法则", "公式", "定义", "概念", "计算", "应用", "方法",
    "判定", "条件", "意义", "类型", "关系", "技巧", "求导", "方程", "展开",
    "要求", "能力", "技能", "结构", "知识",
)


def _keyword_fragments(name):
    """将考纲考点名切分为可用于模糊匹配的最小语义片段。

    考纲考点名多为长句（如「罗尔定理、拉格朗日中值定理、柯西中值定理与泰勒展开定理」），
    而错题标题多为短句（如「泰勒展开阶数匹配失误」）。
    直接做子串包含判断必然失败，故先按分隔符切分，再剥离通用词尾，得到核心词。
    """
    if not name:
        return []
    parts = re.split(r"[、,，/；;]|与|及|和|以及", str(name))
    cands = set()
    for p in parts:
        p = p.strip()
        if len(p) < 2:
            continue
        cands.add(p)
        # 去掉「的」连接的修饰成分，保留核心词
        for seg in re.split(r"的", p):
            seg = seg.strip()
            if len(seg) >= 2:
                cands.add(seg)
        # 逐层剥离通用词尾，得到更短的核心词
        for tail in _TAIL_WORDS:
            if p.endswith(tail) and len(p) - len(tail) >= 2:
                cands.add(p[:-len(tail)])
    # 丢弃泛化停用词与过短片段。
    # 这些词（如「概念」「性质」）几乎出现在每条错题里，保留会让所有考点匹配到全部错题。
    stop = set(_TAIL_WORDS) | {"的", "及其", "其它", "其他", "相关", "基本", "综合"}
    return [f for f in cands if len(f) >= 2 and f not in stop]


# [P3 修复·D9] 占位模板识别：新建档时生成的「考试大纲.md」含大量提示语占位行，
# 例如「第一章：[请根据报考院校官网大纲填入…]」。旧逻辑会把占位行当成真实考点解析，
# 于是图谱只剩 1 个考点、掌握率恒为 0%，对复习毫无指导意义且会误导考生。
#
# [R2-A2 修复·生成侧与判定侧标记不一致] P12 把占位文案改成全角「【待自填…】」后，
# 下面这份**只认 ASCII 方括号**的 pattern 列表不再命中，占位大纲又被当成真实考点，
# 重新产出「1 考点 / 0% 掌握率」的假图谱。现改为三层判定：
#   1) 生成侧权威标记 PRO_PLACEHOLDER_MARKER（syllabus_manager，双导入两侧都试）；
#   2) 全角/半角占位括号形态兜底；
#   3) 既有「有效正文行数」启发式。
_PLACEHOLDER_PATTERNS = (
    r"\[\s*请根据[^\]]*\]",
    r"\[\s*请填写[^\]]*\]",
    r"\[\s*待填写[^\]]*\]",
    r"\[\s*待补充[^\]]*\]",
    r"\[\s*TODO[^\]]*\]",
    r"\[\s*示例[^\]]*\]",
    r"\[\s*请输入[^\]]*\]",
    r"\[\s*\.\.\.[^\]]*\]",
    # 全角方括号形态（P12 起生成侧使用的写法，如「【待自填】」「【请填写…】」）
    r"【\s*待自填",
    r"【\s*待填写",
    r"【\s*待补充",
    r"【\s*请根据",
    r"【\s*请填写",
    r"【\s*请输入",
    r"【\s*示例",
    r"【\s*\.\.\.",
)
_PLACEHOLDER_MIN_REAL_LINES = 5

#: 取不到 syllabus_manager 时的兜底标记（与 PRO_PLACEHOLDER_MARKER 同值）
_PLACEHOLDER_MARKER_FALLBACK = "【待自填"


def _placeholder_marker() -> str:
    """惰性取生成侧权威占位标记（双导入两侧都试，取不到则用内置兜底）。

    生成侧 ``tools/syllabus_manager.py`` 定义 ``PRO_PLACEHOLDER_MARKER`` 作为单一事实源；
    判定侧此前各写一份 pattern 列表，改文案就会漏判（R2-A2）。此处复用同一常量。
    """
    for mod_name in ("syllabus_manager", "tools.syllabus_manager"):
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        marker = str(getattr(mod, "PRO_PLACEHOLDER_MARKER", "") or "")
        if marker:
            return marker
    return _PLACEHOLDER_MARKER_FALLBACK


def _is_placeholder_syllabus(txt):
    """判断考试大纲是否仍为未填写的占位模板（而非院校官方考纲）。

    判定口径（优先级从高到低）：
    1. 命中生成侧显式占位标记（如「【待自填」）→ **直接判为占位**，不再被行数启发式
       否决（占位正文常含 ≥5 行不含括号的说明行，会盖过 ``_PLACEHOLDER_MIN_REAL_LINES``）；
    2. 剔除标题行、表格分隔行、空行后，统计「有效正文行」数量：有效正文行 = 长度 ≥ 8
       且不含任何占位括号标记；少于 5 行即视为占位模板。
    """
    if not txt or not str(txt).strip():
        return True
    text = str(txt)
    marker = _placeholder_marker()
    if marker and marker in text:
        return True
    real_lines = 0
    for line in text.splitlines():
        s = line.strip()
        if len(s) < 8:
            continue
        if s.startswith("#") or re.match(r"^\|[\s:\-|]+\|$", s):
            continue
        if any(re.search(p, s) for p in _PLACEHOLDER_PATTERNS):
            continue
        real_lines += 1
    return real_lines < _PLACEHOLDER_MIN_REAL_LINES


def build_knowledge_map(subject="math", root=None):
    """
    解析指定科目的考试大纲与学情错题，构建考点-掌握度-失分风险二维图谱

    ``root`` 可选：指定工作区根目录（默认模块级 ROOT）。组卷引擎按考纲考点生成
    差异化占位题时传入自己的 ROOT，保证与试卷落盘目录同源（测试隔离亦依赖此）。
    """
    subj_folder = SUBJECT_DIRS.get(subject, "01-数学")
    subj_name = get_subject_name(subject, SUBJECT_NAMES.get(subject, subject))
    s_dir = (Path(root) if root else ROOT) / subj_folder

    # 1. 扫描大纲文件
    syllabus_file = s_dir / "考试大纲.md"
    if not syllabus_file.exists():
        syllabus_file = s_dir / "01_官方考试大纲与核心考点.md"

    chapters = []
    syllabus_placeholder = False
    if syllabus_file.exists():
        txt = syllabus_file.read_text(encoding="utf-8", errors="ignore")
        # [P3 修复·D9] 未填写的占位模板不参与解析，避免产出「1 考点 / 0% 掌握率」的假图谱
        syllabus_placeholder = _is_placeholder_syllabus(txt)
        if syllabus_placeholder:
            txt = ""
        cur_chap = None
        in_table_data = False  # 表格型大纲：仅在分隔行之后才解析数据行
        for line in txt.splitlines():
            line_s = line.strip()
            # 匹配章节标题，如 ## 一、高等数学 或 ### 1. 函数、极限、连续
            if line_s.startswith("### ") or line_s.startswith("## "):
                in_table_data = False
                c_title = re.sub(r"^#+\s*", "", line_s)
                if any(kw in c_title for kw in ("最高红线", "绝不超纲", "AI 私教")):
                    continue
                cur_chap = {"title": c_title, "points": []}
                chapters.append(cur_chap)
            elif re.match(r"^\|[\s:\-|]+\|$", line_s):
                # Markdown 表格分隔行：其后的行才是数据行
                in_table_data = True
                continue
            elif cur_chap is not None and line_s.startswith("|"):
                # 表格型大纲（如英语二题型结构表）：取首列作考点名，其余列作描述
                if not in_table_data:
                    continue  # 表头行，跳过
                cells = [c.strip() for c in line_s.strip("|").split("|")]
                name = re.sub(r"\*\*", "", cells[0]).strip() if cells else ""
                if len(name) >= 2:
                    desc = " / ".join(c for c in cells[1:] if c).strip(" /")
                    cur_chap["points"].append({
                        "name": name,
                        "req_type": "掌握",
                        "full_desc": desc
                    })
            elif cur_chap is not None and (line_s.startswith(("-", "*"))):
                # 匹配多种考点条目风格:
                # 风格 1: - **掌握**：极限的性质、有界性定理...
                m1 = re.search(r"^[-*]\s+\*\*([^*]+)\*\*[：:]\s*(.*)", line_s)
                # 风格 2: - 信号的时移与卷积 (要求：掌握) 或 (掌握)
                m2 = re.search(r"^[-*]\s+(.*?)[（\(](?:要求[：:])?\s*(掌握|理解|了解|会用)[）\)]", line_s)
                # 风格 3: - 第一章：数据结构基本概念 (掌握)
                if m1:
                    req_type = m1.group(1).strip()
                    desc = m1.group(2).strip()
                    # 智能切分顿号/分号，避免切断括号内的并列项
                    # 先提取顶级考点
                    clean_desc = desc
                    # 将括号内的顿号临时保护
                    def _protect_parens(match):
                        return match.group(0).replace("、", "§").replace("；", "¤")
                    protected = re.sub(r"[（\(][^）\)]+[）\)]", _protect_parens, clean_desc)
                    raw_sub = [p.strip().replace("§", "、").replace("¤", "；") for p in re.split(r"[；;]", protected) if len(p.strip()) >= 2]
                    for sp in raw_sub:
                        # 若未带括号，按顿号切分
                        if "（" not in sp and "(" not in sp:
                            for sub_p in re.split(r"[、]", sp):
                                sub_p = sub_p.strip()
                                if len(sub_p) >= 2:
                                    cur_chap["points"].append({
                                        "name": sub_p,
                                        "req_type": req_type,
                                        "full_desc": desc
                                    })
                        else:
                            # 带有括号的考点完整保留，如 "闭区间上连续函数的性质（有界性定理、最值定理、零点定理）"
                            sp_clean = re.sub(r"^[、\s]+", "", sp).strip()
                            if len(sp_clean) >= 2:
                                cur_chap["points"].append({
                                    "name": sp_clean,
                                    "req_type": req_type,
                                    "full_desc": desc
                                })
                elif m2:
                    p_name = m2.group(1).strip().lstrip("0123456789.、- ")
                    req_type = m2.group(2).strip()
                    if len(p_name) >= 2 and not p_name.startswith("[请根据"):
                        cur_chap["points"].append({
                            "name": p_name,
                            "req_type": req_type,
                            "full_desc": line_s
                        })

    # [一致性守卫] 专业课：考纲实际内容与报考科目不符时给出醒目告警。
    # 典型场景：上一轮按 408 建档、本轮改为院校自命题（如 814 信号与系统），
    # 若沿用残留的 408 大纲，map / 组卷 / 变式会全部按错误科目出题。
    syllabus_warning = None
    if subject == "pro" and syllabus_file.exists():
        try:
            _syl_txt = syllabus_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            _syl_txt = ""
        if _syl_txt:
            try:
                from syllabus_manager import looks_like_408_syllabus
            except Exception:
                try:
                    from tools.syllabus_manager import looks_like_408_syllabus
                except Exception:
                    looks_like_408_syllabus = None
            if looks_like_408_syllabus and looks_like_408_syllabus(_syl_txt) and "408" not in str(subj_name):
                syllabus_warning = (
                    f"当前报考科目为【{subj_name}】，但 04-专业课/考试大纲.md 仍是 "
                    f"408 计算机学科专业基础内容。请运行 ky plan 重建考纲，"
                    f"或将院校官方自命题大纲覆盖到该文件后再开始复习！"
                )

    # [P3 修复·D9] 占位模板优先级最高：明确告知图谱不可用及补录路径
    if syllabus_placeholder:
        syllabus_warning = (
            f"【{subj_name}】考试大纲.md 仍为未填写的占位模板，尚未录入院校官方考点，"
            f"知识图谱已拒绝以占位内容生成（不再输出虚假考点与 0% 掌握率）。"
            f"请先补录官方考纲：运行 ky plan 重建，或手动将官方大纲覆盖到 "
            f"{syllabus_file.name} 后再查看图谱。"
        )

    # 若大纲中未解析出考点，提供内置保底模块
    # （占位模板场景除外：此时必须显式报缺，不得用保底模块伪装成真实图谱）
    if not chapters and not syllabus_placeholder:
        chapters = [
            {"title": "基础核心模块", "points": [{"name": f"{subj_name}核心必考概念", "req_type": "掌握", "full_desc": ""}]}
        ]

    # 2. 读取错题本中的活跃错题
    active_errors = []
    if error_logger:
        active_errors = error_logger.scan_error_records(subject)

    # 预计算每条错题的可检索文本，避免在内层循环里反复拼串。
    # 注意：不要把 error_type 放进检索文本 —— 它是「概念漏洞/计算失误」这类分类标签，
    # 会让「极限的**概念**」这类考点与几乎所有错题误匹配。
    for _e in active_errors:
        _e["_haystack"] = " ".join([
            str(_e.get("title", "") or ""),
            str(_e.get("question", "") or ""),
            str(_e.get("detail", "") or ""),
        ])

    # 3. 读取薄弱点雷达
    radar_file = s_dir / "_状态" / "薄弱点雷达.md"
    radar_pain_points = []
    if radar_file.exists():
        r_txt = radar_file.read_text(encoding="utf-8", errors="ignore")
        for line in r_txt.splitlines():
            if "|" in line and not line.startswith("|---|"):
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) >= 3:
                    radar_pain_points.append(parts)

    # 4. 计算每个考点的掌握等级与关联错题数
    #    等级含义: A 熟练 / B 巩固 / C 生疏 / D 盲区 / U 未评估
    #    重要: 「零错题」≠「已掌握」。从未练过、尚无错题记录的考点必须标为 U，
    #    否则新学员会看到 100% 掌握率的假象，进而误判复习优先级。
    total_points = 0
    grade_counts = {"A": 0, "B": 0, "C": 0, "D": 0, "U": 0}

    for chap in chapters:
        for pt in chap["points"]:
            total_points += 1
            p_name = pt["name"]

            # 匹配错题记录：用考点名切出的语义片段做双向模糊匹配。
            # 直接判断「考点名 in 错题标题」几乎永远为假（考点名是长句，标题是短句），
            # 因此改为提取核心关键词后再匹配，例如：
            #   「…柯西中值定理与泰勒展开定理」→「泰勒展开」→ 命中「泰勒展开阶数匹配失误」
            frags = _keyword_fragments(p_name)
            matched_errs = [
                e for e in active_errors
                if any(f in e.get("_haystack", "") for f in frags)
            ]
            unmastered_errs = [e for e in matched_errs if "已掌握" not in e.get("status", "")]

            # 匹配雷达薄弱项：与「错题匹配」同理，必须沿「短片段 → 长文本」的方向判断。
            # 此前写作 `p_name in " ".join(r)`，即用**长考点名**去 in **短雷达模块名**，
            # 恒为 False；导致「雷达已标红 + 1 道活跃错题」本应判 D(高危盲区)，
            # 却被降级为 C(易错生疏)，知识图谱会系统性低估高危区。
            in_radar = any(any(f in " ".join(r) for f in frags)
                           for r in radar_pain_points)

            # 综合评级算法
            if len(unmastered_errs) >= 2 or (in_radar and len(unmastered_errs) >= 1):
                grade = "D"  # 高危盲区 (多次做错或雷达标红)
            elif len(unmastered_errs) == 1:
                grade = "C"  # 易错生疏 (有活跃未消灭错题)
            elif len(matched_errs) >= 2 and len(unmastered_errs) == 0:
                grade = "A"  # 熟练掌握 (经多次复测且全部掌握闭环)
            elif len(matched_errs) == 1 and len(unmastered_errs) == 0:
                grade = "B"  # 基本巩固 (曾错1题，目前已消灭)
            else:
                grade = "U"  # 未评估 (尚未练习，无任何错题或雷达记录佐证)

            pt["grade"] = grade
            pt["error_count"] = len(matched_errs)
            pt["active_error_count"] = len(unmastered_errs)
            grade_counts[grade] += 1

    # 掌握率只统计「已评估」考点，避免把大片未练习的考点算作已掌握而虚高
    assessed_count = total_points - grade_counts["U"]
    if assessed_count > 0:
        mastery_rate = round((grade_counts["A"] + grade_counts["B"]) / assessed_count * 100, 1)
    else:
        mastery_rate = 0.0
    assessed_rate = round(assessed_count / total_points * 100, 1) if total_points > 0 else 0.0

    return {
        "subject": subject,
        "subject_name": subj_name,
        "total_points": total_points,
        "total_topics": total_points,
        "grade_counts": grade_counts,
        "mastery_rate": mastery_rate,
        "assessed_count": assessed_count,
        "unassessed_count": grade_counts["U"],
        "assessed_rate": assessed_rate,
        "syllabus_warning": syllabus_warning,
        "syllabus_placeholder": syllabus_placeholder,
        "chapters": chapters,
        "modules": {c["title"]: c["points"] for c in chapters}
    }


def format_knowledge_map_table(subject="math"):
    """
    将知识图谱格式化为易读的终端报表
    """
    data = build_knowledge_map(subject)
    lines = []
    lines.append(f"\n============================================================")
    lines.append(f"  🗺️ 考研考纲知识点图谱与掌握度大盘 · {data['subject_name']}")
    lines.append(f"============================================================")

    # [P3 修复·D9] 占位模板：直接给出补录指引，不展示任何虚假考点/掌握率
    if data.get("syllabus_placeholder"):
        lines.append("⚠️ 【图谱不可用】考试大纲仍为未填写的占位模板")
        lines.append(f"  {data.get('syllabus_warning', '')}")
        lines.append("  补录完成后再执行 /map 或 ky map 查看真实图谱。")
        lines.append(f"============================================================\n")
        return "\n".join(lines)

    lines.append(f"考点覆盖总量: {data['total_points']} 个 ｜ 全局大纲掌握率: {data['mastery_rate']}%")
    gc = data["grade_counts"]
    lines.append(f"等级分布: A (熟练) {gc['A']} | B (巩固) {gc['B']} | C (生疏) {gc['C']} | D (盲区) {gc['D']} | U (待自测) {gc['U']}\n")

    if data.get("syllabus_warning"):
        lines.append(f"⚠️⚠️⚠️ 【考纲一致性告警】 ⚠️⚠️⚠️")
        lines.append(f"  {data['syllabus_warning']}")
        lines.append(f"============================================================\n")

    for chap in data["chapters"]:
        lines.append(f"【{chap['title']}】")
        for pt in chap["points"][:6]:  # 每章展示前6个核心考点
            g = pt["grade"]
            tag = f"[{g}]"
            err_info = f" (关联错题 {pt['error_count']} 道)" if pt["error_count"] > 0 else ""
            lines.append(f"  {tag} {pt['name']} · 考纲要求: {pt['req_type']}{err_info}")
        if len(chap["points"]) > 6:
            lines.append(f"  ... 另有 {len(chap['points']) - 6} 个细分考点")
        lines.append("")

    lines.append(f"💡 建议：主攻 [C] 与 [D] 评级考点，在终端输入「/variant <考点>」立即展开变式题专项训练！")
    lines.append(f"============================================================\n")
    return "\n".join(lines)


def health_check() -> dict:
    """[B4] 结构化健康自检：``{"status": READY/DEGRADED/UNAVAILABLE, "reason": str}``。

    图谱的**唯一输入**是各科「考试大纲.md」：四科中至少有一科存在且非占位模板
    → READY；否则 UNAVAILABLE 并点名缺哪一科 —— 旧硬编码"已就绪"时用户会对着
    空图谱纳闷。只做文件存在性 + 占位标记读取（KB 级，纯本地）。
    """
    valid, placeholder_only, missing = [], [], []
    for subject, folder in SUBJECT_DIRS.items():
        s_dir = ROOT / folder
        syllabus_file = s_dir / "考试大纲.md"
        if not syllabus_file.exists():
            syllabus_file = s_dir / "01_官方考试大纲与核心考点.md"
        if not syllabus_file.exists():
            missing.append(subject)
            continue
        try:
            txt = syllabus_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            missing.append(subject)
            continue
        if _is_placeholder_syllabus(txt):
            placeholder_only.append(subject)
        else:
            valid.append(subject)

    if valid:
        return {"status": "READY",
                "reason": f"考纲图谱可用（{'/'.join(valid)} 共 {len(valid)} 科大纲已填写）"}
    if placeholder_only:
        return {"status": "UNAVAILABLE",
                "reason": f"考试大纲仍为未填写的占位模板（{'/'.join(placeholder_only)}），图谱将为空；请填写对应科目的「考试大纲.md」"}
    return {"status": "UNAVAILABLE",
            "reason": f"未找到考试大纲文件（{'/'.join(missing)}），图谱将为空；请在各科目录建立「考试大纲.md」"}
