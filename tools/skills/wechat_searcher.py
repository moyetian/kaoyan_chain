# -*- coding: utf-8 -*-
"""
考研学习链 · 微信公众号文章检索与爬虫工具 (WeChat Article Searcher)

核心功能：
  1. 多源检索：搜狗微信搜索 (主源) + Bing 微信文章搜索 (备用源) + 本地经验缓存
  2. 文章正文抓取与 HTML→Markdown 清洗解析
  3. 优质内容沉淀到 .memory/experiences/ 作为社媒考研经验档案 (本地隐私目录)
  4. 联动 school_scout.py 院校侦察引擎丰富研报数据源

合规声明：本工具仅用于个人学习研究，遵循各平台使用条款，不进行大规模分布式爬取。
"""

import sys
import re
import html
import inspect
import time
import random
import threading
import urllib.request
import urllib.parse
import urllib.error
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:  # 双导入路径兼容（源码脚本式 / tools 包式）
    from workspace import resolve_workspace_root
except ImportError:  # pragma: no cover
    from tools.workspace import resolve_workspace_root
from typing import List, Dict, Any

try:  # 双导入路径兼容（项目同时存在 tools.X 与 X 两种导入方式）
    from ky_io import atomic_write_text  # noqa: E402
except ImportError:  # pragma: no cover
    from tools.ky_io import atomic_write_text  # noqa: E402

# [审计 2026-10-02 P0-1] 所有外部搜索结果与正文抓取都必须经过安全通道。
# 仅做一次 assert_url_safe 再调用裸 urlopen 会在 302 后失去逐跳复检和 DNS pin，
# 因此这里不保留旧的裸 opener 旁路。
try:
    from net_guard import MAX_HTTP_RESPONSE_BYTES, safe_urlopen  # noqa: E402
except ImportError:  # pragma: no cover
    from tools.net_guard import MAX_HTTP_RESPONSE_BYTES, safe_urlopen  # type: ignore

ROOT = resolve_workspace_root(__file__)

_RATE_LOCK = threading.Lock()
_RATE_LAST: Dict[str, float] = {}


def _respect_host_rate_limit(url: str, minimum_interval: float = 0.8) -> None:
    """按主机做最小请求间隔，避免多页/多目标巡检轰击源站。"""
    host = urllib.parse.urlparse(str(url or "")).netloc.lower()
    if not host:
        return
    with _RATE_LOCK:
        now = time.monotonic()
        wait = minimum_interval - (now - _RATE_LAST.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        _RATE_LAST[host] = time.monotonic()

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# [P3 修复·D8] 三端口径统一：账号/日期缺失时不再各自渲染成含糊的「未知」，
# 统一由下方 label 函数给出可解释文案，避免"同一份数据三种说法"。
UNKNOWN_ACCOUNT_LABEL = "未识别（平台未公开）"
UNKNOWN_DATE_LABEL = "未标注日期"

# ── [P0 浏览器兜底]（可选增强，闸门 KY_BROWSER_ACQUISITION 默认关闭） ──────────
#: 正文浏览器兜底条数上限（逐条 deadline 12s；搜索链预算见整合方案 §9）
BROWSER_ARTICLE_LIMIT = 3
#: 搜狗结果块标题链接选择器（跳转还原：/link?url= 需真实会话 cookie）
_SOGOU_RESULT_LINK_SELECTOR = "div.txt-box h3 a"

#: [W11 快速失败] 搜狗微信结果页的反爬/验证码特征（与 tools/search/providers/
#: sogou.py 的 _BLOCK_MARKERS 同口径——「源不可用」不得被当成「没有文章」）。
_WX_BLOCK_MARKERS = ("SourceVerifyCode", "请协助验证", "antispider", "请输入验证码",
                     "您的访问过于频繁")


def _looks_blocked(html_text: str) -> bool:
    """结果页是否命中反爬/验证码特征。"""
    return any(m in (html_text or "") for m in _WX_BLOCK_MARKERS)


def _load_browser_manager():
    """惰性加载 BrowserPluginManager（双导入兼容）。

    未装 playwright / 模块不可导入时返回 None —— 浏览器兜底是可选增强，
    任何情况下都不得让检索主链因它导入失败。
    """
    try:
        from tools.intelligence.fetcher import BrowserPluginManager
    except ImportError:
        try:
            from intelligence.fetcher import BrowserPluginManager
        except ImportError:
            return None
    return BrowserPluginManager


def _browser_fallback_ready() -> bool:
    """闸门开启且两级可用（库 + 内核）才为 True。"""
    manager = _load_browser_manager()
    if manager is None:
        return False
    try:
        return bool(manager.is_enabled() and manager.is_available())
    except Exception:
        return False


def _invoke_search(fn, keyword: str, max_results: int, time_range: str):
    """调用搜索函数，**仅当其签名接受 time_range 时才传入**。

    历史写法是 `try: fn(..., time_range=t) except TypeError: fn(...)` —— 那会把
    fn 内部真实的 TypeError 一并吞掉并静默重发一次请求。改为显式签名探测后，
    既兼容旧签名/测试替身，又不会掩盖内部异常。
    """
    try:
        params = inspect.signature(fn).parameters
        accepts = ("time_range" in params
                   or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()))
    except (TypeError, ValueError):
        accepts = False
    if accepts:
        return fn(keyword, max_results, time_range=time_range)
    return fn(keyword, max_results)


def account_label(raw: str) -> str:
    """公众号名称展示文案（缺失时可解释，而非「未知」）。"""
    return (raw or "").strip() or UNKNOWN_ACCOUNT_LABEL


def date_label(raw: str) -> str:
    """发布日期展示文案（缺失时可解释，而非「未知」）。"""
    return (raw or "").strip() or UNKNOWN_DATE_LABEL


@dataclass
class WeChatArticleItem:
    """微信公众号文章数据模型"""
    title: str
    url: str
    source_account: str = ""        # 公众号名称
    publish_date: str = ""          # 发布日期 YYYY-MM-DD
    summary: str = ""               # 摘要
    content_markdown: str = ""      # 正文 Markdown
    content_html: str = ""          # 原始 HTML
    fetched: bool = False           # 是否已抓取正文
    source_platform: str = "sogou"  # 检索来源 (sogou / bing / local)


# 别名兼容
SearchResult = WeChatArticleItem


