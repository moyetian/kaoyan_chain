# -*- coding: utf-8 -*-
"""
tests/test_challenger_1_concurrency_and_decompression.py

Empirical Challenger 1 Adversarial & Stress Verification Suite (Gen 4)
=======================================================================
Rigorous adversarial challenges covering:
1. Concurrency Stress Test on tools/cli/gateway.py:
   - 30-50 concurrent threads simulating severe contention
   - Rapid interleaved appends, clear operations, and snapshot readers
   - Continuous real HTTP GET /api/live and POST /api/clear requests against ThreadingHTTPServer
   - Invariant monitor verifying len(LIVE_SESSION_MESSAGES) <= 60 under lock
   - Checksum-verified data integrity (zero torn state, zero deadlocks, zero RuntimeError)
   - Concurrent query_llm_reply session reader stress
2. Decompression Adversarial Fuzzing on tools/llm_client.py:
   - Truncated gzip streams (headers only, partial payloads, missing CRC32)
   - Corrupt zlib and deflate streams (noise, corrupted Adler-32, flipped bits)
   - Raw deflate (-zlib.MAX_WBITS) with and without headers
   - Double compression (gzip in gzip, deflate in gzip, gzip in deflate, deflate in deflate)
   - Empty byte strings, None, and null byte streams
   - Non-UTF8 binary noise and randomized fuzzing payloads
   - All 9 HTTP 400 error keywords fallback verification (max_tokens, token, tokens,
     max_completion_tokens, max_output_tokens, system, role, 系统, 角色)
   - End-to-end chat_completion and fetch_upstream_models survival tests
"""

from __future__ import annotations

import gzip
import json
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from http.server import ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from tools.cli.gateway import (
    _LIVE_SESSION_LOCK,
    LIVE_SESSION_MESSAGES,
    append_live_message,
    clear_live_messages,
    create_gateway_handler,
    get_live_messages_snapshot,
)
from tools.llm_client import (
    _decompress_response_bytes,
    call_llm_sync,
    chat_completion,
    fetch_upstream_models,
)


@pytest.fixture(autouse=True)
def clean_gateway_buffer():
    """Ensure clean gateway live buffer before and after each test."""
    clear_live_messages()
    yield
    clear_live_messages()


# ============================================================================
# TARGET 1: Concurrency Stress Test on tools/cli/gateway.py
# ============================================================================

