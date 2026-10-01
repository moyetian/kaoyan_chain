# -*- coding: utf-8 -*-
"""W1 埋点底座 —— 回归测试。

覆盖三件事（对应升级规划 W1：删伪 usage + session 补指标）：
1. gateway ``/v1/chat/completions`` 不再返回伪 usage（旧实现用字符数冒充 token）；
   另加源码级防回归断言（伪 usage 模式不得在任何位置复活）。
2. session 事件流新增 ``llm_call`` 事件：每次 LLM 调用的 latency_ms / usage /
   错误分类落盘；usage 只透传上游真值（缺失即 None，**绝不伪造**）。
3. 错误分类覆盖：network（重试后失败，attempts=2）/ http_<code> / too_large /
   invalid_response。

全程离线：mock 网络层（loop.safe_urlopen）与 gateway 的 LLM 调用，绝不联网。
"""

import io
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import tools.agent.loop as loop_module  # noqa: E402
from tools.agent.loop import AgentRunner  # noqa: E402
from tools.agent.session_log import SCHEMA_VERSION, load_events  # noqa: E402

#: 上游返回的真实 usage（mock 用；断言落盘逐字段一致）。
REAL_USAGE = {"prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168}


# ── mock 基础设施 ───────────────────────────────────────────────────────


class _FakeResponse:
    """模拟 urllib 响应对象（context manager + read(n) + headers.get）。"""

    def __init__(self, payload: dict):
        self._body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.headers = {}

    def read(self, n=-1):
        if n is None or n < 0:
            return self._body
        return self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeSSEResponse:
    """[W8] 模拟 urllib **流式**响应对象：按 read(n) 逐片吐 SSE 字节。

    生产代码默认发 ``stream: true``，故成功路径的 mock 走 SSE；``chunk_size``
    故意小于事件长度，顺带验证「SSE 行被 read 切断」也能拼回。
    """

    def __init__(self, body: bytes, chunk_size: int = 23):
        self._body = body
        self._pos = 0
        self._chunk = chunk_size
        self.headers = {"Content-Type": "text/event-stream"}

    def read(self, n=-1):
        if n is None or n < 0:
            piece, self._pos = self._body[self._pos:], len(self._body)
            return piece
        size = min(n, self._chunk)
        piece = self._body[self._pos:self._pos + size]
        self._pos += len(piece)
        return piece

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _sse_response(*chunks, finish_reason="stop", usage=None,
                  tool_calls=None) -> _FakeSSEResponse:
    """[W8] 构造 SSE 流式回包：content 分片 + 可选 tool_calls / usage。"""
    events = []
    for c in chunks:
        events.append({"choices": [{"index": 0, "delta": {"content": c},
                                    "finish_reason": None}]})
    if tool_calls:
        for i, tc in enumerate(tool_calls):
            events.append({"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": i, "id": tc["id"], "type": "function",
                 "function": {"name": tc["name"], "arguments": tc["arguments"]}}]},
                "finish_reason": None}]})
    events.append({"choices": [{"index": 0, "delta": {},
                                "finish_reason": finish_reason}]})
    if usage is not None:
        events.append({"choices": [], "usage": usage})
    lines = []
    for ev in events:
        lines.append("data: " + json.dumps(ev, ensure_ascii=False))
        lines.append("")
    lines.append("data: [DONE]")
    lines.append("")
    return _FakeSSEResponse(("\n".join(lines) + "\n").encode("utf-8"))


def _make_fake_urlopen(responses):
    """返回 (fake_safe_urlopen, calls)。

    ``responses`` 元素为 dict（成功响应，按调用序取，耗尽后沿用最后一项）、
    ``_FakeSSEResponse``（流式回包，原样返回）或 Exception 实例（抛出）。
    """
    calls = []

    def fake(req, timeout=None):
        calls.append({"timeout": timeout})
        idx = min(len(calls) - 1, len(responses) - 1)
        item = responses[idx]
        if isinstance(item, Exception):
            raise item
        if isinstance(item, _FakeSSEResponse):
            return item
        return _FakeResponse(item)

    return fake, calls


def _make_runner(tmp_path, max_steps=3):
    cfg = {"api_key": "sk-test-fake", "model": "样本模型", "active_subject": "pol"}
    return AgentRunner(config=cfg, workspace_root=tmp_path,
                       permission_mode="auto", quiet=True, max_steps=max_steps)


def _read_llm_calls(tmp_path):
    sessions = list((tmp_path / ".memory" / "sessions").glob("*.jsonl"))
    assert sessions, "会话日志未落盘"
    events = []
    for p in sessions:
        events.extend(load_events(p))
    return [e for e in events if e["type"] == "llm_call"]


def _text_reply(content):
    return {"choices": [{"message": {"content": content, "tool_calls": []}}]}


