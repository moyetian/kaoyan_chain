# -*- coding: utf-8 -*-
"""批次二·A 域修复回归（2026-10-09 审查）：判卷 ID 类型归一 + 题源 checksum 口径。

覆盖两项已核实缺陷：

  * [GRADE-H1] ``open_grader._recompute_total`` ID 类型不归一：模型返回的
    ``rubric_hits[].id`` 常为字符串（``"1"``），rubric 解析出的 id 为整数
    （``1``）—— 原样建键/查找 miss → 命中分全按 0 算 → 重算 0.0 与自报分
    偏差超容差 → ``_finalize`` 误判「判分自检未通过」强制转人工（开放题
    自动判分被类型伪矛盾打废）。修复：双端经 ``_rubric_key`` 归一为字符串键
    （int 1 / "1" / 1.0 / " 1 " 同键；None 不建键，保持既有行为）。
  * [STEM-M1] ``question_source.compute_checksum`` 取数覆盖答案/解析小节：
    checksum 从「试题原题」提取到卡尾，把 §2 标准答案 / §3 采分点 / §4 解析
    一并算入 —— 微调解析 → verify 失败 → 组卷侧 source_tampered 误报
    「题干被改动」。修复：截断到第一个答案类小节（``ANSWER_SUBSEC_RE``，
    与 exam_composer 剥离正则单一事实源）再归一化；存量旧口径盖章由
    ``checksum_matches`` 双口径接受（兼容红线），``extract_card_stem``
    输出逐字节不变（组卷密钥依赖它提取标准答案，绝不允许改动）。

全部使用合成数据（示例题 / 示例卡），不含任何真实身份信息。
"""
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from skills import exam_composer as exam  # noqa: E402
from skills import open_grader as og  # noqa: E402
from skills import question_source as qs  # noqa: E402


# ═══════════════════════ GRADE-H1：rubric_hits ID 类型归一 ═══════════════════════

def _rubric_two_points():
    """两条要点的 rubric：4 分 + 6 分（满分 10）。"""
    return [
        {"id": 1, "point": "要点一", "score": 4.0, "must_have": True},
        {"id": 2, "point": "要点二", "score": 6.0, "must_have": False},
    ]


def test_recompute_total_accepts_str_hits_id():
    """① rubric id=int / hits id=str：命中明细必须被采纳。

    修复前 by_id 用原样 id 建键（"1"），查找用 rubric 的 int 1 → miss →
    h={} → 命中分 0 → 重算 0.0。期望：full(4) + partial(6×0.5) = 7/10。
    """
    rec = og._recompute_total(_rubric_two_points(),
                              [{"id": "1", "hit": "full"}, {"id": "2", "hit": "partial"}])
    assert rec == 7.0, f"字符串 id 的命中明细被丢弃：{rec}"


def test_recompute_total_accepts_int_hits_id_against_str_rubric():
    """② 反向：rubric id=str / hits id=int 同样归一。期望：full(4) = 4/10。"""
    rubric = [{"id": "1", "score": 4.0}, {"id": "2", "score": 6.0}]
    rec = og._recompute_total(rubric, [{"id": 1, "hit": "full"}, {"id": 2, "hit": "none"}])
    assert rec == 4.0, f"整数 id 命中明细被丢弃：{rec}"


def test_recompute_total_float_and_padded_ids_same_key():
    """③ float 整值 1.0 / " 1 " / int 1 归一为同一键（命中 5 分要点）。"""
    rubric = [{"id": 1, "score": 5.0}, {"id": 2, "score": 5.0}]
    assert og._recompute_total(rubric, [{"id": 1.0, "hit": "full"}]) == 5.0
    assert og._recompute_total(rubric, [{"id": " 1 ", "hit": "full"}]) == 5.0
    assert og._recompute_total(rubric, [{"id": "1", "hit": "full"}]) == 5.0


def test_recompute_total_same_type_unchanged():
    """④ 同类型 id（既有正常路径）行为不回归：逐值锁定。"""
    rubric = _rubric_two_points()
    assert og._recompute_total(rubric, [{"id": 1, "hit": "full"},
                                        {"id": 2, "hit": "full"}]) == 10.0
    assert og._recompute_total(rubric, [{"id": 1, "hit": "none"},
                                        {"id": 2, "hit": "none"}]) == 0.0
    # 明细缺失 / 无 rubric → None（跳过比对，不降级 —— P1 修复的既有契约）
    assert og._recompute_total(rubric, []) is None
    assert og._recompute_total([], [{"id": 1, "hit": "full"}]) is None
    # None id 不建键、不参与匹配（保持既有行为）
    assert og._recompute_total(rubric, [{"id": None, "hit": "full"}]) == 0.0


