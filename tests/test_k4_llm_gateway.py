# -*- coding: utf-8 -*-
"""[K4] 统一 LLM 出口（llm_client）—— 回归测试。

背景
----
收敛前全仓并存 6 套 LLM HTTP 客户端（llm_client.chat_completion /
agent.loop._call_llm / cli.agent.engine.stream_chat / open_grader /
vision_solver / study_planner），退避公式与错误分类各自为政。K4 把
「发请求 + 分类重试 + SSE 解析」收敛为 ``llm_client.request_chat``，
各调用方只保留展示与降级策略。

本文件覆盖
----------
1. 错误分类矩阵（429/408/5xx 可重试；400+工具关键词 → tools_unsupported；
   401/403 → auth；404 → not_found；其余 4xx → bad_request）；
2. ``parse_retry_after``（秒 / HTTP-date / 非法）；
3. ``compute_backoff`` 与 loop 收敛前公式逐字一致（含 Retry-After 取 max）；
4. ``request_chat``：429+Retry-After 的睡眠值、401 不重试、5xx 重试耗尽、
   payload_adjuster「仅一轮调整」、空流不伪造、结构化异常层级；
5. loop：400 工具敏感 → ``_call_llm_without_tools`` 降级（error_kind 逐字保持）、
   空流 → invalid_response 且不重试；
6. SSE 真机（本机回环服务端）：流式重建 + 停滞判死；
7. ``normalize_openai_url`` 归一矩阵与「单一真源 re-export」；
8. open_grader 的 ``_is_retryable`` / ``_retry_delay`` 委托契约。

全程离线：HTTP stub 或本机回环服务端，绝不联网。
测试内**不全局 patch time.sleep**（退避经 ``sleep_fn`` 注入）。
"""

import datetime
import email.utils
import http.server
import io
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import llm_client  # noqa: E402
import tools.agent.loop as loop_module  # noqa: E402
from llm_client import (  # noqa: E402
    ChatRequest,
    LLMDeterministicError,
    LLMEmptyStreamError,
    LLMError,
    LLMResponseTooLargeError,
    LLMRetryExhausted,
    LLMRetryableError,
    classify_http_error,
    compute_backoff,
    normalize_openai_url,
    parse_retry_after,
    request_chat,
)
from tools.agent.loop import AgentRunner  # noqa: E402
from tools.agent.session_log import load_events  # noqa: E402

URL = "https://api.test.com/v1/chat/completions"


# ── 基础设施 ────────────────────────────────────────────────────────────


def sse_body(*events, done=True) -> bytes:
    """把若干 dict 拼成 SSE 响应体。"""
    lines = []
    for ev in events:
        lines.append("data: " + json.dumps(ev, ensure_ascii=False))
        lines.append("")
    if done:
        lines.append("data: [DONE]")
        lines.append("")
    return ("\n".join(lines) + "\n").encode("utf-8")


def delta(content):
    return {"choices": [{"index": 0, "delta": {"content": content},
                         "finish_reason": None}]}


def finish(reason="stop"):
    return {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}


class FakeStreamResponse:
    """模拟 urllib 流式响应：按 ``read(n)`` 逐片吐字节。"""

    def __init__(self, body: bytes, content_type: str = "text/event-stream",
                 chunk_size: int = 17):
        self._body = body
        self._pos = 0
        self.headers = {"Content-Type": content_type}
        self._chunk_size = chunk_size

    def read(self, n=-1):
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


