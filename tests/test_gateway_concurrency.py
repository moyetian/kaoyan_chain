# -*- coding: utf-8 -*-
"""
tests/test_gateway_concurrency.py

Comprehensive concurrency and race condition test suite for tools/cli/gateway.py
Validates Requirement R3:
- Thread-safe concurrency protection for LIVE_SESSION_MESSAGES via _LIVE_SESSION_LOCK (threading.RLock).
- Strictly bounded window of max 60 messages during concurrent appends.
- Thread-safe snapshot copy creation in get_live_messages_snapshot and GET /api/live.
- Atomic /api/clear operations.
- Zero RuntimeError (e.g. dictionary changed size during iteration, list modified during iteration).
- Zero race conditions and zero torn data across 20+ concurrent threads.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from typing import Any, Dict, List

import pytest

from tools.cli.gateway import (
    _LIVE_SESSION_LOCK,
    LIVE_SESSION_MESSAGES,
    append_live_message,
    clear_live_messages,
    create_gateway_handler,
    get_live_messages_snapshot,
)


@pytest.fixture(autouse=True)
def clean_live_session():
    """Ensure clean message buffer before and after each test."""
    clear_live_messages()
    yield
    clear_live_messages()


def test_lock_and_getter_contract():
    """Verify lock definition, type, and get_live_messages_snapshot isolation semantics."""
    # 1. Verify _LIVE_SESSION_LOCK is an RLock instance
    assert isinstance(_LIVE_SESSION_LOCK, type(threading.RLock()))

    # 2. Populate sample messages
    for i in range(10):
        append_live_message(f"role_{i}", f"message content {i}")

    # 3. Test default limit = 60
    snap = get_live_messages_snapshot()
    assert len(snap) == 10
    assert snap[0]["role"] == "role_0"
    assert snap[-1]["role"] == "role_9"

    # 4. Test specific limits
    snap_5 = get_live_messages_snapshot(limit=5)
    assert len(snap_5) == 5
    assert snap_5[0]["role"] == "role_5"
    assert snap_5[-1]["role"] == "role_9"

    snap_0 = get_live_messages_snapshot(limit=0)
    assert snap_0 == []

    snap_neg = get_live_messages_snapshot(limit=-1)
    assert snap_neg == []

    snap_none = get_live_messages_snapshot(limit=None)  # type: ignore
    assert len(snap_none) == 10

    # 5. Verify snapshot isolation: mutating snapshot does NOT mutate LIVE_SESSION_MESSAGES
    snap[0]["role"] = "MUTATED_ROLE"
    snap[0]["content"] = "MUTATED_CONTENT"

    with _LIVE_SESSION_LOCK:
        assert LIVE_SESSION_MESSAGES[0]["role"] == "role_0"
        assert LIVE_SESSION_MESSAGES[0]["content"] == "message content 0"


def test_bounded_size_under_heavy_concurrency():
    """Verify that 25 concurrent threads appending messages maintain max 60 bounded size."""
    num_threads = 25
    messages_per_thread = 40
    barrier = threading.Barrier(num_threads)
    errors: List[Exception] = []

    def worker(tid: int):
        try:
            barrier.wait()
            for seq in range(messages_per_thread):
                append_live_message(f"agent_{tid}", f"concurrency payload {tid}:{seq}")
                # Periodic verification inside worker
                with _LIVE_SESSION_LOCK:
                    current_len = len(LIVE_SESSION_MESSAGES)
                    assert current_len <= 60, f"Thread {tid} observed oversized buffer: {current_len}"
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent workers encountered errors: {errors}"

    # Final buffer state must be strictly bounded <= 60
    with _LIVE_SESSION_LOCK:
        assert len(LIVE_SESSION_MESSAGES) == 60
        for msg in LIVE_SESSION_MESSAGES:
            assert "role" in msg
            assert "content" in msg
            assert "time" in msg
            assert msg["role"].startswith("agent_")


def test_snapshot_concurrency_no_torn_data():
    """Verify zero torn data and zero RuntimeError across concurrent readers, writers, and clearers."""
    num_writers = 10
    num_readers = 10
    num_clearers = 2
    total_threads = num_writers + num_readers + num_clearers
    barrier = threading.Barrier(total_threads)
    stop_event = threading.Event()
    errors: List[str] = []

    time_pattern = re.compile(r"^\d{2}:\d{2}:\d{2}$")

    def writer(tid: int):
        try:
            barrier.wait()
            for seq in range(80):
                # Write formatted message with internal checksum to detect torn data
                payload = f"THREAD={tid}|SEQ={seq}|HASH={tid * 1000 + seq}"
                append_live_message(f"writer_{tid}", payload)
                time.sleep(0.0005)
        except Exception as exc:
            errors.append(f"Writer-{tid} error: {exc}")

    def clearer(tid: int):
        try:
            barrier.wait()
            while not stop_event.is_set():
                clear_live_messages()
                time.sleep(0.005)
        except Exception as exc:
            errors.append(f"Clearer-{tid} error: {exc}")

    def reader(tid: int):
        try:
            barrier.wait()
            while not stop_event.is_set():
                # Read snapshot and verify data integrity
                snapshot = get_live_messages_snapshot(limit=20)
                assert len(snapshot) <= 20
                for item in snapshot:
                    role = item.get("role", "")
                    content = item.get("content", "")
                    msg_time = item.get("time", "")

                    assert role.startswith("writer_"), f"Torn or corrupted role: {role}"
                    assert time_pattern.match(msg_time), f"Invalid time string: {msg_time}"

                    # Verify internal checksum integrity (no torn / half-written fields)
                    parts = dict(kv.split("=") for kv in content.split("|"))
                    w_tid = int(parts["THREAD"])
                    w_seq = int(parts["SEQ"])
                    w_hash = int(parts["HASH"])
                    assert w_hash == w_tid * 1000 + w_seq, f"Torn data detected: {content}"
                time.sleep(0.001)
        except Exception as exc:
            errors.append(f"Reader-{tid} error: {exc}")

    writer_threads = [threading.Thread(target=writer, args=(i,)) for i in range(num_writers)]
    clearer_threads = [threading.Thread(target=clearer, args=(i,)) for i in range(num_clearers)]
    reader_threads = [threading.Thread(target=reader, args=(i,)) for i in range(num_readers)]

    all_threads = writer_threads + clearer_threads + reader_threads
    for t in all_threads:
        t.start()

    # Wait for writers to complete
    for t in writer_threads:
        t.join()

    # Allow readers and clearers to cycle a bit more under final state
    time.sleep(0.05)
    stop_event.set()

    for t in clearer_threads + reader_threads:
        t.join()

    assert not errors, f"Encountered concurrency errors or torn data:\n" + "\n".join(errors)


def test_http_endpoints_24_concurrent_threads_stress():
    """Hammer HTTP server with 24 concurrent threads testing /api/live, /api/clear, and appends."""
    handler_class = create_gateway_handler(token="")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
    server_port = server.server_address[1]

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    base_url = f"http://127.0.0.1:{server_port}"
    errors: List[str] = []

    # 24 concurrent worker threads:
    # - 8 threads hammering GET /api/live
    # - 8 threads hammering append_live_message
    # - 4 threads hammering POST /api/clear
    # - 4 threads hammering get_live_messages_snapshot
    num_threads = 24
    barrier = threading.Barrier(num_threads)
    stop_event = threading.Event()

    def live_reader(tid: int):
        try:
            barrier.wait()
            for _ in range(40):
                req = urllib.request.Request(f"{base_url}/api/live")
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    assert resp.status == 200
                    assert resp.headers.get("Content-Type", "").startswith("application/json")
                    body = resp.read().decode("utf-8")
                    data = json.loads(body)
                    assert "messages" in data
                    msgs = data["messages"]
                    assert isinstance(msgs, list)
                    assert len(msgs) <= 60
                    for m in msgs:
                        assert "role" in m
                        assert "content" in m
                        assert "time" in m
                time.sleep(0.001)
        except Exception as exc:
            errors.append(f"HTTP LiveReader-{tid} failed: {exc}")

    def message_appender(tid: int):
        try:
            barrier.wait()
            for seq in range(40):
                append_live_message("tutor", f"HTTP Concurrency Check from {tid}-{seq}")
                time.sleep(0.001)
        except Exception as exc:
            errors.append(f"Appender-{tid} failed: {exc}")

    def api_clearer(tid: int):
        try:
            barrier.wait()
            for _ in range(15):
                req = urllib.request.Request(f"{base_url}/api/clear", data=b"", method="POST")
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    assert resp.status == 200
                    body = resp.read().decode("utf-8")
                    data = json.loads(body)
                    assert data.get("status") == "cleared"
                time.sleep(0.003)
        except Exception as exc:
            errors.append(f"HTTP Clearer-{tid} failed: {exc}")

    def snapshot_reader(tid: int):
        try:
            barrier.wait()
            for _ in range(40):
                snap = get_live_messages_snapshot(limit=15)
                assert len(snap) <= 15
                for m in snap:
                    assert "role" in m and "content" in m
                time.sleep(0.001)
        except Exception as exc:
            errors.append(f"SnapshotReader-{tid} failed: {exc}")

    threads: List[threading.Thread] = []
    for i in range(8):
        threads.append(threading.Thread(target=live_reader, args=(i,)))
    for i in range(8):
        threads.append(threading.Thread(target=message_appender, args=(i,)))
    for i in range(4):
        threads.append(threading.Thread(target=api_clearer, args=(i,)))
    for i in range(4):
        threads.append(threading.Thread(target=snapshot_reader, args=(i,)))

    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)
            assert not t.is_alive(), "Worker thread timed out and failed to terminate"
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2.0)

    assert not errors, f"HTTP endpoint concurrency test encountered errors:\n" + "\n".join(errors)


def test_concurrent_clear_and_json_serialization_isolation():
    """Verify that rapid concurrent clear() calls never disrupt JSON serialization in /api/live."""
    stop_event = threading.Event()
    errors: List[str] = []

    def appender():
        try:
            idx = 0
            while not stop_event.is_set():
                append_live_message("user", f"rapid_append_{idx}")
                idx += 1
        except Exception as exc:
            errors.append(f"Rapid appender failed: {exc}")

    def clearer():
        try:
            while not stop_event.is_set():
                clear_live_messages()
        except Exception as exc:
            errors.append(f"Rapid clearer failed: {exc}")

    def serializer():
        try:
            for _ in range(200):
                # Simulates what /api/live does: snapshot under lock, then json.dumps
                with _LIVE_SESSION_LOCK:
                    snapshot = [dict(m) for m in LIVE_SESSION_MESSAGES]
                # If snapshot wasn't isolated or lock failed, json.dumps could throw RuntimeError
                payload = json.dumps({"messages": snapshot}, ensure_ascii=False)
                parsed = json.loads(payload)
                assert isinstance(parsed.get("messages"), list)
        except Exception as exc:
            errors.append(f"Serializer failed: {exc}")

    threads = [
        threading.Thread(target=appender),
        threading.Thread(target=clearer),
        threading.Thread(target=serializer),
        threading.Thread(target=serializer),
    ]

    for t in threads:
        t.start()

    threads[2].join()
    threads[3].join()
    stop_event.set()

    threads[0].join()
    threads[1].join()

    assert not errors, f"Errors in concurrent clear & serialization test:\n" + "\n".join(errors)