def test_gateway_50_threads_burst_contention_and_invariants():
    """Simulate extreme contention with 50 concurrent threads:

    - 15 Appender threads blasting checksummed messages
    - 10 Clearer threads rapidly clearing the session
    - 15 HTTP live reader threads continuously querying GET /api/live
    - 10 Snapshot reader threads querying get_live_messages_snapshot
    - 1 Dedicated Invariant Checker thread continuously asserting len <= 60
    """
    handler_class = create_gateway_handler(token="")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
    server_port = server.server_address[1]

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    base_url = f"http://127.0.0.1:{server_port}"
    errors: List[str] = []
    max_len_observed = [0]
    total_appends = [0]
    total_http_reads = [0]
    total_snapshot_reads = [0]
    total_clears = [0]

    num_threads = 50
    barrier = threading.Barrier(num_threads)
    stop_event = threading.Event()
    time_regex = re.compile(r"^\d{2}:\d{2}:\d{2}$")

    # Invariant checker thread
    def invariant_checker():
        while not stop_event.is_set():
            with _LIVE_SESSION_LOCK:
                curr_len = len(LIVE_SESSION_MESSAGES)
                if curr_len > max_len_observed[0]:
                    max_len_observed[0] = curr_len
                if curr_len > 60:
                    errors.append(f"INVARIANT VIOLATION: buffer length {curr_len} > 60")
            time.sleep(0.0002)

    checker_thread = threading.Thread(target=invariant_checker, daemon=True)
    checker_thread.start()

    def appender_worker(tid: int):
        try:
            barrier.wait()
            for seq in range(60):
                chk = tid * 10000 + seq
                payload = f"TID={tid}|SEQ={seq}|CHK={chk}"
                append_live_message(f"user_{tid}", payload)
                with _LIVE_SESSION_LOCK:
                    total_appends[0] += 1
                    c_len = len(LIVE_SESSION_MESSAGES)
                    if c_len > 60:
                        errors.append(f"Appender {tid} saw buffer len {c_len} > 60")
        except Exception as exc:
            errors.append(f"Appender-{tid} error: {exc}")

    def clearer_worker(tid: int):
        try:
            barrier.wait()
            for _ in range(25):
                clear_live_messages()
                with _LIVE_SESSION_LOCK:
                    total_clears[0] += 1
                time.sleep(0.001)
        except Exception as exc:
            errors.append(f"Clearer-{tid} error: {exc}")

    def http_live_worker(tid: int):
        try:
            barrier.wait()
            for _ in range(30):
                req = urllib.request.Request(f"{base_url}/api/live")
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    assert resp.status == 200
                    body = resp.read().decode("utf-8")
                    data = json.loads(body)
                    assert "messages" in data
                    msgs = data["messages"]
                    assert isinstance(msgs, list)
                    assert len(msgs) <= 60, f"HTTP live returned oversized messages: {len(msgs)}"
                    for m in msgs:
                        assert "role" in m and "content" in m and "time" in m
                        assert time_regex.match(m["time"])
                        # Verify payload checksum if structured
                        content = m["content"]
                        if "TID=" in content:
                            parts = dict(kv.split("=") for kv in content.split("|"))
                            t = int(parts["TID"])
                            s = int(parts["SEQ"])
                            c = int(parts["CHK"])
                            assert c == t * 10000 + s, f"Torn message content: {content}"
                with _LIVE_SESSION_LOCK:
                    total_http_reads[0] += 1
                time.sleep(0.001)
        except Exception as exc:
            errors.append(f"HTTPLive-{tid} error: {exc}")

    def snapshot_worker(tid: int):
        try:
            barrier.wait()
            limits = [None, -5, 0, 1, 10, 30, 59, 60, 61, 1000]
            for i in range(30):
                lim = limits[i % len(limits)]
                snap = get_live_messages_snapshot(limit=lim)
                assert isinstance(snap, list)
                expected_max = 60 if (lim is None or lim > 60) else (0 if lim <= 0 else lim)
                assert len(snap) <= expected_max, f"Snapshot exceeded limit {lim}: {len(snap)}"
                for m in snap:
                    assert "role" in m and "content" in m and "time" in m
                    assert time_regex.match(m["time"])
                with _LIVE_SESSION_LOCK:
                    total_snapshot_reads[0] += 1
                time.sleep(0.001)
        except Exception as exc:
            errors.append(f"Snapshot-{tid} error: {exc}")

    # Build 50 threads:
    # 15 Appenders, 10 Clearers, 15 HTTP Live readers, 10 Snapshot readers
    threads: List[threading.Thread] = []
    for i in range(15):
        threads.append(threading.Thread(target=appender_worker, args=(i,)))
    for i in range(10):
        threads.append(threading.Thread(target=clearer_worker, args=(i,)))
    for i in range(15):
        threads.append(threading.Thread(target=http_live_worker, args=(i,)))
    for i in range(10):
        threads.append(threading.Thread(target=snapshot_worker, args=(i,)))

    start_time = time.perf_counter()
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15.0)
            assert not t.is_alive(), f"Thread {t.name} DEADLOCKED and failed to finish within 15s"
    finally:
        stop_event.set()
        checker_thread.join(timeout=2.0)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=3.0)

    elapsed = time.perf_counter() - start_time

    # Ensure no errors occurred
    assert not errors, f"Contention stress test encountered errors:\n" + "\n".join(errors[:20])
    with _LIVE_SESSION_LOCK:
        assert len(LIVE_SESSION_MESSAGES) <= 60
        assert max_len_observed[0] <= 60

    print(
        f"\n  [√] 50-thread stress completed in {elapsed:.2f}s | "
        f"Appends: {total_appends[0]}, Clears: {total_clears[0]}, "
        f"HTTP Reads: {total_http_reads[0]}, Snapshots: {total_snapshot_reads[0]} | "
        f"Max buffer: {max_len_observed[0]}/60"
    )


