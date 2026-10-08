# -*- coding: utf-8 -*-
"""HTTP 退避 / 节流 / 冷却的**单一真源**（AWS 口径，零外部依赖）

[为什么抽这个模块] R3 全矩阵仿真（3 考生 × 6 环节 × 3 端 = 54 格）实测到两类
上游波动：研招网 ``RemoteDisconnected``、搜狗微信 600s 冷却。追因时发现**仓库
存在两套退避公式**：

  * ``tools/search/providers/_http.py``：``min(cap, base·2^(n-1)) + jitter``（加性抖动）
  * ``tools/intelligence/fetcher.py``：``random(0, min(cap, base·2^n))``（full jitter）

同一台机器上两种节奏并存，对反爬站点而言等价于「惊群」——不同步的重试会
互相踩踏，**恰好加重封禁，与退避的目的相反**。业界通行做法是把限流/熔断/重试
收敛到单一实现（Resilience4j 的 ``RateLimiter → CircuitBreaker → Retry →
Timeout`` 组合即依赖同一套参数口径）。本模块即该单一真源。

[三件套与依据]

1. :func:`full_jitter_delay` —— **full jitter**（AWS Architecture Blog,
   Marc Brooker 2015《Exponential Backoff and Jitter》）：``sleep = U(0, min(cap,
   base·2^n))``。论文结论：全抖动比无抖动指数退避**完成更快、总服务器负载更低**；
   无抖动会让所有客户端在同一时刻齐步重试。
2. :class:`TokenBucket` —— **令牌桶限流**（不做则无法约束对同一上游的请求速率）。
3. :class:`CooldownPolicy` —— **指数延长冷却 + 半开探测**（不再是「单次失败即
   冷却 10 分钟」的硬编码）：连续失败越多冷却越长，但留出半开窗口以恢复。

[刻意不做]
  * 不重试「永久错误」：TLS 证书错误、403/404 不在此列（重试无收益且累积请求量）；
    具体判定留给各协议层，本模块只提供重试**间隔**与节流**节拍**。
  * 不引入 tenacity/aiolimiter 等外部依赖：本项目坚持零外部依赖的可移植构建。
"""
from __future__ import annotations

import random
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

__all__ = [
    "RETRY_BASE_DELAY", "RETRY_MAX_DELAY",
    "full_jitter_delay", "retry_after_or_jitter",
    "TokenBucket", "CooldownPolicy",
]

#: 退避默认参数：0.5s 起、8s 封顶。对院校站点的瞬时抖动足够（期望等待约 2.5s），
#: 同时把单次抓取的额外时间上界压在 ~14s 内，不吃掉调用方的时间预算。
RETRY_BASE_DELAY = 0.5
RETRY_MAX_DELAY = 8.0


def full_jitter_delay(attempt: int, *, base: float = RETRY_BASE_DELAY,
                      cap: float = RETRY_MAX_DELAY,
                      rng: Optional[Callable[[float, float], float]] = None) -> float:
    """第 ``attempt`` 次重试前的等待秒数（full jitter）。

    :param attempt: 从 0 开始的尝试序号（0 表示第一次重试）
    :param base: 指数底数
    :param cap: 单次等待上限
    :param rng: 形如 ``random.uniform(a, b)`` 的随机源（测试可注入
        ``random.Random(0).uniform`` 以获得可复现序列）
    """
    ceiling = min(cap, base * (2 ** max(0, int(attempt))))
    source = rng or random.uniform
    return source(0.0, ceiling)


def jitter_ceiling(attempt: int, *, base: float = RETRY_BASE_DELAY,
                   cap: float = RETRY_MAX_DELAY) -> float:
    """:func:`full_jitter_delay` 的等待上界（供预算估算与测试断言）。"""
    return min(cap, base * (2 ** max(0, int(attempt))))