def _tool_reply(name="read_file", args=None, call_id="call_x"):
    return {"choices": [{"message": {
        "content": None,
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(args or {"path": "样本.txt"},
                                                 ensure_ascii=False)},
        }],
    }}]}


# ── 1. gateway：不再返回伪 usage ────────────────────────────────────────


def test_gateway_chat_completions_has_no_usage_key(monkeypatch):
    """行为级：POST /v1/chat/completions 的响应不含 usage 字段。"""
    import tools.cli.gateway as gateway_module

    monkeypatch.setattr(gateway_module, "query_llm_reply",
                        lambda msg, cfg: "网关回复样本")
    monkeypatch.setattr(gateway_module, "load_config", lambda: {})

    server = ThreadingHTTPServer(("127.0.0.1", 0),
                                 gateway_module.create_gateway_handler(token=""))
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = json.dumps({"messages": [{"role": "user", "content": "你好"}]},
                          ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=body, method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()

    assert "usage" not in data, f"网关仍在返回伪 usage: {data.get('usage')!r}"
    assert data["choices"][0]["message"]["content"] == "网关回复样本"
    assert data["choices"][0]["finish_reason"] == "stop"


def test_gateway_source_has_no_fake_usage_pattern():
    """源码级防回归：伪 usage 特征（字符数冒充 token）不得复活。

    只锚定「usage 字段名」——它们在 gateway.py 中的唯一合法用途就是伪 usage
    构造；注释里的文字描述（如"字符长度"）不算，避免断言脆化。
    """
    src = (ROOT / "tools" / "cli" / "gateway.py").read_text(encoding="utf-8")
    for field in ('"prompt_tokens"', '"completion_tokens"', '"total_tokens"'):
        assert field not in src, f"gateway 源码出现 {field}（伪 usage 复活？）"


# ── 2. session llm_call 事件：成功路径 ──────────────────────────────────


def test_llm_call_event_recorded_with_real_usage(tmp_path, monkeypatch):
    """成功调用（SSE 流式）→ llm_call 事件含真 usage / latency / model，schema v2。"""
    fake, calls = _make_fake_urlopen([
        _sse_response("最终答复", usage=REAL_USAGE),
    ])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)

    runner = _make_runner(tmp_path)
    answer = runner.run("你好", interactive=False)
    assert answer == "最终答复"
    assert len(calls) == 1

    llm_calls = _read_llm_calls(tmp_path)
    assert len(llm_calls) == 1, f"应恰有 1 条 llm_call，实际 {len(llm_calls)}"
    evt = llm_calls[0]
    payload = evt["payload"]
    assert evt["schema_version"] == SCHEMA_VERSION == 2
    assert payload["ok"] is True
    assert payload["error_kind"] is None
    assert payload["usage"] == REAL_USAGE, "usage 必须逐字段透传上游真值"
    assert isinstance(payload["latency_ms"], int) and payload["latency_ms"] >= 0
    assert payload["attempts"] == 1
    assert payload["model"] == "样本模型"
    assert payload["allow_tools"] is True


def test_usage_none_when_upstream_missing(tmp_path, monkeypatch):
    """上游流式回包无 usage → 落盘 None（容缺，绝不伪造）。"""
    fake, _ = _make_fake_urlopen([_sse_response("答复")])  # 无 usage 块
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)

    runner = _make_runner(tmp_path)
    assert runner.run("你好", interactive=False) == "答复"

    llm_calls = _read_llm_calls(tmp_path)
    assert len(llm_calls) == 1
    assert llm_calls[0]["payload"]["usage"] is None
    assert llm_calls[0]["payload"]["ok"] is True


def test_finalize_request_recorded_as_allow_tools_false(tmp_path, monkeypatch):
    """步数耗尽 → 收尾请求（allow_tools=False）同样落 llm_call 事件（均为 SSE 流）。"""
    fake, calls = _make_fake_urlopen([
        # 第 1 步：调工具（步数用尽）——tool_calls 以流式分片到达
        _sse_response(finish_reason="tool_calls", tool_calls=[{
            "id": "call_x", "name": "read_file",
            "arguments": json.dumps({"path": "样本.txt"}, ensure_ascii=False)}]),
        _sse_response("收尾答复", usage=REAL_USAGE),  # 收尾请求
    ])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)

    runner = _make_runner(tmp_path, max_steps=1)
    runner.tool_registry.execute_tool = lambda name, args, interactive=True, call_id="": "样本工具结果"
    answer = runner.run("请完成任务", interactive=False)
    assert answer == "收尾答复"

    llm_calls = _read_llm_calls(tmp_path)
    assert len(llm_calls) == 2, f"主请求 + 收尾请求 = 2 条，实际 {len(llm_calls)}"
    assert llm_calls[0]["payload"]["allow_tools"] is True
    assert llm_calls[1]["payload"]["allow_tools"] is False
    assert llm_calls[1]["payload"]["usage"] == REAL_USAGE


