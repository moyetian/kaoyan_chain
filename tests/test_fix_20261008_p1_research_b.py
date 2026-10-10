# -*- coding: utf-8 -*-
"""[P1 修复·2026-10-08 深度研究 B 组] R3/R4/R5/R6/R7/R10 六条回归测试。

覆盖：
  * **R3** compare 双层预算矛盾：外层熔断剩余墙钟派生内层在线研究预算
    （旧实现内层硬编码 200s，被外层 60s 熔断永远屏蔽）。
  * **R4** 引文闸门：全角括号原文定位（旧半角重组引文误杀真证据）+
    逐科目校验（旧实现只校验第一条，闸门覆盖 25%）。
  * **R5** 信源分级表补 admission_office / official_discovered 键 +
    未登记 type 打 WARNING（旧实现静默降级为「VERIFIED + D 级」）。
  * **R6** 空专业关键词不再静默命中 TARGET_SCHOOLS_DB 首个部门。
  * **R7** 6 位专业代码不再截断为 3 位假科目代码。
  * **R10** 初试科目四种 field 名归一「初试科目」+ 冲突仲裁分组生效。

夹具全部中性化：合成校名/合成 HTML/合成 DB，在线研究与真实 DB 全部打桩，
不联网、不写真实仓库。
"""

import importlib
import logging
import sys

import pytest

from tools.intelligence.chsi_connector import CHSIConnector
from tools.intelligence.citation_engine import verify_citation_excerpt
from tools.intelligence.comparator import SchoolComparator
from tools.intelligence.evidence_engine import (
    build_evidence,
    get_source_level,
    get_source_score,
    resolve_conflicts,
)
from tools.intelligence.extractor import DocumentExtractor, _locate_subject_span
from tools.intelligence.subject_catalog import (
    format_subject_items,
    normalize_subject_items,
    profile_subject_items,
)

# ── 合成页面：官方站大量使用全角「（101）」写法（R4 的实测形态） ──
_HTML_FULLWIDTH = (
    "<html><head><title>测试大学2027年硕士研究生招生简章</title></head>"
    "<body><p>初试科目：（101）思想政治理论、（204）英语（二）、"
    "（302）数学（二）、（408）计算机学科专业基础</p></body></html>"
)
_HTML_HALFWIDTH = (
    "<html><head><title>测试大学2027年硕士研究生招生简章</title></head>"
    "<body><p>初试科目：(101)思想政治理论、(204)英语(二)、"
    "(302)数学(二)、(408)计算机学科专业基础</p></body></html>"
)

_PDF_TEXT = ("测试大学2027年硕士研究生招生专业目录 初试科目："
             "(101)思想政治理论 (204)英语(二) (302)数学(二) (814)通信原理")


# ═══════════════════ R3：双层预算单一真源 ═══════════════════

