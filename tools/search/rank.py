# -*- coding: utf-8 -*-
"""
结果重排与年份锁

**重排**：搜索引擎的排序是「相关性」，不是「对考研场景的价值」。本模块给出可解释的
加权打分，让「官方当年招生目录」稳定压过「三年前的公众号解读」，同时把每一项得分
留在结果里，方便排查「为什么这条排第一」。

**年份锁**：考研数据最大的危险之一是年份混淆 —— 查 2027 却把 2026 的招生人数当答案。
本模块识别每条结果的年份，与查询目标年不一致时标为 ``OUTDATED`` 并显著降权，
但**不删除**：历史数据对判断趋势有用，只是不能被当成当年事实。

权重（无语义向量，故把报告的 semantic 项折算进 lexical / quality）：
    lexical 0.35 · authority 0.25 · freshness 0.20 · entity 0.10 · quality 0.10
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from .models import SearchQuery, SearchResult
from .relevance import significant_tokens
from .segment import normalize_text, canonical

_LOG = logging.getLogger(__name__)

#: 打分权重（键见 ScoreBreakdown）
WEIGHTS: Dict[str, float] = {
    "lexical": 0.35,
    "authority": 0.25,
    "freshness": 0.20,
    "entity": 0.10,
    "quality": 0.10,
}

#: 年份提取（标题/摘要/日期里的 4 位年份）
_YEAR_RE = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")

#: 结果年份相对目标年的状态
YEAR_CURRENT = "current"
YEAR_OLDER = "outdated"
YEAR_UNKNOWN = "unknown"


@dataclass
class ScoreBreakdown:
    """一条结果的打分明细（每一项都在 0~1）。"""

    lexical: float = 0.0
    authority: float = 0.0
    freshness: float = 0.0
    entity: float = 0.0
    quality: float = 0.0
    total: float = 0.0

    def to_dict(self) -> Dict[str, float]:
        return {
            "lexical": round(self.lexical, 3),
            "authority": round(self.authority, 3),
            "freshness": round(self.freshness, 3),
            "entity": round(self.entity, 3),
            "quality": round(self.quality, 3),
            "total": round(self.total, 3),
        }


def detect_year(result: SearchResult) -> int:
    """从发布日期（ISO / 时间戳）或标题摘要里推断结果的年份，推断不出返回 0。"""
    raw = str(getattr(result, "published_at", "") or "").strip()
    if raw:
        # 纯数字当作 Unix 时间戳
        if raw.isdigit() and len(raw) >= 9:
            try:
                return datetime.fromtimestamp(int(raw)).year
            except Exception as exc:                  # pragma: no cover
                # 时间戳非法只丢失年份这一个字段，不影响其余打分；留痕便于排查
                logging.getLogger(__name__).debug("结果年份解析失败（按未知年份处理）: %s", exc)
        m = _YEAR_RE.search(raw)
        if m:
            return int(m.group(0))
    for text in (getattr(result, "title", ""), getattr(result, "snippet", "")):
        m = _YEAR_RE.search(str(text or ""))
        if m:
            return int(m.group(0))
    return 0


def year_status(result_year: int, target_year: Optional[int]) -> str:
    """结果年份相对目标年的状态。

    * 无目标年或结果无年份 → ``unknown``（不惩罚，只给中性分）
    * 与目标年一致 → ``current``
    * 早于目标年 → ``outdated``（显著降权，**但保留**，历史数据仍可能是唯一线索）
    """
    if not target_year or not result_year:
        return YEAR_UNKNOWN
    if result_year == target_year:
        return YEAR_CURRENT
    if result_year < target_year:
        return YEAR_OLDER
    # 晚于目标年（未来/下一年简章预告）：不算过期
    return YEAR_CURRENT


class Ranker:
    """可解释的结果重排器。"""

    def __init__(self, weights: Optional[Dict[str, float]] = None):
        self.weights = dict(weights or WEIGHTS)

    # ── 单条打分 ────────────────────────────────────────────

    def score(self, result: SearchResult, query: SearchQuery,
              tokens: Sequence[str] = ()) -> ScoreBreakdown:
        tokens = tuple(tokens) if tokens else tuple(significant_tokens(query.text))
        title = str(getattr(result, "title", "") or "")
        snippet = str(getattr(result, "snippet", "") or "")
        url = str(getattr(result, "url", "") or "")

        lexical = self._lexical(title, snippet, tokens)
        authority = float(getattr(result, "authority", 0.0) or 0.0)
        freshness = self._freshness(result, query)
        entity = self._entity(url, title, tokens)
        quality = self._quality(result, snippet)

        total = (self.weights["lexical"] * lexical
                 + self.weights["authority"] * authority
                 + self.weights["freshness"] * freshness
                 + self.weights["entity"] * entity
                 + self.weights["quality"] * quality)
        return ScoreBreakdown(lexical=lexical, authority=authority,
                              freshness=freshness, entity=entity,
                              quality=quality, total=total)

    # ── 批量重排 ────────────────────────────────────────────

    def rank(self, results: Sequence[SearchResult],
             query: SearchQuery) -> List[SearchResult]:
        """打分并按总分降序返回（结果对象会带上 score 与年份锁标记）。"""
        tokens = tuple(significant_tokens(query.text))
        scored: List[Tuple[float, SearchResult]] = []
        for result in results:
            breakdown = self.score(result, query, tokens)
            extra = dict(getattr(result, "extra", {}) or {})
            extra["score_breakdown"] = breakdown.to_dict()
            result_year = detect_year(result)
            status = year_status(result_year, query.year)
            if result_year:
                extra["detected_year"] = result_year
            extra["year_status"] = status

            scored.append((breakdown.total, SearchResult(
                title=result.title, url=result.url, snippet=result.snippet,
                engine=result.engine, domain=result.domain,
                source_type=result.source_type,
                published_at=result.published_at,
                authority=result.authority, score=breakdown.total, extra=extra,
            )))

        # 同分时按权威度、再按 provider 优先级稳定排序（避免结果顺序抖动）
        scored.sort(key=lambda pair: (-pair[0], -pair[1].authority, pair[1].url))
        return [r for _, r in scored]

    # ── 各项评分 ────────────────────────────────────────────

    @staticmethod
    def _lexical(title: str, snippet: str, tokens: Sequence[str]) -> float:
        """关键词覆盖度：标题命中权重高于摘要。"""
        if not tokens:
            return 0.0
        low_title, low_snippet = normalize_text(title), normalize_text(snippet)
        hits = 0.0
        for tok in tokens:
            tok = canonical(tok)
            if tok in low_title:
                hits += 1.0
            elif tok in low_snippet:
                hits += 0.6
        return min(1.0, hits / len(tokens))

    @staticmethod
    def _freshness(result: SearchResult, query: SearchQuery) -> float:
        """时效性：目标年一致最高；过期逐年衰减；未知给中性分。"""
        target = query.year or 0
        found = detect_year(result)
        status = year_status(found, target)
        if status == YEAR_UNKNOWN:
            return 0.5
        if status == YEAR_CURRENT:
            return 1.0
        # 过期：每早一年扣 0.25，最低 0.1（保留但不该排前面）
        gap = max(1, target - found) if target and found else 1
        return max(0.1, 1.0 - 0.25 * gap)

    @staticmethod
    def _entity(url: str, title: str, tokens: Sequence[str]) -> float:
        """实体命中：学校/专业等关键词出现在域名或标题里，说明这条更"对题"。"""
        if not tokens:
            return 0.0
        host = url.lower()
        low_title = title.lower()
        hits = sum(1 for tok in tokens if tok in host or tok in low_title)
        return min(1.0, hits / max(1, len(tokens)))

    @staticmethod
    def _quality(result: SearchResult, snippet: str) -> float:
        """文档质量：有摘要 > 无摘要；官方 PDF/目录页略加分。"""
        score = 0.0
        if snippet:
            score += 0.5 + min(0.3, len(snippet) / 600.0)
        if getattr(result, "is_official", False):
            score += 0.2
        return min(1.0, score)


__all__ = [
    "Ranker",
    "ScoreBreakdown",
    "WEIGHTS",
    "YEAR_CURRENT",
    "YEAR_OLDER",
    "YEAR_UNKNOWN",
    "detect_year",
    "year_status",
]
