# -*- coding: utf-8 -*-
"""
KaoYan Intelligence · 引文溯源与反幻觉校验引擎 (Citation Engine)

职责：确保任何写入证据链的“事实片段”都能在其来源原文中**逐字命中**。
这是本项目「搜索只负责发现、官方页面才构成证据」原则的技术兜底 ——
一旦引文在来源中找不到，该条证据必须降级为 UNVERIFIED。

设计要点
--------
1. **双路径数据模型**：安装了 pydantic 时复用其校验能力；未安装时使用内置的
   轻量等价实现。两条路径的**构造与取值行为保持一致**（均支持关键字构造、
   均拒绝未知字段、均在缺失必填字段时抛 TypeError），不会出现
   “没装 pydantic 就完全不能用” 的情况。
   （注：字段取值合法性由 :func:`verify_citations` 统一校验，以便两条路径行为完全一致。）

2. **引文比对忽略空白差异**（换行 / 缩进 / 连续空格会被折叠），但**实义字符序列
   必须完全一致** —— 不允许增删、改写任何字符。这样既能容忍 HTML 抽取造成的
   空白噪声，又不给模型留下编造空间。

3. **空引文一律视为未溯源**。历史实现直接做 ``cited_text in doc``，而
   ``"" in doc`` 恒为 True，导致空引文蒙混过关，本模块显式拒绝。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence, Tuple

__all__ = [
    "Citation",
    "Answer",
    "UngroundedCitation",
    "ALLOWED_CONFIDENCE",
    "ALLOWED_CITATION_TYPES",
    "has_pydantic",
    "verify_citation_excerpt",
    "verify_citations",
]

#: 允许的置信度取值（与 LLM 结构化输出契约一致）
ALLOWED_CONFIDENCE: Tuple[str, ...] = ("high", "medium", "low")

#: 允许的引文定位方式
ALLOWED_CITATION_TYPES: Tuple[str, ...] = ("char_location", "content_block", "chunk_id")

DEFAULT_CITATION_TYPE = ALLOWED_CITATION_TYPES[0]
DEFAULT_CONFIDENCE = ALLOWED_CONFIDENCE[-1]

try:  # pragma: no cover - 取决于运行环境是否安装 pydantic
    from pydantic import BaseModel as _PydanticBaseModel

    _HAS_PYDANTIC = True
except ImportError:  # pragma: no cover
    _PydanticBaseModel = None  # type: ignore[assignment]
    _HAS_PYDANTIC = False


class UngroundedCitation(Exception):
    """引文无法在来源原文中定位（疑似编造）。"""


def has_pydantic() -> bool:
    """当前运行环境是否使用 pydantic 版本的数据模型。"""
    return _HAS_PYDANTIC


def _normalize_ws(text: Any) -> str:
    """折叠全部空白为单个空格并去除首尾空白（不改变任何实义字符）。"""
    return re.sub(r"\s+", " ", str(text if text is not None else "")).strip()


def _as_source_candidates(source_text: Any) -> List[str]:
    """把 ``source_text`` 归一化为候选来源列表。

    支持传入单个字符串，或字符串序列（如 ``[原始 HTML, 反转义后的 HTML]``）——
    只要引文命中**任一**候选来源即视为已溯源。这一点对网页抽取很重要：
    抽取器常先做 HTML 实体反转义，反转义后的片段未必逐字出现在原始 HTML 中。
    """
    if source_text is None:
        return []
    if isinstance(source_text, (str, bytes)):
        return [source_text.decode("utf-8", "replace") if isinstance(source_text, bytes) else str(source_text)]
    if isinstance(source_text, (list, tuple, set)):
        out: List[str] = []
        for item in source_text:
            out.extend(_as_source_candidates(item))
        return out
    return [str(source_text)]


# ════════════════════════════════════════════════════════════════
# 数据模型（pydantic / 内置等价实现 双路径）
# ════════════════════════════════════════════════════════════════

if _HAS_PYDANTIC:

    try:  # pydantic v2
        from pydantic import ConfigDict

        _BASE_CONFIG = ConfigDict(extra="forbid")  # 与内置实现的“拒绝未知字段”保持一致
    except ImportError:  # pragma: no cover - pydantic v1 兼容
        _BASE_CONFIG = None

    class _CompatBase(_PydanticBaseModel):  # type: ignore[misc,valid-type]
        """统一配置基类：禁止未知字段，使两条路径的构造行为完全一致。"""

        if _BASE_CONFIG is not None:  # pydantic v2
            model_config = _BASE_CONFIG
        else:  # pragma: no cover - pydantic v1
            class Config:
                extra = "forbid"

    class Citation(_CompatBase):  # type: ignore[misc]
        """单条引文：指向某个输入文档中的一段原文片段。"""

        type: str = DEFAULT_CITATION_TYPE
        cited_text: str
        document_index: int

    class Answer(_CompatBase):  # type: ignore[misc]
        """待校验的结构化回答。"""

        answer: str
        citations: List[Citation] = []
        confidence: str = DEFAULT_CONFIDENCE

else:

    class _CompatModel:  # pragma: no cover - 仅在无 pydantic 环境启用
        """未安装 pydantic 时的等价数据模型。

        提供与 pydantic 相同的“关键字构造 + 必填校验 + 拒绝未知字段”语义，
        使上游代码无需感知依赖是否存在。
        """

        _FIELDS: Tuple[str, ...] = ()
        _REQUIRED: Tuple[str, ...] = ()
        _DEFAULTS: Dict[str, Any] = {}

        def __init__(self, **kwargs: Any) -> None:
            unknown = set(kwargs) - set(self._FIELDS)
            if unknown:
                raise TypeError(
                    f"{type(self).__name__}() got unexpected field(s): {sorted(unknown)}"
                )
            missing = [f for f in self._REQUIRED if kwargs.get(f) is None]
            if missing:
                raise TypeError(
                    f"{type(self).__name__}() missing required field(s): {missing}"
                )
            for field in self._FIELDS:
                value = kwargs.get(field, self._DEFAULTS.get(field))
                if field in self._DEFAULTS and value is None:
                    value = self._DEFAULTS[field]
                setattr(self, field, value)

        def __repr__(self) -> str:  # pragma: no cover - 仅调试可读性
            inner = ", ".join(f"{f}={getattr(self, f, None)!r}" for f in self._FIELDS)
            return f"{type(self).__name__}({inner})"

        def model_dump(self) -> Dict[str, Any]:
            """与 pydantic v2 同名方法，便于调用方无差别处理。"""
            return {f: getattr(self, f, None) for f in self._FIELDS}

        # pydantic v1 风格别名
        dict = model_dump

    class Citation(_CompatModel):
        _FIELDS = ("type", "cited_text", "document_index")
        _REQUIRED = ("cited_text", "document_index")
        _DEFAULTS = {"type": DEFAULT_CITATION_TYPE}

    class Answer(_CompatModel):
        _FIELDS = ("answer", "citations", "confidence")
        _REQUIRED = ("answer",)
        _DEFAULTS = {"citations": [], "confidence": DEFAULT_CONFIDENCE}

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            if self.citations is None:
                self.citations = []
            elif not isinstance(self.citations, list):
                self.citations = list(self.citations)


def _get(obj: Any, field: str, default: Any = None) -> Any:
    """pydantic 模型 / 兼容模型 / dict 三种载体的统一字段读取。"""
    if isinstance(obj, dict):
        return obj.get(field, default)
    return getattr(obj, field, default)


def _coerce_answer(raw: Any) -> "Answer":
    """把 dict / Answer 统一转换（校验）为 Answer 对象。

    接受 dict 是为了让调用方（如 JSON 模式的 LLM 输出）无需先构造模型。
    """
    if isinstance(raw, dict):
        citations = raw.get("citations") or []
        return Answer(
            answer=raw.get("answer", ""),
            citations=[c if isinstance(c, Citation) else Citation(**_as_citation_kwargs(c)) for c in citations],
            confidence=raw.get("confidence", DEFAULT_CONFIDENCE),
        )
    if isinstance(raw, Answer):
        return raw
    raise UngroundedCitation(
        f"无法识别的回答载体：{type(raw).__name__}（应为 Answer 或 dict）"
    )


def _as_citation_kwargs(raw: Any) -> Dict[str, Any]:
    """把单条引文的 dict / 模型统一成 Citation 构造参数。"""
    if isinstance(raw, dict):
        return {
            "type": raw.get("type", DEFAULT_CITATION_TYPE),
            "cited_text": raw.get("cited_text", ""),
            "document_index": raw.get("document_index", -1),
        }
    return {
        "type": _get(raw, "type", DEFAULT_CITATION_TYPE),
        "cited_text": _get(raw, "cited_text", ""),
        "document_index": _get(raw, "document_index", -1),
    }


# ════════════════════════════════════════════════════════════════
# 引文定位
# ════════════════════════════════════════════════════════════════

def verify_citation_excerpt(
    quote: Any,
    source_text: Any,
    require_exact: bool = False,
) -> Tuple[bool, str]:
    """校验一段引文是否确实出自 ``source_text``。

    Args:
        quote: 待校验的引文片段。
        source_text: 引文声称的来源原文。可以是**单个字符串**，也可以是
            **字符串序列**（任一命中即通过），例如
            ``[原始 HTML, html.unescape(HTML)]`` —— 网页抽取常先做实体反转义，
            反转义后的片段未必逐字出现在原始 HTML 中，故允许给出多个候选来源。
        require_exact: True 时要求**逐字节**完全一致（不折叠空白）；
            默认 False，折叠空白后比对实义字符序列。

    Returns:
        ``(是否命中, 说明)``。说明用于写入证据对象的 ``conflict_detail``。

    Notes:
        空引文 / 空白引文 / 空来源一律判定为未命中 —— 这是本模块对
        ``"" in doc`` 恒真这一历史漏洞的修复。
    """
    raw_quote = str(quote if quote is not None else "")
    candidates = _as_source_candidates(source_text)

    if not raw_quote.strip():
        return False, "引文为空或仅含空白，无法作为溯源依据"
    if not candidates or all(not c.strip() for c in candidates):
        return False, "来源原文为空，无法核验引文出处"

    for source in candidates:
        if raw_quote in source:
            return True, "引文在来源原文中逐字命中"

    if require_exact:
        return False, "引文未在来源原文中逐字命中（严格模式，未折叠空白）"

    norm_quote = _normalize_ws(raw_quote)
    if norm_quote:
        for source in candidates:
            if norm_quote in _normalize_ws(source):
                return True, "引文在来源原文中命中（已忽略换行/缩进等空白差异）"

    return False, "引文未能在来源原文中定位，疑似模型编造或来源已变更"


def verify_citations(
    ans: Any,
    docs: Sequence[str],
    require_exact: bool = False,
) -> "Answer":
    """校验回答中的每一条引文都能在 ``docs`` 中逐字命中。

    Args:
        ans: ``Answer`` 对象，或等价的 dict（如 LLM 的 JSON 输出）。
        docs: 输入文档列表；``Citation.document_index`` 即其下标。
        require_exact: 见 :func:`verify_citation_excerpt`。

    Returns:
        校验通过的 ``Answer`` 对象（原样返回，便于链式调用）。

    Raises:
        UngroundedCitation: 出现以下任一情况时抛出 ——
            * 引文为空 / 仅空白；
            * ``document_index`` 越界；
            * 引文无法在对应文档中定位；
            * ``confidence`` 取值非法；
            * 高/中置信度却**未附任何引文**（无据断言）。
    """
    answer = _coerce_answer(ans)

    confidence = str(_get(answer, "confidence", DEFAULT_CONFIDENCE) or DEFAULT_CONFIDENCE).strip().lower()
    if confidence not in ALLOWED_CONFIDENCE:
        raise UngroundedCitation(
            f"非法的 confidence 取值: {confidence!r}（应为 {list(ALLOWED_CONFIDENCE)} 之一）"
        )

    citations = list(_get(answer, "citations", None) or [])
    doc_list = list(docs or [])

    for idx, citation in enumerate(citations):
        ctype = str(_get(citation, "type", DEFAULT_CITATION_TYPE) or DEFAULT_CITATION_TYPE)
        if ctype not in ALLOWED_CITATION_TYPES:
            raise UngroundedCitation(
                f"第 {idx + 1} 条引文的定位方式非法: {ctype!r}（应为 {list(ALLOWED_CITATION_TYPES)} 之一）"
            )

        doc_idx = _get(citation, "document_index", -1)
        try:
            doc_idx = int(doc_idx)
        except (TypeError, ValueError):
            raise UngroundedCitation(f"第 {idx + 1} 条引文的 document_index 非整数: {doc_idx!r}")

        if not (0 <= doc_idx < len(doc_list)):
            raise UngroundedCitation(
                f"引文指向的文档下标越界: {doc_idx}（可用文档数 {len(doc_list)}）"
            )

        ok, reason = verify_citation_excerpt(
            _get(citation, "cited_text", ""), doc_list[doc_idx], require_exact=require_exact
        )
        if not ok:
            excerpt = _normalize_ws(_get(citation, "cited_text", ""))[:40]
            raise UngroundedCitation(
                f"第 {idx + 1} 条引文未溯源（文档 #{doc_idx}）: {reason} ｜ 引文片段: {excerpt!r}"
            )

    if confidence != DEFAULT_CONFIDENCE and not citations:
        raise UngroundedCitation(
            "缺少引用：confidence 为 "
            f"{confidence!r} 的回答必须附带至少一条可溯源引文"
        )

    return answer
