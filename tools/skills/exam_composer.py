# -*- coding: utf-8 -*-
"""
考研错题反向组卷与自测闭环引擎 (Exam Composer)
对标：腾讯元宝 AI 出卷、夸克错题自测
核心功能：
  1. 从 FSRS 到期队列 + 薄弱点雷达自动拼装盲盒自测卷
  2. 生成全真排版 Markdown 试卷（隐去原答案与推导，保留题干与采分槽）
  3. 隐藏题解与采分点元数据（供作答后自动对题）
  4. 支持自测卷答案判分、闭环推进 FSRS 复测档位并回写错题本
"""

import os
import re
import json
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


# ─────────────────────────────────────────────────────────────
# [P1 修复·答案补录闭环] 白名单真题切片入库时不带答案，判卷端只能「转人工复核」。
# 此前报告虽提示「请在错题档案中补录「标准答案」字段」，却没有任何命令/入口可执行，
# 学员无从下手。现提供 list_keys / upsert_answer 两个入口，供 ky key 子命令调用。
# ─────────────────────────────────────────────────────────────
def list_exam_keys(paper_id: str = ""):
    """列出已归档试卷密钥（默认全部）及其题目答案登记情况。

    Returns: {"success": bool, "papers": [ {paper_id, path, total, answered, items:[...] } ], "msg": str}
    """
    key_dir = ROOT / ".memory" / "exam_keys"
    if not key_dir.exists():
        return {"success": False, "papers": [], "msg": "尚未建立答案密钥库 (.memory/exam_keys 不存在)"}
    papers = []
    files = [key_dir / f"{paper_id}.json"] if paper_id else sorted(key_dir.glob("*.json"))
    for fp in files:
        if not fp.exists():
            continue
        pid = fp.stem
        try:
            raw = _open_keys_payload(pid, fp.read_text(encoding="utf-8"))
            keys = json.loads(raw) if raw else []
        except Exception as e:
            papers.append({"paper_id": pid, "path": str(fp), "error": f"解封/解析失败: {e}",
                           "total": 0, "answered": 0, "items": []})
            continue
        if not isinstance(keys, list):
            keys = [keys]
        items = []
        answered = 0
        for k in keys:
            if not isinstance(k, dict):
                continue
            std = str(k.get("standard_answer", "") or "").strip()
            if std:
                answered += 1
            items.append({
                "id": k.get("id"),
                "title": k.get("title"),
                "has_answer": bool(std),
                "standard_answer": std,
            })
        papers.append({"paper_id": pid, "path": str(fp),
                       "total": len(items), "answered": answered, "items": items})
    return {"success": True, "papers": papers, "msg": ""}


