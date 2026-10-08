# -*- coding: utf-8 -*-
"""[P0-7 修复·2026-10-08] 搜狗微信源两处解析失效的回归测试。

缺陷（2026-10-08 真实结果页实测）：
1. 结果 href 的 query 参数带**字面空格**（10/10，如
   ``…&query=%E9%A9%AC%E5%85%8B%E6%80%9D %E8%80%83%E7%A0%94&token=…``），
   未补百分号编码时进 urllib 抛 ``InvalidURL: URL can't contain control
   characters``，整条检索链必失败；
2. 账号节点从 ``class="account"`` 迁到 ``<span class="all-time-y2">``
   （class="account" 实测 0 命中），只认 L1 的解析使账号字段恒为空。

修复：解析共享件单源化到 ``tools/search/providers/_http.py``
（``normalize_search_url`` / ``parse_sogou_account``），provider 层与
skills 层（wechat_searcher）共用同一实现。

本文件全部使用**合成夹具**（身份中性化），不联网。
"""

from __future__ import annotations

import http.client
import sys
import urllib.parse as up
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.search.providers import sogou as sogou_mod  # noqa: E402
from tools.search.providers._http import (  # noqa: E402
    normalize_search_url,
    parse_sogou_account,
)
from tools.skills.wechat_searcher import (  # noqa: E402
    WeChatSearchEngine,
    parse_sogou_wechat_html,
)


# ── 合成夹具（模拟 2026-10-08 真实页面结构） ──────────────────────────────

#: box_0：现版结构——href 带字面空格 + 账号在 all-time-y2；
#: box_1：老版结构——class="account"（L1 兼容）；
#: box_2：无账号节点（边界：provider 不得凭空猜账号）。
SOGOU_SPACE_HTML = """
<ul class="news-list">
  <li id="sogou_vr_11002601_box_0">
    <div class="txt-box">
      <h3>
        <a href="/link?url=abc123&amp;type=2&amp;query=%E7%A4%BA%E4%BE%8B %E8%80%83%E7%A0%94&amp;token=DEADBEEF" target="_blank">
          示例标题一：背诵要点整理
        </a>
      </h3>
      <p class="txt-info">示例摘要一。</p>
      <div class="s-p">
        <span class="all-time-y2">示例政治笔记</span>
        <span class="s2"><script>timeConvert('1758196800')</script></span>
      </div>
    </div>
  </li>
  <li id="sogou_vr_11002601_box_1">
    <div class="txt-box">
      <h3><a href="/link?url=def456&amp;type=2">示例标题二（老版结构）</a></h3>
      <p class="txt-info">示例摘要二。</p>
      <div class="s-p">
        <a class="account" href="javascript:void(0);">旧版账号甲</a>
      </div>
    </div>
  </li>
  <li id="sogou_vr_11002601_box_2">
    <div class="txt-box">
      <h3><a href="/link?url=ghi789&amp;type=2">示例标题三（无账号节点）</a></h3>
      <p class="txt-info">示例摘要三，无账号字段。</p>
    </div>
  </li>
</ul>
"""

#: 老版页面整体夹具：无空格 href + class="account"（既有测试口径兼容）
SOGOU_LEGACY_HTML = """
<ul class="news-list"><li id="sogou_vr_11002601_box_0">
<div class="txt-box">
  <h3><a href="/link?url=abc&amp;type=2&amp;query=x">示例标题（老版）</a></h3>
  <p class="txt-info">示例摘要。</p>
  <div class="s-p"><a class="account">旧版账号乙</a><span class="s2">timeConvert('1735689600')</span></div>
</div></li>
</ul>
"""


@pytest.fixture()
def patch_sogou_text(monkeypatch):
    """把 sogou provider 的 get_text 换成夹具返回器（不联网）。"""
    def _apply(html_text: str):
        monkeypatch.setattr(sogou_mod, "get_text", lambda *a, **k: html_text)
    return _apply


def _assert_request_line_ok(url: str) -> None:
    """模拟 urlopen 内部的请求行校验（零网络）：

    ``http.client.putrequest`` 与 urlopen 走同一条校验路径——URL 里带空格等
    控制字符时在这里抛 ``InvalidURL``，正是本缺陷「必失败」的现场。
    """
    parts = up.urlsplit(url)
    target = parts.path + ("?" + parts.query if parts.query else "")
    conn = http.client.HTTPConnection("weixin.sogou.com")
    try:
        conn.putrequest("GET", target)   # 含非法字符在此抛 InvalidURL
    finally:
        conn.close()


# ── 1. normalize_search_url：编码边界与幂等 ─────────────────────────────

def test_normalize_search_url_encodes_literal_space():
    raw = ("https://weixin.sogou.com/link?url=abc_-x..&type=2"
           "&query=%E7%A4%BA%E4%BE%8B %E8%80%83%E7%A0%94&token=DEADBEEF")
    out = normalize_search_url(raw)

    assert " " not in out, "字面空格必须被编码（否则 urlopen 抛 InvalidURL）"
    assert "%20" in out
    assert "%E7%A4%BA%E4%BE%8B" in out, "已有 %XX 转义必须原样保留"
    assert "%25" not in out, "不得把已有 %XX 二次编码成 %25XX"
    assert out.startswith("https://weixin.sogou.com/link?url=abc_-x..&type=2&"), \
        "URL 结构字符（:/?&=）不得被编码"
    _assert_request_line_ok(out)


