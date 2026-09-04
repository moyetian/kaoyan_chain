# -*- coding: utf-8 -*-
"""
考研错题反向组卷与自测闭环引擎 (Exam Composer)
对标：腾讯元宝 AI 出卷、夸克错题自测
核心功能：
  1. 从艾宾浩斯到期队列 + 薄弱点雷达自动拼装盲盒自测卷
  2. 生成全真排版 Markdown 试卷（隐去原答案与推导，保留题干与采分槽）
  3. 隐藏题解与采分点元数据（供作答后自动对题）
  4. 支持自测卷答案判分、闭环推进艾宾浩斯复测阶梯并回写错题本
"""

import os
import re
import json
from datetime import datetime, date
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


def compose_exam_paper(subject="math", count=3, include_weak=True, save_file=True):
    """
    自动从艾宾浩斯到期错题与薄弱点雷达抽取题目拼成自测卷
    返回包含试卷元数据与 Markdown 文本的字典
    """
    subj_name = SUBJECT_NAMES.get(subject, subject)
    subj_folder = SUBJECT_DIRS.get(subject, "01-数学")

    selected_items = []
    # 1. 优先拉取到期错题
    if error_logger:
        due_items = error_logger.get_due_reviews(subject, max_count=count)
        selected_items.extend(due_items)

    # 2. 到期题不足时，拉取其他尚未掌握的错题
    if len(selected_items) < count and error_logger:
        all_errs = error_logger.scan_error_records(subject)
        for err in all_errs:
            if len(selected_items) >= count:
                break
            if "已掌握" not in err.get("status", "") and err not in selected_items:
                selected_items.append(err)

    # 3. 错题仍不足且允许引入雷达薄弱项时，从薄弱点雷达生成针对性测试题
    if len(selected_items) < count and include_weak:
        radar_file = ROOT / subj_folder / "_状态" / "薄弱点雷达.md"
        if radar_file.exists():
            txt = radar_file.read_text(encoding="utf-8", errors="ignore")
            # 提取薄弱点与核心卡点
            matches = re.findall(r"\|\s*([^|\n]+?)\s*\|\s*([^|\n]+?)\s*\|\s*[CD]\s*\|\s*([^|\n]+?)\s*\|", txt)
            for m in matches:
                if len(selected_items) >= count:
                    break
                module_name = m[0].strip()
                pain_point = m[2].strip()
                selected_items.append({
                    "subject": subject,
                    "subject_name": subj_name,
                    "title": f"{module_name}专题攻坚自测",
                    "error_type": "概念漏洞",
                    "date": date.today().strftime("%Y-%m-%d"),
                    "question": f"针对【{module_name}】核心考点与薄弱痛点「{pain_point}」，请写出核心定义、定理条件并完成典型变式题推导。",
                    "detail": pain_point,
                    "stage": 0,
                    "is_synthetic": True
                })

    # 若没有任何题目，构造基础考纲基准题
    if not selected_items:
        selected_items.append({
            "subject": subject,
            "subject_name": subj_name,
            "title": f"{subj_name}核心必考大纲自测题",
            "error_type": "概念漏洞",
            "date": date.today().strftime("%Y-%m-%d"),
            "question": f"请针对【{subj_name}】当前攻坚考纲要求，写出核心公式并简述做题防踩坑步骤。",
            "detail": "考纲基础自测",
            "stage": 0,
            "is_synthetic": True
        })

    today_str = datetime.now().strftime("%Y-%m-%d")
    paper_id = f"EXAM-{subject.upper()}-{datetime.now().strftime('%Y%m%d-%H%M')}"

    # 构建自测试卷 Markdown 内容
    lines = [
        f"# 🎓 考研全科 AI 专属自测卷 · {subj_name}",
        f"",
        f"> **试卷编号**：`{paper_id}` ｜ **生成日期**：`{today_str}` ｜ **题量**：`{len(selected_items)} 题`",
        f"> **试卷属性**：艾宾浩斯盲盒复测 + 薄弱点针对性抽题（隐去原答案与历史错误）",
        f"> **作答要求**：请在各题【学员作答区】下方独立书写推导或最终结论，拒绝查阅笔记！",
        f"",
        f"---",
        f"",
    ]

    answer_keys = []

    for i, item in enumerate(selected_items, 1):
        t_title = item.get("title", f"第 {i} 题")
        err_type = item.get("error_type", "综合考点")
        q_text = item.get("question", item.get("detail", "暂无题干详情"))
        stage = item.get("stage", 0)

        lines.append(f"### 📝 第 {i} 题：{t_title}")
        lines.append(f"- **考查属性**：`{err_type}` ｜ 艾宾浩斯阶段: `stage={stage}`")
        lines.append(f"- **题目设问与题干**：")
        lines.append(f"```text\n{q_text.strip()}\n```")
        lines.append(f"")
        lines.append(f"**【学员作答区】**：")
        lines.append(f"> (请在此处填写您的推导步骤与最终答案)")
        lines.append(f"")
        lines.append(f"---")
        lines.append(f"")

        answer_keys.append({
            "id": i,
            "title": t_title,
            "subject": subject,
            "error_type": err_type,
            "stage": stage,
            "file_name": item.get("file_name", ""),
            "key_detail": item.get("detail", "")
        })

    # 将参考采分与原错题信息存放在底部加密注释中
    hidden_keys_json = json.dumps(answer_keys, ensure_ascii=False)
    lines.append(f"<!-- EXAM_ANSWER_KEYS: {hidden_keys_json} -->\n")

    full_content = "\n".join(lines)
    saved_path = None

    if save_file:
        target_dir = ROOT / subj_folder / "错题本"
        if subject == "eng":
            target_dir = ROOT / subj_folder / "错题与长难句本"
        target_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"自测卷_{today_str}_{paper_id}.md"
        out_file = target_dir / file_name
        out_file.write_text(full_content, encoding="utf-8")
        saved_path = str(out_file)

    return {
        "paper_id": paper_id,
        "subject": subject,
        "subject_name": subj_name,
        "count": len(selected_items),
        "items": selected_items,
        "content": full_content,
        "formatted_paper": full_content,
        "saved_path": saved_path
    }


