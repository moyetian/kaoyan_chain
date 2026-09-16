# -*- coding: utf-8 -*-
"""
检索 Provider 与相关性守门测试

夹具取自 2026-09-16 的真实响应片段（已裁剪），因此测的是**真实页面结构**，
而不是我臆想的正则。所有网络调用都被 monkeypatch 掉，CI 不联网也能跑。

关键回归：Bing 对裸 urllib 请求会返回 HTTP 200 + 无关内容 —— 这类「结果」必须
被相关性守门拦下并记为失败源，绝不能流到 Agent 的回答里。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.search import SearchQuery, SearchService  # noqa: E402
from tools.search.providers import ProviderError, available_providers, provider_names  # noqa: E402
from tools.search.providers import bing as bing_mod  # noqa: E402
from tools.search.providers import ddg as ddg_mod  # noqa: E402
from tools.search.providers import sogou as sogou_mod  # noqa: E402
from tools.search.providers import tavily as tavily_mod  # noqa: E402
from tools.search import relevance  # noqa: E402
from tools.search.cache import SearchCache  # noqa: E402


# ── 真实响应夹具（已裁剪） ──────────────────────────────────────

DDG_HTML = """
<div class="result">
  <a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fportal.smu.edu.cn%2Fyzw%2F&amp;rut=x">南方医科大学研究生招生网</a>
  <a class="result__snippet">南方医科大学2025年硕士研究生招生简章 2024/10/08</a>
</div>
<div class="result">
  <a rel="nofollow" class="result__a"
     href="https://www.kaoyan.cn/school/930/plan/1275688">2027年南方医科大学生物医学工程考研专业介绍</a>
  <a class="result__snippet">085409 生物医学工程 初试科目</a>
</div>
"""

#: Bing 软性反爬：查询「考研数学二 大纲」时真实返回的首条（与查询毫无关系）
BING_JUNK_HTML = """
<li class="b_algo"><h2 class=""><a target="_blank" href="https://support.google.com/youtubetv/?hl=en" h="ID=SERP">YouTube TV Help</a></h2>
<div class="b_caption"><p>Watch YouTube TV on supported devices.</p></div></li>
<li class="b_algo"><h2 class=""><a target="_blank" href="https://obsproject.com/tr/downLOAD" h="ID=SERP">İndir | OBS</a></h2>
<div class="b_caption"><p>OBS Studio indir.</p></div></li>
"""

SOGOU_HTML = """
<ul class="news-list"><li id="sogou_vr_11002601_box_0">
<div class="txt-box">
  <h3><a href="/link?url=abc&amp;type=2&amp;query=x">考研数学二复习规划（含 408 对比）</a></h3>
  <p class="txt-info">本文给出数学二全年复习节奏与真题使用建议。</p>
  <div class="s-p"><a class="account">某考研公众号</a><span class="s2">timeConvert('1735689600')</span></div>
</div></li>
<li id="sogou_vr_11002601_box_1">
<div class="txt-box">
  <h3><a href="/link?url=def&amp;type=2">政治帽子词速记</a></h3>
  <p class="txt-info">帽子词与历史节点速记卡片。</p>
  <div class="s-p"><a class="account">另一个公众号</a></div>
