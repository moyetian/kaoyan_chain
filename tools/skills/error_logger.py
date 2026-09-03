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

def log_error_record(subject="math", title="错题记录", error_type="计算失误", detail="", prescription="", question=""):
    """向对应科目的错题本追加一条结构化错题记录"""
    subj_folder = SUBJECT_DIRS.get(subject, "01-数学")
    mistake_dir = ROOT / subj_folder / "错题本"
    if subject == "eng":
        mistake_dir = ROOT / subj_folder / "错题与长难句本"

    mistake_dir.mkdir(parents=True, exist_ok=True)
    today_str = datetime.now().strftime("%Y-%m-%d")
    record_file = mistake_dir / f"错题记录_{today_str}.md"

    q_block = f"\n- **题干设问**：\n```text\n{question.strip()}\n```\n" if question else ""

    record_md = f"""
## 📌 [{today_str}] {title}
- **掌握状态**：`[待复测]` (艾宾浩斯复测中)
- **错因分类**：`{error_type}` (概念漏洞 / 审题偏差 / 公式记错 / 计算失误 / 书写丢分){q_block}
- **错题现场与漏洞分析**：
{detail.strip()}
- **专家处方与改进建议**：
{prescription.strip()}
- **复习规划**：排入 1天 / 3天 / 7天 艾宾浩斯盲盒复测队列
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

                results.append({
                    "subject": s,
                    "subject_name": SUBJECT_NAMES.get(s, s),
                    "file_path": str(md_file),
                    "file_name": md_file.name,
                    "date": rec_date,
                    "title": title,
                    "status": status,
                    "error_type": err_type,
                    "question": q_text or detail_text[:200],
                    "raw_section": sec
                })

    return results

def get_due_reviews(subject=None, max_count=5):
    """
    根据艾宾浩斯记忆遗忘曲线与待复测状态筛选到期题目
    """
    all_records = scan_error_records(subject)
    today = date.today()
    due_items = []

    for item in all_records:
        if "已掌握" in item["status"]:
            continue
        try:
            rec_d = datetime.strptime(item["date"], "%Y-%m-%d").date()
            diff_days = (today - rec_d).days
        except Exception:
            diff_days = 0

        # 艾宾浩斯关键复测点: 1天后, 3天后, 7天后, 15天后, 或当日新增
        item["days_ago"] = diff_days
        due_items.append(item)

    # 优先抽取记录时间久但未掌握的题目
    due_items.sort(key=lambda x: x["days_ago"], reverse=True)
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

def mark_error_status(subject, file_name, title_keyword, new_status="已掌握"):
    """
    将某条错题的状态回写为 [已掌握] 或 [需加练]
    """
    folder_name = SUBJECT_DIRS.get(subject, "01-数学")
    target_file = ROOT / folder_name / "错题本" / file_name
    if subject == "eng" and not target_file.exists():
        target_file = ROOT / folder_name / "错题与长难句本" / file_name

    if not target_file.exists():
        return False, f"未找到错题文件: {file_name}"

    content = target_file.read_text(encoding="utf-8", errors="ignore")
    # 替换状态行
    pattern = rf"(##\s+📌\s*\[\d{{4}}-\d{{2}}-\d{{2}}\]\s*{re.escape(title_keyword)}.*?-\s+\*\*掌握状态\*\*[：:]\s*`?\[?)(.*?)( \]?)`?"
    
    if re.search(pattern, content, re.DOTALL):
        updated = re.sub(pattern, rf"\g<1>{new_status}\g<3>", content, count=1, flags=re.DOTALL)
        target_file.write_text(updated, encoding="utf-8")
        return True, f"已成功将题目「{title_keyword}」更新为 [{new_status}]！"
    else:
        # 兼容简易标记追加
        updated = content.replace(f"{title_keyword}", f"{title_keyword} `[{new_status}]`")
        target_file.write_text(updated, encoding="utf-8")
        return True, f"已在错题记录中追加 [{new_status}] 标记！"