def _finalize_min(review, rubric):
    """最小 Stage-4 汇总：单评审、无仲裁、无分歧、容差 2.0。"""
    cfg = {"pass_threshold": 6.0, "gray_zone": 2.0, "min_confidence": 0.6,
           "divergence_threshold": 2.0, "rubric_tolerance": 2.0}
    return og._finalize(og.OpenGradeResult(), [review], None, cfg, False, 0.0, rubric)


def test_finalize_no_selfcheck_degrade_on_id_type_mismatch():
    """⑤ 端到端：rubric id=int / hits id=str 不得触发「判分自检未通过」降级。

    修复前重算 0.0 vs 自报 10.0（偏差 10 > 容差 2）→ match_level=1 + degraded
    + reason 含「判分自检未通过」；修复后重算一致 → 正常通过。
    """
    rubric = [{"id": 1, "point": "要点一", "score": 10.0, "must_have": True}]
    review = og.ReviewResult(name="评审A", total=10.0, confidence=0.9,
                             mistake_type="无", rubric_hits=[{"id": "1", "hit": "full"}],
                             weight=1.0)
    out = _finalize_min(review, rubric)
    assert "判分自检未通过" not in out.reason, out.reason
    assert out.degraded is False
    assert out.match_level == 2


def test_finalize_mixed_id_still_catches_none_hits_contradiction():
    """⑤ 阴性对照：归一后硬校验仍生效 —— 明细确实全 none 而自报 9.0 必须降级。"""
    rubric = [{"id": 1, "point": "要点一", "score": 10.0}]
    review = og.ReviewResult(name="评审A", total=9.0, confidence=0.9,
                             mistake_type="无", rubric_hits=[{"id": "1", "hit": "none"}],
                             weight=1.0)
    out = _finalize_min(review, rubric)
    assert "判分自检未通过" in out.reason, out.reason
    assert out.degraded is True
    assert out.match_level == 1


def test_finalize_mixed_id_catches_understated_total():
    """⑤ 阴性对照：明细全 full 而自报 0.0（反向矛盾）同样必须降级。

    修复前类型差导致重算也是 0.0，这类「假低报」反而漏检；归一后必须抓住。
    """
    rubric = [{"id": 1, "point": "要点一", "score": 10.0}]
    review = og.ReviewResult(name="评审A", total=0.0, confidence=0.9,
                             mistake_type="无", rubric_hits=[{"id": "1", "hit": "full"}],
                             weight=1.0)
    out = _finalize_min(review, rubric)
    assert "判分自检未通过" in out.reason, out.reason
    assert out.match_level == 1


# ═══════════════════════ STEM-M1：checksum 截断答案/解析小节 ═══════════════════════

def _legacy_hash(stem) -> str:
    """旧口径摘要独立实现（全文仅空白归一）—— 不引用被测模块私有 helper。"""
    return hashlib.sha256(re.sub(r"\s+", "", str(stem)).encode("utf-8")).hexdigest()[:16]


#: 带 §2 标准答案 / §3 采分点 / §4 解析的完整题卡（虚构内容）
_FULL_CARD = (
    "### 【题号 1】论述题（满分: 15 分）\n"
    "- **【题源出处】**：`示例真题2024`\n"
    "#### 1. 试题原题\n"
    "示例题干：论述实践与认识的辩证关系及其方法论意义。\n"
    "#### 2. 标准答案\n"
    "> 示例参考答案要点一；要点二。\n"
    "#### 3. 步骤级采分点标注 (Rubric)\n"
    "| 采分步骤 | 赋分要求 |\n"
    "|---|---|\n"
    "| `要点一` | 示例赋分要求 |\n"
    "#### 4. 命题人逻辑与私教解析\n"
    "示例解析：先立论后展开。\n"
    "---\n"
)


def test_backfilled_full_card_verifies():
    """① 带 §2/§3/§4 的卡 backfill 后 verify True。"""
    stamped, changed = qs.backfill_markdown_text(_FULL_CARD, kind="whitelist")
    assert changed == 1
    stem = qs.extract_card_stem(stamped)
    assert qs.source_from_card(stamped, origin=qs.ORIGIN_WHITELIST).verify(stem) is True


