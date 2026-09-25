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

# [C3 题源溯源] 题源类别常量与身份模型统一由 question_source 提供（单一事实源）；
# 这里重新导出同名常量，`from exam_composer import ORIGIN_*` 的既有调用方不受影响。
try:
    from skills.question_source import (  # noqa: F401
        ORIGIN_MISTAKE, ORIGIN_PLACEHOLDER, ORIGIN_SYNTHETIC_LLM, ORIGIN_WHITELIST,
        QuestionSource, extract_card_stem, has_declared_identity, source_from_card,
        split_card_blocks,
    )
    from skills.question_source import REAL_ORIGINS as _REAL_ORIGINS
except Exception:  # pragma: no cover - 包式导入路径
    from tools.skills.question_source import (  # noqa: F401
        ORIGIN_MISTAKE, ORIGIN_PLACEHOLDER, ORIGIN_SYNTHETIC_LLM, ORIGIN_WHITELIST,
        QuestionSource, extract_card_stem, has_declared_identity, source_from_card,
        split_card_blocks,
    )
    from tools.skills.question_source import REAL_ORIGINS as _REAL_ORIGINS

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
        # [C3 分块口径统一] 用 question_source.split_card_blocks（与补录侧同一
        # 正则）切块 —— 此前用字面量 "### 【题号" 切分，遇到 "###  【题号 2】"
        # 之类的空白变体会把两张卡并成一块，误把后卡的身份行算进前卡。
        for blk in split_card_blocks(txt)[1:]:
            m_src = re.search(r"【题源出处】\*\*[：:]\s*`([^`]+)`", blk)
            source = m_src.group(1).strip() if m_src else slice_file.stem
            m_type = re.search(r"】\s*(.+?)（满分[:：]\s*([0-9.]+)\s*分", blk.splitlines()[0] if blk.splitlines() else "")
            q_type = m_type.group(1).strip() if m_type else "真题"
            score = m_type.group(2).strip() if m_type else ""
            # [C3 单一事实源] 题干提取与 question_source.extract_card_stem 同源
            # （此前这里是逐字复制的内联正则，两处漂移会让渲染侧算出的 checksum
            # 在解析侧校验失败）。
            question = extract_card_stem(blk)
            if len(question) < 8:
                continue
            stem_preview = question.replace('\n', ' ')[:12]
            # [C3 题源溯源] 读取/补全题源身份：
            #   · 卡片**元数据区**声明过身份（ID 或 校验和字段名，含"值被改坏"）
            #     → 必须通过校验，不一致 = 题干被改动过 → source_tampered，组卷侧
            #     排除。半声明 / 值不可解析同样按声明处理 —— 否则"删掉 ID 行"或
            #     "把校验和值改坏"即可绕过；
            #   · 完全无身份字段的存量卡片 → 现场构建（惰性 backfill，不落盘）——
            #     不能因缺字段把存量真题整批拒之门外。
            declared = has_declared_identity(blk)
            src = source_from_card(blk, origin=ORIGIN_WHITELIST, fallback_stem=question)
            tampered = declared and not src.verify(question)
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
                "origin": ORIGIN_WHITELIST,
                # [C3] 题源身份（source_id / checksum / 篡改标记）
                "source_id": src.source_id,
                "source_checksum": src.checksum,
                "source_verified": src.verified,
                "source_tampered": tampered,
            })

    weakness_grams = _bigrams(boost_text)
    scored = [(_score_card_against_weakness(c, weakness_grams), c.get("title", ""), c)
              for c in cards]
    scored.sort(key=lambda t: (-t[0], t[1]))
    # [C3 题源溯源] 可信卡按 need 取 Top-N；被篡改的卡**全部**附在返回列表末尾
    # （不占 Top-N 名额，也不因 need 截断而漏报）—— 组卷侧据此计数诊断
    # （source_breakdown.tampered）并排除。
    trusted = [c for _, _, c in scored if not c.get("source_tampered")]
    tampered = [c for _, _, c in scored if c.get("source_tampered")]
    return trusted[:max(1, need)] + tampered


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
                        "origin": ORIGIN_SYNTHETIC_LLM,
                    }
    except Exception:
        pass
    return {}


