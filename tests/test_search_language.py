"""真实输入差异与双向术语召回回归，不调用外网。"""
import pytest
from tools.search.relevance import significant_tokens, is_relevant
from tools.search.rank import Ranker
from tools.search.segment import normalize_text, segment


def test_whitespace_and_alias_do_not_change_ranking():
    queries = ["华中科技大学 计算机 复试线", "华中科技大学计算机复试线",
               "华科计算机考研复试线多少分"]
    scores = [Ranker._lexical("华中科技大学复试分数线", "", significant_tokens(q))
              for q in queries]
    assert max(scores) - min(scores) < 0.1
    assert scores[0] > 0.5


@pytest.mark.parametrize("a,b", [("复试线", "复试分数线"), ("考纲", "考试大纲"),
                                ("专业课真题", "专业课考试真题"),
                                ("招生目录", "招生专业目录")])
def test_synonyms_match_in_both_directions(a, b):
    assert is_relevant(b, "", "", significant_tokens(a))
    assert is_relevant(a, "", "", significant_tokens(b))
    assert normalize_text(normalize_text(b)) == normalize_text(b)


def test_year_alone_does_not_allow_unrelated_content():
    assert not is_relevant("2027 air conditioning", "", "",
                           significant_tokens("华科计算机2027复试线"))
    assert is_relevant("085409 生物医学工程", "", "",
                       significant_tokens("南方医科大学085409招生"))


def test_unknown_prefix_does_not_consume_known_term():
    assert "复试分数线" in segment("问复试线")
    assert "考试大纲" in segment("甲乙丙考纲")


def test_haystack_token_substring_is_not_a_relevance_match():
    """[P2-8 回归] 「查询词是结果词的子串」不得再被判为相关。

    修复前 ``relevance.is_relevant`` 里有一行对**集合 repr 字符串**做子串匹配的
    兜底：``if tok in str(haystack_set): return True``。集合 repr 是
    ``"{'abc'}"`` 这样的文本，于是 ``'ab' in "{'abc'}"`` 为 True ——
    相关性守门被彻底绕过（反爬返回的无关页只要含一个"包含该子串"的词就放行）。

    阴性对照：把 ``tools/search/relevance.py`` 里那行
    ``if tok in str(haystack_set): return True`` 加回 ``for tok in tokens`` 循环，
    本用例第一、二条断言必须变红。
    """
    # haystack 的 token 集合实为 {"abc"}，但 "ab" 本身不是其中成员
    assert not is_relevant("abc", "", "", ["ab"])
    # 更长的 haystack 词同样不得被子串放行
    assert not is_relevant("abcdef", "", "", ["abc"])
    # 精确成员命中必须仍然放行（防止"修成一律不匹配"这种过度拦截）
    assert is_relevant("abc", "", "", ["abc"])


# ── segment 不可用时的降级分支（中文必须仍能命中） ────────────────

def _force_segment_import_error(monkeypatch):
    """令 ``from .segment import ...`` 抛 ImportError，从而走到降级分支。

    ``sys.modules`` 里某模块被置为 ``None`` 时，import 机制会直接抛
    ``ModuleNotFoundError``（ImportError 子类），这是唯一能稳定触达该分支的方式。
    """
    import sys
    monkeypatch.setitem(sys.modules, "tools.search.segment", None)


def test_degraded_fallback_still_matches_chinese(monkeypatch):
    """[回归] segment 导入失败时，降级分支必须仍能命中中文结果。

    修复前降级分支只做 ``set(haystack.split())`` 的**精确集合**匹配：中文没有
    空格，整段中文是一个 token，查询侧与结果侧的切分互不相等 → 任何中文查询
    恒不命中 → 所有 provider 都被判「反爬失败」→ 检索全空。

    阴性对照：把降级分支的中文子串判定那行删掉，本用例第一条断言变红。
    """
    _force_segment_import_error(monkeypatch)

    tokens = significant_tokens("华中科技大学 计算机 复试线")
    assert tokens, "降级分支仍应抽出关键词"

    # 真实相关结果：中文整词出现在标题里必须放行
    assert is_relevant("华中科技大学2026年计算机复试线公布", "", "", tokens)
    # 无关结果必须仍被拦下（防止"修成一律放行"这种过度拦截）
    assert not is_relevant("日文空调省电标准", "", "", tokens)


def test_degraded_fallback_does_not_reintroduce_substring_false_positive(monkeypatch):
    """[P2-8 回归加固] 降级分支的中文子串判定不得复活 ASCII 子串误命中。

    子串判定只对含 CJK 的 token 生效；若对 ASCII 也做子串，``'ab' in 'abc'``
    会重新放行 —— 那正是 P2-8 删掉的「集合 repr 子串匹配」的等价物。
    """
    _force_segment_import_error(monkeypatch)

    assert not is_relevant("abc", "", "", ["ab"])
    assert not is_relevant("abcdef", "", "", ["abc"])
    assert is_relevant("abc", "", "", ["abc"])