class _AccountNameResolver:
    """公众号名称解析混入类（检索引擎与正文抓取器共用同一份实现）。

    [P3 修复·D8] 原先 `_ACCOUNT_PATTERNS` / `_extract_account_from_html` /
    `_guess_account_from_text` 只定义在 WeChatArticleFetcher 上，而
    WeChatSearchEngine._parse_sogou_results / _parse_bing_results 同样调用
    `self._guess_account_from_text(...)` → 抛 AttributeError，被 `except Exception`
    静默吞掉后返回空列表，导致「多源检索」实质恒为 0 条结果。
    现上提为共享混入：两个类各自继承即可，方法归属正确，行为与抓取器原有逻辑一致。
    """

    # 账号名多选择器清单（覆盖网页版与客户端版微信正文页结构）
    _ACCOUNT_PATTERNS = (
        r'<a[^>]*id="js_name"[^>]*>(.*?)</a>',
        r'id="js_name"[^>]*>([^<]{1,40})<',
        r'<span[^>]*class="rich_media_meta rich_media_meta_nickname"[^>]*>(.*?)</span>',
        r'<meta[^>]*property="og:article:author"[^>]*content="([^"]+)"',
        r'<meta[^>]*property="og:site_name"[^>]*content="([^"]+)"',
        r'var\s+nickname\s*=\s*["\']([^"\']{1,40})["\']',
        r'data-nickname="([^"]{1,40})"',
        r'profile_nickname[^>]*>([^<]{1,40})<',
    )

    # 账号名占位词/噪声词黑名单（命中即视为未识别，避免把「公众号」「获取更多」当成账号名）
    _ACCOUNT_PLACEHOLDERS = (
        "未知", "微信公众号", "公众号", "微信", "蓝字", "上方蓝字",
        "关注", "获取更多", "更多", "阅读全文", "原文", "文章", "点击上方",
        "投稿", "转载", "编辑", "责任编辑",
    )

    def _extract_account_from_html(self, html_text: str) -> str:
        """按多选择器顺序提取公众号名称；全部未命中返回空串。"""
        for pat in self._ACCOUNT_PATTERNS:
            m = re.search(pat, html_text, re.DOTALL)
            if not m:
                continue
            name = re.sub(r'<[^>]+>', '', m.group(1))
            name = html.unescape(name).strip().strip('"').strip()
            if name and name not in self._ACCOUNT_PLACEHOLDERS:
                return name[:40]
        return ""

    def _guess_account_from_text(self, text: str) -> str:
        """从搜索结果摘要/标题中兜底猜测公众号名（如「来源：XXX」「公众号：XXX」）。"""
        if not text:
            return ""
        # [P3 修复·D8] 补充「点击上方蓝字关注 XXX」「由 XXX 发布」等列表页常见句式。
        # 采用 finditer 逐个候选校验：左端噪声命中（如「蓝字关注」）不再直接短路返回，
        # 且标记词后必须跟分隔符，避免把「关注」本身捕成账号名。
        for pat in (r'(?:公众号|来源|出品|作者)[：:\s|]*([\u4e00-\u9fa5A-Za-z0-9_·\-]{2,24})',
                    r'(?:来自|由)[\s：:]*([\u4e00-\u9fa5A-Za-z0-9_·\-]{2,24})',
                    r'(?:蓝字|关注)[\s：:]+([\u4e00-\u9fa5A-Za-z0-9_·\-]{2,24})'):
            for m in re.finditer(pat, text):
                cand = m.group(1).strip("，。,.|")
                if cand and cand not in self._ACCOUNT_PLACEHOLDERS:
                    return cand[:40]
        return ""


