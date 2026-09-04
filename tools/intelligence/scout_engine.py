# -*- coding: utf-8 -*-
"""
KaoYan Intelligence · 招考情报调度中枢 (Scout & Intelligence Coordinator)

串联 L1~L6 全部能力：
  1. 意图解析与高校实体匹配 (University Resolver)
  2. 研招网 (CHSI) 官方 S 级基准提取
  3. 高校研究生院与二级学院官方 A 级证据抽取 (Fetcher + Extractor)
  4. 证据链校验、年份锁定与冲突仲裁 (Evidence Engine & Conflict Resolver)
  5. 社媒口碑与避坑直通车 (Social Connectors)
  6. 格式化终端卡片输出与研报落盘
"""

import os
import json
import urllib.parse
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

from .models import UniversityEntity, EvidenceObject
from .registry import get_registry, resolve_university
from .chsi_connector import CHSIConnector
from .fetcher import HTTPFetcher, BrowserPluginManager
from .discovery import OfficialDiscovery
from .extractor import DocumentExtractor
from .evidence_engine import build_evidence, resolve_conflicts

ROOT = Path(__file__).resolve().parent.parent.parent


class KaoYanIntelligenceEngine:
    """考研招考情报综合引擎"""

    def __init__(self):
        self.registry = get_registry()
        self.chsi = CHSIConnector()
        self.fetcher = HTTPFetcher(timeout=5)
        self.discovery = OfficialDiscovery(self.fetcher)
        self.extractor = DocumentExtractor()

    def query(
        self,
        school_query: str,
        major_query: Optional[str] = None,
        exam_year: int = 2027,
        save_report: bool = False
    ) -> Dict[str, Any]:
        """
        全流程执行高校招考情报检索与证据链聚合
        """
        # 1. 解析目标高校实体
        entity = resolve_university(school_query)
        school_name = entity.name if entity else school_query
        
        # 2. 构建有向站点图
        site_graph = self.registry.build_site_graph(entity, major_query) if entity else {
            "university": school_name,
            "chsi_code": "待查",
            "level": "全国研招单位",
            "region": "未知",
            "domains": {"official": "", "graduate_school": "", "admission_office": ""},
            "chsi_portals": {"zsml_catalog": "https://yz.chsi.com.cn/zsml/queryAction.do"},
            "site_tree": []
        }

        all_evidences: List[EvidenceObject] = []

        # 3. 研招网 S 级基准证据提取
        chsi_evidences = self.chsi.query_catalog(school_name, major_query, target_year=exam_year)
        all_evidences.extend(chsi_evidences)

        # 4. 高校官方站点 A 级证据抽取
        target_domains = []
        if entity:
            if entity.admission_domain:
                target_domains.append(("admission_office", entity.admission_domain))
            if entity.graduate_domain and entity.graduate_domain != entity.admission_domain:
                target_domains.append(("graduate_school", entity.graduate_domain))
            
            # 若匹配到了对应学院
            college_url = site_graph["domains"].get("college")
            if college_url:
                target_domains.append(("college_official", college_url))

        for src_type, url in target_domains[:2]:
            fetch_res = self.fetcher.fetch(url)
            if fetch_res.is_valid and fetch_res.content:
                extracted = self.extractor.extract_from_html(
                    html_text=fetch_res.content,
                    page_url=url,
                    school_name=school_name,
                    target_year=exam_year,
                    source_type=src_type
                )
                all_evidences.extend(extracted)

        # 5. 执行证据链整合与多源冲突仲裁
        resolved_evidences = resolve_conflicts(all_evidences)

        # 6. 生成实名社媒直达专题链接
        kw_part = f" {major_query}" if major_query else ""
        encoded_kw = urllib.parse.quote(f"{school_name}{kw_part} 考研")
        social_links = {
            "zhihu": f"https://www.zhihu.com/search?type=content&q={encoded_kw}%20%E5%B0%B1%E8%AF%BB%E4%BD%93%E9%AA%8C",
            "bilibili": f"https://search.bilibili.com/all?keyword={encoded_kw}%20%E5%A4%87%E8%80%83%E7%BB%8F%E9%AA%8C",
            "xiaohongshu": f"https://www.xiaohongshu.com/search_result?keyword={encoded_kw}%20%E9%81%BF%E5%9D%91"
        }

        # 7. 生成结构化 Markdown 研报与控制台渲染卡片
        markdown_content = self._render_markdown_report(
            school_name=school_name,
            entity=entity,
            major_query=major_query,
            exam_year=exam_year,
            site_graph=site_graph,
            evidences=resolved_evidences,
            social_links=social_links
        )

        saved_path = None
        if save_report:
            saved_path = self._save_report(school_name, major_query, markdown_content)

        return {
            "school": school_name,
            "major": major_query,
            "exam_year": exam_year,
            "entity": entity.to_dict() if entity else None,
            "site_graph": site_graph,
            "evidences": [e.to_dict() for e in resolved_evidences],
            "social_links": social_links,
            "markdown_report": markdown_content,
            "saved_path": str(saved_path) if saved_path else None
        }

    def _render_markdown_report(
        self,
        school_name: str,
        entity: Optional[UniversityEntity],
        major_query: Optional[str],
        exam_year: int,
        site_graph: Dict[str, Any],
        evidences: List[EvidenceObject],
        social_links: Dict[str, str]
    ) -> str:
        """生成专业级结构化考情证据研报"""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        level_str = site_graph.get("level", "全国统考研招单位")
        region_str = site_graph.get("region", "中国")
        chsi_code = site_graph.get("chsi_code", "待查")
        kw_part = f" {major_query}" if major_query else ""

        lines = [
            f"# 🎯 目标院校考研深度情报研报 · {school_name} {major_query or ''}",
            f"> 数据基准：教育部研招网 (S级) + 高校官方站点 (A级) ｜ 锁定年份：{exam_year} ｜ 提取时间：{now_str}",
            "",
            "## 📊 1. 目标院校核心招考画像 (官方注册实体)",
            f"- **高校名称**：`{school_name}` (院校代码: `{chsi_code}`)",
            f"- **办学层次**：`{level_str}`",
            f"- **所在地区**：`{region_str}`",
            f"- **目标方向**：`{major_query or '全科目录'}`",
            ""
        ]

        # 官方站点有向图
        lines.extend([
            "## 🏛️ 2. 官方权威站点有向图谱 (Evidence Tree)",
            f"1. **[{school_name} 本科/学校官网]({site_graph['domains'].get('official') or '#'})**",
            f"2. **[{school_name} 研究生院 / 招生办公室]({site_graph['domains'].get('admission_office') or site_graph['domains'].get('graduate_school') or '#'})**",
            f"3. **[【教育部直达】研招网 {school_name} 信息专页]({site_graph['chsi_portals'].get('school_info') or '#'})**",
            f"4. **[【目录检索】研招网硕士专业目录查询系统]({site_graph['chsi_portals'].get('zsml_catalog') or '#'})**"
        ])
        if site_graph["domains"].get("college"):
            col_name = site_graph["domains"].get("college_name", "二级学院官网")
            lines.append(f"5. **[{school_name} {col_name}]({site_graph['domains'].get('college')})**")
        lines.append("")

        # 核心招考证据链 (Evidence Chain)
        lines.extend([
            f"## 📋 3. 核心招考事实与证据链 (Verified Evidence Chain)",
            "> 遵循「搜索只负责发现，官方页面才构成证据」原则，严格附带信源、置信度与发布时间戳：",
            ""
        ])

        if evidences:
            for i, ev in enumerate(evidences, 1):
                val_repr = str(ev.value)
                if isinstance(ev.value, list):
                    val_repr = "、".join(str(x) for x in ev.value)
                elif isinstance(ev.value, dict):
                    val_repr = json.dumps(ev.value, ensure_ascii=False)

                status_icon = "✅" if ev.status == "VERIFIED" else ("⚠️" if ev.status == "CONFLICT" else "⏳")
                lines.append(f"### {status_icon} 证据项 #{i} · {ev.field}")
                lines.append(f"- **指标数值**：`{val_repr} {ev.unit}`".strip())
                lines.append(f"- **证据来源**：`[{ev.source.level}级权威] {ev.source.name}` ([官方直达]({ev.source.url}))")
                lines.append(f"- **考研年份**：`{ev.exam_year}年` ｜ **置信度**：`{int(ev.confidence * 100)}%` ｜ **核验状态**：`{ev.status}`")
                
                if ev.conflict_detail:
                    lines.append(f"> 💬 **仲裁与风险提示**：\n> {ev.conflict_detail.replace(chr(10), chr(10)+'> ')}")
                lines.append("")
        else:
            lines.append("*(暂未提取到针对特定专业的细分指标，请参考研招网官方目录直达入口)*\n")

        # 实名社媒直通车
        lines.extend([
            "## 💬 4. 实名社媒真实体验与避坑直通车",
            "> 点击直达对应高校学长学姐真实就读体验、实验室氛围与避坑帖子：",
            f"- 💡 **知乎深度讨论**：[{school_name}{kw_part} 考研就读体验与导师评价]({social_links['zhihu']})",
            f"- 📺 **B站高分复盘**：[{school_name}{kw_part} 备考经验贴与真题复盘视频]({social_links['bilibili']})",
            f"- 📕 **小红书避坑帖**：[{school_name}{kw_part} 考研避坑、压分与复试经验]({social_links['xiaohongshu']})",
            "",
            "---",
            f"> 💡 **KaoYan Intelligence 战略提示**：本研报基于权威官方站点生成。可结合自身模考水平，在会话中让 AI 私教为你出具针对 `{school_name}` 的定制备考处方。"
        ])

        return "\n".join(lines)

    def _save_report(self, school_name: str, major_query: Optional[str], content: str) -> Path:
        """保存研报到 04-专业课/"""
        target_dir = ROOT / "04-专业课"
        target_dir.mkdir(parents=True, exist_ok=True)
        
        safe_major = f"_{major_query.strip()}" if major_query else ""
        filename = f"目标院校情报_{school_name}{safe_major}.md"
        filepath = target_dir / filename
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
            
        return filepath


# 全局单例
_default_engine = None

def get_intelligence_engine() -> KaoYanIntelligenceEngine:
    global _default_engine
    if _default_engine is None:
        _default_engine = KaoYanIntelligenceEngine()
    return _default_engine
