# -*- coding: utf-8 -*-
"""
KaoYan Intelligence · 官方站点发现器 (Official Discovery & Sitemap / Search)

职责：
  1. 通过 robots.txt 探测高校官方 Sitemap.xml 并匹配招考关键词
  2. 生成并执行 site: 域名限定搜索（Search as Discovery，仅用于发掘候选页面）
  3. 解密与清洗搜索引擎的跳转链接
"""

import re
import base64
import urllib.parse
from typing import List, Dict, Any, Optional
from .fetcher import HTTPFetcher, FetchResult

ADMISSION_KEYWORDS = [
    "招生简章", "硕士研究生招生", "招生专业目录", "自命题考试大纲",
    "硕士招生", "拟招人数", "复试基本线", "复试细则", "拟录取名单",
    "master", "admission", "enrollment", "zsml"
]


class OfficialDiscovery:
    """高校官方站点页面发现器"""

    def __init__(self, fetcher: Optional[HTTPFetcher] = None):
        self.fetcher = fetcher or HTTPFetcher(timeout=5)

    def discover_from_sitemap(self, base_domain: str) -> List[str]:
        """
        探测 robots.txt 并解析 sitemap.xml 中的招考相关链接
        """
        if not base_domain:
            return []

        parsed = urllib.parse.urlparse(base_domain)
        root_url = f"{parsed.scheme}://{parsed.netloc}"
        robots_url = f"{root_url}/robots.txt"

        res = self.fetcher.fetch(robots_url)
        if not res.is_valid:
            return []

        sitemap_urls = re.findall(r"^Sitemap:\s*(https?://\S+)", res.content, re.MULTILINE | re.IGNORECASE)
        candidate_urls: List[str] = []

        for sm_url in sitemap_urls[:2]:
            sm_res = self.fetcher.fetch(sm_url)
            if sm_res.is_valid:
                # 从 sitemap xml 中提取 <loc>
                locs = re.findall(r"<loc>(https?://[^<]+)</loc>", sm_res.content, re.IGNORECASE)
                for loc in locs:
                    loc_lower = loc.lower()
                    if any(kw in loc_lower for kw in ["yjs", "zs", "master", "grad", "admission", "2026", "2027"]):
                        candidate_urls.append(loc)

        return candidate_urls[:10]

    def build_targeted_queries(
        self,
        school_name: str,
        domain: str,
        major_keyword: Optional[str] = None,
        year: int = 2027
    ) -> List[str]:
        """
        构建针对特定高校官方站点的精准检索词 (site: 语法)
        """
        queries = []
        parsed = urllib.parse.urlparse(domain)
        clean_domain = parsed.netloc or domain.replace("https://", "").replace("http://", "").split("/")[0]

        # 1. 针对该校域名的招生简章查询
        queries.append(f"site:{clean_domain} {year} 硕士 招生简章")
        
        # 2. 针对该校域名的专业目录与考试大纲
        if major_keyword:
            queries.append(f"site:{clean_domain} {major_keyword} {year} 专业目录 大纲")
        else:
            queries.append(f"site:{clean_domain} {year} 硕士研究生 招生专业目录")

        return queries

    def clean_search_url(self, raw_url: str) -> str:
        """
        清洗搜索引擎跳转链接（如必应 Base64 清洗）
        """
        if "bing.com/ck/a?" in raw_url:
            match = re.search(r"[?&]u=a1([A-Za-z0-9+/=_-]+)", raw_url)
            if match:
                encoded = match.group(1)
                # 处理 URL safe base64
                encoded = encoded.replace("-", "+").replace("_", "/")
                rem = len(encoded) % 4
                if rem:
                    encoded += "=" * (4 - rem)
                try:
                    decoded = base64.b64decode(encoded).decode("utf-8", errors="ignore")
                    if decoded.startswith("http://") or decoded.startswith("https://"):
                        return decoded
                except Exception:
                    pass
        return raw_url
