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
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# [F3 修复·脚本直跑导入引导] `py tools/search/cache.py` 时 sys.path[0] 是
# tools/search/，后续 `from tools.*` 需要仓库根在 path 中。
_HERE = Path(__file__).resolve().parent
for _p in (str(_HERE), str(_HERE.parent), str(_HERE.parent.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# [F3 修复·相对导入] 脚本直跑时 __package__ 为空，`from .models import ...`
# 报 "attempted relative import with no known parent package"（实测）。
# 直跑场景显式声明包名，使相对导入解析为 tools.search.* 唯一模块身份。
if __name__ == "__main__" and not __package__:
    __package__ = "tools.search"

from .models import SearchResult

try:  # 双导入路径兼容
    from tools.ky_io import guard_write
except ImportError:  # pragma: no cover
    from ky_io import guard_write

try:  # 双导入路径兼容（源码脚本式 / tools 包式）
    from workspace import resolve_workspace_root
except ImportError:  # pragma: no cover
    from tools.workspace import resolve_workspace_root

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

#: [serve-stale] 过期条目最多还能当 fallback serve 多久（秒）。
#: 超过这个时长的条目宁可不返（结果已可能误导），直接当未命中。
#: 取 7 天与 ``RESOURCE_TTL`` 同量级：真题/经验贴这类内容 7 天内不会变质，
#: 而招生简章这类 6 小时就过期的，7 天前的那份对考生是负资产。
STALE_MAX_AGE = 7 * 24 * 3600


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


def _mark_stale(result: SearchResult, age_seconds: int) -> SearchResult:
    """给 serve-stale 的结果打上「陈旧」标注（不新增顶层字段，只写 ``extra``）。

    只写 ``extra`` 而不改 dataclass 字段，是为了不破坏 ``SearchResult`` 的
    frozen 契约与既有构造调用方（providers / CLI / GUI 都在直接构造它）。
    """
    extra = dict(result.extra)
    extra["stale"] = True
    extra["stale_age_seconds"] = int(age_seconds)
    return SearchResult(
        title=result.title, url=result.url, snippet=result.snippet,
        engine=result.engine, domain=result.domain,
        source_type=result.source_type, published_at=result.published_at,
        authority=result.authority, score=result.score, extra=extra,
    )


def _query_tokens(text: str) -> set:
    """查询的有效词集合（与守门/重排同一分词与停用词口径）。

    [P2 修复·2026-10-08] 用于 serve-stale 的「近义查询」判定。分词器不可用时
    返回空集 → 判定恒不成立（宁可少兜底，不拿错数据顶包）。
    """
    try:
        from .relevance import significant_tokens
        return set(significant_tokens(text))
    except Exception:                              # pragma: no cover - 极端降级
        return set()


def _tokens_compatible(ta: set, tb: set) -> bool:
    """两个词集合是否「同一问题的不同说法」（供陈旧兜底放宽匹配）。

    判定用词集合的重合度：至少共享 ``min(2, 较短侧词数)`` 个有效词，且重合
    占较短侧的一半以上。实测多路规划的变体（原句 / 校名+专业 / 追加
    「招生简章」「复试分数线」等后缀）都能通过；而「不同学校」或「不同专业」
    的查询（只共享一个泛词）不会通过 —— 那正是不能拿旧数据顶包的情形。
    任一侧为空（纯停用词/纯符号）→ 不判定，返回 False。
    """
    if not ta or not tb:
        return False
    shared = len(ta & tb)
    shorter = min(len(ta), len(tb))
    return shared >= min(2, shorter) and shared * 2 >= shorter


class SearchCache:
    """检索结果缓存（内存 + 可选落盘）。"""

    def __init__(self, path: Optional[Path] = None, enabled: bool = True,
                 max_entries: int = MAX_ENTRIES, ttl_map: Optional[Dict[str, int]] = None):
        self.enabled = bool(enabled)
        self.max_entries = int(max_entries)
        self.ttl_map = dict(ttl_map or DEFAULT_TTL)
        self.path = Path(path) if path else (resolve_workspace_root(__file__)
                                             / ".memory" / "search_cache.json")
        self._entries: Dict[str, CacheEntry] = {}
        self.hits = 0
        self.misses = 0
        #: [serve-stale] 命中「过期但仍在兜底窗口内」的次数（诊断用：冷却期
        #: 到底有多依赖陈旧数据 —— 这个数字高说明该源的 TTL 该调长了）。
        #: [P2 修复·2026-10-08] 近义查询兜底命中也计入本计数（同样是「拿旧数据
        #: 顶包」的形态）。
        self.stale_serves = 0
        if self.enabled:
            self._load()

    # ── 键 ──────────────────────────────────────────────────

    @staticmethod
    def make_key(provider: str, query: str, limit: int,
                 time_range: Optional[str]) -> str:
        return f"{provider}|{limit}|{time_range or ''}|{' '.join(str(query).split())}".lower()

    # ── 读写 ────────────────────────────────────────────────

    def get(self, provider: str, query: str, limit: int,
            time_range: Optional[str] = None, *,
            allow_stale: bool = False) -> Optional[List[SearchResult]]:
        """读缓存。

        :param allow_stale: 允许返回**已过期**条目（serve-stale）。默认 ``False``
            保持严格 TTL 语义（过期即未命中）；服务层在「源被反爬冷却」时才置
            ``True`` ——此时「拿一份旧结果并标注陈旧度」远好过「告诉用户没结果」。

        [为什么需要 serve-stale] R3 全矩阵仿真实测：全源冷却时检索能力直接归零，
        模型只看到「未找到结果」并在死路上烧步数。而招生简章/复试线这类信息在
        冷却的 10 分钟内**并不会变化** —— 过期 1 小时的缓存远比空结果有用。
        返回的每条结果都带 ``extra["stale"]=True`` 与 ``extra["stale_age_seconds]``，
        由调用方如实告知考生「这是陈旧数据」。

        [P2 修复·2026-10-08 覆盖放宽] ``allow_stale=True`` 时，精确 key 未命中
        会再尝试「近义查询」兜底（见 :meth:`_find_stale_fallback`）；严格模式
        （默认）行为不变 —— 过期即未命中、查询必须精确匹配。
        """
        if not self.enabled:
            return None
        key = self.make_key(provider, query, limit, time_range)
        entry = self._entries.get(key)
        # [P2 修复·2026-10-08] 精确 key 未命中时，serve-stale 路径放宽到
        # 「同 provider/limit/time_range 的近义查询」条目 —— 多路规划会为同一
        # 问题产出多个变体（原句 / 校名+专业 / 追加「招生简章」等后缀），
        # 精确匹配让兜底只覆盖「一字不差复问」这一种形态，源一旦被反爬冷却，
        # 变体查询就无旧数据可用。**仅 allow_stale=True 生效**：新鲜读必须
        # 精确命中，否则会把别的查询的结果当本次查询的新鲜结果返回。
        fallback = False
        if entry is None and allow_stale:
            entry = self._find_stale_fallback(provider, query, limit, time_range)
            fallback = entry is not None
        if entry is None:
            self.misses += 1
            return None
        if entry.expired:
            age = int(entry.age)
            if not allow_stale or age > STALE_MAX_AGE:
                # 陈旧到超出兜底窗口（或调用方不要陈旧）→ 真miss，并清掉条目。
                # [为什么过期仍保留条目] serve-stale 需要它；这里只在「确定不再用」
                # 时才pop，避免"第一次 miss 就把冷却期的救命数据删掉"。
                if age > STALE_MAX_AGE:
                    self._entries.pop(key, None)
                self.misses += 1
                return None
            self.hits += 1
            self.stale_serves += 1
            return [_mark_stale(SearchResult.from_raw(item, engine=entry.provider),
                                age)
                    for item in entry.results]
        if fallback:
            # 近义查询的条目：相对本次查询同样是「旧数据」（不是为本次查询取的），
            # 照旧标注陈旧度与年龄，交上层如实展示。
            age = int(entry.age)
            self.hits += 1
            self.stale_serves += 1
            _LOG.info("缓存近义兜底：%r 命中缓存查询 %r（provider=%s，age=%ds）",
                      str(query)[:60], entry.query[:60], entry.provider, age)
            return [_mark_stale(SearchResult.from_raw(item, engine=entry.provider),
                                age)
                    for item in entry.results]
        self.hits += 1
        return [SearchResult.from_raw(item, engine=entry.provider) for item in entry.results]

    def _find_stale_fallback(self, provider: str, query: str, limit: int,
                             time_range: Optional[str]) -> Optional[CacheEntry]:
        """为 serve-stale 找一条「近义查询」的缓存条目（找不到返回 None）。

        候选必须同 provider / 同 limit / 同 time_range（与精确匹配同范围，只是
        放宽查询文本），且未超出 ``STALE_MAX_AGE`` 兜底窗口；多条命中取最新
        存入的一条（``stored_at`` 最大）。
        """
        want_provider = str(provider or "")
        want_limit = str(int(limit))
        want_range = str(time_range or "").lower()
        want_tokens = _query_tokens(query)
        if not want_tokens:
            return None
        best: Optional[CacheEntry] = None
        for entry in self._entries.values():
            if entry.provider != want_provider:
                continue
            parts = entry.key.split("|", 3)
            if len(parts) != 4 or parts[1] != want_limit or parts[2] != want_range:
                continue
            if entry.age > STALE_MAX_AGE:
                continue
            if not _tokens_compatible(want_tokens, _query_tokens(entry.query)):
                continue
            if best is None or entry.stored_at > best.stored_at:
                best = entry
        return best

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
            "stale_serves": self.stale_serves,
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
                # [serve-stale] 原实现「加载时即丢弃过期条目」—— 那会让冷却期的
                # 兜底数据在**重启后全部消失**，serve-stale 形同虚设（R3 实测的
                # 「换个端点就忘了」同源缺陷）。现只丢弃超出兜底窗口的。
                if entry.age > STALE_MAX_AGE:
                    continue
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
    "STALE_MAX_AGE",
    "SearchCache",
]