class WeChatSearchEngine(_AccountNameResolver):
    """多源微信公众号文章搜索引擎"""

    SOGOU_WX_URL = "https://weixin.sogou.com/weixin"
    BING_URL = "https://www.bing.com/search"

    def __init__(self):
        # [P3 修复·D8] 检索源失败原因留痕，供上层区分「确实没有结果」与「源已异常」
        self.last_errors: List[str] = []
        #: [P2-9 修复·两端口径] 本次调用各源结果清单（成功 N 条 / 失败原因），
        #: 由 search() 落账，随 wechat_search 返回并打印在 CLI/TUI 报告头 ——
        #: 「同关键词两端结果数不同」时先看源清单即可判定是不是反爬波动。
        self.last_source_status: List[str] = []

    def _record_source_status(self, source_name: str, err_count_before: int, count: int) -> None:
        """记录某检索源本次调用的结果（成功 N 条 / 失败原因）。

        失败判定 = 该源调用期间有**新增** last_errors（``_note_source_error`` 与
        ``_note_source_empty`` 都会写入）——避免把「反爬拦截 / 解析 0 条」误标成
        「成功 0 条」。
        """
        new_errs = self.last_errors[err_count_before:]
        if new_errs:
            reason = new_errs[0]
            prefix = f"{source_name}: "
            if reason.startswith(prefix):
                reason = reason[len(prefix):]
            # 去掉异常类名前缀（如 "RuntimeError: "），展示更干净
            reason = re.sub(r"^[A-Za-z_][A-Za-z0-9_.]*Error: ", "", reason)
            self.last_source_status.append(f"{source_name}: 失败（{reason[:80]}）")
        else:
            self.last_source_status.append(f"{source_name}: 成功 {count} 条")

    def _note_source_error(self, source_name: str, exc: Exception) -> None:
        """记录检索源异常。

        [P3 修复·D8] 原实现 `except Exception: return []` 把网络/解析异常伪装成
        「未检索到相关文章」，缺陷因此在 CLI / TUI / GUI 三端都不可见。
        现按项目既有 [warn] → stderr 的告警风格落一条日志，并写入 last_errors。
        """
        msg = f"{source_name}: {type(exc).__name__}: {exc}"
        self.last_errors.append(msg)
        print(f"[warn] 微信文章检索源异常（{msg}）", file=sys.stderr)

    def _note_source_empty(self, source_name: str, page_len: int) -> None:
        """结果页 HTTP 取回成功但解析出 0 条时留痕。

        [P3 修复·D8] 「源不可用」与「确实没有文章」必须可区分：反爬验证页 /
        站点结构改版会返回 200 + 无结果块，静默下去会被当成「没有相关文章」。
        """
        self._note_source_error(source_name, RuntimeError(
            f"结果页解析 0 条（页面 {page_len} 字符，疑似反爬验证页或站点结构改版）"))

    def search(
        self,
        keyword: str,
        max_results: int = 10,
        source: str = "auto",
        time_range: str = "year",
        should_stop=None
    ) -> List[WeChatArticleItem]:
        """
        统一检索入口
        :param keyword: 检索关键词 (如 "408计算机考研经验")
        :param max_results: 最大结果数
        :param source: "sogou" / "bing" / "auto" (自动降级) / "local"
        :param time_range: "year" (近一年) / "half_year" (近半年) / "three_years" (近三年) / "all" (全部)
        :param should_stop: 可选停止回调（GUI 关窗中断）；返回 True 时在检查点快速退出
        """
        keyword = keyword.strip()
        if not keyword:
            return []

        self.last_source_status = []

        if source == "local":
            local_items = self.search_local_cache(keyword, max_results)
            self.last_source_status.append(f"本地缓存: 成功 {len(local_items)} 条")
            return local_items

        raw_results: List[WeChatArticleItem] = []
        if source == "auto":
            # 优先搜狗，少于3条时降级 Bing，仍不足则补充本地缓存
            _errs = len(self.last_errors)
            sogou_items = _invoke_search(self._search_sogou, keyword, max_results * 2, time_range)
            self._record_source_status("搜狗微信", _errs, len(sogou_items))
            raw_results = list(sogou_items)
            bing_items: List[WeChatArticleItem] = []
            if len(raw_results) < 3:
                _errs = len(self.last_errors)
                bing_items = _invoke_search(self._search_bing, keyword, max_results * 2, time_range)
                self._record_source_status("Bing", _errs, len(bing_items))
                existing_urls = {r.url for r in raw_results}
                for br in bing_items:
                    if br.url not in existing_urls:
                        raw_results.append(br)
                        existing_urls.add(br.url)

            # [P0 浏览器兜底] 搜狗与 Bing 双源均为 0 条且闸门允许时，用真实浏览器
            # 会话渲染搜狗结果页（含 /link 跳转还原）；结果如实标注来源通道。
            if not sogou_items and not bing_items:
                _errs = len(self.last_errors)
                browser_items = self._search_sogou_via_browser(
                    keyword, max_results * 2, time_range, should_stop)
                if browser_items or len(self.last_errors) > _errs:
                    self._record_source_status("浏览器兜底", _errs, len(browser_items))
                else:
                    self.last_source_status.append("浏览器兜底: 未启用（闸门关闭或未装 playwright）")
                existing_urls = {r.url for r in raw_results}
                for bi in browser_items:
                    if bi.url not in existing_urls:
                        raw_results.append(bi)
                        existing_urls.add(bi.url)

            if len(raw_results) < 2:
                local_results = self.search_local_cache(keyword, max_results)
                self.last_source_status.append(f"本地缓存: 成功 {len(local_results)} 条")
                existing_urls = {r.url for r in raw_results}
                for lr in local_results:
                    if lr.url not in existing_urls:
                        raw_results.append(lr)
                        existing_urls.add(lr.url)
        elif source == "sogou":
            _errs = len(self.last_errors)
            raw_results = _invoke_search(self._search_sogou, keyword, max_results * 2, time_range)
            self._record_source_status("搜狗微信", _errs, len(raw_results))
        elif source == "bing":
            _errs = len(self.last_errors)
            raw_results = _invoke_search(self._search_bing, keyword, max_results * 2, time_range)
            self._record_source_status("Bing", _errs, len(raw_results))

        # 智能重排：多因子时效性 + 考研相关度重排序
        ranked = self._rank_and_filter_results(raw_results, keyword, time_range)
        return ranked[:max_results]

    def _rank_and_filter_results(
        self, items: List[WeChatArticleItem], keyword: str, time_range: str
    ) -> List[WeChatArticleItem]:
        """按发布时效、标题语义匹配度与噪声词惩罚综合重排。"""
        if not items:
            return []

        now_year = datetime.now().year

        def _calc_score(item: WeChatArticleItem) -> float:
            score = 0.0
            # 1. 时效性评分 (近 1-2 年高分，3 年前扣分)
            if item.publish_date:
                try:
                    d = datetime.strptime(item.publish_date[:10], "%Y-%m-%d")
                    days_ago = (datetime.now() - d).days
                    if days_ago <= 180:
                        score += 60.0
                    elif days_ago <= 365:
                        score += 45.0
                    elif days_ago <= 730:
                        score += 25.0
                    elif days_ago <= 1095:
                        score += 5.0
                    else:
                        score -= 40.0  # 超过3年的老旧文章重罚
                except Exception:
                    pass

            # 2. 考研关键词与标题匹配度
            t_low = (item.title or "").lower()
            s_low = (item.summary or "").lower()
            kw_terms = [t.lower() for t in re.split(r"[\s+·/]+", keyword) if len(t) >= 2]
            for term in kw_terms:
                if term in t_low:
                    score += 30.0
                elif term in s_low:
                    score += 15.0

            # 3. 考研核心特征词加权
            for hot in ("经验", "复试", "考情", "分数线", "报录比", "真题", "划重点", "上岸", "考研"):
                if hot in t_low:
                    score += 10.0

            # 4. 无关营销/图书广告/陈旧新闻降权
            for junk in ("图书", "教材征订", "热点分析", "招聘", "通知公告", "开班", "培训班"):
                if junk in t_low:
                    score -= 20.0
            return score

        # 针对近一年/近半年筛选：过滤掉明确标注为 3 年前（如 2020/2021/2022）的过期文章
        filtered = []
        for it in items:
            if time_range in ("year", "half_year") and it.publish_date:
                try:
                    yr = int(it.publish_date[:4])
                    if yr < now_year - 2:  # 比如当前 2026，过滤 2023 及更早
                        continue
                except Exception:
                    pass
            filtered.append(it)

        # [缺陷修复] 此前 `filtered if filtered else items`：当命中的全部是过期文章时，
        # filtered 为空又回退到未过滤集合，时效过滤在最该生效时反而失效。
        # 现区分「无结果」与「全过期」：请求了时效过滤就只返回过滤后的结果。
        target_pool = filtered if time_range in ("year", "half_year") else items
        target_pool.sort(key=_calc_score, reverse=True)
        return target_pool

    def _search_sogou(self, keyword: str, max_results: int, time_range: str = "year") -> List[WeChatArticleItem]:
        """搜狗微信搜索 (支持多页合并与鲁棒容错)"""
        # 注意：sogou 携带 tsn=4 时非浏览器访问极易触发验证码，因此统一请求标准列表并通过客户端高精度时间与关键词多因子重排
        pages_to_fetch = 2 if time_range in ("year", "half_year") else 1
        all_items: List[WeChatArticleItem] = []

        # [W11 快速失败] 冷却接线：同进程内已被反爬标记的源直接跳过（覆盖多校
        # 研究场景下的第二次调用）；网络失败/反爬页即时标记，避免反复硬试。
        try:
            from tools.search import health as _wx_health
        except ImportError:  # pragma: no cover
            from search import health as _wx_health  # type: ignore

        for p in range(1, pages_to_fetch + 1):
            if _wx_health.is_cooling("sogou-weixin"):
                # 冷却事实**始终**留痕（调用方据此知道结果不是新抓的）。
                self._note_source_error("搜狗微信", RuntimeError(
                    _wx_health.cooldown_reason("sogou-weixin")))
                # [serve-stale 兜底·2026-10-06] 冷却期**不再直接返回空**：
                # R3 全矩阵仿真实测到「早跑的 CLI 抓 5 篇 / 10 分钟内后跑的 GUI 抓
                # 0 篇」的时序互补 —— 冷却让该源 10 分钟内彻底不参与检索，检索能力
                # 直接归零。改为先查本地沉淀（``.memory/experiences/``），
                # 有就返回并标注「冷却期缓存兜底」，没有才如实返回空。
                local_items = self._cooldown_cache_fallback(keyword, max_results)
                if local_items:
                    return local_items[:max_results]
                break
            params = {
                "type": "2",  # 2 = 搜文章
                "query": keyword,
                "ie": "utf-8",
                "page": str(p),
            }
            url = f"{self.SOGOU_WX_URL}?{urllib.parse.urlencode(params)}"
            _respect_host_rate_limit(url)

            html_content = ""
            try:
                try:
                    from tools.search.providers._http import get_text
                except ImportError:
                    from search.providers._http import get_text  # type: ignore
                # [W11 快速失败] timeout 10→6、max_retries 3→1：原参数最坏
                # ~50s/页才失败（多角色实测「微信文章检索源异常」每次耗时数十秒）。
                html_content = get_text(url, timeout=6, max_retries=1)
            except Exception as http_exc:
                try:
                    req = urllib.request.Request(url, headers={
                        "User-Agent": USER_AGENT,
                        "Referer": "https://weixin.sogou.com/",
                        "Accept-Language": "zh-CN,zh;q=0.9",
                    })
                    with safe_urlopen(req, timeout=6) as resp:
                        html_content = resp.read(MAX_HTTP_RESPONSE_BYTES).decode(
                            "utf-8", errors="replace")
                except Exception as e:
                    self._note_source_error("搜狗微信", e)
                    _wx_health.mark_blocked("sogou-weixin", f"{type(e).__name__}: {e}")
                    break

            try:
                items = self._parse_sogou_results(html_content)
                if not items and p == 1:
                    self._note_source_empty("搜狗微信", len(html_content))
                    if _looks_blocked(html_content):
                        _wx_health.mark_blocked("sogou-weixin", "疑似反爬验证页")
                all_items.extend(items)
            except Exception as e:
                self._note_source_error("搜狗微信", e)
                break

        return all_items[:max_results]

    def _search_bing(self, keyword: str, max_results: int, time_range: str = "year") -> List[WeChatArticleItem]:
        """Bing 搜索微信文章 (限定 mp.weixin.qq.com 域名并注入年份时效词)"""
        now_y = datetime.now().year
        if time_range in ("year", "half_year"):
            query = f"site:mp.weixin.qq.com {keyword} {now_y} OR {now_y - 1}"
        else:
            query = f"site:mp.weixin.qq.com {keyword}"

        params = {"q": query, "count": str(max(max_results * 2, 10))}
        url = f"{self.BING_URL}?{urllib.parse.urlencode(params)}"
        _respect_host_rate_limit(url)

        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9",
            })
            # [W11 快速失败] timeout 10→6（Bing 为微信链路兜底源，单次请求，
            # 不接冷却表——过度冷却会让链路完全无结果）。
            with safe_urlopen(req, timeout=6) as resp:
                html_content = resp.read(MAX_HTTP_RESPONSE_BYTES).decode(
                    "utf-8", errors="replace")

            items = self._parse_bing_results(html_content)[:max_results]
            if not items:
                self._note_source_empty("Bing", len(html_content))
            return items
        except Exception as e:
            self._note_source_error("Bing", e)
            return []

    def _search_sogou_via_browser(self, keyword: str, max_results: int,
                                  time_range: str = "year",
                                  should_stop=None) -> List[WeChatArticleItem]:
        """[P0] 搜狗搜索浏览器兜底：真实会话渲染 + ``/link`` 跳转还原。

        触发前置条件（调用方判定）：搜狗与 Bing 双源均为 0 条。本方法内部再复检
        闸门与两级可用性（库 + 内核）—— 任一不满足即返回空列表，行为与现状一致。

        预算（整合方案 §9）：搜索 ≤15s + 跳转还原 ≤10s；验证码特征页由
        ``fetch_with_browser`` 判 BLOCKED（如实快速失败，不重试轰炸）。
        """
        manager = _load_browser_manager()
        if manager is None:
            return []
        try:
            if not (manager.is_enabled() and manager.is_available()):
                return []
        except Exception:
            return []

        params = {"type": "2", "query": keyword, "ie": "utf-8", "page": "1"}
        url = f"{self.SOGOU_WX_URL}?{urllib.parse.urlencode(params)}"
        try:
            result = manager.fetch_with_browser(
                url,
                timeout_sec=15,
                should_stop=should_stop,
                resolve_links_selector=_SOGOU_RESULT_LINK_SELECTOR,
                resolve_limit=5,
                resolve_budget_sec=10.0,
            )
        except Exception as exc:  # fetch_with_browser 承诺不抛；此处双保险
            self._note_source_error("搜狗微信(浏览器兜底)", exc)
            return []

        if result.access_status != "OK":
            self._note_source_error("搜狗微信(浏览器兜底)", RuntimeError(
                f"浏览器通道未取到结果（{result.access_status}），已如实跳过"))
            return []

        items = self._parse_sogou_results(result.content)
        if not items:
            self._note_source_empty("搜狗微信(浏览器兜底)", len(result.content))
            return []

        # 跳转还原回填：按原链接精确匹配（避免索引错位导致错链）
        resolved = result.resolved_links or {}
        for item in items:
            final_url = resolved.get(item.url)
            if final_url:
                item.url = final_url
            item.source_platform = "sogou_browser"
        return items[:max_results]

    def _cooldown_cache_fallback(self, keyword: str, max_results: int
                                 ) -> List[WeChatArticleItem]:
        """冷却期兜底：返回本地沉淀里命中的文章（标注来源通道）。

        [为什么需要] R3 全矩阵仿真（54 格）实测到该源「10 分钟内彻底不参与检索」，
        公众号检索直接归零 —— 早跑的那次抓了 5 篇，稍后跑的一次抓 0 篇。冷却是为了
        保护源不被封，代价不该是「能力归零」：``.memory/experiences/`` 里的沉淀
        本来就是同一批文章，冷却期先用它顶上。

        标注为 ``local_cooldown``（区别于正常本地检索的 ``local``），让三端都能
        如实告诉考生「这是缓存兜底，不是本次新检索到的」。
        """
        try:
            items = self.search_local_cache(keyword, max_results)
        except Exception as exc:                   # pragma: no cover - 兜底不该抛
            self._note_source_error("搜狗微信(冷却兜底)", exc)
            return []
        for it in items:
            it.source_platform = "local_cooldown"
        if items:
            self.last_source_status.append(
                f"冷却期缓存兜底: 成功 {len(items)} 条（标注 local_cooldown）")
        return items

    def search_local_cache(self, keyword: str, max_results: int) -> List[WeChatArticleItem]:
        """检索已沉淀在 .memory/experiences/ 中的本地文章（兼容读取旧 docs/experiences/ 存量）"""
        # [P0 修复] 经验档案属学员隐私，主读取路径迁移至 .memory/experiences/
        exp_dir = ROOT / ".memory" / "experiences"
        if not exp_dir.exists():
            legacy_dir = ROOT / "docs" / "experiences"  # 旧版存量目录，仅只读兼容
            if not legacy_dir.exists():
                return []
            exp_dir = legacy_dir

        results = []
        kw_lower = keyword.lower()
        for f in exp_dir.glob("*.md"):
            try:
                content = f.read_text(encoding="utf-8")
                if kw_lower in f.name.lower() or kw_lower in content.lower():
                    # 提取标题
                    m_title = re.search(r"^#\s+(.*?)$", content, re.MULTILINE)
                    title = m_title.group(1).strip() if m_title else f.stem
                    # 提取公众号
                    m_acct = re.search(r"\*\*来源公众号\*\*[:：]\s*(.*?)(?:  |\n|$)", content)
                    acct = m_acct.group(1).strip() if m_acct else ""
                    # [P3 修复·D8] 旧档案里写死的「未知」/统一占位文案不应当作真实账号回填
                    if acct in (UNKNOWN_ACCOUNT_LABEL, "未知", "微信公众号"):
                        acct = ""
                    # 提取原文链接
                    m_url = re.search(r"\*\*原文链接\*\*[:：]\s*\[.*?\]\((https?://.*?)\)", content)
                    article_url = m_url.group(1).strip() if m_url else str(f)
                    # 提取发布时间
                    m_date = re.search(r"\*\*发布日期\*\*[:：]\s*(.*?)(?:  |\n|$)", content)
                    pub_date = m_date.group(1).strip() if m_date else ""

                    results.append(WeChatArticleItem(
                        title=title,
                        url=article_url,
                        source_account=acct,
                        publish_date=pub_date,
                        summary=content[:200].replace("\n", " ").strip(),
                        content_markdown=content,
                        fetched=True,
                        source_platform="local"
                    ))
                    if len(results) >= max_results:
                        break
            except Exception:
                continue

        return results

    # 别名兼容
    _fallback_offline_results = search_local_cache


    def _parse_sogou_results(self, html_text: str) -> List[WeChatArticleItem]:
        """解析搜狗搜索结果页并提取文章结构。"""
        items = []
        result_blocks = re.findall(
            r'<li[^>]*(?:news-item|sogou_vr|id=["\']sogou_vr)[^>]*>(.*?)</li>',
            html_text, re.DOTALL | re.IGNORECASE
        )
        if not result_blocks:
            result_blocks = re.findall(
                r'<div class="txt-box"[^>]*>(.*?)</div>\s*(?:</div>|</li>)',
                html_text, re.DOTALL | re.IGNORECASE
            )
        if not result_blocks:
            result_blocks = re.findall(
                r'<div class="txt-box"[^>]*>(.*?)</div>',
                html_text, re.DOTALL | re.IGNORECASE
            )
        for block in result_blocks:
            title_m = re.search(
                r'<h3[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                block, re.DOTALL | re.IGNORECASE
            )
            if not title_m:
                for _cand in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL):
                    cand_text = re.sub(r'<[^>]+>', '', _cand.group(2)).strip()
                    if cand_text and "account" not in _cand.group(0):
                        title_m = _cand
                        break
            if not title_m:
                continue

            raw_url = html.unescape(title_m.group(1)).strip().replace("&amp;", "&")
            if not raw_url.startswith("http"):
                raw_url = urllib.parse.urljoin(self.SOGOU_WX_URL, raw_url)

            title = html.unescape(re.sub(r'<[^>]+>', '', title_m.group(2)))
            title = re.sub(r'\s+', ' ', title).strip()

            summary_m = re.search(r'<p class="txt-info"[^>]*>(.*?)</p>', block, re.DOTALL | re.IGNORECASE)
            summary = html.unescape(re.sub(r'<[^>]+>', '', summary_m.group(1))).strip() if summary_m else ""
            summary = re.sub(r'\s+', ' ', summary).strip()

            account_m = re.search(r'<a[^>]*class="[^"]*account[^"]*"[^>]*>(.*?)</a>', block, re.DOTALL | re.IGNORECASE)
            account = re.sub(r'<[^>]+>', '', account_m.group(1)).strip() if account_m else ""
            if not account:
                span_m = re.search(r'<span[^>]*class="[^"]*all-time-y2[^"]*"[^>]*>(.*?)</span>',
                                   block, re.DOTALL | re.IGNORECASE)
                if span_m:
                    account = html.unescape(re.sub(r'<[^>]+>', '', span_m.group(1))).strip()
            if not account:
                sp_m = re.search(r'<div[^>]*class="s-p"[^>]*>.*?<a[^>]*>(.*?)</a>', block, re.DOTALL | re.IGNORECASE)
                if sp_m:
                    account = html.unescape(re.sub(r'<[^>]+>', '', sp_m.group(1))).strip()
            if not account:
                account = self._guess_account_from_text(f"{summary} {title}")
            account = html.unescape(re.sub(r'<[^>]+>', '', account)).strip()

            time_m = re.search(r"timeConvert\(['\"]?(\d+)['\"]?\)", block)
            pub_date = ""
            if time_m:
                try:
                    pub_date = datetime.fromtimestamp(int(time_m.group(1))).strftime("%Y-%m-%d")
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).debug(
                        "列表页发布时间解析失败: %s -> %s", time_m.group(1), exc)
            if not pub_date:
                date_m = re.search(r'(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})', block)
                if date_m:
                    y, m, d = date_m.groups()
                    pub_date = f"{y}-{int(m):02d}-{int(d):02d}"

            if title and raw_url:
                items.append(WeChatArticleItem(
                    title=title,
                    url=raw_url,
                    source_account=account,
                    publish_date=pub_date,
                    summary=summary,
                    source_platform="sogou"
                ))
        return items

    def _parse_bing_results(self, html_text: str) -> List[WeChatArticleItem]:
        """解析 Bing 搜索结果页"""
        items = []
        try:
            from tools.search.providers._http import clean_bing_url
        except ImportError:
            try:
                from search.providers._http import clean_bing_url
            except ImportError:
                clean_bing_url = lambda u: u

        result_blocks = re.findall(r'<li class="b_algo"[^>]*>(.*?)</li>', html_text, re.DOTALL)
        for block in result_blocks:
            link_m = re.search(r'<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>(.*?)</a></h2>', block, re.DOTALL | re.IGNORECASE)
            if not link_m:
                link_m = re.search(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
            if not link_m:
                continue
            raw_url = html.unescape(link_m.group(1))
            url = clean_bing_url(raw_url)
            title = re.sub(r'<[^>]+>', '', link_m.group(2)).strip()

            summary_m = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
            summary = re.sub(r'<[^>]+>', '', summary_m.group(1)).strip() if summary_m else ""

            date_m = re.search(r'(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})', summary)
            pub_date = ""
            if date_m:
                y, m, d = date_m.groups()
                pub_date = f"{y}-{int(m):02d}-{int(d):02d}"

            # [P3 修复·D8] Bing 结果页本身不带账号字段，先从摘要/标题兜底识别，
            # 抓取正文后由 _extract_metadata 再做一次精确覆盖。
            account = self._guess_account_from_text(f"{summary} {title}")

            if title and ("mp.weixin.qq.com" in url or "mp.weixin.qq.com" in raw_url or "weixin" in url):
                items.append(WeChatArticleItem(
                    title=title,
                    url=url,
                    source_account=account,
                    publish_date=pub_date,
                    summary=summary,
                    source_platform="bing"
                ))
        return items


class WeChatArticleFetcher(_AccountNameResolver):
    """微信公众号文章正文抓取器"""

    def fetch_article(self, item: WeChatArticleItem) -> WeChatArticleItem:
        """
        抓取文章正文并清洗为 Markdown
        """
        if not item.url or item.url.startswith("file://") or Path(item.url).exists():
            return item

        try:
            url = item.url.strip()
            _respect_host_rate_limit(url, minimum_interval=random.uniform(0.5, 1.0))
            try:
                url = urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=-_.~%=")
            except Exception as exc:
                # 转义失败则沿用原始 URL 继续请求，留痕便于区分「URL 本身异常」
                import logging
                logging.getLogger(__name__).debug(
                    "URL 转义失败（沿用原始 URL）: %s -> %s", url, exc)

            # [审计 2026-09-30 P1-7] item.url 来自搜索结果解析（外部可控输入），
            # 发起前先做 SSRF 校验：恶意结果页指向内网/回环地址时 fail-closed 拒绝。
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": "https://mp.weixin.qq.com/",
            })
            with safe_urlopen(req, timeout=12) as resp:
                raw_html = resp.read(MAX_HTTP_RESPONSE_BYTES).decode(
                    "utf-8", errors="replace")

            item.content_html = raw_html
            self._extract_metadata(raw_html, item)
            item.content_markdown = self._html_to_markdown(raw_html)
            item.fetched = bool(item.content_markdown and len(item.content_markdown) > 30)

        except Exception as e:
            item.summary = f"[抓取失败或受限]: {e}"

        return item

    def fetch_article_via_browser(self, item: WeChatArticleItem,
                                  should_stop=None) -> WeChatArticleItem:
        """[P0] 正文浏览器兜底：HTTP 直连失败后，用真实会话渲染文章页。

        仅在闸门开启且两级可用时生效（内部复检，不依赖调用方）；失败保持 item
        原状（不改写既有失败标注，也不清空已抓到的部分内容）。
        """
        manager = _load_browser_manager()
        if manager is None or not item.url or not item.url.startswith("http"):
            return item
        try:
            if not (manager.is_enabled() and manager.is_available()):
                return item
        except Exception:
            return item

        try:
            result = manager.fetch_with_browser(
                item.url, timeout_sec=12, should_stop=should_stop)
        except Exception:
            return item
        if result.access_status != "OK" or not result.content:
            return item

        html_text = result.content
        item.content_html = html_text
        self._extract_metadata(html_text, item)
        item.content_markdown = self._html_to_markdown(html_text)
        item.fetched = bool(item.content_markdown and len(item.content_markdown) > 30)
        if not item.fetched:
            item.summary = "[抓取失败或受限]: 浏览器渲染后仍无有效正文"
        return item

    def _extract_metadata(self, html_text: str, item: WeChatArticleItem):
        """从文章 HTML 提取标题、公众号名称、发布时间

        [P3 修复·D8] 公众号字段此前仅尝试 `id="js_name"` 一种选择器，
        微信正文页改版后该节点常缺失 → 三端一致显示「公众号: 未知」。
        现改为多选择器 + 摘要兜底，最大程度还原账号名。
        """
        title_m = re.search(r'<h1[^>]*id="activity-name"[^>]*>(.*?)</h1>', html_text, re.DOTALL)
        if title_m:
            item.title = re.sub(r'<[^>]+>', '', title_m.group(1)).strip()

        if not item.source_account:
            item.source_account = self._extract_account_from_html(html_text)
        if not item.source_account:
            item.source_account = self._guess_account_from_text(
                " ".join([item.summary or "", item.title or ""]))

        time_m = re.search(r'var\s+ct\s*=\s*["\'](\d+)["\']', html_text)
        if time_m:
            try:
                ts = int(time_m.group(1))
                item.publish_date = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            except Exception as exc:
                # 时间戳非法只丢失发布日期；留痕以便排查时间戳格式变化
                import logging
                logging.getLogger(__name__).debug(
                    "详情页发布时间解析失败（保留空日期）: %s -> %s", time_m.group(1), exc)
        elif not item.publish_date:
            date_m = re.search(r'(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})', html_text)
            if date_m:
                y, m, d = date_m.groups()
                item.publish_date = f"{y}-{int(m):02d}-{int(d):02d}"

    # [P3 修复·D8] `_ACCOUNT_PATTERNS` / `_extract_account_from_html` /
    # `_guess_account_from_text` 已上提至 _AccountNameResolver，与检索引擎共用。

    def _html_to_markdown(self, html_text: str) -> str:
        """
        将微信文章 HTML 转换为整洁的 Markdown
        策略: 提取 #js_content 正文区域 -> 逐标签清洗
        """
        if not html_text:
            return ""

        content_m = re.search(
            r'<div[^>]*id="js_content"[^>]*>(.*?)</div>\s*(?:<div[^>]*id="js_pc_qr_code"|<script|<!--|\Z)',
            html_text, re.DOTALL
        )
        content = content_m.group(1) if content_m else html_text

        # 逐标签清洗转换
        content = re.sub(r'<p[^>]*>', '\n', content)
        content = re.sub(r'</p>', '\n', content)
        content = re.sub(r'<br\s*/?>', '\n', content)
        content = re.sub(r'<h1[^>]*>(.*?)</h1>', r'\n# \1\n', content, flags=re.DOTALL)
        content = re.sub(r'<h2[^>]*>(.*?)</h2>', r'\n## \1\n', content, flags=re.DOTALL)
        content = re.sub(r'<h3[^>]*>(.*?)</h3>', r'\n### \1\n', content, flags=re.DOTALL)
        content = re.sub(r'<h4[^>]*>(.*?)</h4>', r'\n#### \1\n', content, flags=re.DOTALL)
        content = re.sub(r'<(?:strong|b)[^>]*>(.*?)</(?:strong|b)>', r'**\1**', content, flags=re.DOTALL)
        content = re.sub(r'<(?:em|i)[^>]*>(.*?)</(?:em|i)>', r'*\1*', content, flags=re.DOTALL)

        # 图片提取
        content = re.sub(r'<img[^>]+data-src="([^"]+)"[^>]*/?>', r'\n![图片](\1)\n', content)
        content = re.sub(r'<img[^>]+src="([^"]+)"[^>]*/?>', r'\n![图片](\1)\n', content)

        # 链接、代码与引用
        content = re.sub(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', r'[\2](\1)', content, flags=re.DOTALL)
        content = re.sub(r'<code[^>]*>(.*?)</code>', r'`\1`', content, flags=re.DOTALL)
        content = re.sub(r'<pre[^>]*>(.*?)</pre>', r'\n```\n\1\n```\n', content, flags=re.DOTALL)
        content = re.sub(r'<blockquote[^>]*>(.*?)</blockquote>', r'\n> \1\n', content, flags=re.DOTALL)
        content = re.sub(r'<li[^>]*>(.*?)</li>', r'- \1\n', content, flags=re.DOTALL)
        content = re.sub(r'</?[uo]l[^>]*>', '', content)

        # 清除所有剩余 HTML 标签与空白
        content = re.sub(r'<[^>]+>', '', content)
        content = html.unescape(content)
        content = re.sub(r'\n{3,}', '\n\n', content)
        content = re.sub(r'[ \t]+', ' ', content)

        return content.strip()


class WeChatContentPipeline:
    """微信文章内容沉淀管道"""

    # [P0 修复] 经验档案含学员目标院校/专业隐私，落盘迁移至 .memory/experiences/
    # （.memory/ 已被 .gitignore 保护，绝不入库、绝不发布到 GitHub Pages）
    EXPERIENCES_DIR = ROOT / ".memory" / "experiences"

    def save_article(self, item: WeChatArticleItem, category: str = "考研经验") -> str:
        """
        将抓取的文章沉淀为 Markdown 文件到 .memory/experiences/
        :return: 保存路径
        """
        self.EXPERIENCES_DIR.mkdir(parents=True, exist_ok=True)

        safe_title = re.sub(r'[\\/:*?"<>|]+', '_', item.title)[:50]
        date_tag = item.publish_date or datetime.now().strftime("%Y-%m-%d")
        filename = f"微信_{safe_title}_{date_tag}.md"
        filepath = self.EXPERIENCES_DIR / filename

        md_lines = [
            f"# {item.title}",
            "",
            f"> **来源公众号**: {account_label(item.source_account)}  ",
            f"> **发布日期**: {date_label(item.publish_date)}  ",
            f"> **原文链接**: [{item.url}]({item.url})  ",
            f"> **检索平台**: {item.source_platform}  ",
            f"> **抓取时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}  ",
            f"> **内容分类**: {category}",
            "",
            "---",
            "",
            item.content_markdown or item.summary or "(正文抓取失败，请访问原文链接查看)",
            "",
            "---",
            "",
            "*本文由考研学习链微信公众号检索工具自动抓取并沉淀，仅用于个人学习研究。*",
        ]

        atomic_write_text(filepath, "\n".join(md_lines))
        return str(filepath)

    def feed_to_scout(self, item: WeChatArticleItem, school_name: str = "") -> bool:
        """
        将文章内容联动传递给 school_scout.py 院校侦察引擎
        作为社媒口碑数据源补充
        """
        try:
            import sys
            tools_dir = ROOT / "tools"
            if str(tools_dir) not in sys.path:
                sys.path.insert(0, str(tools_dir))
            from skills import school_scout

            if hasattr(school_scout, "append_experience_to_dossier") and school_name:
                # [P0 修复] 对齐 school_scout.append_experience_to_dossier 真实签名
                return school_scout.append_experience_to_dossier(
                    school_name=school_name,
                    source_or_major="微信公众号",
                    author_or_info=item.source_account or "微信学长",
                    content=(item.content_markdown or item.summary)[:1500],
                    url=item.url,
                    title=item.title
                )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "微信经验联动到院校档案失败（%s）: %s", school_name, exc
            )
        return False