def test_gateway_http_clear_and_live_interleaving_30_threads():
    """Hammer HTTP server with 30 concurrent threads strictly interleaving POST /api/clear and GET /api/live."""
    handler_class = create_gateway_handler(token="")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
    server_port = server.server_address[1]

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    base_url = f"http://127.0.0.1:{server_port}"
    errors: List[str] = []

    num_threads = 30
    barrier = threading.Barrier(num_threads)

    def http_clearer(tid: int):
        try:
            barrier.wait()
            for _ in range(25):
                req = urllib.request.Request(f"{base_url}/api/clear", data=b"", method="POST")
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    assert resp.status == 200
                    body = resp.read().decode("utf-8")
                    data = json.loads(body)
                    assert data.get("status") == "cleared"
                time.sleep(0.001)
        except Exception as exc:
            errors.append(f"HTTPClearer-{tid} error: {exc}")

    def http_poller(tid: int):
        try:
            barrier.wait()
            for _ in range(35):
                req = urllib.request.Request(f"{base_url}/api/live")
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    assert resp.status == 200
                    body = resp.read().decode("utf-8")
                    data = json.loads(body)
                    assert "messages" in data
                    assert len(data["messages"]) <= 60
                time.sleep(0.001)
        except Exception as exc:
            errors.append(f"HTTPPoller-{tid} error: {exc}")

    def direct_appender(tid: int):
        try:
            barrier.wait()
            for seq in range(40):
                append_live_message(f"worker_{tid}", f"msg_{seq}")
                time.sleep(0.0005)
        except Exception as exc:
            errors.append(f"DirectAppender-{tid} error: {exc}")

    threads: List[threading.Thread] = []
    for i in range(10):
        threads.append(threading.Thread(target=http_clearer, args=(i,)))
    for i in range(12):
        threads.append(threading.Thread(target=http_poller, args=(i,)))
    for i in range(8):
        threads.append(threading.Thread(target=direct_appender, args=(i,)))

    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15.0)
            assert not t.is_alive(), f"Thread {t.name} deadlocked"
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=3.0)

    assert not errors, f"HTTP clear & live interleaving encountered errors:\n" + "\n".join(errors[:20])


def test_gateway_snapshot_boundary_limits_under_lock():
    """Verify get_live_messages_snapshot edge-case limits under mutation."""
    clear_live_messages()
    for i in range(35):
        append_live_message("bot", f"msg_{i}")

    # Boundary tests
    assert len(get_live_messages_snapshot(limit=0)) == 0
    assert len(get_live_messages_snapshot(limit=-1)) == 0
    assert len(get_live_messages_snapshot(limit=-999999)) == 0
    assert len(get_live_messages_snapshot(limit=None)) == 35
    assert len(get_live_messages_snapshot(limit=1)) == 1
    assert len(get_live_messages_snapshot(limit=35)) == 35
    assert len(get_live_messages_snapshot(limit=36)) == 35
    assert len(get_live_messages_snapshot(limit=1000)) == 35

    # Check isolation: snapshot modifying does not affect internal list
    snap = get_live_messages_snapshot(limit=5)
    snap.clear()
    assert len(get_live_messages_snapshot(limit=5)) == 5


