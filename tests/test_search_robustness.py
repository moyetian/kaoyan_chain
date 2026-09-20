# -*- coding: utf-8 -*-
"""
自动化健壮性测试套件 (Search & Scraping Infrastructure Robustness Test Suite)

覆盖范围：
1. 字符集自适应检测与解码 (UTF-8, GBK, GB2312, CP936, 乱码兜底)
2. 指数退避与抖动重试机制 (HTTP 429/502/503/504, Socket Timeout, ConnectionReset)
3. 微信公众号检索条目解析与清洗 (Title, Source/Account, Date, Clean URL)
4. 招生网页面指纹归一化与动态漂移过滤 (SHA256/MD5 稳定性与新增简章变动检测)
5. 各搜索引擎 Provider 安全降级与 HTML 深度清洗
"""

from __future__ import annotations

import http.client
import socket
import sys
import urllib.error
from pathlib import Path
from typing import Any, List
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.intelligence.watcher import (
    AdmissionWatcher,
    compute_content_fingerprint,
    normalize_content_for_fingerprint,
)
from tools.search.providers._http import (
    ANTI_BOT_MARKERS,
    RETRYABLE_STATUS_CODES,
    USER_AGENTS,
    clean_text,
    detect_and_decode,
    get_browser_headers,
    get_random_user_agent,
    get_text,
    looks_like_anti_bot,
)
from tools.search.providers.base import ProviderError
from tools.search.providers.bing import BingProvider
from tools.search.providers.ddg import DuckDuckGoProvider
from tools.search.providers.sogou import SogouWeixinProvider
from tools.skills.wechat_searcher import (
    WeChatSearchEngine,
    parse_sogou_wechat_html,
    search_wechat_articles,
)


# ══════════════════════════════════════════════════════════════════════
# 1. 字符集自适应检测与解码测试
# ══════════════════════════════════════════════════════════════════════

def test_adaptive_encoding_utf8_plain():
    text = "目标院校马克思主义理论研究生培养方案"
    raw_bytes = text.encode("utf-8")
    decoded = detect_and_decode(raw_bytes)
    assert decoded == text


def test_adaptive_encoding_utf8_bom():
    text = "思想政治教育专业初试大纲与复试科目说明"
    raw_bytes = b"\xef\xbb\xbf" + text.encode("utf-8")
    decoded = detect_and_decode(raw_bytes)
    assert text in decoded


def test_adaptive_encoding_gbk_with_meta():
    text = "马克思主义基本原理概论考研真题与参考答案"
    html_sample = f'<html><head><meta charset="gbk"></head><body><p>{text}</p></body></html>'
    raw_bytes = html_sample.encode("gbk")
    decoded = detect_and_decode(raw_bytes)
    assert text in decoded


def test_adaptive_encoding_gb2312_with_content_type_header():
    text = "中国化马克思主义理论与实践核心知识点精编"
    html_sample = f"<html><body><div>{text}</div></body></html>"
    raw_bytes = html_sample.encode("gb2312")
    headers = {"Content-Type": "text/html; charset=gb2312"}
    decoded = detect_and_decode(raw_bytes, headers=headers)
    assert text in decoded


def test_adaptive_encoding_cp936_special_characters():
    text = "2027年考研“自命题”复习指南——马克思主义中国化（重点·必背）"
    raw_bytes = text.encode("cp936")
    headers = {"Content-Type": "text/html; charset=cp936"}
    decoded = detect_and_decode(raw_bytes, headers=headers)
    assert "自命题" in decoded
    assert "马克思主义中国化" in decoded


def test_adaptive_encoding_fallback_replace_never_crashes():
    corrupted_bytes = b"Hello \xff\xfe\xfa World \x80\x81\x82 \xe4\xb8\xad\xe6\x96\x87"
    decoded = detect_and_decode(corrupted_bytes)
    assert isinstance(decoded, str)
    assert "Hello" in decoded
    assert "World" in decoded


