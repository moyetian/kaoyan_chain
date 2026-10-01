# -*- coding: utf-8 -*-
"""[W8] 流式（SSE）LLM 客户端 —— 回归测试。

背景（实测根因）
----------------
网关（kuaipao.ai，OpenAI 兼容）对**非流式**请求有 ~60s 硬超时：探针强制长输出
（max_tokens=3000）在 60.5s 被 RemoteDisconnected 掐断；同内容改流式可完整跑满
96.8s（首块 3.5s、2460 个 SSE 块、5435 字符）。core50 评测中 12/50 题因此产出
空答案（30 次 network 失败、每次 ≈61s）。故 ``_call_llm`` /
``_call_llm_without_tools`` 默认改走 ``stream: true``，逐块累积重建出与非流式
**同形**的 ``choices[0].message`` 结构。

本文件覆盖
----------
1. SSE content 分片累积；
2. tool_calls 按 index 分片累积（arguments 拼接后可 ``json.loads``）；
3. ``finish_reason`` 透传（stop / length / tool_calls）；
4. Content-Type 非 event-stream 时按 JSON 回退解析（老代理行为）；
5. 流中途断连 / 读超时 → 抛错并被既有重试逻辑捕获（次数与最终结果可断言）；
6. ``stream_options`` 被拒（400）时自动摘字段重发，不阻断整个调用；
7. 超时语义：块间停滞上限 90s（不是总时长上限），健康慢流可超过 request_timeout。

全程离线：mock ``loop.safe_urlopen``，绝不联网。
"""

import http.client
import http.server
import io
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import tools.agent.loop as loop_module  # noqa: E402
from tools.agent.loop import AgentRunner, _STREAM_STALL_TIMEOUT  # noqa: E402
from tools.agent.session_log import load_events  # noqa: E402


# ── 基础设施：SSE 响应体构造 + 流式假响应 ───────────────────────────────


def sse_body(*events, done=True) -> bytes:
    """把若干 dict（或原始字符串行）拼成 SSE 响应体。"""
    lines = []
    for ev in events:
        if isinstance(ev, dict):
            lines.append("data: " + json.dumps(ev, ensure_ascii=False))
        else:
            lines.append(str(ev))
        lines.append("")          # SSE 事件分隔空行
    if done:
        lines.append("data: [DONE]")
        lines.append("")
    return ("\n".join(lines) + "\n").encode("utf-8")


def delta(content=None, **extra):
    d = {"content": content} if content is not None else {}
    d.update(extra)
    return {"choices": [{"index": 0, "delta": d, "finish_reason": None}]}


def finish(reason):
    return {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}


def tool_delta(index, *, call_id=None, name=None, args=None):
    fn = {}
    if name is not None:
        fn["name"] = name
    if args is not None:
        fn["arguments"] = args
    tc = {"index": index, "function": fn}
    if call_id is not None:
        tc["id"] = call_id
        tc["type"] = "function"
    return {"choices": [{"index": 0, "delta": {"tool_calls": [tc]},
                         "finish_reason": None}]}


class FakeStreamResponse:
    """模拟 urllib 流式响应：按 ``read(n)`` 逐片吐字节。

    * ``chunk_size`` 故意小于事件长度，用于验证「SSE 行被 read 切断」也能拼回；
    * ``fail_after`` 次 read 之后抛 ``exc``（默认 RemoteDisconnected），
      模拟流中途被网关掐断；
    * ``delay`` 每次 read 前睡一下，用于「健康慢流总时长 > request_timeout」用例。
    """

    def __init__(self, body: bytes, content_type: str = "text/event-stream",
                 chunk_size: int = 17, fail_after=None, exc=None, delay: float = 0.0):
        self._body = body
        self._pos = 0
        self.headers = {"Content-Type": content_type}
        self._chunk_size = chunk_size
        self._fail_after = fail_after
        self._exc = exc
        self._delay = delay
        self.reads = 0

    def read(self, n=-1):
        if self._fail_after is not None and self.reads >= self._fail_after:
            raise self._exc or http.client.RemoteDisconnected("远端断开 (流中途)")
        if self._delay:
            time.sleep(self._delay)
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


