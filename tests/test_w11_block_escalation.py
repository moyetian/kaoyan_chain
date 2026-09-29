# -*- coding: utf-8 -*-
"""[W11 拦截引导升级] 同类拦截连续计数 → 文案升级为「停止试探」警告。

背景（KaoYanBench core50 w10 实测 + 多角色实测）
----------------------------------------------
W10 已在工具层拦截「引擎直抓」并在 loop 层每次被拦都追加 web_search 引导
（见 test_w10_search_guard.py）。但 w10 全量实测暴露两个残余：
① PLAN-003：模型连续 4 种变体试探命令合并脚本（cd / output 脚本 / 自写脚本 /
   python -c），全部被 run_command 白名单拦截，烧掉约 3 分钟直至任务超时 ——
   单条重复文案对 flash 级模型惯性无效；
② UNI-003：模型收到 nudge 后仍继续猜 URL / 自写脚本，全程 0 次 web_search。
故新增「连续同类拦截计数」：安全拦截（run_command）与引擎直抓拦截各自计数，
连续达到阈值（_BLOCK_ESCALATE_THRESHOLD = 3）后注入「停止试探」级升级警告；
成功执行同类工具即重置（「连续」语义，而非历史累计）。

全程离线：mock _call_llm 与 execute_tool，不发起网络请求。
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))


#: 复刻 fetch_url 工具层拦截文案的关键前缀（真实文案见 tools_impl.py）。
_BLOCKED_FETCH = (
    "Error: 已拦截搜索引擎直抓（bing.com）。本环境已禁用搜索引擎直接抓取——"
    "更换其他搜索引擎重试同样会被拦截。检索信息的正确方式是 **web_search 工具**，"
    "请立即改用。"
)

#: 复刻 run_command 白名单拦截文案的关键前缀（真实文案见 tools_impl.py）。
_BLOCKED_CMD = (
    "安全拦截：命令 `cd` 不在白名单内。允许：cat, git, grep, head, ls, python, "
    "pytest, tail, wc。如需执行其他命令，请在宿主机终端手动运行。"
    "【替代路径】读取/查看文件内容请直接用 read_file 工具。"
)


def _text_reply(content):
    return {"choices": [{"message": {"content": content, "tool_calls": []}}]}


def _tool_reply(name, args, call_id):
    return {"choices": [{"message": {
        "content": "",
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(args, ensure_ascii=False)},
        }],
    }}]}


def _make_runner(tmp_path, exec_results, max_steps=8):
    """构造离线 AgentRunner；execute_tool 按「工具名 → 结果列表」动态返回。

    ``exec_results``：dict[str, list[str]]，同名工具第 N 次调用取第 N 项
    （超出取末项）；未配置的工具返回 "ok"。
    """
    from tools.agent.loop import AgentRunner
    cfg = {"api_key": "sk-test-fake", "model": "样本模型", "active_subject": "pol"}
    runner = AgentRunner(config=cfg, workspace_root=tmp_path,
                         permission_mode="auto", quiet=True, max_steps=max_steps)
    counters = {}

    def fake_exec(name, args, interactive=True):
        seq = exec_results.get(name)
        if not seq:
            return "ok"
        i = counters.get(name, 0)
        counters[name] = i + 1
        return seq[min(i, len(seq) - 1)]

    runner.tool_registry.execute_tool = fake_exec
    return runner


def _install_scripted_llm(monkeypatch, scripted):
    from tools.agent.loop import AgentRunner
    calls = []

    def fake_call_llm(self, messages, allow_tools=True, tools_subset=None):
        calls.append({"messages": list(messages), "allow_tools": allow_tools})
        idx = min(len(calls) - 1, len(scripted) - 1)
        return scripted[idx]

    monkeypatch.setattr(AgentRunner, "_call_llm", fake_call_llm)
    return calls


def _load_events(tmp_path):
    from tools.agent.session_log import load_events
    events = []
    for p in (tmp_path / ".memory" / "sessions").glob("*.jsonl"):
        events.extend(load_events(p))
    return events


def _guard_kinds(tmp_path):
    """事件流中的引导类事件（按写入顺序）。"""
    return [e["payload"]["kind"] for e in _load_events(tmp_path)
            if e.get("payload", {}).get("kind") in (
                "search_guard_nudge", "search_guard_escalation",
                "safety_guard_escalation")]


# ── 1. search 类：引擎直抓拦截的计数与升级 ─────────────────────────────


def test_search_escalation_after_three_blocks(tmp_path, monkeypatch):
    """连续 3 次引擎直抓被拦 → 前 2 次基础 nudge、第 3 次升级警告。"""
    _install_scripted_llm(monkeypatch, [
        _tool_reply("fetch_url", {"url": "https://www.bing.com/search?q=a"}, "c1"),
        _tool_reply("fetch_url", {"url": "https://www.baidu.com/s?wd=b"}, "c2"),
        _tool_reply("fetch_url", {"url": "https://duckduckgo.com/html/?q=c"}, "c3"),
        _text_reply("最终答复"),
    ])
    runner = _make_runner(tmp_path, {"fetch_url": [_BLOCKED_FETCH]})

    out = runner.run("检索任务", interactive=False)

    assert out == "最终答复"
    assert _guard_kinds(tmp_path) == [
        "search_guard_nudge", "search_guard_nudge", "search_guard_escalation"]


def test_search_escalation_message_contains_stop_signal(tmp_path, monkeypatch):
    """升级文案含「已连续 N 次」「彻底禁用」等停止试探信号（进入对话流）。"""
    calls = _install_scripted_llm(monkeypatch, [
        _tool_reply("fetch_url", {"url": "https://www.bing.com/search?q=a"}, "c1"),
        _tool_reply("fetch_url", {"url": "https://www.baidu.com/s?wd=b"}, "c2"),
        _tool_reply("fetch_url", {"url": "https://www.so.com/s?q=c"}, "c3"),
        _text_reply("最终答复"),
    ])
    runner = _make_runner(tmp_path, {"fetch_url": [_BLOCKED_FETCH]})

    runner.run("检索任务", interactive=False)

    # 第 4 次 LLM 调用（终答轮）的消息流里应能看到升级文案
    final_msgs = calls[3]["messages"]
    esc = [m for m in final_msgs if m.get("role") == "user"
           and "已连续 3 次" in str(m.get("content"))]
    assert esc, "升级文案必须进入对话流"
    assert "彻底禁用" in esc[0]["content"]
    assert "web_search" in esc[0]["content"]


def test_search_streak_resets_on_success(tmp_path, monkeypatch):
    """拦 2 次 → 成功 1 次（重置）→ 再拦 2 次 → 仍不升级（共 4 条基础 nudge）。"""
    _install_scripted_llm(monkeypatch, [
        _tool_reply("fetch_url", {"url": "https://www.bing.com/search?q=a"}, "c1"),
        _tool_reply("fetch_url", {"url": "https://www.baidu.com/s?wd=b"}, "c2"),
        _tool_reply("fetch_url", {"url": "https://yz.chsi.com.cn/kyzx/"}, "c3"),
        _tool_reply("fetch_url", {"url": "https://www.so.com/s?q=d"}, "c4"),
        _tool_reply("fetch_url", {"url": "https://search.brave.com/search?q=e"}, "c5"),
        _text_reply("最终答复"),
    ])
    runner = _make_runner(tmp_path, {"fetch_url": [
        _BLOCKED_FETCH, _BLOCKED_FETCH, "页面正文：招生简章……",
        _BLOCKED_FETCH, _BLOCKED_FETCH]})

    out = runner.run("检索任务", interactive=False)

    assert out == "最终答复"
    assert _guard_kinds(tmp_path) == [
        "search_guard_nudge", "search_guard_nudge",
        "search_guard_nudge", "search_guard_nudge"]


# ── 2. safety 类：run_command 安全拦截的计数与升级 ─────────────────────


def test_safety_escalation_after_three_blocks(tmp_path, monkeypatch):
    """连续 3 次命令安全拦截 → 前 2 次不注入（拦截文案已含替代路径）、第 3 次升级。"""
    _install_scripted_llm(monkeypatch, [
        _tool_reply("run_command", {"command": "cd output"}, "c1"),
        _tool_reply("run_command", {"command": "python merge.py"}, "c2"),
        _tool_reply("run_command", {"command": "python -c \"x\""}, "c3"),
        _text_reply("最终答复"),
    ])
    runner = _make_runner(tmp_path, {"run_command": [_BLOCKED_CMD]})

    out = runner.run("合并产物", interactive=False)

    assert out == "最终答复"
    assert _guard_kinds(tmp_path) == ["safety_guard_escalation"]


def test_safety_escalation_message_contains_stop_signal(tmp_path, monkeypatch):
    """安全升级文案含停止试探信号 + 内置工具替代路径。"""
    calls = _install_scripted_llm(monkeypatch, [
        _tool_reply("run_command", {"command": "cd output"}, "c1"),
        _tool_reply("run_command", {"command": "python merge.py"}, "c2"),
        _tool_reply("run_command", {"command": "python -c \"x\""}, "c3"),
        _text_reply("最终答复"),
    ])
    runner = _make_runner(tmp_path, {"run_command": [_BLOCKED_CMD]})

    runner.run("合并产物", interactive=False)

    final_msgs = calls[3]["messages"]
    esc = [m for m in final_msgs if m.get("role") == "user"
           and "安全拦截" in str(m.get("content"))
           and "已连续 3 次" in str(m.get("content"))]
    assert esc, "升级文案必须进入对话流"
    assert "write_file" in esc[0]["content"]
    assert "read_file" in esc[0]["content"]


def test_safety_streak_resets_on_success(tmp_path, monkeypatch):
    """拦 2 次 → run_command 成功 1 次（重置）→ 再拦 2 次 → 不升级。"""
    _install_scripted_llm(monkeypatch, [
        _tool_reply("run_command", {"command": "cd output"}, "c1"),
        _tool_reply("run_command", {"command": "python merge.py"}, "c2"),
        _tool_reply("run_command", {"command": "git status"}, "c3"),
        _tool_reply("run_command", {"command": "python merge.py"}, "c4"),
        _tool_reply("run_command", {"command": "python -c \"x\""}, "c5"),
        _text_reply("最终答复"),
    ])
    runner = _make_runner(tmp_path, {"run_command": [
        _BLOCKED_CMD, _BLOCKED_CMD, "On branch main",
        _BLOCKED_CMD, _BLOCKED_CMD]})

    out = runner.run("合并产物", interactive=False)

    assert out == "最终答复"
    assert _guard_kinds(tmp_path) == []


def test_safety_not_triggered_for_other_tools(tmp_path, monkeypatch):
    """其他工具结果即便含「安全拦截：」字样也不计数（仅限 run_command）。"""
    _install_scripted_llm(monkeypatch, [
        _tool_reply("read_file", {"path": "样本日志.txt"}, "c1"),
        _tool_reply("read_file", {"path": "样本日志2.txt"}, "c2"),
        _tool_reply("read_file", {"path": "样本日志3.txt"}, "c3"),
        _text_reply("最终答复"),
    ])
    runner = _make_runner(tmp_path, {"read_file": [_BLOCKED_CMD]})

    out = runner.run("读日志", interactive=False)

    assert out == "最终答复"
    assert _guard_kinds(tmp_path) == []


# ── 3. 两类计数相互独立 ────────────────────────────────────────────────


def test_streaks_independent_between_kinds(tmp_path, monkeypatch):
    """fetch 拦 2 + run_command 拦 2 交错 → 各自未达阈值、无升级；再各 1 次即升级。"""
    _install_scripted_llm(monkeypatch, [
        _tool_reply("fetch_url", {"url": "https://www.bing.com/search?q=a"}, "c1"),
        _tool_reply("run_command", {"command": "cd output"}, "c2"),
        _tool_reply("fetch_url", {"url": "https://www.baidu.com/s?wd=b"}, "c3"),
        _tool_reply("run_command", {"command": "python merge.py"}, "c4"),
        _tool_reply("fetch_url", {"url": "https://www.so.com/s?q=c"}, "c5"),
        _tool_reply("run_command", {"command": "python -c \"x\""}, "c6"),
        _text_reply("最终答复"),
    ])
    runner = _make_runner(tmp_path, {
        "fetch_url": [_BLOCKED_FETCH],
        "run_command": [_BLOCKED_CMD],
    })

    out = runner.run("混合任务", interactive=False)

    assert out == "最终答复"
    kinds = _guard_kinds(tmp_path)
    # search 1、2 → nudge；safety 3 → escalation；search 3 → escalation
    assert kinds.count("search_guard_nudge") == 2
    assert kinds.count("search_guard_escalation") == 1
    assert kinds.count("safety_guard_escalation") == 1


def test_escalation_keeps_firing_after_threshold(tmp_path, monkeypatch):
    """超过阈值后继续被拦 → 仍注入升级文案（streak 数值更新，信号不消失）。"""
    _install_scripted_llm(monkeypatch, [
        _tool_reply("run_command", {"command": "cd a"}, "c1"),
        _tool_reply("run_command", {"command": "cd b"}, "c2"),
        _tool_reply("run_command", {"command": "cd c"}, "c3"),
        _tool_reply("run_command", {"command": "cd d"}, "c4"),
        _text_reply("最终答复"),
    ])
    runner = _make_runner(tmp_path, {"run_command": [_BLOCKED_CMD]})

    runner.run("合并产物", interactive=False)

    assert _guard_kinds(tmp_path) == [
        "safety_guard_escalation", "safety_guard_escalation"]