def denoise_keyword(keyword: str) -> str:
    """
    [根因修复·检索恒 0 命中] 统一去除关键词里的"档案噪声"。

    背景：考生档案里的专业串常形如「085400 电子信息-通信工程（085400-02）」，
    直接拼进检索词后，搜狗/必应会把它当成一个整体长串做精确匹配 —— 结果恒 0 命中。
    实测对照（唯一变量=关键词）：
        示例院校A 085400 电子信息（085400-01） 考研   ->  0 篇
        示例院校A 电子信息 考研                       -> 10 篇

    此前该逻辑只写在 TUI 一处，CLI 顶层与 GUI 对话框都没有 —— 属于"修复覆盖不全"。
    这里下沉到唯一的检索入口 `wechat_search()`，三端自动同时生效。

    规则（顺序执行，且保证结果非空，否则原样返回）：
      1. 剔除括号段（全角/半角），如「（085400-01）」；
      2. 剔除 4~6 位专业代码数字，如「085400」；
      3. 合并多余空白并去掉首尾的连接符。
    """
    if not keyword:
        return keyword
    cleaned = re.sub(r"[（(][^）)]*[）)]", " ", keyword)   # 去括号段
    cleaned = re.sub(r"(?<!\d)\d{4,6}(?!\d)", " ", cleaned)   # 去 4~6 位专业代码
    # 说明：不能用 \b\d{4,6}\b —— Python 的 \w 含中日韩字符，
    # 「085400电子信息」这类中英粘连处不存在 \b 边界，会导致去噪完全失效。
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -·")
    # 兜底：若去噪后啥都不剩（例如关键词本身就是一长串专业代码），保留原词
    return cleaned if cleaned else keyword