class JsonResponse(FakeStreamResponse):
    """普通 JSON 回包（Content-Type: application/json）。

    ``chunk_size`` 取大值以复刻真实 ``HTTPResponse.read(n)`` 语义：攒满 n 字节
    或 EOF 才返回（本机流式服务端实测），故非流式回退路径一次 read 即拿全量。
    """

    def __init__(self, payload: dict, content_type: str = "application/json"):
        super().__init__(json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                         content_type=content_type, chunk_size=10 ** 9)


def _make_runner(tmp_path, **kw):
    cfg = {"api_key": "sk-test-fake", "model": "样本模型", "active_subject": "pol"}
    return AgentRunner(config=cfg, workspace_root=tmp_path,
                       permission_mode="auto", quiet=True, **kw)


def _make_urlopen(responses):
    """返回 (fake, calls)；responses 元素为响应对象或 Exception。"""
    calls = []

    def fake(req, timeout=None):
        calls.append({"timeout": timeout, "body": json.loads(req.data.decode("utf-8"))})
        idx = min(len(calls) - 1, len(responses) - 1)
        item = responses[idx]
        if isinstance(item, Exception):
            raise item
        return item

    return fake, calls


def _io_bytes(data: bytes):
    return io.BytesIO(data)


# ── 真机端到端：本机 HTTP 服务端（真实 socket + 真实 HTTPResponse）────────


class _SSEHandler(http.server.BaseHTTPRequestHandler):
    """按 SSE/chunked 逐块吐事件的极小服务端（供真机端到端用例使用）。"""

    protocol_version = "HTTP/1.1"
    events: list = []
    delay = 0.02
    stall_after = None
    stall_seconds = 0.0

    def log_message(self, *args):      # 静音
        pass

    def _write(self, data: bytes) -> bool:
        try:
            self.wfile.write(data)
            self.wfile.flush()
            return True
        except Exception:               # 客户端已断开（停滞用例）→ 静默收工
            return False

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for i, ev in enumerate(self.events):
            if self.stall_after is not None and i == self.stall_after:
                time.sleep(self.stall_seconds)
            if not self._write(b"%x\r\n" % len(ev) + ev + b"\r\n"):
                return
            time.sleep(self.delay)
        self._write(b"0\r\n\r\n")


def _serve_sse(events, *, delay=0.02, stall_after=None, stall_seconds=0.0):
    handler = type("_H", (_SSEHandler,),
                   {"events": list(events), "delay": delay,
                    "stall_after": stall_after, "stall_seconds": stall_seconds})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _real_urlopen(monkeypatch):
    """用真 urllib 替换 safe_urlopen（本机回环被 net_guard 拦，属测试夹具需要）。"""
    monkeypatch.setattr(loop_module, "safe_urlopen", urllib.request.urlopen)


class _TimeProxy:
    """只把 ``loop_module.time.sleep`` 变 no-op（跳过退避），其余委托真 time。

    注意：不能直接 ``monkeypatch.setattr(loop_module.time, "sleep", ...)`` ——
    那会连**同进程内服务端线程**的 sleep 一起打掉（本文件有真机服务端用例）。
    """

    def __init__(self, real):
        self._real = real

    def sleep(self, _seconds):
        return None

    def __getattr__(self, name):
        return getattr(self._real, name)


def _no_backoff(monkeypatch):
    monkeypatch.setattr(loop_module, "time", _TimeProxy(time))


