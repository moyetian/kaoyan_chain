# -*- coding: utf-8 -*-
"""输出预算分档（tools/output_budget）测试。

[为什么这些用例要紧] 分档是「用稳定性换厚度」的取舍，其**不变量**必须钉死：
  * ``max_tokens_for`` **永不返回 None/0/负**（None 会被 ``_build_payload`` 整个省略，
    输出长度交给上游默认——正是 R3 观测到的 8998→4927 字符收缩机制）；
  * 档位表的 ``max_tokens`` 必须**严格递增**（否则「更深的档位反而更短」）；
  * 骨架里**不得出现总字数要求**（实测软性字数约束会被无视：要求 1200 字 → 输出
    3979 字，超标 3.3 倍），只写「每节 N 条、每条 N 句」；
  * 截断标注只在 ``finish_reason == "length"`` 时出现（不用长度启发式，避免误报）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import output_budget as ob  # noqa: E402


# ── 档位表自身的性质 ──────────────────────────────────────────────────

def test_three_levels_present():
    assert set(ob.available_levels()) == {"brief", "standard", "deep"}


def test_max_tokens_strictly_increasing():
    """档位越深，上界必须**严格递增**——否则「切深档反而更短」是产品事故。"""
    caps = [ob.BUDGET_LEVELS[lv]["max_tokens"] for lv in ob.available_levels()]
    assert caps == sorted(caps) and len(set(caps)) == len(caps), caps
    assert caps[0] < caps[-1]


def test_target_chars_strictly_increasing():
    chars = [ob.BUDGET_LEVELS[lv]["target_chars"] for lv in ob.available_levels()]
    assert chars == sorted(chars) and len(set(chars)) == len(chars), chars


def test_skeletons_have_no_total_word_count():
    """骨架**不得写总字数**——软性字数约束实测无效（超标 3.3 倍）。

    判据：骨架里不出现「字」作为总量单位（「每条 2-3 句」里的「句」是允许的）。
    """
    for lv in ob.available_levels():
        for line in ob.BUDGET_LEVELS[lv]["skeleton"]:
            assert "字" not in line, f"{lv} 骨架含总字数要求：{line}"


def test_default_level_is_standard():
    assert ob.DEFAULT_LEVEL == "standard"
    assert ob.get_level({}) == "standard"
    assert ob.get_level(None) == "standard"


# ── 读档位 ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [None, "", "bogus", 123, [], {}])
def test_get_level_falls_back_on_garbage(bad):
    assert ob.get_level({"output_budget": bad}) == ob.DEFAULT_LEVEL


def test_get_level_accepts_nested_form():
    """同时接受 {level: "deep"} 形态（未来配置分层时无需改读法）。"""
    assert ob.get_level({"output_budget": {"level": "deep"}}) == "deep"
    assert ob.get_level({"output_budget": {"level": "bogus"}}) == ob.DEFAULT_LEVEL


def test_get_level_ignores_other_keys():
    assert ob.get_level({"other": "deep"}) == ob.DEFAULT_LEVEL


# ── 写档位 ────────────────────────────────────────────────────────────

def test_set_level_writes_valid():
    cfg = {}
    assert ob.set_level(cfg, "deep") is True
    assert cfg[ob.CONFIG_KEY] == "deep"


@pytest.mark.parametrize("bad", ["bogus", "", None, 7])
def test_set_level_rejects_garbage_without_writing(bad):
    """非法档位**不写脏值**——否则一次手滑就会让后续所有读取拿到非法档。"""
    cfg = {}
    assert ob.set_level(cfg, bad) is False
    assert ob.CONFIG_KEY not in cfg


def test_set_level_does_not_copy_table_into_config():
    """只存档位名：档位表是代码侧单一真源，复制会出现第二事实源。"""
    cfg = {}
    ob.set_level(cfg, "brief")
    assert cfg[ob.CONFIG_KEY] == "brief"
    assert not isinstance(cfg[ob.CONFIG_KEY], dict)


# ── max_tokens 取值：核心不变量 ───────────────────────────────────────

@pytest.mark.parametrize("cfg", [None, {}, {"max_tokens": None}, {"max_tokens": 0},
                                 {"max_tokens": -1}, {"max_tokens": "abc"},
                                 {"output_budget": "bogus"}])
def test_max_tokens_never_none_or_nonpositive(cfg):
    """**永不返回 None/0/负**——这是 R4 修掉的根因，不允许回归。"""
    v = ob.max_tokens_for(cfg)
    assert isinstance(v, int) and v > 0


def test_max_tokens_follows_level():
    for lv in ob.available_levels():
        assert ob.max_tokens_for({"output_budget": lv}) \
            == ob.BUDGET_LEVELS[lv]["max_tokens"]


def test_explicit_max_tokens_wins_over_level():
    """显式配置优先（向后兼容：已写死该值的既有配置行为不变）。"""
    assert ob.max_tokens_for({"output_budget": "brief", "max_tokens": 9999}) == 9999
    assert ob.max_tokens_for({"output_budget": "deep", "max_tokens": 300}) == 300


def test_explicit_string_number_accepted():
    assert ob.max_tokens_for({"max_tokens": "2048"}) == 2048


# ── 骨架 ──────────────────────────────────────────────────────────────

def test_skeleton_prompt_mentions_level_and_structure():
    p = ob.skeleton_prompt({"output_budget": "standard"}, task="矛盾普遍性")
    assert "矛盾普遍性" in p
    assert "标准" in p            # 考生知道自己在哪一档
    for line in ob.BUDGET_LEVELS["standard"]["skeleton"]:
        assert line in p


def test_skeleton_prompt_differs_by_level():
    a = ob.skeleton_prompt({"output_budget": "brief"})
    b = ob.skeleton_prompt({"output_budget": "deep"})
    assert a != b
    assert "速览" in a and "深入" in b


def test_skeleton_for_returns_copy():
    """返回副本——调用方改它不能污染档位表（单一真源保护）。"""
    s = ob.skeleton_for({})
    s.append("注入")
    assert "注入" not in ob.skeleton_for({})


# ── 截断标注 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("fr", [None, "", "stop", "length?", "tool_calls"])
def test_no_notice_when_not_truncated(fr):
    """非 length 结尾**不标注**——用长度启发式会在正常长回答上误报。"""
    assert ob.truncation_notice(fr, {"output_budget": "standard"}) == ""


def test_notice_on_length():
    n = ob.truncation_notice("length", {"output_budget": "standard"})
    assert "标准" in n and "2048" in n
    assert "切换" in n, "标注必须告诉考生下一步怎么做"


def test_notice_respects_level():
    assert "深入" in ob.truncation_notice("length", {"output_budget": "deep"})
    assert "4096" in ob.truncation_notice("length", {"output_budget": "deep"})


def test_notice_case_insensitive():
    assert ob.truncation_notice("LENGTH", {}) != ""


# ── 展示 ──────────────────────────────────────────────────────────────

def test_describe_marks_current_level():
    text = ob.describe({"output_budget": "deep"})
    assert "当前档位：深入" in text
    assert "← 当前" in text
    assert text.count("← 当前") == 1, "有且只有一档被标为当前"


# ── 与两条 LLM 路径的一致性（防某一侧漏改）─────────────────────────

def test_engine_and_loop_agree_with_budget_table():
    from tools.cli.agent import engine
    from tools.agent import loop as L
    for lv in ob.available_levels():
        cfg = {"output_budget": lv}
        assert engine._resolve_max_tokens(cfg) == L._resolve_max_tokens(cfg) \
            == ob.BUDGET_LEVELS[lv]["max_tokens"]


# ── 显式 max_tokens 覆盖关系的可见性 ──────────────────────────────────

def test_describe_warns_when_explicit_overrides_level():
    """真实工作区存在显式 max_tokens（实测 16384），它压过档位——
    describe **必须**写明「谁在生效、怎么改」，否则考生切了档位却看不到变化，
    会以为功能坏了。"""
    text = ob.describe({"output_budget": "standard", "max_tokens": 16384})
    assert "16384" in text and "优先于档位" in text
    assert "移除该字段" in text, "必须给出可执行的下一步"


def test_describe_quiet_when_no_override():
    text = ob.describe({"output_budget": "standard"})
    assert "优先于档位" not in text
    assert "2048" in text


def test_describe_marks_overridden_current_level():
    text = ob.describe({"output_budget": "brief", "max_tokens": 16384})
    assert "被显式 16384 覆盖" in text