def http_error(code: int, body: bytes = b"{}", headers=None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(URL, code, "Error", headers or {}, io.BytesIO(body))


def make_request(**kw) -> ChatRequest:
    base = dict(messages=[{"role": "user", "content": "hi"}], model="m",
                stream=True, api_key="sk-test", base_url="https://api.test.com/v1")
    base.update(kw)
    return ChatRequest(**base)


# ── 1. 错误分类矩阵 ─────────────────────────────────────────────────────


@pytest.mark.parametrize("status,body,retryable,kind", [
    (429, "", True, "http_retryable"),
    (408, "", True, "http_retryable"),
    (409, "", True, "http_retryable"),      # 沿用 open_grader 既有可重试集合
    (425, "", True, "http_retryable"),
    (500, "", True, "http_retryable"),
    (502, "", True, "http_retryable"),
    (503, "", True, "http_retryable"),
    (504, "", True, "http_retryable"),
    (400, "Unknown parameter: tools", False, "tools_unsupported"),
    (400, "this model does not support function calling", False, "tools_unsupported"),
    (400, "Invalid request: extra field", False, "tools_unsupported"),
    (400, "bad payload", False, "bad_request"),
    (401, "", False, "auth"),
    (403, "", False, "auth"),
    (404, "", False, "not_found"),
    (422, "", False, "bad_request"),
])
def test_classify_http_error_matrix(status, body, retryable, kind):
    assert classify_http_error(status, body) == (retryable, kind)


def test_classify_http_error_tolerates_garbage_status():
    assert classify_http_error(None, "")[0] is False
    assert classify_http_error("not-a-code", "")[0] is False


# ── 2. parse_retry_after ────────────────────────────────────────────────


def test_parse_retry_after_seconds():
    assert parse_retry_after({"Retry-After": "3"}) == 3.0
    assert parse_retry_after({"Retry-After": "2.5"}) == 2.5
    assert parse_retry_after({"Retry-After": " 4 "}) == 4.0


def test_parse_retry_after_http_date():
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=8)
    val = parse_retry_after({"Retry-After": email.utils.format_datetime(future)})
    assert val is not None and 5.0 < val <= 9.0
    past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=60)
    assert parse_retry_after({"Retry-After": email.utils.format_datetime(past)}) == 0.0


def test_parse_retry_after_invalid():
    assert parse_retry_after(None) is None
    assert parse_retry_after({}) is None
    assert parse_retry_after({"Retry-After": ""}) is None
    assert parse_retry_after({"Retry-After": "soon"}) is None
    assert parse_retry_after({"Retry-After": "nan"}) is None


# ── 3. compute_backoff（与 loop 收敛前公式逐字一致）─────────────────────


def test_compute_backoff_matches_legacy_loop_formula():
    # http_retryable: base = 2.0 + attempt*4.0，抖动 0..1.0
    for _ in range(50):
        assert 2.0 <= compute_backoff("http_retryable", 0) < 3.0
        assert 6.0 <= compute_backoff("http_retryable", 1) < 7.0
        assert 10.0 <= compute_backoff("http_retryable", 2) < 11.0
        # network: base = (2.0, 6.0)[min(attempt,1)]，抖动 0..0.5
        assert 2.0 <= compute_backoff("network", 0) < 2.5
        assert 6.0 <= compute_backoff("network", 1) < 6.5
        assert 6.0 <= compute_backoff("network", 9) < 6.5


def test_compute_backoff_retry_after_takes_max():
    # 有 Retry-After 时 base = max(base, retry_after)
    for _ in range(50):
        assert 3.0 <= compute_backoff("http_retryable", 0, 3.0) < 4.0
        assert 6.0 <= compute_backoff("http_retryable", 1, 3.0) < 7.0
        assert 30.0 <= compute_backoff("http_retryable", 0, 30.0) < 31.0


# ── 4. request_chat 行为 ────────────────────────────────────────────────


