# -*- coding: utf-8 -*-
"""
KaoYan Intelligence · 证据链与多源冲突仲裁引擎 (Evidence Engine)

职责：
  1. 信源可信度评分与分级 (S/A/B/C/D)
  2. 年份锁定 (Exam Year Locking)：严防把往年旧数据冒充为当年真实招考数据
  3. 字段级多源冲突检测与仲裁 (Field Conflict Resolution)
  4. 统一生成标准合规的 EvidenceObject
"""

import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from .models import EvidenceSource, EvidenceObject, current_exam_year
from .citation_engine import verify_citation_excerpt

_LOG = logging.getLogger(__name__)

# 信源基础可信度打分表 (0~100)
SOURCE_SCORES = {
    "chsi": 100,               # S 级：中国研招网 / 教育部全国研究生招生信息平台
    "graduate_school": 95,     # A 级：高校研究生院 / 研招办官方网站
    # [P1 修复·2026-10-08] 补登记 scout_engine 实际会传入的两种信源类型。
    # 此前未登记 → 走 50 分/D 级兜底，招生办官方证据被静默降级，产出
    # 「VERIFIED + D级 + 0.5 置信」的自相矛盾证据（R5 实测）。
    "admission_office": 95,    # A 级：高校硕士招生办公室官方域（与研究生院同级）
    "official_discovered": 90, # A 级：站内检索发现的官方域招生页面（域名已校验）
    "college_official": 90,    # A 级：二级学院官方招生网/通知公告
    "official_wechat": 85,     # B 级：学校/研究生院官方认证微信公众号
    "education_platform": 60,  # C 级：中国教育在线、研招网合作平台
    "social_media": 30,        # C 级：知乎 / B 站 / 小红书实名经验与就读体验
    "forum": 10,               # D 级：考研论坛、非实名贴吧、个人博客
    # 联网失败时的离线兜底：全国统考科目标准模板。
    # 它不是任何一所院校的官方核实数据（院校可能改自命题），故绝不可标为 S 级。
    "offline_baseline": 40,
}

SOURCE_LEVELS = {
    "chsi": "S",
    "graduate_school": "A",
    "admission_office": "A",
    "official_discovered": "A",
    "college_official": "A",
    "official_wechat": "B",
    "education_platform": "C",
    "social_media": "C",
    "forum": "D",
    "offline_baseline": "C",
}

#: 已警告过的未登记信源类型（每种 type 只警告一次，避免逐条证据刷屏日志）。
_WARNED_UNKNOWN_SOURCE_TYPES: set = set()


def _warn_unknown_source_type(source_type: str) -> None:
    """未登记信源类型的 WARNING（每种 type 仅一次）。

    [P1 修复·2026-10-08] 此前兜底完全静默：未登记 type 得 50 分/D 级，
    证据以「VERIFIED + D级」的矛盾状态流出且无人察觉（admission_office /
    official_discovered 实测）。兜底行为保持不变（fail-safe 仍按低可信
    处理），但打 WARNING 让静默降级可被发现、可补登记。
    """
    key = str(source_type or "").strip().lower()
    if key in _WARNED_UNKNOWN_SOURCE_TYPES:
        return
    _WARNED_UNKNOWN_SOURCE_TYPES.add(key)
    _LOG.warning(
        "未登记的信源类型 %r：按兜底 50 分/D 级处理；请在 evidence_engine."
        "SOURCE_SCORES / SOURCE_LEVELS 中补登记，避免证据静默降级。",
        source_type,
    )


def get_source_score(source_type: str) -> int:
    """获取信源基准得分"""
    key = str(source_type or "").lower()
    if key not in SOURCE_SCORES:
        _warn_unknown_source_type(source_type)
    return SOURCE_SCORES.get(key, 50)


def get_source_level(source_type: str) -> str:
    """获取信源级别"""
    key = str(source_type or "").lower()
    if key not in SOURCE_LEVELS:
        _warn_unknown_source_type(source_type)
    return SOURCE_LEVELS.get(key, "D")


