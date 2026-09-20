# -*- coding: utf-8 -*-
"""
检索结果缓存（带 TTL）

[为什么必须有] 本轮实测触发了 DuckDuckGo 的限流（约 20 次密集请求后返回验证页），
而多路检索（`search_planned`，默认 4 路）会把请求数再放大数倍。没有缓存时：
  * 同一个问题被追问两次就多发一轮请求；
  * 多路之间的重复查询（如「校名 招生简章」在多轮里反复出现）全部打向引擎。
缓存把「同一 provider + 同一条查询」的结果复用一段时间，既省配额也更快。

TTL 按来源类型区分（参考报告的建议并按本项目调整）：
  * 官方招生信息：6 小时（简章/目录会改，但不能太旧）
  * 公众号/聚合站：12 小时
  * 社区（知乎等）：24 小时
  * 资料类（真题/经验贴）：7 天（几乎不变）
落盘位置 `.memory/search_cache.json`（已被 .gitignore 忽略，属本地可弃数据）。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .models import SearchResult

try:  # 双导入路径兼容
    from tools.ky_io import guard_write
except ImportError:  # pragma: no cover
    from ky_io import guard_write

_LOG = logging.getLogger(__name__)

#: 默认 TTL（秒）：按来源类型
DEFAULT_TTL: Dict[str, int] = {
    "national_official": 6 * 3600,
    "graduate_school": 6 * 3600,
    "university_official": 6 * 3600,
    "official_document": 12 * 3600,
    "wechat": 12 * 3600,
    "aggregator": 12 * 3600,
    "community": 24 * 3600,
    "video": 24 * 3600,
    "unknown": 6 * 3600,
}
#: 资料类查询（真题/经验贴）内容几乎不变，给更长的 TTL
RESOURCE_TTL = 7 * 24 * 3600
DEFAULT_TTL_FALLBACK = 6 * 3600

#: 缓存条目上限（超出后淘汰最旧的）
MAX_ENTRIES = 500


@dataclass
class CacheEntry:
    """一条缓存记录。"""

    key: str
    provider: str
    query: str
    results: List[Dict[str, Any]] = field(default_factory=list)
    stored_at: float = 0.0
    ttl: int = DEFAULT_TTL_FALLBACK

    @property
    def age(self) -> float:
        return time.time() - self.stored_at

    @property
    def expired(self) -> bool:
        return self.age > self.ttl

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "CacheEntry":
        return cls(
            key=str(data.get("key", "")),
            provider=str(data.get("provider", "")),
            query=str(data.get("query", "")),
            results=list(data.get("results") or []),
            stored_at=float(data.get("stored_at", 0.0)),
            ttl=int(data.get("ttl", DEFAULT_TTL_FALLBACK)),
        )


class SearchCache:
    """检索结果缓存（内存 + 可选落盘）。"""

    def __init__(self, path: Optional[Path] = None, enabled: bool = True,
                 max_entries: int = MAX_ENTRIES, ttl_map: Optional[Dict[str, int]] = None):
        self.enabled = bool(enabled)
        self.max_entries = int(max_entries)
        self.ttl_map = dict(ttl_map or DEFAULT_TTL)
        self.path = Path(path) if path else (Path(__file__).resolve().parent.parent.parent
                                             / ".memory" / "search_cache.json")
        self._entries: Dict[str, CacheEntry] = {}
        self.hits = 0
        self.misses = 0
        if self.enabled:
            self._load()

    # ── 键 ──────────────────────────────────────────────────

    @staticmethod
    def make_key(provider: str, query: str, limit: int,
                 time_range: Optional[str]) -> str:
        return f"{provider}|{limit}|{time_range or ''}|{' '.join(str(query).split())}".lower()

    # ── 读写 ────────────────────────────────────────────────

    def get(self, provider: str, query: str, limit: int,
            time_range: Optional[str] = None) -> Optional[List[SearchResult]]:
        if not self.enabled:
            return None
        entry = self._entries.get(self.make_key(provider, query, limit, time_range))
        if entry is None:
            self.misses += 1
            return None
        if entry.expired:
            self._entries.pop(entry.key, None)
            self.misses += 1
            return None
        self.hits += 1
        return [SearchResult.from_raw(item, engine=entry.provider) for item in entry.results]

    def put(self, provider: str, query: str, limit: int, results: Sequence[SearchResult],
            time_range: Optional[str] = None, ttl: Optional[int] = None) -> None:
        if not self.enabled or not results:
            return
        key = self.make_key(provider, query, limit, time_range)
        self._entries[key] = CacheEntry(
            key=key, provider=provider, query=str(query),
            results=[r.to_dict() for r in results],
            stored_at=time.time(),
            ttl=int(ttl if ttl is not None else self.ttl_for(results)),
        )
        self._prune()
        self._save()

    # ── 维护 ────────────────────────────────────────────────

    def ttl_for(self, results: Sequence[SearchResult]) -> int:
        """按结果里**最权威**的那条决定 TTL（权威信息更新更频繁，缓存期更短）。"""
        if not results:
            return DEFAULT_TTL_FALLBACK
        ResourceHints = ("真题", "回忆版", "题库", "笔记", "讲义", "资料")
        text = " ".join((r.title or "") + " " + (r.snippet or "") for r in results)
        if any(hint in text for hint in ResourceHints):
            return RESOURCE_TTL
        best = max(results, key=lambda r: getattr(r, "authority", 0.0))
        return self.ttl_map.get(getattr(best, "source_type", "unknown"), DEFAULT_TTL_FALLBACK)

    def clear(self) -> None:
        self._entries.clear()
        self._save()

    def clear_expired(self) -> int:
        expired = [k for k, e in self._entries.items() if e.expired]
        for key in expired:
            self._entries.pop(key, None)
        if expired:
            self._save()
        return len(expired)

    def stats(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "entries": len(self._entries),
            "hits": self.hits,
            "misses": self.misses,
            "path": str(self.path),
        }

    # ── 持久化 ──────────────────────────────────────────────

    def _prune(self) -> None:
        if len(self._entries) <= self.max_entries:
            return
        for key, _ in sorted(self._entries.items(),
                             key=lambda kv: kv[1].stored_at)[: len(self._entries) - self.max_entries]:
            self._entries.pop(key, None)

    def _load(self) -> None:
        try:
            if not self.path.exists():
                return
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for item in (data.get("entries") or []):
                entry = CacheEntry.from_json(item)
                if not entry.expired:
                    self._entries[entry.key] = entry
        except Exception as exc:                   # pragma: no cover - 缓存损坏不该影响检索
            _LOG.debug("检索缓存加载失败（按空缓存继续）: %s", exc)

    def _save(self) -> None:
        try:
            # [safe 模式收口] 这里原先是裸 ``write_text``，绕开了 ky_io 的统一写闸门：
            # 实测 ``ky scout``（不带 --save）在 ``--permission=safe`` 下仍会落盘
            # ``.memory/search_cache.json``。先过 ``guard_write`` 再建目录，
            # 只读模式下连目录都不创建；异常由本方法既有的 except 兜住，
            # 表现为「缓存不落盘、检索照常返回」，与模块既有语义一致。
            guard_write("写入检索缓存", self.path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"version": 1, "entries": [e.to_json() for e in self._entries.values()]}
            self.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:                   # pragma: no cover
            _LOG.debug("检索缓存写入失败（忽略）: %s", exc)


__all__ = [
    "CacheEntry",
    "DEFAULT_TTL",
    "MAX_ENTRIES",
    "RESOURCE_TTL",
    "SearchCache",
]
