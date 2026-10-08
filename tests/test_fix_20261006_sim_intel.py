# -*- coding: utf-8 -*-
"""W3 回归（仿真发现 F4/F6/F7/F10/F13 · 情报/大纲层，2026-10-06 修复批次）。

缺陷背景（三考生×六环节×三端仿真 18 格 + 代码链复核）：
    F4  双校对标把 hedge「(408)…或院校自命题」当确认 → 误报「均统一采用 408」
        （A-P5/B-P5 复现）。
    F6  「## 附：参考书目」被建为章节，4 条书目兜底成「未标注」考点（B 实测
        106 条含 4 条书目）。
    F7  311（统考）拿到「请前往目标院校研究生院官网下载最新**自命题**考试大纲」
        的错误指引（C-P1）；已收录学校的 pitfalls 模板无条件写「紧跟**自命题**
        真题」（C-P5）。
    F10 985 简称裸子串（「南大」∈「河南大学」、「北大」∈「湖北大学」）+ 无条件
        覆盖 registry 权威 level → 实测误标 8 校。
    F13 compare_files 对同文件自我对照 / 【待自填】占位基准照常产出「0.0% 稳定」
        报告（C-P6 观察）。独立验证补充：占位检测下沉 compare_texts（覆盖
        ky fetch diff 无参 / REPL /diff 等无路径直调方），警示打印收口到
        render_syllabus_diff（CLI/REPL 共用）。
"""
import pytest


@pytest.fixture(autouse=True)
def _stub_llm_advice(monkeypatch):
    """[P2 修复·2026-10-08] 隔离 format_diff_markdown 默认路径的真实 LLM 调用。

    ``format_diff_markdown`` 不传 ``strategic_advice`` 时经
    ``generate_llm_advice`` 生成战术建议（向后兼容设计）；真实仓库
    ``is_llm_configured=True``，F13 的 3 个 format 用例会各发起一次真实
    计费调用（spy 实测 1 次 timeout=12s）。统一打桩为「离线态」（返回
    None → 走通用模板），断言内容不变、隔离网络副作用与耗时。
    """
    from tools.intelligence.syllabus_diff import SyllabusDiffGenerator
    monkeypatch.setattr(SyllabusDiffGenerator, "generate_llm_advice",
                        lambda self, report_data: None)


# ── F4：双校对标 408 过度断言 ────────────────────────────────────────────

def _profile(items, region="河南"):
    """构造 _analyze_differences 可消费的最小画像（subject_status 留空 →
    不走 structured_ready 分支，直达 408 判定分支）。"""
    return {"exam_subjects": items, "subject_status": "", "region": region}


def _subj(code, name):
    return {"code": code, "name": name}


def _analyze(name1, items1, name2, items2, major="计算机"):
    from tools.intelligence.comparator import SchoolComparator
    return SchoolComparator()._analyze_differences(
        name1, _profile(items1), name2, _profile(items2), major)


_HEDGE_408 = _subj("408", "计算机学科专业基础或院校自命题")
_CONFIRMED_408 = _subj("408", "计算机学科专业基础")
_OTHER = _subj("811", "信号与系统")


def test_f4_hedge_hedge_never_claims_unified_408():
    res = _analyze("甲校", [_HEDGE_408], "乙校", [_HEDGE_408])
    diff = res["subject_diff"]
    assert "均统一采用" not in diff
    assert "待核验" in diff  # 如实提示待核验成分
    assert "核验" in diff


def test_f4_confirmed_confirmed_still_claims_unified_408():
    res = _analyze("甲校", [_CONFIRMED_408], "乙校", [_CONFIRMED_408])
    assert "均统一采用" in res["subject_diff"]


def test_f4_hedge_plus_no_408_does_not_assert_national_exam():
    res = _analyze("甲校", [_HEDGE_408], "乙校", [_OTHER])
    diff = res["subject_diff"]
    assert "采用全国统考 408" not in diff
    assert "均统一采用" not in diff


def test_f4_confirmed_plus_hedge_does_not_claim_unified():
    # 一侧确认、另一侧 hedge：也不得断言两校统一（hedge 侧尚未核验）。
    res = _analyze("甲校", [_CONFIRMED_408], "乙校", [_HEDGE_408])
    diff = res["subject_diff"]
    assert "均统一采用" not in diff
    assert "待核验" in diff


