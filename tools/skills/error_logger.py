# -*- coding: utf-8 -*-
"""
考研错题沉淀与艾宾浩斯盲盒复测闭环引擎 (Error Logger & Ebbinghaus Review Engine)
核心使命：
  1. 将批改产生的错题规范化沉淀到对应科目的「错题本/」
  2. 自动追踪艾宾浩斯记忆周期 (1天 / 3天 / 7天 / 15天)
  3. 支撑 /review 与 /quiz 指令：自动隐去历史解析，生成“盲盒复测试卷”
  4. 闭环状态回写：复测通过标记 [已掌握]，复测失误重置周期，联动更新薄弱点雷达！
"""

import os
import re
from datetime import datetime, date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

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

# 艾宾浩斯与 FSRS-5 简化自适应间隔参数
EBBINGHAUS_INTERVALS = [1, 3, 7, 15, 30]
FSRS_GOOD_INTERVALS = [1, 3, 7, 16, 35]
FSRS_EASY_INTERVALS = [3, 10, 28, 60]


def calc_fsrs_interval(stage: int = 0, rating: str = "good", today: date = None):
    """
    FSRS-5 简化自适应复测算法：
      - again (重来): stage 重置为 0，1 天后立即复测
      - hard (困难): stage+1，间隔因子 1.3x：round(max(1, stage * 2) * 1.3) 天
      - good (良好): stage+1，阶梯：[1, 3, 7, 16, 35] 天
      - easy (简单): stage+2，阶梯：[3, 10, 28, 60] 天
    返回: (new_stage, next_due_date, interval_days)
    """
    from datetime import timedelta
    if today is None:
        today = date.today()

    rating = str(rating or "good").strip().lower()
    stage = max(0, int(stage or 0))

    if rating == "again":
        new_stage = 0
        interval_days = 1
    elif rating == "hard":
        new_stage = stage + 1
        interval_days = int(round(max(1, stage * 2) * 1.3))
    elif rating == "easy":
        new_stage = stage + 2
        idx = min(stage, len(FSRS_EASY_INTERVALS) - 1)
        interval_days = FSRS_EASY_INTERVALS[idx]
    else:  # "good" or default
        new_stage = stage + 1
        idx = min(stage, len(FSRS_GOOD_INTERVALS) - 1)
        interval_days = FSRS_GOOD_INTERVALS[idx]

    next_due_date = today + timedelta(days=interval_days)
    return new_stage, next_due_date, interval_days


def _next_due(today: date, stage_index: int = 0, rating: str = "good") -> date:
    """根据当前复次档位与评级计算下次到期日（向后兼容）。"""
    _, next_due_d, _ = calc_fsrs_interval(stage=stage_index, rating=rating, today=today)
    return next_due_d


def log_error_record(subject="math", title="错题记录", error_type="计算失误", detail="", prescription="", question=""):
    """向对应科目的错题本追加一条结构化错题记录"""
    subj_folder = SUBJECT_DIRS.get(subject, "01-数学")
    mistake_dir = ROOT / subj_folder / "错题本"
    if subject == "eng":
        mistake_dir = ROOT / subj_folder / "错题与长难句本"

    mistake_dir.mkdir(parents=True, exist_ok=True)
    today_str = datetime.now().strftime("%Y-%m-%d")
    today_d = date.today()
    next_due_str = _next_due(today_d, 0).strftime("%Y-%m-%d")
    record_file = mistake_dir / f"错题记录_{today_str}.md"

    q_block = f"\n- **题干设问**：\n```text\n{question.strip()}\n```\n" if question else ""

    record_md = f"""
## 📌 [{today_str}] {title}
- **掌握状态**：`[待复测]` (艾宾浩斯复测中)
- **错因分类**：`{error_type}` (概念漏洞 / 审题偏差 / 公式记错 / 计算失误 / 书写丢分){q_block}
- **复测节奏**：`stage=0` · 下次到期 `{next_due_str}`（1/3/7/15/30 天阶梯）
- **错题现场与漏洞分析**：
{detail.strip()}
- **专家处方与改进建议**：
{prescription.strip()}
- **复习规划**：排入艾宾浩斯间隔序列，复测日 `{next_due_str}` 由 get_due_reviews 自动筛选
---
"""
    if record_file.exists():
        with open(record_file, "a", encoding="utf-8") as f:
            f.write(record_md)
    else:
        record_file.write_text(f"# {subj_folder} · 错题积累集 ({today_str})\n" + record_md, encoding="utf-8")

    return f"已成功将错题归档至: {record_file.relative_to(ROOT)}"

