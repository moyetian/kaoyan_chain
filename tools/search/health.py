# -*- coding: utf-8 -*-
"""
Provider 健康度：反爬/失败后的冷却

[由来] 本轮实测触发 DuckDuckGo 限流后，它在一段时间内的每次请求都会再返回验证页，
而且**每试一次都可能让限流更严**。多源联邦下继续重试同一个被挡的源，只是白白增加
延迟与失败噪音。

因此：一个 provider 被判定为「反爬拦截/不可用」后，进入冷却期，期间不再请求它，
并在 `SearchResponse.providers_failed` 里如实说明「冷却中」，让调用方一眼看出是
**暂时不可用**而不是**这个源没数据**。

[R3 仿真修订·2026-10-06] 原实现是「任一源失败一次 → 硬编码 600s、只存在进程内
``_COOLDOWN`` dict」，实测暴露三个缺陷：

  1. **一次网络抖动即停摆 10 分钟**。R3 仿真出现「CLI 抓 5 篇 / 稍后 GUI 抓 0 篇」
     的时序互补：早跑的那次把源标成冷却，后跑的那次就彻底不参与检索。
  2. **重启即遗忘**。``_COOLDOWN`` 是模块级 dict，CLI/GUI/TUI 每次都是新进程 ——
     刚被封的源换个端点再撞一次墙，封禁只会更严。
  3. **600s 是写死的常量**。连打 5 次和偶发 1 次用同一个时长，既不够狠也不够准。

现改为 :class:`tools.http_backoff.CooldownPolicy`（退避/节流/冷却的单一真源）：

  * **首次冷却 ≥ 600s**（:data:`COOLDOWN_SECONDS` 仍是 600，作为 ``base_seconds``
    传入）—— 这是红线。原实现是「单次失败即 600s」，若改成「2 次失败才冷却但首次
    只 120s」，体验反而变差：请求更多 → 更快被封。
  * **连续失败达阈值才冷却**：仅对「疑似抖动」用 ``record_failure``（阈值 2），
    抵消单次超时误伤；而**已判定被反爬拦截**走 ``force_cooldown``（立即、≥600s）。
  * **指数延长 + 半开探测**：连续被拦越久冷却越长（封顶 1 小时），期满放行**一次**
    探测；成功即清零。
  * **落盘跨进程记忆**（:meth:`CooldownPolicy.snapshot` / :meth:`restore`）到
    ``.memory/search_cooldown.json``，避免「换个端点就忘了自己刚被封」。

文案形态（``冷却中（约 N 秒后自动恢复；此前被反爬/失败拦截，暂不再请求）``）
由 ``CooldownPolicy.message()`` 统一提供，与既有 W11 断言保持一致。
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path
from typing import Dict, Optional

try:  # 双导入路径兼容（源码脚本式 / tools 包式）
    from http_backoff import CooldownPolicy
except ImportError:  # pragma: no cover
    from tools.http_backoff import CooldownPolicy  # type: ignore

try:  # 双导入路径兼容
    from ky_io import guard_write
except ImportError:  # pragma: no cover
    from tools.ky_io import guard_write  # type: ignore

try:  # 双导入路径兼容
    from workspace import resolve_workspace_root
except ImportError:  # pragma: no cover
    from tools.workspace import resolve_workspace_root  # type: ignore

_LOG = logging.getLogger(__name__)

#: 首次冷却时长（秒）—— **红线：不得低于 600**（原实现即此值）
COOLDOWN_SECONDS = 600

#: 连续失败时冷却时长的上限（秒）
MAX_COOLDOWN_SECONDS = 3600

#: 「疑似抖动」进入冷却所需的连续失败次数（反爬判定不走这条，见 force_cool）
FAILURE_THRESHOLD = 2

#: 冷却状态落盘位置（``.memory`` 已被 .gitignore 忽略，属本地可弃数据；
#: 与 ``search_cache.json`` 同目录，语义同类：「上一次跑的时候哪些源被挡了」）
COOLDOWN_STATE_FILE = "search_cooldown.json"

_LOCK = threading.Lock()
_POLICY: Optional[CooldownPolicy] = None
_STATE_PATH: Optional[Path] = None
#: 落盘开关的**显式**覆盖（``None`` = 未覆盖，走 :func:`_persist_enabled` 判定）。
_PERSIST_OVERRIDE: Optional[bool] = None


def _persist_enabled() -> bool:
    """当前是否该落盘。

    **测试进程下默认关闭**：``tests/conftest.py`` 的写闸门只盯 ``ky_config.json`` /
    ``ky_history.json``，本文件不在其列；若测试里真的写进真实工作区，会在跑测
    期间凭空造出又删掉一个开发者文件（记忆项：「验证脚本必须隔离」）。
    判据用 ``pytest`` 是否已在 ``sys.modules`` 里——``PYTEST_CURRENT_TEST`` 只在
    测试**执行期**才有值，而模块导入（= 收集期）就早于它。

    需要验证跨进程语义的用例显式 ``set_persistence(True, path=tmp_path/...)``。
    """
    if _PERSIST_OVERRIDE is not None:
        return _PERSIST_OVERRIDE
    return "pytest" not in sys.modules


def _state_path() -> Path:
    global _STATE_PATH
    if _STATE_PATH is None:
        try:
            root = resolve_workspace_root(__file__)
        except Exception:                        # pragma: no cover - 解析失败退回本地
            root = Path(__file__).resolve().parent.parent.parent
        _STATE_PATH = Path(root) / ".memory" / COOLDOWN_STATE_FILE
    return _STATE_PATH


def set_persistence(enabled: bool, path: Optional[Path] = None) -> None:
    """开关冷却状态落盘（测试用；``path`` 可改落点）。"""
    global _PERSIST_OVERRIDE, _STATE_PATH
    with _LOCK:
        _PERSIST_OVERRIDE = bool(enabled)
        if path is not None:
            _STATE_PATH = Path(path)


def policy() -> CooldownPolicy:
    """进程级单例策略（首次调用时从磁盘恢复冷却状态）。"""
    global _POLICY
    with _LOCK:
        if _POLICY is None:
            _POLICY = CooldownPolicy(
                base_seconds=COOLDOWN_SECONDS,
                max_seconds=MAX_COOLDOWN_SECONDS,
                failure_threshold=FAILURE_THRESHOLD,
            )
            if _persist_enabled():
                _restore_locked(_POLICY)
        return _POLICY


def _key(name: str) -> str:
    return str(name or "").strip().lower()


def _restore_locked(pol: CooldownPolicy) -> None:
    path = _state_path()
    try:
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get("cooling") if isinstance(data, dict) else None
        if isinstance(entries, dict):
            pol.restore({str(k): float(v) for k, v in entries.items()})
    except Exception as exc:                     # pragma: no cover - 状态损坏不该影响检索
        _LOG.debug("冷却状态加载失败（按无冷却继续）: %s", exc)


def _persist_locked(pol: CooldownPolicy) -> None:
    if not _persist_enabled():
        return
    path = _state_path()
    try:
        # [safe 模式收口] 与 ``search/cache.py`` 同一口径：先过统一写闸门，
        # 只读模式下连目录都不创建；异常由本方法兜住，表现为「不落盘、检索照常」。
        guard_write("写入检索冷却状态", path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "cooling": pol.snapshot()},
                                   ensure_ascii=False), encoding="utf-8")
    except Exception as exc:                     # pragma: no cover
        _LOG.debug("冷却状态写入失败（忽略）: %s", exc)


def mark_blocked(name: str, reason: str = "") -> None:
    """把一个 provider 标记为「被拦截」，**立即**进入冷却。

    与 :func:`note_failure` 的区别：调用方已经**判定**这是反爬拦截（验证码页 /
    429 / 命中反爬特征），不是网络抖动 —— 这种信号确定性高，等第二次失败只会
    多挨一次封。
    """
    key = _key(name)
    if not key:
        return
    pol = policy()
    span = pol.force_cooldown(key)
    _persist_locked(pol)
    _LOG.info("provider %s 进入冷却 %.0f 秒（原因：%s）", key, span, reason or "未提供")


def note_failure(name: str, reason: str = "") -> float:
    """记一次**疑似抖动**的失败；连续达阈值才冷却，返回冷却秒数（0=未冷却）。

    目前搜索侧尚未调用（``SearchService`` 只在明确反爬时调 :func:`mark_blocked`），
    预留给「普通超时也累计、但要两次才停」的策略，避免一次网络抖动误伤整个源。
    """
    key = _key(name)
    if not key:
        return 0.0
    pol = policy()
    span = pol.record_failure(key)
    if span > 0:
        _persist_locked(pol)
        _LOG.info("provider %s 连续失败进入冷却 %.0f 秒（原因：%s）",
                  key, span, reason or "未提供")
    return span


def mark_healthy(name: str) -> None:
    """该源本轮成功 → 清零失败计数与冷却（半开探测成功后必须调用）。"""
    key = _key(name)
    if not key:
        return
    pol = policy()
    if pol.failures(key):
        pol.record_success(key)
        _persist_locked(pol)


def is_cooling(name: str) -> bool:
    """是否在冷却期内（过期自动进入半开，可放行一次探测）。"""
    key = _key(name)
    if not key:
        return False
    return policy().is_cooling(key)


def cooldown_reason(name: str) -> str:
    """冷却中的说明文案（形态与 W11 既有断言一致）。"""
    key = _key(name)
    if not key:
        return ""
    return policy().message(key)


def cooldown_state() -> Dict[str, float]:
    """当前冷却表快照（``{源: 剩余秒数}``，供测试与诊断）。"""
    return policy().snapshot()


def reset() -> None:
    """清空冷却表（测试用；连带清掉落盘状态，避免污染后续进程）。"""
    global _POLICY
    with _LOCK:
        _POLICY = None
    if not _persist_enabled():
        return
    # 有意用 ``missing_ok=True``：文件不存在不是错误。
    try:
        _state_path().unlink(missing_ok=True)
    except Exception as exc:                     # pragma: no cover
        _LOG.debug("冷却状态清理失败（忽略）: %s", exc)


#: 门面导出用别名（语义更明确）
reset_cooldown = reset

__all__ = [
    "COOLDOWN_SECONDS",
    "FAILURE_THRESHOLD",
    "MAX_COOLDOWN_SECONDS",
    "cooldown_reason",
    "cooldown_state",
    "is_cooling",
    "mark_blocked",
    "mark_healthy",
    "note_failure",
    "policy",
    "reset",
    "reset_cooldown",
    "set_persistence",
]