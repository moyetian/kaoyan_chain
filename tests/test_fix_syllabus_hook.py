# -*- coding: utf-8 -*-
"""P18/P19 回归测试：数二考纲红线拦截的覆盖度与否定语境豁免。

背景（多角色端到端测试审查报告 P18/P19）：
    tools/agent/hooks.py::syllabus_guard_hook 的函数 docstring 明确声明
    「math2 严禁：… / 向量代数与空间解析几何 / 欧拉方程 / 伯努利方程」，
    但 forbidden_core / forbidden_math2_only 两个列表里**根本没有这些词**，
    于是数二学员问「讲一下欧拉方程」「概率论怎么复习」时 Hook 完全放行，
    与它自己文档化的契约不符 —— 超纲题照样派发。

    另一面（P19），_is_negation_or_note_context 的否定词表只有
    不考/绝不考/严禁/严防/无需/除外/排除/非考点/不涉及/不包含/不要求，
    缺少「不用 / 禁用 / 避免 / 不必」等日常口语否定词。学员说
    「不用讲三重积分了」会被误判为超纲派题而硬阻断，属误伤。

本测试同时锁定这两侧行为：该拦的必须拦，该放的必须放。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent.hooks import HookManager  # noqa: E402


def _guard(tool_args_text: str, math_key: str = "math2", subject: str = "math"):
    """以真实运行时上下文调用考纲守卫 Hook，返回 (allow, reason)。"""
    hm = HookManager(workspace_root=ROOT)
    ctx = {
        "active_subject": subject,
        "math_key": math_key if subject == "math" else None,
        "user_input": tool_args_text,
    }
    allow, reason, _ = hm.trigger_pre_tool_use("generate_exam", {"query": tool_args_text}, ctx)
    return allow, reason


# ────────────────────────── P18：漏网的超纲考点必须被拦 ──────────────────────────
@pytest.mark.parametrize("term", [
    "概率论",          # 数学二不含概率论与数理统计
    "空间解析几何",    # 数学二不含向量代数与空间解析几何
    "欧拉方程",        # 数学二常微分方程不含欧拉方程
    "伯努利方程",      # 数学二常微分方程不含伯努利方程
])
def test_math2_banned_terms_are_intercepted(term):
    """docstring 已声明的数二红线考点，必须真的被 Hook 阻断。"""
    allow, reason = _guard(f"请给学员出一道关于{term}的强化题")
    assert allow is False, f"数二红线考点未被拦截（Hook 与自身文档不符）: {term}"
    assert term in reason or "红线" in reason


def test_math2_core_terms_still_intercepted():
    """原有核心红线不得回归。"""
    for term in ["三重积分", "曲线积分", "曲面积分", "格林公式", "高斯公式", "无穷级数"]:
        allow, _ = _guard(f"出一道{term}的题")
        assert allow is False, f"核心红线回归放行: {term}"


# ────────────────── R2-B1：Hook 禁令清单必须与数二考纲正文逐项一致 ──────────────────
#: 数二考纲正文（tools/syllabus_manager.py 的 MATH_SYLLABI["math2"]["content"]，即
#: 01-数学/考试大纲.md 的「绝不超纲铁律」）逐项列出的禁区考点。
#: 守卫清单比考纲松 = 开后门，故此处做**双向**断言：考纲列了 → Hook 必须拦。
MATH2_BANNED_TERMS = [
    "三重积分", "曲线积分", "曲面积分", "格林公式", "高斯公式", "斯托克斯公式",
    "无穷级数", "傅里叶级数", "空间解析几何", "概率论", "欧拉方程", "伯努利方程",
]
#: 考纲正文措辞与 Hook 关键词偶有差异（考纲写「伯努利微分方程」）
_OUTLINE_ALIAS = {"伯努利方程": "伯努利"}


def test_hook_banlist_matches_math2_syllabus_outline():
    """[R2-B1] 数二考纲列为禁区的考点，Hook 一个都不能漏（此前漏「斯托克斯公式」）。"""
    from syllabus_manager import MATH_SYLLABI

    outline = MATH_SYLLABI["math2"]["content"]
    for term in MATH2_BANNED_TERMS:
        probe = _OUTLINE_ALIAS.get(term, term)
        assert probe in outline, f"数二考纲正文未列该禁区（测试基线需同步更新）: {term}"
        allow, reason = _guard(f"请给学员出一道关于{term}的强化题")
        assert allow is False, f"考纲列为禁区但 Hook 放行（守卫比考纲松）: {term}"


def test_stokes_term_intercepted():
    """[R2-B1] 斯托克斯公式属数二线面积分禁区，必须被拦截。"""
    allow, reason = _guard("出一道斯托克斯公式的题")
    assert allow is False, "斯托克斯公式未被拦截"
    assert "斯托克斯公式" in reason


@pytest.mark.parametrize("text", [
    "需要掌握格林公式",
    "这道题考斯托克斯公式",
    "请讲一下三重积分",
    "概率论与数理统计怎么复习",
])
def test_positive_context_still_intercepted(text):
    """[R2-B2] 扩展否定词后，正向语境不得被顺手放开（防止误放行）。"""
    allow, reason = _guard(text)
    assert allow is False, f"正向语境被误放行: {text} → {reason}"


# ────────────────────────── P19：否定语境必须豁免（不得误伤） ──────────────────────────
@pytest.mark.parametrize("text", [
    "不用讲三重积分了，直接跳过",
    "禁用欧拉方程相关题目",
    "避免派发伯努利方程的题",
    "不必安排概率论的练习",
    "数二不考三重积分，请略过",
])
def test_negation_context_is_exempted(text):
    """学员明确表达「不做/不讲」时不得硬阻断（否定语境豁免）。"""
    allow, reason = _guard(text)
    assert allow is True, f"否定语境被误判为超纲派题而阻断: {text} → {reason}"


# ────────────────── R2-B2：连缀动词 + 冒号式否定语境同样不得误伤 ──────────────────
@pytest.mark.parametrize("text", [
    "不需要掌握格林公式",
    "不需要格林公式",
    "无需掌握格林公式",
    "不要求掌握曲面积分",
    "非考点：欧拉方程",
])
def test_extended_negation_context_is_exempted(text):
    """[R2-B2] 否定词与考点间连缀动词（不需要**掌握**）或夹冒号（非考点：）时不得误拦。"""
    allow, reason = _guard(text)
    assert allow is True, f"连缀动词/冒号式否定语境被误拦: {text} → {reason}"


def test_math1_wide_scope_not_intercepted():
    """数学一范围最广，同词不得拦截（避免过度收紧）。"""
    allow, _ = _guard("出一道三重积分的题", math_key="math1")
    assert allow is True


def test_non_math_subject_untouched():
    """非数学科目不得被数学红线拦截。"""
    allow, _ = _guard("出一道三重积分的题", subject="pro")
    assert allow is True


# ──────────── C1 补漏：口语否定词「别 / 不要 / 不再 / 不做」────────────
# 背景（C1 建考纲评测集时实测发现）：P19 补了「不用 / 禁用 / 避免 / 不必」，
# 但「别讲曲面积分」「不要复习概率论」这类最常用的口语否定仍被误判为超纲
# 派题而硬阻断（与 P19 同类的误伤）。本组做**双向**断言：
# 该豁免的必须豁免，含「别」的其他词（区别/分别/特别）必须仍被拦截。
@pytest.mark.parametrize("text", [
    "别讲曲面积分",
    "别做曲面积分的题",
    "不要讲三重积分",
    "不要复习概率论",
    "不再讲无穷级数",
    "不做欧拉方程",
    "别出三重积分的题",
    "别刷三重积分",
])
def test_colloquial_negation_is_exempted(text):
    """[C1] 口语否定词（别/不要/不再/不做）必须豁免，不得误伤正常教学对话。"""
    allow, reason = _guard(text)
    assert allow is True, f"口语否定语境被误拦: {text} → {reason}"


@pytest.mark.parametrize("text", [
    "区别三重积分与二重积分",
    "分别计算三重积分",
    "特别要注意三重积分",
    "个别同学三重积分不熟",
])
def test_negation_lookalike_still_intercepted(text):
    """[C1] 阴性对照：含「别」的其他词（区别/分别/特别/个别）不得被误认为否定语境。

    若这条变红，说明「别」的匹配过宽 —— 真正该拦的超纲派题会漏网。
    """
    allow, reason = _guard(text)
    assert allow is False, f"含「别」的非否定词被误放行: {text} → {reason}"
