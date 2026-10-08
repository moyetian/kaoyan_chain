"""自测试卷判分流程；依赖由门面显式注入，保留原调用及测试替换契约。"""
import json
import re
from datetime import datetime
from pathlib import Path

try:  # [P0 题库身份链] 通过 registry 找回密钥路径，兼容旧 EXAM_PAPER_ID
    from skills.paper_registry import PaperRegistry
except ImportError:  # pragma: no cover
    from tools.skills.paper_registry import PaperRegistry


def _unique_mistake_title(title, question):
    """[NEW-2 修复·错题标题撞名] 同卷占位题/同类题标题原本完全相同
    （如三道"XX核心必考大纲自测题"），错题本里出现 N 个同名卡片，按标题
    锚定会锚错、人工翻阅也分不清。若标题内尚未携带题干预览，追加 question
    前 12 字；已带预览的不重复追加（幂等）。"""
    t = str(title or "").strip() or "错题"
    q = re.sub(r"\s+", " ", str(question or "").strip())
    if q:
        preview = q[:12]
        if preview and preview not in t:
            t = f"{t} · {preview}"
    return t


def _question_fingerprint(question):
    """题干指纹：去空白后取归一化文本的前 48 字，用于跨标题判重。"""
    return re.sub(r"\s+", "", str(question or ""))[:48]


# ── 数值型 / 文本型标准答案判别 ──────────────────────────────────────────
# [缺陷修复·分点编号误伤判分] 论述/简答的标准答案普遍带分点编号
# （（1）…（2）…（3）…），旧实现只要标准答案含**任何**数字就强制走数值分支
# （``key_nums.issubset(ans_nums)``），文本作答不可能"覆盖"这些编号 →
# stable 判 0 分「数值不符 (标准 1/2/3 / 作答 无)」，并被自动归档错题、
# 错因误归「概念漏洞」、排入 FSRS 队列。
# 现先剥离分点编号与数值 token，再看剩余的中文/字母实质内容：达到阈值即
# 判为**文本型题**（编号只是排版，不是待比对的答案），走文本比对。
_ENUM_MARK = re.compile(
    r"[（(【\[]?\s*(?:\d{1,2}|[一二三四五六七八九十]{1,3}|[①②③④⑤⑥⑦⑧⑨⑩])\s*"
    r"(?:[）)】\]]|[.．、:：])")
_NUM_TOKEN = re.compile(r"[-+]?\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?")
_SUBSTANTIVE_CHAR = re.compile(r"[A-Za-z\u4e00-\u9fff]")
_TEXT_TYPE_MIN_CHARS = 8


def _is_text_type_answer(std_ans: str) -> bool:
    """标准答案是否属于「文本型」（其数字只是分点编号而非待比对答案）。

    剥离分点编号（（1） / 1. / ① / 一、）与数值 token 后，若剩余中文/字母
    实质内容仍达 ``_TEXT_TYPE_MIN_CHARS`` 个字符，即判为文本型；否则维持
    数值型严格判定（保留 P0 防虚高：只写一个数字撞车不能蒙混满分）。
    """
    if not std_ans:
        return False
    body = _ENUM_MARK.sub(" ", str(std_ans))
    body = _NUM_TOKEN.sub(" ", body)
    return len(_SUBSTANTIVE_CHAR.findall(body)) >= _TEXT_TYPE_MIN_CHARS


def _enum_only_numbers(std_ans: str) -> bool:
    """标准答案里的数字是否**只是**分点编号（真正的答案写在编号后的正文里）。

    成立条件：答案中确实存在分点编号，且剥离编号与数值 token 后仍有
    ≥4 个中文/字母字符（说明编号后面真有正文）。
    纯数值答案（``888`` / ``-1/2``）必然不成立 —— 它们必须继续走严格
    数值判定，绝不因"乱写一通撞上一个数字"就蒙混满分（P0 防虚高）。
    """
    if not std_ans or not _ENUM_MARK.search(std_ans):
        return False
    body = _NUM_TOKEN.sub(" ", _ENUM_MARK.sub(" ", str(std_ans)))
    return len(_SUBSTANTIVE_CHAR.findall(body)) >= 4