class TestR3BudgetSingleSource:
    """R3：内层在线研究预算必须由外层 compare 熔断的剩余墙钟派生。"""

    @staticmethod
    def _stub_capture(monkeypatch):
        """打桩在线研究，捕获每次调用收到的 budget_s（不联网）。"""
        import tools.intelligence.agentic_research as ar
        captured = []

        def _stub(school_name, major_keyword="", api_config=None, budget_s=240.0):
            captured.append((school_name, budget_s))
            return {"name": school_name, "code": "10000", "level": "测试层次",
                    "region": "北京", "official": "", "graduate": "",
                    "majors": ["(101)思想政治理论"],
                    "exam_subjects": [{"code": "101", "name": "思想政治理论"}],
                    "subject_status": "catalog_record", "subject_source": "stub",
                    "catalog_source": "[STUB 在线研究打桩]",
                    "score_trend": "未核验", "ratio": "未核验", "protect": "未核验",
                    "reputation": "", "pitfalls": ""}

        monkeypatch.setattr(ar, "research_university_profile", _stub)
        return captured

    def test_inner_budget_derives_from_outer_timeout(self, monkeypatch):
        """timeout=5 → 内层收到 ≈5s 剩余预算（而非旧硬编码 200s）。"""
        captured = self._stub_capture(monkeypatch)
        comp = SchoolComparator()
        comp._get_two_profiles("测试大学甲", None, "测试大学乙", None,
                               "测试专业", timeout=5.0)
        assert len(captured) == 2
        assert all(0 < b <= 5.0 for _, b in captured), \
            f"内层预算应由外层剩余墙钟派生: {captured}"
        assert all(b != 200.0 for _, b in captured), \
            "旧实现内层恒为 200s（被外层熔断永远屏蔽），修复后不得回退"

    def test_no_timeout_keeps_200s_budget(self, monkeypatch):
        """timeout<=0（不熔断）→ 沿用 200s 研究上限（保持旧行为）。"""
        captured = self._stub_capture(monkeypatch)
        comp = SchoolComparator()
        comp._get_two_profiles("测试大学甲", None, "测试大学乙", None,
                               "测试专业", timeout=0)
        assert len(captured) == 2
        assert all(b == 200.0 for _, b in captured), f"不熔断场景: {captured}"

    def test_default_timeout_derives_within_60s(self, monkeypatch):
        """默认（None）→ 60s 熔断预算派生到内层。"""
        captured = self._stub_capture(monkeypatch)
        comp = SchoolComparator()
        comp._get_two_profiles("测试大学甲", None, "测试大学乙", None, "测试专业")
        assert len(captured) == 2
        assert all(0 < b <= 60.0 for _, b in captured), f"默认 60s: {captured}"

    def test_budget_never_exceeds_total_on_same_tick(self, monkeypatch):
        """[R11 修复·浮点边界] 粗粒度单调时钟（Windows CI ~15.6ms）下
        worker 与 deadline 构造落在同一 tick：``deadline - now`` 退化为
        ``(start + budget) - start``，浮点舍入可致结果略超 budget
        （CI win3.10 实测 60.00000000000006，打红 ``<= 60`` 边界断言）。
        修复后夹紧到总预算，「剩余墙钟 ≤ 总预算」恒成立。
        打桩值经本地扫描确认可稳定触发该舍入方向（+2.3e-13）。"""
        import types
        import tools.intelligence.comparator as cmp_mod
        _tick = 1988.085000000923
        assert (_tick + 60.0) - _tick > 60.0, \
            "打桩前提失效：该值不再触发浮点越界，请重新扫描同 tick 值"
        monkeypatch.setattr(cmp_mod, "time",
                            types.SimpleNamespace(monotonic=lambda: _tick))
        captured = self._stub_capture(monkeypatch)
        comp = SchoolComparator()
        comp._get_two_profiles("测试大学甲", None, "测试大学乙", None, "测试专业")
        assert len(captured) == 2
        assert all(b <= 60.0 for _, b in captured), f"同 tick 浮点舍入越界: {captured}"


# ═══════════════════ R4：引文闸门（全角括号 + 逐科目） ═══════════════════

class TestR4CitationGate:
    """R4：引文用原文片段 group(0)；每条科目都必须有原文出处。"""

    def test_fullwidth_quote_located_and_verified(self):
        span = _locate_subject_span([_HTML_FULLWIDTH], "(101)思想政治理论")
        assert span == "（101）思想政治理论"
        ok, _ = verify_citation_excerpt(span, [_HTML_FULLWIDTH])
        assert ok, "原文片段（全角）应逐字命中"

    def test_old_halfwidth_quote_misfires_on_fullwidth_source(self):
        """对照：旧实现引文（半角重组格式）对全角原文逐字命中失败 → 误杀。"""
        ok, _ = verify_citation_excerpt("(101)思想政治理论", [_HTML_FULLWIDTH])
        assert ok is False

    def test_extract_html_fullwidth_subjects_pass_gate(self):
        """端到端：全角科目页的「初试科目」证据不再被引文闸门误杀。"""
        evs = DocumentExtractor().extract_from_html(
            _HTML_FULLWIDTH, "https://yz.example.edu.cn/n/1", "测试大学",
            target_year=2027)
        subs = [e for e in evs if e.field == "初试科目"]
        assert len(subs) == 1
        assert subs[0].status == "VERIFIED", subs[0].conflict_detail
        assert len(subs[0].value) == 4
        # 逐科目校验：value 每条都能在原文定位（旧实现只校验第一条）
        assert all(_locate_subject_span([_HTML_FULLWIDTH], it) for it in subs[0].value)

    def test_unlocatable_item_returns_none(self):
        """阴性：无原文出处的条目定位失败（会被剔除，不进入 value）。"""
        assert _locate_subject_span([_HTML_FULLWIDTH], "(999)不存在的科目") is None