def test_normalize_search_url_is_idempotent_and_safe_on_empty():
    raw = "https://weixin.sogou.com/link?url=a&query=%E7%A4%BA%E4%BE%8B %E8%80%83"
    once = normalize_search_url(raw)
    assert normalize_search_url(once) == once, "规范化必须幂等（重复应用不漂移）"
    assert normalize_search_url("") == ""


# ── 2. sogou provider：空格 href + 账号三级回退 ─────────────────────────

def test_sogou_provider_fixes_space_url_and_reads_l2_account(patch_sogou_text):
    patch_sogou_text(SOGOU_SPACE_HTML)
    results = sogou_mod.SogouWeixinProvider().search("示例查询", limit=5)

    assert len(results) == 3
    first = results[0]
    assert " " not in first.url
    assert "%20" in first.url
    assert "%25" not in first.url
    assert first.url.startswith("https://weixin.sogou.com/link?url=")
    _assert_request_line_ok(first.url)
    assert first.extra.get("account") == "示例政治笔记", \
        "现版页面账号在 all-time-y2（L2），必须被解析出来"


def test_sogou_provider_keeps_legacy_l1_account(patch_sogou_text):
    """老版 class="account" 结构（既有夹具口径）仍按 L1 解析。"""
    patch_sogou_text(SOGOU_SPACE_HTML)
    results = sogou_mod.SogouWeixinProvider().search("示例查询", limit=5)

    second = results[1]
    assert second.extra.get("account") == "旧版账号甲"
    assert " " not in second.url
    assert second.url.startswith("https://weixin.sogou.com/link?url=")


def test_sogou_provider_without_account_node_yields_empty(patch_sogou_text):
    """无账号节点：provider 层只认结构化字段，不做句式猜测（保持原语义）。"""
    patch_sogou_text(SOGOU_SPACE_HTML)
    results = sogou_mod.SogouWeixinProvider().search("示例查询", limit=5)

    third = results[2]
    assert third.extra.get("account") == ""
    assert " " not in third.url


def test_sogou_provider_legacy_page_still_parses(patch_sogou_text):
    """老版整页（无空格 href）解析不回归：账号 L1 + 跳转 URL 绝对化。"""
    patch_sogou_text(SOGOU_LEGACY_HTML)
    results = sogou_mod.SogouWeixinProvider().search("示例查询", limit=5)

    assert len(results) == 1
    assert results[0].extra.get("account") == "旧版账号乙"
    assert results[0].url.startswith("https://weixin.sogou.com/link?url=abc&type=2")
    _assert_request_line_ok(results[0].url)


# ── 3. parse_sogou_account：三级回退逐级验证 ────────────────────────────

def test_parse_sogou_account_l1_class_account():
    block = ('<div class="s-p">'
             '<a class="account" href="javascript:void(0);">一级账号甲</a></div>')
    assert parse_sogou_account(block) == "一级账号甲"


def test_parse_sogou_account_l2_all_time_y2():
    block = ('<div class="s-p"><span class="all-time-y2">二级账号乙</span>'
             '<span class="s2">2026-09-15</span></div>')
    assert parse_sogou_account(block) == "二级账号乙"


def test_parse_sogou_account_l3_sp_anchor():
    block = ('<div class="s-p"><a href="javascript:void(0);">三级账号丙</a>'
             '<span class="s2">x</span></div>')
    assert parse_sogou_account(block) == "三级账号丙"


def test_parse_sogou_account_guess_fallback():
    block = '<div class="s-p"><span class="s2">2026-09-15</span></div>'
    assert parse_sogou_account(block) == "", "未提供兜底文本时不得凭空造账号"
    assert parse_sogou_account(block, guess_text="来源：示例公众号") == "示例公众号"


def test_parse_sogou_account_guess_skips_placeholder():
    block = '<div class="s-p"></div>'
    assert parse_sogou_account(block, guess_text="公众号：公众号") == "", \
        "占位词（公众号）不得被当成账号名"


def test_parse_sogou_account_l1_precedes_l2():
    block = ('<div class="s-p"><a class="account">优先账号</a>'
             '<span class="all-time-y2">备选账号</span></div>')
    assert parse_sogou_account(block) == "优先账号"


# ── 4. wechat_searcher 同源：同一夹具两端结果一致 ───────────────────────

def test_wechat_parse_sogou_same_fixture_no_space_and_accounts():
    items = WeChatSearchEngine()._parse_sogou_results(SOGOU_SPACE_HTML)

    assert len(items) == 3
    assert all(" " not in it.url for it in items), \
        "skills 层 URL 同样必须补编码（与 provider 层同源）"
    assert all("%25" not in it.url for it in items)
    assert [it.source_account for it in items] == ["示例政治笔记", "旧版账号甲", ""]


def test_wechat_end_to_end_convenience_format():
    rows = parse_sogou_wechat_html(SOGOU_SPACE_HTML)

    assert len(rows) == 3
    assert rows[0]["source"] == "示例政治笔记"
    assert " " not in rows[0]["url"]
    _assert_request_line_ok(rows[0]["url"])


def test_provider_and_wechat_layers_agree_on_accounts(patch_sogou_text):
    """单源化 sanity：同一页面 provider 与 skills 两端的账号解析结果一致。"""
    patch_sogou_text(SOGOU_SPACE_HTML)
    prov = [r.extra.get("account", "")
            for r in sogou_mod.SogouWeixinProvider().search("示例查询", limit=5)]
    wx = [it.source_account
          for it in WeChatSearchEngine()._parse_sogou_results(SOGOU_SPACE_HTML)]
    assert prov == wx