def test_real_socket_sse_stream_parsed(tmp_path, monkeypatch):
    """真机：本机服务端逐块吐 SSE（chunked）→ 完整解析出 content/tool_calls。"""
    events = [
        b'data: {"choices":[{"index":0,"delta":{"content":"\xe8\x80\x83\xe7\xa0\x94"},"finish_reason":null}]}\n\n',
        b'data: {"choices":[{"index":0,"delta":{"content":"\xe6\x94\xbf\xe6\xb2\xbb"},"finish_reason":null}]}\n\n',
        b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"c1","type":"function","function":{"name":"read_file","arguments":"{\\"path\\":"}}]},"finish_reason":null}]}\n\n',
        b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":" \\"a.txt\\"}"}}]},"finish_reason":null}]}\n\n',
        b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n\n',
        b'data: [DONE]\n\n',
    ]
    server = _serve_sse(events)
    try:
        _real_urlopen(monkeypatch)
        runner = _make_runner(tmp_path, request_timeout=10)
        runner.config["base_url"] = f"http://127.0.0.1:{server.server_address[1]}"
        data = runner._call_llm([{"role": "user", "content": "x"}])
    finally:
        server.shutdown()
        server.server_close()

    msg = data["choices"][0]["message"]
    assert msg["content"] == "考研政治"
    assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"path": "a.txt"}
    assert data["choices"][0]["finish_reason"] == "tool_calls"


def test_real_socket_stalled_stream_hits_socket_timeout(tmp_path, monkeypatch):
    """真机：流中途停滞超过 socket 超时 → socket.timeout → 走 network 重试。"""
    events = [
        b'data: {"choices":[{"index":0,"delta":{"content":"x"},"finish_reason":null}]}\n\n',
        b'data: {"choices":[{"index":0,"delta":{"content":"y"},"finish_reason":null}]}\n\n',
    ]
    server = _serve_sse(events, delay=0.0, stall_after=1, stall_seconds=5.0)
    try:
        _real_urlopen(monkeypatch)
        _no_backoff(monkeypatch)
        runner = _make_runner(tmp_path, request_timeout=0.4)
        runner.config["base_url"] = f"http://127.0.0.1:{server.server_address[1]}"
        started = time.monotonic()
        data = runner._call_llm([{"role": "user", "content": "x"}])
        elapsed = time.monotonic() - started
    finally:
        server.shutdown()
        server.server_close()

    assert data is None, "停滞流必须判死（不得把半截内容当回包）"
    # 3 次尝试（每次在 ~0.4s 的块间停滞超时上判死）+ 2 次退避（已置零）
    assert elapsed < 3.0, f"停滞应在 ~0.4s×3 次尝试内抛错（实测 {elapsed:.2f}s）"
    assert elapsed >= 0.4, f"应真的等到 socket 超时才判死（实测 {elapsed:.2f}s）"


def _llm_calls(tmp_path):
    events = []
    for p in (tmp_path / ".memory" / "sessions").glob("*.jsonl"):
        events.extend(load_events(p))
    return [e for e in events if e["type"] == "llm_call"]


# ── 1. content 分片累积 ─────────────────────────────────────────────────


def test_sse_content_accumulates_across_deltas(tmp_path, monkeypatch):
    """多个 content delta（且被 read 切断）拼接为完整 content。"""
    body = sse_body(
        delta("考研"), delta("政治"), delta("：帽子题"),
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    )
    fake, calls = _make_urlopen([FakeStreamResponse(body, chunk_size=7)])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)

    runner = _make_runner(tmp_path)
    data = runner._call_llm([{"role": "user", "content": "你好"}])

    assert data is not None
    msg = data["choices"][0]["message"]
    assert msg["content"] == "考研政治：帽子题"
    assert data["choices"][0]["finish_reason"] == "stop"
    assert calls[0]["body"]["stream"] is True
    assert len(calls) == 1, "健康流一次成功，不应重试"


def test_sse_reasoning_content_accumulates(tmp_path, monkeypatch):
    """reasoning_content 同样按序累积（deepseek-reasoner 类模型）。"""
    body = sse_body(
        {"choices": [{"index": 0, "delta": {"reasoning_content": "先看"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"reasoning_content": "题干"}, "finish_reason": None}]},
        delta("答案"), finish("stop"),
    )
    fake, _ = _make_urlopen([FakeStreamResponse(body)])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)

    data = _make_runner(tmp_path)._call_llm([{"role": "user", "content": "x"}])
    msg = data["choices"][0]["message"]
    assert msg["content"] == "答案"
    assert msg["reasoning_content"] == "先看题干"


# ── 2. tool_calls 分片累积 ──────────────────────────────────────────────