def test_concurrent_session_readers_and_engine_simulation():
    """Stress test concurrent callers querying session history (simulating query_llm_reply in engine.py)

    while other threads are heavily appending and clearing.
    """
    from tools.cli.agent.engine import query_llm_reply

    num_callers = 10
    num_appender = 10
    num_clearer = 5
    barrier = threading.Barrier(num_callers + num_appender + num_clearer)
    errors: List[str] = []

    # Mock query_llm_reply's network call to return fast dummy response
    mock_resp_obj = {"choices": [{"message": {"content": "私教回复测试成功"}}]}
    mock_resp_bytes = json.dumps(mock_resp_obj).encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.read.return_value = mock_resp_bytes

    def caller_worker(tid: int):
        try:
            barrier.wait()
            for _ in range(20):
                # query_llm_reply reads LIVE_SESSION_MESSAGES[-6:] internally
                res = query_llm_reply(f"并发测试提问-{tid}", cfg={"active_subject": "politics"})
                assert "私教" in res or "提问" in res
                time.sleep(0.001)
        except Exception as exc:
            errors.append(f"Caller-{tid} error: {exc}")

    def appender_worker(tid: int):
        try:
            barrier.wait()
            for seq in range(40):
                append_live_message("user", f"并发提问-{tid}-{seq}")
                time.sleep(0.0005)
        except Exception as exc:
            errors.append(f"Appender-{tid} error: {exc}")

    def clearer_worker(tid: int):
        try:
            barrier.wait()
            for _ in range(15):
                clear_live_messages()
                time.sleep(0.001)
        except Exception as exc:
            errors.append(f"Clearer-{tid} error: {exc}")

    with patch("tools.cli.agent.engine.safe_urlopen", return_value=mock_resp):
        with patch("tools.cli.agent.engine.load_config", return_value={"api_key": "dummy_key", "base_url": "https://api.test.com/v1", "model": "test"}):
            threads = (
                [threading.Thread(target=caller_worker, args=(i,)) for i in range(num_callers)]
                + [threading.Thread(target=appender_worker, args=(i,)) for i in range(num_appender)]
                + [threading.Thread(target=clearer_worker, args=(i,)) for i in range(num_clearer)]
            )
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10.0)
                assert not t.is_alive(), f"Thread {t.name} timed out"

    assert not errors, f"Concurrent engine query encountered errors:\n" + "\n".join(errors)


# ============================================================================
# TARGET 2: Decompression Adversarial Tests on tools/llm_client.py
# ============================================================================