# ════════════════════════════════════════════════════════════════
# [P0 修复·白名单门禁从「免责声明」改为「真门禁」]
#
# 缺陷现场（2026-09-23 实测，参考资料/ 为空、错题本为空）：
#   ky exam math --count=3 →
#     第 1 题：数学核心必考大纲自测题
#     第 2 题：数学考纲综合自测题 1     ← 题干 = 同一句话 + "（第 1 组）"
#     第 3 题：数学考纲综合自测题 2     ← 题干 = 同一句话 + "（第 2 组）"
#   三题共用一句「请针对【数学】当前攻坚考纲要求，写出核心公式并简述做题防踩坑
#   步骤。」，无标准答案、无法判分、无学习价值，却被包装成一份「自测卷」。
#   门禁只剩一行「题源声明」——降级本身诚实，产物却几乎为零价值。
#   更糟的是雷达模板占位题排在白名单真题卡**之前**：只要雷达里有 C/D 行且未配大
#   模型，即便 参考资料/ 已有真题切片，也会先用模板句凑数。
#
# 现改为按题源覆盖率分级响应：
#   · 有真实题源（错题本 / 白名单真题卡 / 按薄弱点由大模型命制且含参考答案）→ 照常组卷；
#   · 题源不足 → **降题量**组卷，卷首明确「仅 N 题来自真实题源，其余已省略」，绝不凑数；
#   · 完全无题源 → **拒绝组卷**，改为输出可执行的上手引导（放资料 → ky ingest → 重组）；
#   · 占位题只在调用方显式 allow_placeholder=True 时产出，且必须按考纲考点 / 薄弱点
#     生成**互不相同**的题干（不再复制同一句话）。
# 题源顺序也随之修正：错题本 → 白名单真题卡 → 大模型变式 → （显式允许时）占位题。
# ════════════════════════════════════════════════════════════════

# 题卡 origin 字段取值（ORIGIN_MISTAKE / WHITELIST / SYNTHETIC_LLM / PLACEHOLDER）
# 与「真实题源」集合 _REAL_ORIGINS 已在文件头从 question_source 导入 ——
# 那是单一事实源（题卡渲染侧 material_ingestion 也依赖同一份定义）。


def _read_radar_rows(subject: str):
    """读取「_状态/薄弱点雷达.md」里评级为 C/D 的行 → [(模块名, 核心卡点)]。"""
    subj_folder = SUBJECT_DIRS.get(subject, "01-数学")
    radar_file = ROOT / subj_folder / "_状态" / "薄弱点雷达.md"
    if not radar_file.exists():
        return []
    try:
        txt = radar_file.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    rows = []
    seen = set()
    for m in re.findall(r"\|\s*([^|\n]+?)\s*\|\s*([^|\n]+?)\s*\|\s*[CD]\s*\|\s*([^|\n]+?)\s*\|", txt):
        module_name = m[0].strip()
        pain_point = m[2].strip()
        if not module_name or module_name in seen:
            continue
        seen.add(module_name)
        rows.append((module_name, pain_point))
    return rows


#: 占位题按考点展开时的科目化设问模板（同一考点只用一次，题干天然互不相同）
_POINT_STEM_TEMPLATES = {
    "math": ("【{point}】：写出该考点的核心定义 / 定理成立条件，完整推演一道典型题型的"
             "标准步骤，并列出 2 个最常见的失分点。"),
    "eng": ("【{point}】：说明该题型 / 技能的标准解题步骤与定位方法，自选一句真题长难句"
            "拆出主干，并说明选项排除依据。"),
    "pol": ("【{point}】：写出该考点的核心表述（含关键帽子词），辨析 1 组最易混淆的选项，"
            "并说明多选题排谬 / 排异步骤。"),
    "pro": ("【{point}】：界定核心概念，列出代表观点 / 原理要点与出处，并按论述题规范"
            "写出「总—分—总」答题提纲。"),
}