# ── 3. 错误分类 ─────────────────────────────────────────────────────────


def test_error_kind_network_after_retry(tmp_path, monkeypatch):
    """URLError → 重试 2 次仍失败 → error_kind=network、attempts=3。

    [W7 收尾强化] max_retries 1→2（共 3 次尝试）：长上下文单次请求可慢至
    100s+，双重超时后 api_failed 跳过收尾是空答案主因；现改为 api_failed
    仍走收尾兜底（极简 → 完整两档，也各 3 次尝试）。本测试全程网络失败，
    因此 9 次底层调用 = 主对话 3 + 极简收尾 3 + 完整收尾 3，事件 3 条。
    """
    fake, calls = _make_fake_urlopen([urllib.error.URLError("boom")])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)
    monkeypatch.setattr(loop_module.time, "sleep", lambda s: None)  # 跳过退避等待

    runner = _make_runner(tmp_path)
    answer = runner.run("你好", interactive=False)
    assert answer == ""            # 全部路径失败 → 空串
    assert len(calls) == 9, "主对话 3 次尝试 + 收尾兜底两档各 3 次（网络全失败）"

    llm_calls = _read_llm_calls(tmp_path)
    assert len(llm_calls) == 3     # 主对话 + 极简收尾 + 完整收尾
    payload = llm_calls[0]["payload"]
    assert payload["ok"] is False
    assert payload["error_kind"] == "network"
    assert payload["attempts"] == 3
    assert payload["usage"] is None
    assert payload["allow_tools"] is True
    # 后两条为收尾请求（禁用工具）
    assert [c["payload"]["allow_tools"] for c in llm_calls[1:]] == [False, False]


def test_error_kind_http_status(tmp_path, monkeypatch):
    """HTTP 500 属瞬时故障 → 退避重试（共 3 次尝试）→ error_kind=http_500、attempts=3。"""
    err = urllib.error.HTTPError("https://x/v1/chat/completions", 500,
                                 "boom", None, io.BytesIO(b"server error"))
    fake, calls = _make_fake_urlopen([err])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)
    monkeypatch.setattr(loop_module.time, "sleep", lambda s: None)  # 跳过退避等待

    runner = _make_runner(tmp_path)
    assert runner.run("你好", interactive=False) == ""
    assert len(calls) == 9, "5xx 重试后主对话 3 + 收尾兜底 3+3（全部 500）"

    llm_calls = _read_llm_calls(tmp_path)
    assert len(llm_calls) == 3
    payload = llm_calls[0]["payload"]
    assert payload["ok"] is False
    assert payload["error_kind"] == "http_500"
    assert payload["attempts"] == 3


def test_error_kind_too_large(tmp_path, monkeypatch):
    """解压后超体积上限 → error_kind=too_large。"""
    fake, _ = _make_fake_urlopen([_text_reply("答复")])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)
    monkeypatch.setattr(loop_module, "decompress_limited",
                        lambda raw, enc: (raw, True))

    runner = _make_runner(tmp_path)
    assert runner.run("你好", interactive=False) == ""

    payload = _read_llm_calls(tmp_path)[0]["payload"]
    assert payload["ok"] is False
    assert payload["error_kind"] == "too_large"


def test_error_kind_invalid_response_on_html(tmp_path, monkeypatch):
    """上游返回 HTML 而非 JSON → error_kind=invalid_response。"""
    class _HtmlResponse(_FakeResponse):
        def __init__(self):
            self._body = b"<!doctype html><html>SPA</html>"
            self.headers = {}

    def fake(req, timeout=None):
        return _HtmlResponse()

    monkeypatch.setattr(loop_module, "safe_urlopen", fake)

    runner = _make_runner(tmp_path)
    assert runner.run("你好", interactive=False) == ""

    payload = _read_llm_calls(tmp_path)[0]["payload"]
    assert payload["ok"] is False
    assert payload["error_kind"] == "invalid_response"


# ── 4. 兼容性：旧事件读取不受 v2 影响 ───────────────────────────────────


def test_old_v1_events_still_readable(tmp_path):
    """v1 旧事件（无 llm_call、schema_version=1）仍可加载，llm_call 为未知类型前兼容。"""
    sess_dir = tmp_path / ".memory" / "sessions"
    sess_dir.mkdir(parents=True, exist_ok=True)
    old_path = sess_dir / "20260101-000000-aaaaaa.jsonl"
    old_path.write_text(
        json.dumps({"id": "a1", "ts": "2026-01-01T00:00:00.000", "type": "user",
                    "parent": None, "payload": {"content": "旧事件"},
                    "schema_version": 1}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    events = load_events(old_path)
    assert len(events) == 1
    assert events[0]["schema_version"] == 1
