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

import json
import logging
import time
import urllib.parse
from pathlib import Path

try:  # 双导入路径兼容（源码脚本式 / tools 包式）
    from workspace import resolve_workspace_root
except ImportError:  # pragma: no cover
    from tools.workspace import resolve_workspace_root
from typing import Dict, Any, List, Optional
from datetime import datetime

from .models import UniversityEntity, EvidenceObject, current_exam_year
from .registry import get_registry, resolve_university
from .chsi_connector import CHSIConnector
from .fetcher import HTTPFetcher
from .discovery import OfficialDiscovery
from .extractor import DocumentExtractor
from .evidence_engine import resolve_conflicts

# [G5 修复·except 内 import] 顶部统一双路径导入 ky_io：PermissionDeniedError
# 供只读模式兜底、atomic_write_text 供研报落盘（原两处分别写在 except 体与
# _save_report 函数体内，属同一类「import 藏在执行路径里」的缺陷）。
try:
    from ky_io import atomic_write_text, PermissionDeniedError  # noqa: E402
except ImportError:  # pragma: no cover
    from tools.ky_io import atomic_write_text, PermissionDeniedError  # noqa: E402

ROOT = resolve_workspace_root(__file__)

#: [P2 修复·2026-10-08] ``query`` 的总墙钟预算（秒，单一真源）。
#: 此前 query 无任何总时长预算：未收录校名走在线研究（内层自身 240s 默认）
#: + 研招网基准抓取 + 官方站点抓取（每页最多 3 次尝试）+ 站内检索发现
#: （最多 2 域名 × 2 查询 = 4 次联邦检索，每次多源）—— 弱网下总墙钟无界，
#: CLI/REPL 直调可无限等待（agent 工具路径仅靠外层 daemon join 兜底，
#: 后台线程本身仍在跑）。现与 comparator R3 同款口径：该预算是唯一真源，
#: 剩余墙钟派生给内层在线研究（见 ``query``）；预算耗尽后跳过后续在线
#: 补充并如实降级（已获取证据照常渲染）。``budget_s<=0`` 表示不熔断。
_DEFAULT_SCOUT_BUDGET = 300.0


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
        exam_year: Optional[int] = None,
        save_report: bool = False,
        budget_s: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        全流程执行高校招考情报检索与证据链聚合

        :param budget_s: [P2 修复·2026-10-08] 本次侦察的总墙钟预算（秒）。
            None 用默认 ``_DEFAULT_SCOUT_BUDGET``；<=0 不熔断（全量等待）。
            该预算是唯一真源：内层在线研究收到「剩余墙钟」作为自身预算
            （对照 comparator R3 的单源传递）；预算耗尽后跳过后续在线补充
            （研招网抓取/官方站点抓取/站内检索发现），保留已获取证据渲染报告。
        """
        exam_year = exam_year or current_exam_year()
        budget = _DEFAULT_SCOUT_BUDGET if budget_s is None else float(budget_s)
        deadline = (time.monotonic() + budget) if budget > 0 else None
        # 1. 解析目标高校实体
        entity = resolve_university(school_query)
        school_name = entity.name if entity else school_query
        
        # 2. 构建有向站点图
        if entity:
            site_graph = self.registry.build_site_graph(entity, major_query)
        else:
            from tools.intelligence.agentic_research import research_university_profile
            # [P2 修复·2026-10-08] 剩余墙钟单一真源传给内层在线研究（对照
            # comparator R3 同款口径）：旧实现内层恒用自身 240s 默认预算，
            # 与本次侦察的总预算完全脱钩。不熔断（deadline=None）时不传参，
            # 内层沿用自身默认预算（保持旧行为，避免第二处硬编码 240）。
            if deadline is None:
                prof = research_university_profile(school_name, major_query or "")
            else:
                prof = research_university_profile(
                    school_name, major_query or "",
                    budget_s=max(0.0, deadline - time.monotonic()))
            site_graph = {
                "university": school_name,
                "chsi_code": prof.get("code") or prof.get("chsi_code") or f"UNLISTED_{school_name}",
                "level": prof.get("level") or "全国研招单位",
                "region": prof.get("region") or "全国",
                "domains": {
                    "official": prof.get("official", ""),
                    "graduate_school": prof.get("graduate", ""),
                    "admission_office": prof.get("graduate", "")
                },
                "chsi_portals": {"zsml_catalog": "https://yz.chsi.com.cn/zsml/queryAction.do"},
                "site_tree": []
            }

        all_evidences: List[EvidenceObject] = []

        # 3. 研招网 S 级基准证据提取
        # [P2 修复·2026-10-08] 各在线阶段前检查总预算：耗尽即跳过并如实降级
        # （报告仍渲染已获取证据，不丢已得结果）。
        chsi_evidences: List[EvidenceObject] = []
        if deadline is None or time.monotonic() <= deadline:
            chsi_evidences = self.chsi.query_catalog(school_name, major_query, target_year=exam_year)
        else:
            logging.getLogger(__name__).info("侦察总预算耗尽，跳过研招网基准抓取")
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
            # [P2 修复·2026-10-08] 单页抓取最多 3 次尝试（≈3×timeout），逐页
            # 前检查总预算，耗尽即停止后续在线补充。
            if deadline is not None and time.monotonic() > deadline:
                logging.getLogger(__name__).info("侦察总预算耗尽，跳过剩余官方站点抓取")
                break
            fetch_res = self.fetcher.fetch(url)
            if fetch_res.is_valid and fetch_res.content:
                extracted = self.extractor.extract_from_html(
                    html_text=fetch_res.content,
                    page_url=url,
                    school_name=school_name,
                    target_year=exam_year,
                    source_type=src_type,
                    ssl_verified=getattr(fetch_res, "ssl_verified", True),
                    access_status=getattr(fetch_res, "access_status", "OK"),
                )
                all_evidences.extend(extracted)

        # 4b. [接通 discovery·原先的完全死代码] 官方站内检索补充。
        #     院校注册表里的域名可能**过期、缺失或只填了学校主页**，此时上面那轮
        #     直接抓取会一无所获，而 `OfficialDiscovery.build_targeted_queries()`
        #     生成的 `site:域名 + 年份 + 招生简章/专业目录` 查询此前从未被任何代码
        #     调用过（只有构造、没有使用）。现在把它接上检索运行时：
        #     用站内查询找到**官方招生页/专业目录页**，再抓取抽取证据。
        discovered = self._discover_official_pages(
            entity, school_name, major_query, exam_year,
            already_fetched={u for _, u in target_domains[:2]},
            deadline=deadline)
        for url in discovered[:2]:
            # [P2 修复·2026-10-08] 发现页抓取同样受总预算约束（逐页检查）。
            if deadline is not None and time.monotonic() > deadline:
                logging.getLogger(__name__).info("侦察总预算耗尽，跳过剩余发现页抓取")
                break
            fetch_res = self.fetcher.fetch(url)
            if fetch_res.is_valid and fetch_res.content:
                all_evidences.extend(self.extractor.extract_from_html(
                    html_text=fetch_res.content,
                    page_url=url,
                    school_name=school_name,
                    target_year=exam_year,
                    source_type="official_discovered",
                    ssl_verified=getattr(fetch_res, "ssl_verified", True),
                    access_status=getattr(fetch_res, "access_status", "OK"),
                ))

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
            # [B6] 只读模式下拒绝落盘但保留报告文本（与 comparator 同口径）。
            try:
                saved_path = self._save_report(school_name, major_query, markdown_content)
            except Exception as _save_exc:
                if isinstance(_save_exc, PermissionDeniedError):
                    markdown_content += (
                        "\n\n> 🔒 当前为严格只读模式，研报未落盘，"
                        "仅展示本次侦察结果。\n")
                else:
                    raise

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

    def _discover_official_pages(self, entity, school_name: str,
                                 major_query: str, exam_year: int,
                                 already_fetched: set = None,
                                 deadline: Optional[float] = None) -> List[str]:
        """用站内检索发现官方招生页 URL（注册表域名失效时的兜底发现路径）。

        这是 `OfficialDiscovery` 的**首次真实调用**：它此前只被实例化、从未被使用。
        检索命中后只保留**官方域名**的链接（研招网/研究生院/学校官网），
        避免把培训机构页面当成官方来源。

        :param deadline: [P2 修复·2026-10-08] 侦察总预算的墙钟 deadline
            （``query`` 的单一真源派生）；每次联邦检索（多源、可能带重试）前
            检查，耗尽即返回已发现结果。None 表示不熔断。
        """
        already = set(already_fetched or set())
        found: List[str] = []
        if deadline is not None and time.monotonic() > deadline:
            return found
        domains = []
        if entity:
            for attr in ("admission_domain", "graduate_domain", "official_domain"):
                value = str(getattr(entity, attr, "") or "").strip()
                if value:
                    domains.append(value)
        if not domains:
            return []

        try:
            try:
                from search import SearchQuery, SearchService
            except ImportError:  # pragma: no cover
                from tools.search import SearchQuery, SearchService  # type: ignore

            # 必须走 default()：只有它会装配 Deduplicator / Ranker / SearchCache，
            # 直连构造会让去重静默失效（发现路径会把同一官方页的多种跳转 URL 重复收下）。
            service = SearchService.default()
            for domain in domains[:2]:
                for query in self.discovery.build_targeted_queries(
                        school_name=school_name, domain=domain,
                        major_keyword=major_query or None, year=exam_year):
                    # [P2 修复·2026-10-08] 每次联邦检索前检查总预算：此前该
                    # 循环无任何时长预算（最多 2 域名 × 2 查询 = 4 次多源
                    # 检索），弱网下可把总墙钟拖到分钟级。
                    if deadline is not None and time.monotonic() > deadline:
                        logging.getLogger(__name__).info("侦察总预算耗尽，停止站内检索发现")
                        return found
                    resp = service.search(SearchQuery(text=query, limit=3))
                    for result in resp.results:
                        if result.url in already or result.url in found:
                            continue
                        # 只收官方来源（研招网/研究生院/学校官网/官方文档）
                        host = urllib.parse.urlparse(result.url).hostname or ""
                        allowed = [urllib.parse.urlparse(d).hostname or d for d in domains]
                        if ((result.is_official or result.source_type == "official_discovered")
                                and any(host == d or host.endswith("." + d) for d in allowed)):
                            found.append(result.url)
                    if found:
                        break
        except Exception as exc:                   # pragma: no cover - 发现失败不该阻断侦察
            logging.getLogger(__name__).info("官方站点发现失败（忽略）: %s", exc)
        return found

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
        # [P2 修复·空锚点死链] 此前 domains 缺失时回落 `'#'`，渲染出
        # `[安徽财经大学 本科/学校官网](#)` 这类点了没反应的死链（econ 沙箱实测：
        # 该校未收录 official/admission 域名，前两条链接全部落 `#`）。
        # 处置：① 研究生院条目按 招生办→研究生院 回退（有真实 URL 就用真实 URL）；
        # ② 研招网专页缺失时按校名现拼官方检索 URL（研招网 sch/search 入口）；
        # ③ 确实没有任何 URL 的条目降级为纯文本并注明未收录，不再输出可点击的 '#'。
        _domains = site_graph.get("domains") or {}
        _chsi_portals = site_graph.get("chsi_portals") or {}
        _official_url = _domains.get("official")
        _grad_url = _domains.get("admission_office") or _domains.get("graduate_school")
        _chsi_school_url = _chsi_portals.get("school_info") or (
            "https://yz.chsi.com.cn/sch/search.do?ssdm=&yjsy=&xxmc="
            + urllib.parse.quote(school_name))
        _chsi_zsml_url = _chsi_portals.get("zsml_catalog") or "https://yz.chsi.com.cn/zsml/queryAction.do"

        def _site_entry(idx: str, label: str, url: Optional[str]) -> str:
            if url:
                return f"{idx}. **[{label}]({url})**"
            return f"{idx}. **{label}**（本地院校库暂未收录该域名，可经下方研招网入口检索）"

        lines.extend([
            "## 🏛️ 2. 官方权威站点有向图谱 (Evidence Tree)",
            _site_entry("1", f"{school_name} 本科/学校官网", _official_url),
            _site_entry("2", f"{school_name} 研究生院 / 招生办公室", _grad_url),
            _site_entry("3", f"【教育部直达】研招网 {school_name} 信息专页", _chsi_school_url),
            _site_entry("4", "【目录检索】研招网硕士专业目录查询系统", _chsi_zsml_url),
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
                # [缺陷修复] 单位只对"数量型"取值有意义；对清单/结构化取值（list/dict）
                # 追加单位会渲染出「…专业自命题或统考 门」这类悬空字符，故此处跳过。
                unit_str = f" {ev.unit}" if (
                    getattr(ev, "unit", None) and not isinstance(ev.value, (list, dict))) else ""
                lines.append(f"- **指标数值**：`{val_repr}{unit_str}`")
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
            ""
        ])

        # 5. 个人学情量化报考风险与提分门槛诊断 (User State Gap Analysis)
        lines.extend(self._assess_user_risk(entity, major_query, evidences))

        # [缺陷修复·虚假来源声明] 此前无论证据是否真的来自官方站点，结尾都断言
        # 「本研报基于权威官方站点生成」；而同一份研报的第 3 节刚写明
        # 「研招网/官网当期页面未能成功抓取…离线基准…置信度 30%」，前后自相矛盾。
        # 现按证据链的真实置信度决定措辞：只有确已取得官方证据才宣称有官方支撑。
        _max_conf = 0.0
        try:
            _max_conf = max([float(getattr(_e, "confidence", 0) or 0) for _e in (evidences or [])] or [0.0])
        except Exception:
            _max_conf = 0.0
        if _max_conf > 0.3:
            _src_line = "> 💡 **KaoYan Intelligence 战略提示**：本研报核心指标由官方站点证据链支撑。"
        else:
            _src_line = ("⚠️ **数据来源声明**：本次**未能**抓取研招网/目标高校官网当期页面，"
                         "上述指标为**离线基准兜底推定值（未核验）**，并非该校官方核实数据；"
                         "报考前请务必以院校研究生院当年招生简章与专业目录为准。")
        lines.extend([
            "---",
            f"{_src_line} 可结合自身模考水平，在会话中让 AI 私教为你出具针对 `{school_name}` 的定制备考处方。"
        ])

        return "\n".join(lines)

    def _assess_user_risk(
        self,
        entity: Optional[UniversityEntity],
        major_query: Optional[str],
        evidences: List[EvidenceObject]
    ) -> List[str]:
        """结合学员当前基准分与目标分，量化诊断报考冲刺风险与提分阈值"""
        lines = [
            "## 🎯 5. 个人学情量化报考风险与提分门槛诊断 (User State Gap Analysis)",
            "> 联动学员 ky_config.json 设定的初始摸底分与战役目标成绩，提供量化录取门槛研判："
        ]
        cfg_path = ROOT / "ky_config.json"
        cfg = {}
        if cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            except Exception as exc:
                # 配置损坏会让整份诊断静默退回通用默认值（学员的个性化目标全部失效），
                # 属于用户可见的降级，故用 warning 而非 debug 留痕
                import logging
                logging.getLogger(__name__).warning(
                    "ky_config.json 解析失败，将使用默认目标分: %s -> %s", cfg_path, exc)

        plan = cfg.get("study_plan", {})
        math_key = plan.get("math_key", "")
        math_target = plan.get("math_target") or cfg.get("math_target", "110+")
        eng_target = plan.get("english_target") or plan.get("eng_target") or cfg.get("eng_target", "65+")
        pol_target = plan.get("politics_target") or plan.get("pol_target") or cfg.get("pol_target", "70+")
        pro_target = plan.get("pro_target") or cfg.get("pro_target", "120-130")
        total_target = plan.get("target_score") or plan.get("total_target") or cfg.get("total_target", "370+")
        math_name = plan.get("math_name") or cfg.get("math_name", "数学")
        pro_name = plan.get("pro_name") or cfg.get("pro_name", "专业课")

        # [P1 修复·研招情报画像一致性闸门] 根据考生画像过滤不匹配的建议
        math_disabled = str(math_key).lower() in {"none", "no", "不考数学"} or math_name == "不考数学"

        targets = [f"英语 `{eng_target}`", f"政治 `{pol_target}`", f"{pro_name} `{pro_target}`"]
        if not math_disabled:
            targets.insert(0, f"{math_name} `{math_target}`")

        # [P2 修复·宣称与占位现实矛盾] 此前无条件宣称「围绕…已核验的考试大纲与
        # 题源安排复习」——与项目红线「替换前不得宣称按纲出题」冲突（matmech
        # 沙箱实测：专业课大纲仍为【待自填】占位、无任何题源时，admission 报告
        # 已宣称「已核验的考试大纲与题源」）。现按大纲真实状态与白名单实况分档。
        _pro_books_raw = str(plan.get("pro_books") or cfg.get("pro_books") or "").strip()
        _pro_books_ready = bool(_pro_books_raw) and not _pro_books_raw.startswith(
            "暂未放置实体资料") and "待自填" not in _pro_books_raw
        _syllabus_state = "missing"
        try:
            try:
                from syllabus_manager import pro_syllabus_state
            except ImportError:
                from tools.syllabus_manager import pro_syllabus_state
            _syllabus_state = pro_syllabus_state(ROOT)
        except Exception:
            _syllabus_state = "missing"

        if _syllabus_state == "ready" and _pro_books_ready:
            _pro_prep = f"围绕「{pro_name}」已核验的考试大纲与题源安排复习。"
        elif _syllabus_state == "ready":
            _pro_prep = (f"「{pro_name}」考试大纲已就绪；题源白名单尚未导入，"
                         "可先用 `ky mount` 盘点本地资料、`ky ingest` 切片入库，再安排刷题。")
        else:
            _state_word = "仍为【待自填】占位骨架" if _syllabus_state == "placeholder" else "尚未导入"
            _pro_prep = (f"「{pro_name}」考试大纲{_state_word}（未核验），**替换前不得宣称按纲出题**；"
                         "请先从目标院校研究生院官网下载真实大纲替换 `04-专业课/考试大纲.md`，再安排按纲复习。")

        lines.extend([
            f"- **个人目标**：总分 `{total_target}` ｜ " + " ｜ ".join(targets),
            "- **录取风险**：个人目标分不代表院校门槛或录取保证。需核验同年度、同专业方向、"
            "同学习方式的招生计划、复试线、单科线和录取规则后再评估。",
            f"- **专业课准备**：{_pro_prep}",
            "- **复试准备**：以目标学院当年复试细则为准，确认笔试、面试和实践考核内容。",
            "- **一志愿规则**：以官方复试录取办法和录取名单为依据，不根据院校层次推断保护政策。",
        ])

        # [P1 修复·画像一致性闸门] 不考数学的考生不提供数学相关建议
        if math_disabled:
            lines.append("")
            lines.append("> ℹ️ **备考特殊说明**：根据你的备考方案（math_key=none），"
                        "本次不安排数学相关复习建议。若专业实际要求数学，请在 `ky plan` 中重新配置。")

        lines.append("")
        return lines

    def _save_report(self, school_name: str, major_query: Optional[str], content: str) -> Path:
        """保存研报到 04-专业课/（路径走 report_paths 单一真源，与 ky scout 同函数）"""
        from .report_paths import scout_report_path
        target_dir = ROOT / "04-专业课"
        target_dir.mkdir(parents=True, exist_ok=True)

        filepath = scout_report_path(ROOT, school_name, major_query)

        # [P0 修复] admission(证据链版) 与 scout(口碑版) 均落盘到同名文件，
        # 后写者会直接覆盖前者导致证据链/口碑研报丢失。写入前按项目惯例备份旧报告。
        if filepath.exists():
            try:
                from syllabus_manager import backup_syllabus_file
            except Exception:
                try:
                    from tools.syllabus_manager import backup_syllabus_file
                except Exception:
                    backup_syllabus_file = None
            if backup_syllabus_file:
                backup_syllabus_file(filepath)

        # [B6 修复·safe 绕过] 原裸 open(w) 不经 guard_write，
        # --permission=safe 下仍落盘。atomic_write_text 自带守卫（只读模式抛错，
        # 由上游 IntelTaskWorker 转为可读文本，不丢异常语义）。
        # [G5 修复·import 内联] atomic_write_text 已提到模块顶部统一导入。
        atomic_write_text(filepath, content)

        return filepath


# 全局单例
_default_engine = None

def get_intelligence_engine() -> KaoYanIntelligenceEngine:
    global _default_engine
    if _default_engine is None:
        _default_engine = KaoYanIntelligenceEngine()
    return _default_engine


# 兼容别名
ScoutEngine = KaoYanIntelligenceEngine