#: 无考纲考点可用时的兜底设问角度（每个科目 3 个不同角度，超出后按轮次编号）
_GENERIC_STEM_ANGLES = {
    "math": (
        "请针对【{subj}】当前攻坚考纲要求，写出核心公式并简述做题防踩坑步骤。",
        "请针对【{subj}】当前攻坚考纲要求，挑一道你最近做错的典型题，复现完整推导并标出每一步的采分点。",
        "请针对【{subj}】当前攻坚考纲要求，列出 3 个最常混淆的定理 / 公式使用条件，并各举一个反例说明。",
    ),
    "eng": (
        "请针对【{subj}】当前攻坚考纲要求，完成一段长难句主干拆解，并说明阅读选项定位与排除依据。",
        "请针对【{subj}】当前攻坚考纲要求，写出一段功能句模板（观点—论证—总结），并替换 2 组高分词汇。",
        "请针对【{subj}】当前攻坚考纲要求，总结 3 类干扰选项的常见特征，并各举一例说明识别方法。",
    ),
    "pol": (
        "请针对【{subj}】当前攻坚考纲要求，辨析一对易混帽子词，并说明多选题排谬 / 排异步骤。",
        "请针对【{subj}】当前攻坚考纲要求，梳理一个核心原理的「是什么—为什么—怎么办」三段式表述。",
        "请针对【{subj}】当前攻坚考纲要求，列出 3 个最易记混的历史节点 / 会议，并写出对应的关键判断。",
    ),
    "pro": (
        "请针对【{subj}】当前攻坚考纲要求，写出核心概念界定与代表人物主要观点，并完成一道典型题目的规范论述步骤。",
        "请针对【{subj}】当前攻坚考纲要求，选一个核心原理写出「概念—要点—现实意义」的论述提纲。",
        "请针对【{subj}】当前攻坚考纲要求，对比两个易混概念的异同，并各举一道真题式设问说明答题思路。",
    ),
}


def _syllabus_points_for_placeholder(subject: str, limit: int):
    """从考试大纲解析考点名（按 D 盲区 > C 生疏 > U 未评估 > B > A 排序），供差异化占位题使用。

    考纲缺失 / 仍为占位模板 / 解析异常时返回空列表（由调用方回落到通用设问角度）。
    """
    if limit <= 0:
        return []
    try:
        try:
            from skills import knowledge_map
        except Exception:
            from tools.skills import knowledge_map
        km = knowledge_map.build_knowledge_map(subject, root=ROOT)
    except Exception:
        return []
    if not isinstance(km, dict) or km.get("syllabus_placeholder"):
        return []
    rank = {"D": 0, "C": 1, "U": 2, "B": 3, "A": 4}
    pts = []
    seen = set()
    for chap in km.get("chapters") or []:
        for pt in chap.get("points") or []:
            name = str(pt.get("name") or "").strip()
            # 内置保底模块（无考纲时 knowledge_map 自己造的）不算真实考点
            if len(name) < 2 or name in seen or name.endswith("核心必考概念"):
                continue
            seen.add(name)
            pts.append((rank.get(pt.get("grade", "U"), 2), len(pts), name))
    pts.sort()
    return [name for _, _, name in pts[:limit]]