# ══════════════════════════════════════════════════════════════════════
# 2. HTTP 指数退避与抖动重试机制测试
# ══════════════════════════════════════════════════════════════════════

def test_user_agent_rotation():
    seen_uas = set()
    for _ in range(30):
        ua = get_random_user_agent()
        assert ua in USER_AGENTS
        seen_uas.add(ua)
    assert len(seen_uas) > 1, "User-Agent 应当发生随机轮换"

    headers = get_browser_headers({"X-Custom": "test"})
    assert headers["X-Custom"] == "test"
    assert headers["User-Agent"] in USER_AGENTS
    assert "gzip" in headers["Accept-Encoding"]


def test_retry_on_transient_http_errors_then_success(monkeypatch):
    """测试在 429 / 503 等瞬时错误下重试并最终成功。"""
    attempts = 0

    class MockHTTPResponse:
        def __init__(self, content: bytes, status: int = 200):
            self.status = status
            self.headers = {"Content-Type": "text/html; charset=utf-8"}
            self._content = content

        def read(self):
            return self._content

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    def mock_urlopen(req, timeout, context):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)
        elif attempts == 2:
            raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, None)
        return MockHTTPResponse(b"<html><body><h1>Success after retry</h1></body></html>")

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    # backoff_base 设小加速测试
    result = get_text("https://example.edu.cn/test", max_retries=3, backoff_base=0.01)
    assert attempts == 3
    assert "Success after retry" in result


