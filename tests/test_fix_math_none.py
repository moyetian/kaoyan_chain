# -*- coding: utf-8 -*-
"""P4 / P12 / P14 回归：不考数学不得写成数二考纲 + 占位文案 + 结束指引。

缺陷背景（审查报告）：
    P4  全新副本跑文科向导后，根 AGENTS.md 写「不考数学 / 0.0 小时」，
        但 01-数学/考试大纲.md 与 01-数学/AGENTS.md 却是「数学二 (302)」并注入
        数二超纲禁区 —— 只因 MATH_SYLLABI 无 none 键而静默回退 math2。
    P12 自命题考纲正文全是 `[请根据报考院校官网大纲填入…]` 占位符，
        未明确标注「待自填」，用户易误当成品。
    P14 结束指引硬编码「数学报到」，不考数学的考生照着发会被拒绝。
"""
import pytest

from tools import init_workspace as iw
from tools import syllabus_manager as sm


@pytest.fixture
def ws(tmp_path):
    """构造最小工作区：仅需 01-数学/AGENTS.md 供「考试科目」行替换。"""
    math_dir = tmp_path / "01-数学"
    math_dir.mkdir(parents=True, exist_ok=True)
    (math_dir / "AGENTS.md").write_text(
        "# 01-数学 · 私教协议\n\n"
        "- **考试科目**：`数学二 (302)`\n"
        "- **目标分数**：`110+ 分`\n\n"
        "## 1. 超纲与题源禁区\n"
        "- 严防超出所考科目大纲的偏题怪题；\n",
        encoding="utf-8",
    )
    return tmp_path


# ── P4：不考数学 ──────────────────────────────────────────────

def test_math_none_writes_placeholder_not_math2(ws):
    math_info, _eng, _files = sm.apply_syllabus_selection(
        math_key="none", workspace_root=ws, auto_write=True)

    assert math_info["name"] == "不考数学"

    outline = (ws / "01-数学" / "考试大纲.md").read_text(encoding="utf-8")
    assert "不考数学" in outline
    assert "数学二" not in outline and "302" not in outline

    agents = (ws / "01-数学" / "AGENTS.md").read_text(encoding="utf-8")
    assert "- **考试科目**：`不考数学`" in agents
    # 关键：不得注入数二超纲禁区
    assert "数二严禁超纲禁区" not in agents
    assert "严禁考查三重积" not in agents


def test_math_custom_writes_selfdefined_placeholder(ws):
    math_info, _eng, _files = sm.apply_syllabus_selection(
        math_key="custom", workspace_root=ws, auto_write=True)

    assert math_info["name"] == "院校自主命题数学"
    outline = (ws / "01-数学" / "考试大纲.md").read_text(encoding="utf-8")
    assert "院校自主命题数学" in outline
    assert "数学二" not in outline and "302" not in outline
    assert "数二严禁超纲禁区" not in (
        ws / "01-数学" / "AGENTS.md").read_text(encoding="utf-8")


def test_math2_still_writes_real_syllabus(ws):
    math_info, _eng, _files = sm.apply_syllabus_selection(
        math_key="math2", workspace_root=ws, auto_write=True)

    assert "数学二" in math_info["name"]
    outline = (ws / "01-数学" / "考试大纲.md").read_text(encoding="utf-8")
    assert "数学二 (302)" in outline
    agents = (ws / "01-数学" / "AGENTS.md").read_text(encoding="utf-8")
    assert "数二严禁超纲禁区" in agents  # 正常路径仍注入超纲红线


# ── P4：init_workspace 菜单拆分 ────────────────────────────────

def _feed(monkeypatch, values):
    it = iter(values)
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(it))


def test_init_menu_option6_means_none(monkeypatch):
    _feed(monkeypatch, ["6", "1", "1", "801 信号与系统"])
    math_key, _eng_key, _pro_type, _pro_name = iw.choose_exam_subjects_and_syllabi(
        interactive=True)
    assert math_key == "none"


def test_init_menu_option5_means_custom(monkeypatch):
    _feed(monkeypatch, ["5", "1", "1", "801 信号与系统"])
    math_key, _eng_key, _pro_type, _pro_name = iw.choose_exam_subjects_and_syllabi(
        interactive=True)
    assert math_key == "custom"


# ── P12：占位文案明确标注「待自填」 ──────────────────────────

def test_pro_placeholder_body_marks_pending_and_guides_to_official_site(ws):
    sm.apply_syllabus_selection(
        math_key="math2", eng_key="eng2", pro_type="custom",
        pro_name="自命题科目1", workspace_root=ws, auto_write=True)

    body = (ws / "04-专业课" / "考试大纲.md").read_text(encoding="utf-8")
    assert sm.PRO_PLACEHOLDER_MARKER in body
    assert "待自填" in body
    assert "研究生院官网" in body
    # 旧的含糊占位文案不应再出现
    assert "请根据报考院校官网大纲填入" not in body


def test_pro2_placeholder_also_marked(ws):
    sm.apply_syllabus_selection(
        math_key="math2", eng_key="eng2", pro_type="custom",
        pro_name="自命题科目1", pro2_name="823 中国化马克思主义",
        workspace_root=ws, auto_write=True)

    body = (ws / "04-专业课" / "考试大纲_专业课二.md").read_text(encoding="utf-8")
    assert "待自填" in body and "研究生院官网" in body


# ── P14：结束指引口令 ────────────────────────────────────────

