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
4. [W7 收尾强化] 收尾链扩为三档：全量 FINALIZE → 全量 RETRY → **极简消息
   MINIMAL**（系统提示 + 任务 + 最近工具结果摘要；长工具链后全量消息收尾
   空回复率高，压缩上下文可显著恢复）；
5. [W7 收尾强化] API 硬失败（``_call_llm`` 返回 None）**不再跳过收尾**：
   网络可能只是瞬断，先试一次低成本的极简收尾、再试全量；仍失败才回退
   「最后一条非空 assistant 文本」；两者皆无才返回空串。

全程离线：mock ``AgentRunner._call_llm`` 与工具执行，绝不联网。
"""

import io
import json
import sys
import urllib.error
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

    def fake_call_llm(self, messages, allow_tools=True, tools_subset=None):
        calls.append({
            "messages": list(messages),
            "allow_tools": allow_tools,
            "tools_subset": tools_subset,
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
    """收尾三档链全失败（返回 None）→ 回退最后一条非空 assistant 文本。"""
    calls = _install_scripted_llm(monkeypatch, [
        _tool_reply(content="第一步分析"),
        _tool_reply(content="第二步分析（更完整）"),
        None,  # 收尾请求网络失败
    ])
    runner = _make_runner(tmp_path, max_steps=2)

    answer = runner.run("问题", interactive=False)

    assert answer == "第二步分析（更完整）", f"实际返回: {answer!r}"
    assert len(calls) == 5, \
        f"应为 2 步工具 + 3 次收尾尝试（FINALIZE/RETRY/MINIMAL），实际 {len(calls)}"
    print("  [√] 收尾失败（三档链）→ 回退最后一条 assistant 文本")


def test_finalize_empty_and_no_text_returns_empty(tmp_path, monkeypatch):
    """收尾（含重试与极简档）与回退都拿不到内容 → 返回空串（绝不伪造答案）。"""
    calls = _install_scripted_llm(monkeypatch, [
        _tool_reply(content=None),
        _text_reply(""),
    ])
    runner = _make_runner(tmp_path, max_steps=1)

    answer = runner.run("问题", interactive=False)

    assert answer == "", f"应保持空串，实际: {answer!r}"
    assert len(calls) == 4, \
        f"应为 1 步工具 + 3 次收尾尝试（FINALIZE/RETRY/MINIMAL），实际 {len(calls)}"
    print("  [√] 无任何可用文本 → 返回空串（不伪造）")


def test_finalize_empty_then_retry_succeeds(tmp_path, monkeypatch):
    """第一次收尾空回复 → 换更强指令重试一次 → 拿到内容。

    评测实测（KaoYanBench core50）：长工具链（10+ 次调用）后部分模型对
    「总结」类收尾指令返回空 content；换用「已获得什么信息」的具体化指令
    重试一次可显著恢复产出。
    """
    calls = _install_scripted_llm(monkeypatch, [
        _tool_reply(content=None),          # 工具轮（无文本）
        _text_reply(""),                    # 第一次收尾：空
        _text_reply('{"schedule": []}'),    # 重试：成功
    ])
    runner = _make_runner(tmp_path, max_steps=1)

    answer = runner.run("问题", interactive=False)

    assert answer == '{"schedule": []}', f"实际返回: {answer!r}"
    assert len(calls) == 3, f"应为 1 步工具 + 2 次收尾尝试，实际 {len(calls)}"
    assert calls[1]["allow_tools"] is False
    assert calls[2]["allow_tools"] is False, "重试同样必须禁用工具"
    assert calls[2]["messages"][-1]["content"] == AgentRunner.FINALIZE_RETRY_INSTRUCTION, \
        "重试必须使用 FINALIZE_RETRY_INSTRUCTION"
    print("  [√] 收尾空 → 重试指令恢复产出")


# ── 2. 边界：API 硬失败 / 收尾异常 / 正常路径零额外调用 ────────────────


def test_api_hard_failure_still_attempts_finalize(tmp_path, monkeypatch):
    """[W7] API 硬失败且无任何 assistant 文本 → **仍尝试收尾**（极简 + 全量）→ 空串。

    旧实现在 API 硬失败时直接跳过收尾（假定「网络已断，只会再失败一次」），
    实测网络多为瞬断：跳过收尾是空答案题的主要失分路径之一。
    """
    calls = _install_scripted_llm(monkeypatch, [None])
    runner = _make_runner(tmp_path, max_steps=3)

    answer = runner.run("问题", interactive=False)

    assert answer == ""
    assert len(calls) == 3, "API 硬失败后仍应有 1 次极简 + 1 次全量收尾尝试"
    assert calls[0]["allow_tools"] is True, "主循环请求带工具"
    assert calls[1]["allow_tools"] is False and calls[2]["allow_tools"] is False, \
        "收尾请求必须禁用工具"
    assert len(calls[1]["messages"]) <= len(calls[2]["messages"]), \
        "极简收尾的消息数不应超过全量收尾"
    print("  [√] API 硬失败 → 仍尝试极简 + 全量收尾 → 空串（不伪造）")


def test_api_hard_failure_falls_back_to_last_assistant_text(tmp_path, monkeypatch):
    """API 硬失败但此前有工具轮分析文本 → 收尾尝试后仍无内容 → 回退该文本。

    评测实测：模型先调工具、再遇连接异常（``[连接异常] Remote end closed``），
    旧实现直接返回空串，白丢模型已经写出的分析内容。
    """
    calls = _install_scripted_llm(monkeypatch, [
        _tool_reply(content="第一步分析：已定位考点与采分点"),
        None,  # 第二步 API 硬失败
    ])
    runner = _make_runner(tmp_path, max_steps=3)

    answer = runner.run("问题", interactive=False)

    assert answer == "第一步分析：已定位考点与采分点", f"实际返回: {answer!r}"
    assert len(calls) == 4, "应为 1 步工具 + 1 步失败 + 2 次收尾尝试（极简+全量）"
    print("  [√] API 硬失败 + 有文本 → 收尾尝试后回退文本")


def test_finalize_exception_falls_back_to_last_assistant_text(tmp_path, monkeypatch):
    """收尾请求抛异常（非返回 None）→ 三档全异常 → 静默回退 assistant 文本。"""
    calls = []

    def fake_call_llm(self, messages, allow_tools=True, tools_subset=None):
        calls.append(allow_tools)
        if allow_tools:
            return _tool_reply(content="工具轮分析文本")
        raise RuntimeError("连接被重置")

    monkeypatch.setattr(AgentRunner, "_call_llm", fake_call_llm)
    runner = _make_runner(tmp_path, max_steps=1)

    answer = runner.run("问题", interactive=False)

    assert answer == "工具轮分析文本", f"实际返回: {answer!r}"
    assert calls == [True, False, False, False], \
        "应为 1 步工具 + 3 次收尾尝试（FINALIZE/RETRY/MINIMAL）"
    print("  [√] 收尾请求异常（三档）→ 静默回退、run() 不崩")


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


# ── 2.5 [W7] 极简收尾消息与 api_failed 优先策略 ─────────────────────────


def test_api_failed_minimal_finalize_uses_summary_not_full_history(tmp_path, monkeypatch):
    """[W7] API 硬失败时优先极简收尾：上下文压为「系统 + 任务 + 工具结果摘要」，
    不含完整工具结果原文（长上下文是收尾空回复与超时的主要来源）。"""
    long_result = "长工具结果" * 500  # 3000 字符
    calls = _install_scripted_llm(monkeypatch, [
        _tool_reply(content="轮一分析"),
        _tool_reply(content="轮二分析"),
        None,  # 第三步 API 硬失败
    ])
    runner = _make_runner(tmp_path, max_steps=3, exec_result=long_result)

    answer = runner.run("任务：输出报告", interactive=False)

    assert answer == "轮二分析", f"实际返回: {answer!r}"
    assert len(calls) == 5, f"应为 3 次主循环 + 2 次收尾，实际 {len(calls)}"
    minimal_msgs = calls[3]["messages"]
    full_msgs = calls[4]["messages"]
    assert calls[3]["allow_tools"] is False and calls[4]["allow_tools"] is False
    assert len(minimal_msgs) < len(full_msgs), "极简收尾消息数应少于全量"
    joined_min = json.dumps(minimal_msgs, ensure_ascii=False)
    assert "工具结果摘要" in joined_min, "极简收尾必须携带工具结果摘要"
    assert long_result not in joined_min, "极简收尾不得携带完整工具结果原文"
    print("  [√] api_failed 极简收尾：摘要替代长历史")


def test_minimal_finalize_messages_structure(tmp_path):
    """[W7] ``_build_minimal_finalize_messages`` 结构：系统提示截断 + 首条任务
    + 最近至多 5 条工具结果摘要（每条截断 600 字符）。"""
    runner = _make_runner(tmp_path, max_steps=1)
    messages = [
        {"role": "system", "content": "S" * 8000},
        {"role": "user", "content": "任务要求：输出 schedule JSON"},
        {"role": "assistant", "content": "分析一", "tool_calls": []},
        {"role": "tool", "name": "read_file", "content": "T1" * 1000},
        {"role": "tool", "name": "grep", "content": "T2"},
        {"role": "tool", "name": "web_search", "content": "T3"},
        {"role": "tool", "name": "fetch_url", "content": "T4"},
        {"role": "tool", "name": "write_file", "content": "T5"},
        {"role": "tool", "name": "read_file", "content": "T6"},
        {"role": "tool", "name": "read_file", "content": "T7"},
    ]
    out = runner._build_minimal_finalize_messages(messages)

    roles = [m["role"] for m in out]
    assert roles[0] == "system", "首条必须是系统提示"
    assert out[0]["content"] == "S" * 6000, "系统提示应截断到 6000 字符"
    assert roles[1] == "user" and "schedule JSON" in out[1]["content"], "必须保留首条任务"
    assert "工具结果摘要" in out[2]["content"], "必须附工具结果摘要"
    # 只保留最近 5 条（T3..T7），T1/T2 不出现；每条截断 600 字符
    assert "[read_file] " + "T1" * 1000 not in out[2]["content"]
    assert "T7" in out[2]["content"] and "T3" in out[2]["content"]
    assert "T2" not in out[2]["content"], "更早的工具结果应被丢弃"
    print("  [√] 极简收尾消息结构：截断 + 任务 + 最近 5 条摘要")


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


# ── 4. [W7] 网络层重试：瞬时故障（URLError / 5xx）退避重试 ─────────────


def _ok_body():
    return json.dumps(
        {"choices": [{"message": {"content": "ok", "tool_calls": []}}]},
        ensure_ascii=False).encode("utf-8")


def _ok_resp():
    resp = MagicMock()
    resp.__enter__.return_value = resp
    resp.read.return_value = _ok_body()
    resp.headers = {}
    return resp


def test_call_llm_retries_network_errors(tmp_path, monkeypatch):
    """[W7] 网络类瞬时故障重试：前两次 URLError → 第三次成功（共 3 次尝试）。"""
    attempts = []

    def fake_urlopen(req, timeout=None):
        attempts.append(1)
        if len(attempts) < 3:
            raise urllib.error.URLError("模拟网络抖动")
        return _ok_resp()

    cfg = {"api_key": "sk-test-fake", "base_url": "https://api.test.com/v1",
           "model": "样本模型"}
    runner = AgentRunner(config=cfg, workspace_root=tmp_path,
                         permission_mode="auto", quiet=True)
    sleeps = []
    monkeypatch.setattr("tools.agent.loop.time.sleep", lambda s: sleeps.append(s))
    with patch("tools.agent.loop.safe_urlopen", side_effect=fake_urlopen):
        data = runner._call_llm([{"role": "user", "content": "hi"}])

    assert data is not None, "第三次尝试应成功"
    assert data["choices"][0]["message"]["content"] == "ok"
    assert len(attempts) == 3, f"应为 3 次尝试（1→2 重试），实际 {len(attempts)}"
    assert len(sleeps) == 2, "两次退避（0.5s / 1.5s 档 + 抖动）"
    print("  [√] 网络瞬时故障：2 次重试后成功")


def test_call_llm_network_errors_exhausted_returns_none(tmp_path, monkeypatch):
    """[W7] 网络持续失败 → 3 次尝试后如实返回 None（不伪造响应）。"""
    attempts = []

    def fake_urlopen(req, timeout=None):
        attempts.append(1)
        raise urllib.error.URLError("持续断网")

    cfg = {"api_key": "sk-test-fake", "base_url": "https://api.test.com/v1",
           "model": "样本模型"}
    runner = AgentRunner(config=cfg, workspace_root=tmp_path,
                         permission_mode="auto", quiet=True)
    monkeypatch.setattr("tools.agent.loop.time.sleep", lambda s: None)
    with patch("tools.agent.loop.safe_urlopen", side_effect=fake_urlopen):
        data = runner._call_llm([{"role": "user", "content": "hi"}])

    assert data is None
    assert len(attempts) == 3, f"应为 3 次尝试（max_retries=2），实际 {len(attempts)}"
    print("  [√] 网络持续失败：3 次尝试后如实返回 None")


def test_call_llm_retries_http_5xx(tmp_path, monkeypatch):
    """[W7] HTTP 5xx（网关抖动）退避重试；429 同理。"""
    attempts = []

    def fake_urlopen(req, timeout=None):
        attempts.append(1)
        if len(attempts) < 2:
            raise urllib.error.HTTPError(
                "https://api.test.com/v1/chat/completions", 503,
                "Service Unavailable", {},
                io.BytesIO(b'{"error": "unavailable"}'))
        return _ok_resp()

    cfg = {"api_key": "sk-test-fake", "base_url": "https://api.test.com/v1",
           "model": "样本模型"}
    runner = AgentRunner(config=cfg, workspace_root=tmp_path,
                         permission_mode="auto", quiet=True)
    monkeypatch.setattr("tools.agent.loop.time.sleep", lambda s: None)
    with patch("tools.agent.loop.safe_urlopen", side_effect=fake_urlopen):
        data = runner._call_llm([{"role": "user", "content": "hi"}])

    assert data is not None and data["choices"][0]["message"]["content"] == "ok"
    assert len(attempts) == 2, "503 一次后重试成功"
    print("  [√] HTTP 5xx：退避重试后成功")


def test_call_llm_http_4xx_not_retried(tmp_path, monkeypatch):
    """[W7] 4xx（除 429）不重试：400/401/403 属确定性错误，立即如实返回 None。"""
    attempts = []

    def fake_urlopen(req, timeout=None):
        attempts.append(1)
        raise urllib.error.HTTPError(
            "https://api.test.com/v1/chat/completions", 401,
            "Unauthorized", {}, io.BytesIO(b'{"error": "bad key"}'))

    cfg = {"api_key": "sk-test-fake", "base_url": "https://api.test.com/v1",
           "model": "样本模型"}
    runner = AgentRunner(config=cfg, workspace_root=tmp_path,
                         permission_mode="auto", quiet=True)
    monkeypatch.setattr("tools.agent.loop.time.sleep", lambda s: None)
    with patch("tools.agent.loop.safe_urlopen", side_effect=fake_urlopen):
        data = runner._call_llm([{"role": "user", "content": "hi"}])

    assert data is None
    assert len(attempts) == 1, "401 不得重试（确定性错误）"
    print("  [√] HTTP 401：不重试，如实返回 None")