def test_request_chat_429_retry_after_controls_sleep():
    """429 + Retry-After: 3 → 睡眠 base 取 max(公式, 3)；重试后成功。"""
    calls, sleeps = [], []

    def stub(req, timeout=None):
        calls.append(json.loads(req.data.decode("utf-8")))
        if len(calls) <= 2:
            raise http_error(429, b'{"error":"rate limited"}',
                             {"Retry-After": "3"})
        return FakeStreamResponse(sse_body(delta("限流后成功"), finish()))

    stats = {}
    data = request_chat(make_request(), max_retries=2,
                        sleep_fn=sleeps.append, stats=stats, urlopen_fn=stub)

    assert data["choices"][0]["message"]["content"] == "限流后成功"
    assert len(calls) == 3
    assert len(sleeps) == 2
    assert 3.0 <= sleeps[0] < 4.0, f"attempt0 base=max(2.0,3.0)+抖动，实测 {sleeps[0]}"
    assert 6.0 <= sleeps[1] < 7.0, f"attempt1 base=max(6.0,3.0)+抖动，实测 {sleeps[1]}"
    assert stats["attempts"] == 3


def test_request_chat_401_deterministic_no_retry():
    """401 属确定性错误：不重试、不睡眠、抛 LLMDeterministicError(kind=auth)。"""
    calls, sleeps = [], []

    def stub(req, timeout=None):
        calls.append(1)
        raise http_error(401, b'{"error":"bad key"}')

    with pytest.raises(LLMDeterministicError) as ei:
        request_chat(make_request(), max_retries=2,
                     sleep_fn=sleeps.append, urlopen_fn=stub)
    assert ei.value.status == 401
    assert ei.value.kind == "auth"
    assert len(calls) == 1, "401 不得重试"
    assert sleeps == []


def test_request_chat_5xx_exhausted_raises_retry_exhausted():
    calls, sleeps = [], []

    def stub(req, timeout=None):
        calls.append(1)
        raise http_error(503, b'{"error":"unavailable"}')

    stats = {}
    with pytest.raises(LLMRetryExhausted) as ei:
        request_chat(make_request(), max_retries=2,
                     sleep_fn=sleeps.append, stats=stats, urlopen_fn=stub)
    assert ei.value.status == 503
    assert ei.value.kind == "http_retryable"
    assert isinstance(ei.value.last_error, LLMRetryableError)
    assert len(calls) == 3, "max_retries=2 → 共 3 次尝试"
    assert len(sleeps) == 2
    assert 2.0 <= sleeps[0] < 3.0 and 6.0 <= sleeps[1] < 7.0
    assert stats["attempts"] == 3
    assert stats["last_error_kind"] == "http_retryable"


def test_request_chat_payload_adjuster_single_round():
    """payload_adjuster 仅允许一轮调整：返回 dict → 就地换 payload 重发一次。"""
    calls, adjusted = [], []

    def stub(req, timeout=None):
        calls.append(json.loads(req.data.decode("utf-8")))
        raise http_error(400, b'{"error":"unsupported parameter"}')

    def adjuster(payload, err):
        adjusted.append(err)
        return dict(payload, marker=len(adjusted))

    with pytest.raises(LLMDeterministicError):
        request_chat(make_request(), max_retries=2, payload_adjuster=adjuster,
                     sleep_fn=lambda s: None, urlopen_fn=stub)

    assert len(adjusted) == 1, "调整只允许一轮"
    assert len(calls) == 2, "首发 + 调整后重发一次"
    assert calls[1]["marker"] == 1


def test_request_chat_empty_stream_is_error_not_fake_answer():
    """空流 → LLMEmptyStreamError（确定性、不伪造空回复、不重试）。"""
    calls = []

    def stub(req, timeout=None):
        calls.append(1)
        return FakeStreamResponse(b"data: [DONE]\n\n")

    with pytest.raises(LLMEmptyStreamError) as ei:
        request_chat(make_request(), max_retries=2,
                     sleep_fn=lambda s: None, urlopen_fn=stub)
    assert isinstance(ei.value, ValueError), "loop 依赖 ValueError 归类 invalid_response"
    assert isinstance(ei.value, LLMDeterministicError)
    assert len(calls) == 1, "确定性坏包不重试"