def test_sse_tool_calls_fragments_merge_by_index(tmp_path, monkeypatch):
    """两个 tool_call 的 arguments 分片到达 → 按 index 归并成完整可解析 JSON。"""
    args0 = json.dumps({"path": "04-专业课/真题/2024.pdf", "page": 3},
                       ensure_ascii=False)
    args1 = json.dumps({"command": "dir"}, ensure_ascii=False)
    body = sse_body(
        tool_delta(0, call_id="call_a", name="read_file", args=args0[:9]),
        tool_delta(1, call_id="call_b", name="run_command", args=args1[:4]),
        tool_delta(0, args=args0[9:20]),
        tool_delta(1, args=args1[4:]),
        tool_delta(0, args=args0[20:]),
        # 部分网关每块重复发送 id/name —— 必须取首个非空值而非覆盖
        tool_delta(0, call_id="call_a", name="read_file", args=""),
        finish("tool_calls"),
    )
    fake, _ = _make_urlopen([FakeStreamResponse(body, chunk_size=13)])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)

    data = _make_runner(tmp_path)._call_llm([{"role": "user", "content": "x"}])
    calls = data["choices"][0]["message"]["tool_calls"]
    assert [c["id"] for c in calls] == ["call_a", "call_b"]
    assert [c["type"] for c in calls] == ["function", "function"]
    assert [c["function"]["name"] for c in calls] == ["read_file", "run_command"]
    # 下游（loop.run）会对 arguments 做 json.loads —— 必须是完整字符串
    assert json.loads(calls[0]["function"]["arguments"]) == {
        "path": "04-专业课/真题/2024.pdf", "page": 3}
    assert json.loads(calls[1]["function"]["arguments"]) == {"command": "dir"}
    assert data["choices"][0]["finish_reason"] == "tool_calls"


def test_sse_tool_call_without_id_or_name(tmp_path, monkeypatch):
    """网关只给 arguments 分片（无 id/name）时也必须重建出结构完整的 tool_calls。"""
    body = sse_body(
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "{\"q\": 1}"}}]},
            "finish_reason": None}]},
        finish("tool_calls"),
    )
    fake, _ = _make_urlopen([FakeStreamResponse(body)])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)

    data = _make_runner(tmp_path)._call_llm([{"role": "user", "content": "x"}])
    call = data["choices"][0]["message"]["tool_calls"][0]
    assert call["id"], "缺失 id 时必须生成可用 id（下游以它配对 tool 结果）"
    assert call["type"] == "function"
    assert json.loads(call["function"]["arguments"]) == {"q": 1}


# ── 3. finish_reason 透传 ───────────────────────────────────────────────


@pytest.mark.parametrize("reason", ["stop", "length", "tool_calls"])
def test_finish_reason_passthrough(tmp_path, monkeypatch, reason):
    body = sse_body(delta("内容"), finish(reason))
    fake, _ = _make_urlopen([FakeStreamResponse(body)])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)

    data = _make_runner(tmp_path)._call_llm([{"role": "user", "content": "x"}])
    assert data["choices"][0]["finish_reason"] == reason


# ── 4. Content-Type 非 event-stream → JSON 回退 ─────────────────────────


def test_json_fallback_when_content_type_is_not_event_stream(tmp_path, monkeypatch):
    """老代理把回包缓冲成 JSON（Content-Type: application/json）→ 按原方式解析。"""
    payload = {"choices": [{"message": {"content": "回退答复", "tool_calls": []}}],
               "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}
    fake, calls = _make_urlopen([JsonResponse(payload)])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)

    data = _make_runner(tmp_path)._call_llm([{"role": "user", "content": "x"}])
    assert data["choices"][0]["message"]["content"] == "回退答复"
    assert data["usage"]["total_tokens"] == 3
    assert calls[0]["body"]["stream"] is True, "仍应请求流式，只是回包被缓冲"


