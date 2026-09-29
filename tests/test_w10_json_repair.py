# -*- coding: utf-8 -*-
"""[W10 JSON 交付自检] 疑似 JSON 答案语法非法时的一次性修复 —— 回归测试。

背景（KaoYanBench core50 w9 实测）
----------------------------------
SEARCH-008 的答案以 ``{`` 开头、内容完整（1392 字符），但 ``notes`` 对象里
混入了无键名的裸字符串元素 → 整体非法 JSON → 判分的 json_schema 检查
（``$.versions`` / ``$.changes``）解析失败、整题失分（72.12）。这类
「内容正确、语法非法」的失分与检索/推理能力无关。

修复（tools/agent/loop.py::AgentRunner._repair_json_answer）
------------------------------------------------------------
1. 仅在答案首字符为 ``{`` / ``[`` 且 ``json.loads`` 失败时触发；
2. 发一次禁用工具的修复请求（附原答案与解析错误；指令明确「仅修复语法、
   不得增删改任何实质内容」）；
3. 修复结果（剥代码围栏/``<tool_call>`` 标签后）仍非法、或请求失败 →
   保留原答案（绝不伪造）；
4. 成功时记 ``json_answer_repair`` 事件。

全程离线：mock ``AgentRunner._call_llm``，绝不联网。
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from tools.agent.loop import AgentRunner  # noqa: E402
from tools.agent.session_log import load_events  # noqa: E402

#: 复刻 SEARCH-008 的非法形态：notes 对象里混入无键名裸字符串（对象/数组语法写混）。
BROKEN_JSON = (
    '{"versions":[{"year":2024,"url":"https://a.example.edu.cn/"}],'
    '"changes":[{"aspect":"入口渠道"}],'
    '"notes":{"完成度说明":"未能取得正文页面","因此上述仅为访问层证据","建议核验路径：见官网"}}'
)
#: 修复版：把裸字符串元素归入数组（仅语法修复，内容不变）。
FIXED_JSON = (
    '{"versions":[{"year":2024,"url":"https://a.example.edu.cn/"}],'
    '"changes":[{"aspect":"入口渠道"}],'
    '"notes":{"完成度说明":"未能取得正文页面",'
    '"补充说明":["因此上述仅为访问层证据","建议核验路径：见官网"]}}'
)


def _text_reply(content):
    """无工具调用的普通回复（循环应在此终止）。"""
    return {"choices": [{"message": {"content": content, "tool_calls": []}}]}


def _make_runner(tmp_path):
    cfg = {"api_key": "sk-test-fake", "model": "样本模型", "active_subject": "pol"}
    runner = AgentRunner(config=cfg, workspace_root=tmp_path,
                         permission_mode="auto", quiet=True, max_steps=3)
    runner.tool_registry.execute_tool = (
        lambda name, args, interactive=True: "样本工具结果")
    return runner


def _patch_finalize(monkeypatch, returns):
    """替换 ``_finalize_request``；返回调用记录（messages, instruction）。"""
    calls = []

    def fake_finalize(self, messages, instruction):
        calls.append({"messages": list(messages), "instruction": instruction})
        return returns

    monkeypatch.setattr(AgentRunner, "_finalize_request", fake_finalize)
    return calls


# ── 1. 不触发的两类：合法 JSON / 非 JSON ────────────────────────────────


def test_valid_json_untouched(tmp_path, monkeypatch):
    """合法 JSON → 原样返回，不发起修复请求。"""
    runner = _make_runner(tmp_path)
    calls = _patch_finalize(monkeypatch, FIXED_JSON)
    out = runner._repair_json_answer('{"a": 1, "b": [2, 3]}', [])
    assert out == '{"a": 1, "b": [2, 3]}'
    assert calls == []


def test_prose_answer_untouched(tmp_path, monkeypatch):
    """散文答案（非 { / [ 开头）→ 原样返回，不发起修复请求。"""
    runner = _make_runner(tmp_path)
    calls = _patch_finalize(monkeypatch, FIXED_JSON)
    prose = "结论：2027 年招生人数尚未公布，建议 9 月复查官网。"
    assert runner._repair_json_answer(prose, []) == prose
    assert calls == []


def test_json_array_answer_also_checked(tmp_path, monkeypatch):
    """以 [ 开头的答案同样参与自检（非法时触发修复）。"""
    runner = _make_runner(tmp_path)
    calls = _patch_finalize(monkeypatch, '[{"a": 1}]')
    out = runner._repair_json_answer('[{"a": 1,}]', [])
    assert out == '[{"a": 1}]'
    assert len(calls) == 1


# ── 2. 触发修复：成功路径 ──────────────────────────────────────────────


def test_broken_json_repaired(tmp_path, monkeypatch):
    """非法 JSON + 修复版合法 → 返回修复版。"""
    runner = _make_runner(tmp_path)
    calls = _patch_finalize(monkeypatch, FIXED_JSON)
    out = runner._repair_json_answer(BROKEN_JSON, [])
    assert out == FIXED_JSON
    assert json.loads(out)  # 确实合法
    assert len(calls) == 1
    # 修复指令包含原答案与「不得增删改」约束
    instr = calls[0]["instruction"]
    assert "仅修复 JSON 语法" in instr
    assert "不得增删或改变任何实质内容" in instr
    assert BROKEN_JSON in instr


def test_repair_request_carries_system_prompt(tmp_path, monkeypatch):
    """修复请求透传 system 提示（截断 6000 字符），且不含工具消息。"""
    runner = _make_runner(tmp_path)
    calls = _patch_finalize(monkeypatch, FIXED_JSON)
    messages = [
        {"role": "system", "content": "系统提示" * 10},
        {"role": "user", "content": "任务原文"},
        {"role": "assistant", "content": "中间分析"},
        {"role": "tool", "content": "工具结果"},
    ]
    runner._repair_json_answer(BROKEN_JSON, messages)
    sent = calls[0]["messages"]
    assert sent == [{"role": "system", "content": "系统提示" * 10}]


def test_repair_result_fenced_is_unwrapped(tmp_path, monkeypatch):
    """修复版被 ``` 围栏包裹 → 剥壳后合法 → 采用剥壳版。"""
    runner = _make_runner(tmp_path)
    _patch_finalize(monkeypatch, "```json\n" + FIXED_JSON + "\n```")
    out = runner._repair_json_answer(BROKEN_JSON, [])
    assert out == FIXED_JSON


def test_repair_result_tool_call_tag_truncated(tmp_path, monkeypatch):
    """修复版尾部带 <tool_call> 降级标签 → 取标签前文本（合法则采用）。"""
    runner = _make_runner(tmp_path)
    _patch_finalize(monkeypatch, FIXED_JSON + "\n<tool_call>{\"name\": \"x\"}</tool_call>")
    out = runner._repair_json_answer(BROKEN_JSON, [])
    assert out == FIXED_JSON


# ── 3. 失败路径：一律保留原答案（不伪造） ──────────────────────────────


def test_repair_empty_keeps_original(tmp_path, monkeypatch):
    """修复请求返回空（网络失败/空回复）→ 保留原答案。"""
    runner = _make_runner(tmp_path)
    _patch_finalize(monkeypatch, "")
    assert runner._repair_json_answer(BROKEN_JSON, []) == BROKEN_JSON


def test_repair_still_broken_keeps_original(tmp_path, monkeypatch):
    """修复版仍非法 → 保留原答案。"""
    runner = _make_runner(tmp_path)
    _patch_finalize(monkeypatch, BROKEN_JSON)  # 没修好
    assert runner._repair_json_answer(BROKEN_JSON, []) == BROKEN_JSON


def test_repair_prose_keeps_original(tmp_path, monkeypatch):
    """修复版不是 JSON（模型改答散文）→ 保留原答案。"""
    runner = _make_runner(tmp_path)
    _patch_finalize(monkeypatch, "抱歉，我无法修复。")
    assert runner._repair_json_answer(BROKEN_JSON, []) == BROKEN_JSON


# ── 4. 集成：run() 全链路 ─────────────────────────────────────────────


def test_run_repairs_broken_json_answer(tmp_path, monkeypatch):
    """run() 全链路：模型首答非法 JSON → 自动修复 → final_answer 为修复版。"""
    runner = _make_runner(tmp_path)
    scripted = [_text_reply(BROKEN_JSON), _text_reply(FIXED_JSON)]
    calls = []

    def fake_call_llm(self, messages, allow_tools=True, tools_subset=None):
        calls.append({"allow_tools": allow_tools})
        idx = min(len(calls) - 1, len(scripted) - 1)
        return scripted[idx]

    monkeypatch.setattr(AgentRunner, "_call_llm", fake_call_llm)
    out = runner.run("请输出 JSON 报告", interactive=False)
    assert out == FIXED_JSON
    # 第二次调用是修复请求：禁用工具
    assert len(calls) == 2
    assert calls[1]["allow_tools"] is False
    # 事件已记录
    events = []
    for p in (tmp_path / ".memory" / "sessions").glob("*.jsonl"):
        events.extend(load_events(p))
    repairs = [e for e in events if e.get("payload", {}).get("kind") == "json_answer_repair"]
    assert len(repairs) == 1


def test_run_valid_json_no_extra_call(tmp_path, monkeypatch):
    """run() 全链路：模型首答合法 JSON → 不产生额外调用。"""
    runner = _make_runner(tmp_path)
    calls = []

    def fake_call_llm(self, messages, allow_tools=True, tools_subset=None):
        calls.append({"allow_tools": allow_tools})
        return _text_reply('{"ok": true}')

    monkeypatch.setattr(AgentRunner, "_call_llm", fake_call_llm)
    out = runner.run("请输出 JSON", interactive=False)
    assert out == '{"ok": true}'
    assert len(calls) == 1