def test_editing_analysis_keeps_verify_true():
    """① 仅改 §4 解析正文 → verify 仍 True（修复目标：解析不属于题源身份）。"""
    stamped, _ = qs.backfill_markdown_text(_FULL_CARD, kind="whitelist")
    edited = stamped.replace("示例解析：先立论后展开。", "示例解析：先立论后展开，再补充反例。")
    assert edited != stamped
    stem = qs.extract_card_stem(edited)
    assert qs.source_from_card(edited, origin=qs.ORIGIN_WHITELIST).verify(stem) is True


def test_editing_answer_section_keeps_verify_true():
    """① 仅改 §2 标准答案 → verify 仍 True（checksum 不再覆盖答案小节）。"""
    stamped, _ = qs.backfill_markdown_text(_FULL_CARD, kind="whitelist")
    edited = stamped.replace("> 示例参考答案要点一；要点二。", "> 示例参考答案要点一；要点二；要点三。")
    assert edited != stamped
    stem = qs.extract_card_stem(edited)
    assert qs.source_from_card(edited, origin=qs.ORIGIN_WHITELIST).verify(stem) is True


def test_editing_stem_keeps_verify_false():
    """② 改 §1 题干 → verify False（防篡改保留）。"""
    stamped, _ = qs.backfill_markdown_text(_FULL_CARD, kind="whitelist")
    edited = stamped.replace("论述实践与认识的辩证关系", "论述实践与认识的辩证关系X")
    assert edited != stamped
    stem = qs.extract_card_stem(edited)
    src = qs.source_from_card(edited, origin=qs.ORIGIN_WHITELIST)
    assert src.checksum == "", "题干被改动的卡不得被认证"
    assert src.verify(stem) is False


def test_legacy_stamped_card_still_verifies():
    """③ 旧口径兼容（完整声明）：存量卡用旧算法盖章（含答案小节）仍 verify True。"""
    stem = qs.extract_card_stem(_FULL_CARD)
    legacy = _legacy_hash(stem)
    card = _FULL_CARD.replace(
        "- **【题源出处】**：`示例真题2024`\n",
        "- **【题源出处】**：`示例真题2024`\n"
        f"- **【题源ID】**：`{qs.ORIGIN_WHITELIST}-{legacy[:12]}`\n"
        f"- **【题源校验和】**：`{legacy}`\n",
        1,
    )
    src = qs.source_from_card(card, origin=qs.ORIGIN_WHITELIST)
    assert src.checksum == legacy, "旧口径完整声明卡被误拒（checksum 被清空）"
    assert src.verify(stem) is True


def test_legacy_id_prefix_only_card_verifies():
    """③ 旧口径兼容（弱前缀路径）：仅 ID 行（旧口径前缀）、无校验和行。"""
    stem = qs.extract_card_stem(_FULL_CARD)
    legacy = _legacy_hash(stem)
    card = _FULL_CARD.replace(
        "- **【题源出处】**：`示例真题2024`\n",
        "- **【题源出处】**：`示例真题2024`\n"
        f"- **【题源ID】**：`{qs.ORIGIN_WHITELIST}-{legacy[:12]}`\n",
        1,
    )
    src = qs.source_from_card(card, origin=qs.ORIGIN_WHITELIST)
    assert src.checksum, "弱前缀双口径未接受旧口径前缀"
    assert src.verify(stem) is True


def test_plain_card_new_and_legacy_hash_identical():
    """④ 兼容红线：无答案小节的普通卡新旧口径 hash 逐字节相同。"""
    stem = "示例题干：简述矛盾的普遍性与特殊性的辩证统一关系。"
    assert qs.compute_checksum(stem) == _legacy_hash(stem)


def test_exam_composer_regex_single_source():
    """⑤ 组卷侧无回归：剥离正则与 question_source 同源（同一对象）+ 行为抽查。"""
    assert exam._STEM_ANSWER_SUBSEC_RE is qs.ANSWER_SUBSEC_RE
    stem = qs.extract_card_stem(_FULL_CARD)
    clean, ans = exam._split_answer_from_stem(stem)
    assert "示例参考答案要点一" in ans
    assert "标准答案" not in clean and "命题人逻辑" not in clean
    assert exam._strip_answer_sections(stem) == clean