def scan_error_records(subject=None):
    """
    扫描各科目「错题本/」下的全部错题记录
    返回结构化字典列表
    """
    subjs = [subject] if subject and subject in SUBJECT_DIRS else list(SUBJECT_DIRS.keys())
    results = []

    for s in subjs:
        folder_name = SUBJECT_DIRS[s]
        mistake_dir = ROOT / folder_name / "错题本"
        if s == "eng" and not mistake_dir.exists():
            mistake_dir = ROOT / folder_name / "错题与长难句本"

        if not mistake_dir.exists():
            continue

        for md_file in mistake_dir.glob("*.md"):
            if md_file.name.startswith("_"):  # 忽略 _模板.md 和 _索引.md
                continue
            try:
                content = md_file.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            # 按 ## 📌 [YYYY-MM-DD] 划分错题卡片
            sections = re.split(r"\n(?=##\s+📌)", content)
            for sec in sections:
                if not sec.strip().startswith("## 📌"):
                    continue
                # 提取日期与标题
                header_m = re.search(r"##\s+📌\s*\[(\d{4}-\d{2}-\d{2})\]\s*(.*)", sec)
                if not header_m:
                    continue
                rec_date = header_m.group(1).strip()
                title = header_m.group(2).strip()

                # 提取状态
                status = "待复测"
                status_m = re.search(r"-\s+\*\*掌握状态\*\*[：:]\s*`?\[?(.*?)\]?`?(?:\s|$)", sec)
                if status_m:
                    status = status_m.group(1).strip()

                # 提取错因
                err_type = "需强化复练"
                err_m = re.search(r"-\s+\*\*错因分类\*\*[：:]\s*`?(.*?)`?(?:\s|\(|$)", sec)
                if err_m:
                    err_type = err_m.group(1).strip().strip("`")

                # 提取题干设问
                q_text = ""
                q_m = re.search(r"-\s+\*\*题干设问\*\*[：:]\s*```text\s*(.*?)\s*```", sec, re.DOTALL)
                if q_m:
                    q_text = q_m.group(1).strip()

                # 提取错题现场与解析
                detail_text = ""
                d_m = re.search(r"-\s+\*\*错题现场与漏洞分析\*\*[：:]\s*(.*?)(?=\n-\s+\*\*专家处方|\n-\s+\*\*复习规划|\Z)", sec, re.DOTALL)
                if d_m:
                    detail_text = d_m.group(1).strip()

                # 提取复测节奏 stage 与 next_due_date
                stage = 0
                next_due = ""
                sched_m = re.search(r"-\s+\*\*复测节奏\*\*[：:]\s*`?stage=(\d+)`?(?:.*|)\s*下次到期\s*`?(\d{4}-\d{2}-\d{2})`?", sec, re.DOTALL)
                if sched_m:
                    try:
                        stage = int(sched_m.group(1))
                    except Exception:
                        stage = 0
                    next_due = sched_m.group(2).strip()

                results.append({
                    "subject": s,
                    "subject_name": SUBJECT_NAMES.get(s, s),
                    "file_path": str(md_file),
                    "file_name": md_file.name,
                    "date": rec_date,
                    "title": title,
                    "status": status,
                    "error_type": err_type,
                    "stage": stage,
                    "next_due": next_due,
                    "question": q_text or detail_text[:200],
                    "raw_section": sec
                })

    return results

def get_due_reviews(subject=None, max_count=5):
    """
    真正的艾宾浩斯到期筛选：只返回 next_due_date ≤ today 的待复测题。
    - 旧记录（无 next_due 字段）按首次到期处理：date + 1天 ≤ today 即到期
    - 缺失 next_due 但归档时间已 ≥1 天，保守视为到期（向后兼容）
    - 按到期天数倒序排，最久没复测的优先
    """
    all_records = scan_error_records(subject)
    today = date.today()
    due_items = []

    for item in all_records:
        if "已掌握" in item["status"]:
            continue

        # 计算 next_due：优先用记录字段，缺失则回退到 date + 1天
        next_due = item.get("next_due") or ""
        if next_due:
            try:
                next_due_d = datetime.strptime(next_due, "%Y-%m-%d").date()
            except Exception:
                next_due_d = None
        else:
            next_due_d = None

        try:
            rec_d = datetime.strptime(item["date"], "%Y-%m-%d").date()
        except Exception:
            rec_d = None

        if next_due_d is None and rec_d is not None:
            from datetime import timedelta
            next_due_d = rec_d + timedelta(days=1)  # 向后兼容：旧记录视为首次到期

        if next_due_d is None:
            continue

        days_until = (next_due_d - today).days
        if days_until > 0:
            # 还没到期：不进入队列
            item["days_until_due"] = days_until
            continue

        item["days_until_due"] = days_until  # 0 或负数
        item["days_overdue"] = -days_until
        due_items.append(item)

    # 逾期最久的优先（days_overdue 越大越优先）
    due_items.sort(key=lambda x: (x.get("days_overdue", 0), x["date"]), reverse=True)
    return due_items[:max_count]

