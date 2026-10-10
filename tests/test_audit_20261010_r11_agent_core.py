# -*- coding: utf-8 -*-
"""2026-10-10 Agent 核心域修复批次（A#1–A#11）回归测试。

覆盖 11 项已复核缺陷（每项：先红捕获 → 修复 → 绿）：

* A#1  pytest 闸门未覆盖收集路径**祖先链** conftest.py（tools_impl.run_command）
* A#2  loop 主循环 ``fn_args`` 非 dict 时 ``.items()`` 崩溃
* A#3  loop 主循环 ``{"choices": []}`` IndexError
* A#4  配置值 JSON null 崩溃（15 处同模式；代表点行为测试 + 源码扫描）
* A#5  mcp_client 构造期 ``s_conf`` 非 dict 崩溃（REPL/GUI 全部无法启动）
* A#6  llm_client 非法 temperature 致整个配置被丢弃（返回 {}）
* A#7  llm_client ``IncompleteRead`` 不在重试捕获列表（docstring 声称会重试）
* A#8  session_log user/assistant 正文静默截断 4000（破坏 resume 一致性契约）
* A#9  loop JSON 修复成功后截断标注丢失（如实标注被吞）
* A#10 context_engine 回溯归零时插入空摘要（消息没减少、误报压缩）
* A#11 recovery ``autosave_json_outputs`` 绕过沙箱（``../`` 穿越写出工作区外）

全程离线：LLM 一律打桩 / 注入，绝不联网；工作区一律 ``tmp_path``。
夹具禁止真实身份字面量（用「示例农业大学 / 618 示例科目」类占位）。
"""

import http.client
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tools.agent.loop as loop_module  # noqa: E402
import tools.output_budget as output_budget  # noqa: E402
import tools.study_planner as study_planner  # noqa: E402
from tools.agent import sandbox as sandbox_mod  # noqa: E402
from tools.agent import tools_impl as tools_impl_mod  # noqa: E402
from tools.agent.context_engine import ContextEngine  # noqa: E402
from tools.agent.loop import AgentRunner  # noqa: E402
from tools.agent.mcp_client import MCPClientManager  # noqa: E402
from tools.agent.permissions import PermissionManager  # noqa: E402
from tools.agent.recovery import autosave_json_outputs  # noqa: E402
from tools.agent.sandbox import Sandbox  # noqa: E402
from tools.agent.session_log import (  # noqa: E402
    EVENT_ASSISTANT,
    EVENT_COMPACT,
    EVENT_TOOL_RESULT,
    EVENT_USER,
    SessionLog,
    load_events,
    rebuild_history,
)
from tools.agent.tools_impl import ToolRegistry  # noqa: E402
from tools.llm_client import (  # noqa: E402
    ChatRequest,
    LLMRetryExhausted,
    chat_completion,
    get_llm_config,
    is_llm_configured,
    request_chat,
)
from tools.skills import vision_solver  # noqa: E402


# ══════════════════════════════════════════════════════════════════════════
# 公共设施
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _isolate_process_wide_pollution(monkeypatch):
    """污染集合是**进程级**全局：逐条用例换成全新集合，互不污染。"""
    monkeypatch.setattr(sandbox_mod, "_SESSION_WRITTEN_FILES", set())


@pytest.fixture
def ws(tmp_path):
    """独立工作区（含受控目录 tools/ 与 tests/，与真实仓库隔离）。"""
    root = tmp_path / "ws"
    (root / "tools").mkdir(parents=True)
    (root / "tests").mkdir(parents=True)
    return root


def _registry(ws, *, mode="auto", approval_channel=None):
    perm = PermissionManager(mode=mode, workspace_root=ws,
                             approval_channel=approval_channel)
    return ToolRegistry(Sandbox(workspace_root=ws), perm)


class _RecordingRun:
    """记录型 subprocess.run 替身：不真实执行，仅记录 argv。"""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


class _SubprocessProxy:
    """只覆盖 ``run`` 的 subprocess 代理（避免污染进程内其他调用方）。"""

    def __init__(self, real, run):
        self._real = real
        self.run = run

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture
def run_probe(monkeypatch):
    """安装记录型执行探针（仅替换 tools_impl 命名空间里的 subprocess）。"""
    p = _RecordingRun()
    monkeypatch.setattr(tools_impl_mod, "subprocess", _SubprocessProxy(subprocess, p))
    return p