def retry_after_or_jitter(attempt: int, retry_after: Optional[float] = None, *,
                          base: float = RETRY_BASE_DELAY,
                          cap: float = RETRY_MAX_DELAY,
                          max_retry_after: float = 30.0,
                          rng: Optional[Callable[[], float]] = None) -> float:
    """优先遵守服务端 ``Retry-After``，否则退回 full jitter。

    反爬站点（搜狗微信、研招网、Bing）常用 429/503 + ``Retry-After`` 告知冷却时长；
    忽略它会立刻重试并再被拒。``retry_after`` 非法或过大时回落到抖动公式。
    """
    if retry_after is not None:
        try:
            value = float(retry_after)
        except (TypeError, ValueError):
            value = -1.0
        if 0.0 <= value <= max_retry_after:
            return value
    return full_jitter_delay(attempt, base=base, cap=cap, rng=rng)


class TokenBucket:
    """按 ``key``（通常是 host）限流：匀速放行令牌，桶容量允许小突发。

    [为什么需要] 全仓此前**无任何请求间隔控制**（``fetcher.py`` / ``_http.py`` /
    ``school_scout.py`` 都没有），而 bing/ddg/sogou/tavily 四个搜索源共用
    ``_http.get_text`` 一个出口——在那里加桶即可一处生效四源。

    [语义] ``try_acquire(key)`` 返回 ``True`` 表示本次请求可以发出；返回 ``False``
    时调用方应 :meth:`reserve` 取等待秒数并 sleep（**不 sleep 由调用方决定**，
    便于测试与上层按预算截断）。
    """

    def __init__(self, rate_per_sec: float = 1.0, burst: int = 1,
                 *, monotonic: Optional[Callable[[], float]] = None):
        """
        :param rate_per_sec: 每秒放行速率（令牌生成速率）
        :param burst: 桶容量（允许的突发请求数）
        :param monotonic: 时间源（测试可注入）
        """
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec 必须为正")
        if burst < 1:
            raise ValueError("burst 至少为 1")
        self.rate = float(rate_per_sec)
        self.capacity = float(burst)
        self._tokens = float(burst)
        self._last = (monotonic or time.monotonic)()
        self._monotonic = monotonic or time.monotonic
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = self._monotonic()
        elapsed = max(0.0, now - self._last)
        self._last = now
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)

    def try_acquire(self, key: str = "") -> bool:
        """尝试取 1 个令牌（``key`` 保留以便将来做多桶；当前全局单桶语义）。"""
        with self._lock:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False

    def reserve(self, key: str = "") -> float:
        """取不到令牌时，返回需要等待的秒数（不 sleep）。"""
        with self._lock:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return 0.0
            return (1.0 - self._tokens) / self.rate

    def throttle(self, key: str = "") -> float:
        """确保放行：必要时阻塞到有令牌，返回实际等待秒数。"""
        waited = 0.0
        while not self.try_acquire(key):
            delay = self.reserve(key)
            if delay <= 0:
                break
            time.sleep(delay)
            waited += delay
        return waited