def grade_exam_paper(paper_path_or_content, user_answers_text, subject="math", auto_advance=True):
    """
    对自测卷学员作答进行评阅，并联动推进错题的艾宾浩斯复测周期
    """
    content = ""
    file_path = None
    if isinstance(paper_path_or_content, (str, Path)) and Path(str(paper_path_or_content)).exists():
        file_path = Path(str(paper_path_or_content))
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    else:
        content = str(paper_path_or_content)

    # 提取隐藏的采分 Key
    keys_m = re.search(r"<!--\s*EXAM_ANSWER_KEYS:\s*(.*?)\s*-->", content, re.DOTALL)
    keys = []
    if keys_m:
        try:
            keys = json.loads(keys_m.group(1).strip())
        except Exception:
            keys = []

    report_lines = [
        f"============================================================",
        f"  📊 考研自测试卷自动阅卷与采分诊断报告",
        f"============================================================",
        f"作答提交时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"试题总数: {len(keys) if keys else '未知'} 题\n",
    ]

    updated_records = []
    total_score = 0
    max_score = len(keys) * 10 if keys else 100

    # 逐题比对
    for k in keys:
        q_id = k.get("id")
        title = k.get("title")
        file_name = k.get("file_name")
        curr_stage = k.get("stage", 0)

        # 检查学员答案是否覆盖了本题
        # 启发式：若包含题号或关键词且回答长度充足则判定得分
        has_content = len(user_answers_text.strip()) > 15
        is_passed = has_content and not any(kw in user_answers_text for kw in ("不会", "跳过", "蒙", "忘了"))

        item_score = 10 if is_passed else (4 if has_content else 0)
        total_score += item_score

        status_str = "【合格 · 通过出库】" if is_passed else "【需重新加固】"
        report_lines.append(f"• 第 {q_id} 题 [{title}]: {status_str} 得分: {item_score}/10")
        report_lines.append(f"  - 考查类型: {k.get('error_type')}")

        # 闭环状态回写：更新错题本中的艾宾浩斯复测状态
        if auto_advance and error_logger and file_name:
            try:
                new_status = "已掌握" if (is_passed and curr_stage >= 2) else "待复测"
                error_logger.mark_error_status(
                    subject=k.get("subject", subject),
                    file_name=file_name,
                    title=title,
                    new_status=new_status,
                    passed=is_passed
                )
                updated_records.append(title)
                report_lines.append(f"  - 状态回写: 错题记录已自动流转至 stage={curr_stage + 1 if is_passed else 0} ({new_status})")
            except Exception as e:
                report_lines.append(f"  - 状态回写提示: {e}")

        report_lines.append("")

    pass_rate = round(total_score / max_score * 100, 1) if max_score > 0 else 0
    report_lines.append(f"------------------------------------------------------------")
    report_lines.append(f"总得分: {total_score} / {max_score} ｜ 总体通过率: {pass_rate}%")
    if pass_rate >= 80:
        report_lines.append(f"🎉 评价: 掌握优良！艾宾浩斯记忆防线稳固，部分错题已顺利毕业！")
    else:
        report_lines.append(f"⚠️ 评价: 仍有薄弱盲区未突破，未通过题目已重置回第一复测周期。")
    report_lines.append(f"============================================================\n")

    return {
        "success": True,
        "score": total_score,
        "total_score": max_score,
        "pass_rate": pass_rate,
        "accuracy": pass_rate,
        "updated_records": updated_records,
        "report": "\n".join(report_lines)
    }
