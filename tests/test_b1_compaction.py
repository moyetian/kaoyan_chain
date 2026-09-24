# -*- coding: utf-8 -*-
"""B1 批次回归 · 上下文压缩升级：结构化摘要 + compact_mode 开关。

缺陷现场（B1，可从旧代码直接读出）
----------------------------------
旧 ``ContextEngine.compact_context`` 把被压缩的历史逐条压成
``学员此前曾提问: {content[:100]}`` 之类的行，且**只保留最后 10 行**
（``compressed_summary_lines[-10:]``）。后果：会话早期出现的考纲约束、错因、
复习计划一旦被挤出「最近 6 条保留窗口 + 最后 10 行摘要窗口」，就被**静默丢弃**
—— 学员与私教都不知情，后续派题可能直接踩考纲红线。

本文件锁定（不得回退）：
1. 早期注入的 10 条互异考纲约束，压缩后**逐条**仍出现在摘要文本里；
2. 阴性对照：把 key_info 保护摘除后，同样的 10 条约束必须出现丢失
   （证明第 1 条不是"没压缩所以没丢"的假通过）；
3. ``validate_compacted``：合法结构返回空列表；孤儿 tool / 缺结果 / 非法 role
   必须报出问题；
4. ``agent.compact_mode`` 解析：缺失 / 非法 / 类型错误一律 ``rule_only``（fail-safe）；
5. ``llm`` 模式：注入的 llm_fn 返回合法 JSON 时摘要生效；抛异常 / 返回 None /
   非 JSON / 缺键时降级回规则摘要且不抛异常、不产生任何真实网络请求；
6. ``focus`` 命中消息在规则摘要里优先保留；
7. 既有契约：摘要块含字面量 ``Context Compaction``；BeforeCompact 钩子照旧触发。

本文件全程离线：所有 LLM 路径要么注入 ``llm_fn``，要么用未配置 key 的 config
走真实默认入口（``chat_completion`` 未配置时自然返回 None）。
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools.agent import compaction as compaction_mod  # noqa: E402
from tools.agent.context_engine import ContextEngine  # noqa: E402
from tools.agent.tokenizer import COMPACT_THRESHOLD, RESERVED_OUTPUT_TOKENS  # noqa: E402

#: 用小窗口让水位可控（与 tests/test_tokenizer_budget.py 同口径）。
WINDOW = 16_000
WATERMARK = int((WINDOW - RESERVED_OUTPUT_TOKENS) * COMPACT_THRESHOLD)

#: 10 条互异的早期考纲约束（(标记, 关键词, 原文)）。措辞刻意各不相同，
#: 且全部以「约束-X：」开头 —— 这是学员/私教记录考纲边界的常见写法。
_CONSTRAINTS = (
    ("约束-甲", "洛必达法则", "极限部分只考洛必达法则与等价无穷小，不考泰勒展开的复杂余项"),
    ("约束-乙", "初等变换求秩", "矩阵题不考秩的证明，只考初等变换求秩与方程组解的判定"),
    ("约束-丙", "书信与通知", "英语小作文只考书信与通知，不考图表描述"),
    ("约束-丁", "大纲原文", "政治多选题不考超纲时政细节，考点要求全部来自大纲原文"),
    ("约束-戊", "核心概念", "专业课名词解释只考核心概念，不考人物生平"),
    ("约束-己", "直接求导", "导数证明题不考中值定理构造性证明，只考直接求导"),
    ("约束-庚", "傅里叶级数", "级数部分不考傅里叶级数，考纲明确剔除"),
    ("约束-辛", "条件概率", "概率论不考贝叶斯公式的复杂推导，只考条件概率基础题"),
    ("约束-壬", "功能句", "作文模板只背三套功能句，不背整篇范文"),
    ("约束-癸", "近五年", "真题训练只看近五年，不做十年前的旧题"),
)

#: 保留尾部（最近 6 条）：刻意不含任何约束标记，保证"约束只能靠摘要活下来"。
_TAIL = [
    {"role": "user", "content": "最后确认一下：下一轮从第一题讲起。"},
    {"role": "assistant", "content": "好的，先讲思路再动手。"},
    {"role": "user", "content": "第二题呢？"},
    {"role": "assistant", "content": "第二题同理，注意定义域。"},
    {"role": "user", "content": "明白了。"},
    {"role": "assistant", "content": "那我们开始下一轮练习。"},
]

#: LLM 模式返回的合法结构化 JSON（含全部五个键与三个 key_info 子类）。
_LLM_JSON = json.dumps({
    "goal": ["LLM-目标：拿下翻译得分点"],
    "progress": ["LLM-进展：已讲完定语从句拆分"],
    "key_info": {
        "syllabus": ["LLM-约束：小作文只考书信与通知"],
        "mistakes": ["LLM-错因：时态混用"],
        "review": ["LLM-待复习：背诵三套功能句"],
    },
    "file_ops": ["read_file → 02-英语/真题.md"],
    "pending": ["LLM-诉求：还想练翻译"],
}, ensure_ascii=False)


def _make_engine(tmp_path, config=None):
    """构造走纯 Python 启发式计数的引擎（水位可控、结果确定）。"""
    ce = ContextEngine(workspace_root=tmp_path, active_subject="math",
                       max_context_tokens=WINDOW, config=config)
    ce._force_python = True
    ce.tokenizer._enc = None
    return ce


def _build_long_history(ce):
    """构造远超水位的历史：10 条约束在**早期**，被大量填充挤出最近窗口。"""
    msgs = [
        {"role": "system", "content": "顶层协议：严格按考纲与真题辅导。"},
        {"role": "user", "content": "报到，今天开始强化阶段。"},
        {"role": "assistant", "content": "好的，先汇报今天的复习路线图。"},
        {"role": "user", "content": "先把昨天的错题过一遍。"},
        {"role": "assistant", "content": "收到，我们从第一题开始。"},
        {"role": "user", "content": "行。"},
    ]
    for marker, _keyword, text in _CONSTRAINTS:
        msgs.append({"role": "user", "content": f"{marker}：{text}"})
        msgs.append({"role": "assistant", "content": "收到。"})
    # 工具消息：顺带覆盖 file_ops 抽取与 tool 配对保护
    msgs.append({"role": "assistant", "content": "我来调阅真题文件。",
                 "tool_calls": [{"id": "call_1", "type": "function",
                                 "function": {"name": "read_file", "arguments": "{}"}}]})
    msgs.append({"role": "tool", "tool_call_id": "call_1", "name": "read_file",
                 "content": "已读取 01-数学/真题汇编.md 共 1200 字。"})
    filler_index = 0
    while ce.estimate_tokens(msgs + _TAIL) <= WATERMARK + 800:
        msgs.append({"role": "user", "content": f"练习第{filler_index}题：" + "题干内容" * 60})
        msgs.append({"role": "assistant", "content": f"第{filler_index}题解析：" + "步骤说明" * 60})
        filler_index += 1
    assert filler_index > 0, "填充不足，测试数据构造失败"
    return msgs + _TAIL


def _summary_text(messages):
    """取出压缩块（role=system 且含 Context Compaction 字面量）。"""
    blocks = [m.get("content") or "" for m in messages
              if isinstance(m, dict) and m.get("role") == "system"
              and "Context Compaction" in (m.get("content") or "")]
    assert len(blocks) == 1, f"压缩后应恰好有一条摘要消息，实际 {len(blocks)} 条"
    return blocks[0]


# ── 核心验收：早期约束必须活下来 ────────────────────────────────────────


def test_early_constraints_survive_compaction(tmp_path):
    """核心验收：早期 10 条考纲约束压缩后逐条保留（旧实现会静默丢弃）。"""
    ce = _make_engine(tmp_path)
    messages = _build_long_history(ce)
    assert ce.estimate_tokens(messages) > WATERMARK, "测试数据未超过压缩水位"

    compacted = ce.compact_context(messages)

    # 前提：压缩真的发生了（防止"没压缩所以没丢"的假通过）
    assert len(compacted) < len(messages), "压缩未真正发生"

    summary_text = _summary_text(compacted)
    for marker, keyword, _text in _CONSTRAINTS:
        assert marker in summary_text, f"早期约束 {marker} 在压缩摘要中丢失"
        assert keyword in summary_text, f"约束 {marker} 的关键词「{keyword}」在压缩摘要中丢失"

    # 约束只可能通过摘要保留 —— 保留尾部里不应再出现它们，否则本测试失去意义
    tail_text = "\n".join((m.get("content") or "") for m in compacted[2:])
    for marker, _keyword, _text in _CONSTRAINTS:
        assert marker not in tail_text, f"{marker} 落在保留尾部，无法证明摘要保护生效"

    # 压缩产物结构合法（无孤儿 tool 消息）
    assert compaction_mod.validate_compacted(compacted) == []


def test_negative_control_without_key_info_protection_loses_constraints(tmp_path, monkeypatch):
    """阴性对照：摘除 key_info 保护后，同样的 10 条约束必须出现丢失。

    没有这条对照，上一条测试可能被"根本没压缩"或"约束恰好被别的分区保留"
    蒙混过关。
    """
    ce = _make_engine(tmp_path)
    messages = _build_long_history(ce)

    monkeypatch.setattr(
        compaction_mod, "extract_key_info",
        lambda messages: {"syllabus": [], "mistakes": [], "review": []},
    )
    compacted = ce.compact_context(messages)
    assert len(compacted) < len(messages), "对照组也必须真的发生压缩"

    text = "\n".join((m.get("content") or "") for m in compacted)
    lost = [marker for marker, _kw, _t in _CONSTRAINTS if marker not in text]
    assert lost, "阴性对照失效：无保护时 10 条约束一条未丢"


# ── key_info 抽取与渲染 ─────────────────────────────────────────────────


def test_key_info_items_not_truncated_at_100_chars():
    """key_info 单条上限 400 字符：旧实现 100 字符截断的根因不得回退。"""
    long_constraint = "约束-长：" + "核心约束内容" * 25 + "，尾部标记-Ω"
    assert 100 < len(long_constraint) <= 400

    summary = compaction_mod.build_structured_summary([{"role": "user", "content": long_constraint}])
    text = compaction_mod.render_summary(summary)
    assert "核心约束内容" in text
    assert "尾部标记-Ω" in text, "长约束被截断（尾部标记丢失）"


def test_extract_key_info_covers_three_categories():
    """三类保护信息各自命中：考纲约束 / 错因 / 待复习。"""
    messages = [
        {"role": "user", "content": "约束-甲：矩阵题不考秩的证明"},
        {"role": "assistant", "content": "本题错因：审题偏差，把秩当成行列式"},
        {"role": "user", "content": "待复习：明天先复盘行列式性质"},
        {"role": "user", "content": "今天天气不错"},
    ]
    info = compaction_mod.extract_key_info(messages)
    assert info["syllabus"] == ["约束-甲：矩阵题不考秩的证明"]
    assert info["mistakes"] == ["本题错因：审题偏差，把秩当成行列式"]
    assert info["review"] == ["待复习：明天先复盘行列式性质"]


def test_render_summary_caps_sections_and_notes_omitted():
    """渲染有界：每分区最多 12 条，超出部分写明省略条数。"""
    items = [f"约束-{i}：只考第{i}章" for i in range(15)]
    summary = {"goal": [], "progress": [],
               "key_info": {"syllabus": items, "mistakes": [], "review": []},
               "file_ops": [], "pending": []}
    text = compaction_mod.render_summary(summary)
    assert text.count("[考纲约束]") == compaction_mod.MAX_SECTION_ITEMS
    assert "另有 3 条已省略" in text


def test_render_summary_omits_empty_sections():
    """空分区不渲染空标题，但摘要头（Context Compaction 字面量）必须保留。"""
    text = compaction_mod.render_summary(
        {"goal": [], "progress": [], "key_info": {}, "file_ops": [], "pending": []}
    )
    assert "Context Compaction" in text
    assert "[学员目标]" not in text
    assert "[学习进展]" not in text
    assert "[待处理诉求]" not in text


def test_file_ops_extracted_from_tool_messages():
    """file_ops：从 tool 消息抽「工具名 + 目标文件路径」，抽不到路径只记工具名。"""
    messages = [
        {"role": "tool", "name": "read_file", "content": "已读取 01-数学/真题汇编.md 共 1200 字"},
        {"role": "tool", "name": "verify_math", "content": "计算结果：1.414"},
    ]
    summary = compaction_mod.build_structured_summary(messages)
    assert any("read_file" in op and "01-数学/真题汇编.md" in op for op in summary["file_ops"])
    assert "verify_math" in summary["file_ops"]


def test_pending_captures_trailing_unanswered_request():
    """pending：只取末尾未被回应的连续 user 消息。"""
    messages = [
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "这是第一问的解答"},
        {"role": "user", "content": "再帮我讲讲第三问的切入点"},
    ]
    summary = compaction_mod.build_structured_summary(messages)
    assert summary["pending"] and "第三问" in summary["pending"][0]


# ── 结构校验 ────────────────────────────────────────────────────────────


def test_validate_compacted_accepts_legal_structure():
    legal = [
        {"role": "system", "content": "协议"},
        {"role": "system", "content": "摘要"},
        {"role": "user", "content": "做题"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "name": "read_file", "content": "内容"},
        {"role": "assistant", "content": "讲完了"},
    ]
    assert compaction_mod.validate_compacted(legal) == []


def test_validate_compacted_rejects_orphan_and_missing_pairs():
    orphan = [{"role": "system", "content": "s"},
              {"role": "tool", "tool_call_id": "call_x", "name": "read_file", "content": "c"}]
    assert compaction_mod.validate_compacted(orphan) != [], "孤儿 tool 消息未被识别"

    missing_result = [{"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]}]
    assert compaction_mod.validate_compacted(missing_result) != [], "缺少 tool 结果的调用未被识别"

    bad_role = [{"role": "wizard", "content": "x"}]
    assert compaction_mod.validate_compacted(bad_role) != [], "非法 role 未被识别"

    id_mismatch = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_999", "name": "read_file", "content": "c"},
    ]
    assert compaction_mod.validate_compacted(id_mismatch) != [], "tool_call_id 不匹配未被识别"


# ── compact_mode 解析 ───────────────────────────────────────────────────


@pytest.mark.parametrize("config,expected", [
    (None, "rule_only"),
    ({}, "rule_only"),
    ({"agent": None}, "rule_only"),
    ({"agent": {}}, "rule_only"),
    ({"agent": {"compact_mode": "rule_only"}}, "rule_only"),
    ({"agent": {"compact_mode": "llm"}}, "llm"),
    ({"agent": {"compact_mode": " LLM "}}, "llm"),
    ({"agent": {"compact_mode": "magic"}}, "rule_only"),
    ({"agent": {"compact_mode": 123}}, "rule_only"),
    ({"agent": "不是字典"}, "rule_only"),
])
def test_resolve_compact_mode_fail_safe(config, expected):
    """缺失 / 非法 / 类型错误一律 rule_only（fail-safe，绝不意外走网络）。"""
    assert compaction_mod.resolve_compact_mode(config) == expected


# ── LLM 摘要：成功与全失败面降级（全部离线） ────────────────────────────


def test_llm_summarize_accepts_valid_json():
    """注入的 llm_fn 返回合法 JSON → 五分区结构化摘要生效。"""
    def fake(prompt, **kwargs):
        assert "JSON" in prompt
        return _LLM_JSON

    result = compaction_mod.llm_summarize([{"role": "user", "content": "x"}], llm_fn=fake)
    assert result is not None
    assert set(result) == set(compaction_mod.SUMMARY_KEYS)
    assert result["key_info"]["syllabus"] == ["LLM-约束：小作文只考书信与通知"]
    assert result["pending"] == ["LLM-诉求：还想练翻译"]


def test_llm_summarize_tolerates_json_code_fence():
    """模型偶尔套 ```json 围栏：容忍并解析，不因格式小瑕疵就降级。"""
    fenced = "```json\n" + _LLM_JSON + "\n```"
    result = compaction_mod.llm_summarize(
        [{"role": "user", "content": "x"}], llm_fn=lambda prompt, **kw: fenced
    )
    assert result is not None
    assert result["goal"] == ["LLM-目标：拿下翻译得分点"]


@pytest.mark.parametrize("behavior", ["raise", "none", "not_json", "missing_key", "key_info_not_object"])
def test_llm_summarize_degrades_to_none_on_any_failure(behavior):
    """抛异常 / 返回 None / 非 JSON / 缺键 / key_info 非对象 → 一律返回 None 且不抛。"""
    def fake(prompt, **kwargs):
        if behavior == "raise":
            raise RuntimeError("模拟 LLM 网络异常")
        if behavior == "none":
            return None
        if behavior == "not_json":
            return "这不是JSON"
        if behavior == "missing_key":
            return '{"goal": ["只有目标"]}'
        return '{"goal": [], "progress": [], "key_info": "不是对象", "file_ops": [], "pending": []}'

    assert compaction_mod.llm_summarize([{"role": "user", "content": "x"}], llm_fn=fake) is None


def test_default_llm_entry_without_api_key_makes_no_network_call(monkeypatch):
    """未配置 key 时走真实默认入口（chat_completion）：返回 None 且不碰网络层。"""
    import tools.llm_client as llm_client

    def _forbidden(*args, **kwargs):
        raise AssertionError("未配置 API Key 时不应发起任何网络请求")

    monkeypatch.setattr(llm_client, "_llm_urlopen", _forbidden)
    monkeypatch.setattr(llm_client, "safe_urlopen", _forbidden)

    result = compaction_mod.llm_summarize(
        [{"role": "user", "content": "x"}],
        config={"api_key": "", "base_url": "https://example.invalid/v1", "model": "demo-model"},
    )
    assert result is None


# ── compact_context 集成：模式、focus、钩子、既有契约 ───────────────────


def test_compact_context_llm_mode_uses_injected_summary(tmp_path, monkeypatch):
    """llm 模式：注入的 llm_fn 生效，摘要走 LLM 结果而非规则抽取。"""
    monkeypatch.setattr(compaction_mod, "_default_llm_fn",
                        lambda: (lambda prompt, **kw: _LLM_JSON))
    ce = _make_engine(tmp_path, config={"agent": {"compact_mode": "llm"}})
    messages = _build_long_history(ce)

    summary_text = _summary_text(ce.compact_context(messages))
    assert "LLM-约束：小作文只考书信与通知" in summary_text
    assert "LLM-诉求：还想练翻译" in summary_text


def test_compact_context_llm_mode_degrades_to_rule_summary(tmp_path, monkeypatch, capsys):
    """llm 模式失败 → 打印降级提示、回退规则摘要、关键约束仍在、不抛异常。"""
    def _boom(prompt, **kwargs):
        raise RuntimeError("模拟 LLM 故障")

    monkeypatch.setattr(compaction_mod, "_default_llm_fn", lambda: _boom)
    ce = _make_engine(tmp_path, config={"agent": {"compact_mode": "llm"}})
    messages = _build_long_history(ce)

    summary_text = _summary_text(ce.compact_context(messages))
    assert "Context Compaction" in summary_text
    assert "约束-甲" in summary_text, "降级后规则摘要仍须保护关键约束"
    assert "降级为规则摘要" in capsys.readouterr().out


def test_rule_only_mode_never_calls_llm(tmp_path, monkeypatch):
    """rule_only（默认）：不得触碰 LLM 入口（零网络、零依赖）。"""
    def _forbidden(*args, **kwargs):
        raise AssertionError("rule_only 模式不得调用 LLM")

    monkeypatch.setattr(compaction_mod, "_default_llm_fn", _forbidden)
    ce = _make_engine(tmp_path)
    messages = _build_long_history(ce)

    summary_text = _summary_text(ce.compact_context(messages))
    assert "Context Compaction" in summary_text
    assert "约束-甲" in summary_text


def test_rule_summary_notice_printed_once(tmp_path, capsys):
    """规则摘要提示每实例只出现一次（别每轮刷屏）。"""
    ce = _make_engine(tmp_path)
    messages = _build_long_history(ce)

    ce.compact_context(messages)
    assert "agent.compact_mode=llm" in capsys.readouterr().out

    ce.compact_context(messages)
    assert capsys.readouterr().out.strip() == "", "规则摘要提示重复打印"


def test_focus_prioritizes_matching_message_in_rule_summary(tmp_path):
    """focus 命中：相关消息在规则摘要的 goal 抽取中优先保留（无 focus 则丢弃）。"""
    ce = _make_engine(tmp_path)
    messages = _build_long_history(ce)
    focus_msg = {"role": "user", "content": "我想重点攻克洛必达法则的适用条件。"}
    messages = messages[:6] + [focus_msg] + messages[6:]

    plain = _summary_text(ce.compact_context(messages))
    assert "洛必达法则的适用条件" not in plain, "无 focus 时该消息不应入选（对照前提）"

    focused = _summary_text(ce.compact_context(messages, focus="洛必达"))
    assert "洛必达法则的适用条件" in focused


class _RecordingHooks:
    """只记录 BeforeCompact 调用次数与上下文的最小替身。"""

    def __init__(self):
        self.calls = []

    def trigger_before_compact(self, messages, context):
        self.calls.append({"count": len(messages), "context": dict(context)})


def test_before_compact_hook_still_triggered(tmp_path):
    """既有契约：压缩前仍触发 BeforeCompact 钩子；未触发压缩则不调用。"""
    ce = _make_engine(tmp_path)
    messages = _build_long_history(ce)
    hooks = _RecordingHooks()

    ce.compact_context(messages, hook_manager=hooks)
    assert len(hooks.calls) == 1
    assert hooks.calls[0]["count"] == len(messages)
    assert hooks.calls[0]["context"].get("active_subject") == "math"

    ce.compact_context([{"role": "system", "content": "s"},
                        {"role": "user", "content": "u"}], hook_manager=hooks)
    assert len(hooks.calls) == 1, "未触发压缩时不得调用 BeforeCompact"