def _make_runner(tmp_path, **overrides):
    cfg = {"api_key": "sk-test-fake", "model": "样本模型", "active_subject": "pol"}
    cfg.update(overrides)
    return AgentRunner(config=cfg, workspace_root=tmp_path,
                       permission_mode="auto", quiet=True)


def _script_llm(runner, responses):
    """把 runner._call_llm 替换为按序吐响应的脚本桩（用尽后沿用最后一项）。"""
    calls = []

    def fake(messages, allow_tools=True, tools_subset=None):
        calls.append({"messages": messages, "allow_tools": allow_tools})
        idx = min(len(calls) - 1, len(responses) - 1)
        return responses[idx]

    runner._call_llm = fake
    return calls


def _tool_call_response(fn_name, arguments):
    return {"choices": [{
        "message": {"content": None, "tool_calls": [{
            "id": "call_1", "type": "function",
            "function": {"name": fn_name, "arguments": arguments}}]},
        "finish_reason": "tool_calls"}]}


def _final_response(text):
    return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}


class _JsonResponse:
    """最小 JSON 回包替身（Content-Type: application/json → 整体读取路径）。"""

    def __init__(self, payload):
        self._raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.headers = {"Content-Type": "application/json"}

    def read(self, n=-1):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ══════════════════════════════════════════════════════════════════════════
# A#1 · pytest 闸门覆盖祖先链 conftest.py
# ══════════════════════════════════════════════════════════════════════════


def _plant_conftest(reg, ws):
    """在工作区根落一个 conftest.py 并登记为「本会话写入」。"""
    p = ws / "conftest.py"
    p.write_text("print('root conftest')\n", encoding="utf-8")
    reg.sandbox.register_written_file(p)
    return p


def _spy_approval(monkeypatch, reg):
    """记录 check_session_script_exec 的调用（委托真实实现）。"""
    calls = []
    orig = reg.permissions.check_session_script_exec

    def spy(script_display, tool_args, interactive=True):
        calls.append((script_display, dict(tool_args)))
        return orig(script_display, tool_args, interactive=interactive)

    monkeypatch.setattr(reg.permissions, "check_session_script_exec", spy)
    return calls


def test_a1_root_conftest_blocks_pytest_dir(ws, run_probe, monkeypatch):
    """核心现场：根 conftest.py 被本会话写入 + ``pytest tests/``。

    pytest 会自动加载收集路径祖先链上的 conftest.py（根 conftest 在
    ``tests/`` 之外、不在既有「目录子树」检查范围）→ 必须触发会话写入审批。
    """
    reg = _registry(ws)
    _plant_conftest(reg, ws)
    (ws / "tests" / "test_ok.py").write_text("print('ok')\n", encoding="utf-8")
    calls = _spy_approval(monkeypatch, reg)

    out = reg.execute_tool("run_command", {"command": "pytest tests"},
                           interactive=False)

    assert calls, "祖先链 conftest 未触发会话写入审批（闸门缺口）"
    assert "PermissionDenied" in out and "conftest" in out, out
    assert run_probe.calls == [], f"闸门被绕过，命令被执行: {run_probe.calls}"


def test_a1_root_conftest_blocks_pytest_file(ws, run_probe, monkeypatch):
    """文件参数分支同样覆盖：``pytest tests/test_x.py`` 也会加载根 conftest。"""
    reg = _registry(ws)
    _plant_conftest(reg, ws)
    (ws / "tests" / "test_x.py").write_text("print('x')\n", encoding="utf-8")
    calls = _spy_approval(monkeypatch, reg)

    out = reg.execute_tool("run_command",
                           {"command": "pytest tests/test_x.py"},
                           interactive=False)

    assert calls, "文件参数分支未覆盖祖先链 conftest"
    assert "PermissionDenied" in out and "conftest" in out, out
    assert run_probe.calls == []


