# -*- coding: utf-8 -*-
"""[P1 修复·2026-10-08 R10 值归一] 全角/半角同义证据值不再产生噪音 CONFLICT。

背景（R10 后半）：field 名归一让同语义证据进同一分组后，值判等仍按原始
文本比较 —— extractor 保留原文形态（名称内全角括号「(204)英语（二）」），
chsi 离线标准模板为「(204)英语(二)」，语义相同但文本不等 →
``resolve_conflicts`` 判 CONFLICT 噪音。

修复：新增 ``_normalize_evidence_value``，判等键做保守归一（全角 ASCII
U+FF01–FF5E + 全角空格 U+3000 → 半角 + strip 首尾），**仅用于判等**；
输出/合并保留原值。不用 NFKC（会做兼容分解 Ⅱ→II、①→1 等，误伤面
不可控）；不折叠连续空白（「计算机 408」vs「计算机408」有假同风险）。

全程离线：build_evidence / resolve_conflicts 均为纯内存计算，不联网、
不碰真实工作区。
"""

from tools.intelligence.evidence_engine import (
    _normalize_evidence_value,
    build_evidence,
    resolve_conflicts,
)

_FULL = "(204)英语（二）"          # extractor 原文形态：名称内全角括号
_HALF = "(204)英语(二)"            # chsi 离线标准模板：全半角


def _ev(field, value, source_type="graduate_school", source_name="来源A",
        url="https://a.example.edu.cn"):
    return build_evidence(field, value, "", 2027, source_type, source_name,
                          url, target_year=2027)


def _ev_chsi(field, value):
    return _ev(field, value, "chsi", "研招网", "https://yz.chsi.com.cn")


# ═══════════════════ 归一函数：边界与保守性 ═══════════════════

class TestNormalizeValueBoundaries:
    """归一函数本身的边界（宁少合并、不错合并）。"""

    def test_fullwidth_brackets_to_halfwidth(self):
        assert _normalize_evidence_value(_FULL) == _HALF

    def test_fullwidth_alnum_and_space(self):
        assert _normalize_evidence_value("　（３０１）数学（一） ") == "(301)数学(一)"

    def test_inner_space_not_collapsed(self):
        """连续空白折叠有假同风险 → 默认不做：内部空格差异保留。"""
        assert _normalize_evidence_value("计算机 408") != \
            _normalize_evidence_value("计算机408")

    def test_roman_numeral_circle_superscript_untouched(self):
        """手工映射边界：罗马数字/圆圈数字/上标不在全角 ASCII 区，不转换
        （NFKC 会把它们兼容分解为 II/1/2 —— 误伤面证据）。"""
        assert _normalize_evidence_value("Ⅱ") == "Ⅱ"
        assert _normalize_evidence_value("①") == "①"
        assert _normalize_evidence_value("²") == "²"

    def test_non_string_returned_as_is(self):
        assert _normalize_evidence_value(60) == 60
        assert _normalize_evidence_value(None) is None
        d = {"a": 1}
        assert _normalize_evidence_value(d) is d

    def test_list_recurses_dict_elements_untouched(self):
        out = _normalize_evidence_value(["（101）思想政治理论", {"k": "（v）"}])
        assert out == ["(101)思想政治理论", {"k": "（v）"}]


# ═══════════════════ 行为：判同 / 判异 / 保留原形态 ═══════════════════

