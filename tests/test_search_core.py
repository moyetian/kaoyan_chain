# -*- coding: utf-8 -*-
"""
检索运行时（tools/search）核心回归测试

覆盖「检索链路的契约」而不是「能不能连上 Bing」——后者依赖外网，
放进 benchmark 里按需开启。这里用假 provider 驱动真实服务代码，断言：

  * 结果一定带 provenance（来源类型 + 权威分），否则「官方来源」无从判定
  * 一个源失败不能拖垮整次检索，且失败原因要如实带出来
  * site: 语义按「域名或子域」匹配（`smu.edu.cn` 要能匹配 `yjs.smu.edu.cn`）
  * 零可用 provider 时给结构化空结果，不抛栈、也不假装「已检索」
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.search import (  # noqa: E402
    SearchProvider,
    SearchQuery,
    SearchResult,
    SearchService,
    format_results,
    provider_names,
    register,
)
from tools.search import source_registry  # noqa: E402
from tools.search.models import Document, domain_of, merge_responses  # noqa: E402
from tools.search.providers import ProviderError, available_providers  # noqa: E402
from tools.search.providers.base import NullProvider  # noqa: E402


# ── 测试替身 ────────────────────────────────────────────────────

DEFAULT_ITEMS = (
    {"title": "2027 招生专业目录", "url": "https://yjs.smu.edu.cn/a.html",
     "snippet": "085409 生物医学工程"},
    {"title": "某机构解读", "url": "https://mp.weixin.qq.com/s/x",
     "snippet": "据说招 58 人"},
)


class FakeProvider(SearchProvider):
    """返回预置结果，可按需抛错。

    注意 `name` 是**类属性**（与 SearchProvider 约定一致）——注册表按类属性登记，
    实例若在 __init__ 里改名，注册表与实例就会对不上。
    """

    name = "fake"
    items = DEFAULT_ITEMS
    error = None
    available = True
    explode = False

    @classmethod
    def is_available(cls) -> bool:      # type: ignore[override]
        return bool(cls.available)

    def search(self, query: str, *, limit: int = 10,
               time_range: Optional[str] = None) -> List[SearchResult]:
        if self.explode:
            raise RuntimeError("解析页面时崩了")
        if self.error:
            raise ProviderError(self.error)
        return self.normalize(list(self.items)[:limit])


def make_provider(name: str, *, items=None, error=None, available=True,
                  explode=False):
    """按名构造一个 provider 子类（name 必须是类属性，便于注册表登记）。"""
    attrs: Dict[str, Any] = {"name": name, "available": available,
                             "explode": explode}
    if items is not None:
        attrs["items"] = tuple(items)
    if error is not None:
        attrs["error"] = error
    return type(f"FakeProvider_{name}", (FakeProvider,), attrs)


class FakeFetcher:
    """假抓取器：验证 fetch 的解析与失败语义，不碰网络。"""

    def __init__(self, content: str = "", valid: bool = True):
        self._content = content
        self._valid = valid
        self.calls: List[str] = []

    def fetch(self, url: str):
        self.calls.append(url)

        class _Res:
            is_valid = self._valid
            content = self._content

        return _Res()


# ── 模型层 ──────────────────────────────────────────────────────

def test_search_result_normalizes_domain_and_keys():
    """provider 的原始 dict 键名不统一，模型层要容错。"""
    r = SearchResult.from_raw({"name": "标题", "link": "https://www.SMU.edu.cn/x",
                               "desc": "摘要"}, engine="bing")
    assert r.title == "标题" and r.snippet == "摘要"
    assert r.domain == "smu.edu.cn", "域名应小写并去掉 www."
    assert r.engine == "bing"


def test_domain_of_handles_bad_input():
    assert domain_of("not a url") == ""
    assert domain_of("") == ""
    assert domain_of("https://WWW.Example.COM/path") == "example.com"


def test_is_official_depends_on_source_type_not_content():
    """公众号写得再专业也不等于官方来源 —— 判定只认来源类型。"""
    wechat = SearchResult(title="权威解读（正文很长很专业）", url="https://mp.weixin.qq.com/s/x",
                          source_type="wechat")
    official = SearchResult(title="目录", url="https://yjs.smu.edu.cn/a",
                            source_type="graduate_school")
    assert wechat.is_official is False
    assert official.is_official is True


def test_query_with_text_preserves_filters():
    q = SearchQuery(text="原始", limit=3, domains=("smu.edu.cn",), year=2027)
    q2 = q.with_text("改写后的词")
    assert q2.text == "改写后的词"
    assert q2.limit == 3 and q2.domains == ("smu.edu.cn",) and q2.year == 2027


def test_response_summary_and_dict():
    from tools.search.models import SearchResponse
    resp = SearchResponse(
        query="q", results=(SearchResult(title="t", url="https://a.edu.cn/x"),),
        providers_used=("bing",), providers_failed=(("ddg", "超时"),),
        candidates=10, duplicates=2, year=2027)
    line = resp.summary_line()
    assert "provider 1 个" in line and "候选 10" in line and "去重 2" in line
    assert "失败 ddg(超时)" in line
    payload = resp.to_dict()
    # 未标注 source_type 的结果不算官方来源（归类由服务层完成，见下方服务层用例）
    assert payload["year"] == 2027
    assert payload["results"][0]["is_official"] is False


def test_merge_responses_dedups_provider_names():
    from tools.search.models import SearchResponse
    a = SearchResponse(query="a", providers_used=("bing",), candidates=3)
    b = SearchResponse(query="b", providers_used=("bing", "ddg"), candidates=4)
    merged = merge_responses([a, b], query="a|b")
    assert merged.providers_used == ("bing", "ddg")
    assert merged.candidates == 7 and merged.queries_run == 2


# ── 来源权威度 ──────────────────────────────────────────────────

@pytest.mark.parametrize("url,expected_type", [
    ("https://yz.chsi.com.cn/x", "national_official"),
    ("https://yjs.smu.edu.cn/a", "graduate_school"),
    ("https://www.smu.edu.cn/a", "university_official"),
    ("https://mp.weixin.qq.com/s/x", "wechat"),
    ("https://zhihu.com/question/1", "community"),
    ("https://random-blog.xyz/p", "unknown"),
])
def test_source_classification(url, expected_type):
    assert source_registry.source_type_of(url) == expected_type


def test_official_sources_outrank_selfmedia():
    official = source_registry.authority_of("https://yjs.smu.edu.cn/a")
    wechat = source_registry.authority_of("https://mp.weixin.qq.com/s/x")
    assert official > wechat


def test_domains_for_school_from_registry():
    """院校三域名来自院校注册表；查不到时返回空 dict（不编域名）。"""
    domains = source_registry.domains_for_school("华中科技大学")
    assert domains.get("official", "").endswith("hust.edu.cn")
    assert source_registry.domains_for_school("完全不存在的野鸡大学xyz") == {}


# ── Registry ────────────────────────────────────────────────────

def test_register_and_names():
    TmpProvider = make_provider("tmp-registry-probe")
    register(TmpProvider)                   # type: ignore[arg-type]

    assert "tmp-registry-probe" in provider_names()
    assert any(p.name == "tmp-registry-probe" for p in available_providers(["tmp-registry-probe"]))


def test_unknown_provider_is_skipped_not_crash():
    assert available_providers(["no-such-provider"]) == []


def test_unavailable_provider_is_skipped():
    register(make_provider("tmp-unavailable-probe", available=False))  # type: ignore[arg-type]
    assert available_providers(["tmp-unavailable-probe"]) == []


def test_null_provider_returns_empty():
    assert NullProvider().search("x") == []


def test_provider_abstract_method_enforced():
    with pytest.raises(TypeError):
        SearchProvider()                    # type: ignore[abstract]


# ── 服务层 ──────────────────────────────────────────────────────

def _service(*providers, fetcher=None) -> SearchService:
    """直接注入实例（绕过注册表，便于构造异常场景）。"""
    return SearchService(providers=list(providers), fetcher=fetcher)


def _p(name: str = "fake", **kw) -> FakeProvider:
    return make_provider(name, **kw)()


def test_results_are_annotated_with_source_type_and_authority():
    svc = _service(_p())
    resp = svc.search(SearchQuery(text="q", limit=10))
    assert resp.has_results
    by_host = {r.domain: r for r in resp.results}
    assert by_host["yjs.smu.edu.cn"].source_type == "graduate_school"
    assert by_host["yjs.smu.edu.cn"].authority > 0.9
    assert by_host["mp.weixin.qq.com"].authority < 0.6


def test_official_result_ranks_first():
    """无重排模块时也按权威度排序，官方目录不能排在公众号后面。"""
    svc = _service(_p())
    resp = svc.search(SearchQuery(text="q", limit=10))
    assert resp.results[0].domain == "yjs.smu.edu.cn"


def test_site_filter_matches_subdomain():
    svc = _service(_p())
    resp = svc.search(SearchQuery(text="q", domains=("smu.edu.cn",), limit=10))
    assert [r.domain for r in resp.results] == ["yjs.smu.edu.cn"]


def test_exclude_domain_filter():
    svc = _service(_p())
    resp = svc.search(SearchQuery(text="q", exclude_domains=("mp.weixin.qq.com",), limit=10))
    assert all(r.domain != "mp.weixin.qq.com" for r in resp.results)


def test_provider_error_is_collected_not_raised():
    svc = _service(_p("ok"), _p("bad", error="连接超时"))
    resp = svc.search(SearchQuery(text="q"))
    assert resp.has_results, "一个源失败不应让整次检索失败"
    assert ("bad", "连接超时") in resp.providers_failed
    assert "ok" in resp.providers_used


def test_provider_unexpected_exception_is_collected():
    svc = _service(_p("boom", explode=True))
    resp = svc.search(SearchQuery(text="q"))
    assert resp.providers_failed and "未预期异常" in resp.providers_failed[0][1]


def test_no_provider_returns_structured_empty_response():
    """零可用源时必须给结构化结果，且如实说明「没有可用的检索源」。"""
    resp = _service().search(SearchQuery(text="q"))
    assert not resp.has_results
    assert resp.providers_failed and "可用" in resp.providers_failed[0][1]
    assert "未找到结果" in format_results(resp)
    assert "失败源" in format_results(resp)


def test_limit_is_respected():
    items = [{"title": f"t{i}", "url": f"https://yjs.smu.edu.cn/{i}"} for i in range(10)]
    svc = _service(_p(items=items))
    resp = svc.search(SearchQuery(text="q", limit=3))
    assert len(resp.results) == 3
    assert resp.candidates == 10, "候选数应记录去重前总量（供解释器展示）"


def test_format_results_includes_urls_and_failures():
    svc = _service(_p(), _p("ddg", error="被限流"))
    out = format_results(svc.search(SearchQuery(text="查询词")))
    assert "查询词" in out and "https://yjs.smu.edu.cn" in out
    assert "未参与的检索源" in out and "被限流" in out


# ── fetch（证据） ───────────────────────────────────────────────

def test_fetch_extracts_title_and_marks_html():
    html = "<html><head><title>2027 专业目录</title></head><body>正文</body></html>"
    svc = _service(fetcher=FakeFetcher(html))
    doc = svc.fetch("https://yjs.smu.edu.cn/a.html")
    assert doc.ok and doc.title == "2027 专业目录" and doc.content_type == "html"
    assert doc.to_dict()["length"] > 0


def test_fetch_rejects_non_http_without_crashing():
    doc = _service().fetch("ftp://example.com/x")
    assert not doc.ok and "error" in doc.meta


def test_fetch_failure_is_reported_not_faked():
    doc = _service(fetcher=FakeFetcher(valid=False)).fetch("https://x.edu.cn/a")
    assert not doc.ok and doc.meta.get("error")


def test_inspect_returns_only_matching_lines():
    """inspect 用于「打开网页找关键词」，避免把整页正文塞进上下文。"""
    content = ("<html><body>"
               "第一段无关内容。\n"
               "085409 生物医学工程 拟招生 12 人。\n"
               "第三段也无关。</body></html>")
    svc = _service(fetcher=FakeFetcher(content))
    hits = svc.inspect("https://yjs.smu.edu.cn/a", r"085409")
    assert len(hits) == 1 and "085409" in hits[0]


def test_pdf_document_is_honest_when_extractor_missing():
    doc = _service().fetch("https://yjs.smu.edu.cn/目录.pdf")
    assert doc.content_type == "pdf"
    if not doc.ok:
        assert "PDF" in doc.meta.get("error", "")
