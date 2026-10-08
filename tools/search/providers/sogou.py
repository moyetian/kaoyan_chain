# -*- coding: utf-8 -*-
"""
搜狗微信检索（面向公众号文章 / 经验贴，即「资料检索」场景）

[为什么不直接做通用网页检索] 实测（2026-09-16）：
  * `www.sogou.com/web?query=...` 对自动化请求直接返回**验证码页**
    （页面含「请协助验证 / SourceVerifyCode」），拿不到结果 ——
    与其把验证码文本当结果返回，不如不提供该 provider；
  * `weixin.sogou.com/weixin?type=2&query=...` 正常响应（本机实测 33KB、
    10 条 `div.txt-box`），适合检索公众号文章与经验贴。

[已知边界] 结果链接是搜狗的 `/link?url=...&token=...` 跳转地址，需要搜索会话的
cookie 才能在浏览器/抓取器里还原成真正的 `mp.weixin.qq.com` 原文地址；
把跳转解析成原文是 `skills/wechat_searcher.py` 已有的能力，本 provider 不重复实现，
只如实标注来源类型为 wechat（避免被域名表按 sogou.com 误判成普通聚合站）。
"""

from __future__ import annotations

import html
import logging
import re
import urllib.parse
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from ..models import SearchResult
from . import register
from ._http import (
    absolute,
    clean_text,
    get_text,
    normalize_search_url,
    parse_sogou_account,
)
from .base import ProviderError, SearchProvider

_LOG = logging.getLogger(__name__)

_ENDPOINT = "https://weixin.sogou.com/weixin"
_BASE = "https://weixin.sogou.com"