def test_request_chat_timeout_is_caller_decided():
    """超时策略留在调用方：ChatRequest.timeout 原样传给传输层。"""
    seen = {}

    def stub(req, timeout=None):
        seen["timeout"] = timeout
        return FakeStreamResponse(sse_body(delta("ok"), finish()))

    request_chat(make_request(timeout=7.5), max_retries=0, urlopen_fn=stub)
    assert seen["timeout"] == 7.5


def test_request_chat_default_transport_is_patchable():
    """未注入 urlopen_fn 时走模块级 safe_urlopen（生产路径，可被既有测试 patch）。"""
    def fake(req, timeout=None):
        return FakeStreamResponse(sse_body(delta("默认通道"), finish()))

    with patch("tools.llm_client.safe_urlopen", side_effect=fake) as m:
        data = request_chat(make_request(), max_retries=0)
    assert data["choices"][0]["message"]["content"] == "默认通道"
    assert m.call_count == 1


def test_exception_hierarchy_contract():
    assert issubclass(LLMRetryableError, LLMError)
    assert issubclass(LLMDeterministicError, LLMError)
    assert issubclass(LLMResponseTooLargeError, LLMDeterministicError)
    assert issubclass(LLMResponseTooLargeError, ValueError)
    assert issubclass(LLMEmptyStreamError, LLMDeterministicError)
    assert issubclass(LLMEmptyStreamError, ValueError)
    assert issubclass(LLMRetryExhausted, LLMRetryableError)


# ── 5. loop 集成契约（error_kind 逐字保持）──────────────────────────────


def _make_runner(tmp_path):
    return AgentRunner(
        config={"api_key": "sk-test-fake", "model": "样本模型",
                "active_subject": "pol"},
        workspace_root=tmp_path, permission_mode="auto", quiet=True)


def _llm_calls(tmp_path):
    events = []
    for p in (tmp_path / ".memory" / "sessions").glob("*.jsonl"):
        events.extend(load_events(p))
    return [e for e in events if e["type"] == "llm_call"]


