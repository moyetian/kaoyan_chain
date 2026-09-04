# -*- coding: utf-8 -*-
"""
考纲知识点图谱与动态掌握度映射引擎 (Knowledge Map Engine)
对标：小猿 AI 专属知识图谱、全科考点分值与掌握度大盘
核心功能：
  1. 结构化解析各科目「考试大纲.md」中的模块、章节与官方掌握要求 (掌握/理解/熟练应用)
  2. 智能交叉关联错题本、艾宾浩斯复测阶段与「薄弱点雷达.md」
  3. 动态评定每个考点的掌握等级 (A 熟练掌握 / B 基本巩固 / C 易错生疏 / D 高危盲区)
  4. 输出全科知识图谱矩阵与终端可视化表格
"""

import os
import re
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

try:
    from skills import error_logger
except Exception:
    try:
        from tools.skills import error_logger
    except Exception:
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


def build_knowledge_map(subject="math"):
    """
    解析指定科目的考试大纲与学情错题，构建考点-掌握度-失分风险二维图谱
    """
    subj_folder = SUBJECT_DIRS.get(subject, "01-数学")
    subj_name = SUBJECT_NAMES.get(subject, subject)
    s_dir = ROOT / subj_folder

    # 1. 扫描大纲文件
    syllabus_file = s_dir / "考试大纲.md"
    if not syllabus_file.exists():
        syllabus_file = s_dir / "01_官方考试大纲与核心考点.md"

    chapters = []
    if syllabus_file.exists():
        txt = syllabus_file.read_text(encoding="utf-8", errors="ignore")
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
            elif cur_chap is not None and (line_s.startswith("- **") or line_s.startswith("* **")):
                # 匹配考点条目，如 - **掌握**：极限的性质...
                m = re.search(r"[-*]\s+\*\*([^*]+)\*\*[：:]\s*(.*)", line_s)
                if m:
                    req_type = m.group(1).strip()
                    desc = m.group(2).strip()
                    # 按顿号或分号切分具体考点
                    sub_points = [p.strip() for p in re.split(r"[；;、]", desc) if len(p.strip()) >= 2]
                    for sp in sub_points:
                        cur_chap["points"].append({
                            "name": sp,
                            "req_type": req_type,
                            "full_desc": desc
                        })

    # 若大纲中未解析出考点，提供内置保底模块
    if not chapters:
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

            # 匹配雷达薄弱项
            in_radar = any(p_name in " ".join(r) for r in radar_pain_points)

            # 综合评级算法
            if len(unmastered_errs) >= 2 or (in_radar and len(unmastered_errs) >= 1):
                grade = "D"  # 高危盲区
            elif len(unmastered_errs) == 1:
                grade = "C"  # 易错生疏
            elif len(matched_errs) > 0 and len(unmastered_errs) == 0:
                grade = "B"  # 基本巩固 (曾错但已通过复测)
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
    lines.append(f"考点覆盖总量: {data['total_points']} 个 ｜ 全局大纲掌握率: {data['mastery_rate']}%")
    gc = data["grade_counts"]
    lines.append(f"等级分布: A (熟练) {gc['A']} | B (巩固) {gc['B']} | C (生疏) {gc['C']} | D (盲区) {gc['D']}\n")

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
