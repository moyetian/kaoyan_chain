# -*- coding: utf-8 -*-
"""
KaoYan Intelligence · 文档与页面结构化抽取器 (Document & Page Extractor)

职责：
  1. 解析官方通知 HTML，清洗正文并提取标题、发布日期
  2. 提取拟招生人数、初试科目、自命题代码与复试分数线关键招考事实
  3. 智能发现并解析 PDF 附件（如招生专业目录.pdf、大纲.pdf）
"""

import re
import html
from typing import Dict, Any, List, Optional
from .models import EvidenceObject, EvidenceSource
from .evidence_engine import build_evidence


class DocumentExtractor:
    """招考文档与网页内容抽取器"""

    def extract_from_html(
        self,
        html_text: str,
        page_url: str,
        school_name: str,
        target_year: int = 2027,
        source_type: str = "graduate_school"
    ) -> List[EvidenceObject]:
        """从官方通知 HTML 提取关键事实并转化为证据对象"""
        evidences: List[EvidenceObject] = []
        if not html_text:
            return evidences

        # 1. 抽取标题
        title = self._extract_title(html_text)
        pub_date = self._extract_pub_date(html_text)
        
        # 判断年份
        detected_year = target_year
        year_match = re.search(r"(202[4-9])\s*年?", title)
        if year_match:
            detected_year = int(year_match.group(1))

        # 2. 招生简章 / 专业目录公告本身作为一条证据
        if any(kw in title for kw in ["招生简章", "专业目录", "招考方案", "简章"]):
            ev_notice = build_evidence(
                field_name="官方硕士招生简章与通告",
                value={"title": title, "url": page_url, "published_date": pub_date},
                unit="篇",
                exam_year=detected_year,
                source_type=source_type,
                source_name=f"{school_name} 官方公告",
                source_url=page_url,
                published_at=pub_date,
                target_year=target_year
            )
            evidences.append(ev_notice)

        # 3. 提取拟招生人数
        quota_match = re.search(r"拟[招录收][收录取]?\s*(?:全日制|非全日制)?\s*(?:硕士)?(?:研究生)?(?:人数|计划)?[:：\s]*(\d+)\s*人", html_text)
        if not quota_match:
            quota_match = re.search(r"(?:招生计划|招生规模|拟招)[:：\s]*(\d+)\s*人", html_text)
            
        if quota_match:
            quota = int(quota_match.group(1))
            ev_quota = build_evidence(
                field_name="拟招生人数",
                value=quota,
                unit="人",
                exam_year=detected_year,
                source_type=source_type,
                source_name=f"{school_name} 官方通告",
                source_url=page_url,
                published_at=pub_date,
                target_year=target_year
            )
            evidences.append(ev_quota)

        # 4. 提取初试科目信息 (如 408, 101, 204 等)
        subjects = self._extract_subjects(html_text)
        if subjects:
            ev_sub = build_evidence(
                field_name="初试科目配置",
                value=subjects,
                unit="门",
                exam_year=detected_year,
                source_type=source_type,
                source_name=f"{school_name} 官方大纲/目录",
                source_url=page_url,
                published_at=pub_date,
                target_year=target_year
            )
            evidences.append(ev_sub)

        # 5. 发现 PDF 附件
        pdf_links = self._extract_pdf_links(html_text, page_url)
        if pdf_links:
            ev_pdf = build_evidence(
                field_name="官方PDF招生目录附件",
                value=pdf_links,
                unit="个",
                exam_year=detected_year,
                source_type=source_type,
                source_name=f"{school_name} 官方附件",
                source_url=page_url,
                published_at=pub_date,
                target_year=target_year
            )
            evidences.append(ev_pdf)

        return evidences

    def _extract_title(self, html_text: str) -> str:
        """提取页面标题"""
        match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
        if match:
            raw_title = match.group(1).strip()
            # 移除常见后缀
            cleaned = re.sub(r"[-_]\s*.*?(?:研究生院|招生网|大学官网)$", "", raw_title)
            return html.unescape(cleaned).strip()
        return "高校研招官方通知"

    def _extract_pub_date(self, html_text: str) -> Optional[str]:
        """提取发布时间"""
        # 常见日期格式：2026-09-04 或 2026年09月04日
        match = re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", html_text)
        if match:
            y, m, d = match.groups()
            return f"{y}-{int(m):02d}-{int(d):02d}"
        return None

    def _extract_subjects(self, html_text: str) -> List[str]:
        """提取初试统考与自命题科目"""
        subjects = []
        # 匹配统考特征词
        patterns = [
            r"(\(101\)思想政治理论|101思想政治理论|思想政治理论)",
            r"(\(201\)英语\(一\)|201英语一|英语一)",
            r"(\(204\)英语\(二\)|204英语二|英语二)",
            r"(\(301\)数学\(一\)|301数学一|数学一)",
            r"(\(302\)数学\(二\)|302数学二|数学二)",
            r"(\(408\)计算机学科专业基础|408计算机学科专业基础|408)",
            r"(\(\d{3}\)[\u4e00-\u9fa5]+)"  # 自命题三位代码科目
        ]
        for p in patterns:
            found = re.findall(p, html_text)
            for item in found:
                if item and item not in subjects and len(subjects) < 4:
                    subjects.append(item)
        return subjects

    def _extract_pdf_links(self, html_text: str, base_url: str) -> List[Dict[str, str]]:
        """提取页面中的 PDF 下载链接与说明"""
        results = []
        matches = re.findall(r"<a[^>]+href=[\"']([^\"']+\.pdf)[\"'][^>]*>(.*?)</a>", html_text, re.IGNORECASE)
        for link, text in matches[:5]:
            clean_text = re.sub(r"<[^>]+>", "", text).strip() or "招生专业目录/自命题大纲 PDF"
            full_url = link
            if not link.startswith("http"):
                import urllib.parse
                full_url = urllib.parse.urljoin(base_url, link)
            results.append({"name": clean_text, "url": full_url})
        return results