def build_evidence(
    field_name: str,
    value: Any,
    unit: str,
    exam_year: int,
    source_type: str,
    source_name: str,
    source_url: str,
    published_at: Optional[str] = None,
    target_year: Optional[int] = None,
    extra_confidence_decay: float = 0.0,
    ssl_verified: bool = True,
    fetched_at: Optional[str] = None,
    extractor_version: str = "v2.6",
    quote: Optional[str] = None,
    source_text: Any = None,
) -> EvidenceObject:
    """
    构建标准化证据对象并自动计算置信度与状态

    Args:
        quote: 该条事实在来源中的**原文片段**（引文）。与 ``source_text`` 同时提供时，
            会启用「引文溯源闸门」：引文必须在来源原文中逐字命中，否则本条证据强制
            降级为 UNVERIFIED。这是本项目反幻觉原则的技术兜底。
        source_text: 来源原文；可为单个字符串或字符串序列（任一命中即通过），
            例如 ``[原始 HTML, html.unescape(HTML)]``。
    """
    target_year = target_year or current_exam_year()
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

    # 离线兜底数据：未经联网核验，既不能冒充权威信源，也不能标为已验证
    if str(source_type).lower() == "offline_baseline":
        status = "UNVERIFIED"
        conflict_detail = (
            "⚠️ 离线基准：研招网/官网当期页面未能成功抓取，本条为「全国统考科目标准模板」兜底推定值，"
            "并非该校官方核实数据（院校可能改为自命题），务必以官方简章为准。"
        )

    # [P0 修复] SSL 未通过完整权威证书链校验 -> 禁止标为 VERIFIED，级别强制降为 D
    if not ssl_verified:
        status = "UNVERIFIED"
        level = "D"
        source.level = "D"
        confidence = min(confidence, 0.3)
        conflict_detail = "⚠️ 该来源 SSL 证书链校验失败，内容可能被中间人篡改，请勿据此决策"

    # [反幻觉闸门] 引文溯源：提供了引文与来源原文时，引文必须逐字命中。
    # 这是最强的一道校验（前两道只判断"来源是否权威"，这一道判断"内容是否真的出自该来源"），
    # 故放在最后执行，一旦失败即覆盖前面的结论。缺失引文不做判定（保持向后兼容）。
    if quote is not None or source_text is not None:
        grounded, why = verify_citation_excerpt(quote, source_text)
        if not grounded:
            status = "UNVERIFIED"
            level = "D"
            source.level = "D"
            confidence = min(confidence, 0.3)
            conflict_detail = (
                f"⚠️ 引文未溯源：{why}。"
                "本条数据无法在其声称的来源原文中定位，已强制降级，禁止作为报考决策依据。"
            )

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
        conflict_detail=conflict_detail,
        ssl_verified=ssl_verified,
        fetched_at=fetched_at or retrieved_at,
        extractor_version=extractor_version
    )


# ── [P1 修复·2026-10-08 R10 值归一] 冲突判等专用的保守值归一 ──
# 实测差异：extractor 产出保留原文形态（如名称内全角括号「(204)英语（二）」），
# chsi 离线标准模板为「(204)英语(二)」——语义相同、文本不等；field 名归一（R10
# 前半）让两者进同一分组后，旧实现按原始文本比较即判 CONFLICT，产生噪音冲突。
#
# 归一范围刻意收窄为「零歧义兼容字符」：
#   * 全角 ASCII 区 U+FF01–U+FF5E → 半角 U+0021–U+007E（Unicode 兼容区，
#     与 ASCII 一一对应，转换语义零歧义）；
#   * 全角空格 U+3000 → 半角空格。
# 不用 NFKC：它还会做兼容分解（Ⅱ→II、①→1、²→2、㈠→(一)、半角片假名展开等），
# 会把「形态不同但可能想区分」的值也一并合并，误伤面不可控；本场景实测差异
# 全部落在全角 ASCII 区内，手工映射已足够且行为可预测（宁少合并、不错合并）。
# 不做连续空白折叠（「计算机 408」vs「计算机408」有假同风险），仅 strip 首尾。
_FULLWIDTH_ASCII_MAP = {cp: cp - 0xFEE0 for cp in range(0xFF01, 0xFF5F)}
_FULLWIDTH_ASCII_MAP[0x3000] = 0x20