def test_sse_text_without_content_type_still_parsed(tmp_path, monkeypatch):
    """网关漏标 Content-Type 但回包确是 SSE 文本 → 仍能重建（容错，不误判为坏包）。"""
    body = sse_body(delta("漏标也"), delta("能解析"), finish("stop"))
    # 漏标 Content-Type → 走整体读取路径，故用大 chunk_size 复刻一次性到齐
    fake, _ = _make_urlopen([FakeStreamResponse(body, content_type="",
                                                chunk_size=10 ** 9)])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)

    data = _make_runner(tmp_path)._call_llm([{"role": "user", "content": "x"}])
    assert data["choices"][0]["message"]["content"] == "漏标也能解析"


def test_gzipped_sse_body_falls_back_to_buffered_parse(tmp_path, monkeypatch):
    """代理无视 identity 仍压缩 SSE → 整体解压后按 SSE 文本重建（不误判为坏包）。"""
    import gzip
    raw = sse_body(delta("压缩也"), delta("能解析"), finish("stop"))
    resp = FakeStreamResponse(gzip.compress(raw), content_type="text/event-stream",
                              chunk_size=10 ** 9)
    resp.headers["Content-Encoding"] = "gzip"
    fake, _ = _make_urlopen([resp])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)

    data = _make_runner(tmp_path)._call_llm([{"role": "user", "content": "x"}])
    assert data["choices"][0]["message"]["content"] == "压缩也能解析"


def test_identity_content_encoding_still_streams(tmp_path, monkeypatch):
    """Content-Encoding: identity 属未压缩 → 仍走增量 SSE 分支。"""
    body = sse_body(delta("identity"), finish("stop"))
    resp = FakeStreamResponse(body, chunk_size=5)
    resp.headers["Content-Encoding"] = "identity"
    fake, _ = _make_urlopen([resp])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)

    data = _make_runner(tmp_path)._call_llm([{"role": "user", "content": "x"}])
    assert data["choices"][0]["message"]["content"] == "identity"


# ── 5. 流中途断连 / 读超时 → 既有重试逻辑 ───────────────────────────────


def test_midstream_disconnect_retried_then_fails(tmp_path, monkeypatch):
    """流中途 RemoteDisconnected：重试 3 次后如实返回 None，error_kind=network。"""
    body = sse_body(delta("半截内容"), finish("stop"))
    resp = FakeStreamResponse(body, chunk_size=8, fail_after=2)
    fake, calls = _make_urlopen([resp])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)
    monkeypatch.setattr(loop_module.time, "sleep", lambda s: None)

    runner = _make_runner(tmp_path, max_steps=1)
    runner.tool_registry.execute_tool = lambda name, args, interactive=True, call_id="": "样本工具结果"
    answer = runner.run("你好", interactive=False)

    assert answer == "", "残缺流不得被当成答案（不伪造）"
    assert len(calls) == 9, "主对话 3 次 + 收尾两档各 3 次（全部流中途断连）"
    first = _llm_calls(tmp_path)[0]["payload"]
    assert first["ok"] is False
    assert first["error_kind"] == "network"
    assert first["attempts"] == 3


def test_midstream_disconnect_then_success(tmp_path, monkeypatch):
    """第 1 次流中途断连、第 2 次完整 → 成功返回，遥测 attempts=2。"""
    good = FakeStreamResponse(sse_body(delta("第二次成功"), finish("stop")))
    bad = FakeStreamResponse(sse_body(delta("半截")), chunk_size=8, fail_after=1)
    fake, calls = _make_urlopen([bad, good])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)
    monkeypatch.setattr(loop_module.time, "sleep", lambda s: None)

    runner = _make_runner(tmp_path)
    assert runner.run("你好", interactive=False) == "第二次成功"
    assert len(calls) == 2
    assert _llm_calls(tmp_path)[0]["payload"]["attempts"] == 2
    assert _llm_calls(tmp_path)[0]["payload"]["ok"] is True


