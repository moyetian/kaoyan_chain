# -*- coding: utf-8 -*-
"""
KaoYan Intelligence · 官方站点发现器 (Official Discovery & Search)

职责：
  生成 site: 域名限定搜索词（Search as Discovery，仅用于发掘候选页面）

[P2 清理·2026-10-08] 原「robots.txt/Sitemap 探测」与「搜索引擎跳转链接解密」
两个方法全仓零引用（死代码，含字符串/动态引用穷举核验），已删除；
实际在用的只有 ``build_targeted_queries``（scout_engine 与 search/rewrite 消费）。
"""

import urllib.parse
from typing import List, Optional
from .fetcher import HTTPFetcher
from .models import current_exam_year

ADMISSION_KEYWORDS = [
    "招生简章", "硕士研究生招生", "招生专业目录", "自命题考试大纲",
    "硕士招生", "拟招人数", "复试基本线", "复试细则", "拟录取名单",
    "master", "admission", "enrollment", "zsml"
]


class OfficialDiscovery:
    """高校官方站点页面发现器"""

    def __init__(self, fetcher: Optional[HTTPFetcher] = None):
        self.fetcher = fetcher or HTTPFetcher(timeout=5)

    def build_targeted_queries(
        self,
        school_name: str,
        domain: str,
        major_keyword: Optional[str] = None,
        year: Optional[int] = None
    ) -> List[str]:
        """
        构建针对特定高校官方站点的精准检索词 (site: 语法)
        """
        # [R11 修复·字面 0/None] 默认参数不得在导入期求值（旧写法
        # `year: int = current_exam_year()` 把导入时刻的年份冻结成默认值）；
        # 调用方传 None/0 时在此**运行时**兜底为当前考试年，查询词恒为 4 位年份，
        # 不再出现 `site:域名 0 硕士 招生简章` 或字面 None。
        year = year or current_exam_year()
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
