# -*- coding: utf-8 -*-
"""tokenizer 与上下文预算回归测试（2026-09-24，规划批次 A2）。

背景（真实缺陷，非推测）：
    旧实现 `estimate_tokens = int(total_chars * 0.6)` 把中英一视同仁 —— 中文
    1 个汉字实际约 1–1.5 token（不同 tokenizer 差异很大），0.6 系数把中文上下文
    **低估 2 倍以上**；同时 `max_context_tokens` 硬编码 48000，对 2026 年的模型
    窗口过于保守。压缩触发线因此既不准也不可控。

修复后的口径（本测试钉住，不得回退）：
    1. 五类字符启发式（汉字/中文标点/ASCII 字母/空白/其余），单价由真实 API
       标定得出（见 fixtures 的 `_meta`），在 ≥6 条黄金向量上误差 ≤10%；
    2. 预算 = 模型窗口 − RESERVED_OUTPUT_TOKENS，压缩水位为可用预算的 70%，
       **断言 budget ≤ max − RESERVED 恒成立**；
    3. 窗口解析优先级：`context.max_tokens` 配置项 > 模型查表 > 保守默认；
       未知模型取默认值而**不假定大窗口**（高估窗口 = 400 溢出，低估只是提前压缩）；
    4. Rust 侧 `ky_rust_ext.estimate_tokens` 与 Python 侧在同一向量上逐位相同。

数据来源：
    黄金向量的 `reference_tokens` 是 2026-09-24 对**真实 API** 的
    `usage.prompt_tokens` 按差值法（扣除实测固定开销 62 token）得到的净文本计数，
    已固化进 `fixtures/tokenizer_golden_vectors.json`，故本测试**完全离线**。
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools.agent.tokenizer import (  # noqa: E402
    COMPACT_THRESHOLD,
    CONTEXT_BUDGET,
    DEFAULT_CONTEXT_WINDOW,
    RESERVED_OUTPUT_TOKENS,
    Tokenizer,
    classify_chars,
    heuristic_count,
    heuristic_count_messages,
    lookup_context_window,
    make_budget,
    resolve_budget,
    resolve_context_window,
    tokens_from_counts,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "tokenizer_golden_vectors.json"

try:
    import ky_rust_ext as _rust  # noqa: F401
    _HAS_RUST = True
except ImportError:  # pragma: no cover - 未编译 Rust 扩展的环境
    _HAS_RUST = False


def _golden():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# ── 黄金向量（核心验收）──────────────────────────────────────────────


def test_golden_vectors_cover_required_categories():
    """验收要求覆盖 中文/英文/代码/中英混排/长文档/短消息 六类，且 ≥6 条。"""
    doc = _golden()
    names = {v["name"] for v in doc["vectors"]}
    assert len(doc["vectors"]) >= 6, f"黄金向量不足 6 条：{len(doc['vectors'])}"
    for required in ("chinese_paragraph", "english_paragraph", "code_snippet",
                     "mixed_cn_en", "long_document", "short_message"):
        assert required in names, f"缺少必需类目：{required}"


def test_golden_vectors_within_tolerance():
    """启发式估算与真实模型 tokenizer 的偏差必须在容差内。

    容差口径（与 fixtures `_meta.tolerance` 同源）：
      - 净计数 ≥20 token → 相对误差 ≤10%；
      - 净计数 <20 token → 绝对误差 ≤3 token（固定开销扣除与整数取整本身的
        噪声就有 ±1~2 token，相对误差无意义）。
    """
    doc = _golden()
    tol = doc["_meta"]["tolerance"]
    failures = []
    for v in doc["vectors"]:
        ref = v["reference_tokens"]
        got = heuristic_count(v["text"])
        if ref >= tol["relative_min_reference_tokens"]:
            err = abs(got - ref) / ref
            if err > tol["relative"]:
                failures.append(f"{v['name']}: 实净={ref} 估算={got} 相对误差={err:.1%}")
        else:
            if abs(got - ref) > tol["absolute_tokens_for_tiny"]:
                failures.append(f"{v['name']}: 实净={ref} 估算={got} 绝对误差超限")
    assert not failures, "黄金向量超容差：\n  " + "\n  ".join(failures)


def test_golden_vectors_fixture_is_self_consistent():
    """夹具里预存的 heuristic 列必须与当前实现一致（防止改了系数忘了重算夹具）。"""
    for v in _golden()["vectors"]:
        assert heuristic_count(v["text"]) == v["heuristic"], f"{v['name']} 与夹具预存值不符"
        assert v["chars"] == len(v["text"])
        assert sum(v["classes"].values()) == v["chars"], f"{v['name']} 分类计数与字符数不符"


def test_golden_vectors_reference_is_live_measurement():
    """`reference_tokens` 必须是扣除固定开销后的真实 API 净计数，而非臆造值。"""
    meta = _golden()["_meta"]
    assert meta["measured_fixed_overhead_tokens"] > 0
    for v in _golden()["vectors"]:
        assert v["api_raw_tokens"] - meta["measured_fixed_overhead_tokens"] == v["reference_tokens"]


# ── Rust / Python 逐位一致 ───────────────────────────────────────────


@pytest.mark.skipif(not _HAS_RUST, reason="未编译 Rust 扩展（可选加速）")
def test_rust_and_python_agree_on_golden_vectors():
    """两侧必须逐位相同 —— 任一系数或字符分类漂移都会在这里暴露。"""
    for v in _golden()["vectors"]:
        msgs = [{"content": v["text"]}]
        assert heuristic_count_messages(msgs) == _rust.estimate_tokens(msgs), v["name"]


@pytest.mark.skipif(not _HAS_RUST, reason="未编译 Rust 扩展（可选加速）")
@pytest.mark.parametrize("messages", [
    [{"content": "中英混排 mixed 123，。！“”" * 37}],
    [{"content": "x", "tool_calls": [{"id": "1", "function": {"name": "t"}}]}],
    [{"content": "x", "tool_calls": None}],   # None 不得被数成 "None" 的 4 个字符
    [{"content": None}],
    [{"content": "a b c\n\t d"}],
    [{"content": "😀🎉"}],
    [{"content": "汉字" * 500}],
])
def test_rust_and_python_agree_on_edge_cases(messages):
    assert heuristic_count_messages(messages) == _rust.estimate_tokens(messages)


# ── 预算解析与水位 ───────────────────────────────────────────────────


def test_budget_never_exceeds_window_minus_reserved():
    """核心不变量：可用预算恒等于 窗口 − 预留，且水位不超过预算。"""
    for window in (8_000, 8_001, 32_768, 128_000, 200_000, 1_000_000):
        b = make_budget(window)
        assert b.budget == window - RESERVED_OUTPUT_TOKENS
        assert b.watermark <= b.budget
        assert b.watermark == int(b.budget * COMPACT_THRESHOLD)


def test_budget_floors_at_zero_for_tiny_windows():
    """窗口小于预留位时预算/水位归零，而不是变成负数。"""
    for window in (1, 50, 100, RESERVED_OUTPUT_TOKENS):
        b = make_budget(window)
        assert b.budget == 0 and b.watermark == 0


def test_context_window_precedence_config_over_table_over_default():
    """优先级：config['context']['max_tokens'] > 模型查表 > 默认。"""
    assert resolve_context_window("gpt-4o") == (CONTEXT_BUDGET["gpt-4o"], "table:gpt-4o")
    assert resolve_context_window("gpt-4o", {"context": {"max_tokens": 32_000}}) == (32_000, "config")
    # 未知模型取默认值，且来源标注为 default（便于上层把"正在假定窗口"暴露给用户）
    assert resolve_context_window("某未收录型号-xyz") == (DEFAULT_CONTEXT_WINDOW, "default")


def test_unknown_model_does_not_assume_large_window():
    """未知模型不得拿到比默认值更大的窗口（高估窗口 = 直接 400 溢出）。"""
    for name in ("", "某未收录型号", "某中转特价型号-flash", "totally-made-up-model"):
        window, source = resolve_context_window(name)
        assert window <= DEFAULT_CONTEXT_WINDOW, f"{name} 被假定了大窗口：{window}"
        assert source == "default"


@pytest.mark.parametrize("bad", ["abc", "", None, True, False, -1, 0, 3.5, [], {}])
def test_invalid_config_values_fall_back_without_raising(bad):
    """非法配置项静默回落查表，不抛异常 —— 上下文预算出错不该让会话起不来。"""
    window, source = resolve_context_window("gpt-4o", {"context": {"max_tokens": bad}})
    assert (window, source) == (CONTEXT_BUDGET["gpt-4o"], "table:gpt-4o")


@pytest.mark.parametrize("good,expect", [(32_000, 32_000), ("32000", 32_000), (32000.0, 32_000)])
def test_integral_config_values_are_accepted(good, expect):
    """JSON 里写成 128000.0 / "128000" 是常见的，按整数收；3.5 这类非整值则回落。"""
    assert resolve_context_window("gpt-4o", {"context": {"max_tokens": good}}) == (expect, "config")


def test_budget_source_is_reported():
    assert resolve_budget("gpt-4o").source == "table:gpt-4o"
    assert resolve_budget("某未收录型号").source == "default"
    assert resolve_budget("gpt-4o", {"context": {"max_tokens": 16_000}}).source == "config"
    assert make_budget(16_000).source == "explicit"


def test_lookup_prefers_longest_table_key():
    """`gpt-4o` 不得被更短的 `gpt-4` 前缀抢走（表内最长匹配优先）。"""
    assert lookup_context_window("gpt-4o")[1] == "table:gpt-4o"
    assert lookup_context_window("GPT-4O")[1] == "table:gpt-4o"  # 大小写不敏感


# ── 字符分类 ─────────────────────────────────────────────────────────


def test_classify_chars_covers_all_five_categories():
    han, punct, alpha, space, sym = classify_chars("汉字，。abc \n123+-")
    assert (han, punct, alpha, space, sym) == (2, 2, 3, 2, 5)


def test_classify_chars_treats_chinese_quotes_as_punct():
    """中文引号/破折号在通用标点区，但单价与全角标点同档，不得混入"其余"。"""
    han, punct, alpha, space, sym = classify_chars("“引号”—…·")
    assert han == 2 and punct == 5 and (alpha, space, sym) == (0, 0, 0)


def test_tokens_from_counts_is_single_formula_entry():
    """两个入口共用同一公式：分条与合并的口径一致（先求和再整除一次）。"""
    assert tokens_from_counts(10, 0, 0, 0, 0) == 5      # 10 汉字 × 0.52
    assert tokens_from_counts(0, 2, 0, 0, 0) == 3       # 2 标点 × 1.5
    assert tokens_from_counts(0, 0, 100, 0, 0) == 18    # 100 字母 × 0.18
    assert heuristic_count("中文") == tokens_from_counts(2, 0, 0, 0, 0)
    assert heuristic_count_messages([{"content": "中"}, {"content": "文"}]) == heuristic_count("中文")


def test_heuristic_counts_tool_calls_and_skips_none():
    with_tc = heuristic_count_messages(
        [{"content": "x", "tool_calls": [{"id": "1", "function": {"name": "t"}}]}])
    assert with_tc > heuristic_count_messages([{"content": "x"}])
    assert heuristic_count_messages([{"content": "x", "tool_calls": None}]) == \
        heuristic_count_messages([{"content": "x"}])


# ── Tokenizer（tiktoken 可选路径）────────────────────────────────────


def test_tokenizer_falls_back_to_heuristic_without_tiktoken(monkeypatch):
    """未装 tiktoken 时必须静默降级，绝不因缺少可选依赖而报错。"""
    tk = Tokenizer("某模型")
    monkeypatch.setattr(tk, "_enc", None)
    monkeypatch.setattr(tk, "mode", "heuristic")
    assert tk.count("中文") == heuristic_count("中文")
    assert tk.count_messages([{"content": "中文"}]) == heuristic_count_messages([{"content": "中文"}])


def test_tokenizer_count_is_never_negative_and_handles_empty():
    tk = Tokenizer("")
    assert tk.count("") == 0
    assert tk.count_messages([]) == 0
    assert heuristic_count("") == 0


# ── ContextEngine 接入 ───────────────────────────────────────────────


def test_context_engine_no_longer_hardcodes_48000(tmp_path):
    from tools.agent.context_engine import ContextEngine

    ce = ContextEngine(workspace_root=tmp_path, active_subject="math")
    assert ce.max_context_tokens == DEFAULT_CONTEXT_WINDOW != 48_000
    assert ce.compact_watermark == int((DEFAULT_CONTEXT_WINDOW - RESERVED_OUTPUT_TOKENS)
                                       * COMPACT_THRESHOLD)


def test_context_engine_honours_config_override(tmp_path):
    from tools.agent.context_engine import ContextEngine

    ce = ContextEngine(workspace_root=tmp_path,
                       config={"context": {"max_tokens": 32_000}, "model": "某模型"})
    assert ce.max_context_tokens == 32_000
    assert ce.compact_watermark == int((32_000 - RESERVED_OUTPUT_TOKENS) * COMPACT_THRESHOLD)


def test_context_engine_explicit_window_wins_over_config(tmp_path):
    from tools.agent.context_engine import ContextEngine

    ce = ContextEngine(workspace_root=tmp_path, max_context_tokens=100,
                       config={"context": {"max_tokens": 32_000}})
    assert ce.max_context_tokens == 100 and ce.budget.source == "explicit"


def test_new_estimate_beats_old_coefficient_on_every_golden_vector(tmp_path):
    """新启发式必须比旧的 `int(chars*0.6)` 更接近真实模型 —— 逐条向量都更准。

    旧系数对所有字符一律 0.6：对英文/代码高估 3 倍以上（实测 0.18 token/字符），
    对中文轻微高估（0.52 vs 0.6）。逐条更准是本批 A2 的实质收益。
    """
    from tools.agent.context_engine import ContextEngine

    ce = ContextEngine(workspace_root=tmp_path)
    ce.tokenizer._enc = None
    worse = []
    for v in _golden()["vectors"]:
        ref = v["reference_tokens"]
        old = int(v["chars"] * 0.6)
        new = ce.estimate_tokens([{"content": v["text"]}])
        if abs(new - ref) > abs(old - ref):
            worse.append(f"{v['name']}: 真实={ref} 旧={old} 新={new}")
    assert not worse, "新估算反而不如旧系数：\n  " + "\n  ".join(worse)


def test_context_engine_force_python_means_pure_python_fallback(tmp_path):
    """`_force_python` 沿用全仓约定：跳过加速实现、走纯 Python 回退。

    回归背景：接入 tiktoken 后若仍让它优先于 `_force_python`，`test_new_features.py`
    的双模一致性校验会拿 tiktoken 结果去比 Rust 启发式，必然不等 —— Rust 只能做
    启发式，故 `_force_python` 必须同时跳过 tiktoken 精确路径。
    """
    from tools.agent.context_engine import ContextEngine

    ce = ContextEngine(workspace_root=tmp_path)
    msgs = [{"content": "考研数学二强化冲刺，中英 mixed 123"}]
    ce._force_python = True
    assert ce.estimate_tokens(msgs) == heuristic_count_messages(msgs)
    ce._force_python = False
    assert ce.estimate_tokens(msgs) == ce.tokenizer.count_messages(msgs)


def test_context_engine_compaction_triggers_at_watermark_not_at_window(tmp_path):
    """水位语义：估算超过「窗口−预留」的 70% 才压缩，而不是超过窗口才压缩。"""
    from tools.agent.context_engine import ContextEngine

    window = 16_000
    watermark = int((window - RESERVED_OUTPUT_TOKENS) * COMPACT_THRESHOLD)
    ce = ContextEngine(workspace_root=tmp_path, max_context_tokens=window)
    ce.tokenizer._enc = None
    head = [{"role": "system", "content": "S"}]
    tail = [{"role": "user", "content": f"第{i}问"} for i in range(8)]
    tail_tokens = ce.estimate_tokens(head + tail)

    def _history_of(target_tokens: int):
        """构造估算值恰好最接近 target 的历史（汉字单价 0.52，直接解出字数）。"""
        chars = max(1, int((target_tokens - tail_tokens) * 100 / 52))
        body = [{"role": "user", "content": "汉" * chars}]
        return head + body + tail

    below = _history_of(watermark - 500)
    assert ce.estimate_tokens(below) < watermark, ce.estimate_tokens(below)
    assert ce.compact_context(below) == below, "未超水位却触发了压缩"

    above = _history_of(watermark + 500)
    assert ce.estimate_tokens(above) > watermark, ce.estimate_tokens(above)
    out = ce.compact_context(above)
    assert any("Context Compaction" in m.get("content", "") for m in out), "超水位却未压缩"
