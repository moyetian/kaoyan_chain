# -*- coding: utf-8 -*-
"""
KaoYan Intelligence · 高校实体解析器与有向站点图谱 (University Resolver & Site Graph)

职责：
  1. 加载并管理 data/universities/registry.json
  2. 模糊匹配高校别名、简称（如“华科”、“南医大”、“成电”、“HUST”）
  3. 构建高校有向站点树（官网 → 研究生院 → 招生网 → 二级学院）
"""

import os
import json
import urllib.parse
from pathlib import Path
from typing import Dict, Any, List, Optional
from .models import UniversityEntity

REGISTRY_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "universities" / "registry.json"


class UniversityRegistry:
    """高校注册表管理器"""

    def __init__(self, registry_file: Optional[Path] = None):
        self.file_path = registry_file or REGISTRY_PATH
        self._entities: Dict[str, UniversityEntity] = {}
        self._alias_map: Dict[str, str] = {} # alias_lower -> chsi_code
        self._name_map: Dict[str, str] = {}  # name_lower -> chsi_code
        self.load()

    def load(self) -> None:
        """加载高校注册表"""
        if not self.file_path.exists():
            return
        
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
        except Exception as e:
            raw_data = {}

        self._entities.clear()
        self._alias_map.clear()
        self._name_map.clear()

        for code, item in raw_data.items():
            entity = UniversityEntity(
                chsi_code=str(item.get("chsi_code", code)),
                name=item.get("name", ""),
                aliases=item.get("aliases", []),
                level=item.get("level", []),
                region=item.get("region", ""),
                official_domain=item.get("official_domain", ""),
                graduate_domain=item.get("graduate_domain", ""),
                admission_domain=item.get("admission_domain", ""),
                departments=item.get("departments", {})
            )
            self._entities[entity.chsi_code] = entity
            self._name_map[entity.name.lower()] = entity.chsi_code

            # 注册别名
            for alias in entity.aliases:
                self._alias_map[alias.lower().strip()] = entity.chsi_code

    def count(self) -> int:
        """高校总数"""
        return len(self._entities)

    def resolve(self, query: str) -> Optional[UniversityEntity]:
        """
        根据用户输入的自然语言、高校全称、拼音缩写或代码精准解析对应高校实体
        """
        if not query or not query.strip():
            return None

        q = query.strip()
        q_lower = q.lower()

        # 1. 直接代码匹配 (如 "10487")
        if q in self._entities:
            return self._entities[q]

        # 2. 精确名称匹配 (如 "华中科技大学")
        if q_lower in self._name_map:
            return self._entities[self._name_map[q_lower]]

        # 3. 精确别名匹配 (如 "华科", "hust", "南医大")
        if q_lower in self._alias_map:
            return self._entities[self._alias_map[q_lower]]

        # 4. 前缀或包含匹配 (如 "华中科技" 包含在 "华中科技大学" 中)
        for name, code in self._name_map.items():
            if q_lower in name or name in q_lower:
                return self._entities[code]

        # 5. 别名模糊匹配
        for alias, code in self._alias_map.items():
            if len(alias) >= 2 and (alias in q_lower or q_lower in alias):
                return self._entities[code]

        return None

    def build_site_graph(
        self,
        entity: UniversityEntity,
        discipline_keyword: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        构建高校专属有向站点树与官方通道
        """
        college_info = None
        if discipline_keyword and entity.departments:
            for dept_key, dept_val in entity.departments.items():
                if discipline_keyword in dept_key or dept_key in discipline_keyword:
                    college_info = dept_val
                    break

        encoded_name = urllib.parse.quote(entity.name)
        
        return {
            "university": entity.name,
            "chsi_code": entity.chsi_code,
            "level": " / ".join(entity.level),
            "region": entity.region,
            "domains": {
                "official": entity.official_domain,
                "graduate_school": entity.graduate_domain,
                "admission_office": entity.admission_domain,
                "college": college_info.get("college_domain") if college_info else None,
                "college_name": college_info.get("college_name") if college_info else None,
                "default_majors": college_info.get("default_majors", []) if college_info else []
            },
            "chsi_portals": {
                "school_info": f"https://yz.chsi.com.cn/sch/search.do?ssdm=&yjsy=&xxmc={encoded_name}",
                "zsml_catalog": "https://yz.chsi.com.cn/zsml/queryAction.do",
                "admission_disclosure": "https://yz.chsi.com.cn/gkml/"
            },
            "site_tree": [
                {"name": f"{entity.name} 官网", "url": entity.official_domain, "type": "official"},
                {"name": f"{entity.name} 研究生院", "url": entity.graduate_domain, "type": "graduate_school"},
                {"name": f"{entity.name} 硕士招生办公室", "url": entity.admission_domain, "type": "admission_office"},
                *(
                    [{"name": college_info.get("college_name", "二级学院"), "url": college_info.get("college_domain"), "type": "college"}]
                    if college_info and college_info.get("college_domain") else []
                )
            ]
        }

    def list_all(self) -> List[UniversityEntity]:
        """返回所有已注册高校列表"""
        return list(self._entities.values())


# 全局单例
_default_registry = None

def get_registry() -> UniversityRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = UniversityRegistry()
    return _default_registry

def resolve_university(query: str) -> Optional[UniversityEntity]:
    return get_registry().resolve(query)
