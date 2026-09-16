# -*- coding: utf-8 -*-
"""
来源权威度表（域名 → 来源类型 + 权威分）

[为什么需要它] 搜索引擎的排序是「相关性」，不是「权威性」。考研场景里
「某公众号解读 2027 专业目录」与「研招网官方专业目录」的相关性可能接近，
但作为事实依据的价值天差地别。没有这张表，Agent 会把前者当后者用。

**Source Quality ≠ Claim Correctness**：权威度只描述「这个来源本身有多可信」，
不代表它说的每句话都对。公众号也可能写得很专业，但它**不会因此变成官方证据** ——
所以 `is_official` 只认来源类型，不认内容质量（见 models.SearchResult.is_official）。
"""

from __future__ import annotations

from typing import Dict, List, Tuple
from urllib.parse import urlparse

#: 权威分区间说明（供文档与排查）
#: 1.00 国家级官方 / 0.95~0.99 高校官方 / 0.50~0.60 自媒体 / 0.30~0.40 社区
DEFAULT_AUTHORITY = 0.40
DEFAULT_TYPE = "unknown"

#: 精确域名表（命中即用）
DOMAIN_TABLE: Dict[str, Tuple[str, float]] = {
    # 国家级官方
    "yz.chsi.com.cn": ("national_official", 1.00),
    "chsi.com.cn": ("national_official", 0.98),
    "moe.gov.cn": ("national_official", 1.00),
    "cdgdc.edu.cn": ("national_official", 0.98),
    # 高校官方（研究生院子域通常单独命名）
    "yz.": ("graduate_school", 0.98),
    "yjs.": ("graduate_school", 0.98),
    "gs.": ("graduate_school", 0.98),
    "grad.": ("graduate_school", 0.98),
    "graduate.": ("graduate_school", 0.98),
    # 自媒体 / 社区 / 视频
    "mp.weixin.qq.com": ("wechat", 0.55),
    "zhuanlan.zhihu.com": ("community", 0.40),
    "zhihu.com": ("community", 0.38),
    "xiaohongshu.com": ("community", 0.35),
    "bilibili.com": ("video", 0.35),
    "douyin.com": ("video", 0.30),
    "tieba.baidu.com": ("community", 0.30),
    # 聚合/转载（培训机构常改写官方内容，权威度低于官方原文）
    "kaoyan.com": ("aggregator", 0.45),
    "kaoyan365.cn": ("aggregator", 0.40),
    "wendu.com": ("aggregator", 0.40),
    "xdf.cn": ("aggregator", 0.45),
    "koolearn.com": ("aggregator", 0.45),
    # 代码/资料托管
    "github.com": ("aggregator", 0.55),
    "gitee.com": ("aggregator", 0.50),
}

#: 后缀规则（在精确表未命中时按顺序匹配）
_SUFFIX_RULES: Tuple[Tuple[str, str, float], ...] = (
    (".edu.cn", "university_official", 0.97),   # 中国高校教育与科研网
    (".edu", "university_official", 0.92),      # 境外高校
    (".gov.cn", "national_official", 0.98),
    (".ac.cn", "national_official", 0.90),      # 中科院系统
)


def _host(url_or_host: str) -> str:
    text = str(url_or_host or "").strip().lower()
    if "://" in text:
        text = (urlparse(text).hostname or "").lower()
    else:
        text = urlparse("//" + text).hostname or text
    return text[4:] if text.startswith("www.") else text


def classify(url_or_host: str) -> Tuple[str, float]:
    """返回 ``(source_type, authority)``。未知来源给保守的默认值。

    未命中不代表不可用，只代表「不确定」—— 排序时会吃亏，但不会被打成官方。
    """
    host = _host(url_or_host)
    if not host:
        return DEFAULT_TYPE, DEFAULT_AUTHORITY

    if host in DOMAIN_TABLE:
        return DOMAIN_TABLE[host]

    # 研究生院子域常见前缀（yjs./yz./gs.…）
    head = host.split(".", 1)[0]
    for prefix, (stype, score) in DOMAIN_TABLE.items():
        if prefix.endswith(".") and (host.startswith(prefix) or head == prefix.rstrip(".")):
            return stype, score

    for suffix, stype, score in _SUFFIX_RULES:
        if host.endswith(suffix):
            return stype, score

    return DEFAULT_TYPE, DEFAULT_AUTHORITY


def authority_of(url_or_host: str) -> float:
    return classify(url_or_host)[1]


def source_type_of(url_or_host: str) -> str:
    return classify(url_or_host)[0]


def describe_table() -> List[Dict[str, object]]:
    """供 CLI/文档展示权威度表。"""
    rows: List[Dict[str, object]] = []
    for domain, (stype, score) in sorted(DOMAIN_TABLE.items(),
                                         key=lambda kv: -kv[1][1]):
        rows.append({"domain": domain, "source_type": stype, "authority": score})
    for suffix, stype, score in _SUFFIX_RULES:
        rows.append({"domain": f"*{suffix}", "source_type": stype, "authority": score})
    return rows


def is_official(url_or_host: str) -> bool:
    return source_type_of(url_or_host) in (
        "national_official", "graduate_school",
        "university_official", "official_document")


#: 不可当作「院校自有域名」的宿主：注册表对未收录院校会合成一个
#: 指向研招网站内检索的占位 URL，若当真会让查询改写去 `site:yz.chsi.com.cn`。
_NOT_SCHOOL_HOSTS = frozenset({"yz.chsi.com.cn", "chsi.com.cn", "yz.chsi.cn"})


def domains_for_school(school: str) -> Dict[str, str]:
    """由院校名取该院校的三个官方域名（学校 / 研究生院 / 研招办）。

    **只采信院校注册表里真正登记过的记录**。注册表对未收录院校会做「启发式合成」，
    此时 `chsi_code` 为占位值（如「待查」），且域名字段指向研招网站内检索 URL ——
    把这种东西当成院校官网会让查询改写产出 `site:yz.chsi.com.cn 某某大学 招生简章`
    这种误导性检索（实测确实如此）。查不到就返回空 dict，让改写退化为通用检索词。
    """
    name = str(school or "").strip()
    if not name:
        return {}
    try:
        try:
            from intelligence.registry import resolve_university
        except ImportError:                        # pragma: no cover
            from tools.intelligence.registry import resolve_university  # type: ignore

        entity = resolve_university(name)
    except Exception:                              # pragma: no cover
        return {}
    if entity is None:
        return {}

    code = str(getattr(entity, "chsi_code", "") or "").strip()
    if not code.isdigit():
        # 未登记（启发式合成）→ 不猜域名
        return {}

    out: Dict[str, str] = {}
    for key, attr in (("official", "official_domain"),
                      ("graduate", "graduate_domain"),
                      ("admission", "admission_domain")):
        value = str(getattr(entity, attr, "") or "").strip()
        host = _host(value) if value else ""
        if host and host not in _NOT_SCHOOL_HOSTS:
            out[key] = host
    return out


__all__ = [
    "DEFAULT_AUTHORITY",
    "DEFAULT_TYPE",
    "DOMAIN_TABLE",
    "authority_of",
    "classify",
    "describe_table",
    "domains_for_school",
    "is_official",
    "source_type_of",
]
