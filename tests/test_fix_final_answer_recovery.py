# -*- coding: utf-8 -*-
"""[收尾答案] AgentRunner.run() 步数耗尽时的最终答复恢复 —— 回归测试。

背景（KaoYanBench core50 实测）
------------------------------
被测 Agent 在评测中每一步都在调用工具，循环因 ``max_steps`` 耗尽而退出，
``final_answer`` 保持空串 —— 50/50 题的 ``final_answer`` 全为空，
``json_schema`` / ``numeric`` / ``source_level`` 等结构化检查成建制判负。

修复（tools/agent/loop.py）
--------------------------
1. 步数耗尽 / 模型空回复时，追加「禁用工具」的收尾指令
   （``FINALIZE_INSTRUCTION``）再请求一次：``_call_llm(..., allow_tools=False)``，
   HTTP payload 不含 ``tools`` / ``tool_choice``；
2. 仍无内容则回退「最后一条非空 assistant 文本」；
3. 都没有则返回空串（绝不伪造答案）；
4. API 硬失败（``_call_llm`` 返回 None）不介入，保持空串，让 GUI/REPL 走
   各自的「未返回有效回复」诊断提示。

全程离线：mock ``AgentRunner._call_llm`` 与工具执行，绝不联网。
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from tools.agent.loop import AgentRunner  # noqa: E402


def _text_reply(content):
    """无工具调用的普通回复（循环应在此终止）。"""
    return {"choices": [{"message": {"content": content, "tool_calls": []}}]}


def _tool_reply(name="read_file", args=None, content=None, call_id="call_x"):
    """带工具调用的回复（模型继续要工具 → 循环继续）。"""
    return {"choices": [{"message": {
        "content": content,
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(args or {"path": "样本.txt"},
                                                 ensure_ascii=False)},
        }],
    }}]}


def _install_scripted_llm(monkeypatch, scripted):
    """把 ``_call_llm`` 替换为脚本化假实现；返回调用记录列表。

    ``scripted`` 元素：dict 直接作为响应返回；``None`` 模拟 API 硬失败。
    脚本耗尽后沿用最后一项（便于断言「不会多调」）。
    """
    calls = []

    def fake_call_llm(self, messages, allow_tools=True):
        calls.append({
            "messages": list(messages),
            "allow_tools": allow_tools,
        })
        idx = min(len(calls) - 1, len(scripted) - 1)
        return scripted[idx]

    monkeypatch.setattr(AgentRunner, "_call_llm", fake_call_llm)
    return calls


def _make_runner(tmp_path, max_steps, exec_result="样本工具结果"):
    """构造离线 AgentRunner；工具执行固定返回，不触碰真实工具。"""
    cfg = {"api_key": "sk-test-fake", "model": "样本模型", "active_subject": "pol"}
    runner = AgentRunner(config=cfg, workspace_root=tmp_path,
                         permission_mode="auto", quiet=True, max_steps=max_steps)
    runner.tool_registry.execute_tool = (
        lambda name, args, interactive=True: exec_result)
    return runner


# ── 1. 核心：步数耗尽 → 收尾请求 ────────────────────────────────────────


def test_exhausted_steps_triggers_finalize_request(tmp_path, monkeypatch):
    """步数耗尽 → 追加「禁用工具」的收尾请求，返回其内容。"""
    calls = _install_scripted_llm(monkeypatch, [
        _tool_reply(content="第一步分析"),
        _tool_reply(content="第二步分析"),
        _text_reply("最终答复：报告已整理完成"),
    ])
    runner = _make_runner(tmp_path, max_steps=2)

    answer = runner.run("请给出最终报告", interactive=False)

    assert answer == "最终答复：报告已整理完成", f"实际返回: {answer!r}"
    assert len(calls) == 3, f"应为 2 步工具 + 1 次收尾，实际 {len(calls)} 次"
    assert calls[0]["allow_tools"] is True
    assert calls[1]["allow_tools"] is True
    assert calls[2]["allow_tools"] is False, "收尾请求必须禁用工具"
    assert calls[2]["messages"][-1]["content"] == AgentRunner.FINALIZE_INSTRUCTION, \
        "收尾请求最后一条消息必须是收尾指令"
    print("  [√] 步数耗尽 → 收尾请求返回最终答复")


def test_finalize_failure_falls_back_to_last_assistant_text(tmp_path, monkeypatch):
    """收尾请求失败（返回 None）→ 回退最后一条非空 assistant 文本。"""
    calls = _install_scripted_llm(monkeypatch, [
        _tool_reply(content="第一步分析"),
        _tool_reply(content="第二步分析（更完整）"),
        None,  # 收尾请求网络失败
    ])
    runner = _make_runner(tmp_path, max_steps=2)

    answer = runner.run("问题", interactive=False)

    assert answer == "第二步分析（更完整）", f"实际返回: {answer!r}"
    assert len(calls) == 3
    print("  [√] 收尾失败 → 回退最后一条 assistant 文本")


def test_finalize_empty_and_no_text_returns_empty(tmp_path, monkeypatch):
    """收尾与回退都拿不到内容 → 返回空串（绝不伪造答案）。"""
    calls = _install_scripted_llm(monkeypatch, [
        _tool_reply(content=None),
        _text_reply(""),
    ])
    runner = _make_runner(tmp_path, max_steps=1)

    answer = runner.run("问题", interactive=False)

    assert answer == "", f"应保持空串，实际: {answer!r}"
    assert len(calls) == 2
    print("  [√] 无任何可用文本 → 返回空串（不伪造）")


# ── 2. 边界：API 硬失败不介入 / 正常路径零额外调用 ──────────────────────


def test_api_hard_failure_skips_finalize(tmp_path, monkeypatch):
    """API 硬失败 → 不做收尾请求（保持空串，GUI/REPL 走诊断提示）。"""
    calls = _install_scripted_llm(monkeypatch, [None])
    runner = _make_runner(tmp_path, max_steps=3)

    answer = runner.run("问题", interactive=False)

    assert answer == ""
    assert len(calls) == 1, "API 硬失败后不得再发起收尾请求"
    print("  [√] API 硬失败 → 不介入、不额外请求")


def test_normal_answer_does_not_trigger_finalize(tmp_path, monkeypatch):
    """正常一轮出答案 → 零额外请求（收尾逻辑不介入）。"""
    calls = _install_scripted_llm(monkeypatch, [_text_reply("正常答案")])
    runner = _make_runner(tmp_path, max_steps=5)

    answer = runner.run("问题", interactive=False)

    assert answer == "正常答案"
    assert len(calls) == 1, "正常路径不得有收尾请求"
    assert calls[0]["allow_tools"] is True
    print("  [√] 正常路径零额外调用")


def test_finalize_tool_call_tags_are_stripped_then_fallback(tmp_path, monkeypatch):
    """收尾响应仍是 <tool_call> 标签文本 → 剥标签；为空则回退 assistant 文本。"""
    calls = _install_scripted_llm(monkeypatch, [
        _tool_reply(content="分析说明"),
        _text_reply('<tool_call>{"name": "read_file", "arguments": {}}</tool_call>'),
    ])
    runner = _make_runner(tmp_path, max_steps=1)

    answer = runner.run("问题", interactive=False)

    assert answer == "分析说明", f"实际返回: {answer!r}"
    assert "<tool_call>" not in answer
    assert len(calls) == 2
    print("  [√] 收尾响应含 tool_call 标签 → 剥标签后回退")


def test_finalize_tool_call_tags_with_prefix_text(tmp_path, monkeypatch):
    """收尾响应 = 正文 + <tool_call> 标签 → 取标签前的正文作为答案。"""
    calls = _install_scripted_llm(monkeypatch, [
        _tool_reply(content=None),
        _text_reply('答案正文如下。\n<tool_call>{"name": "read_file"}</tool_call>'),
    ])
    runner = _make_runner(tmp_path, max_steps=1)

    answer = runner.run("问题", interactive=False)

    assert answer == "答案正文如下。", f"实际返回: {answer!r}"
    assert len(calls) == 2
    print("  [√] 收尾响应 正文+标签 → 取正文")


# ── 3. HTTP 层：allow_tools 参数真实作用于请求体 ────────────────────────


def _fake_urlopen_capturing(captured):
    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        resp = MagicMock()
        resp.__enter__.return_value = resp
        resp.read.return_value = json.dumps(
            {"choices": [{"message": {"content": "ok", "tool_calls": []}}]},
            ensure_ascii=False).encode("utf-8")
        resp.headers = {}
        return resp
    return fake_urlopen


def test_call_llm_allow_tools_false_omits_tools_in_payload(tmp_path):
    """HTTP 层：allow_tools=False 时请求体不含 tools / tool_choice；默认为 True。"""
    captured = {}
    cfg = {"api_key": "sk-test-fake", "base_url": "https://api.test.com/v1",
           "model": "样本模型"}
    runner = AgentRunner(config=cfg, workspace_root=tmp_path,
                         permission_mode="auto", quiet=True)

    with patch("tools.agent.loop.safe_urlopen",
               side_effect=_fake_urlopen_capturing(captured)):
        runner._call_llm([{"role": "user", "content": "hi"}], allow_tools=False)
    assert "tools" not in captured["body"], "allow_tools=False 不得携带 tools 字段"
    assert "tool_choice" not in captured["body"], "allow_tools=False 不得携带 tool_choice"

    with patch("tools.agent.loop.safe_urlopen",
               side_effect=_fake_urlopen_capturing(captured)):
        runner._call_llm([{"role": "user", "content": "hi"}])
    assert "tools" in captured["body"], "默认 allow_tools=True 应携带 tools 字段"
    assert "tool_choice" in captured["body"]
    print("  [√] HTTP payload：allow_tools 开关真实生效")
