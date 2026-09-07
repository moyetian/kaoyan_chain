# -*- coding: utf-8 -*-
"""
考研学习链 · 微信公众号文章检索与爬虫工具 (WeChat Article Searcher)

核心功能：
  1. 多源检索：搜狗微信搜索 (主源) + Bing 微信文章搜索 (备用源) + 本地经验缓存
  2. 文章正文抓取与 HTML→Markdown 清洗解析
  3. 优质内容沉淀到 docs/experiences/ 作为社媒考研经验档案
  4. 联动 school_scout.py 院校侦察引擎丰富研报数据源

合规声明：本工具仅用于个人学习研究，遵循各平台使用条款，不进行大规模分布式爬取。
"""

import os
import re
import json
import html
import time
import random
import urllib.request
import urllib.parse
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent.parent

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


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


class WeChatSearchEngine:
    """多源微信公众号文章搜索引擎"""

    SOGOU_WX_URL = "https://weixin.sogou.com/weixin"
    BING_URL = "https://www.bing.com/search"

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

            return self._parse_sogou_results(html_content)[:max_results]
        except Exception:
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

            return self._parse_bing_results(html_content)[:max_results]
        except Exception:
            return []

    def search_local_cache(self, keyword: str, max_results: int) -> List[WeChatArticleItem]:
        """检索已沉淀在 docs/experiences/ 中的本地文章"""
        exp_dir = ROOT / "docs" / "experiences"
        if not exp_dir.exists():
            return []

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
        """解析搜狗搜索结果页"""
        items = []
        result_blocks = re.findall(
            r'<div class="txt-box"[^>]*>(.*?)</div>\s*</div>',
            html_text, re.DOTALL
        )
        for block in result_blocks:
            title_m = re.search(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
            if not title_m:
                continue
            raw_url = html.unescape(title_m.group(1))
            if not raw_url.startswith("http"):
                raw_url = urllib.parse.urljoin(self.SOGOU_WX_URL, raw_url)
            title = re.sub(r'<[^>]+>', '', title_m.group(2)).strip()

            account_m = re.search(r'<a[^>]*class="account"[^>]*>(.*?)</a>', block, re.DOTALL)
            account = re.sub(r'<[^>]+>', '', account_m.group(1)).strip() if account_m else ""

            summary_m = re.search(r'<p class="txt-info"[^>]*>(.*?)</p>', block, re.DOTALL)
            summary = re.sub(r'<[^>]+>', '', summary_m.group(1)).strip() if summary_m else ""

            time_m = re.search(r"timeConvert\(['\"](\d+)['\"]\)", block)
            pub_date = ""
            if time_m:
                try:
                    pub_date = datetime.fromtimestamp(int(time_m.group(1))).strftime("%Y-%m-%d")
                except Exception:
                    pass

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

            if title and "mp.weixin.qq.com" in url:
                items.append(WeChatArticleItem(
                    title=title,
                    url=url,
                    publish_date=pub_date,
                    summary=summary,
                    source_platform="bing"
                ))
        return items


class WeChatArticleFetcher:
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

            req = urllib.request.Request(item.url, headers={
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
        """从文章 HTML 提取标题、公众号名称、发布时间"""
        title_m = re.search(r'<h1[^>]*id="activity-name"[^>]*>(.*?)</h1>', html_text, re.DOTALL)
        if title_m:
            item.title = re.sub(r'<[^>]+>', '', title_m.group(1)).strip()

        if not item.source_account:
            acct_m = re.search(r'<a[^>]*id="js_name"[^>]*>(.*?)</a>', html_text, re.DOTALL)
            if acct_m:
                item.source_account = re.sub(r'<[^>]+>', '', acct_m.group(1)).strip()

        time_m = re.search(r'var\s+ct\s*=\s*["\'](\d+)["\']', html_text)
        if time_m:
            try:
                ts = int(time_m.group(1))
                item.publish_date = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            except Exception:
                pass
        elif not item.publish_date:
            date_m = re.search(r'(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})', html_text)
            if date_m:
                y, m, d = date_m.groups()
                item.publish_date = f"{y}-{int(m):02d}-{int(d):02d}"

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

    EXPERIENCES_DIR = ROOT / "docs" / "experiences"

    def save_article(self, item: WeChatArticleItem, category: str = "考研经验") -> str:
        """
        将抓取的文章沉淀为 Markdown 文件到 docs/experiences/
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
            f"> **来源公众号**: {item.source_account or '未知'}  ",
            f"> **发布日期**: {item.publish_date or '未知'}  ",
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

        filepath.write_text("\n".join(md_lines), encoding="utf-8")
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
                return school_scout.append_experience_to_dossier(
                    school_name=school_name,
                    source="微信公众号",
                    author=item.source_account or "微信学长",
                    content=(item.content_markdown or item.summary)[:1500],
                    url=item.url,
                    title=item.title
                )
        except Exception:
            pass
        return False


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
    :param save_to_local: 是否沉淀到本地 docs/experiences/
    :param school_name: 联动院校侦察引擎的目标校名
    :param source: 检索源 "sogou" / "bing" / "local" / "auto"
    :return: 包含检索状态、结果列表与落盘路径的字典
    """
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
        "total": len(items),
        "fetched": sum(1 for i in items if i.fetched),
        "results": [
            {
                "title": i.title,
                "url": i.url,
                "source_account": i.source_account,
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

