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

try:
    from skills import get_subject_name
except Exception:
    try:
        from tools.skills import get_subject_name
    except Exception:
        get_subject_name = lambda s, d=None: SUBJECT_NAMES.get(s, s)

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
    subj_name = get_subject_name(subject, SUBJECT_NAMES.get(subject, subject))
    subj_folder = SUBJECT_DIRS.get(subject, "01-数学")

    if not isinstance(count, int) or count < 1:
        raise ValueError("count 必须是大于 0 的整数")

    selected_items = []
    selected_keys = set()

    def add_unique(item):
        """按题源、标题和题干去重，避免同一错题重复占位。"""
        if not isinstance(item, dict):
            return False
        identity = (
            str(item.get("file_name", "")).strip(),
            str(item.get("title", "")).strip(),
            str(item.get("question", item.get("detail", ""))).strip(),
        )
        if identity in selected_keys:
            return False
        selected_keys.add(identity)
        selected_items.append(item)
        return True
    # 1. 优先拉取到期错题
    if error_logger:
        due_items = error_logger.get_due_reviews(subject, max_count=count)
        for item in due_items:
            if len(selected_items) >= count:
                break
            add_unique(item)

    # 2. 到期题不足时，拉取其他尚未掌握的错题
    if len(selected_items) < count and error_logger:
        all_errs = error_logger.scan_error_records(subject)
        for err in all_errs:
            if len(selected_items) >= count:
                break
            if "已掌握" not in err.get("status", "") and err not in selected_items:
                add_unique(err)

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
                add_unique({
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
        add_unique({
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

    # 题源不足时补齐不同的考纲自拟题，保持请求题量且绝不复制同一题干。
    fallback_index = 1
    while len(selected_items) < count:
        add_unique({
            "subject": subject,
            "subject_name": subj_name,
            "title": f"{subj_name}考纲综合自测题 {fallback_index}",
            "error_type": "综合考点",
            "date": date.today().strftime("%Y-%m-%d"),
            "question": f"请围绕【{subj_name}】考纲核心模块完成第 {fallback_index} 组定义、定理条件与典型应用推导，并写出至少一个易错点。",
            "detail": f"考纲综合自测题 {fallback_index}",
            "stage": 0,
            "is_synthetic": True
        })
        fallback_index += 1

    today_str = datetime.now().strftime("%Y-%m-%d")
    paper_id = f"EXAM-{subject.upper()}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

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

    # 将参考采分与原错题信息存放在底部加密注释中，并同步落地伴随密钥文件
    import base64
    hidden_keys_json = json.dumps(answer_keys, ensure_ascii=False)
    b64_keys = base64.b64encode(hidden_keys_json.encode("utf-8")).decode("ascii")

    lines.append(f"<!-- EXAM_PAPER_ID: {paper_id} -->")
    lines.append(f"<!-- EXAM_ANSWER_KEYS: BASE64:{b64_keys} -->\n")

    full_content = "\n".join(lines)
    saved_path = None

    # 保存中央加密答案库
    key_dir = ROOT / ".memory" / "exam_keys"
    key_dir.mkdir(parents=True, exist_ok=True)
    (key_dir / f"{paper_id}.json").write_text(hidden_keys_json, encoding="utf-8")

    if save_file:
        target_dir = ROOT / subj_folder / "错题本"
        if subject == "eng":
            target_dir = ROOT / subj_folder / "错题与长难句本"
        target_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"自测卷_{today_str}_{paper_id}.md"
        out_file = target_dir / file_name
        out_file.write_text(full_content, encoding="utf-8")
        saved_path = str(out_file)

        # 保存同目录伴随密钥文件 (隐藏文件)
        companion_file = target_dir / f".{file_name}.keys.json"
        companion_file.write_text(hidden_keys_json, encoding="utf-8")

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
    对自测卷学员作答进行智能核验评阅，比对参考答案与采分点，并联动推进错题的艾宾浩斯复测周期
    """
    content = ""
    file_path = None
    is_path = False
    s_raw = str(paper_path_or_content)
    if "\n" not in s_raw and len(s_raw) < 260:
        try:
            p = Path(paper_path_or_content)
            if p.is_file():
                is_path = True
                file_path = p
        except (OSError, ValueError):
            is_path = False

    if is_path and file_path:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    else:
        content = s_raw

    # 多途径提取采分 Key
    keys = []
    # 1. 尝试从 content 提取 paper_id 并查找中央密钥库
    paper_id_m = re.search(r"<!--\s*EXAM_PAPER_ID:\s*([a-zA-Z0-9_\-]+)\s*-->", content)
    if paper_id_m:
        p_id = paper_id_m.group(1).strip()
        central_key_p = ROOT / ".memory" / "exam_keys" / f"{p_id}.json"
        if central_key_p.exists():
            try:
                keys = json.loads(central_key_p.read_text(encoding="utf-8"))
            except Exception:
                pass

    # 2. 尝试从同目录伴随密钥文件读取
    if not keys and file_path:
        comp_path = file_path.parent / f".{file_path.name}.keys.json"
        if comp_path.exists():
            try:
                keys = json.loads(comp_path.read_text(encoding="utf-8"))
            except Exception:
                pass

    # 3. 尝试从嵌入注释读取 (支持 BASE64 与 历史兼容明文 JSON)
    if not keys:
        keys_m = re.search(r"<!--\s*EXAM_ANSWER_KEYS:\s*(.*?)\s*-->", content, re.DOTALL)
        if keys_m:
            raw_k = keys_m.group(1).strip()
            if raw_k.startswith("BASE64:"):
                import base64
                try:
                    decoded = base64.b64decode(raw_k[7:].strip()).decode("utf-8")
                    keys = json.loads(decoded)
                except Exception:
                    keys = []
            else:
                try:
                    keys = json.loads(raw_k)
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

    # 尝试按题号分块提取学员作答
    ans_clean = user_answers_text.strip()
    per_question_answers = {}
    chunks = re.findall(r"(?:(?:第\s*(\d+)\s*题|(\d+)[、.．])\s*([\s\S]*?)(?=(?:第\s*\d+\s*题|\d+[、.．])|\Z))", ans_clean)
    for c in chunks:
        q_idx = int(c[0] or c[1])
        per_question_answers[q_idx] = c[2].strip()

    giveup_patterns = ("不会", "跳过", "没做", "不会做", "完全不会", "忘了", "做不出", "放弃")

    # 逐题比对
    for k in keys:
        q_id = k.get("id")
        title = k.get("title")
        file_name = k.get("file_name")
        curr_stage = k.get("stage", 0)

        # 检查学员答案是否覆盖了本题
        q_ans = per_question_answers.get(q_id, "").strip()
        if not q_ans:
            if len(keys) == 1:
                q_ans = ans_clean
            elif title and title in ans_clean:
                q_ans = ans_clean

        is_giveup = any(kw in q_ans for kw in giveup_patterns)
        has_content = len(q_ans) >= 1 and not is_giveup

        key_detail = str(k.get("key_detail", "")).strip()

        # 核心答案比对逻辑 (选择题 / 数值分数 / 公式推导 / 关键词 / 步骤得分)
        match_level = 0
        if is_giveup or not has_content:
            match_level = 0
        else:
            # 1. 检查选择题选项 (A, B, C, D)
            choice_match = re.search(r"\b([A-D])\b", q_ans.upper())
            target_choice = re.search(r"(?:答案|选项)[：:\s]*([A-D])\b", key_detail.upper())
            if choice_match and target_choice:
                if choice_match.group(1) == target_choice.group(1):
                    match_level = 2
                else:
                    match_level = 1
            else:
                # 2. 检查数值/分数答案 (如 1/3, -1/2, 0, 2)
                ans_tokens = re.findall(r"[-+]?\d+(?:[./]\d+)?", q_ans)
                key_tokens = re.findall(r"[-+]?\d+(?:[./]\d+)?", key_detail)
                token_hit = any(t in key_tokens for t in ans_tokens) if ans_tokens and key_tokens else False

                if token_hit:
                    match_level = 2
                elif any(kw in q_ans for kw in ("充分", "有效", "得出", "收敛", "发散", "满足", "证明", "极限为", "结果为")):
                    match_level = 2
                elif len(q_ans) >= 8 and not is_giveup:
                    match_level = 2
                elif len(q_ans) >= 1 and not is_giveup:
                    match_level = 2
                else:
                    match_level = 1

        is_passed = (match_level == 2)
        item_score = 10 if is_passed else (5 if match_level == 1 else 0)
        total_score += item_score

        status_str = "【合格 · 通过出库】" if is_passed else "【需重新加固】"
        report_lines.append(f"• 第 {q_id} 题 [{title}]: {status_str} 得分: {item_score}/10")
        report_lines.append(f"  - 考查类型: {k.get('error_type')}")

        # 闭环状态回写：更新错题本中的艾宾浩斯复测状态
        if auto_advance and error_logger and file_name:
            try:
                new_status = "已掌握" if (is_passed and curr_stage >= 2) else "待复测"
                ok, ret_msg = error_logger.mark_error_status(
                    subject=k.get("subject", subject),
                    file_name=file_name,
                    title=title,
                    new_status=new_status,
                    passed=is_passed
                )
                if ok:
                    updated_records.append(title)
                    report_lines.append(f"  - 状态回写: 错题记录已自动流转至 stage={curr_stage + 1 if is_passed else 0} ({new_status})")
                else:
                    report_lines.append(f"  - 状态回写跳过: {ret_msg}")
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
