# -*- coding: utf-8 -*-
"""
KaoYan Intelligence · 证据链与多源冲突仲裁引擎 (Evidence Engine)

职责：
  1. 信源可信度评分与分级 (S/A/B/C/D)
  2. 年份锁定 (Exam Year Locking)：严防把往年旧数据冒充为当年真实招考数据
  3. 字段级多源冲突检测与仲裁 (Field Conflict Resolution)
  4. 统一生成标准合规的 EvidenceObject
"""

from typing import List, Dict, Any, Optional
from datetime import datetime
from .models import EvidenceSource, EvidenceObject

# 信源基础可信度打分表 (0~100)
SOURCE_SCORES = {
    "chsi": 100,               # S 级：中国研招网 / 教育部全国研究生招生信息平台
    "graduate_school": 95,     # A 级：高校研究生院 / 研招办官方网站
    "college_official": 90,    # A 级：二级学院官方招生网/通知公告
    "official_wechat": 85,     # B 级：学校/研究生院官方认证微信公众号
    "education_platform": 60,  # C 级：中国教育在线、研招网合作平台
    "social_media": 30,        # C 级：知乎 / B 站 / 小红书实名经验与就读体验
    "forum": 10,               # D 级：考研论坛、非实名贴吧、个人博客
}

SOURCE_LEVELS = {
    "chsi": "S",
    "graduate_school": "A",
    "college_official": "A",
    "official_wechat": "B",
    "education_platform": "C",
    "social_media": "C",
    "forum": "D"
}


def get_source_score(source_type: str) -> int:
    """获取信源基准得分"""
    return SOURCE_SCORES.get(source_type.lower(), 50)


def get_source_level(source_type: str) -> str:
    """获取信源级别"""
    return SOURCE_LEVELS.get(source_type.lower(), "D")


def build_evidence(
    field_name: str,
    value: Any,
    unit: str,
    exam_year: int,
    source_type: str,
    source_name: str,
    source_url: str,
    published_at: Optional[str] = None,
    target_year: int = 2027,
    extra_confidence_decay: float = 0.0
) -> EvidenceObject:
    """
    构建标准化证据对象并自动计算置信度与状态
    """
    base_score = get_source_score(source_type)
    level = get_source_level(source_type)
    
    # 归一化为 0.0 ~ 1.0 的 confidence
    confidence = max(0.1, min(1.0, (base_score / 100.0) - extra_confidence_decay))
    
    source = EvidenceSource(
        level=level,
        type=source_type,
        name=source_name,
        url=source_url,
        published_at=published_at
    )
    
    # 年份锁定判定
    status = "VERIFIED"
    conflict_detail = None
    
    if exam_year < target_year:
        status = "OUTDATED"
        conflict_detail = (
            f"⚠️ 年份预警：目标锁定 {target_year} 年，本条数据为 {exam_year} 年往期历史基准，"
            f"新一届官方数据尚未正式发布，仅供参考对比。"
        )
        # 往年数据置信度适当衰减 10%
        confidence = round(max(0.1, confidence * 0.9), 2)
    elif exam_year > target_year + 1:
        status = "UNVERIFIED"
        conflict_detail = f"⚠️ 异常数据：年份 {exam_year} 超前异常，建议核验。"
        
    retrieved_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    
    return EvidenceObject(
        field=field_name,
        value=value,
        unit=unit,
        exam_year=exam_year,
        source=source,
        retrieved_at=retrieved_at,
        confidence=round(confidence, 2),
        status=status,
        conflict_detail=conflict_detail
    )


def resolve_conflicts(evidences: List[EvidenceObject]) -> List[EvidenceObject]:
    """
    多源冲突仲裁器 (Conflict Resolver)
    对同一字段、同一年份的多条不同来源证据进行比对与裁决。
    例如：
      研招网 (S级) 统考人数 = 60
      学院官网 (A级) 统考人数 = 58
    发生冲突时，不抹杀任意一方，而是将状态标记为 CONFLICT，并自动生成结构化仲裁说明。
    """
    if not evidences:
        return []

    # 按 (field, exam_year) 分组
    grouped: Dict[tuple, List[EvidenceObject]] = {}
    for ev in evidences:
        key = (ev.field, ev.exam_year)
        grouped.setdefault(key, []).append(ev)

    resolved: List[EvidenceObject] = []

    for (field_name, year), ev_list in grouped.items():
        if len(ev_list) == 1:
            resolved.append(ev_list[0])
            continue

        # 检查值是否一致
        unique_values = set()
        for ev in ev_list:
            if isinstance(ev.value, list):
                unique_values.add(tuple(ev.value))
            elif isinstance(ev.value, dict):
                unique_values.add(str(ev.value))
            else:
                unique_values.add(str(ev.value))

        if len(unique_values) == 1:
            # 数据一致，按来源优先级保留最高置信度的证据，并提升置信度（多源佐证）
            best_ev = max(ev_list, key=lambda x: x.confidence)
            best_ev.confidence = min(1.0, round(best_ev.confidence + 0.03, 2))
            resolved.append(best_ev)
        else:
            # 存在数据冲突！
            detail_lines = [f"⚠️ 字段【{field_name}】({year}年) 存在多源官方冲突："]
            for ev in ev_list:
                val_str = f"{ev.value} {ev.unit}".strip()
                pub = f" (发布于 {ev.source.published_at})" if ev.source.published_at else ""
                detail_lines.append(
                    f"  • [{ev.source.level}级] {ev.source.name}: {val_str}{pub} (置信度 {int(ev.confidence*100)}%)"
                )
            
            # 判断优先级：若有学院最新补充通知 vs 研招网
            has_college = any(ev.source.type == "college_official" for ev in ev_list)
            has_chsi = any(ev.source.type == "chsi" for ev in ev_list)
            if has_college and has_chsi:
                detail_lines.append("  💡 裁决建议：研招网数据多为教育部统一上报预案，二级学院公告通常为推免锁定后的实招修正，建议以学院最新正式通知为准。")
            else:
                detail_lines.append("  💡 裁决建议：来源存在出入，请以高校研究生招生办公室最新公示为准。")

            conflict_summary = "\n".join(detail_lines)

            for ev in ev_list:
                ev.status = "CONFLICT"
                ev.conflict_detail = conflict_summary
                resolved.append(ev)

    return resolved