class CooldownPolicy:
    """连续失败的**指数延长冷却 + 半开探测**（替代「单次失败即冷却 600s」）。

    [为什么改] 现行 ``tools/search/health.py`` 是「任一源失败一次 → 全源冷却
    600 秒」的进程内硬编码：一次网络抖动会让该源在 10 分钟内彻底不参与检索，
    表现为「公众号检索 0 篇」「全源冷却 → 模型空转」。R3 实测正是这个形态。

    [新语义]
      * **连续失败**达 ``failure_threshold``（默认 2，抵消单次抖动）才进冷却；
      * 冷却时长按失败次数**指数延长**：``base · 2^(n-1)``，封顶 ``max_seconds``；
      * 冷却期满进入**半开**：放行一次探测；成功则清零，失败则继续冷却；
      * 状态可在进程间**持久化**（:meth:`snapshot` / :meth:`restore`），
        解决「进程内 dict 导致重启即遗忘、反复踩同一堵墙」。
    """

    def __init__(self, *, base_seconds: float = 120.0, max_seconds: float = 1800.0,
                 failure_threshold: int = 2, monotonic: Optional[Callable[[], float]] = None,
                 time_source: Optional[Callable[[], float]] = None):
        """
        :param base_seconds: 首次冷却时长
        :param max_seconds: 冷却时长上限
        :param failure_threshold: 进入冷却所需的连续失败次数
        :param monotonic: 单调时间源（冷却计算用它，避免系统时间回拨）
        :param time_source: 墙钟时间源（仅用于持久化的可读时间戳）
        """
        self.base = float(base_seconds)
        self.max_seconds = float(max_seconds)
        self.threshold = max(1, int(failure_threshold))
        self._mono = monotonic or time.monotonic
        self._wall = time_source or time.time
        self._lock = threading.Lock()
        # key → {"until": 单调时刻, "fails": 连续失败次数, "half_open": bool}
        self._state: Dict[str, Dict[str, object]] = {}

    # ── 查询 ────────────────────────────────────────────────
    def failures(self, key: str) -> int:
        with self._lock:
            return int(self._state.get(key, {}).get("fails", 0))

    def is_cooling(self, key: str) -> bool:
        """``key`` 是否处于冷却（含半开前的冷却期）。"""
        with self._lock:
            st = self._state.get(key)
            if not st:
                return False
            if st.get("half_open"):
                return False
            return self._mono() < float(st.get("until", 0.0))

    def remaining(self, key: str) -> float:
        """剩余冷却秒数（0 表示不在冷却）。"""
        with self._lock:
            st = self._state.get(key)
            if not st or st.get("half_open"):
                return 0.0
            return max(0.0, float(st.get("until", 0.0)) - self._mono())

    def message(self, key: str) -> str:
        """面向考生的话术（保留 R3 仿真观察到的「冷却中（约 N 秒后…）」形态）。"""
        left = int(round(self.remaining(key)))
        if left <= 0:
            return ""
        return (f"冷却中（约 {left} 秒后自动恢复；此前被反爬/失败拦截，"
                f"暂不再请求）")

    # ── 状态转移 ────────────────────────────────────────────
    def record_failure(self, key: str) -> float:
        """记一次失败；达到阈值则进入冷却并返回冷却秒数（0=未冷却）。"""
        with self._lock:
            st = self._state.setdefault(key, {"until": 0.0, "fails": 0,
                                              "half_open": False})
            fails = int(st.get("fails", 0)) + 1
            st["fails"] = fails
            # [P1 修复·2026-10-08 S2] 半开探测在途时的失败必须**强制**重新冷却：
            # 探测结果回来了说明源仍不可用，若等它再凑满阈值，半开名额会悬空
            # （调用方已消费名额却得不到冷却，源既不在冷却也不再被放行=静默死锁）。
            # ``max(0, …)`` 保证强制冷却时 span 不低于 base（红线：首次 ≥ base）。
            if fails < self.threshold and not st.get("half_open"):
                return 0.0
            span = min(self.max_seconds,
                       self.base * (2 ** max(0, fails - self.threshold)))
            st["until"] = self._mono() + span
            st["half_open"] = False
            return span

    def record_success(self, key: str) -> None:
        """成功即清零（退出冷却与半开）。"""
        with self._lock:
            self._state.pop(key, None)

    def force_cooldown(self, key: str) -> float:
        """**立即**进入冷却（不等 ``failure_threshold``），返回冷却秒数。

        [为什么需要这条独立入口] ``record_failure`` 的阈值语义是为「网络抖动」设计的
        —— 单次超时不该让源停摆 10 分钟。但**反爬拦截是确定性判定**：页面里出现验证码 /
        ``SourceVerifyCode`` / 限流特征时，源确实正在封我们，此时再等第二次失败只会
        多挨一次封。因此把两类信号分开：

          * :meth:`record_failure` —— 疑似抖动，攒够 ``failure_threshold`` 次才冷却；
          * :meth:`force_cooldown` —— 已判定被拦，**首次即冷却**，时长不低于 ``base``。

        [时长口径] 首次 ``base``，之后按 ``base·2^(n-1)`` 指数延长、封顶 ``max_seconds``；
        因此「首次冷却 ≥ 600s」这类红线由 ``base`` 一个参数保证，不会因阈值而缩水。
        """
        with self._lock:
            st = self._state.setdefault(key, {"until": 0.0, "fails": 0,
                                              "half_open": False})
            fails = int(st.get("fails", 0)) + 1
            st["fails"] = fails
            span = min(self.max_seconds, self.base * (2 ** (fails - 1)))
            st["until"] = self._mono() + span
            st["half_open"] = False
            return span

    def try_half_open(self, key: str) -> bool:
        """冷却期满后放行**一次**探测；成功请立刻 :meth:`record_success`。

        [为什么要「只放一次」] 半开若不限制次数，并发请求会同时全部放行 —— 那等于
        冷却从未生效（这正是「惊群」要避免的）。故 :attr:`half_open` 为真时直接拒绝。

        [P1 修复·2026-10-08 S2] 从未进入冷却的键（``until`` 为 0，如
        ``record_failure`` 记过一次但未达阈值的疑似抖动）直接放行且**不占用**
        半开名额：它们没有「冷却期满」的语义，若一并消费，正常源在并发下会被
        错误地限成单探测。
        """
        with self._lock:
            st = self._state.get(key)
            if not st:
                return True
            until = float(st.get("until", 0.0))
            if until <= 0.0:
                return True          # 从未进入冷却 → 正常放行，不占用半开名额
            if self._mono() < until:
                return False
            if st.get("half_open"):
                return False   # 半开名额已用掉，等这次探测的结果
            st["half_open"] = True   # 放行这一次探测
            return True

    # ── 持久化（跨进程记忆冷却，避免重启即遗忘）────────────
    def snapshot(self) -> Dict[str, Any]:
        """导出为可 JSON 序列化的 ``{"stored_at": 墙钟, "cooling": {key: 剩余秒数}}``。

        [P0-6 修复·冷却跨进程冻结] 此前只导出 ``{key: 剩余秒数}``（**无写入
        时间**），:meth:`restore` 只能把剩余秒数当作「从当前时钟起算」——每次
        新进程启动都给旧冷却**续满**（实测：11:11 写盘的 600 秒在 15:37 的新
        进程里原样存活，4 小时流逝未被折算）。stored_at 让 restore 能按真实
        流逝时间扣减。仅含仍在冷却的键。
        """
        out: Dict[str, float] = {}
        for key in list(self._state):
            left = self.remaining(key)
            if left > 0:
                out[key] = left
        return {"stored_at": self._wall(), "cooling": out}

    def restore(self, data: Dict[str, Any]) -> None:
        """从 :meth:`snapshot` 的结果恢复，**按快照写入时间折算**已流逝秒数。

        新格式 ``{"stored_at": 墙钟, "cooling": {key: 剩余}}`` → 逐键
        ``max(0, 剩余 - (now - stored_at))``；折算到 0 的键自然过期（不写入）。
        旧格式（平铺 ``{key: 剩余}``，无写入时间）无法判断已流逝多久——
        **整批丢弃**：宁可提前放行一次半开探测，也不让 600 秒冷却跨天存活
        （这正是本修复要消灭的形态）。时钟回拨时 ``now < stored_at`` 按 0
        折算（保守：冷却不因回拨而缩短）。
        """
        if not isinstance(data, dict):
            return
        cooling = data.get("cooling")
        stored_at = data.get("stored_at")
        if not isinstance(cooling, dict) or not cooling:
            return
        try:
            elapsed = max(0.0, self._wall() - float(stored_at))
        except (TypeError, ValueError):
            return  # 无写入时间戳（旧格式）→ 无法折算，整批丢弃
        now = self._mono()
        for key, left in cooling.items():
            try:
                left_f = float(left) - elapsed
            except (TypeError, ValueError):
                continue
            if left_f > 0:
                self._state[key] = {"until": now + left_f, "fails": self.threshold,
                                    "half_open": False}
