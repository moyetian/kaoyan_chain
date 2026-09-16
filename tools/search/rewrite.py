# -*- coding: utf-8 -*-
"""
查询改写与查询规划（Query Rewrite / Planning）

[解决的问题] 直接用用户原句去搜，召回质量取决于运气：
「南方医科大学085409今年招多少人」这句话里，搜索引擎既不知道 085409 是专业代码，
也不知道该校官网域名是 smu.edu.cn、研究生院在 portal.smu.edu.cn。实测同一条
需求，用「南方医科大学 085409 招生」能命中研究生招生网，用原句则经常只拿到
培训机构的二手解读。

本模块把一条自然语言需求拆成**多路针对性查询**：
  1. 通用召回（原句去噪）
  2. 站内检索：学校官网 / 研究生院 / 研招办（域名取自院校注册表，不猜）
  3. 权威榜：研招网（yz.chsi.com.cn）
  4. 意图扩展：招生简章 / 专业目录 / 复试线 / 拟录取名单 / 真题回忆版

站内查询的生成复用 `intelligence.discovery.OfficialDiscovery.build_targeted_queries`
—— 该函数此前**从未被任何代码调用**（死代码），本模块让它真正生效。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .models import SearchQuery
from .source_registry import domains_for_school

_LOG = logging.getLogger(__name__)

#: 6 位专业代码（考研专业代码形如 085409 / 081200）
_MAJOR_CODE_RE = re.compile(r"(?<!\d)(0\d{5}|\d{6})(?!\d)")
#: 4 位年份
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
#: 与「资料/资源」相关的意图词
_RESOURCE_HINTS = ("真题", "回忆版", "题库", "笔记", "讲义", "资料", "pdf", "网盘",
                   "学长", "经验", "参考书", "书单")
#: 与「事实/政策」相关的意图词
_FACT_HINTS = ("招生简章", "专业目录", "初试科目", "参考书", "复试线", "分数线",
               "拟录取", "报录比", "名额", "人数", "调剂", "推免", "大纲",
               "多少人", "招多少", "考什么", "考试科目", "怎么样", "难不难")

#: 事实类需求默认追加的查询后缀（按查询类型分组，避免一次塞太多）
_FACT_SUFFIXES: Tuple[str, ...] = ("招生简章", "招生专业目录", "复试分数线")
#: 资料类需求默认追加的查询后缀
_RESOURCE_SUFFIXES: Tuple[str, ...] = ("真题", "回忆版 真题", "参考书目")

#: 「网页类」检索源（站内检索只应由它们承担）
WEB_PROVIDERS: Tuple[str, ...] = ("ddg", "bing")
#: 资料发现常用的检索源（公众号经验贴/资料）
RESOURCE_PROVIDERS: Tuple[str, ...] = ("ddg", "bing", "sogou-weixin")


def providers_for_query(query: str, intent: str) -> Tuple[str, ...]:
    """按查询类型选择检索源。

    实测教训：把 `site:portal.smu.edu.cn 2027 硕士 招生简章` 这类站内查询发给
    「公众号文章检索」只会稳定失败（不会有人把招生简章发在公众号里并带 site: 语法），
    白白产生一条噪音失败记录。反过来，找真题/经验贴时又确实该带上公众号源。
    """
    text = str(query or "").lower()
    if "site:" in text:
        return WEB_PROVIDERS
    if intent == "resource":
        return RESOURCE_PROVIDERS
    return ()


@dataclass(frozen=True)
class QueryEntities:
    """从一条自然语言需求里抽出的考研实体。"""

    raw: str
    school: str = ""
    major_code: str = ""
    year: int = 0
    keywords: Tuple[str, ...] = ()
    domains: Dict[str, str] = field(default_factory=dict)

    @property
    def has_school(self) -> bool:
        return bool(self.school)

    def base_terms(self) -> List[str]:
        """用于拼查询的核心词（学校 + 专业代码/首个有效关键词）。

        关键词里要排除学校名本身，否则会拼出「华中科技大学 华中科技大学 招生简章」
        这种自我重复的查询（实测出现过）。
        """
        parts: List[str] = []
        if self.school:
            parts.append(self.school)
        if self.major_code:
            parts.append(self.major_code)
        else:
            for kw in self.keywords:
                if self.school and self.school in kw:
                    continue
                parts.append(kw)
                break
        return parts


@dataclass(frozen=True)
class QueryPlan:
    """一次需求的检索计划。"""

    query: str
    intent: str                              # fact / resource / general
    entities: QueryEntities
    queries: Tuple[SearchQuery, ...] = ()

    @property
    def texts(self) -> List[str]:
        return [q.text for q in self.queries]

    def summary(self) -> str:
        ents = []
        if self.entities.school:
            ents.append(f"学校={self.entities.school}")
        if self.entities.major_code:
            ents.append(f"专业代码={self.entities.major_code}")
        if self.entities.year:
            ents.append(f"年份={self.entities.year}")
        return (f"意图={self.intent} · " + (" ".join(ents) if ents else "未识别到实体")
                + f" · {len(self.queries)} 路查询")


def extract_entities(text: str, *, year: Optional[int] = None) -> QueryEntities:
    """从需求文本里抽取院校 / 专业代码 / 年份 / 关键词。"""
    raw = str(text or "").strip()
    code = ""
    m = _MAJOR_CODE_RE.search(raw)
    if m:
        code = m.group(1)

    yr = int(year or 0)
    if not yr:
        ym = _YEAR_RE.search(raw)
        if ym:
            yr = int(ym.group(0))

    school = ""
    try:
        try:
            from intelligence.registry import resolve_university
        except ImportError:                       # pragma: no cover
            from tools.intelligence.registry import resolve_university  # type: ignore

        # 注册表按「整串包含」解析，故用原文试一次；命中则取其规范名
        entity = resolve_university(raw)
        name = str(getattr(entity, "name", "") or "")
        code_field = str(getattr(entity, "chsi_code", "") or "")
        if name and code_field.isdigit():         # 只采信真登记过的院校
            school = name
    except Exception as exc:                      # pragma: no cover
        _LOG.debug("院校解析失败（忽略）: %s", exc)

    # 关键词：切分后的中文片段，剔除纯数字、过长（多半是整句）与含校名的片段
    raw_words = re.split(r"[\s,，。、？?！!：:；;]+", raw)
    words: List[str] = []
    for w in raw_words:
        w = w.strip()
        if len(w) < 2 or len(w) > 15 or w.isdigit():
            continue
        if code and code in w:
            w = w.replace(code, "").strip()
        if school and school in w:
            continue
        if len(w) >= 2:
            words.append(w)
    keywords = tuple(dict.fromkeys(words))

    domains = domains_for_school(school) if school else {}
    return QueryEntities(raw=raw, school=school, major_code=code, year=yr,
                         keywords=keywords, domains=domains)


def detect_intent(text: str) -> str:
    """判别需求属于「资料检索」还是「事实核验」。"""
    low = str(text or "").lower()
    if any(hint in low for hint in _RESOURCE_HINTS):
        return "resource"
    if any(hint in low for hint in _FACT_HINTS):
        return "fact"
    return "general"


def _site_queries(school: str, domains: Dict[str, str], major_kw: str,
                  year: int) -> List[str]:
    """站内查询：复用 OfficialDiscovery（此前是死代码）。"""
    out: List[str] = []
    try:
        try:
            from intelligence.discovery import OfficialDiscovery
        except ImportError:                       # pragma: no cover
            from tools.intelligence.discovery import OfficialDiscovery  # type: ignore

        # 该实例只用于调用纯函数式方法（不发起请求）
        discovery = OfficialDiscovery()
        for key in ("admission", "graduate", "official"):
            domain = domains.get(key)
            if not domain:
                continue
            try:
                out.extend(discovery.build_targeted_queries(
                    school_name=school, domain=domain,
                    major_keyword=major_kw or None,
                    year=year or 0))
            except Exception as exc:              # pragma: no cover
                _LOG.debug("站内查询生成失败: %s", exc)
    except Exception as exc:                      # pragma: no cover
        _LOG.debug("OfficialDiscovery 不可用，退化为通用查询: %s", exc)
    return out


def plan_queries(text: str, *, year: Optional[int] = None,
                 school: Optional[str] = None, major: Optional[str] = None,
                 limit: int = 8, domains: Sequence[str] = (),
                 providers: Sequence[str] = ()) -> QueryPlan:
    """把一条需求规划成多路查询。

    :param limit: 生成的查询条数上限（**不追求多**：每路都会真的发一次请求，
        3~8 路足够覆盖文档里提到的官方/研招网/专业目录/资料四类来源）。
    """
    entities = extract_entities(text, year=year)
    if school and not entities.school:
        entities = QueryEntities(raw=entities.raw, school=str(school),
                                 major_code=entities.major_code,
                                 year=entities.year, keywords=entities.keywords,
                                 domains=domains_for_school(str(school)))
    intent = detect_intent(text)

    major_kw = str(major or entities.major_code or "").strip()
    base = " ".join(entities.base_terms()) or entities.raw
    queries: List[str] = [entities.raw.strip() or base]

    if base and base != entities.raw.strip():
        queries.append(base)

    # 分类后缀（事实 / 资料）
    suffixes = _FACT_SUFFIXES if intent != "resource" else _RESOURCE_SUFFIXES
    for suffix in suffixes:
        if len(queries) >= limit - 2:
            break
        queries.append(f"{base} {suffix}".strip())

    # 站内检索（学校官网 / 研究生院 / 研招办）
    for q in _site_queries(entities.school, entities.domains, major_kw, entities.year):
        if len(queries) >= limit - 1:
            break
        queries.append(q)

    # 研招网
    if entities.school and len(queries) < limit:
        queries.append(f"site:yz.chsi.com.cn {entities.school} {major_kw}".strip())

    # 去重保序 + 转成 SearchQuery
    seen: List[str] = []
    for q in queries:
        q = re.sub(r"\s+", " ", str(q)).strip()
        if q and q not in seen:
            seen.append(q)

    planned = tuple(SearchQuery(
        text=q, limit=limit, domains=tuple(domains),
        year=entities.year or None,
        providers=tuple(providers) or providers_for_query(q, intent))
        for q in seen[:limit])
    return QueryPlan(query=entities.raw, intent=intent, entities=entities,
                     queries=planned)


__all__ = [
    "RESOURCE_PROVIDERS",
    "WEB_PROVIDERS",
    "QueryEntities",
    "QueryPlan",
    "detect_intent",
    "extract_entities",
    "plan_queries",
    "providers_for_query",
]