# ═══════════════════ R5：信源分级补键 + 未登记 WARNING ═══════════════════

class TestR5SourceRegistry:
    """R5：招生办/官方发现两键补登记；未登记 type 静默降级改显式 WARNING。"""

    def test_admission_office_not_downgraded(self):
        ev = build_evidence("测试字段", "值", "", 2027, "admission_office",
                            "测试大学招生办", "https://yz.example.edu.cn")
        assert ev.source.level == "A", "旧实现缺键 → 兜底 D 级"
        assert ev.confidence == 0.95
        assert ev.status == "VERIFIED", "旧实现产生「VERIFIED + D级」自相矛盾"

    def test_official_discovered_registered(self):
        assert get_source_level("official_discovered") == "A"
        assert get_source_score("official_discovered") == 90

    def test_unregistered_type_warns_and_falls_back(self, caplog):
        from tools.intelligence import evidence_engine as ee
        uniq = "probe_unregistered_type_r5_regression"
        ee._WARNED_UNKNOWN_SOURCE_TYPES.discard(uniq)  # 幂等：清掉可能的历史警告标记
        with caplog.at_level(logging.WARNING,
                             logger="tools.intelligence.evidence_engine"):
            assert get_source_level(uniq) == "D"
            assert get_source_score(uniq) == 50
        assert any("未登记" in r.getMessage() for r in caplog.records), \
            "未登记信源类型的静默降级必须打 WARNING"


# ═══════════════════ R6：空专业关键词守卫 ═══════════════════

_FAKE_SCHOOL_DB = {
    "测试大学": {
        "level": "测试层次",
        "region": "北京",
        "pro_departments": {
            "计算机": {
                "major_code": "081200",
                "majors": ["081200 计算机科学与技术"],
                "exam_subjects": [{"code": "408", "name": "计算机学科专业基础"}],
                "subject_status": "catalog_record",
                "score_trend": "x", "ratio_quota": "x", "protect_first": "x",
                "reputation": [], "pitfalls": [],
            },
            "合成专业": {
                "major_code": "030100",
                "majors": ["030100 合成专业"],
                "score_trend": "y", "ratio_quota": "y", "protect_first": "y",
                "reputation": [], "pitfalls": [],
            },
        },
    }
}


@pytest.fixture
def stub_online_research(monkeypatch):
    """在线研究打桩（不联网），返回带可辨识标记的合成画像。"""
    import tools.intelligence.agentic_research as ar

    def _stub(school_name, major_keyword="", api_config=None, budget_s=240.0):
        return {"name": school_name, "catalog_source": "[STUB 在线研究打桩]",
                "majors": ["(101)思想政治理论"]}

    monkeypatch.setattr(ar, "research_university_profile", _stub)


@pytest.fixture
def fake_school_db(monkeypatch):
    """patch 全部 school_scout 模块实例。

    双导入陷阱：``skills.school_scout`` 与 ``tools.skills.school_scout`` 是
    两份独立模块实例（各自持有 TARGET_SCHOOLS_DB），且前者只在 tools.* 的
    导入链把 tools/ 补进 sys.path 之后才可导入 —— 必须先加载两份再全量 patch。
    """
    for name in ("tools.skills.school_scout", "skills.school_scout"):
        try:
            importlib.import_module(name)
        except ImportError:
            pass
    patched = []
    for key, mod in list(sys.modules.items()):
        if key.endswith("school_scout") and hasattr(mod, "TARGET_SCHOOLS_DB"):
            monkeypatch.setattr(mod, "TARGET_SCHOOLS_DB", _FAKE_SCHOOL_DB)
            patched.append(key)
    assert patched, "未找到任何 school_scout 模块实例"
    return patched