def _find_existing_mistake_record(*, subject, title, question, error_logger=None):
    """[F8 幂等] 三级判重：查该科目错题本是否已有本题记录。

    返回命中记录的标题（供回执展示），未命中返回 ``None``。
    判重口径：
      1. 精确标题（含 FSRS 记录的标准「📌」标题行）；
      2. 标题含题干预览（``_unique_mistake_title`` 的追加片段）；
      3. 题干指纹（归一化题干前 48 字）出现在记录的题干设问/正文中。
    扫描失败一律返回 ``None``（按"未命中"处理，走既有新建路径 ——
    宁可重复归档也不能让闭环断裂）。
    """
    try:
        if error_logger is None:
            try:
                from error_logger import scan_error_records  # noqa: E402
            except ImportError:  # pragma: no cover - 兼容 tools. 包式导入
                from tools.skills.error_logger import scan_error_records  # type: ignore
            records = scan_error_records(subject)
        else:
            records = error_logger.scan_error_records(subject)
    except Exception:
        return None

    q_fp = _question_fingerprint(question)
    q_preview = re.sub(r"\s+", " ", str(question or "").strip())[:12]
    t_norm = str(title or "").strip()
    for rec in records or []:
        r_title = str(rec.get("title", "") or "").strip()
        r_q = str(rec.get("question", "") or "").strip()
        # ① 精确标题（含 FSRS 记录中生成的唯一化标题）
        if t_norm and r_title == t_norm:
            return r_title
        # ② 标题含题干预览（_unique_mistake_title 的追加片段）
        if q_preview and q_preview in r_title:
            return r_title
        # ③ 题干指纹：跨空白归一化后，查询题干指纹命中记录的题干
        if q_fp and q_fp in re.sub(r"\s+", "", r_q):
            return r_title or t_norm
    return None


def _item_full_score(card: dict) -> float:
    """单题满分：优先 score/points/分值/full_score，缺失或非法才回落 10 分。

    [P1 修复·分值不等权] 真题卷每题分值不等（2/5/15 分），一律按 10 分计
    会把总分与通过率算错。模块级定义使一致性校验链路可在测试中替换此口径。
    """
    for _f in ("score", "points", "分值", "full_score"):
        _v = card.get(_f)
        if _v is None:
            continue
        try:
            _fv = float(str(_v).strip())
            if _fv > 0:
                return _fv
        except (TypeError, ValueError):
            continue
    return 10.0