def test_f4_confirmed_plus_other_keeps_single_school_claim():
    # 确认 408 vs 无 408：保留既有「【甲校】采用全国统考 408」单校断言。
    res = _analyze("甲校", [_CONFIRMED_408], "乙校", [_OTHER])
    assert "【甲校】采用全国统考 408" in res["subject_diff"]


# ── F6：参考书目不得计为考点 ─────────────────────────────────────────────

def test_f6_appendix_book_list_excluded_from_knowledge_map(tmp_path, monkeypatch):
    from tools.skills import knowledge_map as km

    # 隔离错题扫描（默认会读工作区错题本，与本用例无关）
    monkeypatch.setattr(km, "error_logger", None, raising=False)

    pro_dir = tmp_path / "04-专业课"
    pro_dir.mkdir(parents=True, exist_ok=True)
    (pro_dir / "考试大纲.md").write_text(
        "# 大纲\n\n"
        "## 第一章 马克思主义基本原理\n"
        "- 世界的物质性 (要求：掌握)\n"
        "- 实践与认识 (要求：理解)\n"
        "\n"
        "## 附：参考书目\n"
        "- 《马克思主义基本原理概论》高等教育出版社\n"
        "- 《毛泽东思想和中国特色社会主义理论体系概论》\n"
        "- 《习近平新时代中国特色社会主义思想概论》\n"
        "- 《思想政治教育学原理》\n",
        encoding="utf-8",
    )

    data = km.build_knowledge_map("pro", root=tmp_path)
    titles = list(data["modules"].keys())
    assert not any("参考书目" in t for t in titles)
    # 真实考点数不受书目影响：仅第一章 2 条
    assert data["total_points"] == 2
    assert all("《" not in p["name"]
               for c in data["chapters"] for p in c["points"])


def test_f6_bullets_after_appendix_not_counted(tmp_path, monkeypatch):
    """附录章节必须阻断其下条目：附录后再出现新章节才恢复解析。"""
    from tools.skills import knowledge_map as km

    monkeypatch.setattr(km, "error_logger", None, raising=False)

    pro_dir = tmp_path / "04-专业课"
    pro_dir.mkdir(parents=True, exist_ok=True)
    (pro_dir / "考试大纲.md").write_text(
        "## 第一章 数据结构\n"
        "- 线性表 (要求：掌握)\n"
        "- 栈与队列 (要求：理解)\n"
        "## 参考文献\n"
        "- [1] 严蔚敏. 数据结构(C语言版)\n"
        "- [2] 王道论坛. 数据结构考研复习指导\n"
        "## 第二章 操作系统\n"
        "- 进程管理 (要求：掌握)\n"
        "- 内存管理 (要求：掌握)\n",
        encoding="utf-8",
    )
    data = km.build_knowledge_map("pro", root=tmp_path)
    names = [p["name"] for c in data["chapters"] for p in c["points"]]
    assert "线性表" in names and "进程管理" in names
    assert not any("严蔚敏" in n or "王道论坛" in n for n in names)
    assert data["total_points"] == 4


# ── F7：占位文案分档 + pitfalls 中性化 ───────────────────────────────────

def test_f7_unified_placeholder_guides_to_moe_not_self_defined():
    from tools import syllabus_manager as sm

    body = sm._pro_placeholder_body("311 教育学专业基础", "教育学原理")
    assert "教育部" in body and "统考" in body
    assert "自命题考试大纲" not in body


def test_f7_self_defined_placeholder_keeps_university_guide():
    from tools import syllabus_manager as sm

    body = sm._pro_placeholder_body("801 信号与系统", "信号与系统")
    assert "自命题考试大纲" in body
    assert "研究生院官网" in body


def test_f7_is_unified_pro_subject_detection():
    from tools import syllabus_manager as sm

    assert sm.is_unified_pro_subject("custom", "311 教育学专业基础") is True
    assert sm.is_unified_pro_subject("408", "计算机学科专业基础") is True
    assert sm.is_unified_pro_subject("199", "管理类综合能力") is True
    assert sm.is_unified_pro_subject("custom", "801 信号与系统") is False
    assert sm.is_unified_pro_subject("custom", "610 法学基础") is False
    # 前后紧邻数字的自命题编号不得误判为统考
    assert sm.is_unified_pro_subject("custom", "1811 自命题综合") is False


