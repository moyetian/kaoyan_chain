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
import random
from datetime import datetime, date
from pathlib import Path

try:  # 双导入路径兼容（项目同时存在 tools.X 与 X 两种导入方式）
    from ky_io import atomic_write_text  # noqa: E402
except ImportError:  # pragma: no cover
    from tools.ky_io import atomic_write_text  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent


# ════════════════════════════════════════════════════════════════
# [P0 修复] 答案密钥封装层 (Blind-box Anti-Peek)
# 目标：密钥文件落盘后「打开不可直接读出答案」，满足盲盒复测承诺。
# 方案：本机随机盐 (仅存 .memory/，不入库) + HMAC-SHA256(paper_id) 派生
#       密钥流 XOR 明文 JSON 后 Base64 封装为 ENC1 载荷。
# 说明：标准库零依赖约束下，这是「防直接阅读」的混淆层而非密码学强度
#       加密；配合 .gitignore 已阻断入库与 Pages 发布两条外泄路径。
# ════════════════════════════════════════════════════════════════

def _get_exam_key_salt() -> bytes:
    """获取（或首次生成）本机答案密钥加盐，存放于 .memory/exam_keys/.salt"""
    salt_path = ROOT / ".memory" / "exam_keys" / ".salt"
    try:
        if salt_path.exists():
            return bytes.fromhex(salt_path.read_text(encoding="utf-8").strip())
        salt_path.parent.mkdir(parents=True, exist_ok=True)
        salt = os.urandom(32)
        atomic_write_text(salt_path, salt.hex())
        return salt
    except (OSError, ValueError) as e:
        # 降级：固定盐仍可防止"打开即得答案"（防君子），但该盐硬编码在源码中，
        # 任何拿到源码的人都能解密；且换机/重装后将无法解密历史试卷答案。
        # [P1 修复] 原先静默降级，用户误以为答案已安全加密，现显式告警以便及时处置。
        print(f"[⚠️ 安全告警] 无法读写本机密钥盐 .memory/exam_keys/.salt "
              f"({type(e).__name__}: {e})，已降级为源码内置固定盐加密。"
              f"当前答案仅防直接查看、不具真实保密性，且换机后历史试卷答案将无法解密。"
              f"请检查 .memory 目录权限后重新生成试卷。")
        return b"kaoyan-chain-exam-key-fallback-salt"


def _derive_keystream(paper_id: str, length: int) -> bytes:
    """由 HMAC-SHA256(salt, paper_id) 计数器模式派生密钥流"""
    import hashlib
    import hmac
    digest = hmac.new(_get_exam_key_salt(), paper_id.encode("utf-8"), hashlib.sha256).digest()
    keystream = bytearray()
    counter = 0
    while len(keystream) < length:
        keystream.extend(hashlib.sha256(digest + counter.to_bytes(4, "big")).digest())
        counter += 1
    return bytes(keystream[:length])


def _seal_keys_payload(paper_id: str, plaintext_json: str) -> str:
    """将答案 JSON 加密封装为 ENC1 载荷"""
    import base64
    data = plaintext_json.encode("utf-8")
    keystream = _derive_keystream(paper_id, len(data))
    cipher = bytes(b ^ k for b, k in zip(data, keystream))
    return "ENC1:" + base64.b64encode(cipher).decode("ascii")


def _open_keys_payload(paper_id: str, payload: str):
    """解封 ENC1 载荷；非 ENC1 内容（历史明文/BASE64 兼容）原样返回。

    [P1 修复] 解封失败时返回 None（原先返回空字符串 ''），使调用方能区分
    「解封失败」与「内容本身为空」，避免 json.loads('') 抛错后被静默吞掉。
    """
    if not payload.startswith("ENC1:"):
        return payload
    import base64
    try:
        cipher = base64.b64decode(payload[5:].strip())
        keystream = _derive_keystream(paper_id, len(cipher))
        return bytes(b ^ k for b, k in zip(cipher, keystream)).decode("utf-8", errors="replace")
    except Exception:
        return None

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