def _should_stop_now(should_stop) -> bool:
    """安全轮询停止回调（异常视作停止，fail-closed）。"""
    if should_stop is None:
        return False
    try:
        return bool(should_stop())
    except Exception:
        return True


def wechat_search(
    keyword: str,
    max_results: int = 10,
    fetch_content: bool = True,
    save_to_local: bool = False,
    school_name: str = "",
    source: str = "auto",
    time_range: str = "year",
    should_stop=None
) -> Dict[str, Any]:
    """
    微信公众号文章检索与抓取统一入口

    :param keyword: 检索关键词
    :param max_results: 最大结果数
    :param fetch_content: 是否抓取文章正文
    :param save_to_local: 是否沉淀到本地 .memory/experiences/
    :param school_name: 联动院校侦察引擎的目标校名
    :param source: 检索源 "sogou" / "bing" / "local" / "auto"
    :param time_range: 时间范围 "year" (近一年) / "half_year" (近半年) / "three_years" (近三年) / "all" (全部)
    :param should_stop: 可选停止回调（GUI 关窗中断）；浏览器兜底链在检查点轮询
    :return: 包含检索状态、结果列表与落盘路径的字典
    """
    # [根因修复] 去噪放在唯一入口，保证 CLI / TUI / GUI 三端行为一致。
    # 返回体里的 keyword_used 供上层回显，便于排查"为什么搜出来的和输入的不一样"。
    keyword_used = denoise_keyword(keyword)
    keyword = keyword_used

    engine = WeChatSearchEngine()
    fetcher = WeChatArticleFetcher()
    pipeline = WeChatContentPipeline()

    # 1. 检索
    items = engine.search(keyword, max_results, source=source, time_range=time_range,
                          should_stop=should_stop)

    # 2. 抓取正文
    if fetch_content:
        # [P0 浏览器兜底] HTTP 直连失败的条目再用真实会话渲染（上限条数，逐条
        # 预算 12s）；闸门 off / 未装 playwright / 半态时 browser_left 恒为 0，
        # 行为与基线逐字节一致。
        browser_left = BROWSER_ARTICLE_LIMIT if _browser_fallback_ready() else 0
        for item in items:
            if _should_stop_now(should_stop):
                break
            if not item.fetched:
                fetcher.fetch_article(item)
            if not item.fetched and browser_left > 0:
                browser_left -= 1
                fetcher.fetch_article_via_browser(item, should_stop=should_stop)

    # 3. 沉淀与联动
    saved_paths = []
    scout_linked = False
    if save_to_local:
        for item in items:
            path = pipeline.save_article(item, category=keyword)
            saved_paths.append(path)
            if school_name:
                linked = pipeline.feed_to_scout(item, school_name)
                if linked:
                    scout_linked = True

    return {
        "success": True,
        "keyword": keyword,
        "keyword_used": keyword_used,
        "total": len(items),
        "fetched": sum(1 for i in items if i.fetched),
        # [P3 修复·D8] 检索源失败原因随结果一并上抛，供上层区分「确实没有结果」与「检索源异常」
        "source_errors": list(engine.last_errors),
        # [P2-9 修复·两端口径] 各源结果清单（成功 N 条 / 失败原因 / 未启用），
        # CLI 与 TUI 报告头统一打印同一份清单。
        "source_status": list(engine.last_source_status),
        "results": [
            {
                "title": i.title,
                "url": i.url,
                "source": i.source_account or account_label(i.source_account),
                "date": i.publish_date or date_label(i.publish_date),
                "source_account": i.source_account,
                # [P3 修复·D8] 下沉到唯一入口的三端统一文案，避免各端各写一套「未知」
                "account_display": account_label(i.source_account),
                "date_display": date_label(i.publish_date),
                "publish_date": i.publish_date,
                "summary": (i.summary or i.content_markdown[:200]).replace("\n", " ").strip(),
                "content_length": len(i.content_markdown),
                "fetched": i.fetched,
                "source_platform": i.source_platform
            }
            for i in items
        ],
        "saved_paths": saved_paths,
        "scout_linked": scout_linked,
    }