</div></li>
</ul>
"""

SOGOU_CAPTCHA_HTML = "<html>请输入验证码 SourceVerifyCode：480928ab 请协助验证</html>"
SOGOU_EMPTY_HTML = "<html>以下内容来自微信公众平台 呀！ 没有找到相关的微信公众号文章。</html>"


@pytest.fixture(autouse=True)
def _isolate_search_state():
    """隔离全局状态：provider 注册表与冷却表都是**进程级全局**的。

    不隔离的话，其它测试文件注册的假 provider（如 test_search_core 里的
    FakeProvider_*）会泄漏进 `available_providers()`，本文件的用例就会莫名
    多出几个源；冷却表同理。
    """
    from tools.search import health
    from tools.search.providers import _REGISTRY

    saved_registry = dict(_REGISTRY)
    health.reset()
    try:
        yield
    finally:
        health.reset()
        _REGISTRY.clear()
        _REGISTRY.update(saved_registry)


@pytest.fixture()
def patch_text(monkeypatch):
    """把各 provider 用的 get_text 换成夹具返回器。"""
    def _apply(module, html_text):
        monkeypatch.setattr(module, "get_text", lambda *a, **k: html_text)
    return _apply


# ── DDG ────────────────────────────────────────────────────────

def test_ddg_parses_real_markup_and_decodes_redirect(patch_text):
    patch_text(ddg_mod, DDG_HTML)
    results = ddg_mod.DuckDuckGoProvider().search("南方医科大学 085409 招生", limit=5)
    assert len(results) == 2
    assert results[0].title == "南方医科大学研究生招生网"
    assert results[0].url == "https://portal.smu.edu.cn/yzw/", "必须还原 uddg 跳转拿到真实链接"
    assert results[1].url.startswith("https://www.kaoyan.cn/")
    assert results[0].engine == "ddg"


def test_ddg_raises_when_structure_changes(patch_text):
    patch_text(ddg_mod, "<html>nothing here</html>")
    with pytest.raises(ProviderError):
        ddg_mod.DuckDuckGoProvider().search("x")


# ── Bing（软性反爬守门） ────────────────────────────────────────

def test_bing_parses_markup(patch_text):
    patch_text(bing_mod, BING_JUNK_HTML)
    results = bing_mod.BingProvider().search("考研数学二 大纲", limit=5)
    assert len(results) == 2
    assert results[0].url.startswith("https://support.google.com/")


def test_bing_junk_is_rejected_by_relevance_gate():
    """Bing 的「200 但无关内容」必须被守门判定为不相关。"""
    tokens = relevance.significant_tokens("考研数学二 大纲")
    assert not relevance.is_relevant(
        "YouTube TV Help", "Watch YouTube TV on supported devices.",
        "https://support.google.com/youtubetv/?hl=en", tokens)
    assert not relevance.is_relevant(
        "İndir | OBS", "OBS Studio indir.", "https://obsproject.com/tr/downLOAD", tokens)


def test_relevance_accepts_real_ddg_results():
    tokens = relevance.significant_tokens("南方医科大学 085409 招生")
    assert relevance.is_relevant("南方医科大学研究生招生网", "2025年硕士研究生招生简章",
                                 "https://portal.smu.edu.cn/yzw/", tokens)


def test_relevance_rejects_year_only_match():
    """反例：查询含 2027 时，日文垃圾页里的「2027年」不足以判定相关。"""
    tokens = relevance.significant_tokens("华中科技大学 计算机 2027 复试线")
    assert not relevance.is_relevant("エアコン2027年問題とは", "省エネ基準改正",
                                     "https://qbei.isotop.jp/archives/6643", tokens)


def test_relevance_pure_numeric_query_still_works():
    """纯数字查询（只有专业代码）退化为「命中任一 token」而不是一律不相关。"""
    tokens = relevance.significant_tokens("085409")
    assert relevance.is_relevant("招生目录", "085409 生物医学工程",
                                 "https://yjs.smu.edu.cn/a", tokens)


def test_service_marks_anti_bot_provider_as_failed(patch_text, tmp_path):
    """集成：Bing 返回垃圾 → 记为失败源，且结果里不出现垃圾。

    缓存必须指向临时目录：默认缓存落在 `.memory/search_cache.json`（跨运行留存），
    若之前联网跑过同一条查询，这里会命中**真实结果**的缓存而看不到"Bing 垃圾"，
    用例就会假失败。
    """
    patch_text(bing_mod, BING_JUNK_HTML)
    patch_text(ddg_mod, DDG_HTML)
    svc = SearchService.default(providers=[bing_mod.BingProvider(),
                                           ddg_mod.DuckDuckGoProvider()])
    svc._cache = SearchCache(path=tmp_path / "c.json")
    resp = svc.search(SearchQuery(text="南方医科大学 085409 招生", limit=5))

    assert resp.has_results and all("youtube" not in r.url for r in resp.results)
    failed = dict(resp.providers_failed)
    assert "bing" in failed and "无关" in failed["bing"]
    assert "ddg" in resp.providers_used


# ── 搜狗微信 ────────────────────────────────────────────────────

def test_sogou_parses_articles_and_marks_source_type(patch_text):
    patch_text(sogou_mod, SOGOU_HTML)
    results = sogou_mod.SogouWeixinProvider().search("考研数学二 复习规划", limit=5)
    assert len(results) == 2
    assert results[0].title.startswith("考研数学二复习规划")
    assert results[0].url.startswith("https://weixin.sogou.com/link?url=")
    assert results[0].source_type == "wechat", "链接指向公众号文章，不能被域名表判成普通聚合站"


def test_sogou_captcha_raises_not_silent(patch_text):
    patch_text(sogou_mod, SOGOU_CAPTCHA_HTML)
    with pytest.raises(ProviderError) as exc:
        sogou_mod.SogouWeixinProvider().search("x")
    assert "验证码" in str(exc.value) or "反爬" in str(exc.value)


def test_sogou_no_result_is_empty_not_error(patch_text):
    """「没有找到相关文章」是正常结果，不该当成失败。"""
    patch_text(sogou_mod, SOGOU_EMPTY_HTML)
    assert sogou_mod.SogouWeixinProvider().search("很冷门的查询词") == []


# ── 可选在线源 ──────────────────────────────────────────────────

def test_tavily_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert tavily_mod.TavilyProvider.is_available() is False
    with pytest.raises(ProviderError):
        tavily_mod.TavilyProvider().search("x")


def test_tavily_available_with_key(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "dummy")
    assert tavily_mod.TavilyProvider.is_available() is True
    assert [p.name for p in available_providers(["tavily"])] == ["tavily"]


# ── 注册表与优先级 ──────────────────────────────────────────────

def test_builtin_providers_registered():
    names = provider_names()
    for expected in ("ddg", "bing", "sogou-weixin", "tavily"):
        assert expected in names


def test_ddg_has_top_priority():
    """DDG 是实测可用的主力源，必须排在 Bing 前面。"""
    from tools.search.providers import get_provider_class
    ddg = get_provider_class("ddg")
    bing = get_provider_class("bing")
    assert ddg.priority < bing.priority


def test_service_queries_providers_in_priority_order(monkeypatch):
    order: List[str] = []

    class Probe:
        def __init__(self, name, priority):
            self.name, self.priority = name, priority

        def search(self, query, *, limit=10, time_range=None):
            order.append(self.name)
            return []

    svc = SearchService(providers=[Probe("late", 90), Probe("early", 10)])
    svc.search(SearchQuery(text="x"))
    assert order == ["early", "late"], "应按优先级从小到大查询"


# ── school_scout 委托（回到同一实现） ───────────────────────────

def test_school_scout_raw_web_search_delegates(monkeypatch):
    """旧调用点必须仍返回 {title,url,snippet} 形状（零破坏）。

    注意：本项目有「双路径导入」约定，`search` 与 `tools.search` 可能被导入成
    **两个模块对象**（两个类对象、两份 provider 注册表）。因此补丁要打在
    school_scout 实际解析到的那个模块上 —— 这里把两个可能的路径都覆盖。
    """
    import importlib

    from tools.skills import school_scout

    class _Resp:
        results = (
            type("R", (), {"title": "标题", "url": "https://yjs.smu.edu.cn/a",
                           "snippet": "摘要"})(),
        )

    class _Svc:
        def search(self, query):
            return _Resp()

    patched = 0
    for mod_name in ("search.service", "tools.search.service"):
        try:
            module = importlib.import_module(mod_name)
        except ImportError:
            continue
        monkeypatch.setattr(module.SearchService, "default",
                            classmethod(lambda cls: _Svc()))
        patched += 1
    assert patched >= 1, "未能定位 SearchService 模块"

    out = school_scout.raw_web_search("南方医科大学 研究生 就读体验", max_results=3)
    assert out == [{"title": "标题", "url": "https://yjs.smu.edu.cn/a", "snippet": "摘要"}]


def test_school_scout_reexports_dossier_functions():
    """经验档案逻辑已拆到独立模块，但旧引用路径必须继续可用。"""
    from tools.skills import school_scout
    for name in ("filter_community_experiences", "save_experience_dossier",
                 "append_experience_to_dossier", "apply_scout_to_config"):
        assert callable(getattr(school_scout, name)), f"{name} 未重新导出"


def test_school_scout_within_size_ceiling():
    """就近拆分后必须回到硬红线以内（改造前 1069 行）。"""
    lines = len((ROOT / "tools" / "skills" / "school_scout.py")
                .read_text(encoding="utf-8").splitlines())
    assert lines <= 800, f"school_scout.py 仍有 {lines} 行"


# ── 真实联网验证（默认跳过，CI 不跑） ──────────────────────────
# 项目已声明 live marker；这里再加环境变量门闩，避免默认执行时因无网络而红。
# 手动验证：KY_LIVE_TEST=1 py -m pytest tests/test_search_providers.py -m live -q

@pytest.mark.live
@pytest.mark.skipif(os.environ.get("KY_LIVE_TEST") != "1",
                    reason="联网测试需显式开启（KY_LIVE_TEST=1）")
def test_live_search_returns_results_with_real_urls():
    """真实检索：必须返回带真实链接、且与查询相关的结果。

    跳过与失败的判据：
      * **没有任何源成功** → 跳过（引擎限流/断网属环境条件，不是代码缺陷）；
      * **源成功了却无合格结果** → 失败（说明流水线有问题）。
    """
    svc = SearchService.default()
    resp = svc.search(SearchQuery(text="华中科技大学 计算机 复试线", limit=5))

    if not resp.providers_used:
        pytest.skip(f"当前无可用检索源（引擎限流或网络不通）：{resp.summary_line()}")

    assert resp.has_results, f"源已返回但无结果，流水线可能有问题：{resp.summary_line()}"
    assert all(r.url.startswith("http") for r in resp.results), "每条结果都必须有真实链接"
    tokens = relevance.significant_tokens("华中科技大学 计算机 复试线")
    assert any(relevance.is_relevant(r.title, r.snippet, r.url, tokens)
               for r in resp.results), f"结果应与查询相关：{[r.title[:20] for r in resp.results]}"


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("KY_LIVE_TEST") != "1",
                    reason="联网测试需显式开启（KY_LIVE_TEST=1）")
def test_live_web_source_prefers_official_domains():
    """有**网页源**（ddg/bing）真正返回时，应能命中该校官方域名。

    若网页源都被限流（本轮实测 DDG 会限流、Bing 返回无关内容），公众号源仍会出结果，
    但那不该用来判定"官方召回" —— 故此处显式跳过，避免把环境波动当成代码回归。
    """
    svc = SearchService.default()
    resp = svc.search(SearchQuery(text="华中科技大学 计算机 复试线", limit=5))
    web_used = [n for n in resp.providers_used
                if n.replace("(缓存)", "") in ("ddg", "bing")]
    if not web_used:
        pytest.skip(f"网页源未参与或无结果，无法验证官方召回：{resp.summary_line()}")

    assert any("hust.edu.cn" in r.domain for r in resp.results),         f"网页源已返回，却未命中官方域名：{[r.domain for r in resp.results]}"
