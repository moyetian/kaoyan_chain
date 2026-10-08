# -*- coding: utf-8 -*-
"""``tools/http_backoff.py`` 单一真源测试：full jitter / 令牌桶 / 冷却策略

为什么这组用例重要：R3 仿真暴露「同一台机器两套退避节奏」与「单次失败即冷却
10 分钟」两个问题，本模块是它们的唯一修复点。用例须钉住**语义**（不重试的
不放行、冷却不误伤、半开只放一次、跨进程可恢复），而不只是数值。
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.http_backoff import (  # noqa: E402
    CooldownPolicy, TokenBucket, full_jitter_delay, jitter_ceiling,
    retry_after_or_jitter,
)


class _Clock:
    """可手工推进的单调时钟（避免测试真等）。"""

    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> float:
        self.t += dt
        return self.t


# ── full jitter ────────────────────────────────────────────────────────

def test_full_jitter_within_bounds_and_growing_ceiling():
    for attempt in range(6):
        ceiling = jitter_ceiling(attempt)
        rng = random.Random(attempt)
        for _ in range(200):
            d = full_jitter_delay(attempt, rng=rng.uniform)
            assert 0.0 <= d <= ceiling + 1e-9
        if attempt:
            assert ceiling >= jitter_ceiling(attempt - 1)


def test_full_jitter_caps_at_max_delay():
    assert jitter_ceiling(20) == 8.0
    assert full_jitter_delay(20, rng=random.Random(0).uniform) <= 8.0


def test_full_jitter_is_reproducible_with_injected_rng():
    a = full_jitter_delay(2, rng=random.Random(42).uniform)
    b = full_jitter_delay(2, rng=random.Random(42).uniform)
    assert a == b, "注入固定随机源后必须可复现（否则测试与排障都不可重复）"


# ── Retry-After 优先 ──────────────────────────────────────────────────

def test_retry_after_wins_when_valid():
    assert retry_after_or_jitter(3, retry_after=7.0) == 7.0


@pytest.mark.parametrize("bad", [None, -1.0, float("nan"), 1e9, "abc"])
def test_retry_after_falls_back_to_jitter_when_invalid(bad):
    got = retry_after_or_jitter(1, retry_after=bad)
    assert 0.0 <= got <= jitter_ceiling(1) + 1e-9


# ── 令牌桶 ────────────────────────────────────────────────────────────

def test_token_bucket_limits_rate():
    clk = _Clock()
    bucket = TokenBucket(rate_per_sec=1.0, burst=2, monotonic=clk)
    assert bucket.try_acquire() and bucket.try_acquire(), "桶容量内的突发应放行"
    assert not bucket.try_acquire(), "超出桶容量必须拒绝（否则等于没限流）"
    wait = bucket.reserve()
    assert 0.0 < wait <= 1.0
    clk.advance(1.0)
    assert bucket.try_acquire(), "时间推进后应恢复放行"


def test_token_bucket_throttle_waits_and_returns_elapsed():
    clk = _Clock()
    bucket = TokenBucket(rate_per_sec=10.0, burst=1, monotonic=clk)
    assert bucket.try_acquire()
    slept: list[float] = []
    import tools.http_backoff as mod
    real_sleep, mod.time.sleep = mod.time.sleep, lambda s: (slept.append(s), clk.advance(s))
    try:
        waited = bucket.throttle()
    finally:
        mod.time.sleep = real_sleep
    assert waited > 0 and slept, "throttle 应阻塞到有令牌并返回等待秒数"
    assert not bucket._tokens or bucket._tokens >= 0.0  # 状态自洽


def test_token_bucket_rejects_bad_params():
    with pytest.raises(ValueError):
        TokenBucket(rate_per_sec=0)
    with pytest.raises(ValueError):
        TokenBucket(rate_per_sec=1.0, burst=0)


# ── 冷却策略 ──────────────────────────────────────────────────────────

def test_single_failure_does_not_cool_down():
    """核心行为变更：单次网络抖动**不再**直接进冷却（原实现是「一次失败即 600s」）。"""
    p = CooldownPolicy(base_seconds=120.0, failure_threshold=2, monotonic=_Clock())
    assert p.record_failure("sogou") == 0.0
    assert not p.is_cooling("sogou")


def test_threshold_failure_enters_exponential_cooldown():
    clk = _Clock()
    p = CooldownPolicy(base_seconds=100.0, max_seconds=400.0,
                       failure_threshold=2, monotonic=clk)
    assert p.record_failure("sogou") == 0.0
    assert p.record_failure("sogou") == 100.0
    assert p.is_cooling("sogou")
    assert 99.0 <= p.remaining("sogou") <= 100.0
    # 继续失败 → 指数延长（100 → 200），封顶 400
    clk.advance(101)
    assert p.record_failure("sogou") == 200.0
    for _ in range(5):
        clk.advance(1)
        p.record_failure("sogou")
    assert p.remaining("sogou") <= 400.0


def test_cooldown_message_keeps_player_facing_shape():
    p = CooldownPolicy(base_seconds=120.0, failure_threshold=1, monotonic=_Clock())
    p.record_failure("bing")
    msg = p.message("bing")
    assert "冷却中" in msg and "秒后自动恢复" in msg
    clk_msg = p.message("never-failed")
    assert clk_msg == ""


def test_success_clears_cooldown():
    p = CooldownPolicy(base_seconds=60.0, failure_threshold=1, monotonic=_Clock())
    p.record_failure("ddg")
    assert p.is_cooling("ddg")
    p.record_success("ddg")
    assert not p.is_cooling("ddg") and p.failures("ddg") == 0


def test_half_open_allows_exactly_one_probe():
    clk = _Clock()
    p = CooldownPolicy(base_seconds=60.0, failure_threshold=1, monotonic=clk)
    p.record_failure("tavily")
    assert not p.try_half_open("tavily"), "冷却期内不得放行"
    clk.advance(61)
    assert p.try_half_open("tavily"), "冷却期满应放行一次探测"
    assert not p.try_half_open("tavily"), "半开期间只放行一次（防并发雪崩）"
    p.record_success("tavily")
    assert not p.is_cooling("tavily")


def test_snapshot_restore_survives_process_restart():
    """跨进程记忆：冷却状态可持久化（进程内 dict 的原缺陷是「重启即遗忘」）。"""
    clk = _Clock()
    wall = _Clock(1_700_000_000.0)   # 墙钟（快照 stored_at 用；P0-6 起 snapshot 带时间戳）
    p = CooldownPolicy(base_seconds=300.0, failure_threshold=1, monotonic=clk,
                       time_source=wall)
    p.record_failure("sogou")
    snap = p.snapshot()
    # [P0-6 修复 2026-10-08] snapshot 结构升级为 {"stored_at", "cooling"}：
    # stored_at 让 restore 按真实流逝折算，不再给旧冷却续满。
    assert "sogou" in snap["cooling"] and 0 < snap["cooling"]["sogou"] <= 300.0

    clk2 = _Clock(1000.0)          # 新进程：单调整体前移，冷却仍在
    p2 = CooldownPolicy(base_seconds=300.0, failure_threshold=1, monotonic=clk2,
                        time_source=wall)
    p2.restore(snap)
    assert p2.is_cooling("sogou"), "重启后应记得仍在冷却（避免反复撞同一堵墙）"
    assert p2.remaining("sogou") <= snap["cooling"]["sogou"] + 1e-6