class TestResolveConflictsFullwidthHalfwidth:
    """行为钉住：全角/半角判同、真差异仍冲突、合并保留原值。"""

    def test_scalar_fullwidth_vs_halfwidth_merges(self):
        """核心修复：全角 vs 半角同义值 → 不再判 CONFLICT，合并为 1 条。"""
        out = resolve_conflicts([_ev("初试科目", _FULL), _ev_chsi("初试科目", _HALF)])
        assert len(out) == 1, "同义值应合并（修复前按原文比较判 CONFLICT）"
        assert out[0].status != "CONFLICT"

    def test_list_value_real_shape_merges(self):
        """真实形态：初试科目 value 是科目清单（list of str），差异在元素内。"""
        out = resolve_conflicts([
            _ev("初试科目", ["(101)思想政治理论", _FULL, "(302)数学（二）"]),
            _ev_chsi("初试科目", ["(101)思想政治理论", _HALF, "(302)数学(二)"]),
        ])
        assert len(out) == 1
        assert out[0].status != "CONFLICT"

    def test_true_difference_still_conflicts(self):
        """阴性：真差异（英语二 vs 英语一）必须仍判 CONFLICT。"""
        out = resolve_conflicts([_ev("初试科目", _FULL),
                                 _ev_chsi("初试科目", "(204)英语(一)")])
        assert len(out) == 2
        assert all(e.status == "CONFLICT" for e in out)

    def test_true_difference_in_list_still_conflicts(self):
        out = resolve_conflicts([_ev("初试科目", [_FULL]),
                                 _ev_chsi("初试科目", ["(204)英语(一)"])])
        assert len(out) == 2
        assert all(e.status == "CONFLICT" for e in out)

    def test_merge_keeps_original_value_not_normalized_form(self):
        """合并保留原值形态：best_ev 原值是什么就输出什么，不得写回归一形态。

        用「全角代码括号」变体制造差异 —— 归一形态 ``(204)英语(二)`` 与
        两条原值均不同：若实现错误地把归一值写回 value，本条必红。
        """
        full_variant = "（204）英语（二）"   # 全角代码括号 + 全角名称括号
        out = resolve_conflicts([
            _ev("初试科目", full_variant),                       # A 级 0.95
            _ev_chsi("初试科目", "(204)英语（二）"),              # S 级 1.0
        ])
        assert len(out) == 1
        assert out[0].value == "(204)英语（二）", "应保留最高置信度者的原值"
        assert out[0].value != "(204)英语(二)", "不得写回归一形态"


# ═══════════════════ 非字符串值：行为不回归 ═══════════════════

class TestNonStringValuesUnchanged:
    """int / dict / list-of-dict 的判等语义与 P0-4 修复不受值归一影响。"""

    def test_int_conflict_unchanged(self):
        out = resolve_conflicts([_ev("招生人数", 60, "chsi", "研招网",
                                     "https://yz.chsi.com.cn"),
                                 _ev("招生人数", 58, "college_official", "学院",
                                     "https://cs.example.edu.cn")])
        assert len(out) == 2 and all(e.status == "CONFLICT" for e in out)

    def test_int_same_value_merges(self):
        out = resolve_conflicts([_ev("招生人数", 60), _ev("招生人数", 60)])
        assert len(out) == 1 and out[0].status != "CONFLICT"

    def test_dict_same_merges_different_conflicts(self):
        d = {"title": "甲版", "date": "2026-09-01"}
        out = resolve_conflicts([_ev("简章元信息", dict(d)),
                                 _ev("简章元信息", dict(d))])
        assert len(out) == 1
        out2 = resolve_conflicts([_ev("简章元信息", {"title": "甲版"}),
                                  _ev("简章元信息", {"title": "乙版"})])
        assert len(out2) == 2 and all(e.status == "CONFLICT" for e in out2)

    def test_list_of_dict_pdf_links_no_crash(self):
        """P0-4 形态回归：list-of-dict（PDF 附件）同值合并、不崩溃。"""
        links = [{"name": "2027 招生目录", "url": "https://a.example.edu.cn/a.pdf"}]
        out = resolve_conflicts([_ev("官方PDF招生目录附件", links, "college_official"),
                                 _ev("官方PDF招生目录附件", [dict(links[0])],
                                     "college_official")])
        assert len(out) == 1, "同值 PDF 附件应合并（json 序列化路径不得被归一破坏）"