def generate_blind_quiz(error_item):
    """
    生成盲盒测试题面：抹去原答案与解题过程，只保留题干背景与错因提示
    """
    s_name = error_item.get("subject_name", "考研科目")
    e_type = error_item.get("error_type", "综合漏洞")
    title = error_item.get("title", "核心错题复测")
    q_content = error_item.get("question", "").strip()

    quiz_text = f"""
╭────────────────────────────────────────────────────────────────────────╮
│  🎯 【艾宾浩斯错题盲盒重测 · 闭环考核】                                │
│  科目: {s_name:<20} 错因预警: {e_type:<15}     │
╰────────────────────────────────────────────────────────────────────────╯

📌 【考核题目】: {title}
⏱️ 【历史记录时间】: {error_item.get('date')} ({error_item.get('days_ago', 0)} 天前)

📝 【题面核心内容与设问】：
{q_content}

💡 【盲盒重测规则】：
  1. 本界面已自动隐去历史推导过程与标准答案；
  2. 请在草稿纸上独立推导完整步骤；
  3. 推导完毕后，直接输入你的最终结果或解答核心步骤进行核对；
  4. 回答正确将自动标记为 [√ 已掌握] 并从待测队列出库！
"""
    return quiz_text.strip()

def mark_error_status(subject, file_name, title_keyword=None, new_status="已掌握", rating=None, passed=None, **kwargs):
    """
    回写错题卡片的掌握状态 + 联动更新复测节奏（FSRS-5 / 艾宾浩斯自适应）：
      - rating: "again" / "hard" / "good" / "easy"（可选）
      - passed: bool（可选，向后兼容：False -> again，True -> good）
      - new_status: 显式指定掌握状态（默认为 "已掌握"）
      - 调用方拿到 (ok, msg)；状态回写失败时返回 (False, 原因)，由上层提示学员
    """
    if title_keyword is None:
        title_keyword = kwargs.get("title", "")
    title_keyword = str(title_keyword or "")
    folder_name = SUBJECT_DIRS.get(subject, "01-数学")
    target_file = ROOT / folder_name / "错题本" / file_name
    if subject == "eng" and not target_file.exists():
        target_file = ROOT / folder_name / "错题与长难句本" / file_name

    if not target_file.exists():
        return False, f"未找到错题文件: {file_name}"

    content = target_file.read_text(encoding="utf-8", errors="ignore")
    today_d = date.today()

    # 锚定到指定标题的小节（DOTALL 直到下一个 ## 📌 或文件末尾）
    section_pattern = rf"(##\s+📌\s*\[(\d{{4}}-\d{{2}}-\d{{2}})\]\s*{re.escape(title_keyword)}.*?)(?=\n##\s+📌|\Z)"
    m_sec = re.search(section_pattern, content, re.DOTALL)
    if not m_sec:
        return False, f"未在文件 {file_name} 中找到匹配标题「{title_keyword}」的错题卡片，请检查拼写"

    section_text = m_sec.group(1)

    # 提取当前 stage
    cur_stage_m = re.search(r"-\s+\*\*复测节奏\*\*[：:]\s*`?stage=(\d+)`?", section_text)
    cur_stage = int(cur_stage_m.group(1)) if cur_stage_m else 0

    # 解析 rating
    if rating is None:
        if passed is not None:
            rating = "good" if passed else "again"
        elif new_status == "已掌握":
            rating = "good"
        else:
            rating = "again"
    else:
        rating = str(rating).strip().lower()

    # 计算新 stage 与到期日
    new_stage, next_due_d, interval_days = calc_fsrs_interval(stage=cur_stage, rating=rating, today=today_d)
    next_due_str = next_due_d.strftime("%Y-%m-%d")

    actual_status = "待复测" if rating == "again" else new_status

    # 行级精确替换：仅替换 [xxx] 这一对中括号内的内容
    new_section = re.sub(
        r"(-\s+\*\*掌握状态\*\*[：:]\s*`\[)[^\]]*(\])",
        rf"\g<1>{actual_status}\g<2>",
        section_text,
        count=1
    )
    # 替换复测节奏行
    new_section = re.sub(
        r"(-\s+\*\*复测节奏\*\*[：:]).*",
        f"\\g<1> `stage={new_stage}` · 下次到期 `{next_due_str}`（FSRS-5 自适应: {interval_days}天 | {rating}）",
        new_section,
        count=1
    )

    new_content = content.replace(section_text, new_section, 1)
    target_file.write_text(new_content, encoding="utf-8")
    if actual_status == "已掌握":
        msg = f"已掌握，下一次复测日 {next_due_str}（stage={new_stage}, 间隔 {interval_days} 天, 评级: {rating}）"
    else:
        msg = f"已重置为待复测，下次复测日 {next_due_str}（stage={new_stage}, 评级: {rating}）"
    return True, msg