def _build_placeholder_items(subject: str, subj_name: str, need: int, radar_rows):
    """生成 ``need`` 道**互不相同**的占位题（仅在 allow_placeholder=True 时调用）。

    优先级：薄弱点雷达行（带学员真实卡点）→ 考纲考点（按盲区优先）→ 通用设问角度。
    每张题卡 origin=placeholder / is_placeholder=True / grading_mode=open / 无标准答案。
    """
    items = []
    today = date.today().strftime("%Y-%m-%d")

    def _mk(title, question, error_type, detail):
        return {
            "subject": subject,
            "subject_name": subj_name,
            "title": title,
            "error_type": error_type,
            "date": today,
            "question": question,
            "detail": detail,
            # 开放题不登记伪标准答案，判卷端按「转人工复核」契约处理
            "standard_answer": "",
            "grading_mode": "open",
            "stage": 0,
            "is_synthetic": True,
            "is_placeholder": True,
            "origin": ORIGIN_PLACEHOLDER,
        }

    for module_name, pain_point in radar_rows:
        if len(items) >= need:
            break
        items.append(_mk(
            f"{module_name}专题攻坚自测",
            f"针对【{module_name}】核心考点与薄弱痛点「{pain_point}」，请写出核心定义、定理条件并完成典型变式题推导。",
            "概念漏洞", pain_point))

    tmpl = _POINT_STEM_TEMPLATES.get(subject, _POINT_STEM_TEMPLATES["pro"])
    for point in _syllabus_points_for_placeholder(subject, need - len(items)):
        if len(items) >= need:
            break
        items.append(_mk(f"{point} · 考纲自测", tmpl.format(point=point), "综合考点", f"考纲考点：{point}"))

    angles = _GENERIC_STEM_ANGLES.get(subject, _GENERIC_STEM_ANGLES["pro"])
    round_no = 0
    while len(items) < need:
        idx = len(items)
        angle = angles[idx % len(angles)].format(subj=subj_name)
        round_no = idx // len(angles)
        suffix = f"（第 {round_no + 1} 轮）" if round_no else ""
        title = f"{subj_name}核心必考大纲自测题" if idx == 0 else f"{subj_name}考纲综合自测题 {idx}"
        items.append(_mk(title, angle + suffix, "综合考点" if idx else "概念漏洞", "考纲基础自测"))
    return items[:need]


def _build_refusal_guidance(subject, subj_name, subj_folder, diag, requested):
    """无任何题源时返回给学员的**可执行**上手引导（替代原来的雷同占位卷）。"""
    ref_dir = f"{subj_folder}/参考资料/"
    mistake_dir = f"{subj_folder}/{'错题与长难句本' if subject == 'eng' else '错题本'}/"
    lines = [
        f"# ⛔ 未组卷 · {subj_name}",
        "",
        f"> 请求题量 {requested} 题，但本地没有任何可用题源。**白名单题源门禁**拒绝用考纲模板句冒充试卷",
        "> （占位题没有标准答案、无法判分、也不会进入 FSRS 复测队列，对提分没有价值）。",
        "",
        "## 当前题源盘点",
        f"- 错题本（`{mistake_dir}`）：{diag['mistake']} 条待复测 / 未掌握错题",
        f"- 白名单真题卡（`{ref_dir}题库切片_*.md`）：{diag['whitelist']} 张"
        + ("（目录不存在）" if not diag["ref_dir_exists"] else
           ("（目录为空，尚未放入任何资料）" if diag["ref_files"] == 0 else
            f"（目录内有 {diag['ref_files']} 个文件，但尚未切片入库）")),
        f"- 薄弱点雷达（`{subj_folder}/_状态/薄弱点雷达.md`）："
        + (f"{diag['radar']} 行 C/D 级薄弱项，但未配置大模型，无法据此命制变式题"
           if diag["radar"] else "尚未建立（由 AI 私教在批改对话中逐步写入，可先从模板复制）"),
    ]
    # [C3 题源溯源] 被防篡改闸门排除的卡片必须在盘点里可见 —— 否则学员看到
    # "盘点 1 张卡 / 组卷说无题源"会一头雾水，也不知道怎么修。
    if diag.get("tampered"):
        lines.append(
            f"- ⚠️ 另有 {diag['tampered']} 张题源卡（白名单 / 错题本）**因题干与题源ID 校验和不符被排除**"
            "（卡片被改动过，或身份行残缺）——请改回题干，或删掉该卡的「题源ID / 题源校验和」"
            "两行后运行 `python tools/backfill_source_ids.py` 重新盖章。")
    lines.extend([
        "",
        "## 3 步启动「组卷 → 判分 → 归因 → 复测」闭环",
        f"1. 把至少 1 份近年真题（PDF / Word / Markdown / TXT）放入 `{ref_dir}`（该目录已被 .gitignore 保护，绝不上云）；",
        f"2. 切片入库：`ky ingest \"<真题文件路径>\" --subject={subject} --source=\"2025 真题\"`；",
        f"3. 重新组卷：`ky exam {subject} --count={requested} --save`，作答后用 `ky exam-submit <试卷路径> <作答>` 判分。",
        "",
        "> 已有做题记录？先用 `ky exam-submit` 判分或让私教 `log_mistake` 归档，错题会自动成为组卷题源。",
        f"> 如确需几道考纲级设问框架热身，可显式加 `--allow-placeholder`（占位题不计分、不入复测队列）。",
    ])
    return "\n".join(lines)


