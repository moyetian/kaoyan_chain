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

# 仅作 config 缺失时的中性回退；实际科目名以 ky_config.json 的 study_plan 为准
_SUBJECT_NAME_FALLBACK = {
    "math": "数学",
    "eng": "英语",
    "pol": "思想政治理论",
    "pro": "专业课",
}
SUBJECT_NAMES = dict(_SUBJECT_NAME_FALLBACK)


def _extract_answer_tokens(text: str) -> list:
    """从作答或标准答案中抽取可比对的数字/分数 token（已归一化空格）。

    例：「最终结果为 -1/2」 → ['-1/2']；「答 888」 → ['888']
    """
    if not text:
        return []
    norm = re.sub(r"\s+", "", str(text))
    return re.findall(r"[-+]?\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?", norm)


def _text_answer_hit(std_ans: str, user_ans: str) -> bool:
    """文本型标准答案的宽松命中判定：归一化后包含，或核心关键词重合度 ≥ 60%。"""
    if not std_ans or not user_ans:
        return False

    def _norm(s):
        return re.sub(r"[\s，,。.;；:：、（）()\[\]【】\"'`*#>-]+", "", str(s)).lower()

    a, b = _norm(std_ans), _norm(user_ans)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    # 中文按 2-gram 计算重合度，英文按单词
    def _grams(s):
        if re.search(r"[\u4e00-\u9fff]", s):
            return {s[i:i + 2] for i in range(len(s) - 1)} or {s}
        return set(re.findall(r"[a-z0-9]+", s))
    ga, gb = _grams(a), _grams(b)
    if not ga or not gb:
        return False
    return len(ga & gb) / len(ga) >= 0.6


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
            # key_detail 仅为「错因描述」，用于回写错题本定位，绝不作为判卷答案
            "key_detail": item.get("detail", ""),
            # standard_answer 才是判卷唯一权威基准；缺失时该题转人工复核，绝不自动给分
            "standard_answer": str(item.get("standard_answer", "") or "").strip()
        })

    # 答案键只落地到密钥文件，不再内嵌进试卷 Markdown（此前 BASE64 内嵌可被直接解码还原）
    hidden_keys_json = json.dumps(answer_keys, ensure_ascii=False, indent=2)

    lines.append(f"<!-- EXAM_PAPER_ID: {paper_id} -->")
    lines.append(f"<!-- 参考答案与采分点已单独归档至 .memory/exam_keys/{paper_id}.json，试卷内不含答案 -->\n")

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
    need_review_titles = []
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

        # key_detail 是「错因描述」，仅用于人工复盘定位；
        # standard_answer 才是判卷的唯一权威基准。
        key_detail = str(k.get("key_detail", "")).strip()
        std_ans = str(k.get("standard_answer", "") or "").strip()

        # 核心答案比对逻辑 (选择题 / 数值分数 / 关键词)，绝不允许"写了就给满分"
        match_level = 0
        judge_basis = "未作答或明确放弃"
        if is_giveup or not has_content:
            match_level = 0
        else:
            # 1. 选择题选项比对
            choice_match = re.search(r"(?:^|[^A-Za-z])([A-D])(?:$|[^A-Za-z])", q_ans.upper())
            target_src = (std_ans or key_detail).upper()
            target_choice = re.search(r"(?:答案|选项)[：:\s]*([A-D])\b", target_src)
            if choice_match and target_choice:
                if choice_match.group(1) == target_choice.group(1):
                    match_level = 2
                    judge_basis = f"选择题命中 (标准 {target_choice.group(1)} / 作答 {choice_match.group(1)})"
                else:
                    match_level = 0
                    judge_basis = f"选择题不符 (标准 {target_choice.group(1)} / 作答 {choice_match.group(1)})"
            else:
                # 2. 数值 / 分数答案严格比对
                ans_tokens = _extract_answer_tokens(q_ans)
                key_tokens = _extract_answer_tokens(std_ans)
                if key_tokens:
                    if ans_tokens and any(t in key_tokens for t in ans_tokens):
                        match_level = 2
                        judge_basis = f"数值命中 (标准 {'/'.join(key_tokens)} / 作答 {'/'.join(ans_tokens)})"
                    else:
                        match_level = 0
                        judge_basis = f"数值不符 (标准 {'/'.join(key_tokens)} / 作答 {'/'.join(ans_tokens) or '无'})"
                elif std_ans:
                    # 3. 文本型标准答案：归一化后做包含 / 关键词重合度比对
                    if _text_answer_hit(std_ans, q_ans):
                        match_level = 2
                        judge_basis = "文本答案命中"
                    else:
                        match_level = 1
                        judge_basis = "文本答案不符，转人工复核"
                else:
                    # 无标准答案基准：坚决不给满分，转人工复核
                    match_level = 1
                    judge_basis = "⚠ 本题未登记标准答案，无法自动判分，已转人工复核"

        is_passed = (match_level == 2)
        item_score = 10 if is_passed else (5 if match_level == 1 else 0)
        total_score += item_score
        if match_level == 1:
            need_review_titles.append(f"第 {q_id} 题 {title}")

        status_str = "【合格 · 通过出库】" if is_passed else ("【待复核】" if match_level == 1 else "【需重新加固】")
        report_lines.append(f"• 第 {q_id} 题 [{title}]: {status_str} 得分: {item_score}/10")
        report_lines.append(f"  - 考查类型: {k.get('error_type')}")
        report_lines.append(f"  - 判定依据: {judge_basis}")

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
    if need_review_titles:
        report_lines.append(
            f"🔍 【待人工复核 {len(need_review_titles)} 题】: {'；'.join(need_review_titles)}")
        report_lines.append(
            f"   说明: 上述题目缺少标准答案登记或作答未命中标准答案，系统已拒绝对其自动满分，"
            f"请对照解析人工确认后在错题档案中补录「标准答案」字段。")
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