def upsert_answer(paper_id: str, question_id, answer: str):
    """为指定试卷的指定题目补录 / 更新标准答案（重新加密封装回密钥库）。

    Args:
        paper_id: 试卷编号 (EXAM_PAPER_ID)
        question_id: 题号（支持 int 或数字字符串，与密钥里 id 字段比对）
        answer: 标准答案正文

    Returns: {"success": bool, "msg": str}
    """
    if not paper_id:
        return {"success": False, "msg": "缺少试卷编号"}
    answer = (answer or "").strip()
    if not answer:
        return {"success": False, "msg": "标准答案不能为空"}
    key_dir = ROOT / ".memory" / "exam_keys"
    fp = key_dir / f"{paper_id}.json"
    if not fp.exists():
        return {"success": False, "msg": f"未找到试卷密钥库: {fp.name}（请确认试卷编号）"}
    try:
        raw = _open_keys_payload(paper_id, fp.read_text(encoding="utf-8"))
        keys = json.loads(raw) if raw else []
    except Exception as e:
        return {"success": False, "msg": f"密钥库解封失败: {e}（本机密钥盐可能已丢失）"}
    if not isinstance(keys, list):
        keys = [keys]

    try:
        want = int(str(question_id).strip())
    except (TypeError, ValueError):
        return {"success": False, "msg": f"题号非法: {question_id}"}

    hit = None
    for k in keys:
        if not isinstance(k, dict):
            continue
        try:
            kid = int(str(k.get("id")).strip())
        except (TypeError, ValueError):
            continue
        if kid == want:
            hit = k
            break
    if hit is None:
        return {"success": False, "msg": f"试卷 {paper_id} 中未找到第 {want} 题"}

    hit["standard_answer"] = answer
    sealed = _seal_keys_payload(paper_id, json.dumps(keys, ensure_ascii=False, indent=2))
    try:
        atomic_write_text(fp, sealed)
    except Exception as e:
        return {"success": False, "msg": f"写回密钥库失败: {e}"}

    # [P1 修复·双源不一致] 判卷端会按「中央密钥库 → 同目录伴随文件 → 内嵌注释」顺序读取，
    # 伴随文件命名实为 `.自测卷_<日期>_<paper_id>.md.keys.json`，此前 glob 模式写成
    # `.EXAM-<pid>.md.keys.json` 永远匹配不到（死代码）→ 补录后伴随文件仍是旧答案，
    # 判卷取到旧值，补录形同无效。现按「文件名含 paper_id」穷举 `0[1-4]-*/错题*` 目录，
    # 用 rglob 兜底，确保所有副本同步刷新。
    synced = 0
    try:
        for subj_dir in ROOT.glob("0[1-4]-*"):
            for cand in subj_dir.rglob(f"*{paper_id}*.keys.json"):
                # 防越界：只处理位于本工作区内的文件
                try:
                    if not str(cand.resolve()).startswith(str(ROOT.resolve())):
                        continue
                    atomic_write_text(cand, sealed)
                    synced += 1
                except Exception:
                    continue
    except Exception:
        pass
    suffix = f"（同步刷新 {synced} 份伴随副本）" if synced else "（未发现伴随副本，仅更新中央库）"
    return {"success": True,
            "msg": f"已为试卷 {paper_id} 第 {want} 题补录标准答案"
                   f"（{len(answer)} 字），并重新加密归档{suffix}"}

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


def _bigrams(text: str) -> set:
    """中文字符 2-gram 集合（零依赖，用于考点相关度打分）。"""
    t = re.sub(r"\s+", "", str(text or ""))
    if not t:
        return set()
    if re.search(r"[\u4e00-\u9fff]", t):
        return {t[i:i + 2] for i in range(len(t) - 1)} or {t}
    return set(re.findall(r"[A-Za-z0-9]{2,}", t.lower()))


def _score_card_against_weakness(card: dict, weakness_grams: set) -> int:
    """白名单题卡与薄弱/错因文本的 2-gram 重合数（靶向组卷打分）。"""
    if not weakness_grams:
        return 0
    hay = _bigrams(str(card.get("question") or "")) | _bigrams(str(card.get("title") or ""))
    return len(hay & weakness_grams)