def test_f7_books_placeholder_text_branches_by_unified(tmp_path):
    from tools import syllabus_manager as sm

    pro_dir = tmp_path / "04-专业课"
    pro_dir.mkdir(parents=True, exist_ok=True)
    (pro_dir / "考试大纲.md").write_text("【待自填】占位", encoding="utf-8")

    unified_txt = sm.pro_books_placeholder_text(tmp_path, "311 教育学专业基础")
    selfdef_txt = sm.pro_books_placeholder_text(tmp_path, "801 信号与系统")
    assert "教育部" in unified_txt and "统考" in unified_txt
    assert "研究生院官网" in selfdef_txt


def test_f7_pitfalls_template_neutral_for_unified_subject():
    from tools.intelligence.agentic_research import AgenticResearchEngine

    prof = AgenticResearchEngine().dynamic_fallback_profile("河南大学", "教育学")
    pitfalls = prof["pitfalls"]
    assert "紧跟自命题真题" not in pitfalls
    assert "统考科目以教育部考试大纲为准" in pitfalls
    assert "自命题科目以院校指定大纲为准" in pitfalls


# ── F10：院校层次 985 误标 ───────────────────────────────────────────────

@pytest.mark.parametrize("name", ["河南大学", "湖北大学", "西北大学", "西南大学"])
def test_f10_lookalike_names_not_top_985(name):
    from tools.skills.school_scout import _is_top_985_name

    assert _is_top_985_name(name) is False


def test_f10_exact_full_names_match_with_suffix_stripping():
    from tools.skills.school_scout import _is_top_985_name

    assert _is_top_985_name("清华大学") is True
    assert _is_top_985_name("清华大学（北京）") is True
    assert _is_top_985_name(" 南开大学 ") is True
    assert _is_top_985_name("河南大学") is False


def test_f10_henan_university_level_not_985_but_double_first_class():
    from tools.skills.school_scout import infer_general_school_intel

    intel = infer_general_school_intel("河南大学", "教育学")
    assert "985" not in intel["level"]
    assert "211" not in intel["level"]
    assert "双一流" in intel["level"]


def test_f10_registry_hit_keeps_authoritative_level(monkeypatch):
    from types import SimpleNamespace

    from tools.intelligence import registry as registry_mod
    from tools.skills.school_scout import infer_general_school_intel

    class _FakeRegistry:
        def resolve(self, name):
            return SimpleNamespace(
                name="南开大学", level=["双一流建设高校"], region="天津",
                official_domain="nankai.edu.cn", graduate_domain="",
            )

    monkeypatch.setattr(registry_mod, "get_registry", lambda: _FakeRegistry())
    intel = infer_general_school_intel("南开大学", "教育学")
    # registry 命中时 985 全名启发式不得覆盖权威 level
    assert "985" not in intel["level"]
    assert "双一流建设高校" in intel["level"]


# ── F13：diff 占位/自我对照警示 ──────────────────────────────────────────

def _write_syllabus(path, body):
    path.write_text(body, encoding="utf-8")
    return path


def test_f13_same_file_sets_baseline_warning_and_markdown(tmp_path):
    from tools.intelligence.syllabus_diff import SyllabusDiffGenerator

    gen = SyllabusDiffGenerator()
    p = _write_syllabus(tmp_path / "408考纲.md",
                        "## 一、数据结构\n- 线性表 (要求：掌握)\n")
    rep = gen.compare_files(p, p)
    warning = rep.get("baseline_warning")
    assert warning and "同一文件" in warning
    md = gen.format_diff_markdown(rep)
    assert "> ⚠️" in md and "同一文件" in md


def test_f13_same_file_placeholder_same_file_warning_wins(tmp_path):
    """同文件且内容为占位模板 → 同文件警示优先（语义更具体，优先级契约）。"""
    from tools.intelligence.syllabus_diff import SyllabusDiffGenerator

    gen = SyllabusDiffGenerator()
    p = _write_syllabus(tmp_path / "占位考纲.md",
                        "【待自填】请替换为真实大纲\n## 一、章节\n")
    rep = gen.compare_files(p, p)
    warning = rep.get("baseline_warning")
    assert warning and "同一文件" in warning