def test_stalled_stream_read_timeout_retried(tmp_path, monkeypatch):
    """完全停滞的流（read 抛 socket.timeout）→ 走 network 重试，最终 None。"""
    stalled = FakeStreamResponse(sse_body(delta("x")), fail_after=0,
                                 exc=socket.timeout("读超时：块间停滞"))
    fake, calls = _make_urlopen([stalled])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)
    monkeypatch.setattr(loop_module.time, "sleep", lambda s: None)

    runner = _make_runner(tmp_path, max_steps=1)
    runner.tool_registry.execute_tool = lambda name, args, interactive=True, call_id="": "r"
    assert runner.run("你好", interactive=False) == ""
    payload = _llm_calls(tmp_path)[0]["payload"]
    assert payload["error_kind"] == "network"
    assert payload["attempts"] == 3
    assert len(calls) == 9


def test_empty_stream_is_error_not_fake_answer(tmp_path, monkeypatch):
    """空流（只有 [DONE]）→ 判为坏包进入重试，不得伪造成「模型空回复」。"""
    fake, calls = _make_urlopen([FakeStreamResponse(b"data: [DONE]\n\n")])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)
    monkeypatch.setattr(loop_module.time, "sleep", lambda s: None)

    runner = _make_runner(tmp_path, max_steps=1)
    runner.tool_registry.execute_tool = lambda name, args, interactive=True, call_id="": "r"
    assert runner.run("你好", interactive=False) == ""
    payload = _llm_calls(tmp_path)[0]["payload"]
    assert payload["error_kind"] == "invalid_response"
    assert payload["ok"] is False
    assert payload["attempts"] == 1, "确定性坏包不重试（与旧的 JSON 坏包语义一致）"
    # 主对话 1 次 + 收尾两档各 1 次（都不重试）
    assert len(calls) == 3


# ── 6. stream_options 被拒（400）→ 自动降级重发 ─────────────────────────