class TestR6EmptyMajorKeyword:
    """R6：空专业关键词不得静默命中首个部门（旧判据 ``"" in k`` 恒真）。"""

    def test_empty_keyword_falls_through_to_online(self, fake_school_db,
                                                   stub_online_research):
        comp = SchoolComparator()
        prof = comp._get_school_profile("测试大学", None, "")
        assert prof.get("catalog_source") == "[STUB 在线研究打桩]", \
            "空关键词不应命中 DB 首个部门（旧实现命中并贴「人工整理考情专栏」标签）"

    def test_normal_keyword_still_hits_db(self, fake_school_db,
                                          stub_online_research):
        """阴性：正常关键词仍命中 DB（守卫不破坏匹配）。"""
        comp = SchoolComparator()
        prof = comp._get_school_profile("测试大学", None, "计算机")
        assert "CURATED_NOTES" in str(prof.get("catalog_source"))
        assert "081200" in " ".join(prof.get("majors") or [])


# ═══════════════════ R7：6 位专业代码不截断 ═══════════════════

class TestR7SubjectCodeTruncation:
    """R7：6 位「专业代码+名称」不得被截成 3 位假科目代码渲染。"""

    def test_six_digit_code_not_truncated(self):
        items = normalize_subject_items(
            ["085400 电子信息", "081200 计算机科学与技术", "105400 护理"])
        assert all(it["code"] == "" for it in items), \
            f"6 位专业代码不得被截为 3 位科目代码: {items}"
        assert [it["name"] for it in items] == \
            ["085400 电子信息", "081200 计算机科学与技术", "105400 护理"]

    def test_real_subject_code_still_parsed(self):
        """阴性：真科目代码仍正常解析。"""
        items = normalize_subject_items(["(101)思想政治理论", "301数学（一）"])
        assert items[0] == {"code": "101", "name": "思想政治理论"}
        assert items[1] == {"code": "301", "name": "数学（一）"}

    def test_format_never_renders_fake_code(self):
        items = normalize_subject_items(["085400 电子信息", "(101)思想政治理论"])
        rendered = format_subject_items(items)
        assert "(085)400" not in rendered
        assert "085400 电子信息" in rendered
        assert "(101)思想政治理论" in rendered

    def test_profile_majors_end_to_end(self):
        """端到端：真实库 majors 描述形态（6 位代码 + 说明文本）不再产出假代码。"""
        items = profile_subject_items(
            {"majors": ["085400 电子信息 / 计算机技术 (本部专硕，初试：政治、英一、数一、408)"]})
        assert items and items[0]["code"] == ""
        assert "085400" in items[0]["name"]


# ═══════════════════ R10：初试科目 field 名归一 + 冲突分组 ═══════════════════