def test_loop_400_tools_unsupported_downgrades_to_without_tools(tmp_path):
    """400 工具敏感 → 平滑降级为纯文本请求，error_kind=http_400_downgrade。"""
    calls = []

    def stub(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        calls.append(body)
        if "tools" in body:
            raise http_error(400, '{"error":"This model does not support tools"}'.encode())
        return FakeStreamResponse(sse_body(delta("纯文本降级答复"), finish()))

    runner = _make_runner(tmp_path)
    runner._ensure_session_log()          # 让遥测事件真的落盘
    with patch.object(loop_module, "safe_urlopen", stub):
        data = runner._call_llm([{"role": "user", "content": "hi"}])

    assert data["choices"][0]["message"]["content"] == "纯文本降级答复"
    assert "tools" in calls[0], "首发应携带工具 schema"
    # calls[1] 是 stream_options 被 400 拒绝后的摘字段重发（仍带 tools，与旧行为一致），
    # 最后一次调用才是降级后的纯文本请求。
    assert "stream_options" in calls[0]
    assert "stream_options" not in calls[1]
    assert "tools" not in calls[-1], "降级请求不得携带 tools"
    payload = _llm_calls(tmp_path)[0]["payload"]
    assert payload["ok"] is False
    assert payload["error_kind"] == "http_400_downgrade"


def test_loop_empty_stream_maps_to_invalid_response_once(tmp_path):
    """空流 → error_kind=invalid_response 且 attempts=1（确定性坏包不重试）。"""
    calls = []

    def stub(req, timeout=None):
        calls.append(1)
        return FakeStreamResponse(b"data: [DONE]\n\n")

    runner = _make_runner(tmp_path)
    runner._ensure_session_log()
    with patch.object(loop_module, "safe_urlopen", stub):
        data = runner._call_llm([{"role": "user", "content": "hi"}])

    assert data is None, "空流不得伪造成回复"
    assert len(calls) == 1
    payload = _llm_calls(tmp_path)[0]["payload"]
    assert payload["ok"] is False
    assert payload["error_kind"] == "invalid_response"
    assert payload["attempts"] == 1


# ── 6. SSE 真机（本机回环服务端）────────────────────────────────────────


class _SSEHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    events: list = []
    delay = 0.01
    stall_after = None
    stall_seconds = 0.0

    def log_message(self, *args):      # 静音
        pass

    def _write(self, data: bytes) -> bool:
        try:
            self.wfile.write(data)
            self.wfile.flush()
            return True
        except Exception:
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


def _serve_sse(events, *, delay=0.01, stall_after=None, stall_seconds=0.0):
    handler = type("_H", (_SSEHandler,),
                   {"events": list(events), "delay": delay,
                    "stall_after": stall_after, "stall_seconds": stall_seconds})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_real_socket_sse_rebuild_and_on_chunk():
    """真机：本机服务端逐块吐 SSE → 重建 dict + on_chunk 逐块回调。"""
    events = [
        'data: {"choices":[{"index":0,"delta":{"content":"考研"},"finish_reason":null}]}\n\n'.encode(),
        'data: {"choices":[{"index":0,"delta":{"content":"政治"},"finish_reason":null}]}\n\n'.encode(),
        b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n',
        b'data: [DONE]\n\n',
    ]
    server = _serve_sse(events)
    chunks = []
    try:
        req = make_request(base_url=f"http://127.0.0.1:{server.server_address[1]}",
                           timeout=10)
        # 回环地址被 net_guard 拦（SSRF），测试夹具改用真 urllib
        data = request_chat(req, max_retries=0, on_chunk=chunks.append,
                            urlopen_fn=urllib.request.urlopen)
    finally:
        server.shutdown()
        server.server_close()

    assert data["choices"][0]["message"]["content"] == "考研政治"
    assert data["choices"][0]["finish_reason"] == "stop"
    assert "".join(chunks) == "考研政治"


def test_real_socket_stalled_stream_hits_timeout():
    """真机：流中途停滞超过 socket 超时 → 网络类重试耗尽（快速判死，不吞半截）。"""
    events = [
        'data: {"choices":[{"index":0,"delta":{"content":"x"},"finish_reason":null}]}\n\n'.encode(),
        'data: {"choices":[{"index":0,"delta":{"content":"y"},"finish_reason":null}]}\n\n'.encode(),
    ]
    server = _serve_sse(events, delay=0.0, stall_after=1, stall_seconds=5.0)
    try:
        req = make_request(base_url=f"http://127.0.0.1:{server.server_address[1]}",
                           timeout=0.4)
        started = time.monotonic()
        with pytest.raises(LLMRetryExhausted) as ei:
            request_chat(req, max_retries=0, sleep_fn=lambda s: None,
                         urlopen_fn=urllib.request.urlopen)
        elapsed = time.monotonic() - started
    finally:
        server.shutdown()
        server.server_close()

    assert ei.value.kind == "network"
    assert ei.value.status is None
    # 下界留 0.35（而非 0.4）：单次尝试的 elapsed ≈ socket 超时本身，
    # Windows 定时器粒度（~15.6ms）与满负载调度抖动会让实测出现 0.39x
    # （全量 pytest 实测 0.39s）；0.35 仍能区分「过早判死」的实现缺陷。
    assert 0.35 <= elapsed < 3.0, f"应在 ~0.4s 的块间停滞超时上判死（实测 {elapsed:.2f}s）"


# ── 7. normalize_openai_url 单一真源 ────────────────────────────────────


@pytest.mark.parametrize("base,endpoint,expected", [
    ("https://generativelanguage.googleapis.com/v1beta/openai", "chat/completions",
     "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"),
    ("https://api.deepseek.com", "chat/completions",
     "https://api.deepseek.com/v1/chat/completions"),
    ("https://api.deepseek.com/", "chat/completions",
     "https://api.deepseek.com/v1/chat/completions"),
    ("https://x.com/v2", "chat/completions",
     "https://x.com/v2/chat/completions"),
    ("https://x.com/v1/chat/completions", "chat/completions",
     "https://x.com/v1/chat/completions"),
    ("https://x.com/v1/chat/completions", "models",
     "https://x.com/v1/models"),
    ("https://open.bigmodel.cn/api/paas/v4", "models",
     "https://open.bigmodel.cn/api/paas/v4/models"),
    ("https://x.com/openai", "chat/completions",
     "https://x.com/openai/chat/completions"),
    ("", "chat/completions",
     "https://api.deepseek.com/v1/chat/completions"),
])
def test_normalize_openai_url_matrix(base, endpoint, expected):
    assert normalize_openai_url(base, endpoint) == expected


def test_normalize_openai_url_single_source_reexports():
    """loop / engine 的 normalize 必须与 llm_client 是同一函数对象（单一真源）。"""
    import tools.cli.agent.engine as engine
    assert loop_module.normalize_openai_url is llm_client.normalize_openai_url
    assert engine.normalize_openai_url is llm_client.normalize_openai_url


def test_loop_reexports_sse_constants():
    """W8 测试直接 import 的常量必须保持可用。"""
    from tools.agent.loop import _SSE_READ_CHUNK, _STREAM_STALL_TIMEOUT
    assert _STREAM_STALL_TIMEOUT == 90.0
    assert _SSE_READ_CHUNK == 4096


# ── 8. open_grader 委托契约 ─────────────────────────────────────────────


def test_open_grader_retry_classification_delegates():
    from skills import open_grader as G

    assert G._status_of(RuntimeError("HTTP 429")) == 429
    assert G._is_retryable(RuntimeError("HTTP 429")) is True
    assert G._is_retryable(RuntimeError("HTTP 408")) is True
    assert G._is_retryable(RuntimeError("HTTP 503")) is True
    assert G._is_retryable(RuntimeError("HTTP 401")) is False
    assert G._is_retryable(RuntimeError("HTTP 403")) is False
    assert G._is_retryable(RuntimeError("HTTP 404")) is False
    assert G._is_retryable(RuntimeError("连接被重置")) is True   # 无状态码 → 可重试


def test_open_grader_retry_delay_uses_retry_after_and_cap():
    from skills import open_grader as G

    err = RuntimeError("HTTP 429")
    err.retry_after = 3.0
    assert G._retry_delay(err, 0, 45.0) == 3.0
    # 无 Retry-After → 线性 0.5*(attempt+1)
    assert G._retry_delay(RuntimeError("HTTP 429"), 0, 45.0) == 0.5
    assert G._retry_delay(RuntimeError("HTTP 429"), 1, 45.0) == 1.0
    # 上限为 max(1.0, timeout)
    assert G._retry_delay(RuntimeError("HTTP 429"), 0, 0.2) == 0.5
    assert G._retry_delay(RuntimeError("HTTP 429"), 0, 1.0) == 0.5
    # 文本形态的 Retry-After 仍可解析（经 parse_retry_after 归一）
    assert G._retry_delay(RuntimeError("HTTP 429 Retry-After: 7"), 0, 45.0) == 7.0


def test_open_grader_http_error_wrapped_as_runtime_error_with_retry_after():
    """OpenAICompatClient 的异常形态：RuntimeError("HTTP {status}") + retry_after 属性。"""
    from skills import open_grader as G

    def stub(req, timeout=None):
        raise http_error(429, b'{"error":"slow down"}', {"Retry-After": "5"})

    client = G.OpenAICompatClient(
        endpoint={"base_url": "https://api.test.com/v1", "api_key": "sk-x"}, timeout=10)
    with patch.object(G, "safe_urlopen", stub):
        with pytest.raises(RuntimeError) as ei:
            client.chat([{"role": "user", "content": "hi"}])

    assert "HTTP 429" in str(ei.value)
    assert G._status_of(ei.value) == 429
    assert G._is_retryable(ei.value) is True
    assert G._retry_delay(ei.value, 0, 45.0) == 5.0