def test_retry_exhaustion_raises_provider_error(monkeypatch):
    """测试重试耗尽时抛出标准的 ProviderError。"""
    def mock_urlopen(req, timeout, context):
        raise urllib.error.HTTPError(req.full_url, 502, "Bad Gateway", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    with pytest.raises(ProviderError) as exc_info:
        get_text("https://example.edu.cn/server_error", max_retries=2, backoff_base=0.01)
    assert "502" in str(exc_info.value)


def test_retry_on_network_timeout_and_connection_reset(monkeypatch):
    """测试在网络超时与连接重置 (ConnectionReset) 下平滑重试。"""
    attempts = 0

    class MockHTTPResponse:
        status = 200
        headers = {}
        def read(self):
            return b"OK response"
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    def mock_urlopen(req, timeout, context):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise socket.timeout("timed out")
        elif attempts == 2:
            raise ConnectionResetError("Connection reset by peer")
        return MockHTTPResponse()

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    result = get_text("https://example.edu.cn/network_flaky", max_retries=3, backoff_base=0.01)
    assert attempts == 3
    assert "OK response" in result


def test_non_retryable_404_fails_immediately(monkeypatch):
    """测试 404 等致命错误不进行多余重试，立即失败。"""
    attempts = 0

    def mock_urlopen(req, timeout, context):
        nonlocal attempts
        attempts += 1
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    with pytest.raises(ProviderError) as exc_info:
        get_text("https://example.edu.cn/404", max_retries=3, backoff_base=0.01)
    assert attempts == 1
    assert "404" in str(exc_info.value)


# ══════════════════════════════════════════════════════════════════════
# 3. HTML 文本清洗与实体反转义测试
# ══════════════════════════════════════════════════════════════════════

def test_clean_text_strips_nested_tags_and_unescapes_entities():
    raw_html = (
        '<div>\n'
        '  <script>var x = 1;</script>\n'
        '  <style>.box { color: red; }</style>\n'
        '  <!-- 内部注释 -->\n'
        '  <h3>目标院校&nbsp;&amp;&nbsp;2027考研&lt;b&gt;马克思主义&lt;/b&gt;</h3>\n'
        '  <p>发布时间&#58;&nbsp;2026-09-18&#x3000;\u200b</p>\n'
        '</div>'
    )
    cleaned = clean_text(raw_html)
    assert "var x = 1" not in cleaned
    assert "color: red" not in cleaned
    assert "内部注释" not in cleaned
    assert "目标院校 & 2027考研 马克思主义" in cleaned
    assert "发布时间: 2026-09-18" in cleaned
    assert "\u200b" not in cleaned
    assert "\u3000" not in cleaned


def test_looks_like_anti_bot_detection():
    assert looks_like_anti_bot("<html>请输入验证码以继续访问</html>") == "请输入验证码"
    assert looks_like_anti_bot("Please verify you are a human: anomaly detected") == "anomaly"
    assert looks_like_anti_bot("正常的研究生院招生简章页面公告") == ""


# ══════════════════════════════════════════════════════════════════════
# 4. 微信公众号检索与解析测试
# ══════════════════════════════════════════════════════════════════════

WECHAT_SOGOU_FIXTURE = """
<ul class="news-list">
  <li id="sogou_vr_11002601_box_0">
    <div class="img-box">
      <a href="/thumb_link"><img src="thumb.jpg" /></a>
    </div>
    <div class="txt-box">
      <h3>
        <a href="/link?url=dn9a8as9df8a9sd8f&amp;query=408" target="_blank">
          2027年<em>目标院校</em>马克思主义理论考研上岸全规划
        </a>
      </h3>
      <p class="txt-info">
        本文深度复盘618马原与823中国化真题题型，梳理背诵闭环口诀。
      </p>
      <div class="s-p">
        <a class="account" href="javascript:void(0);">考研前沿指南</a>
        <span class="s2"><script>timeConvert('1758196800')</script></span>
      </div>
    </div>
  </li>
  <li id="sogou_vr_11002601_box_1">
    <div class="txt-box">
      <h3>
        <a href="https://mp.weixin.qq.com/s/abcdef123456" target="_blank">
          马理论考研如何从100分跨越到130分？
        </a>
      </h3>
      <p class="txt-info">
        真题采分点步骤拆解，告别盲目死记硬背。
      </p>
      <div class="s-p">
        <span class="all-time-y2">学霸政治笔记</span>
        <span class="s2">2026-09-15</span>
      </div>
    </div>
  </li>
</ul>
"""

def test_wechat_sogou_article_extraction():
    parsed = parse_sogou_wechat_html(WECHAT_SOGOU_FIXTURE)
    assert len(parsed) == 2

    first = parsed[0]
    assert first["title"] == "2027年目标院校马克思主义理论考研上岸全规划"
    assert first["source"] == "考研前沿指南"
    assert first["url"].startswith("https://weixin.sogou.com/link?url=")
    assert first["date"] != ""

    second = parsed[1]
    assert "马理论考研如何从100分跨越到130分" in second["title"]
    assert second["source"] == "学霸政治笔记"
    assert second["url"] == "https://mp.weixin.qq.com/s/abcdef123456"
    assert second["date"] == "2026-09-15"


def test_wechat_search_articles_convenience_format(monkeypatch):
    """测试 search_wechat_articles 输出规范的字典列表结构。"""
    monkeypatch.setattr(
        "tools.skills.wechat_searcher.WeChatSearchEngine._search_sogou",
        lambda self, kw, max_r: WeChatSearchEngine()._parse_sogou_results(WECHAT_SOGOU_FIXTURE)
    )
    articles = search_wechat_articles("目标院校 马克思主义理论", max_results=5, source="sogou")
    assert len(articles) == 2
    for art in articles:
        assert "title" in art
        assert "source" in art
        assert "date" in art
        assert "url" in art
        assert art["url"].startswith("http")


# ══════════════════════════════════════════════════════════════════════
# 5. 高校网站监控指纹与标题健壮提取测试
# ══════════════════════════════════════════════════════════════════════

PAGE_BASE_HTML = """
<!DOCTYPE html>
<html>
<head>
  <title>目标院校研究生院 - 招生工作</title>
  <script>var jsessionid = "AB12CD34EF56"; var visits = 19283;</script>
</head>
<body>
  <div class="header">
    <span>今天是：2026年9月18日 星期五</span>
    <span>当前时间：2026-09-18 19:30:25</span>
    <span id="views">访问量: 59281</span>
  </div>
  <div class="content-list">
    <ul>
      <li>
        <span class="date">[2026-09-12]</span>
        <a href="/info/1024/1.htm" title="目标院校2027年硕士研究生招生简章及专业目录">
          目标院校2027年硕士研究生...
        </a>
      </li>
      <li>
        <a href="/info/1024/2.htm">马克思主义学院2027年硕士研究生初试自命题大纲及参考书目</a>
        <span class="time">(09-15)</span>
      </li>
    </ul>
  </div>
  <div class="footer">版权所有：目标院校研究生院 网站地图 联系我们</div>
</body>
</html>
"""

PAGE_IDENTICAL_CONTENT_DYNAMIC_DRIFT = """
<!DOCTYPE html>
<html>
<head>
  <title>目标院校研究生院 - 招生工作</title>
  <script>var jsessionid = "ZZ99YY88XX77"; var visits = 19999;</script>
</head>
<body>
  <div class="header">
    <span>今天是：2026年9月19日 星期六</span>
    <span>当前时间：2026-09-19 08:15:00</span>
    <span id="views">访问量: 60412</span>
  </div>
  <div class="content-list">
    <ul>
      <li>
        <span class="date">[2026-09-12]</span>
        <a href="/info/1024/1.htm?sid=88234&_t=1758240000" title="目标院校2027年硕士研究生招生简章及专业目录">
          目标院校2027年硕士研究生...
        </a>
      </li>
      <li>
        <a href="/info/1024/2.htm?token=secret99">马克思主义学院2027年硕士研究生初试自命题大纲及参考书目</a>
        <span class="time">(09-15)</span>
      </li>
    </ul>
  </div>
  <div class="footer">版权所有：目标院校研究生院 网站地图 联系我们</div>
</body>
</html>
"""

PAGE_WITH_NEW_ANNOUNCEMENT = """
<!DOCTYPE html>
<html>
<head><title>目标院校研究生院 - 招生工作</title></head>
<body>
  <div class="content-list">
    <ul>
      <li>
        <a href="/info/1024/3.htm">2027年硕士研究生报考点公告与网报注意事项</a>
      </li>
      <li>
        <a href="/info/1024/1.htm" title="目标院校2027年硕士研究生招生简章及专业目录">
          目标院校2027年硕士研究生招生简章及专业目录
        </a>
      </li>
      <li>
        <a href="/info/1024/2.htm">马克思主义学院2027年硕士研究生初试自命题大纲及参考书目</a>
      </li>
    </ul>
  </div>
</body>
</html>
"""


def test_site_fingerprint_ignores_dynamic_drift():
    """验证包含动态会话ID、时钟与访问量漂移的页面产生完全一致的 SHA256/MD5 指纹。"""
    sha1 = compute_content_fingerprint(PAGE_BASE_HTML, algorithm="sha256")
    sha2 = compute_content_fingerprint(PAGE_IDENTICAL_CONTENT_DYNAMIC_DRIFT, algorithm="sha256")
    assert sha1 == sha2, "动态时间戳与访问量漂移不应改变内容指纹"

    md5_1 = compute_content_fingerprint(PAGE_BASE_HTML, algorithm="md5")
    md5_2 = compute_content_fingerprint(PAGE_IDENTICAL_CONTENT_DYNAMIC_DRIFT, algorithm="md5")
    assert md5_1 == md5_2
    assert sha1 != md5_1


def test_site_fingerprint_detects_real_announcement_change():
    """验证真实公告新增时指纹发生确定性变动。"""
    sha_base = compute_content_fingerprint(PAGE_BASE_HTML, algorithm="sha256")
    sha_new = compute_content_fingerprint(PAGE_WITH_NEW_ANNOUNCEMENT, algorithm="sha256")
    assert sha_base != sha_new, "新增公告必须改变指纹哈希"


def test_robust_title_extraction_from_university_markup():
    """验证能提取完整的 title 属性并过滤导航噪音词 (默认生产配置，无需 _force_python)。"""
    watcher = AdmissionWatcher()

    titles = watcher._extract_recent_titles(PAGE_BASE_HTML)
    assert len(titles) == 2
    # 优先采用 title 属性中的完整长标题，而不是截断的 "目标院校2027年硕士研究生..."
    assert titles[0] == "目标院校2027年硕士研究生招生简章及专业目录"
    assert titles[1] == "马克思主义学院2027年硕士研究生初试自命题大纲及参考书目"
    assert not any("版权所有" in t for t in titles)
    assert not any("网站地图" in t for t in titles)


def test_robust_title_extraction_edge_cases():
    """验证超长中文标题(>60字节)、实体转义、嵌套标签与无属性链接的抽取稳健性。"""
    html_markup = """
    <div class="announcements">
      <a href="/1" title="目标院校2027年马克思主义学院全日制硕士研究生招生专业目录与复试参考范围汇总">
        目标院校2027年马克思主义学院...
      </a>
      <a href="/2"><span>【官方通知】</span>2027年招收攻读硕士学位研究生自命题科目考试大纲<i>(09-18)</i></a>
      <a href="/3" title="关于&amp;ldquo;全国硕士研究生招生考试&amp;rdquo;河南农大考点网上确认公告">网上确认公告</a>
      <a href="/nav">网站地图</a>
      <a href="/contact">联系我们</a>
    </div>
    """
    watcher = AdmissionWatcher()
    titles = watcher._extract_recent_titles(html_markup)
    assert len(titles) == 3
    assert titles[0] == "目标院校2027年马克思主义学院全日制硕士研究生招生专业目录与复试参考范围汇总"
    assert titles[1] == "【官方通知】 2027年招收攻读硕士学位研究生自命题科目考试大纲"
    assert titles[2] == "关于“全国硕士研究生招生考试”河南农大考点网上确认公告"
    assert not any("网站地图" in t for t in titles)
    assert not any("联系我们" in t for t in titles)


# ══════════════════════════════════════════════════════════════════════
# 6. Provider 优雅降级测试 (Safe Search)
# ══════════════════════════════════════════════════════════════════════

def test_sogou_safe_search_on_captcha_and_error(monkeypatch):
    """测试 SogouWeixinProvider 在 safe=True 时优雅返回空列表，在 safe=False 时抛出异常。"""
    provider = SogouWeixinProvider()

    # 1. 验证码模拟
    monkeypatch.setattr("tools.search.providers.sogou.get_text", lambda *a, **k: "请输入验证码 SourceVerifyCode")
    assert provider.safe_search("test") == []
    with pytest.raises(ProviderError):
        provider.search("test", safe=False)

    # 2. 网络错误模拟
    def mock_net_err(*a, **k):
        raise ProviderError("网络超时不可达")
    monkeypatch.setattr("tools.search.providers.sogou.get_text", mock_net_err)
    assert provider.safe_search("test") == []


def test_bing_safe_search_on_network_error(monkeypatch):
    provider = BingProvider()
    monkeypatch.setattr("tools.search.providers.bing.get_text", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("网络中断")))
    assert provider.safe_search("test") == []
    with pytest.raises(ProviderError):
        provider.search("test", safe=False)


def test_ddg_safe_search_on_anti_bot(monkeypatch):
    provider = DuckDuckGoProvider()
    monkeypatch.setattr("tools.search.providers.ddg.get_text", lambda *a, **k: "<html>anomaly captcha required</html>")
    assert provider.safe_search("test") == []
    with pytest.raises(ProviderError):
        provider.search("test", safe=False)