def test_start_command_switches_when_math_none():
    assert iw._start_command_for("none") == "英语报到"
    assert iw._start_command_for("不考数学") == "英语报到"
    assert iw._start_command_for("math2") == "数学报到"
    assert iw._start_command_for(None) == "数学报到"


# ── R2-A2：占位判定必须与生成侧标记同源，且不得误伤真实大纲 ─────────

def _km():
    from tools.skills import knowledge_map as km
    return km


def test_placeholder_marker_reuses_generator_constant():
    """判定侧复用的标记必须与生成侧 PRO_PLACEHOLDER_MARKER 完全一致（单一事实源）。"""
    assert _km()._placeholder_marker() == sm.PRO_PLACEHOLDER_MARKER


def test_p12_fullwidth_placeholder_is_detected(ws):
    """R2-A2：P12 生成的全角「【待自填」占位大纲必须判为占位。

    此前 _PLACEHOLDER_PATTERNS 只认 ASCII `[请填写…]`，全角标记一律漏判，
    占位正文被当成真实考点解析 → 产出「1 考点 / 0% 掌握率」的假图谱。
    """
    sm.apply_syllabus_selection(
        math_key="math2", eng_key="eng2", pro_type="custom",
        pro_name="自命题科目1", workspace_root=ws, auto_write=True)
    body = (ws / "04-专业课" / "考试大纲.md").read_text(encoding="utf-8")
    assert sm.PRO_PLACEHOLDER_MARKER in body
    assert _km()._is_placeholder_syllabus(body) is True


def test_marker_wins_over_line_count_heuristic():
    """R2-A2 第二重漏判：占位正文含 ≥5 行「长且不含括号」的说明行时，
    显式 marker 仍必须直接判为占位，不被 _PLACEHOLDER_MIN_REAL_LINES 否决。"""
    km = _km()
    txt = (
        "# 04-专业课 · 【618】官方考试大纲与核心考点清单\n\n"
        "> ⚠️ **【待自填·占位大纲】** 本文件为系统生成的占位模板。\n"
        "> 请前往目标院校研究生院官网下载最新自命题考试大纲。\n"
        "> 在替换之前，AI 私教不得据本文件宣称已按考纲出题。\n"
        "> 说明行一：这一行足够长且完全不含任何括号标记。\n"
        "> 说明行二：这一行足够长且完全不含任何括号标记。\n"
        "> 说明行三：这一行足够长且完全不含任何括号标记。\n"
        "> 说明行四：这一行足够长且完全不含任何括号标记。\n"
    )
    # 去掉 marker 后，行数启发式会判为「非占位」——正是旧实现的漏判场景
    assert km._is_placeholder_syllabus(txt) is True
    assert km._is_placeholder_syllabus(txt.replace(km._placeholder_marker(), "X")) is False


def test_legacy_ascii_placeholder_still_detected():
    """兼容旧占位文案（ASCII 方括号形态）。"""
    txt = ("# 04-专业课 · 考试大纲\n\n## 核心考查章节\n"
           "- 第一章：[请根据报考院校官网大纲填入] (要求：掌握)\n")
    assert _km()._is_placeholder_syllabus(txt) is True


@pytest.mark.parametrize("key", ["math1", "math2", "math3", "math396"])
def test_real_math_syllabi_are_not_misflagged(key):
    """防「过度识别」：真实统考大纲不得被判成占位模板。"""
    assert _km()._is_placeholder_syllabus(sm.MATH_SYLLABI[key]["content"]) is False


@pytest.mark.parametrize("key", ["eng1", "eng2"])
def test_real_english_syllabi_are_not_misflagged(key):
    assert _km()._is_placeholder_syllabus(sm.ENGLISH_SYLLABI[key]["content"]) is False


def test_politics_and_408_real_syllabi_not_misflagged():
    km = _km()
    assert km._is_placeholder_syllabus(sm.POLITICS_SYLLABUS) is False
    assert km._is_placeholder_syllabus(sm.CS408_SYLLABUS) is False


def test_knowledge_map_refuses_placeholder_graph(monkeypatch, tmp_path):
    """占位大纲不得产出假图谱：syllabus_placeholder=True 且 0 考点、0 章节。

    正文刻意带足 5+ 行「长且不含括号」的说明行，确保**只有** marker 判定能命中
    （否则行数启发式会放过它，正是 R2-A2 的漏判现场）。
    """
    km = _km()
    pro_dir = tmp_path / "04-专业课"
    pro_dir.mkdir(parents=True)
    (pro_dir / "考试大纲.md").write_text(
        "# 04-专业课 · 【618】官方考试大纲与核心考点清单\n\n"
        "> ⚠️ **【待自填·占位大纲】** 本文件为系统生成的占位模板。\n"
        "> 请前往目标院校研究生院官网下载最新大纲。\n"
        "> 说明行一：足够长且不含任何括号标记的一行说明。\n"
        "> 说明行二：足够长且不含任何括号标记的一行说明。\n"
        "> 说明行三：足够长且不含任何括号标记的一行说明。\n"
        "> 说明行四：足够长且不含任何括号标记的一行说明。\n",
        encoding="utf-8")
    monkeypatch.setattr(km, "ROOT", tmp_path)

    data = km.build_knowledge_map("pro")
    assert data.get("syllabus_placeholder") is True
    assert data.get("total_points") == 0
    assert data.get("chapters") == []
    # 必须给出「图谱不可用 / 先补录考纲」的明确告警
    assert "占位" in (data.get("syllabus_warning") or "")
    # 终端报表必须显式拒绝生成图谱
    assert "图谱不可用" in km.format_knowledge_map_table("pro")


