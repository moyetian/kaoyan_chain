# -*- coding: utf-8 -*-
"""
考研学习链 (Kaoyan AI Study Chain) · 招考情报与证据链引擎 (KaoYan Intelligence)

核心原则：
  1. Search = Discovery, Official Page = Evidence (搜索负责发现，官方页面才构成证据)
  2. LLM as Reasoner, not Database (大模型不作招考数据库，只做事实之上的推理分析)
  3. Ground Truth First (研招网 S 级基准，研究生院 A 级权威，社媒 C 级辅助)
  4. Progressive Enhancement (纯 Python 标准库零重度依赖开箱即用，动态浏览器作为可选插件)
"""

__version__ = "1.0.0"

from .models import (
    EvidenceSource,
    EvidenceObject,
    UniversityEntity,
    QueryIntent
)

from .registry import (
    UniversityRegistry,
    get_registry,
    resolve_university
)

from .evidence_engine import (
    build_evidence,
    resolve_conflicts,
    get_source_score,
    get_source_level
)

from .chsi_connector import CHSIConnector
from .fetcher import HTTPFetcher, BrowserPluginManager, FetchResult
from .discovery import OfficialDiscovery
from .extractor import DocumentExtractor
from .scout_engine import KaoYanIntelligenceEngine, get_intelligence_engine
from .watcher import AdmissionWatcher

__all__ = [
    "EvidenceSource",
    "EvidenceObject",
    "UniversityEntity",
    "QueryIntent",
    "UniversityRegistry",
    "get_registry",
    "resolve_university",
    "build_evidence",
    "resolve_conflicts",
    "get_source_score",
    "get_source_level",
    "CHSIConnector",
    "HTTPFetcher",
    "BrowserPluginManager",
    "FetchResult",
    "OfficialDiscovery",
    "DocumentExtractor",
    "KaoYanIntelligenceEngine",
    "get_intelligence_engine",
    "AdmissionWatcher"
]