def _norm_numeric_tokens(tokens: list) -> set:
    """将数值 token 归一化为可比较的浮点集合（无法解析的 token 直接丢弃）。

    使 -1/2 与 -0.5、2 与 2.0、1/2 与 0.5 判定等价，避免形式差异造成误判。
    """
    out = set()
    for t in tokens or []:
        try:
            if "/" in t:
                a, _, b = t.partition("/")
                out.add(round(float(a) / float(b), 6))
            else:
                out.add(round(float(t), 6))
        except (ValueError, ZeroDivisionError):
            continue
    return out


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


def _load_whitelist_cards(subject, need=1):
    """从各科「参考资料/题库切片_*.md」抽取已入库的白名单真题卡片。

    背景（[P0 修复] 组卷题源闭环）：切片入库管道 (ky ingest) 产出的题卡此前
    未被组卷引擎消费，导致即便真题已入库，ky exam 仍输出占位自拟题，
    「白名单题源抽题门禁」形同虚设。本函数补上这条数据链路。

    题卡由 material_ingestion 生成，格式约定：
      ### 【题号 N】题型（满分: X 分）
      - **【题源出处】**：`来源名`
      #### 1. 试题原题
      <题干正文（可多行，含选项）>
    """
    subj_name = get_subject_name(subject, SUBJECT_NAMES.get(subject, subject))
    subj_folder = SUBJECT_DIRS.get(subject, "01-数学")
    ref_dir = ROOT / subj_folder / "参考资料"
    if not ref_dir.exists():
        return []

    cards = []
    for slice_file in sorted(ref_dir.glob("题库切片_*.md")):
        try:
            txt = slice_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for blk in txt.split("### 【题号")[1:]:
            m_src = re.search(r"【题源出处】\*\*[：:]\s*`([^`]+)`", blk)
            source = m_src.group(1).strip() if m_src else slice_file.stem
            m_type = re.search(r"】\s*(.+?)（满分[:：]\s*([0-9.]+)\s*分", blk.splitlines()[0] if blk.splitlines() else "")
            q_type = m_type.group(1).strip() if m_type else "真题"
            score = m_type.group(2).strip() if m_type else ""
            m_q = re.search(r"####\s*\d+\s*[.、]?\s*试题原题\s*\n(.*?)(?=\n-{3,}|\Z)", blk, re.DOTALL)
            if not m_q:
                continue
            question = m_q.group(1).strip()
            if len(question) < 8:
                continue
            cards.append({
                "subject": subject,
                "subject_name": subj_name,
                "file_name": source,
                "title": f"白名单真题 · {q_type}" + (f"（{score} 分）" if score else ""),
                "error_type": "真题演练",
                "date": "",
                "question": question,
                "detail": f"题源出处: {source}",
                # 切片题卡不含答案 (答案归档于 .memory/exam_keys)，判卷端按「转人工复核」契约处理
                "standard_answer": "",
                "stage": 0,
                "is_whitelist_card": True,
            })

    random.shuffle(cards)
    return cards[:max(1, need)]


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
                    # [P0 修复] 开放题严禁以「说明文字」冒充标准答案：
                    # 此前该字段存放免责声明，导致判卷时以其为基准做文本重合度比对
                    # ——学员认真作答恒为 0 分，而抄写该说明文字反而满分。
                    # 现统一置空并标记 grading_mode=open，交由人工/多模型复核通道处理。
                    "standard_answer": "",
                    "grading_mode": "open",
                    "stage": 0,
                    "is_synthetic": True
                })

    # 3.5 [P0 修复·题源闭环] 错题与雷达仍不足时，优先抽取已入库的白名单真题卡，
    #     只有在无任何题卡可用时才降级到合成题/占位题（守住「白名单题源抽题门禁」）。
    if len(selected_items) < count:
        for card in _load_whitelist_cards(subject, need=count - len(selected_items)):
            if len(selected_items) >= count:
                break
            add_unique(card)

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
            # [P0 修复] 同上一处：开放题不登记伪标准答案，改走复核通道
            "standard_answer": "",
            "grading_mode": "open",
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
            # [P0 修复] 同上一处：开放题不登记伪标准答案，改走复核通道
            "standard_answer": "",
            "grading_mode": "open",
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
            # [修复] 开放题判分需要完整题面：多模型引擎据此抽取评分要点并比对作答。
            # 缺失该字段会让开放题恒被判为「题面缺失」而无法自动判分。
            "question": str(item.get("question", "") or "").strip(),
            "file_name": item.get("file_name", ""),
            # key_detail 仅为「错因描述」，用于回写错题本定位，绝不作为判卷答案
            "key_detail": item.get("detail", ""),
            # standard_answer 才是判卷唯一权威基准；缺失时该题转人工复核，绝不自动给分
            "standard_answer": str(item.get("standard_answer", "") or "").strip(),
            # [P0 修复] 透传判分模式：open=开放论述/推导题（无唯一数值解），
            # 判卷端据此给出「转复核而非答错」的准确提示，避免误导学员。
            "grading_mode": str(item.get("grading_mode", "exact") or "exact").strip()
        })

    # [P0 修复] 答案键只落地到密钥文件且加密存储 (ENC1)，不再内嵌进试卷 Markdown；
    # 此前 BASE64 内嵌可被直接解码还原，明文 JSON 落盘则打开即得答案，盲盒复测形同虚设。
    hidden_keys_json = json.dumps(answer_keys, ensure_ascii=False, indent=2)
    sealed_keys = _seal_keys_payload(paper_id, hidden_keys_json)

    lines.append(f"<!-- EXAM_PAPER_ID: {paper_id} -->")
    lines.append(f"<!-- 参考答案与采分点已加密归档至 .memory/exam_keys/{paper_id}.json (ENC1)，试卷内不含答案 -->\n")

    full_content = "\n".join(lines)
    saved_path = None

    # 保存中央加密答案库
    key_dir = ROOT / ".memory" / "exam_keys"
    key_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text((key_dir / f"{paper_id}.json"), sealed_keys)

    if save_file:
        target_dir = ROOT / subj_folder / "错题本"
        if subject == "eng":
            target_dir = ROOT / subj_folder / "错题与长难句本"
        target_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"自测卷_{today_str}_{paper_id}.md"
        out_file = target_dir / file_name
        atomic_write_text(out_file, full_content)
        saved_path = str(out_file)

        # 保存同目录伴随密钥文件 (隐藏文件，同样加密存储，防直接阅读)
        companion_file = target_dir / f".{file_name}.keys.json"
        atomic_write_text(companion_file, sealed_keys)

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


