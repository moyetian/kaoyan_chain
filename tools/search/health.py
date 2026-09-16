# -*- coding: utf-8 -*-
"""
Provider 健康度：反爬/失败后的冷却

[由来] 本轮实测触发 DuckDuckGo 限流后，它在一段时间内的每次请求都会再返回验证页，
而且**每试一次都可能让限流更严**。多源联邦下继续重试同一个被挡的源，只是白白增加
延迟与失败噪音。

因此：一个 provider 被判定为「反爬拦截/不可用」后，进入冷却期（默认 10 分钟），
期间不再请求它，并在 `SearchResponse.providers_failed` 里如实说明「冷却中」，
让调用方一眼看出是**暂时不可用**而不是**这个源没数据**。
"""

from __future__ import annotations

import logging
import time
from typing import Dict

_LOG = logging.getLogger(__name__)

#: 冷却时长（秒）
COOLDOWN_SECONDS = 600

#: provider -> 进入冷却的时间戳
_COOLDOWN: Dict[str, float] = {}


def mark_blocked(name: str, reason: str = "") -> None:
    """把一个 provider 标记为「被拦截」，进入冷却。"""
    name = str(name or "").strip().lower()
    if not name:
        return
    _COOLDOWN[name] = time.time()
    _LOG.info("provider %s 进入冷却 %s 秒（原因：%s）", name, COOLDOWN_SECONDS, reason or "未提供")


def is_cooling(name: str) -> bool:
    """是否在冷却期内（过期自动解除）。"""
    name = str(name or "").strip().lower()
    started = _COOLDOWN.get(name)
    if started is None:
        return False
    if time.time() - started >= COOLDOWN_SECONDS:
        _COOLDOWN.pop(name, None)
        return False
    return True


def cooldown_reason(name: str) -> str:
    """冷却中的说明文案。"""
    name = str(name or "").strip().lower()
    started = _COOLDOWN.get(name)
    if started is None:
        return ""
    remaining = max(0, int(COOLDOWN_SECONDS - (time.time() - started)))
    return f"冷却中（约 {remaining} 秒前被反爬/失败拦截，暂不再请求）"


def cooldown_state() -> Dict[str, float]:
    """当前冷却表快照（供测试与诊断）。"""
    return dict(_COOLDOWN)


def reset() -> None:
    """清空冷却表（测试用）。"""
    _COOLDOWN.clear()


#: 门面导出用别名（语义更明确）
reset_cooldown = reset


__all__ = [
    "COOLDOWN_SECONDS",
    "reset_cooldown",
    "cooldown_reason",
    "cooldown_state",
    "is_cooling",
    "mark_blocked",
    "reset",
]