def _material_diagnostics(subject, subj_folder):
    """组卷失败时的题源诊断快照（只读，不落盘）。"""
    ref_dir = ROOT / subj_folder / "参考资料"
    ref_files = 0
    if ref_dir.exists():
        try:
            ref_files = sum(1 for p in ref_dir.iterdir()
                            if p.is_file() and p.name.lower() != "readme.md" and not p.name.startswith("."))
        except Exception:
            ref_files = 0
    cards = _load_whitelist_cards(subject, need=10 ** 6) if ref_dir.exists() else []
    return {
        "ref_dir_exists": ref_dir.exists(),
        "ref_files": ref_files,
        # 只数**可信**卡：被防篡改闸门排除的卡不算"可用题源"（否则会出现
        # "盘点说库里有 1 张卡、组卷却说没有任何可用题源"的自相矛盾）
        "whitelist": sum(1 for c in cards if not c.get("source_tampered")),
        "tampered": sum(1 for c in cards if c.get("source_tampered")),
        "radar": len(_read_radar_rows(subject)),
        "mistake": 0,  # 由调用方填充（依赖 error_logger）
    }


def compose_exam_paper(subject="math", count=3, include_weak=True, save_file=True,
                       allow_placeholder=False):
    """
    自动从 FSRS 到期错题、白名单真题卡与薄弱点雷达抽取题目拼成自测卷
    返回包含试卷元数据与 Markdown 文本的字典

    题源门禁（[P0 修复]）：
      * ``success=True``：有真实题源。题量可能**少于** ``count``（``shortfall`` > 0），
        卷首会声明「仅 N 题来自真实题源，其余已省略」，绝不用占位题凑数；
      * ``success=False`` / ``refused=True``：没有任何真实题源，**不产出试卷、不写密钥、
        不落盘**，``content`` / ``formatted_paper`` 为可执行的上手引导；
      * ``allow_placeholder=True``：显式允许用考纲级设问框架补足题量（题干按薄弱点 /
        考纲考点差异化生成，逐题标注「私教自拟占位题」，无标准答案、不计分）。
    """
    subj_name = get_subject_name(subject, SUBJECT_NAMES.get(subject, subject))
    subj_folder = SUBJECT_DIRS.get(subject, "01-数学")

    if not isinstance(count, int) or count < 1:
        raise ValueError("count 必须是大于 0 的整数")

    selected_items = []
    selected_keys = set()

    def add_unique(item, origin=None):
        """按题源、标题和题干去重，避免同一错题重复占位；同时补齐 origin 标签。"""
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
        if origin and not item.get("origin"):
            item["origin"] = origin
        # [C3 题源溯源] 每道进入试卷的题都挂题源身份。存量卡片（错题本 / 旧切片）
        # 没有 source_id 时现场构建 —— 身份是题干的纯派生值，故无需落盘即可校验；
        # 已有 source_id 的（如白名单卡）保持原样，不覆盖其来源语义。
        if not item.get("source_id"):
            _stem = str(item.get("question") or item.get("title") or "")
            if _stem:
                _src = QuestionSource.build(
                    _stem, str(item.get("origin") or origin or ""))
                item["source_id"] = _src.source_id
                item["source_checksum"] = _src.checksum
        selected_keys.add(identity)
        selected_items.append(item)
        return True
    #: [C3 题源溯源] 因题干与题源ID 校验和不符而被排除的卡片数（防篡改闸门）
    tampered_count = 0

    # 0. 错题池：一次全量扫描（含防篡改标记），供后续两步复用。
    #    [C3 复查 P2] 错题卡此前完全不读落盘身份 —— backfill 给它盖的章成了死数据，
    #    改过题干的错题仍会进卷（落盘身份与进卷身份分裂）。现在与白名单卡同口径：
    #    声明过身份就必须通过校验。全量扫描使坏卡计数不受"选满退出"截断影响。
    all_errs = error_logger.scan_error_records(subject) if error_logger else []
    tampered_count += sum(1 for e in all_errs if e.get("source_tampered"))

    # 1. 优先拉取到期错题
    if error_logger:
        due_items = error_logger.get_due_reviews(subject, max_count=count)
        for item in due_items:
            if len(selected_items) >= count:
                break
            if item.get("source_tampered"):
                continue  # 已在上方计数；不占名额、不进试卷
            add_unique(item, ORIGIN_MISTAKE)

    # 2. 到期题不足时，拉取其他尚未掌握的错题
    if len(selected_items) < count and error_logger:
        for err in all_errs:
            if len(selected_items) >= count:
                break
            if err.get("source_tampered"):
                continue  # 已在上方计数
            if "已掌握" not in err.get("status", "") and err not in selected_items:
                add_unique(err, ORIGIN_MISTAKE)
    mistake_count = len(selected_items)

    # 薄弱点雷达行：既作为白名单靶向打分的线索，也是大模型命制变式题的依据
    radar_rows = _read_radar_rows(subject) if include_weak else []

    # 3. [P0 修复·题源顺序] 白名单真题卡**先于**任何自拟题：
    #    此前雷达模板占位题排在白名单前面，导致 参考资料/ 已有真题切片时仍先用模板句凑数。
    #    boost_text 把已选错题的题干 / 错因与雷达痛点喂给打分器，实现真靶向。
    if len(selected_items) < count:
        _boost_parts = []
        for _it in selected_items:
            _boost_parts.append(str(_it.get("question") or ""))
            _boost_parts.append(str(_it.get("detail") or ""))
            _boost_parts.append(str(_it.get("title") or ""))
        for _mod, _pain in radar_rows:
            _boost_parts.append(f"{_mod} {_pain}")
        _boost_text = "\n".join(p for p in _boost_parts if p)
        for card in _load_whitelist_cards(subject, need=count - len(selected_items),
                                          boost_text=_boost_text):
            # [C3 题源溯源] 题干与题源ID 的 checksum 不符 → 卡片被改动过，
            # 不进入试卷（计入 source_breakdown.tampered 供卷首声明与诊断）。
            # 篡改判定必须在「选满退出」**之前**：坏卡被刻意排在返回列表末尾，
            # 若先 break 就永远数不到它们，诊断信号会静默归零。
            if card.get("source_tampered"):
                tampered_count += 1
                continue
            if len(selected_items) >= count:
                break
            add_unique(card, ORIGIN_WHITELIST)

    # 4. 仍不足且允许引入雷达薄弱项时，按薄弱点由大模型命制变式题（含参考答案）。
    #    未配置大模型 / 调用失败时**不再**用模板句顶替 —— 那属于占位题，由第 5 步按显式开关处理。
    llm_synth_failed_rows = []
    if len(selected_items) < count and include_weak:
        for module_name, pain_point in radar_rows:
            if len(selected_items) >= count:
                break
            llm_synth = _generate_synthetic_question_llm(subject, subj_name, module_name, pain_point, workspace_root=ROOT)
            if llm_synth and llm_synth.get("question"):
                add_unique(llm_synth, ORIGIN_SYNTHETIC_LLM)
            else:
                llm_synth_failed_rows.append((module_name, pain_point))

    real_items = [it for it in selected_items if it.get("origin") in _REAL_ORIGINS]

    # 5. 题源门禁裁决
    source_breakdown = {
        "mistake": mistake_count,
        "whitelist": sum(1 for it in selected_items if it.get("origin") == ORIGIN_WHITELIST),
        "synthetic_llm": sum(1 for it in selected_items if it.get("origin") == ORIGIN_SYNTHETIC_LLM),
        "placeholder": 0,
        # [C3] 被防篡改闸门排除的卡片数（>0 说明题库里有题干与 ID 不符的卡片）
        "tampered": tampered_count,
    }
    if not real_items and not allow_placeholder:
        diag = _material_diagnostics(subject, subj_folder)
        diag["mistake"] = mistake_count
        # [C3 复查 P2] 诊断里的 tampered 改用组卷侧已算好的总数 —— 含白名单卡
        # **与**错题卡两类（_material_diagnostics 只扫白名单，会漏报错题卡）。
        diag["tampered"] = tampered_count
        guidance = _build_refusal_guidance(subject, subj_name, subj_folder, diag, count)
        return {
            "success": False,
            "refused": True,
            "reason": "no_material",
            "paper_id": "",
            "subject": subject,
            "subject_name": subj_name,
            "count": 0,
            "requested_count": count,
            "shortfall": count,
            "items": [],
            "content": guidance,
            "formatted_paper": guidance,
            "saved_path": None,
            "source_breakdown": source_breakdown,
            "diagnostics": diag,
            "guidance": guidance,
        }

    if allow_placeholder and len(selected_items) < count:
        # 显式允许时才补占位题；雷达行里已被大模型命制过的模块不再重复出占位题
        for ph in _build_placeholder_items(subject, subj_name, count - len(selected_items),
                                           llm_synth_failed_rows):
            if len(selected_items) >= count:
                break
            add_unique(ph, ORIGIN_PLACEHOLDER)
        source_breakdown["placeholder"] = sum(
            1 for it in selected_items if it.get("origin") == ORIGIN_PLACEHOLDER)

    shortfall = max(0, count - len(selected_items))

    today_str = datetime.now().strftime("%Y-%m-%d")
    paper_id = f"EXAM-{subject.upper()}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    # 构建自测试卷 Markdown 内容
    lines = [
        f"# 🎓 考研全科 AI 专属自测卷 · {subj_name}",
        f"",
        f"> **试卷编号**：`{paper_id}` ｜ **生成日期**：`{today_str}` ｜ **题量**：`{len(selected_items)} 题`",
        f"> **试卷属性**： FSRS 盲盒复测 + 薄弱点针对性抽题（隐去原答案与历史错误）",
    ]
    # [缺陷修复·题源诚实标注] 占位题与大模型变式题都不是真题，必须在卷首与逐题两级标注，
    # 与同一产品内 ky variant 的 `[⚠️ 私教自拟变式]` 口径一致。
    _placeholder_count = source_breakdown["placeholder"]
    _llm_count = source_breakdown["synthetic_llm"]
    _real_count = len(selected_items) - _placeholder_count
    if _placeholder_count:
        lines.append(
            f"> ⚠️ **题源声明**：本卷 {len(selected_items)} 题中，**{_placeholder_count} 题为【私教自拟占位题】**"
            f"（本地「参考资料/」未命中该考点真题，仅有考纲级设问框架，**不是真实试题**，无标准答案、不计分）；"
            f"真实题源题 {_real_count} 题。请把真题资料放入对应科目「参考资料/」并运行 `ky ingest` 后重新组卷。")
    if _llm_count:
        lines.append(
            f"> ⚠️ **变式声明**：本卷 {_llm_count} 题为【私教自拟变式】——按薄弱点雷达由大模型命制，"
            f"附参考答案但**并非真题**，仅作针对性巩固。")
    # [C3 复查 P1] 成功出卷路径也必须呈现防篡改闸门的排除信号 —— 此前只在
    # 「无题可出」的拒绝引导里可见；库里坏卡导致题量缩水时，卷面却引导学员去
    # "补充真题"（归因错误），学员无从知道真正原因是卡片被改动过。
    if tampered_count:
        lines.append(
            f"> ⚠️ **题源完整性声明**：另有 {tampered_count} 张题源卡因**题干与题源ID 校验和不符**"
            f"被防篡改闸门排除（白名单卡在「参考资料/题库切片_*.md」、错题卡在「错题本/」；"
            f"卡片被改动过，或身份行残缺）——请改回题干，或删掉该卡的「题源ID / 题源校验和」"
            f"两行后运行 `python tools/backfill_source_ids.py` 重新盖章。")
    if shortfall:
        if tampered_count:
            _excluded = (f"另有 {tampered_count} 张题源卡被防篡改闸门排除"
                         f"（见上行「题源完整性声明」）")
            _fix_hint = "修复上述卡片后重新组卷即可恢复题量。"
        else:
            _excluded = "本地「参考资料/」未命中更多真题"
            _fix_hint = "补充真题后运行 `ky ingest` 即可扩充题源；"
        lines.append(
            f"> ⚠️ **题量声明**：本次请求 {count} 题，实际仅 {len(selected_items)} 题来自真实题源"
            f"（错题本 {source_breakdown['mistake']} 题 / 白名单真题 {source_breakdown['whitelist']} 题 / "
            f"大模型变式 {_llm_count} 题），其余 {shortfall} 题已省略 —— {_excluded}，"
            f"系统拒绝用考纲模板句凑数。{_fix_hint}"
            f"如确需占位题热身，可加 `--allow-placeholder`。")
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
        if item.get("is_placeholder"):
            # 逐题标注，确保学员不会把占位题当成真题来做（与 ky variant 的标注口径一致）
            lines.append(
                "- **题源属性**：⚠️ `[私教自拟占位题]` —— 本地「参考资料/」未命中该考点真题，"
                "本题只有考纲级设问框架，**不是真实试题**，不可据此判断真实应试水平。")
        elif item.get("is_synthetic"):
            lines.append(
                "- **题源属性**：⚠️ `[私教自拟变式]` —— 按薄弱点由大模型命制的巩固题，"
                "附参考答案但**并非真题**。")
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
            "grading_mode": str(item.get("grading_mode", "exact") or "exact").strip(),
            # 题源标签：占位题在判卷端不计分、不入复测队列
            "origin": str(item.get("origin", "") or ""),
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
        "success": True,
        "refused": False,
        "paper_id": paper_id,
        "subject": subject,
        "subject_name": subj_name,
        "count": len(selected_items),
        "requested_count": count,
        "shortfall": shortfall,
        "source_breakdown": source_breakdown,
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


def health_check() -> dict:
    """[B4] 结构化健康自检：``{"status": READY/DEGRADED/UNAVAILABLE, "reason": str}``。

    组卷有两条腿：本地题库/到期错题（永远可用）与 LLM 靶向命题（需 API Key）。
      * 已配置 Key → READY：题库不足时可由大模型补题；
      * 未配置 Key → DEGRADED：题库与到期错题不足时会返回空卷，只能靠已有题库组卷。
    只读 ky_config.json 判断，不联网（旧硬编码"已就绪"掩盖的正是后者）。
    """
    if not callable(globals().get("grade_exam_paper")):
        return {"status": "UNAVAILABLE", "reason": "判分/组卷核心入口缺失，ky exam 将不可用"}
    import json as _json
    from pathlib import Path as _Path
    cfg: dict = {}
    try:
        cfg = _json.loads((_Path(ROOT) / "ky_config.json").read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    if str(cfg.get("api_key", "") or "").strip():
        return {"status": "READY", "reason": "本地题库组卷 + LLM 靶向补题全链路可用"}
    return {"status": "DEGRADED",
            "reason": "未配置大模型 API Key：本地题库/到期错题不足时无法 LLM 补题，仅能使用已有题库组卷"}
