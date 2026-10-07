# -*- coding: utf-8 -*-
"""
结构化检索报告（把「没找到」说清楚）

[为什么] 朴素的「没有找到相关资料」对用户毫无价值 —— 它分不清：
  * **资料确实不存在**（该院今年还没发 2027 目录）；
  * **我们没搜到**（源被反爬挡了、查询词不对、源不覆盖该校）。

前者该让用户改去别处查证，后者该让用户换个时间/换个源重试。本模块把一次检索的
「已检查 / 找到 / 缺失」如实列出来，两者一眼可辨。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .models import SearchResponse, SearchResult

#: 结果的展示条数上限
MAX_LISTED = 8


@dataclass
class SourceStatus:
    """一个检索源的状态。"""

    name: str
    ok: bool
    detail: str = ""
    #: [W11 全源冷却] 该源是否处于反爬冷却期（暂时不可用，稍后自动恢复）。
    cooling: bool = False

    def label(self) -> str:
        return f"✓ {self.name}" if self.ok else f"✗ {self.name}"

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail,
                "cooling": self.cooling}


@dataclass
class SearchReport:
    """一次检索的完整交代。"""

    query: str
    intent: str = "general"
    year: Optional[int] = None
    sources: List[SourceStatus] = field(default_factory=list)
    found: List[SearchResult] = field(default_factory=list)
    found_official: List[SearchResult] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    # ── 判定 ────────────────────────────────────────────────

    @property
    def all_sources_failed(self) -> bool:
        return bool(self.sources) and all(not s.ok for s in self.sources)

    @property
    def all_sources_cooling(self) -> bool:
        """所有源都失败、且失败原因全部是「反爬冷却期」。

        [W11] 与 ``all_sources_failed``（笼统「没搜到」）分开：冷却期是
        **暂时**不可用（等一会儿自动恢复），处置是「勿立即重试」而非「换源再搜」。
        """
        return (bool(self.sources)
                and all((not s.ok) and s.cooling for s in self.sources))

    @property
    def verdict(self) -> str:
        """结论：区分「确实没有」「我们没搜到」与「源暂时冷却」。"""
        if self.found:
            return "found"
        if self.all_sources_cooling:
            return "cooling"
        if self.all_sources_failed:
            return "not_searched"            # 源全挂了 → 是"没搜到"
        return "not_found"                   # 源都正常但为空 → 大概率确实没有

    def verdict_text(self) -> str:
        mapping = {
            "found": "已找到相关结果（见下方「找到」）",
            "cooling": "⏸️ 全部检索源处于**反爬冷却期**（暂时不可用，稍后自动恢复）"
                       "——这不代表资料不存在；请勿立即重试同一查询，"
                       "可先改用工作区资料，或等待数分钟后重试",
            "not_searched": "本次**未能完成检索**（所有检索源都未成功返回结果），"
                            "不代表资料不存在 —— 建议稍后重试或换用其它源",
            "not_found": "各检索源均正常返回但**没有匹配结果**，"
                         "大概率是资料尚未发布或未被检索源收录",
        }
        return mapping[self.verdict]

    # ── 输出 ────────────────────────────────────────────────

    def to_markdown(self) -> str:
        lines = [f"## 检索报告：{self.query}", ""]
        lines.append(f"- 需求类型：{self.intent}")
        if self.year:
            lines.append(f"- 目标年份：{self.year}")
        lines.append(f"- 结论：{self.verdict_text()}")
        lines.append("")

        lines.append("### 已检查的检索源")
        if self.sources:
            for source in self.sources:
                detail = f" —— {source.detail}" if source.detail else ""
                lines.append(f"- {source.label()}{detail}")
        else:
            lines.append("- （无可用检索源）")
        lines.append("")

        lines.append("### 找到")
        if self.found:
            for i, r in enumerate(self.found[:MAX_LISTED], 1):
                tag = "官方" if r.is_official else r.source_type
                year_note = ""
                year_status = (r.extra or {}).get("year_status")
                if year_status == "outdated":
                    year_note = f"（⚠️ 年份 {(r.extra or {}).get('detected_year', '?')}，非目标年）"
                lines.append(f"{i}. [{tag}] {r.title}{year_note}")
                lines.append(f"   {r.url}")
        else:
            lines.append("- （无）")
        lines.append("")

        if self.missing:
            lines.append("### 缺失 / 未覆盖")
            for item in self.missing:
                lines.append(f"- {item}")
            lines.append("")

        if self.notes:
            lines.append("### 备注")
            for note in self.notes:
                lines.append(f"- {note}")
            lines.append("")

        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query, "intent": self.intent, "year": self.year,
            "verdict": self.verdict, "verdict_text": self.verdict_text(),
            "sources": [s.to_dict() for s in self.sources],
            "found": [r.to_dict() for r in self.found[:MAX_LISTED]],
            "found_official": [r.to_dict() for r in self.found_official],
            "missing": list(self.missing), "notes": list(self.notes),
        }


#: ``providers_used`` 里的缓存标注后缀（新鲜命中 / 陈旧兜底）。
#: [serve-stale] 冷却期会用陈旧缓存兜底，标注必须能被识别并**如实呈现陈旧度**
#: —— 否则报告会写成「命中缓存」，让模型以为拿到的是新鲜数据。
_CACHE_SUFFIX_RE = re.compile(r"\(缓存(?:·陈旧)?\)")


def _strip_cache_suffix(name: str) -> str:
    return _CACHE_SUFFIX_RE.sub("", name)


def build_report(response: SearchResponse, *, intent: str = "general",
                 year: Optional[int] = None,
                 expect_sources: Sequence[str] = ()) -> SearchReport:
    """把一次检索响应整理成结构化报告。"""
    report = SearchReport(query=response.query, intent=intent,
                          year=year or response.year)

    used = {_strip_cache_suffix(name) for name in response.providers_used}
    failed = dict(response.providers_failed)
    cooling = {name for name, _ in response.providers_cooling}
    stale_used = any("·陈旧" in name for name in response.providers_used)

    for name in sorted(used | set(failed)):
        if name in failed:
            report.sources.append(SourceStatus(name, False, failed[name],
                                               cooling=name in cooling))
        else:
            hit = next((n for n in response.providers_used
                        if _strip_cache_suffix(n) == name), "")
            if "·陈旧" in hit:
                extra = "（命中陈旧缓存，检索源当时不可用）"
            elif hit:
                extra = "（命中缓存）"
            else:
                extra = ""
            report.sources.append(SourceStatus(name, True, extra))
    report.sources.sort(key=lambda s: (not s.ok, s.name))

    report.found = list(response.results)
    report.found_official = list(response.official_results)

    # 缺失项：期望覆盖但没覆盖到的源
    for name in expect_sources:
        if name not in used and name not in failed:
            report.missing.append(f"检索源 {name} 未参与本次检索")

    if response.providers_failed:
        report.missing.append("上述失败源未返回结果，相关来源可能未被覆盖")
    if response.year and not report.found:
        report.missing.append(f"未找到 {response.year} 年的官方数据")
    if response.candidates:
        report.notes.append(response.summary_line())
    if any((r.extra or {}).get("year_status") == "outdated" for r in report.found):
        report.notes.append("结果中包含往年数据（已标注 ⚠️），"
                            "请勿直接当作目标年份的官方结论")
    if stale_used:
        # [serve-stale] 陈旧兜底必须留痕：结论仍成立，但时效性由考生自行判断。
        report.notes.append("部分结果来自**过期缓存**兜底（相应检索源当时被反爬拦截"
                            "或不可用）——内容大概率仍然有效，但请勿当作刚刚发布的口径")

    return report


__all__ = ["MAX_LISTED", "SearchReport", "SourceStatus", "build_report"]
