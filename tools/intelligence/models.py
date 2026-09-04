# -*- coding: utf-8 -*-
"""
KaoYan Intelligence · 核心数据模型 (Data Models)

定义 EvidenceObject、UniversityEntity、QueryIntent 等标准数据结构。
"""

from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional
from datetime import datetime


@dataclass
class EvidenceSource:
    """证据来源元数据"""
    level: str             # "S", "A", "B", "C", "D"
    type: str              # "chsi", "graduate_school", "college_official", "official_wechat", "education_platform", "social_media", "forum"
    name: str              # 来源中文名，如 "中国研究生招生信息网", "华中科技大学研究生招生网"
    url: str               # 来源直接 URL
    published_at: Optional[str] = None  # 发布日期 YYYY-MM-DD 或 YYYY

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceObject:
    """
    统一证据对象 (Evidence Object)
    保证所有招考字段必须包含来源、时间、可信度、年份及状态。
    """
    field: str                        # 字段名，如 "招生人数", "初试科目", "复试分数线", "一志愿保护", "招生简章"
    value: Any                        # 字段值 (int / str / list / dict)
    unit: str                         # 单位，如 "人", "分", "篇"
    exam_year: int                    # 对应的考研年份，如 2026, 2027
    source: EvidenceSource            # 来源
    retrieved_at: str                 # 抓取/提取时间戳
    confidence: float                 # 置信度 (0.0 ~ 1.0)
    status: str = "VERIFIED"          # "VERIFIED", "CONFLICT", "OUTDATED", "UNVERIFIED"
    conflict_detail: Optional[str] = None # 若存在冲突，记录冲突说明与对比

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if isinstance(self.source, EvidenceSource):
            d["source"] = self.source.to_dict()
        return d


@dataclass
class UniversityEntity:
    """高校官方注册信息实体"""
    chsi_code: str                    # 教育部 5 位院校代码，如 "10487"
    name: str                         # 官方全称，如 "华中科技大学"
    aliases: List[str]                # 常见别名/简称，如 ["华科", "华科大", "HUST"]
    level: List[str]                  # 办学层次标签，如 ["985", "211", "双一流A类", "自划线"]
    region: str                       # 所在省市，如 "湖北武汉"
    official_domain: str              # 学校官网
    graduate_domain: str              # 研究生院/招生网
    admission_domain: str             # 研招办/硕士招生入口
    departments: Dict[str, Any] = field(default_factory=dict) # 核心学科与院系映射

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class QueryIntent:
    """用户招考查询意图解析结果"""
    raw_query: str
    school: str                       # 识别出的院校名或别名
    major_code: Optional[str] = None  # 专业代码，如 "085404"
    major_name: Optional[str] = None  # 专业方向，如 "计算机技术"
    exam_year: int = 2027             # 目标考研年份，默认当下筹备年份
    fields: List[str] = field(default_factory=lambda: ["招生人数", "初试科目", "复试线", "简章"])

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