class TestR10InitialSubjectsFieldUnification:
    """R10：HTML/PDF/离线基准三路同语义字段统一「初试科目」。

    [INTEL-H2 修正] 研招网在线目录的 value 是专业目录条目而非科目清单，
    已拆出「招生院系与专业」独立字段，不再与科目清单同组仲裁。
    """

    def test_four_producers_share_unified_field(self):
        """R10：HTML/PDF/离线基准三路真·科目清单统一「初试科目」。

        [INTEL-H2 修正] 研招网在线目录原也被断言为「初试科目」，但它的 value 是
        专业目录条目（院系/专业代码/专业名/方向），**不含科目** —— 与真科目清单
        同名分组后每次在线抓取成功都无条件报 CONFLICT 假警报。现改为
        「招生院系与专业」，回归钉子见下方两个 test_chsi_catalog_* 用例。
        """
        html_evs = DocumentExtractor().extract_from_html(
            _HTML_HALFWIDTH, "https://yz.example.edu.cn/n/1", "测试大学",
            target_year=2027)
        pdf_evs = DocumentExtractor().extract_from_pdf(
            _PDF_TEXT, "测试大学",
            source_url="https://yz.example.edu.cn/2027.pdf", target_year=2027)
        conn = CHSIConnector()
        off_evs = conn._generate_ground_truth_evidences(
            "测试大学", "085404", "https://yz.chsi.com.cn", 2027)
        assert [e.field for e in html_evs if e.field == "初试科目"] == ["初试科目"]
        assert [e.field for e in pdf_evs if e.field == "初试科目"] == ["初试科目"]
        assert [e.field for e in off_evs if e.field == "初试科目"] == ["初试科目"]
        # 专业细分移入 source（离线基准不再靠 field 名携带专业后缀）
        assert "085404 计算机技术" in off_evs[0].source.name

    def test_chsi_catalog_entry_not_claimed_as_subject(self):
        """[INTEL-H2 回归] 研招网专业目录条目不得自称「初试科目」。

        这是假冲突的最小复现：目录 dict 与官网科目 list 同名分组时，
        判等键（dict 走 str() vs list 走 json.dumps）必然不同 → 无条件 CONFLICT。
        """
        on_evs = CHSIConnector()._parse_catalog_html(
            "<table><tr><td>测试大学</td><td>计算机学院</td><td>085404 计算机技术</td>"
            "<td>不区分研究方向</td><td>全日制</td><td>60</td></tr></table>",
            "测试大学", "https://yz.chsi.com.cn", 2027)
        assert on_evs, "在线目录解析应产出一条证据"
        assert all(e.field == "招生院系与专业" for e in on_evs)
        # value 仍是专业条目，键名不得被改动（下游按 major_code 取值）
        assert on_evs[0].value["major_code"] == "085404"

    def test_chsi_catalog_entry_no_false_conflict_with_official_subjects(self):
        """[INTEL-H2 回归] 研招网目录 + 官网科目清单同批 → 不再报假 CONFLICT。"""
        official = build_evidence(
            "初试科目", ["(101)思想政治理论", "(302)数学(二)"], "", 2027,
            "graduate_school", "官网目录", "https://a.cn", target_year=2027)
        catalog = CHSIConnector()._parse_catalog_html(
            "<table><tr><td>测试大学</td><td>计算机学院</td><td>085404 计算机技术</td>"
            "<td>不区分研究方向</td><td>全日制</td><td>60</td></tr></table>",
            "测试大学", "https://yz.chsi.com.cn", 2027)[0]

        out = resolve_conflicts([official, catalog])
        assert not any(e.status == "CONFLICT" for e in out), (
            "语义不同的证据（科目清单 vs 专业目录条目）不得互判冲突")
        assert {e.status for e in out} == {"VERIFIED"}

    def test_conflicting_values_share_group(self):
        """异值多源 → 进入同一冲突分组（修复前四种 field 名永不进同组）。"""
        evs = [
            build_evidence("初试科目", ["(302)数学(二)"], "", 2027,
                           "graduate_school", "来源A", "https://a.cn",
                           target_year=2027),
            build_evidence("初试科目", ["(301)数学(一)"], "", 2027,
                           "chsi", "来源B", "https://b.cn", target_year=2027),
        ]
        out = resolve_conflicts(evs)
        assert len(out) == 2
        assert all(e.status == "CONFLICT" for e in out)

    def test_same_value_merged_with_multi_source(self):
        """同值多源 → 合并为一条（多源佐证）。"""
        evs = [
            build_evidence("初试科目", ["(101)思想政治理论"], "", 2027,
                           "graduate_school", "来源A", "https://a.cn",
                           target_year=2027),
            build_evidence("初试科目", ["(101)思想政治理论"], "", 2027,
                           "chsi", "来源B", "https://b.cn", target_year=2027),
        ]
        out = resolve_conflicts(evs)
        assert len(out) == 1
        assert out[0].status == "VERIFIED"

    def test_legacy_distinct_field_names_never_group(self):
        """对照：旧四种 field 名各异 → 同语义证据永不进同一分组（失效机理）。"""
        old_fields = ["初试科目配置", "PDF大纲/目录初试科目",
                      "专业目录与统考初试科目", "初试科目组合 (085404 计算机技术)"]
        evs = [build_evidence(f, [f"值{i}"], "", 2027, "chsi", f"来源{i}",
                              "https://e.cn", target_year=2027)
               for i, f in enumerate(old_fields)]
        out = resolve_conflicts(evs)
        assert len(out) == 4
        assert all(e.status != "CONFLICT" for e in out)
