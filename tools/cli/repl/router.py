# -*- coding: utf-8 -*-
"""
REPL 口令路由器 (router.py)
处理中文原生口令映射、快捷命令调度与采分点/查漏辅助
"""

import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from tools.cli.shared import (
        ROOT, SUBJECT_DIRS, load_config, save_config, read_text_safe,
        mark_today_task_done, detect_repl_safe_mode_violation,
        subject_display_name
    )
except ImportError:
    from cli.shared import (
        ROOT, SUBJECT_DIRS, load_config, save_config, read_text_safe,
        mark_today_task_done, detect_repl_safe_mode_violation,
        subject_display_name
    )

try:
    from tools.cli.repl.renderer import C, colorize
except ImportError:
    from cli.repl.renderer import C, colorize

try:
    from skills import (
        error_logger, exam_composer, variant_retriever, knowledge_map,
        math_verifier, english_dissector, socratic_tutor
    )
except ImportError:
    try:
        from tools.skills import (
            error_logger, exam_composer, variant_retriever, knowledge_map,
            math_verifier, english_dissector, socratic_tutor
        )
    except ImportError:
        error_logger = None
        exam_composer = None
        variant_retriever = None
        knowledge_map = None
        math_verifier = None
        english_dissector = None
        socratic_tutor = None

def build_homework_menu() -> str:
    """生成「交作业与批改模式」的三种提交方式指引文本"""
    return (
        "\n[📝 考研私教交作业与批改模式]\n"
        "请选择交作业方式：\n"
        "  1. 截图/草稿：先截图 (Alt+A / Win+Shift+S)，然后在此输入 /paste 立即视觉逐步批改与采分点打分\n"
        "  2. 客观答题卡：输入 /batch 你的答案 [标准答案] 立即批量核对正确率与归因\n"
        "  3. 推导大题文字：直接输入推导步骤，私教将严格按照采分点扣分与步骤赋分！\n"
    )

def build_weakness_scan_report() -> str:
    """生成「全科薄弱点雷达与到期复测清单」文本"""
    lines = ["=== 🔍 考研全科薄弱点雷达与到期复测清单 ==="]
    for s_k, (d_name, label) in SUBJECT_DIRS.items():
        radar_file = ROOT / d_name / "_状态" / "薄弱点雷达.md"
        due_items = error_logger.get_due_reviews(s_k, max_count=3) if error_logger else []
        due_count = len(due_items)
        lines.append(f"  • {label}: {due_count} 道到期待复测")
        if radar_file.exists():
            r_txt = read_text_safe(radar_file)
            weakness_lines = []
            for r_line in r_txt.splitlines():
                ls = r_line.strip()
                if not ls.startswith("|"):
                    continue
                if set(ls) <= set("|-: "):
                    continue
                cells = [c.strip() for c in ls.strip("|").split("|")]
                if len(cells) < 2:
                    continue
                head = cells[0]
                if head in ("模块名称", "题型模块", "考点描述", "核心句子", "模块",
                            "能力项", "错因", "题目/概念", "项目", "大类", "科目",
                            "#", "序号", "编号", "失误类型", "易混点", "点位", "优先级",
                            "指标", "考点", "专题", "题型"):
                    continue
                if head.isdigit():
                    continue
                body = " ｜ ".join(c for c in cells[1:] if c)
                if any(p in body for p in ("待首次自测", "待评估", "待做题后", "待首次",
                                           "首次完成自测", "首次刷题", "首次做")):
                    continue
                rating = " ".join(cells[1:3])
                has_rating = any(r in rating.split() for r in ("C", "D")) or rating in ("C", "D")
                has_signal = any(kw in body for kw in
                                 ("易错", "薄弱", "混淆", "失误", "偏差", "错因", "不熟", "卡点"))
                # [收尾修复·0 计数空条目] "定位偏差: 0 ｜ 相邻段落混淆"这类计数为 0
                # 的行此前照打（关键词"混淆"命中即打印）。数字格全零 → 无实质信号。
                num_cells = [c for c in cells[1:]
                             if re.fullmatch(r"0+(?:\.0+)?%?", c.strip())]
                digit_cells = [c for c in cells[1:] if re.search(r"\d", c)]
                if digit_cells and len(num_cells) == len(digit_cells):
                    continue
                if not (has_rating or has_signal):
                    continue
                weakness_lines.append(f"    - ⚠ {head}: {body}")
            if weakness_lines:
                for w in weakness_lines[:6]:
                    lines.append(w)
            else:
                lines.append("    （暂无已登记的薄弱项：完成首次自测后，私教将在此沉淀具体卡点）")
    lines.append("")
    lines.append("💡 立即复测错题请输入 /review，查看今日任务请输入 /today。")
    return "\n".join(lines)

CHINESE_SUBJECT_MAP = {
    "数学报到": "math", "学数学": "math", "切换数学": "math",
    "英语报到": "eng", "学英语": "eng", "切换英语": "eng",
    "政治报到": "pol", "学政治": "pol", "切换政治": "pol",
    "专业课报到": "pro", "学专业课": "pro", "切换专业课": "pro"
}


def build_subject_checkin_brief(cfg: dict, curr_subj: str) -> str:
    """生成某科目的「私教报到就绪」本地播报文本 (纯展示、无副作用)。"""
    plan = cfg.get("study_plan", {})
    if curr_subj == "math" and (plan.get("math_key") == "none" or plan.get("math_name") == "不考数学"):
        return "当前方案为不考数学，不派发数学任务。请使用英语报到、政治报到或专业课报到。"
    subj_name = subject_display_name(cfg, curr_subj)
    hours = plan.get(f"{curr_subj}_hours", 2.0)
    target = plan.get(f"{curr_subj}_target", "高分冲刺")
    weak = plan.get(f"{curr_subj}_weakness", "核心考点攻坚")

    lines = [
        f"🎓 【{subj_name} · 私教报到就绪】",
        f"• 今日规划投入: {hours} 小时 ｜ 战役目标: {target}",
        f"• 核心薄弱防线: 【{weak}】",
    ]

    due_count = 0
    if error_logger:
        try:
            due_count = len(error_logger.get_due_reviews(curr_subj, max_count=3))
        except Exception:
            due_count = 0
    if due_count:
        lines.append(f"🔔 检测到您有 {due_count} 道 FSRS 到期错题！完成作答并输入「交作业」即可启动盲盒复测。")
    else:
        t_file = ROOT / SUBJECT_DIRS[curr_subj][0] / "_状态" / "今日任务.md"
        task_lines = []
        if t_file.exists():
            txt = read_text_safe(t_file)
            for l in txt.splitlines():
                l_s = l.strip()
                if re.match(r"^-\s*\[ \]", l_s) or ("|" in l_s and "[ ]" in l_s):
                    task_lines.append(l_s)
        if task_lines:
            lines.append("📋 今日攻坚任务清单（前 2 项）：")
            lines.extend(f"  {t}" for t in task_lines[:2])
    lines.append("💡 私教提示：可直接输入题目或题干提问，完成后输入「交作业」按采分点批改！")
    return "\n".join(lines)