def test_a1_non_conftest_session_py_does_not_extra_block(ws, run_probe):
    """阴性对照：会话写入的是 ``output/foo.py``（非 conftest、非收集子树内）
    → ``pytest tests/`` 不被额外拦截（闸门只针对 conftest 祖先链）。"""
    reg = _registry(ws)
    p = ws / "output" / "foo.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("print('foo')\n", encoding="utf-8")
    reg.sandbox.register_written_file(p)
    (ws / "tests" / "test_ok.py").write_text("print('ok')\n", encoding="utf-8")

    out = reg.execute_tool("run_command", {"command": "pytest tests"},
                           interactive=False)

    assert "PermissionDenied" not in out and "安全拦截" not in out, out
    assert len(run_probe.calls) == 1, "无关的会话写入 .py 不应阻断收集"


def test_a1_ancestor_conftest_still_blocks_with_node_id(ws, run_probe, monkeypatch):
    """node id 语法（``tests/test_x.py::test_evil``）同样命中祖先链闸门。"""
    reg = _registry(ws)
    _plant_conftest(reg, ws)
    (ws / "tests" / "test_x.py").write_text("def test_evil():\n    pass\n",
                                            encoding="utf-8")

    out = reg.execute_tool(
        "run_command",
        {"command": "pytest tests/test_x.py::test_evil"},
        interactive=False)

    assert "PermissionDenied" in out and "conftest" in out, out
    assert run_probe.calls == []


# ══════════════════════════════════════════════════════════════════════════
# A#2 / A#3 · loop 主循环防御
# ══════════════════════════════════════════════════════════════════════════


def test_a2_non_dict_tool_arguments_do_not_crash(tmp_path):
    """``json.loads("[1,2]")`` 成功但返回 list：状态行 ``fn_args.items()``
    此前直接 AttributeError 中断整个 run。"""
    runner = _make_runner(tmp_path)
    _script_llm(runner, [
        _tool_call_response("no_such_tool", "[1,2]"),
        _final_response("最终答案"),
    ])

    answer = runner.run("你好", interactive=False)

    assert answer == "最终答案", f"非 dict 参数不应中断 run: {answer!r}"


@pytest.mark.parametrize("empty_choices", [[], None])
def test_a3_empty_choices_do_not_crash(tmp_path, empty_choices):
    """``{"choices": []}``（键存在但空列表）/ ``{"choices": null}``：
    此前 IndexError / TypeError；现按空响应走既有恢复路径。"""
    runner = _make_runner(tmp_path)
    _script_llm(runner, [
        {"choices": empty_choices},
        _final_response("恢复后的答案"),
    ])

    answer = runner.run("你好", interactive=False)

    assert answer == "恢复后的答案", f"空 choices 不应崩溃: {answer!r}"


# ══════════════════════════════════════════════════════════════════════════
# A#4 · 配置值 null 崩溃（代表点 + 源码扫描）
# ══════════════════════════════════════════════════════════════════════════


def test_a4_is_llm_configured_null_values_returns_false():
    assert is_llm_configured({"api_key": None, "base_url": None,
                              "model": None}) is False


def test_a4_chat_completion_null_config_returns_none():
    assert chat_completion("示例提问", config={"api_key": None,
                                               "base_url": None,
                                               "model": None}) is None


def test_a4_loop_run_null_api_key_reports_missing(tmp_path):
    """loop.run 入口（:456）：api_key 为 JSON null → 报「未配置」而非崩溃。"""
    runner = _make_runner(tmp_path, api_key=None)

    out = runner.run("你好", interactive=False)

    assert "未配置" in out and "API Key" in out, out


def test_a4_loop_call_llm_null_api_key_does_not_crash(tmp_path, monkeypatch):
    """loop._call_llm（:1237）：null → 空串 key 继续走请求（不 AttributeError）。"""
    runner = _make_runner(tmp_path, api_key=None)
    monkeypatch.setattr(loop_module, "safe_urlopen",
                        lambda req, timeout=None: _JsonResponse(_final_response("示例回复")))

    data = runner._call_llm([{"role": "user", "content": "hi"}])

    assert data["choices"][0]["message"]["content"] == "示例回复"


