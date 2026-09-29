# -*- coding: utf-8 -*-
"""[W4/W5] 作答契约与引用规范注入 + 压缩来源保留 —— 回归测试。

背景（KaoYanBench core50 实测）
------------------------------
- 引用维度 0 分：系统提示无任何引用要求、压缩把 URL 与网页正文整段丢弃；
- json_schema 成建制失败：模型输出散文而非 JSON（提示词未给结构化输出要求）；
- 产物文件缺失（knowledge_report.json 等）：提示词未硬性要求 write_file。

修复
----
1. ``context_engine.build_system_prompt`` 无条件注入「作答契约与引用规范」段
   （引用 URL / 结构化输出 / 产物落盘 / 收尾自检），教练版与通用版均覆盖；
2. ``compaction`` 新增 citations 保护类别：URL 与出处行在压缩中存活，
   渲染为独立分区（不与其他关键信息共享 12 条上限）。

全程离线。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from tools.agent.compaction import (  # noqa: E402
    CITATION_MAX_ITEMS,
    build_structured_summary,
    extract_key_info,
    render_summary,
)
from tools.agent.context_engine import ContextEngine  # noqa: E402


def _make_engine(tmp_path):
    return ContextEngine(workspace_root=tmp_path, active_subject="pol", config={})


# ── 1. 作答契约段注入 ──────────────────────────────────────────────────


def test_contract_section_injected_without_agents_md(tmp_path):
    """评测/空 workspace（无 AGENTS.md，走通用版）也必须注入作答契约段。"""
    engine = _make_engine(tmp_path)
    prompt = engine.build_system_prompt()
    assert "作答契约与引用规范" in prompt
    assert "完整 URL" in prompt
    assert "write_file" in prompt
    assert "第一字符必须是 { 或 [" in prompt
    assert "收尾自检" in prompt
    print("  [√] 空 workspace 注入作答契约段")


def test_contract_section_injected_with_agents_md(tmp_path):
    """真实工作区（有 AGENTS.md，走教练版）同样注入作答契约段。"""
    (tmp_path / "AGENTS.md").write_text("# 总控协议\n测试内容", encoding="utf-8")
    engine = _make_engine(tmp_path)
    prompt = engine.build_system_prompt()
    assert "作答契约与引用规范" in prompt
    assert "完整 URL" in prompt
    print("  [√] 教练版同样注入作答契约段")


# ── 1b. [W10] 契约段新增三条规范（联网检索/规范表述/JSON 自查） ─────────


def test_contract_section_has_search_tool_guidance(tmp_path):
    """[W10] 联网检索引导：优先 web_search、不手动拼搜索引擎 URL（修 SEARCH-005）。"""
    engine = _make_engine(tmp_path)
    prompt = engine.build_system_prompt()
    assert "优先使用 web_search" in prompt
    assert "不要手动拼接搜索引擎 URL" in prompt
    assert "文件形态" in prompt  # 文件直链专项检索引导
    print("  [√] 联网检索工具选择规范已注入")


def test_contract_section_has_data_availability_wording(tmp_path):
    """[W10] 数据可得性规范表述：未公开时用「尚未公布」类表述（修 HAL-004）。"""
    engine = _make_engine(tmp_path)
    prompt = engine.build_system_prompt()
    assert "数据可得性表述" in prompt
    assert "尚未公布" in prompt
    print("  [√] 数据可得性规范表述已注入")


def test_contract_section_has_json_syntax_selfcheck(tmp_path):
    """[W10] JSON 语法自查：对象元素必须有键名（防 SEARCH-008 型非法 JSON）。"""
    engine = _make_engine(tmp_path)
    prompt = engine.build_system_prompt()
    assert "JSON 语法自查" in prompt
    assert "不得混入无键名的裸字符串" in prompt
    print("  [√] JSON 语法自查规范已注入")


# ── 2. 压缩来源保留（citations 类别） ──────────────────────────────────


def test_extract_key_info_keeps_urls():
    """URL 行 / 出处行进入 citations 桶（压缩不丢来源）。"""
    messages = [
        {"role": "tool", "name": "web_search",
         "content": "结果:\n[1] 研招网公告\n    https://yz.chsi.com.cn/kyzx/notice.html\n摘要..."},
        {"role": "assistant", "content": "根据研招网公告，初试时间为 12 月。"},
        {"role": "user", "content": "来源：https://yz.chsi.com.cn/"},
    ]
    info = extract_key_info(messages)
    assert "citations" in info
    joined = "\n".join(info["citations"])
    assert "https://yz.chsi.com.cn/kyzx/notice.html" in joined
    assert "来源：https://yz.chsi.com.cn/" in joined
    print("  [√] URL 与出处行进入 citations 保护桶")


def test_extract_key_info_keeps_citation_phrases():
    """「据…公告/报道」类引文行同样受保护。"""
    messages = [
        {"role": "assistant", "content": "据教育部公告，2026 年报名时间为 10 月。"},
    ]
    info = extract_key_info(messages)
    joined = "\n".join(info["citations"])
    assert "据教育部公告" in joined
    print("  [√] 引文短语受保护")


def test_citation_bucket_capped():
    """citations 桶上限 CITATION_MAX_ITEMS：URL 洪水不撑爆摘要。"""
    messages = [{"role": "tool", "name": "web_search",
                 "content": "\n".join(f"https://example.com/page{i}" for i in range(20))}]
    info = extract_key_info(messages)
    assert len(info["citations"]) == CITATION_MAX_ITEMS
    print("  [√] citations 桶按上限截断")


def test_render_summary_has_separate_citation_section():
    """渲染：来源与引文独立分区。"""
    summary = build_structured_summary([
        {"role": "user", "content": "来源：https://a.example.com/x"},
    ])
    text = render_summary(summary)
    assert "[来源与引文（可引用）]" in text
    assert "https://a.example.com/x" in text
    print("  [√] 来源与引文独立分区渲染")


def test_render_summary_citations_not_in_key_info_section():
    """citations 不出现在「关键信息」分区（避免与考纲/错因混排挤占上限）。"""
    summary = build_structured_summary([
        {"role": "user", "content": "考纲约束：矩阵不考证明\n来源：https://a.example.com/x"},
    ])
    text = render_summary(summary)
    key_section = text.split("[关键信息")[1].split("[来源与引文")[0]
    assert "考纲约束" in key_section
    assert "https://a.example.com/x" not in key_section
    print("  [√] citations 不与关键信息混排")


def test_existing_categories_unchanged():
    """既有三类保护不受影响（回归防线）。"""
    messages = [
        {"role": "user", "content": "考纲约束：矩阵不考秩的证明"},
        {"role": "assistant", "content": "错因：概念漏洞——混淆了秩与行列式"},
        {"role": "user", "content": "待复习：泰勒展开的余项估计"},
    ]
    info = extract_key_info(messages)
    assert any("矩阵不考秩的证明" in x for x in info["syllabus"])
    assert any("概念漏洞" in x for x in info["mistakes"])
    assert any("泰勒展开" in x for x in info["review"])
    print("  [√] 既有三类保护不受影响")


# ── 3. [W4] 引用产出：_emit_citations 落盘与诚实核验 ────────────────────


def _make_runner(tmp_path):
    from tools.agent.loop import AgentRunner
    cfg = {"api_key": "sk-test-fake", "model": "样本模型", "active_subject": "pol"}
    return AgentRunner(config=cfg, workspace_root=tmp_path,
                       permission_mode="auto", quiet=True, max_steps=1)


def _read_citations(tmp_path):
    import json
    p = tmp_path / ".memory" / "last_citations.json"
    return json.loads(p.read_text(encoding="utf-8"))


def test_emit_citations_supported_when_in_tool_results(tmp_path):
    """答案 URL 在本轮工具结果中出现过 → supported=True（有检索证据）。"""
    runner = _make_runner(tmp_path)
    answer = "初试时间为 12 月。\n来源：https://yz.chsi.com.cn/kyzx/notice.html"
    messages = [
        {"role": "tool", "name": "web_search",
         "content": "结果: [1] 公告 https://yz.chsi.com.cn/kyzx/notice.html ..."},
    ]
    runner._emit_citations(answer, messages)

    data = _read_citations(tmp_path)
    assert len(data) == 1, f"应为 1 条引用，实际 {len(data)}"
    c = data[0]
    assert c["source_ref"] == "https://yz.chsi.com.cn/kyzx/notice.html"
    assert c["supported"] is True
    assert c["unsupported_reason"] is None
    assert "来源" in c["claim"]
    assert c["judge"] == "agent_reported"
    print("  [√] 工具证据核验：supported=True")


def test_emit_citations_unsupported_when_not_in_tool_results(tmp_path):
    """答案 URL 未在本轮工具结果中出现 → 如实标 supported=False + 原因。"""
    runner = _make_runner(tmp_path)
    answer = "据 https://model-invented.example.com/x 所述，初试时间为 12 月。"
    runner._emit_citations(answer, [{"role": "tool", "name": "web_search",
                                     "content": "结果: 无相关页面"}])

    data = _read_citations(tmp_path)
    assert len(data) == 1
    c = data[0]
    assert c["supported"] is False, "未核验的引用不得标 supported=True"
    assert c["unsupported_reason"] and "未在本轮工具结果中" in c["unsupported_reason"]
    print("  [√] 未核验引用：如实标 supported=False")


def test_emit_citations_empty_answer_no_file(tmp_path):
    """空答案 → 不产生引用文件（不伪造空壳）。"""
    runner = _make_runner(tmp_path)
    runner._emit_citations("", [{"role": "tool", "name": "x", "content": "y"}])
    assert not (tmp_path / ".memory" / "last_citations.json").exists()
    print("  [√] 空答案不落盘")


def test_emit_citations_dedup_and_order(tmp_path):
    """同一 URL 多次出现 → 去重；citation_id 按出现顺序编号。"""
    runner = _make_runner(tmp_path)
    answer = ("https://a.example.com/1\n中间说明\n"
              "https://a.example.com/1\nhttps://b.example.com/2")
    runner._emit_citations(answer, [])
    data = _read_citations(tmp_path)
    assert [c["source_ref"] for c in data] == [
        "https://a.example.com/1", "https://b.example.com/2"]
    assert [c["citation_id"] for c in data] == ["cit1", "cit2"]
    print("  [√] 去重与编号")