def _load_whitelist_cards(subject, need=1, boost_text: str = ""):
    """从各科「参考资料/题库切片_*.md」抽取已入库的白名单真题卡片。

    背景（[P0 修复] 组卷题源闭环）：切片入库管道 (ky ingest) 产出的题卡此前
    未被组卷引擎消费，导致即便真题已入库，ky exam 仍输出占位自拟题，
    「白名单题源抽题门禁」形同虚设。本函数补上这条数据链路。

    [P0 修复·随机抽题] 此前 `random.shuffle(cards)` 取前 N，零考点筛选，
    号称"靶向"实为随机。现按题卡与薄弱/错因文本的 2-gram 重合度打分取
    Top-N（确定性排序，平分按标题稳定排列），boost_text 为空时退化为标题
    顺序（仍确定，不再随机）。

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
            stem_preview = question.replace('\n', ' ')[:12]
            cards.append({
                "subject": subject,
                "subject_name": subj_name,
                "file_name": source,
                "title": f"白名单真题 · {q_type}" + (f"（{score} 分）" if score else "") + f" · {stem_preview}",
                "error_type": "真题演练",
                "date": "",
                "question": question,
                "detail": f"题源出处: {source}",
                # 切片题卡不含答案 (答案归档于 .memory/exam_keys)，判卷端按「转人工复核」契约处理
                "standard_answer": "",
                "stage": 0,
                # [P1 修复·分值不等权] 把切片里解析到的「满分 X 分」带入题卡，
                # 供阅卷端按题计分（此前一律按 10 分算，真题卷分值会被算错）
                "score": score,
                "is_whitelist_card": True,
            })

    weakness_grams = _bigrams(boost_text)
    scored = [(_score_card_against_weakness(c, weakness_grams), c.get("title", ""), c)
              for c in cards]
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [c for _, _, c in scored[:max(1, need)]]


def _generate_synthetic_question_llm(subject: str, subj_name: str, topic: str, pain_point: str = "", workspace_root=None) -> dict:
    """当本地题库与到期错题不足时，若已配置 LLM 则调用大模型命制逼真真题；未配置或失败则优雅降级返回空字典"""
    try:
        try:
            from tools.llm_client import is_llm_configured, chat_completion
        except ImportError:
            from llm_client import is_llm_configured, chat_completion

        ws = workspace_root or ROOT
        if is_llm_configured(workspace_root=ws):
            focus = f"核心考点【{topic}】" + (f"，针对学员薄弱痛点「{pain_point}」" if pain_point else "")
            prompt = (
                f"你是一位资深中国研究生入学考试命题专家。\n"
                f"科目：{subj_name}。\n"
                f"考查重点：{focus}。\n"
                f"请为该考生量身命制一道贴近全国真题风格的高质量自测试题，要求严谨规范。\n\n"
                f"请以 JSON 字典格式输出，且仅输出合法的 JSON 文本：\n"
                f"{{\n"
                f'  "title": "{topic}专题攻坚自测",\n'
                f'  "question": "题干文本，如果是论述题/综合题请附带具体问答要求与材料",\n'
                f'  "standard_answer": "标准参考答案及关键步骤",\n'
                f'  "score": 10\n'
                f"}}"
            )
            raw = chat_completion(prompt, workspace_root=ws, timeout=15.0)
            if raw:
                raw_json = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
                raw_json = re.sub(r"\s*```$", "", raw_json)
                data = json.loads(raw_json)
                if isinstance(data, dict) and data.get("question"):
                    return {
                        "subject": subject,
                        "subject_name": subj_name,
                        "title": str(data.get("title", f"{topic}专题攻坚自测")),
                        "error_type": "概念与综合题攻坚",
                        "date": date.today().strftime("%Y-%m-%d"),
                        "question": str(data["question"]),
                        "detail": pain_point or topic,
                        "standard_answer": str(data.get("standard_answer", "")),
                        "grading_mode": "strict" if data.get("standard_answer") else "open",
                        "score": str(data.get("score", 10)),
                        "stage": 0,
                        "is_synthetic": True,
                    }
    except Exception:
        pass
    return {}


def compose_exam_paper(subject="math", count=3, include_weak=True, save_file=True):
    """
    自动从 FSRS 到期错题与薄弱点雷达抽取题目拼成自测卷
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
        if not str(item.get("question") or "").strip():
            return False
        identity = (
            str(item.get("file_name", "")).strip(),
            str(item.get("title", "")).strip(),
            str(item.get("question") or item.get("title") or "").strip(),
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
                llm_synth = _generate_synthetic_question_llm(subject, subj_name, module_name, pain_point, workspace_root=ROOT)
                if llm_synth and llm_synth.get("question"):
                    add_unique(llm_synth)
                else:
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
    #     boost_text 把已选错题的题干/错因与雷达痛点喂给打分器，实现真靶向。
    if len(selected_items) < count:
        _boost_parts = []
        for _it in selected_items:
            _boost_parts.append(str(_it.get("question") or ""))
            _boost_parts.append(str(_it.get("detail") or ""))
            _boost_parts.append(str(_it.get("title") or ""))
        _boost_text = "\n".join(p for p in _boost_parts if p)
        for card in _load_whitelist_cards(subject, need=count - len(selected_items),
                                          boost_text=_boost_text):
            if len(selected_items) >= count:
                break
            add_unique(card)

    # 若没有任何题目，构造基础考纲基准题
    # [文科适配·占位题串味] 此前全科目统一"写出核心公式"，哲学考生拿到理科模板。
    # 现按科目分支设问（仍为【私教自拟占位题】，明确标注非真题）。
    if subject == "pro":
        _fb_question = (f"请针对【{subj_name}】当前攻坚考纲要求，写出核心概念界定与"
                        f"代表人物主要观点，并完成一道典型题目的规范论述步骤。")
    elif subject == "eng":
        _fb_question = (f"请针对【{subj_name}】当前攻坚考纲要求，完成一段长难句主干拆解，"
                        f"并说明阅读选项定位与排除依据。")
    elif subject == "pol":
        _fb_question = (f"请针对【{subj_name}】当前攻坚考纲要求，辨析一对易混帽子词，"
                        f"并说明多选题排谬/排异步骤。")
    else:
        _fb_question = (f"请针对【{subj_name}】当前攻坚考纲要求，写出核心公式并简述"
                        f"做题防踩坑步骤。")
    if not selected_items:
        add_unique({
            "subject": subject,
            "subject_name": subj_name,
            "title": f"{subj_name}核心必考大纲自测题",
            "error_type": "概念漏洞",
            "date": date.today().strftime("%Y-%m-%d"),
            "question": _fb_question,
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
            "question": f"{_fb_question}（第 {fallback_index} 组）",
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
        f"> **试卷属性**： FSRS 盲盒复测 + 薄弱点针对性抽题（隐去原答案与历史错误）",
    ]
    # [缺陷修复·题源诚实标注] 当本地「参考资料/」未命中该考点真题时，组卷会退化为
    # 考纲自拟占位题（`is_synthetic=True`）。此前这类题以「XX核心必考大纲自测题」的
    # 名义直接混在真卷里，学员无法分辨"这是真题"还是"私教凑数的模板句"，
    # 而同一产品内的 ky variant 却明确标注 `[⚠️ 私教自拟变式]` —— 标准不一致。
    _synthetic_count = sum(1 for _it in selected_items if _it.get("is_synthetic"))
    if _synthetic_count:
        _real_count = len(selected_items) - _synthetic_count
        lines.append(
            f"> ⚠️ **题源声明**：本卷 {len(selected_items)} 题中，**{_synthetic_count} 题为【私教自拟占位题】**"
            f"（本地「参考资料/」未命中该考点真题，仅有考纲级设问框架，**不是真实试题**）；"
            f"真实题源题 {_real_count} 题。请把真题资料放入对应科目「参考资料/」后重新组卷。")
    lines.extend([
        f"> **作答要求**：请在各题【学员作答区】下方独立书写推导或最终结论，拒绝查阅笔记！",
        f"",
        f"---",
        f"",
    ])

    answer_keys = []

    for i, item in enumerate(selected_items, 1):
        t_title = item.get("title", f"第 {i} 题")
        err_type = item.get("error_type", "综合考点")
        q_text = item.get("question") or item.get("title") or "【题干设问缺失】"
        stage = item.get("stage", 0)

        lines.append(f"### 📝 第 {i} 题：{t_title}")
        lines.append(f"- **考查属性**：`{err_type}` ｜ FSRS 档位: `stage={stage}`")
        if item.get("is_synthetic"):
            # 逐题标注，确保学员不会把占位题当成真题来做（与 ky variant 的标注口径一致）
            lines.append(
                "- **题源属性**：⚠️ `[私教自拟占位题]` —— 本地「参考资料/」未命中该考点真题，"
                "本题只有考纲级设问框架，**不是真实试题**，不可据此判断真实应试水平。")
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
            "score": item.get("score") or item.get("points") or 10,
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
    """兼容入口：判分与组卷分离，仍使用本模块提供的配置和答案接口。"""
    from .exam_grading import grade_exam_paper as grade
    return grade(paper_path_or_content, user_answers_text, subject, auto_advance,
                 root=ROOT, error_logger=error_logger, open_keys=_open_keys_payload,
                 grade_open=_grade_open_by_llm, extract_tokens=_extract_answer_tokens,
                 norm_tokens=_norm_numeric_tokens, text_hit=_text_answer_hit)