def grade_exam_paper(paper_path_or_content, user_answers_text, subject="math", auto_advance=True, *,
                     root, error_logger, open_keys, grade_open, extract_tokens, norm_tokens, text_hit):
    """
    对自测卷学员作答进行智能核验评阅，比对参考答案与采分点，并联动推进错题的 FSRS 复测周期
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
    source_id_m = re.search(r"<!--\s*SOURCE_PAPER_ID:\s*(PAPER-[a-fA-F0-9]+)\s*-->", content)
    source_paper_id = source_id_m.group(1).strip() if source_id_m else ""
    registry_record = None
    registry_key_path = None
    if p_id:
        try:
            matches = PaperRegistry(root).find_by_metadata("exam_paper_id", p_id)
            registry_record = matches[-1] if matches else None
            if registry_record and registry_record.get("key_path"):
                candidate = Path(registry_record["key_path"])
                registry_key_path = candidate if candidate.is_absolute() else Path(root) / candidate
        except Exception as e:
            key_read_errors.append(f"题库身份清单读取失败: {type(e).__name__}: {e}")
        if p_id and registry_record is None:
            # 旧试卷没有 registry 记录仍允许走原有三通道；把缺失变成
            # 可诊断信息，避免用户误以为 EXAM_PAPER_ID 已完成完整登记。
            key_read_errors.append(f"题库身份清单未登记试卷 {p_id}（兼容旧试卷继续尝试密钥文件）")
    elif source_paper_id:
        try:
            registry_record = PaperRegistry(root).get_paper(source_paper_id)
        except Exception as e:
            key_read_errors.append(f"题库身份清单读取失败: {type(e).__name__}: {e}")
        if registry_record:
            key_read_errors.append(
                f"题源 {source_paper_id} 已登记，但该入库材料没有 EXAM_PAPER_ID 或答案密钥；"
                "请先用真实题源组卷后再提交判卷")
    if p_id:
        central_key_candidates = []
        if registry_key_path:
            central_key_candidates.append(registry_key_path)
        central_key_candidates.append(root / ".memory" / "exam_keys" / f"{p_id}.json")
        # 同一逻辑路径只尝试一次；registry 记录通常指向中央库，但
        # 去重也覆盖了人工填写绝对路径的情况。
        seen_key_paths = set()
        for central_key_p in central_key_candidates:
            central_key_p = Path(central_key_p)
            if str(central_key_p).casefold() in seen_key_paths:
                continue
            seen_key_paths.add(str(central_key_p).casefold())
            if not central_key_p.exists():
                key_read_errors.append(f"中央密钥库文件不存在: {central_key_p.name}")
                continue
            try:
                # [P0 修复] 密钥文件为 ENC1 加密载荷，需先解封；历史明文 JSON 由 _open_keys_payload 原样透传兼容
                raw_keys = open_keys(p_id, central_key_p.read_text(encoding="utf-8"))
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
            if keys:
                break

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
                raw_keys = open_keys(comp_pid, comp_path.read_text(encoding="utf-8"))
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

    # [缺陷修复·密钥缺 id] 历史或手工编辑的密钥库可能缺 id 字段，
    # 此前 `int(k["id"])` 会 KeyError 直穿到用户、整卷无法判分。
    # 此处统一规范化：仅保留含合法整数 id 的条目，其余计入诊断信息。
    if keys:
        _valid, _invalid = [], []
        for _k in keys:
            try:
                int(_k.get("id"))
                _valid.append(_k)
            except (TypeError, ValueError, AttributeError):
                _invalid.append(str(_k)[:60])
        if _invalid:
            key_read_errors.append(f"密钥条目缺少合法 id，已跳过 {len(_invalid)} 条: {_invalid[:3]}")
        keys = _valid

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
    # [三审修复·2026-10-08 文案如实] 收集「未通过但未能回写复测状态」的真实原因，
    # 供汇总行分类归因。此前一律说「写入被拒绝或记录缺失，请检查权限」——而最常见的
    # 真实原因是题干缺失（卷面无题干文本 / 题卡无 question 字段），与权限无关、重试无用。
    unarchived_no_question = []   # 缺题干：["第 X 题 标题", ...]
    unarchived_errors = []        # 回写异常：["第 X 题（异常类型: 消息）", ...]
    total_score = 0.0
    # [K1 修复·判卷口径一致性] 逐题得分留痕，供返回前做「sum(单题得分)==总分」校验
    item_scores = []
    # [缺陷修复·口径不一致] 「待人工复核」的题目（无标准答案 / 开放题 / 答案未命中）
    # 在单题明细里明确写着「本次不计分」，却仍以 0 分计入总分与分母，
    # 使通过率被无谓拉低并触发"未通过"评价。现单独累计其满分，事后从分母中剔除。
    excluded_full = 0.0
    # [P1 修复·分值不等权] 此前全卷一律按「每题 10 分」计分（len(keys)*10），
    # 若题目自带 score 字段（真题卷常见 2/5/15 分不等），总分与通过率都会被算错。
    # 现优先读取每题的 score/points/分值，缺失才回落到 10 分（见模块级 _item_full_score）。
    max_score = sum(_item_full_score(k) for k in keys) if keys else 100.0

    from .exam_answers import parse_answers
    ans_clean, per_question_answers, parse_error = parse_answers(
        user_answers_text, {int(k["id"]) for k in keys})
    if parse_error:
        return {"success": False, "msg": parse_error, "report": parse_error,
                "score": 0, "total_score": 0, "accuracy": 0.0, "pass_rate": 0.0,
                "updated_records": []}

    giveup_patterns = ("不会", "跳过", "没做", "不会做", "完全不会", "忘了", "做不出", "放弃")

    # 逐题比对
    for k in keys:
        q_id = int(k.get("id"))
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

        is_giveup = q_ans.strip("。.!！ ") in giveup_patterns
        has_content = len(q_ans) >= 1 and not is_giveup

        # key_detail 是「错因描述」，仅用于人工复盘定位；
        # standard_answer 才是判卷的唯一权威基准。
        key_detail = str(k.get("key_detail", "")).strip()
        std_ans = str(k.get("standard_answer", "") or "").strip()

        # 核心答案比对逻辑 (选择题 / 数值分数 / 关键词)，绝不允许"写了就给满分"
        match_level = 0
        judge_basis = "未作答或明确放弃"
        if not has_content and not is_giveup:
            match_level = 1
            judge_basis = "未提交本题答案，待补交；不作为答错归档"
        elif is_giveup:
            match_level = 0
        else:
            # 1. 选择题选项严格比对（仅当题干或标准答案明确为选择题时）
            target_src = std_ans.upper()
            # [P1 修复·crash] 此前写作 re.search(r"^[A-D]$", ...) 无捕获组，
            # 而下一行统一调用 .group(1) → 当标准答案是裸单字母 "A"/"B" 时
            # 直接抛 IndexError: no such group，整卷判分崩溃。
            # 现统一补捕获组，裸字母与带前缀写法都能取到选项。
            target_choice_m = (re.search(r"(?:答案|选项)[：:\s]*([A-D])\b", target_src)
                               or re.search(r"^([A-D])$", target_src.strip()))
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
                ans_tokens = extract_tokens(q_ans)
                key_tokens = extract_tokens(std_ans)
                # [缺陷修复·分点编号误伤判分] 标准答案为文本型（含（1）（2）（3）
                # 之类分点编号）时不再走数值分支，改走文本比对；数值分支仅在
                # 标准答案确实是"数值型答案"时生效，防虚高语义不变。
                if key_tokens and not _is_text_type_answer(std_ans):
                    key_nums = norm_tokens(key_tokens)
                    ans_nums = norm_tokens(ans_tokens)
                    if key_nums and ans_nums and key_nums.issubset(ans_nums):
                        match_level = 2
                        judge_basis = f"数值命中 (标准 {'/'.join(key_tokens)} / 作答 {'/'.join(ans_tokens)})"
                    elif _enum_only_numbers(std_ans) and text_hit(std_ans, q_ans):
                        # [缺陷修复·分点编号误伤判分] 数值分支不命中时回退文本比对：
                        # 标准答案带分点编号、但正文短到未达文本型阈值时，学员用
                        # 正常文本作答仍应判「命中」，而不是 0 分并归档错题。
                        match_level = 2
                        judge_basis = "文本答案命中（标准答案含分点编号，已回退文本比对）"
                    else:
                        match_level = 0
                        judge_basis = f"数值不符 (标准 {'/'.join(key_tokens)} / 作答 {'/'.join(ans_tokens) or '无'})"
                elif std_ans:
                    # 3. 文本型标准答案：归一化后做包含 / 关键词重合度比对
                    if text_hit(std_ans, q_ans):
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
                        match_level, judge_basis = grade_open(k, q_ans, subject)
                    else:
                        judge_basis = "⚠ 本题未登记标准答案，无法自动判分，已转人工复核 (0分)"

        is_passed = (match_level == 2)
        # [P1 修复] 待人工复核的无标准答案题目不给分 (0分)，杜绝虚高通过率
        # [P1 修复·分值不等权] 单题得分取该题自身满分，不再写死 10 分
        item_full = _item_full_score(k)
        item_score = item_full if is_passed else 0.0
        if item_score == int(item_score):
            item_score = int(item_score)
        item_full_disp = int(item_full) if item_full == int(item_full) else item_full
        total_score += item_score
        item_scores.append(item_score)
        if match_level == 1:
            need_review_titles.append(f"第 {q_id} 题 {title}")
            # 未判分题：不计入分母，也不参与复测状态回写（它不是"答错"）
            excluded_full += item_full

        status_str = "【合格 · 通过出库】" if is_passed else ("【待复核】" if match_level == 1 else "【需重新加固】")
        report_lines.append(f"• 第 {q_id} 题 [{title}]: {status_str} 得分: {item_score}/{item_full_disp}")
        report_lines.append(f"  - 考查类型: {k.get('error_type')}")
        report_lines.append(f"  - 判定依据: {judge_basis}")

        # 闭环状态回写：更新错题本中的 FSRS 复测状态
        # 未判分题（match_level==1）不参与回写：它不是"答错"，而是"没有可判分的标准答案"。
        # [缺陷修复·无源文件题漏归档] 此前该分支额外要求 `file_name` 非空，
        # 而来自「考纲自拟」的占位题没有源文件 → 判负后被整段跳过、永不入队。
        # 现拆开：file_name 仅用于"更新既有记录"，"新建记录"不依赖它。
        if auto_advance and error_logger and match_level != 1:
            try:
                _archived = False
                _display_title = title
                if file_name:
                    new_status = "已掌握" if (is_passed and curr_stage >= 2) else "待复测"
                    ok, ret_msg = error_logger.mark_error_status(
                        subject=k.get("subject", subject),
                        file_name=file_name,
                        title=title,
                        new_status=new_status,
                        passed=is_passed
                    )
                    if ok:
                        _archived = True
                        report_lines.append(f"  - 状态回写: {ret_msg}")
                    elif is_passed:
                        report_lines.append(f"  - 状态回写跳过: {ret_msg}")
                # [缺陷修复·FSRS 闭环断裂] 此前只做"更新**既有**错题记录"。
                # 而记录通常根本还没被创建（其创建依赖 Agent 在对话中自觉调用
                # log_mistake），于是回写静默跳过 → 题目永不进入 FSRS 复测队列，
                # 但汇总行仍宣称"已重置回第一复测周期"——声称的状态变更从未发生。
                # 现改为：未通过且未找到既有记录时，由判卷链路**确定性新建**一条。
                # [F8 修复·重复判负幂等] 同一题再次判负（整卷重判 / 状态回写失败后
                # 重跑）时，旧实现**无条件再新建一条**，重复刷屏且污染 FSRS 队列与
                # 错因统计。现新建前先做三级判重：① 精确标题；② 标题+题干锚定；
                # ③「题干指纹」正文匹配。任一命中即视为已归档，跳过新建。
                if not _archived and not is_passed:
                    if str(k.get("question") or "").strip():
                        _already = _find_existing_mistake_record(
                            subject=k.get("subject", subject),
                            title=title,
                            question=k["question"],
                            error_logger=error_logger,
                        )
                        if _already:
                            _archived = True
                            _display_title = _already
                            report_lines.append(
                                f"  - 状态回写: 错题已在 FSRS 复测队列（{_already}），不重复归档")
                        else:
                            _display_title = _unique_mistake_title(title, k.get("question"))
                            record_msg = error_logger.log_error_record(
                                subject=k.get("subject", subject),
                                title=_display_title,
                                error_type=k.get("error_type", "概念漏洞"),
                                detail=(judge_basis or "") + "\n（本题由自测卷链路自动归档，原卷题面见上方【题干设问】）",
                                prescription="复测订正：先复现采分点步骤，再独立重做一遍。",
                                question=k["question"],
                            )
                            _archived = True
                            report_lines.append(f"  - 状态回写: 已新建错题记录并排入 FSRS 复测队列（{record_msg}）")
                    else:
                        # [三审修复·2026-10-08 文案如实] 题干缺失 → 新建归档没有题干
                        # 锚点（判重与展示都依赖它），只能跳过；如实收集原因供汇总行归因。
                        unarchived_no_question.append(f"第 {q_id} 题 {title}")
                if _archived:
                    updated_records.append(_display_title)
            except Exception as e:
                report_lines.append(f"  - 状态回写提示: {e}")
                if not is_passed:
                    # [三审修复·2026-10-08 文案如实] 未通过题的写入异常如实收集，
                    # 供汇总行区分「写入异常」与「缺题干」两类真实原因。
                    unarchived_errors.append(f"第 {q_id} 题（{type(e).__name__}: {e}）")

        report_lines.append("")

    # 分母只统计"真正被自动判分"的题；待复核题不计分（与单题明细口径一致）
    graded_max = max_score - excluded_full
    pass_rate = round(total_score / graded_max * 100, 1) if graded_max > 0 else 0

    # [K1 修复·判卷口径一致性] 渲染前内部一致性校验。违规**不抛异常**
    # （整卷报告绝不能因校验失败而丢失），只以告警行 + consistency_warnings 暴露。
    consistency_warnings = []
    if round(float(sum(item_scores)), 6) != round(float(total_score), 6):
        consistency_warnings.append(
            f"单题得分合计 {sum(item_scores)} ≠ 总分 {total_score}")
    if not (0 <= pass_rate <= 100):
        consistency_warnings.append(f"通过率越界: {pass_rate}% (应在 0~100)")
    if not (0 <= graded_max <= max_score):
        consistency_warnings.append(
            f"可判分满分越界: graded_max={graded_max} 不在 [0, {max_score}]")

    total_disp = int(total_score) if float(total_score) == int(total_score) else total_score
    max_disp = int(graded_max) if float(graded_max) == int(graded_max) else graded_max
    report_lines.append(f"------------------------------------------------------------")
    report_lines.append(f"总得分: {total_disp} / {max_disp} ｜ 总体通过率: {pass_rate}%")
    if not graded_max:
        # 全卷没有可自动判分的题目：绝不出具通过/不通过结论
        report_lines.append("ℹ️ 评价: 本轮没有可自动判分的题目（全部待人工复核），未生成通过率结论，"
                            "也未改动任何复测周期。")
    elif pass_rate >= 80:
        report_lines.append(f"🎉 评价: 掌握优良！ FSRS 记忆防线稳固，部分错题已顺利毕业！")
    elif updated_records:
        # [缺陷修复·禁止假陈述] 只有真的把题目写进复测队列，才谈得上"已重置复测周期"
        report_lines.append(f"⚠️ 评价: 仍有薄弱盲区未突破，未通过题目已重置回第一复测周期。")
    else:
        # [三审修复·2026-10-08 文案如实] 此前一律说「写入被拒绝或记录缺失，请检查权限
        # 后重试」——但「缺题干」与「自动回写未启用」两类原因与权限无关、重试无用。
        # 现按循环中实际收集到的原因分类如实输出，并给对应的可操作指引。
        _reasons = []
        if unarchived_no_question:
            _shown = "；".join(unarchived_no_question[:3])
            _more = f" 等 {len(unarchived_no_question)} 题" if len(unarchived_no_question) > 3 else ""
            _reasons.append(f"{len(unarchived_no_question)} 题因**缺少题干**无法自动归档"
                            f"（{_shown}{_more}），请补充题干后重新判卷")
        if unarchived_errors:
            _shown = "；".join(unarchived_errors[:2])
            _reasons.append(f"错题本写入异常（{_shown}），请检查权限后重试")
        if not _reasons:
            if not auto_advance:
                _reasons.append("自动复测回写已关闭（auto_advance=False），复测队列未变更")
            elif not error_logger:
                _reasons.append("错题本组件不可用，复测队列未变更")
            else:
                _reasons.append("未通过题目均未命中可回写记录，复测队列未变更")
        report_lines.append(f"⚠️ 评价: 仍有薄弱盲区未突破；但本轮**未能回写复测状态**"
                            f"（{'；'.join(_reasons)}）。")
    if need_review_titles:
        report_lines.append(
            f"🔍 【待人工复核 {len(need_review_titles)} 题】: {'；'.join(need_review_titles)}")
        report_lines.append(
            f"   说明: 上述题目缺少标准答案登记或作答未命中标准答案，系统已拒绝对其自动满分。")
        report_lines.append(
            f"   补录入口: 运行 `ky key set {p_id or '<试卷编号>'} <题号> \"<标准答案正文>\"` "
            f"补录后重新判卷即可自动采分；用 `ky key list {p_id or ''}` 查看登记情况。")
    for _w in consistency_warnings:
        report_lines.append(f"⚠ 内部一致性告警: {_w}")
    report_lines.append(f"============================================================\n")

    return {
        "success": True,
        "score": total_score,
        # [K1 修复·判卷口径一致性] total_score 与 pass_rate 同分母（graded_max）。
        # 此前返回 max_score（全卷满分，含待复核题），下游显示 5/10 却通过率 100%
        # 的口径打架；全卷满分请改用显式键 max_score。
        "total_score": graded_max,
        "max_score": max_score,
        "graded_max": graded_max,
        "pass_rate": pass_rate,
        "accuracy": pass_rate,
        "updated_records": updated_records,
        "need_review": need_review_titles,
        "consistency_warnings": consistency_warnings,
        "report": "\n".join(report_lines)
    }