#: 结果块：li 内含 div.txt-box（标题 + 摘要 + 账号/日期）
_ITEM_RE = re.compile(r'<div class="txt-box">(.*?)</div>\s*</li>', re.DOTALL | re.IGNORECASE)
_TITLE_RE = re.compile(r'<h3>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                       re.DOTALL | re.IGNORECASE)
_SNIPPET_RE = re.compile(r'<p class="txt-info"[^>]*>(.*?)</p>', re.DOTALL | re.IGNORECASE)
# [P0-7 修复·2026-10-08] 原 _ACCOUNT_RE 只认 class="account"（实测现页面 0 命中，
# 账号已迁到 <span class="all-time-y2">）——删除，改用 _http.parse_sogou_account
# 共享三级回退解析器（与 skills/wechat_searcher.py 同源）。
_DATE_RE = re.compile(r"timeConvert\('(\d+)'\)")

#: 无结果（属正常结果，不是错误）
_NO_RESULT_MARKERS = ("没有找到相关的微信公众号文章", "没有找到相关文章")
#: 反爬/验证码（属错误，必须显式失败）
_BLOCK_MARKERS = ("SourceVerifyCode", "请协助验证", "antispider", "请输入验证码",
                  "您的访问过于频繁")

# ── [P1 修复·2026-10-08 S3] 发布时间解析与客户端时间过滤 ─────────────────
#: ``time_range`` → 结果允许的最大年龄（天）。ddg/bing 有服务端时间参数
#: （df=/filters=），搜狗没有——只能在客户端按 published_at 过滤。
_TIME_WINDOW_DAYS = {"week": 7, "month": 30, "year": 365}

#: timeConvert 时间戳的合理区间（2000-01-01 ~ 2100-01-01，Unix 秒）。
#: 越界值（毫秒戳、占位数字）按「无日期」处理，避免转出 1970 年代幻觉日期。
_TS_MIN, _TS_MAX = 946684800, 4102444800


def _parse_published_at(raw: str) -> Tuple[str, Optional[datetime]]:
    """``timeConvert('…')`` 的 Unix 秒 → ``(ISO 日期, datetime)``；无法解析返回 ``("", None)``。

    [P1 修复·2026-10-08 S3] 旧实现把裸 Unix 时间戳（如 1617193814）原样写进
    ``published_at``，违反 ``SearchResult`` 的「ISO 日期或空串」契约
    （models.py:53），下游任何按字符串解析日期的消费方都会拿到 1970 年代幻觉。
    转**本地时区**：与 skills 层（``wechat_searcher._parse_sogou_results``）的
    既有实现 ``datetime.fromtimestamp(...)`` 同口径——同一页面在两条链路上必须
    给出同一天，且与用户本机看到的搜狗页面时间一致。
    """
    try:
        ts = int(str(raw or "").strip())
    except (TypeError, ValueError):
        return "", None
    if not (_TS_MIN <= ts <= _TS_MAX):
        return "", None
    try:
        dt = datetime.fromtimestamp(ts)
    except (OverflowError, OSError, ValueError):   # pragma: no cover - 区间已收窄
        return "", None
    return dt.strftime("%Y-%m-%d"), dt


def _within_time_range(published: Optional[datetime],
                       time_range: Optional[str]) -> bool:
    """``time_range`` 客户端过滤：有日期且超期的结果剔除；无日期无法判断 → 保留。

    [P1 修复·2026-10-08 S3] ``time_range`` 此前被完全忽略——上层传了
    year/month/week 也照样返回十年前的旧文（旧文会挤占 limit 名额、误导模型
    引用过时口径）。**无日期条目保留**：无法证明其超期，误杀会损失召回
    （召回优先是检索层的既有口径，见 ``relevance`` 的守门设计）。
    """
    days = _TIME_WINDOW_DAYS.get(str(time_range or "").lower())
    if days is None or published is None:
        return True
    return (datetime.now() - published) <= timedelta(days=days)


@register
class SogouWeixinProvider(SearchProvider):
    """搜狗微信文章检索（公众号文章/经验贴）。"""

    name = "sogou-weixin"
    engine_type = "resource"
    priority = 30
    description = "搜狗微信文章检索（公众号经验贴/资料，覆盖「资料发现」场景）"

    def search(self, query: str, *, limit: int = 10,
               time_range: Optional[str] = None,
               safe: bool = False) -> List[SearchResult]:
        params = {"type": "2", "query": str(query)}
        url = f"{_ENDPOINT}?{urllib.parse.urlencode(params)}"
        try:
            # [W11 快速失败] 反爬源快速放弃：原 timeout=12 × max_retries=3（默认）
            # 最坏 ~50s 才失败——多角色实测「微信文章检索源异常」每次失败耗时
            # 数十秒。收紧为 timeout=6 × max_retries=1（最多 2 次请求，最坏 ~13s）。
            # 反爬验证页本身单次即抛（下方 _BLOCK_MARKERS 检查，不重试）；
            # 此参数只影响网络类失败的重试次数。
            html_text = get_text(url, timeout=6, max_retries=1)
        except Exception as exc:
            if safe:
                _LOG.warning("搜狗微信抓取网络异常，优雅降级为空列表: %s", exc)
                return []
            raise ProviderError(f"搜狗微信网络请求失败: {exc}") from exc

        if any(marker in html_text for marker in _BLOCK_MARKERS):
            if safe:
                _LOG.warning("搜狗微信要求验证码/限制访问（反爬），优雅降级为空列表")
                return []
            raise ProviderError("搜狗要求验证码/限制访问（反爬），本次跳过该源")
        if any(marker in html_text for marker in _NO_RESULT_MARKERS):
            return []                    # 确实没有相关文章，属正常结果

        blocks = _ITEM_RE.findall(html_text)
        if not blocks:
            # 备用容错匹配（针对部分非标准 li/div 排版）
            blocks = re.findall(
                r'<div class="txt-box"[^>]*>(.*?)</div>\s*(?:</div>|</li>)',
                html_text,
                re.DOTALL | re.IGNORECASE
            )
        if not blocks:
            if safe:
                return []
            # [P1 修复·2026-10-08 S4] 「改版」与「被限流」不再共用文案：改版是
            # 确定性的解析失败（页面拿到了、结构不匹配），与反爬/限流是两类
            # 故障。共用文案会让改版每次都被 service._maybe_cooldown 当限流
            # 立即冷却，冷却按指数增长却永远修不好页面。独立文案「疑似页面
            # 改版」→ 走阈值型 note_failure（不立即冷却）；真反爬（验证码/
            # 访问频繁）仍走上方 _BLOCK_MARKERS 分支立即冷却。
            raise ProviderError("页面结构未匹配到文章块（疑似页面改版）")

        items: List[Dict[str, str]] = []
        for block in blocks:
            m = _TITLE_RE.search(block)
            if not m:
                continue
            href, title_html = m.group(1), m.group(2)
            s_match = _SNIPPET_RE.search(block)
            d_match = _DATE_RE.search(block)
            # [P1 修复·2026-10-08 S3] 时间戳 → ISO 日期（本地时区，与 skills 层
            # 同口径），并补客户端 time_range 过滤（搜狗无服务端时间参数）。
            published_at, published_dt = _parse_published_at(
                d_match.group(1) if d_match else "")
            if not _within_time_range(published_dt, time_range):
                continue
            # [P0-7 修复·2026-10-08] 真实 href 的 query 参数带字面空格（实测 10/10），
            # 进 urllib 必抛 InvalidURL —— absolute 补全后统一走共享
            # normalize_search_url 补百分号编码（已有 %XX 不二次编码）。
            clean_url = normalize_search_url(
                absolute(html.unescape(href).replace("&amp;", "&"), _BASE))
            items.append({
                "title": clean_text(title_html),
                "url": clean_url,
                "snippet": clean_text(s_match.group(1)) if s_match else "",
                "source_type": "wechat",     # 链接指向公众号文章，而非搜狗自身
                # [P0-7 修复·2026-10-08] 账号名三级回退（L1 class=account →
                # L2 all-time-y2 → L3 s-p>a）共享解析器，替换已失效的 L1 单实现。
                "account": clean_text(parse_sogou_account(block)),
                "published_at": published_at,
            })
            if len(items) >= max(limit, 1):
                break
        if not items:
            if safe:
                return []
            raise ProviderError("解析到文章块，但无可用链接")
        return self.normalize(items)

    def safe_search(self, query: str, *, limit: int = 10,
                    time_range: Optional[str] = None) -> List[SearchResult]:
        """安全检索：遇到网络异常或反爬验证码时优雅降级为空列表，绝不抛出异常。"""
        return self.search(query, limit=limit, time_range=time_range, safe=True)


__all__ = ["SogouWeixinProvider"]
