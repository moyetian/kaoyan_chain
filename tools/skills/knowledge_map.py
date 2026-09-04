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
        for line in txt.splitlines():
            line_s = line.strip()
            # 匹配章节标题，如 ## 一、高等数学 或 ### 1. 函数、极限、连续
            if line_s.startswith("### ") or line_s.startswith("## "):
                c_title = re.sub(r"^#+\s*", "", line_s)
                if any(kw in c_title for kw in ("最高红线", "绝不超纲", "AI 私教")):
                    continue
                cur_chap = {"title": c_title, "points": []}
                chapters.append(cur_chap)
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
    total_points = 0
    grade_counts = {"A": 0, "B": 0, "C": 0, "D": 0}

    for chap in chapters:
        for pt in chap["points"]:
            total_points += 1
            p_name = pt["name"]

            # 匹配错题记录
            matched_errs = [e for e in active_errors if p_name in e.get("title", "") or p_name in e.get("question", "") or p_name in e.get("detail", "")]
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
                grade = "A"  # 熟练掌握 (零错题或基准良好)

            pt["grade"] = grade
            pt["error_count"] = len(matched_errs)
            pt["active_error_count"] = len(unmastered_errs)
            grade_counts[grade] += 1

    mastery_rate = round((grade_counts["A"] + grade_counts["B"]) / total_points * 100, 1) if total_points > 0 else 100.0

    return {
        "subject": subject,
        "subject_name": subj_name,
        "total_points": total_points,
        "total_topics": total_points,
        "grade_counts": grade_counts,
        "mastery_rate": mastery_rate,
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
