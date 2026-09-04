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

        # 6. 启发式通用高校实体合成 (Universal Heuristic Entity Synthesizer)
        # 支持全国任意非热门双非院校、地方工科/师范/财经本科高校
        if any(keyword in q for keyword in ("大学", "学院", "学校", "研究院", "研究所", "中心")):
            synthetic_entity = self._synthesize_unlisted_school(q)
            if synthetic_entity:
                # 动态回填映射，加速后续查询
                self._entities[synthetic_entity.chsi_code] = synthetic_entity
                self._name_map[q_lower] = synthetic_entity.chsi_code
                return synthetic_entity

        return None

    def _synthesize_unlisted_school(self, school_name: str) -> UniversityEntity:
        """启发式推导未收录高校的区域、办学层次与教育部研招直达通道"""
        # 常见双非/地方高校教育部代码与域名索引
        KNOWN_REGIONAL = {
            "东莞理工学院": ("11845", "广东东莞", "http://yjs.dgut.edu.cn", "https://www.dgut.edu.cn"),
            "河南科技大学": ("10464", "河南洛阳", "https://yjsc.haust.edu.cn", "https://www.haust.edu.cn"),
            "河南理工大学": ("10460", "河南焦作", "http://admissions.hpu.edu.cn", "https://www.hpu.edu.cn"),
            "河南工业大学": ("10463", "河南郑州", "https://yjs.haut.edu.cn", "https://www.haut.edu.cn"),
            "桂林电子科技大学": ("10595", "广西桂林", "https://www.guet.edu.cn/yjsy", "https://www.guet.edu.cn"),
            "杭州电子科技大学": ("10336", "浙江杭州", "https://grs.hdu.edu.cn", "https://www.hdu.edu.cn"),
            "重庆邮电大学": ("10617", "重庆", "http://yjs.cqupt.edu.cn", "https://www.cqupt.edu.cn"),
            "西安邮电大学": ("10709", "陕西西安", "http://gr.xupt.edu.cn", "https://www.xupt.edu.cn"),
            "长春理工大学": ("10186", "吉林长春", "http://yzb.cust.edu.cn", "https://www.cust.edu.cn"),
            "齐鲁工业大学": ("10431", "山东济南", "https://yjs.qlu.edu.cn", "https://www.qlu.edu.cn"),
            "燕山大学": ("10216", "河北秦皇岛", "https://ysuyzb.ysu.edu.cn", "https://www.ysu.edu.cn"),
            "江苏大学": ("10299", "江苏镇江", "https://yz.ujs.edu.cn", "https://www.ujs.edu.cn"),
            "南京邮电大学": ("10293", "江苏南京", "http://yzb.njupt.edu.cn", "https://www.njupt.edu.cn"),
            "广东工业大学": ("11845", "广东广州", "http://yjs.gdut.edu.cn", "https://www.gdut.edu.cn"),
            "深圳大学": ("10590", "广东深圳", "https://yz.szu.edu.cn", "https://www.szu.edu.cn"),
            "浙江工业大学": ("10337", "浙江杭州", "http://www.yz.zjut.edu.cn", "https://www.zjut.edu.cn"),
            "太原科技大学": ("10109", "山西太原", "https://yjs.tyust.edu.cn", "https://www.tyust.edu.cn"),
            "中北大学": ("10110", "山西太原", "http://grs.nuc.edu.cn", "https://www.nuc.edu.cn"),
            "常州大学": ("10292", "江苏常州", "http://yzb.cczu.edu.cn", "https://www.cczu.edu.cn"),
            "青岛科技大学": ("10426", "山东青岛", "https://grad.qust.edu.cn", "https://www.qust.edu.cn"),
            "三峡大学": ("11075", "湖北宜昌", "http://sxdxpos.ctgu.edu.cn", "https://www.ctgu.edu.cn"),
            "长江大学": ("10489", "湖北荆州", "http://gs.yangtzeu.edu.cn", "https://www.yangtzeu.edu.cn")
        }

        chsi_code = "待查"
        region = "全国"
        grad_domain = ""
        official_domain = ""

        # 1. 命中知名双非对照表
        if school_name in KNOWN_REGIONAL:
            code, reg, grad, off = KNOWN_REGIONAL[school_name]
            chsi_code = code
            region = reg
            grad_domain = grad
            official_domain = off
        else:
            # 2. 地理区域启发式提取
            CITY_MAP = {
                "东莞": "广东东莞", "洛阳": "河南洛阳", "焦作": "河南焦作", "开封": "河南开封", "新乡": "河南新乡",
                "保定": "河北保定", "秦皇岛": "河北秦皇岛", "徐州": "江苏徐州", "苏州": "江苏苏州", "无锡": "江苏无锡",
                "常州": "江苏常州", "南通": "江苏南通", "镇江": "江苏镇江", "温州": "浙江温州", "宁波": "浙江宁波",
                "杭州": "浙江杭州", "芜湖": "安徽芜湖", "蚌埠": "安徽蚌埠", "赣州": "江西赣州", "九江": "江西九江",
                "绵阳": "四川绵阳", "泸州": "四川泸州", "湘潭": "湖南湘潭", "株洲": "湖南株洲", "衡阳": "湖南衡阳",
                "宜昌": "湖北宜昌", "荆州": "湖北荆州", "襄阳": "湖北襄阳", "大连": "辽宁大连", "鞍山": "辽宁鞍山",
                "吉林": "吉林", "齐齐哈尔": "黑龙江齐齐哈尔", "桂林": "广西桂林", "柳州": "广西柳州",
                "延安": "陕西延安", "汉中": "陕西汉中", "青岛": "山东青岛", "烟台": "山东烟台", "威海": "山东威海"
            }
            for city_key, reg_val in CITY_MAP.items():
                if city_key in school_name:
                    region = reg_val
                    break

            if region == "全国":
                PROVINCES = [
                    "北京", "天津", "上海", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江", "江苏", "浙江",
                    "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南", "广东", "海南", "四川", "贵州",
                    "云南", "陕西", "甘肃", "青海", "台湾", "内蒙古", "广西", "西藏", "宁夏", "新疆"
                ]
                for prov in PROVINCES:
                    if school_name.startswith(prov):
                        region = prov
                        break

            # 官方通道降级：直达研招网该校专页
            encoded = urllib.parse.quote(school_name)
            official_domain = f"https://yz.chsi.com.cn/sch/search.do?ssdm=&yjsy=&xxmc={encoded}"
            grad_domain = official_domain

        # 3. 判定国家线分区 (A区 vs B区)
        B_ZONE_PROVINCES = ("内蒙古", "广西", "海南", "贵州", "云南", "西藏", "甘肃", "青海", "宁夏", "新疆")
        is_b_zone = any(bp in region for bp in B_ZONE_PROVINCES)
        zone_label = "国家二区线高校 (B区，享受降分照顾)" if is_b_zone else "国家一区线高校 (A区)"

        levels = ["省属重点公办高校", "硕士研究生培养单位", zone_label]

        return UniversityEntity(
            chsi_code=chsi_code,
            name=school_name,
            aliases=[school_name],
            level=levels,
            region=region,
            official_domain=official_domain,
            graduate_domain=grad_domain,
            admission_domain=grad_domain,
            departments={}
        )

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
