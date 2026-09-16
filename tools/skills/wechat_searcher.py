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
import time
import random
import urllib.request
import urllib.parse
import urllib.error
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

try:  # 双导入路径兼容（项目同时存在 tools.X 与 X 两种导入方式）
    from ky_io import atomic_write_text  # noqa: E402
except ImportError:  # pragma: no cover
    from tools.ky_io import atomic_write_text  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# [P3 修复·D8] 三端口径统一：账号/日期缺失时不再各自渲染成含糊的「未知」，
# 统一由下方 label 函数给出可解释文案，避免"同一份数据三种说法"。
UNKNOWN_ACCOUNT_LABEL = "未识别（平台未公开）"
UNKNOWN_DATE_LABEL = "未标注日期"


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
        source: str = "auto"
    ) -> List[WeChatArticleItem]:
        """
        统一检索入口
        :param keyword: 检索关键词 (如 "408计算机考研经验")
        :param max_results: 最大结果数
        :param source: "sogou" / "bing" / "auto" (自动降级) / "local"
        """
        keyword = keyword.strip()
        if not keyword:
            return []

        if source == "local":
            return self.search_local_cache(keyword, max_results)

        if source == "auto":
            # 优先搜狗，少于3条时降级 Bing，仍不足则补充本地缓存
            results = self._search_sogou(keyword, max_results)
            if len(results) < 3:
                bing_results = self._search_bing(keyword, max_results - len(results))
                # 依据 url 去重
                existing_urls = {r.url for r in results}
                for br in bing_results:
                    if br.url not in existing_urls:
                        results.append(br)
                        existing_urls.add(br.url)

            if len(results) < 2:
                local_results = self.search_local_cache(keyword, max_results - len(results))
                existing_urls = {r.url for r in results}
                for lr in local_results:
                    if lr.url not in existing_urls:
                        results.append(lr)
                        existing_urls.add(lr.url)

            return results[:max_results]
        elif source == "sogou":
            return self._search_sogou(keyword, max_results)
        elif source == "bing":
            return self._search_bing(keyword, max_results)

        return []

    def _search_sogou(self, keyword: str, max_results: int) -> List[WeChatArticleItem]:
        """搜狗微信搜索"""
        params = {
            "type": "2",  # 2 = 搜文章
            "query": keyword,
            "ie": "utf-8"
        }
        url = f"{self.SOGOU_WX_URL}?{urllib.parse.urlencode(params)}"

        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Referer": "https://weixin.sogou.com/",
                "Accept-Language": "zh-CN,zh;q=0.9",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                html_content = resp.read().decode("utf-8", errors="replace")

            items = self._parse_sogou_results(html_content)[:max_results]
            if not items:
                # [P3 修复·D8] 反爬验证页/改版页会 200 + 0 结果块，静默即等于「没有文章」
                self._note_source_empty("搜狗微信", len(html_content))
            return items
        # [P3 修复·D8] 去掉静默吞异常：失败必须留痕，否则「检索源崩了」会被伪装成「没有文章」
        except Exception as e:
            self._note_source_error("搜狗微信", e)
            return []

    def _search_bing(self, keyword: str, max_results: int) -> List[WeChatArticleItem]:
        """Bing 搜索微信文章 (限定 mp.weixin.qq.com 域名)"""
        query = f"site:mp.weixin.qq.com {keyword}"
        params = {"q": query, "count": str(max(max_results * 2, 10))}
        url = f"{self.BING_URL}?{urllib.parse.urlencode(params)}"

        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                html_content = resp.read().decode("utf-8", errors="replace")

            items = self._parse_bing_results(html_content)[:max_results]
            if not items:
                # [P3 修复·D8] 同上：解析 0 条同样留痕
                self._note_source_empty("Bing", len(html_content))
            return items
        # [P3 修复·D8] 同上：Bing 源失败同样留痕，不再静默返回空列表
        except Exception as e:
            self._note_source_error("Bing", e)
            return []

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
        """解析搜狗搜索结果页

        [P3 修复·D8] 两级修正：
        ① 结果块此前只按 `<div class="txt-box">...</div></div>` 非贪婪切片，
           公众号/时间节点位于同级兄弟节点（`div.s-p`）时被截断 → 账号字段整列丢失；
           改为按 `<li ...news-item|sogou_vr...>` 整块切分（账号、时间都在块内），
           取不到再回退 txt-box 切片。
        ② 结果块内首个 `<a>` 实际是缩略图链接（`div.img-box`），旧逻辑直接取首个锚点
           → 标题被剥成空串，条目在 `if title and raw_url` 处被整体丢弃，
           真实结果页（实测 10 条）因此恒返回 0 条。
           现改为优先取 `<h3>` 内的标题锚点，并回退到首个「锚文本非空」的链接。
        """
        items = []
        result_blocks = re.findall(
            r'<li[^>]*(?:news-item|sogou_vr)[^>]*>(.*?)</li>',
            html_text, re.DOTALL
        )
        if not result_blocks:
            result_blocks = re.findall(
                r'<div class="txt-box"[^>]*>(.*?)</div>\s*</div>',
                html_text, re.DOTALL
            )
        for block in result_blocks:
            # [P3 修复·D8] 优先取标题锚点：<h3> 内链接；回退到首个锚文本非空的链接
            title_m = re.search(
                r'<h3[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                block, re.DOTALL
            )
            if not title_m:
                for _cand in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL):
                    if re.sub(r'<[^>]+>', '', _cand.group(2)).strip():
                        title_m = _cand
                        break
            if not title_m:
                continue
            raw_url = html.unescape(title_m.group(1))
            if not raw_url.startswith("http"):
                raw_url = urllib.parse.urljoin(self.SOGOU_WX_URL, raw_url)
            title = html.unescape(re.sub(r'<[^>]+>', '', title_m.group(2))).strip()

            account_m = re.search(r'<a[^>]*class="[^"]*account[^"]*"[^>]*>(.*?)</a>', block, re.DOTALL)
            account = re.sub(r'<[^>]+>', '', account_m.group(1)).strip() if account_m else ""
            # [P3 修复·D8] 新版列表页无 class="account" 锚点，账号名位于 <span class="all-time-y2">
            if not account:
                span_m = re.search(r'<span[^>]*class="[^"]*all-time-y2[^"]*"[^>]*>(.*?)</span>',
                                   block, re.DOTALL)
                if span_m:
                    account = html.unescape(re.sub(r'<[^>]+>', '', span_m.group(1))).strip()

            summary_m = re.search(r'<p class="txt-info"[^>]*>(.*?)</p>', block, re.DOTALL)
            summary = html.unescape(re.sub(r'<[^>]+>', '', summary_m.group(1))).strip() if summary_m else ""

            # [P3 修复·D8] 列表页账号缺失时从摘要/标题兜底识别，避免直接落到「未知」
            if not account:
                account = self._guess_account_from_text(f"{summary} {title}")

            time_m = re.search(r"timeConvert\(['\"](\d+)['\"]\)", block)
            pub_date = ""
            if time_m:
                try:
                    pub_date = datetime.fromtimestamp(int(time_m.group(1))).strftime("%Y-%m-%d")
                except Exception as exc:
                    # 时间戳非法只丢失发布日期这一条元数据，不影响正文入库
                    import logging
                    logging.getLogger(__name__).debug(
                        "列表页发布时间解析失败（保留空日期）: %s -> %s", time_m.group(1), exc)

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
        result_blocks = re.findall(r'<li class="b_algo"[^>]*>(.*?)</li>', html_text, re.DOTALL)
        for block in result_blocks:
            link_m = re.search(r'<a[^>]+href="(https?://mp\.weixin\.qq\.com/[^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
            if not link_m:
                continue
            url = html.unescape(link_m.group(1))
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

            if title and "mp.weixin.qq.com" in url:
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
            # 随机轻微延迟，避免高频请求触发流控
            time.sleep(random.uniform(0.3, 0.8))

            url = item.url.strip()
            try:
                url = urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=-_.~%=")
            except Exception as exc:
                # 转义失败则沿用原始 URL 继续请求，留痕便于区分「URL 本身异常」
                import logging
                logging.getLogger(__name__).debug(
                    "URL 转义失败（沿用原始 URL）: %s -> %s", url, exc)

            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": "https://mp.weixin.qq.com/",
            })
            with urllib.request.urlopen(req, timeout=12) as resp:
                raw_html = resp.read().decode("utf-8", errors="replace")

            item.content_html = raw_html
            self._extract_metadata(raw_html, item)
            item.content_markdown = self._html_to_markdown(raw_html)
            item.fetched = bool(item.content_markdown and len(item.content_markdown) > 30)

        except Exception as e:
            item.summary = f"[抓取失败或受限]: {e}"

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
    cleaned = re.sub(r"\b\d{4,6}\b", " ", cleaned)          # 去 4~6 位专业代码
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -·")
    # 兜底：若去噪后啥都不剩（例如关键词本身就是一长串专业代码），保留原词
    return cleaned if cleaned else keyword


def wechat_search(
    keyword: str,
    max_results: int = 10,
    fetch_content: bool = True,
    save_to_local: bool = False,
    school_name: str = "",
    source: str = "auto"
) -> Dict[str, Any]:
    """
    微信公众号文章检索与抓取统一入口

    :param keyword: 检索关键词
    :param max_results: 最大结果数
    :param fetch_content: 是否抓取文章正文
    :param save_to_local: 是否沉淀到本地 .memory/experiences/
    :param school_name: 联动院校侦察引擎的目标校名
    :param source: 检索源 "sogou" / "bing" / "local" / "auto"
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
    items = engine.search(keyword, max_results, source=source)

    # 2. 抓取正文
    if fetch_content:
        for item in items:
            if not item.fetched:
                fetcher.fetch_article(item)

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
        "results": [
            {
                "title": i.title,
                "url": i.url,
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


# 别名兼容
search_wechat_experiences = wechat_search