def test_f13_placeholder_old_sets_baseline_warning(tmp_path):
    from tools.intelligence.syllabus_diff import SyllabusDiffGenerator

    gen = SyllabusDiffGenerator()
    old = _write_syllabus(
        tmp_path / "old.md", "【待自填】请替换为真实大纲\n## 一、章节\n- 考点 (要求：掌握)\n")
    new = _write_syllabus(
        tmp_path / "new.md", "## 一、章节\n- 考点A (要求：掌握)\n- 考点B (要求：掌握)\n")
    rep = gen.compare_files(old, new)
    warning = rep.get("baseline_warning")
    assert warning and "占位" in warning
    md = gen.format_diff_markdown(rep)
    assert "> ⚠️" in md


def test_f13_normal_two_files_no_baseline_warning(tmp_path):
    from tools.intelligence.syllabus_diff import SyllabusDiffGenerator

    gen = SyllabusDiffGenerator()
    old = _write_syllabus(
        tmp_path / "old.md", "## 一、章节\n- 考点A (要求：掌握)\n- 考点B (要求：了解)\n")
    new = _write_syllabus(
        tmp_path / "new.md", "## 一、章节\n- 考点A (要求：掌握)\n- 考点C (要求：掌握)\n")
    rep = gen.compare_files(old, new)
    assert not rep.get("baseline_warning")
    md = gen.format_diff_markdown(rep)
    assert "> ⚠️" not in md


# ── F13 补充（独立验证上报 1）：无路径直调 compare_texts 的占位警示 ──────

def test_f13_compare_texts_placeholder_sets_warning_directly():
    """ky fetch diff 无参 / REPL /diff 走 compare_texts（无文件路径），
    对【待自填】占位基准同样须产生 baseline_warning（此前仅 compare_files 有）。"""
    from tools.intelligence.syllabus_diff import SyllabusDiffGenerator

    gen = SyllabusDiffGenerator()
    rep = gen.compare_texts(
        old_text="【待自填】请替换为真实大纲\n## 一、章节\n- 考点 (要求：掌握)\n",
        new_text="## 一、章节\n- 考点A (要求：掌握)\n- 考点B (要求：掌握)\n",
        school="测试大学", major="专业课",
    )
    warning = rep.get("baseline_warning")
    assert warning and "占位" in warning


def test_f13_compare_texts_normal_no_warning_directly():
    """阴性对照：正常基准文本直调 compare_texts 不产生警示。"""
    from tools.intelligence.syllabus_diff import SyllabusDiffGenerator

    gen = SyllabusDiffGenerator()
    rep = gen.compare_texts(
        old_text="## 一、章节\n- 考点A (要求：掌握)\n",
        new_text="## 一、章节\n- 考点A (要求：掌握)\n- 考点B (要求：了解)\n",
    )
    assert not rep.get("baseline_warning")


# ── F13 补充：警示打印收口 render_syllabus_diff（CLI/REPL 共用入口） ──────

_F13_METRICS = {
    "stability_grade": "稳定", "volatility_percentage": 0.0,
    "total_old": 1, "total_new": 1,
    "added_count": 0, "removed_count": 0,
    "modified_count": 0, "unchanged_count": 1,
}


def test_f13_render_syllabus_diff_prints_baseline_warning(capsys):
    from tools.cli.commands.intel import render_syllabus_diff

    rep = {"metrics": dict(_F13_METRICS), "diff_items": [],
           "baseline_warning": "基准（旧）大纲为【待自填】占位模板，比对不代表真实考纲变动；请先导入真实旧版大纲"}
    render_syllabus_diff(rep, "测试大学", "专业课", 2026, 2027)
    out = capsys.readouterr().out
    assert "基准警示" in out and "待自填" in out


def test_f13_render_syllabus_diff_silent_without_warning(capsys):
    """阴性对照：无警示键时渲染器不打警示行。"""
    from tools.cli.commands.intel import render_syllabus_diff

    rep = {"metrics": dict(_F13_METRICS), "diff_items": []}
    render_syllabus_diff(rep, "测试大学", "专业课", 2026, 2027)
    out = capsys.readouterr().out
    assert "考纲变动全景看板" in out, "正常渲染路径未走到，前置条件不成立"
    assert "基准警示" not in out