def test_a4_loop_call_llm_without_tools_null_api_key_does_not_crash(tmp_path, monkeypatch):
    """降级路径（:1420）同修。"""
    runner = _make_runner(tmp_path, api_key=None)
    monkeypatch.setattr(loop_module, "safe_urlopen",
                        lambda req, timeout=None: _JsonResponse(_final_response("降级回复")))

    data = runner._call_llm_without_tools([{"role": "user", "content": "hi"}])

    assert data["choices"][0]["message"]["content"] == "降级回复"


def test_a4_stream_chat_null_api_key_returns_empty():
    import tools.cli.agent.engine as engine

    out = engine.stream_chat([{"role": "user", "content": "x"}],
                             {"api_key": None, "base_url": None, "model": None})
    assert out == ""


def test_a4_vision_solver_null_api_key_returns_hint():
    out = vision_solver.call_text_llm(
        [{"role": "user", "content": "x"}],
        {"api_key": None, "base_url": "https://api.test.com/v1", "model": "m"},
        stream=False)
    assert "尚未配置" in out


def test_a4_study_planner_null_api_key_returns_text(tmp_path, monkeypatch):
    cfg_file = tmp_path / "ky_config.json"
    cfg_file.write_text(json.dumps({"api_key": None, "model": None},
                                   ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(study_planner, "CONFIG_FILE", cfg_file)

    out = study_planner.run_ai_study_plan_generation(
        {"school": "示例农业大学", "math_key": "none"}, interactive=False)

    assert isinstance(out, str) and out.strip()


#: A#4 落点所在源文件（不含 school_scout.py —— 其同型第 16 处不在本批次范围）。
_A4_SOURCE_FILES = (
    "tools/agent/loop.py",
    "tools/cli/agent/engine.py",
    "tools/llm_client.py",
    "tools/study_planner.py",
    "tools/skills/vision_solver.py",
    "tools/doctor.py",
)


def test_a4_nullable_strip_pattern_removed_from_sources():
    """源码扫描：15 处 ``cfg.get(..., "").strip()`` 脆弱模式全部清除。

    null 时 ``.get(key, default)`` 的默认值**不生效** → 必须在源码层逐点收敛，
    本扫描把「其余靠 grep 复核」固化为可回归断言（覆盖 study_planner / doctor
    等无法轻量行为测试的落点）。
    """
    offenders = []
    for rel in _A4_SOURCE_FILES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for key in ("api_key", "base_url", "model"):
            needle = f'.get("{key}", "").strip()'
            if needle in text:
                offenders.append(f"{rel}: {needle}")
    assert not offenders, f"仍残留 null 崩溃模式: {offenders}"


# ══════════════════════════════════════════════════════════════════════════
# A#5 · mcp_client 构造期 s_conf 非 dict 崩溃
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("bad_conf", ["uvx foo", None, 123, ["uvx"]])
def test_a5_malformed_server_config_recorded_not_crash(tmp_path, bad_conf):
    mgr = MCPClientManager(workspace_root=tmp_path)

    mgr.load_from_config({"x": bad_conf}, start_timeout=1)

    assert "x" in mgr.failed, "格式错误的 server 必须在 failed 中留痕"
    assert "配置格式错误" in mgr.failed["x"]
    assert mgr.clients == {}


# ══════════════════════════════════════════════════════════════════════════
# A#6 · 非法 temperature 不得牵连整个配置
# ══════════════════════════════════════════════════════════════════════════


def _write_cfg(tmp_path, **kw):
    (tmp_path / "ky_config.json").write_text(
        json.dumps(kw, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize("bad_temp", ["abc", []])
def test_a6_invalid_temperature_keeps_other_fields(tmp_path, bad_temp):
    """temperature 非法 → 仅温度回落 0.3，api_key/base_url/model 必须仍在。"""
    _write_cfg(tmp_path, api_key="sk-test", base_url="https://api.test.com/v1",
               model="样本模型", temperature=bad_temp)

    cfg = get_llm_config(tmp_path)

    assert cfg.get("api_key") == "sk-test", f"整个配置被丢弃: {cfg!r}"
    assert cfg.get("base_url") == "https://api.test.com/v1"
    assert cfg.get("model") == "样本模型"
    assert cfg.get("temperature") == 0.3


def test_a6_valid_temperature_unchanged(tmp_path):
    _write_cfg(tmp_path, api_key="sk-test", base_url="https://api.test.com/v1",
               model="样本模型", temperature=0.5)

    cfg = get_llm_config(tmp_path)

    assert cfg.get("temperature") == 0.5


# ══════════════════════════════════════════════════════════════════════════
# A#7 · IncompleteRead 纳入网络类重试
# ══════════════════════════════════════════════════════════════════════════


def _sse_body(*events, done=True):
    lines = []
    for ev in events:
        lines.append("data: " + json.dumps(ev, ensure_ascii=False))
        lines.append("")
    if done:
        lines.append("data: [DONE]")
        lines.append("")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _delta(content):
    return {"choices": [{"index": 0, "delta": {"content": content},
                         "finish_reason": None}]}


def _finish(reason="stop"):
    return {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}


class _StreamResponse:
    """最小 SSE 响应替身：第 ``fail_after`` 次 read 起抛 ``exc``。"""

    def __init__(self, body, chunk_size=17, fail_after=None, exc=None):
        self._body = body
        self._pos = 0
        self.headers = {"Content-Type": "text/event-stream"}
        self._chunk_size = chunk_size
        self._fail_after = fail_after
        self._exc = exc
        self.reads = 0

    def read(self, n=-1):
        if self._fail_after is not None and self.reads >= self._fail_after:
            raise self._exc or http.client.IncompleteRead(b"partial")
        self.reads += 1
        if n is None or n < 0:
            piece = self._body[self._pos:]
            self._pos = len(self._body)
            return piece
        size = min(n, self._chunk_size)
        piece = self._body[self._pos:self._pos + size]
        self._pos += len(piece)
        return piece

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _chat_request(**kw):
    base = dict(messages=[{"role": "user", "content": "hi"}], model="样本模型",
                stream=True, api_key="sk-test", base_url="https://api.test.com/v1")
    base.update(kw)
    return ChatRequest(**base)


def test_a7_incomplete_read_retried_then_success():
    """流中途 IncompleteRead → 按网络类异常重试；第二次完整 → 成功。"""
    calls = []

    def stub(req, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            return _StreamResponse(_sse_body(_delta("半截")),
                                   chunk_size=8, fail_after=1,
                                   exc=http.client.IncompleteRead(b"partial"))
        return _StreamResponse(_sse_body(_delta("第二次成功"), _finish("stop")))

    data = request_chat(_chat_request(), max_retries=2,
                        sleep_fn=lambda s: None, urlopen_fn=stub)

    assert data["choices"][0]["message"]["content"] == "第二次成功"
    assert len(calls) == 2, f"应发生一次重试，实际请求 {len(calls)} 次"


def test_a7_incomplete_read_exhausted_is_network_kind():
    """持续 IncompleteRead → 重试耗尽抛 LLMRetryExhausted(kind=network)。"""
    calls = []

    def stub(req, timeout=None):
        calls.append(1)
        return _StreamResponse(_sse_body(_delta("半截")),
                               chunk_size=8, fail_after=1,
                               exc=http.client.IncompleteRead(b"partial"))

    with pytest.raises(LLMRetryExhausted) as ei:
        request_chat(_chat_request(), max_retries=1,
                     sleep_fn=lambda s: None, urlopen_fn=stub)

    assert ei.value.kind == "network"
    assert len(calls) == 2, "max_retries=1 → 共 2 次尝试"


# ══════════════════════════════════════════════════════════════════════════
# A#8 · session_log 正文截断分流
# ══════════════════════════════════════════════════════════════════════════


def test_a8_assistant_content_not_truncated_at_4000(tmp_path):
    log = SessionLog(workspace_root=tmp_path, session_id="a8-assistant")
    text = "答" * 4500
    log.append(EVENT_ASSISTANT, {"content": text})
    log.close()

    events = load_events(log.path)
    evt = [e for e in events if e["type"] == EVENT_ASSISTANT][0]
    assert evt["payload"]["content"] == text, "assistant 正文被静默截断"


def test_a8_user_content_not_truncated_at_4000(tmp_path):
    log = SessionLog(workspace_root=tmp_path, session_id="a8-user")
    text = "问" * 4500
    log.append(EVENT_USER, {"content": text})
    log.close()

    events = load_events(log.path)
    evt = [e for e in events if e["type"] == EVENT_USER][0]
    assert evt["payload"]["content"] == text


def test_a8_tool_result_still_capped_at_4000(tmp_path):
    """行为不变：tool_result 不参与 resume 重建，仍按 4000 截断。"""
    log = SessionLog(workspace_root=tmp_path, session_id="a8-tool")
    log.append(EVENT_TOOL_RESULT, {"tool_call_id": "c1", "name": "read_file",
                                   "content": "数" * 5000})
    log.close()

    events = load_events(log.path)
    evt = [e for e in events if e["type"] == EVENT_TOOL_RESULT][0]
    assert len(evt["payload"]["content"]) == 4000


def test_a8_rebuild_history_matches_live_content(tmp_path):
    """resume 重建契约：rebuild_history 的 assistant content 与原文逐字一致。"""
    log = SessionLog(workspace_root=tmp_path, session_id="a8-rebuild")
    answer = "解" * 4500
    log.append(EVENT_USER, {"content": "示例提问"})
    log.append(EVENT_ASSISTANT, {"content": answer})
    log.close()

    history = rebuild_history(load_events(log.path))

    assistant_msgs = [m for m in history if m.get("role") == "assistant"]
    assert assistant_msgs and assistant_msgs[-1]["content"] == answer


# ══════════════════════════════════════════════════════════════════════════
# A#9 · JSON 修复成功后截断标注保留
# ══════════════════════════════════════════════════════════════════════════


def test_a9_repair_keeps_truncation_notice(tmp_path):
    runner = _make_runner(tmp_path)
    notice = output_budget.truncation_notice("length", runner.config)
    assert notice, "前置条件：length 档位应产出截断标注"

    final_answer = '{"result": "半截' + notice
    seen = {}

    def fake_finalize(messages, instruction):
        seen["instruction"] = instruction
        return '{"result": "补全"}'

    runner._finalize_request = fake_finalize
    out = runner._repair_json_answer(final_answer, [])

    assert '{"result": "补全"}' in out, out
    assert "输出预算" in out and out.endswith("]"), \
        f"修复成功后截断标注丢失: {out!r}"
    assert "输出预算" not in seen.get("instruction", ""), \
        "修复请求不应把截断标注当作 JSON 正文发给模型"


def test_a9_repair_without_notice_unchanged(tmp_path):
    """阴性对照：无截断标注时行为不变（只返回修复后的 JSON 本体）。"""
    runner = _make_runner(tmp_path)
    runner._finalize_request = lambda messages, instruction: '{"a": 1}'

    out = runner._repair_json_answer('{"a": ', [])

    assert out == '{"a": 1}'


# ══════════════════════════════════════════════════════════════════════════
# A#10 · context_engine 回溯归零不插空摘要
# ══════════════════════════════════════════════════════════════════════════


def _make_engine(tmp_path):
    ce = ContextEngine(workspace_root=tmp_path, active_subject="math",
                       max_context_tokens=8000,
                       config={"agent": {"compact_mode": "rule_only"}})
    ce._force_python = True
    ce.tokenizer._enc = None
    return ce


def test_a10_cut_zero_returns_messages_unchanged(tmp_path):
    """system + 6 条消息（cut 恰归零）：压缩无意义 → 原样返回，不插空摘要。"""
    ce = _make_engine(tmp_path)
    messages = [
        {"role": "system", "content": "顶层协议：按考纲辅导。"},
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "第一答"},
        {"role": "user", "content": "第二问"},
        {"role": "assistant", "content": "第二答"},
        {"role": "user", "content": "第三问"},
        {"role": "assistant", "content": "第三答"},
    ]

    out = ce.compact_context(messages, force=True)

    assert out == messages, "cut 归零时不得插入空摘要消息"
    assert len(out) == len(messages)


def test_a10_normal_compaction_still_works(tmp_path):
    """阴性对照：有可压缩历史（cut>0）时压缩照常发生（消息数下降 + 摘要插入）。"""
    ce = _make_engine(tmp_path)
    messages = [{"role": "system", "content": "顶层协议：按考纲辅导。"}]
    for i in range(10):
        messages.append({"role": "user", "content": f"第{i}问：请讲考点"})
        messages.append({"role": "assistant", "content": f"第{i}答：考点解析"})

    out = ce.compact_context(messages, force=True)

    assert len(out) < len(messages), "正常压缩应减少消息数"
    assert any("Context Compaction" in str(m.get("content") or "")
               for m in out if m.get("role") == "system")


# ══════════════════════════════════════════════════════════════════════════
# A#11 · autosave_json_outputs 沙箱校验
# ══════════════════════════════════════════════════════════════════════════


def test_a11_path_escape_rejected(tmp_path):
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    answer = '{"result": "ok"}'

    saved = autosave_json_outputs(ws_root, ["output/../../evil.json"], answer)

    assert saved == [], f"越界路径不得写入: {saved}"
    assert not (tmp_path / "evil.json").exists(), "文件被写出了工作区外"


def test_a11_normal_path_still_written(tmp_path):
    ws_root = tmp_path / "ws"
    ws_root.mkdir()

    saved = autosave_json_outputs(ws_root, ["output/a.json"], '{"result": "ok"}')

    assert saved == ["output/a.json"]
    target = ws_root / "output" / "a.json"
    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8")) == {"result": "ok"}


# ══════════════════════════════════════════════════════════════════════════
# 第 2 轮 N1 · compact 摘要截断（resume 契约在压缩路径仍破）
# ══════════════════════════════════════════════════════════════════════════


def test_n1_compact_summary_not_truncated(tmp_path):
    """compact 的 summary 同样参与 rebuild_history（摘要置顶）——
    5000 字符摘要不得被 4000 截断（A#8 分流漏了 EVENT_COMPACT）。"""
    log = SessionLog(workspace_root=tmp_path, session_id="n1-compact")
    summary = "摘" * 5000
    log.append(EVENT_COMPACT, {"summary": summary, "before_messages": 20,
                               "after_messages": 2})
    log.close()

    events = load_events(log.path)
    evt = [e for e in events if e["type"] == EVENT_COMPACT][0]
    assert evt["payload"]["summary"] == summary, "compact 摘要被静默截断"


def test_n1_rebuild_history_summary_matches_source(tmp_path):
    """resume 契约：重建出的摘要消息与实时摘要逐字一致。"""
    log = SessionLog(workspace_root=tmp_path, session_id="n1-rebuild")
    summary = "摘" * 5000
    log.append(EVENT_COMPACT, {"summary": summary, "before_messages": 20,
                               "after_messages": 2})
    log.append(EVENT_USER, {"content": "示例提问"})
    log.close()

    history = rebuild_history(load_events(log.path))

    assert history[0]["role"] == "system"
    assert history[0]["content"] == summary, "resume 摘要与实时上下文不一致"


# ══════════════════════════════════════════════════════════════════════════
# 第 2 轮 N2 · school_scout base_url/model 的 JSON null 防护
# ══════════════════════════════════════════════════════════════════════════


def test_n2_school_scout_null_base_url_model_no_crash(monkeypatch):
    """cfg 含 JSON null（ky_cli.load_config 原样保留 null）时：
    base_url/model 不得 None.rstrip 崩溃 —— 回退默认值并正常调用。"""
    from tools.skills import school_scout

    seen = {}

    def fake_chat(messages, **kwargs):
        seen.update(kwargs.get("config") or {})
        return "示例研报正文"

    monkeypatch.setattr(school_scout, "chat_completion", fake_chat)

    out = school_scout.synthesize_report_with_llm(
        "示例农业大学", "618 示例科目", [], {},
        {"api_key": "sk-test-fake", "base_url": None, "model": None})

    assert out == "示例研报正文"
    assert seen.get("base_url") == "https://api.deepseek.com/v1"
    assert seen.get("model") == "deepseek-chat"


# ══════════════════════════════════════════════════════════════════════════
# 第 2 轮 N3+N5 · conftest 闸门：同场景单卡（去重）与白名单顺序
# ══════════════════════════════════════════════════════════════════════════


class _ApprovingChannel:
    """批准一切的假审批通道（记录卡片请求数，用于断言「只弹一次卡」）。"""

    def __init__(self):
        self.is_interactive = True
        self.session_allowed_tools = set()
        self.requests = []

    def request(self, tool_name, level, tool_args):
        self.requests.append((tool_name, dict(tool_args)))
        return True, "测试批准单次执行"

    def request_plan(self, tool_name, level, tool_args):
        return self.request(tool_name, level, tool_args)


def test_n3_root_conftest_dir_single_card(ws, run_probe):
    """场景①：根 conftest + ``pytest tests/``（tests/ 内无会话 py）
    → 弹卡一次，批准后执行。"""
    ch = _ApprovingChannel()
    reg = _registry(ws, approval_channel=ch)
    _plant_conftest(reg, ws)
    (ws / "tests" / "test_ok.py").write_text("print('ok')\n", encoding="utf-8")

    out = reg.execute_tool("run_command", {"command": "pytest tests"},
                           interactive=False)

    assert len(ch.requests) == 1, f"应只弹一次审批卡，实际 {len(ch.requests)} 次"
    assert len(run_probe.calls) == 1, out


def test_n3_root_conftest_with_dir_session_py_single_card(ws, run_probe):
    """场景②：根 conftest + tests/ 内有会话写入 py —— 祖先链闸门与
    目录检查不得各弹一次（此前 approval_calls=2）。"""
    ch = _ApprovingChannel()
    reg = _registry(ws, approval_channel=ch)
    _plant_conftest(reg, ws)
    tp = ws / "tests" / "test_ok.py"
    tp.write_text("print('ok')\n", encoding="utf-8")
    reg.sandbox.register_written_file(tp)

    out = reg.execute_tool("run_command", {"command": "pytest tests"},
                           interactive=False)

    assert len(ch.requests) == 1, f"同场景双弹卡，实际 {len(ch.requests)} 次"
    assert len(run_probe.calls) == 1, out


def test_n3_pytest_conftest_file_single_card(ws, run_probe):
    """场景③：``pytest tests/conftest.py``（文件参数=会话写入的 conftest
    自身）—— 祖先链闸门与文件闸门不得各弹一次（此前 approval_calls=2）。"""
    ch = _ApprovingChannel()
    reg = _registry(ws, approval_channel=ch)
    cp = ws / "tests" / "conftest.py"
    cp.write_text("print('tests conftest')\n", encoding="utf-8")
    reg.sandbox.register_written_file(cp)

    out = reg.execute_tool("run_command", {"command": "pytest tests/conftest.py"},
                           interactive=False)

    assert len(ch.requests) == 1, f"同场景双弹卡，实际 {len(ch.requests)} 次"
    assert len(run_probe.calls) == 1, out


def test_n3_non_conftest_scenario_unchanged(ws, run_probe):
    """场景④：非 conftest 场景行为不变 —— 会话写入 output/foo.py
    不影响 ``pytest tests/``（不弹卡、正常执行）。"""
    ch = _ApprovingChannel()
    reg = _registry(ws, approval_channel=ch)
    p = ws / "output" / "foo.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("print('foo')\n", encoding="utf-8")
    reg.sandbox.register_written_file(p)
    (ws / "tests" / "test_ok.py").write_text("print('ok')\n", encoding="utf-8")

    out = reg.execute_tool("run_command", {"command": "pytest tests"},
                           interactive=False)

    assert ch.requests == [], f"无关会话写入不应弹卡: {ch.requests}"
    assert len(run_probe.calls) == 1, out


def test_n5_whitelist_reject_no_wasted_approval(ws, run_probe):
    """N5：注定被白名单拒的目录（01-数学/）不得先弹一张白批准的卡 ——
    白名单检查在前，approval_calls 必须为 0 且返回安全拦截。"""
    ch = _ApprovingChannel()
    reg = _registry(ws, approval_channel=ch)
    _plant_conftest(reg, ws)
    (ws / "01-数学").mkdir()

    out = reg.execute_tool("run_command", {"command": "pytest 01-数学/"},
                           interactive=False)

    assert ch.requests == [], f"注定被拒的路径先弹了卡: {ch.requests}"
    assert "安全拦截" in out, out
    assert run_probe.calls == []