def test_decompression_truncated_gzip_matrix():
    """Adversarially challenge _decompress_response_bytes with truncated gzip payloads."""
    full_text = "考研政治唯物辩证法核心考点精讲" * 20
    valid_gz = gzip.compress(full_text.encode("utf-8"))

    truncated_cases = [
        b"\x1f",                          # 1 byte
        b"\x1f\x8b",                      # Magic bytes only (2 bytes)
        b"\x1f\x8b\x08",                  # 3 bytes
        b"\x1f\x8b\x08\x00",              # Header start (4 bytes)
        valid_gz[:10],                    # Partial header
        valid_gz[: len(valid_gz) // 4],   # 25% truncated
        valid_gz[: len(valid_gz) // 2],   # 50% truncated
        valid_gz[: len(valid_gz) - 8],    # Missing CRC32/ISIZE footer (last 8 bytes stripped)
        valid_gz[: len(valid_gz) - 1],    # Missing 1 byte from footer
    ]

    for idx, case in enumerate(truncated_cases):
        # 1. With Content-Encoding: gzip header
        res1 = _decompress_response_bytes(case, {"Content-Encoding": "gzip"})
        assert isinstance(res1, str), f"Case {idx} with header failed to return str: {type(res1)}"

        # 2. Without header (relying on magic bytes detection)
        res2 = _decompress_response_bytes(case, {})
        assert isinstance(res2, str), f"Case {idx} without header failed to return str: {type(res2)}"

        # 3. Headers is None
        res3 = _decompress_response_bytes(case, None)
        assert isinstance(res3, str), f"Case {idx} with None headers failed to return str: {type(res3)}"


def test_decompression_corrupt_zlib_and_deflate_matrix():
    """Adversarially challenge _decompress_response_bytes with corrupt zlib and deflate payloads."""
    full_text = "考研英语长难句同位语从句与定语从句深度辨析" * 15
    valid_zlib = zlib.compress(full_text.encode("utf-8"))

    # Craft payloads that match zlib header checksum ((b0 * 256 + b1) % 31 == 0) but have corrupted body
    zlib_header_corrupt = b"\x78\x9c" + os.urandom(128)
    zlib_header_truncated = b"\x78\x9c"

    # Corrupt valid zlib by flipping middle bytes
    corrupted_mid = bytearray(valid_zlib)
    for i in range(5, min(25, len(corrupted_mid))):
        corrupted_mid[i] ^= 0xFF
    corrupted_mid_bytes = bytes(corrupted_mid)

    # Corrupt valid zlib by tampering with Adler-32 checksum (last 4 bytes)
    corrupted_adler = bytearray(valid_zlib)
    corrupted_adler[-4:] = b"\x00\x00\x00\x00"
    corrupted_adler_bytes = bytes(corrupted_adler)

    cases = [
        zlib_header_corrupt,
        zlib_header_truncated,
        corrupted_mid_bytes,
        corrupted_adler_bytes,
        b"\x78\x01\x00\x00\x00\x00",
        b"\x78\xda" + b"\xff" * 64,
    ]

    for idx, case in enumerate(cases):
        # 1. With Content-Encoding: deflate
        res1 = _decompress_response_bytes(case, {"Content-Encoding": "deflate"})
        assert isinstance(res1, str), f"Corrupt zlib case {idx} with header failed"

        # 2. Without header (relying on 0x78 magic check)
        res2 = _decompress_response_bytes(case, {})
        assert isinstance(res2, str), f"Corrupt zlib case {idx} without header failed"


def test_decompression_raw_deflate_without_headers():
    """Test raw deflate (-zlib.MAX_WBITS) with and without headers."""
    text = "考研数学线性代数特征值与特征向量相似对角化"
    comp = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    raw_deflate_bytes = comp.compress(text.encode("utf-8")) + comp.flush()

    # 1. Explicit header: deflate
    res_header = _decompress_response_bytes(raw_deflate_bytes, {"Content-Encoding": "deflate"})
    assert res_header == text

    # 2. No header at all (relying on raw deflate fallback in UnicodeDecodeError handler)
    res_no_header = _decompress_response_bytes(raw_deflate_bytes, {})
    assert res_no_header == text

    # 3. None header
    res_none_header = _decompress_response_bytes(raw_deflate_bytes, None)
    assert res_none_header == text


def test_decompression_double_compression_matrix():
    """Challenge _decompress_response_bytes with double compression layers."""
    text = "考研专业课真题深度串讲"
    raw_utf8 = text.encode("utf-8")

    gz_gz = gzip.compress(gzip.compress(raw_utf8))
    zl_gz = zlib.compress(gzip.compress(raw_utf8))
    gz_zl = gzip.compress(zlib.compress(raw_utf8))
    zl_zl = zlib.compress(zlib.compress(raw_utf8))

    cases = [
        (gz_gz, {"Content-Encoding": "gzip"}),
        (zl_gz, {"Content-Encoding": "deflate"}),
        (gz_zl, {"Content-Encoding": "gzip"}),
        (zl_zl, {"Content-Encoding": "deflate"}),
        (gz_gz, {}),
        (zl_zl, {}),
    ]

    for idx, (payload, headers) in enumerate(cases):
        res = _decompress_response_bytes(payload, headers)
        assert isinstance(res, str), f"Double compression case {idx} failed to return str"


def test_decompression_empty_and_null_bytes():
    """Test edge cases with empty strings, None, and null byte streams."""
    assert _decompress_response_bytes(b"", {}) == ""
    assert _decompress_response_bytes(b"", {"Content-Encoding": "gzip"}) == ""
    assert _decompress_response_bytes(b"", {"Content-Encoding": "deflate"}) == ""
    assert _decompress_response_bytes(None) == ""  # type: ignore
    assert _decompress_response_bytes("") == ""  # type: ignore

    nulls = b"\x00" * 256
    res_nulls = _decompress_response_bytes(nulls, {})
    assert isinstance(res_nulls, str)
    assert len(res_nulls) == 256


def test_decompression_brotli_corrupted_and_truncated():
    """Test brotli decoding resilience when brotli module is present or absent."""
    try:
        import brotli
        valid_br = brotli.compress("考研冲刺提分".encode("utf-8"))
        # Valid decompression
        assert _decompress_response_bytes(valid_br, {"Content-Encoding": "br"}) == "考研冲刺提分"

        # Truncated brotli
        trunc_br = valid_br[: len(valid_br) // 2]
        res_trunc = _decompress_response_bytes(trunc_br, {"Content-Encoding": "br"})
        assert isinstance(res_trunc, str)

        # Corrupted brotli
        corrupt_br = b"\xce\xb1\x00\xff\xfe" + os.urandom(64)
        res_corrupt = _decompress_response_bytes(corrupt_br, {"Content-Encoding": "brotli"})
        assert isinstance(res_corrupt, str)
    except ImportError:
        # If brotli is not installed, verify it doesn't crash
        res_no_mod = _decompress_response_bytes(b"dummy_br", {"Content-Encoding": "br"})
        assert isinstance(res_no_mod, str)


def test_decompression_non_utf8_binary_noise_and_fuzzing():
    """Fuzz _decompress_response_bytes with randomized binary noise and non-UTF8 multibyte sequences."""
    random.seed(42)

    # 1. Invalid UTF-8 sequences (isolated surrogates, overlong encodings, invalid continuation bytes)
    invalid_utf8_sequences = [
        b"\xff\xfe\xfd\x80\x81",
        b"\xc0\xaf",                      # Overlong 2-byte ASCII
        b"\xe0\x80\xaf",                  # Overlong 3-byte
        b"\xf0\x80\x80\xaf",              # Overlong 4-byte
        b"\xed\xa0\x80",                  # UTF-16 surrogate D800
        b"\xed\xbf\xbf",                  # UTF-16 surrogate DFFF
        b"\xf4\x90\x80\x80",              # Above U+10FFFF
    ]

    for seq in invalid_utf8_sequences:
        res = _decompress_response_bytes(seq, {})
        assert isinstance(res, str)
        assert "\ufffd" in res or len(res) > 0

    # 2. 50 random binary noise chunks of varying lengths
    for i in range(50):
        length = random.randint(1, 2048)
        noise = os.urandom(length)
        enc = random.choice(["gzip", "deflate", "br", "", "identity"])
        headers = {"Content-Encoding": enc} if enc else {}
        res = _decompress_response_bytes(noise, headers)
        assert isinstance(res, str), f"Fuzzing iteration {i} failed for noise length {length}"


def test_decompression_all_9_http_400_fallback_keywords():
    """Exhaustively verify that all 9 recognized HTTP 400 error keywords trigger self-healing retry.

    Keywords:
    - max_tokens, token, tokens, max_completion_tokens, max_output_tokens (strips max_tokens)
    - system, role, 系统, 角色 (merges system into user)
    """
    cfg = {"api_key": "test_api_key", "base_url": "https://api.test.com/v1", "model": "test-model"}

    # 1. Token keywords matrix
    token_keywords = ["max_tokens", "token", "tokens", "max_completion_tokens", "max_output_tokens"]
    for kw in token_keywords:
        calls = []
        def mock_urlopen_token(req, timeout=10.0):
            calls.append(req)
            payload = json.loads(req.data.decode("utf-8"))
            if "max_tokens" in payload:
                err_data = {"error": f"Invalid param: {kw} is rejected"}
                gz_err = gzip.compress(json.dumps(err_data).encode("utf-8"))
                fp = BytesIO(gz_err)
                raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {"Content-Encoding": "gzip"}, fp)
            else:
                resp = MagicMock()
                resp.read.return_value = json.dumps({"choices": [{"message": {"content": f"Success after {kw}"}}]}).encode("utf-8")
                resp.headers = {}
                resp.__enter__.return_value = resp
                return resp

        with patch("tools.llm_client.safe_urlopen", side_effect=mock_urlopen_token):
            res = chat_completion("测试 token 错误", config=cfg, max_tokens=256)
            assert res == f"Success after {kw}", f"Failed fallback for keyword: {kw}"
            assert len(calls) == 2
            assert "max_tokens" not in json.loads(calls[1].data.decode("utf-8"))

    # 2. System/role keywords matrix
    system_keywords = ["system", "role", "系统", "角色"]
    for kw in system_keywords:
        calls = []
        def mock_urlopen_sys(req, timeout=10.0):
            calls.append(req)
            payload = json.loads(req.data.decode("utf-8"))
            msgs = payload.get("messages", [])
            if any(m.get("role") == "system" for m in msgs):
                err_data = {"error": f"Unsupported {kw} prompt configuration"}
                df_err = zlib.compress(json.dumps(err_data, ensure_ascii=False).encode("utf-8"))
                fp = BytesIO(df_err)
                raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {"Content-Encoding": "deflate"}, fp)
            else:
                resp = MagicMock()
                resp.read.return_value = json.dumps({"choices": [{"message": {"content": f"Success after {kw}"}}]}).encode("utf-8")
                resp.headers = {}
                resp.__enter__.return_value = resp
                return resp

        with patch("tools.llm_client.safe_urlopen", side_effect=mock_urlopen_sys):
            input_msgs = [{"role": "system", "content": "系统指导"}, {"role": "user", "content": "学员提问"}]
            res = chat_completion(input_msgs, config=cfg)
            assert res == f"Success after {kw}", f"Failed fallback for system keyword: {kw}"
            assert len(calls) == 2
            req2_msgs = json.loads(calls[1].data.decode("utf-8")).get("messages", [])
            assert not any(m.get("role") == "system" for m in req2_msgs)


def test_chat_completion_adversarial_resilience_matrix():
    """Adversarially challenge chat_completion and call_llm_sync with all corrupted/malformed responses.

    Verify that chat_completion NEVER raises an unhandled exception and gracefully returns None or string.
    """
    cfg = {"api_key": "test_api_key", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"}

    def make_mock_http_error(code: int, raw_bytes: bytes, headers: dict) -> urllib.error.HTTPError:
        fp = BytesIO(raw_bytes)
        return urllib.error.HTTPError("https://api.deepseek.com/v1/chat/completions", code, "Error", headers, fp)

    # 1. 200 OK with truncated gzip body
    mock_resp_trunc_gz = MagicMock()
    mock_resp_trunc_gz.__enter__.return_value = mock_resp_trunc_gz
    mock_resp_trunc_gz.read.return_value = b"\x1f\x8b\x08\x00truncated_gzip_stream"
    mock_resp_trunc_gz.headers = {"Content-Encoding": "gzip"}

    with patch("tools.llm_client.safe_urlopen", return_value=mock_resp_trunc_gz):
        res = chat_completion("测试 200 OK 截断 gzip", config=cfg)
        assert res is None, "Non-JSON truncated gzip should safely return None without crashing"

    # 2. 200 OK with corrupt zlib body
    mock_resp_corrupt_zlib = MagicMock()
    mock_resp_corrupt_zlib.__enter__.return_value = mock_resp_corrupt_zlib
    mock_resp_corrupt_zlib.read.return_value = b"\x78\x9c\xff\xff\xff\xff"
    mock_resp_corrupt_zlib.headers = {"Content-Encoding": "deflate"}

    with patch("tools.llm_client.safe_urlopen", return_value=mock_resp_corrupt_zlib):
        res = chat_completion("测试 200 OK 损坏 zlib", config=cfg)
        assert res is None

    # 3. 200 OK with HTML error page (e.g. Cloudflare / Nginx 502/504 HTML)
    mock_resp_html = MagicMock()
    mock_resp_html.__enter__.return_value = mock_resp_html
    mock_resp_html.read.return_value = b"<html><head><title>502 Bad Gateway</title></head><body>502</body></html>"
    mock_resp_html.headers = {"Content-Type": "text/html"}

    with patch("tools.llm_client.safe_urlopen", return_value=mock_resp_html):
        res = chat_completion("测试 200 HTML 页面", config=cfg)
        assert res is None, "HTML response should be safely discarded"

    # 4. 200 OK with pure binary noise
    mock_resp_noise = MagicMock()
    mock_resp_noise.__enter__.return_value = mock_resp_noise
    mock_resp_noise.read.return_value = os.urandom(512)
    mock_resp_noise.headers = {}

    with patch("tools.llm_client.safe_urlopen", return_value=mock_resp_noise):
        res = chat_completion("测试 200 二进制噪声", config=cfg)
        assert res is None

    # 5. 400 Bad Request with truncated Gzip error body (does NOT match max_tokens/system)
    err_trunc_gz = make_mock_http_error(400, b"\x1f\x8b\x08\x00truncated", {"Content-Encoding": "gzip"})
    with patch("tools.llm_client.safe_urlopen", side_effect=err_trunc_gz):
        res = chat_completion("测试 400 截断 gzip", config=cfg)
        assert res is None

    # 6. 400 Bad Request with corrupt Zlib error body
    err_corrupt_zl = make_mock_http_error(400, b"\x78\x9c\x00\x00\xff\xff", {"Content-Encoding": "deflate"})
    with patch("tools.llm_client.safe_urlopen", side_effect=err_corrupt_zl):
        res = chat_completion("测试 400 损坏 zlib", config=cfg)
        assert res is None

    # 7. 400 Bad Request with binary noise error body
    err_noise = make_mock_http_error(400, os.urandom(256), {})
    with patch("tools.llm_client.safe_urlopen", side_effect=err_noise):
        res = chat_completion("测试 400 噪声", config=cfg)
        assert res is None

    # 8. 500 Internal Server Error with truncated Gzip
    err_500 = make_mock_http_error(500, b"\x1f\x8b\x08", {"Content-Encoding": "gzip"})
    with patch("tools.llm_client.safe_urlopen", side_effect=err_500):
        res = chat_completion("测试 500 截断 gzip", config=cfg)
        assert res is None

    # 9. Verify call_llm_sync alias behaves identically
    with patch("tools.llm_client.safe_urlopen", side_effect=err_500):
        res_alias = call_llm_sync("测试 500 别名", config=cfg)
        assert res_alias is None


def test_fetch_upstream_models_adversarial_resilience():
    """Verify fetch_upstream_models safely survives corrupt, truncated, or non-JSON payloads."""
    # 1. 200 OK with truncated gzip
    mock_resp_gz = MagicMock()
    mock_resp_gz.__enter__.return_value = mock_resp_gz
    mock_resp_gz.read.return_value = b"\x1f\x8b\x08\x00garbage"
    mock_resp_gz.headers = {"Content-Encoding": "gzip"}

    with patch("tools.llm_client.safe_urlopen", return_value=mock_resp_gz):
        ok, models, msg = fetch_upstream_models("key", "https://api.test.com/v1")
        assert ok is False
        assert models == []
        assert "网络连接失败" in msg

    # 2. 400 Bad Request with corrupted zlib error body
    fp = BytesIO(b"\x78\x9c\xff\xff\xff\xff")
    headers = {"Content-Encoding": "deflate"}
    http_err = urllib.error.HTTPError("https://api.test.com/v1/models", 400, "Bad Request", headers, fp)

    with patch("tools.llm_client.safe_urlopen", side_effect=http_err):
        ok, models, msg = fetch_upstream_models("key", "https://api.test.com/v1")
        assert ok is False
        assert models == []
        assert "HTTP 400" in msg

    # 3. 200 OK with empty body
    mock_resp_empty = MagicMock()
    mock_resp_empty.__enter__.return_value = mock_resp_empty
    mock_resp_empty.read.return_value = b""
    mock_resp_empty.headers = {}

    with patch("tools.llm_client.safe_urlopen", return_value=mock_resp_empty):
        ok, models, msg = fetch_upstream_models("key", "https://api.test.com/v1")
        assert ok is False
        assert models == []


if __name__ == "__main__":
    pytest.main([__file__, "-vv"])
