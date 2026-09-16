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
import urllib.parse
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

from .models import UniversityEntity, EvidenceObject, current_exam_year
from .registry import get_registry, resolve_university
from .chsi_connector import CHSIConnector
from .fetcher import HTTPFetcher
from .discovery import OfficialDiscovery
from .extractor import DocumentExtractor
from .evidence_engine import resolve_conflicts

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
        exam_year: Optional[int] = None,
        save_report: bool = False
    ) -> Dict[str, Any]:
        """
        全流程执行高校招考情报检索与证据链聚合
        """
        exam_year = exam_year or current_exam_year()
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
            already_fetched={u for _, u in target_domains[:2]})
        for url in discovered[:2]:
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

    def _discover_official_pages(self, entity, school_name: str,
                                 major_query: str, exam_year: int,
                                 already_fetched: set = None) -> List[str]:
        """用站内检索发现官方招生页 URL（注册表域名失效时的兜底发现路径）。

        这是 `OfficialDiscovery` 的**首次真实调用**：它此前只被实例化、从未被使用。
        检索命中后只保留**官方域名**的链接（研招网/研究生院/学校官网），
        避免把培训机构页面当成官方来源。
        """
        already = set(already_fetched or set())
        found: List[str] = []
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

            service = SearchService()
            for domain in domains[:2]:
                for query in self.discovery.build_targeted_queries(
                        school_name=school_name, domain=domain,
                        major_keyword=major_query or None, year=exam_year):
                    resp = service.search(SearchQuery(text=query, limit=3))
                    for result in resp.results:
                        if result.url in already or result.url in found:
                            continue
                        # 只收官方来源（研招网/研究生院/学校官网/官方文档）
                        if result.is_official or result.source_type == "official_discovered":
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
        math_target = plan.get("math_target") or cfg.get("math_target", "110+")
        eng_target = plan.get("english_target") or plan.get("eng_target") or cfg.get("eng_target", "65+")
        pol_target = plan.get("politics_target") or plan.get("pol_target") or cfg.get("pol_target", "70+")
        pro_target = plan.get("pro_target") or cfg.get("pro_target", "120-130")
        total_target = plan.get("target_score") or plan.get("total_target") or cfg.get("total_target", "370+")
        math_name = plan.get("math_name") or cfg.get("math_name", "数学")
        pro_name = plan.get("pro_name") or cfg.get("pro_name", "专业课")

        is_top_985 = entity and any(t in entity.level for t in ["985", "C9联盟", "双一流A类", "自划线"])

        lines.extend([
            f"- **当前战役目标成绩**：总分 `{total_target}` ｜ 数学 `{math_target}` ｜ 英语 `{eng_target}` ｜ 政治 `{pol_target}` ｜ 专业课 `{pro_target}`",
        ])

        sch_title = entity.name if entity else "目标院校"
        if is_top_985:
            lines.extend([
                f"- **院校门槛定位**：`{sch_title}` 属 34 所自主划线 / 顶尖名校，考研竞争处于白热化高压区，通常具备以下硬性门槛：",
                f"  1. **初试底线**：近三年专硕/学硕复试线通常在 330~355 分高位，设定的 `{total_target}` 目标分处于安全上岸区（具备约 15~25 分复试差额缓冲垫）；",
                f"  2. **数学与专业课提分死命令**：{math_name}必须确保达到 `{math_target}`，{pro_name}必须攻坚至 `{pro_target}`，两门单科合计需贡献 230+ 分基本盘；",
                f"  3. **复试硬实力储备**：顶尖名校极其看重编程机试与专业素养，初试后需无缝衔接算法题库训练，不可松懈。"
            ])
        else:
            is_b_zone = any("二区" in l or "B区" in l for l in (entity.level if entity else []))
            zone_desc = "教育部国家二区线 (B区，享受降分照顾)" if is_b_zone else "教育部国家一区线 (A区)"
            # [缺陷修复·层次误判(N3)] 此前对所有非 985/自划线院校一律断言
            # "属地方公办/行业重点高校"。对未收录院校（层次字段为空）这是无依据的定性，
            # 且会**低估**真实层次 —— 例如天津工业大学是"双一流"建设高校，
            # 却被写成"地方公办/行业重点"、"非热门院校"。
            _lvl = "、".join([l for l in (entity.level if entity else []) if l]) or ""
            if _lvl:
                _level_desc = f"本地院校库收录层次：{_lvl}"
            else:
                _level_desc = ("**未收录于本地院校库**，本条不作层次定性，请以院校官方公示为准")
            lines.extend([
                f"- **院校层次**：`{sch_title}` —— {_level_desc}",
                # [缺陷修复·国家线与复试线混用(N4)] 此前先用国家线定位门槛，
                # 随即又断言"超出历年复试线约 70~100 分""降维打击""稳妥上岸优选"。
                # 国家线是**最低准入线**，与院校自定的复试线是两个概念；
                # 本次并未取得该校复试线（未联网核验），因此不能给出任何分差结论。
                f"- **初试最低门槛（国家线，仅作下限参考）**：执行 **{zone_desc}**"
                f"（近年工科/理科国家线总分约 `265~275 分`、单科约 `36~38 分`）。"
                f"⚠️ 达线**仅表示具备复试与调剂资格**，不等于达到该校复试线。",
                f"- **复试线未核验（不给出分差结论）**：本次未取得 `{sch_title}` 的历年复试线"
                f"与报录比，故**无法判断**目标分 `{total_target}` 相对该校复试线的余量，"
                f"也不宜据此判断「稳不稳」。建议联网条件允许时运行 "
                f"`ky admission {sch_title} {major_query or ''}`，或直接查阅该校研究生院官网"
                f"《复试录取工作办法》与拟录取名单，拿到真实数据后再评估。",
                f"- **考情画像与私教攻坚处方**：",
                f"  1. **单科死穴优先**：最危险的情形不是总分不够，而是总分很高却因英语/数学"
                f"差 1 分未过**单科线**被一票否决。请把 {math_name} 稳在 `{math_target}`、"
                f"英语稳在 `{eng_target}`，先保单科不失。",
                f"  2. **自命题专业课**：{pro_name} 目标 `{pro_target}`。院校自命题通常不公布"
                f"官方真题与参考答案，需通过直系学长学姐获取近 3 年回忆版试卷，并在每年 9 月"
                f"核验最新《招生专业目录》，防范突然改考统考。",
                f"  3. **一志愿保护须自行核验**：请自行检索该校目标学院是否存在"
                f"「压一志愿、招调剂」的负面舆情；在拿到官方复试录取名单之前，"
                f"不要假定任何学校会保护一志愿。",
            ])

        lines.append("")
        return lines

    def _save_report(self, school_name: str, major_query: Optional[str], content: str) -> Path:
        """保存研报到 04-专业课/"""
        target_dir = ROOT / "04-专业课"
        target_dir.mkdir(parents=True, exist_ok=True)

        safe_major = f"_{major_query.strip()}" if major_query else ""
        filename = f"目标院校情报_{school_name}{safe_major}.md"
        filepath = target_dir / filename

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