def _normalize_evidence_value(value: Any) -> Any:
    """证据值的保守归一，**仅用于冲突判等**，绝不写回/输出（输出保留原值）。

    - 字符串：全角 ASCII → 半角、全角空格 → 半角空格、strip 首尾空白；
    - list：递归归一每个元素（「初试科目」证据的 value 是科目清单 list of str，
      真实差异就落在清单元素的名称括号上）；
    - 其他（int/dict/None/…）：原样返回，判等路径与行为保持不变。
    """
    if isinstance(value, str):
        return value.translate(_FULLWIDTH_ASCII_MAP).strip()
    if isinstance(value, list):
        return [_normalize_evidence_value(item) for item in value]
    return value


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
        # [P1 修复·2026-10-08 R10 值归一] 判等键用 _normalize_evidence_value
        # 做保守归一（全角 ASCII→半角、strip），消除全角/半角同义值的噪音
        # CONFLICT；归一仅作用于判等键，ev.value 原值不做任何改写，输出/渲染
        # 仍是原形态（合并时保留 best_ev 自身携带的原值）。
        unique_values = set()
        for ev in ev_list:
            if isinstance(ev.value, list):
                # [P0-4 修复·list-of-dict 崩溃] 此前 tuple(ev.value) 直接入 set：
                # 元素是 dict 时（PDF 附件证据 value=[{"name":…, "url":…}]，
                # extractor._extract_pdf_links 的产出）→ TypeError: unhashable
                # type: 'dict' → 整条 scout 证据链挂（官方简章页挂 PDF 附件是常态，
                # 同一次 scout 两个页面都含 PDF 即触发）。
                # 改 json 序列化后比较：可哈希、键序稳定（sort_keys 防同内容
                # 不同键序误判冲突）、非 JSON 原生类型兜底（default=str）。
                # 序列化前先对 list 内字符串元素归一（嵌套 dict 元素保持原样）。
                try:
                    unique_values.add(json.dumps(
                        _normalize_evidence_value(ev.value), sort_keys=True,
                        ensure_ascii=False, default=str))
                except Exception:
                    unique_values.add(_normalize_evidence_value(str(ev.value)))
            elif isinstance(ev.value, dict):
                unique_values.add(str(ev.value))
            else:
                unique_values.add(_normalize_evidence_value(str(ev.value)))

        if len(unique_values) == 1:
            # 数据一致，按来源优先级保留最高置信度的证据，并提升置信度（多源佐证）
            best_ev = max(ev_list, key=lambda x: x.confidence)
            best_ev.confidence = min(1.0, round(best_ev.confidence + 0.03, 2))
            resolved.append(best_ev)
        else:
            # [R11 修复·同源多行假冲突] 冲突仲裁的前提是「多源」：同一来源
            # （type+url 相同）在同一字段下产出的多条并列条目互不相等，但**不是**
            # 「多源官方冲突」——研招网目录每个专业行一条证据（chsi_connector.
            # _parse_catalog_html，value 为不同 dict），实测 ky scout / ky
            # admission 只要目录 ≥2 行（常态）就全部标 CONFLICT + 假裁决文案。
            # 同源异值 → 原样保留全部条目，不做冲突裁决、不生成裁决说明。
            # 注：判定放在值判等**之后**：同源同值仍走上面的合并去重（既有行为，
            # 由 test_int_same_value_merges 等钉住），此处只拦截假冲突。
            # [N6 修复·url None/"" 归一] 集合未归一 url 时 None 与 "" 被凑成两个
            # 来源 → 同源假冲突残留（部分连接器缺 URL 时 url=None、另一条 ""）。
            sources = {(ev.source.type, ev.source.url or "") for ev in ev_list}
            if len(sources) == 1:
                resolved.extend(ev_list)
                continue

            # 存在数据冲突！
            detail_lines = [f"⚠️ 字段【{field_name}】({year}年) 存在多源官方冲突："]
            for ev in ev_list:
                unit_str = f" {ev.unit}" if getattr(ev, "unit", "") else ""
                val_str = f"{ev.value}{unit_str}"
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