def parse_sogou_wechat_html(html_text: str) -> List[Dict[str, str]]:
    """从搜狗微信 HTML 中直接提取标准文章条目列表。

    :return: 包含 title, source, date, url 的标准字典列表
    """
    engine = WeChatSearchEngine()
    items = engine._parse_sogou_results(html_text)
    return [
        {
            "title": item.title,
            "source": item.source_account or account_label(item.source_account),
            "date": item.publish_date or date_label(item.publish_date),
            "url": item.url,
        }
        for item in items
    ]


def search_wechat_articles(
    keyword: str,
    max_results: int = 10,
    source: str = "auto"
) -> List[Dict[str, str]]:
    """快速检索微信公众号文章并返回干净的结构化列表。

    :return: `[{"title": ..., "source": ..., "date": ..., "url": ...}]`
    """
    res = wechat_search(keyword, max_results=max_results, fetch_content=False, source=source)
    return [
        {
            "title": r["title"],
            "source": r.get("source") or r.get("account_display") or "",
            "date": r.get("date") or r.get("date_display") or "",
            "url": r["url"],
        }
        for r in res.get("results", [])
    ]


# 别名兼容
search_wechat_experiences = wechat_search


def health_check() -> dict:
    """[B4] 结构化健康自检：``{"status": READY/DEGRADED/UNAVAILABLE, "reason": str}``。

    本技能零第三方依赖（纯标准库 + 外网检索）：用**真调一次最小用例**验证
    关键词去噪链路可用。联网可达性不做探活（慢且离线会假红），在 reason 中如实
    标注"需联网"——调用失败时由检索层给出网络错误提示。
    """
    try:
        kw = denoise_keyword("样本关键词（测试）")
    except Exception as e:  # noqa: BLE001 - 自检异常必须收敛为可见状态
        return {"status": "UNAVAILABLE",
                "reason": f"关键词去噪链路失败（{type(e).__name__}: {e}），ky wechat 将不可用"}
    if kw is None:
        return {"status": "UNAVAILABLE", "reason": "关键词去噪产出为空，ky wechat 将不可用"}
    return {"status": "READY",
            "reason": "多源检索与 Markdown 清洗可用（核心零第三方依赖；检索需联网；"
                      "浏览器兜底为可选增强，默认关闭）"}

