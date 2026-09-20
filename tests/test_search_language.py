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