#: 开放题判分不可用时的统一兜底话术（明确「不代表作答错误」，避免误导学员）
_OPEN_REVIEW_FALLBACK = (
    "⚠ 开放题（论述/推导）无唯一数值解且未登记标准答案，"
    "已转人工复核（本次不计分，不代表作答错误）")


def _grade_open_by_llm(key_item: dict, student_answer: str, subject: str):
    """调用开放题多模型判分引擎。

    遵循「失败必须显式、绝不臆造分数」原则：模块缺失、未启用、无 API 或任何异常，
    一律回落 match_level=1（转人工复核），绝不因判分失败而给学员 0 分结论。

    Returns:
        (match_level, judge_basis)
    """
    try:
        from skills import open_grader
    except Exception:
        try:
            from tools.skills import open_grader
        except Exception:
            return 1, _OPEN_REVIEW_FALLBACK

    try:
        og = open_grader.grade_open_question(
            question=str(key_item.get("question", "") or ""),
            student_answer=student_answer,
            subject=str(key_item.get("subject", subject) or subject),
            reference_answer=str(key_item.get("standard_answer", "") or ""),
            key_points=key_item.get("key_points"),
        )
        if og.match_level == 2:
            return 2, f"多模型复核通过：{og.reason}"
        if og.match_level == 0:
            return 0, f"多模型复核未通过：{og.reason}"
        return 1, (og.reason or _OPEN_REVIEW_FALLBACK)
    except Exception as e:
        return 1, f"⚠ 多模型复核不可用（{type(e).__name__}），已转人工复核"


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
    # [P1 修复] 记录各读取通道失败原因，使密钥不可读时能在报告中给出可诊断的线索
    key_read_errors = []
    # 1. 尝试从 content 提取 paper_id 并查找中央密钥库
    paper_id_m = re.search(r"<!--\s*EXAM_PAPER_ID:\s*([a-zA-Z0-9_\-]+)\s*-->", content)
    p_id = paper_id_m.group(1).strip() if paper_id_m else ""
    if p_id:
        central_key_p = ROOT / ".memory" / "exam_keys" / f"{p_id}.json"
        if central_key_p.exists():
            try:
                # [P0 修复] 密钥文件为 ENC1 加密载荷，需先解封；历史明文 JSON 由 _open_keys_payload 原样透传兼容
                raw_keys = _open_keys_payload(p_id, central_key_p.read_text(encoding="utf-8"))
                if raw_keys is None:
                    key_read_errors.append(
                        f"中央密钥库 {central_key_p.name} 为 ENC1 载荷但解封失败"
                        f"（本机密钥盐 .memory/exam_keys/.salt 可能已丢失或被替换）")
                else:
                    try:
                        keys = json.loads(raw_keys)
                    except Exception as e:
                        # 解封本身不会抛错（异或解密恒成功），盐不匹配时产出的是乱码，
                        # 在此落到 JSON 解析失败。据此给出可操作的定位提示。
                        hint = ""
                        if raw_keys and not raw_keys.lstrip().startswith(("{", "[")):
                            hint = "；解封结果不是合法 JSON，极可能是本机密钥盐不匹配"
                        key_read_errors.append(
                            f"中央密钥库 {central_key_p.name} 解析失败: "
                            f"{type(e).__name__}: {e}{hint}")
            except Exception as e:
                key_read_errors.append(f"中央密钥库 {central_key_p.name} 读取失败: {type(e).__name__}: {e}")
        else:
            key_read_errors.append(f"中央密钥库文件不存在: {central_key_p.name}")

    # 2. 尝试从同目录伴随密钥文件读取
    if not keys and file_path:
        comp_path = file_path.parent / f".{file_path.name}.keys.json"
        if comp_path.exists():
            try:
                # 伴生文件名形如 .自测卷_<日期>_<paper_id>.md.keys.json，可反解 paper_id 用于解封
                comp_pid = p_id
                if not comp_pid:
                    fn_m = re.search(r"_([A-Za-z0-9_\-]+)\.md\.keys\.json$", comp_path.name)
                    comp_pid = fn_m.group(1) if fn_m else ""
                raw_keys = _open_keys_payload(comp_pid, comp_path.read_text(encoding="utf-8"))
                if raw_keys is None:
                    key_read_errors.append(f"伴随密钥文件 {comp_path.name} 解封失败")
                else:
                    keys = json.loads(raw_keys)
            except Exception as e:
                key_read_errors.append(f"伴随密钥文件 {comp_path.name} 解析失败: {type(e).__name__}: {e}")
        else:
            key_read_errors.append(f"伴随密钥文件不存在: {comp_path.name}")

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
                except Exception as e:
                    keys = []
                    key_read_errors.append(f"试卷内嵌密钥(BASE64)解析失败: {type(e).__name__}: {e}")
            else:
                try:
                    keys = json.loads(raw_k)
                except Exception as e:
                    keys = []
                    key_read_errors.append(f"试卷内嵌密钥(明文)解析失败: {type(e).__name__}: {e}")
        else:
            key_read_errors.append("试卷内容中未找到 EXAM_ANSWER_KEYS 内嵌注释")

    # 兼容历史数据：密钥结构异常（非列表）时视为未取到
    if keys and not isinstance(keys, list):
        keys = []

    # [P0 修复] 三个读取通道均未取到答案密钥时必须明确失败，严禁产出 0 分报告。
    # 此前 keys 为空仍继续计分，且 max_score 兜底为 100，最终返回 success=True + 0/100 分
    # 并给出「仍有薄弱盲区未突破、已重置回第一复测周期」的错误结论，
    # 学员会误以为自己全部答错，实际是密钥不可读，属于危险的静默失败。
    if not keys:
        err_lines = [
            "=" * 60,
            "  ⚠️ 无法判分：未读取到本卷答案密钥，已拒绝自动评分",
            "=" * 60,
            f"试卷文件: {file_path.name if file_path else '（内联文本，非文件路径）'}",
            f"试卷编号: {p_id or '（未能从试卷中解析 EXAM_PAPER_ID）'}",
            "",
            "【诊断信息】各读取通道实际结果：",
        ] + [f"    - {e}" for e in (key_read_errors or ["（无可用诊断信息）"])] + [
            "",
            "可能原因：",
            "  1. 中央密钥库 .memory/exam_keys/<试卷编号>.json 缺失或被清理；",
            "  2. 密钥为 ENC1 加密载荷，但本机密钥盐 .memory/exam_keys/.salt 丢失或被替换；",
            "  3. 试卷内嵌的 EXAM_ANSWER_KEYS 注释被破坏。",
            "",
            "处理建议：确认 .salt 与密钥文件完整后重新判卷；",
            "          本次不给出任何分数结论，也不会回写错题复测周期。",
            "=" * 60,
        ]
        return {
            "success": False,
            "msg": "未读取到本卷答案密钥，已拒绝自动判分（避免误判 0 分）",
            "score": 0,
            "total_score": 0,
            "pass_rate": 0.0,
            "accuracy": 0.0,
            "updated_records": [],
            "report": "\n".join(err_lines),
        }

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
    # [P1 修复] 题号分隔符中的 "." 必须排除小数点场景：
    # 原先 "0." 会被当作下一题的题号标记，导致作答 "-0.5" 被截断为 "-"，
    # 进而一切小数答案（0.5 / 3.14 / -0.5）恒被判 0 分。
    # 现要求分隔符后不紧跟数字（真题号形如 "2、"、"2. " 后接文字）。
    _NUM_SEP = r"\d+[、.．](?![0-9])"
    chunks = re.findall(
        rf"(?:(?:第\s*(\d+)\s*题|(\d+)[、.．](?![0-9]))\s*"
        rf"([\s\S]*?)(?=(?:第\s*\d+\s*题|{_NUM_SEP})|\Z))",
        ans_clean)
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
            # 1. 选择题选项严格比对（仅当题干或标准答案明确为选择题时）
            target_src = std_ans.upper()
            target_choice_m = re.search(r"(?:答案|选项)[：:\s]*([A-D])\b", target_src) or re.search(r"^[A-D]$", target_src.strip())
            target_choice = target_choice_m.group(1) if target_choice_m else None
            
            choice_match = None
            if target_choice:
                ans_upper = q_ans.strip().upper()
                c_m = re.search(r"(?:选|答案|选项)[：:\s]*([A-D])\b", ans_upper)
                if not c_m and len(ans_upper) <= 12:
                    c_m = re.search(r"^[^A-Z]*\b([A-D])\b", ans_upper)
                choice_match = c_m.group(1) if c_m else None

            if choice_match and target_choice:
                if choice_match == target_choice:
                    match_level = 2
                    judge_basis = f"选择题命中 (标准 {target_choice} / 作答 {choice_match})"
                else:
                    match_level = 0
                    judge_basis = f"选择题不符 (标准 {target_choice} / 作答 {choice_match})"
            else:
                # 2. 数值 / 分数答案严格比对
                # [P0 修复] 原判据 any(t in key_tokens for t in ans_tokens) 属「反向命中」：
                # 学员作答中只要有任意一个数字撞上标准答案即判满分，而标准答案常含公式噪声
                # （如 ∫x^2dx=x^3/3+C 中的 2），导致只写「2」也能满分，虚高通过率。
                # 现改为覆盖式判定：学员作答必须覆盖标准答案的全部关键数值，
                # 并在归一化后比较（-1/2 与 -0.5 等价）。宁可转复核，绝不虚高给分。
                ans_tokens = _extract_answer_tokens(q_ans)
                key_tokens = _extract_answer_tokens(std_ans)
                if key_tokens:
                    key_nums = _norm_numeric_tokens(key_tokens)
                    ans_nums = _norm_numeric_tokens(ans_tokens)
                    if key_nums and ans_nums and key_nums.issubset(ans_nums):
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
                    # 无标准答案基准：坚决不给分，转人工复核
                    match_level = 1
                    if str(k.get("grading_mode", "")).lower() == "open":
                        # 开放题：交由多模型判分引擎（未启用/异常均回落为「转人工复核」）
                        match_level, judge_basis = _grade_open_by_llm(k, q_ans, subject)
                    else:
                        judge_basis = "⚠ 本题未登记标准答案，无法自动判分，已转人工复核 (0分)"

        is_passed = (match_level == 2)
        # [P1 修复] 待人工复核的无标准答案题目不给分 (0分)，杜绝虚高通过率
        item_score = 10 if is_passed else 0
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
