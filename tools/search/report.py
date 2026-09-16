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

    def label(self) -> str:
        return f"✓ {self.name}" if self.ok else f"✗ {self.name}"

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


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
    def verdict(self) -> str:
        """结论：区分「确实没有」与「我们没搜到」。"""
        if self.found:
            return "found"
        if self.all_sources_failed:
            return "not_searched"            # 源全挂了 → 是"没搜到"
        return "not_found"                   # 源都正常但为空 → 大概率确实没有

    def verdict_text(self) -> str:
        mapping = {
            "found": "已找到相关结果（见下方「找到」）",
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


def build_report(response: SearchResponse, *, intent: str = "general",
                 year: Optional[int] = None,
                 expect_sources: Sequence[str] = ()) -> SearchReport:
    """把一次检索响应整理成结构化报告。"""
    report = SearchReport(query=response.query, intent=intent,
                          year=year or response.year)

    used = {name.replace("(缓存)", "") for name in response.providers_used}
    failed = dict(response.providers_failed)

    for name in sorted(used | set(failed)):
        if name in failed:
            report.sources.append(SourceStatus(name, False, failed[name]))
        else:
            extra = "（命中缓存）" if f"{name}(缓存)" in response.providers_used else ""
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

    return report


__all__ = ["MAX_LISTED", "SearchReport", "SourceStatus", "build_report"]