def test_stream_options_rejected_400_auto_retry(tmp_path, monkeypatch):
    """网关 400（不认识 stream_options）→ 摘字段重发一次，调用整体成功。"""
    calls = []

    def fake(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        calls.append(body)
        if "stream_options" in body:
            raise urllib.error.HTTPError(
                "https://x/v1/chat/completions", 400, "Bad Request", {},
                _io_bytes(b'{"error":{"message":"Unknown parameter: stream_options"}}'))
        return FakeStreamResponse(sse_body(delta("降级后成功"), finish("stop")))

    monkeypatch.setattr(loop_module, "safe_urlopen", fake)

    runner = _make_runner(tmp_path)
    assert runner.run("你好", interactive=False) == "降级后成功"
    assert len(calls) == 2, "首发带 stream_options + 摘除后重发 = 2 次"
    assert calls[0].get("stream_options") == {"include_usage": True}
    assert "stream_options" not in calls[1]
    assert calls[1]["stream"] is True, "降级只摘 stream_options，仍保持流式"
    # 该降级不消耗网络重试次数 → 遥测 attempts 仍为 1
    assert _llm_calls(tmp_path)[0]["payload"]["attempts"] == 1
    assert _llm_calls(tmp_path)[0]["payload"]["ok"] is True


def test_stream_options_drop_persists_across_network_retry(tmp_path, monkeypatch):
    """stream_options 被 400 摘除后，后续 5xx 重试不再重复携带（不重复踩坑）。"""
    seen = []

    def fake(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        seen.append(body)
        if "stream_options" in body:
            raise urllib.error.HTTPError(
                "https://x/v1/chat/completions", 400, "Bad Request", {},
                _io_bytes(b'{"error":"unknown param stream_options"}'))
        if len(seen) == 2:
            raise urllib.error.HTTPError(
                "https://x/v1/chat/completions", 503, "Service Unavailable", {},
                _io_bytes(b'{"error":"unavailable"}'))
        return FakeStreamResponse(sse_body(delta("最终成功"), finish("stop")))

    monkeypatch.setattr(loop_module, "safe_urlopen", fake)
    monkeypatch.setattr(loop_module.time, "sleep", lambda s: None)

    runner = _make_runner(tmp_path)
    assert runner.run("你好", interactive=False) == "最终成功"
    assert len(seen) == 3, "400 降级 1 次 + 503 重试 1 次 + 成功 1 次"
    assert "stream_options" in seen[0]
    assert all("stream_options" not in b for b in seen[1:])


def test_without_tools_path_also_streams_and_degrades(tmp_path, monkeypatch):
    """降级纯文本路径（收尾链）同样流式 + stream_options 400 自动摘除。"""
    calls = []

    def fake(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        calls.append(body)
        if "stream_options" in body:
            raise urllib.error.HTTPError(
                "https://x/v1/chat/completions", 400, "Bad Request", {},
                _io_bytes(b'{"error":"unsupported field stream_options"}'))
        return FakeStreamResponse(sse_body(delta("收尾答复"), finish("stop")))

    monkeypatch.setattr(loop_module, "safe_urlopen", fake)

    data = _make_runner(tmp_path)._call_llm_without_tools([{"role": "user", "content": "x"}])
    assert data["choices"][0]["message"]["content"] == "收尾答复"
    assert len(calls) == 2
    assert calls[1]["stream"] is True


# ── 7. 超时语义：块间停滞上限，而非总时长上限 ───────────────────────────


def test_stream_timeout_is_stall_capped(tmp_path, monkeypatch):
    """socket 超时 = min(request_timeout, 90s)：默认 120 → 90。"""
    fake, calls = _make_urlopen([FakeStreamResponse(sse_body(delta("ok"), finish("stop")))])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)

    _make_runner(tmp_path)._call_llm([{"role": "user", "content": "x"}])
    assert calls[0]["timeout"] == _STREAM_STALL_TIMEOUT == 90.0


def test_stream_timeout_respects_smaller_request_timeout(tmp_path, monkeypatch):
    """用户显式调低 request_timeout（GUI 场景）时从严取小值。"""
    fake, calls = _make_urlopen([FakeStreamResponse(sse_body(delta("ok"), finish("stop")))])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)

    _make_runner(tmp_path, request_timeout=30)._call_llm([{"role": "user", "content": "x"}])
    assert calls[0]["timeout"] == 30.0


def test_healthy_slow_stream_exceeds_request_timeout(tmp_path, monkeypatch):
    """健康慢流：总时长 > request_timeout（块持续到达）仍成功——超时按块间算，不按总时长。"""
    body = sse_body(*([delta("慢")] * 30), finish("stop"))
    resp = FakeStreamResponse(body, chunk_size=64, delay=0.02)   # 约 40 片 × 20ms
    fake, calls = _make_urlopen([resp])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)

    started = time.monotonic()
    data = _make_runner(tmp_path, request_timeout=0.2)._call_llm([{"role": "user", "content": "x"}])
    elapsed = time.monotonic() - started

    assert data["choices"][0]["message"]["content"] == "慢" * 30
    assert calls[0]["timeout"] == 0.2
    assert elapsed > 0.2, f"本用例前提是总耗时超过 request_timeout（实测 {elapsed:.2f}s）"
    assert len(calls) == 1, "慢但健康的流不得被判死"


# ── 8. usage 尽力而为 ───────────────────────────────────────────────────


def test_usage_from_final_chunk_recorded(tmp_path, monkeypatch):
    """stream_options 生效时，末尾块的 usage 原样透传进 llm_call 事件。"""
    usage = {"prompt_tokens": 111, "completion_tokens": 22, "total_tokens": 133}
    body = sse_body(delta("答复"), finish("stop"),
                    {"choices": [], "usage": usage})
    fake, _ = _make_urlopen([FakeStreamResponse(body)])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)

    runner = _make_runner(tmp_path)
    assert runner.run("你好", interactive=False) == "答复"
    assert _llm_calls(tmp_path)[0]["payload"]["usage"] == usage


def test_usage_absent_stays_none(tmp_path, monkeypatch):
    """上游不回 usage → 落盘 None（绝不伪造），且下游不因缺字段崩溃。"""
    fake, _ = _make_urlopen([FakeStreamResponse(sse_body(delta("答复"), finish("stop")))])
    monkeypatch.setattr(loop_module, "safe_urlopen", fake)

    runner = _make_runner(tmp_path)
    assert runner.run("你好", interactive=False) == "答复"
    payload = _llm_calls(tmp_path)[0]["payload"]
    assert payload["usage"] is None
    assert payload["ok"] is True


