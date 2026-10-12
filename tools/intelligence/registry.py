# -*- coding: utf-8 -*-
"""
KaoYan Intelligence · 高校实体解析器与有向站点图谱 (University Resolver & Site Graph)

职责：
  1. 加载并管理 data/universities/registry.json
  2. 模糊匹配高校别名、简称（如“华科”、“南医大”、“成电”、“HUST”）
  3. 构建高校有向站点树（官网 → 研究生院 → 招生网 → 二级学院）
"""

import json
import sys
import urllib.parse
from pathlib import Path


try:  # 双导入路径兼容（源码脚本式 / tools 包式）
    from workspace import resolve_workspace_root
except ImportError:  # pragma: no cover
    from tools.workspace import resolve_workspace_root
from typing import Any, Dict, List, Optional, Tuple

import yaml

from .models import UniversityEntity
from .registry_synth import synthesize_unlisted_school


def _resolve_data_path(subpath: str) -> Path:
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        for candidate in (
            exe_dir / "data" / subpath,
            exe_dir / "_internal" / "data" / subpath,
        ):
            if candidate.exists():
                return candidate
    if hasattr(sys, "_MEIPASS"):
        for candidate in (
            Path(sys._MEIPASS) / "data" / subpath,
            Path(sys._MEIPASS) / "universities" / subpath.split("/")[-1],
        ):
            if candidate.exists():
                return candidate
    return resolve_workspace_root(__file__) / "data" / subpath


REGISTRY_PATH = _resolve_data_path("universities/registry.json")
NATIONAL_INSTITUTIONS_PATH = _resolve_data_path("universities/national_institutions.json")

PROVINCES_LIST = [
    "北京", "天津", "上海", "重庆",
    "河北", "山西", "辽宁", "吉林", "黑龙江",
    "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南", "广东", "海南",
    "四川", "贵州", "云南", "陕西", "甘肃", "青海", "台湾",
    "内蒙古", "广西", "西藏", "宁夏", "新疆",
    "香港", "澳门"
]


def extract_province(region_str: str, school_name: str = "") -> str:
    """从地域文本或学校名称中提取标准省份/直辖市/自治区"""
    if region_str:
        for p in PROVINCES_LIST:
            if region_str.startswith(p) or p in region_str:
                return p
    if school_name:
        for p in PROVINCES_LIST:
            if school_name.startswith(p) or p in school_name:
                return p
    return "其他"


def validate_university_yaml(data_or_path: Any) -> Tuple[bool, List[str]]:
    """
    校验高校 YAML 档案是否满足 Schema 标准：
    必填项：name, chsi_code / code, region / province, level, official_domain / official_site
    """
    data = data_or_path
    if isinstance(data_or_path, (str, Path)):
        p = Path(data_or_path)
        if not p.exists():
            return False, [f"文件不存在: {p}"]
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception as e:
            return False, [f"YAML 解析失败: {e}"]

    if not isinstance(data, dict):
        return False, ["高校数据必须为 YAML 映射字典"]

    errors = []
    if not data.get("name") or not str(data.get("name")).strip():
        errors.append("缺少必填字段: name (高校全称)")

    code = data.get("chsi_code") or data.get("code")
    if not code or not str(code).strip():
        errors.append("缺少必填字段: chsi_code / code (院校代码)")

    reg = data.get("region") or data.get("province")
    if not reg or not str(reg).strip():
        errors.append("缺少必填字段: region / province (省份/区域)")

    lvl = data.get("level")
    if not lvl:
        errors.append("缺少必填字段: level (办学层次)")

    dom = data.get("official_domain") or data.get("official_site") or data.get("domains")
    if not dom:
        errors.append("缺少必填字段: official_domain / official_site (官网通道)")

    return len(errors) == 0, errors


def export_to_provincial_yamls(target_dir: Optional[Path] = None, source_registry: Optional[Path] = None) -> List[Path]:
    """
    将集中式 registry.json 中的高校实体按省份分流导出为 YAML 文件：
    data/universities/<省份>/<高校名称>.yaml
    """
    source_path = source_registry or REGISTRY_PATH
    out_dir = target_dir or REGISTRY_PATH.parent

    if not source_path.exists():
        return []

    try:
        with open(source_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
    except Exception:
        return []

    exported_paths = []
    for code, item in raw_data.items():
        name = item.get("name", "").strip()
        if not name:
            continue
        region = item.get("region", "")
        prov = extract_province(region, name)
        prov_dir = out_dir / prov
        prov_dir.mkdir(parents=True, exist_ok=True)

        yaml_obj = {
            "chsi_code": str(item.get("chsi_code", code)),
            "name": name,
            "aliases": item.get("aliases", []),
            "level": item.get("level", []),
            "region": region,
            "province": prov,
            "official_domain": item.get("official_domain", ""),
            "graduate_domain": item.get("graduate_domain", ""),
            "admission_domain": item.get("admission_domain", ""),
            "departments": item.get("departments", {})
        }

        yaml_file = prov_dir / f"{name}.yaml"
        with open(yaml_file, "w", encoding="utf-8") as yf:
            yaml.dump(yaml_obj, yf, allow_unicode=True, sort_keys=False, default_flow_style=False)
        exported_paths.append(yaml_file)

    return exported_paths


#: 实体标量字段的填空式合并顺序（先加载的源优先，后加载者只补空缺）
_SCALAR_FIELDS = (
    "chsi_code", "name", "region", "province", "city", "zone", "authority",
    "school_type", "name_eng", "chsi_sch_id", "chsi_url",
    "official_domain", "graduate_domain", "admission_domain",
)

#: 视为「空值」的占位符，合并时允许被真实值覆盖
_BLANK_TOKENS = ("", "待查", "待核验", "全国", "未知", "None")


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() in _BLANK_TOKENS
    if isinstance(value, (list, dict, tuple)):
        return len(value) == 0
    return False


def compose_intro(entity: UniversityEntity) -> str:
    """由已核验的结构化字段拼装学校简介。

    [反幻觉纪律] 本函数只做「事实串联」：所有分句都由研招网院校库/官网抓取到的字段直接生成，
    不添加任何未经核验的描述性内容（如办学历史、师资规模、排名等），字段缺失时整句省略。
    """
    parts: List[str] = []
    head = entity.name
    if entity.name_eng:
        head += f"（{entity.name_eng}）"
    loc = entity.region or entity.province
    if loc:
        head += f"位于{loc}"
    parts.append(head + "。")

    if entity.authority:
        parts.append(f"主管部门：{entity.authority}。")

    traits: List[str] = []
    if entity.school_type:
        traits.append(f"{entity.school_type}类")
    if "双一流建设高校" in entity.level or "双一流" in entity.level:
        traits.append("“双一流”建设高校")
    if "985" in entity.level:
        traits.append("985 工程高校")
    if "211" in entity.level:
        traits.append("211 工程高校")
    if "设研究生院" in entity.level:
        traits.append("设研究生院")
    if "自划线高校" in entity.level:
        traits.append("自划线高校")
    if traits:
        parts.append("院校特性：" + "、".join(traits) + "。")

    if entity.zone:
        zone_cn = "二区线（B区）" if entity.zone == "B区" else "一区线（A区）"
        parts.append(f"所在省份执行国家{zone_cn}。")

    if entity.departments:
        # departments 的键统一为「学科/专业」（如「(081200)计算机科学与技术」「马克思主义理论」），
        # 院系名在 value 的 college_name 里，故此处措辞用「学科专业」而非「院系」。
        majors = [k for k in entity.departments.keys() if k]
        if majors:
            shown = "、".join(majors[:6])
            more = f" 等 {len(majors)} 个学科专业" if len(majors) > 6 else ""
            parts.append(f"已收录学科专业：{shown}{more}。")

    if entity.chsi_url:
        parts.append(f"研招网院校直达页：{entity.chsi_url}")
    return "".join(parts)


#: [P0 修复·2026-10-11 第五轮深审] 院校名解析的输入长度上限（字符）。
#: 中文校名最长约 20 字（如「北京师范大学-香港浸会大学联合国际学院」），
#: 自然语言输入（"我想考XX大学"）通常 10 字以内。60 是宽松上限：
#: 超过即视为非校名查询（长句/垃圾串），不再进入子串与合成匹配。
_MAX_QUERY_LEN = 60


class UniversityRegistry:
    """高校注册表管理器"""

    def __init__(self, registry_file: Optional[Path] = None, national_file: Optional[Path] = None):
        self.file_path = registry_file or REGISTRY_PATH
        self.national_path = national_file or NATIONAL_INSTITUTIONS_PATH
        self._entities: Dict[str, UniversityEntity] = {}
        self._alias_map: Dict[str, str] = {} # alias_lower -> chsi_code
        self._name_map: Dict[str, str] = {}  # name_lower -> chsi_code
        self.load()

    def _register_item(self, item: Dict[str, Any], default_code: str = "") -> None:
        """内部注册单个高校字典至实体池与索引。

        [合并语义] 同一所高校可能同时出现在 registry.json（精品库，先加载）、
        national_institutions.json（全国底座）与分省 YAML 中。旧实现直接以
        ``self._entities[key] = entity`` 覆盖，导致后加载的批量数据会把精品库里
        手工维护的研究生院域名、二级学院、初试科目整段冲掉。现改为「填空式合并」：
        先加载者（权威度更高）的非空字段不被覆盖，后加载者只补空缺。
        """
        chsi_code = str(item.get("chsi_code") or item.get("code") or default_code or "").strip()
        name = str(item.get("name", "")).strip()
        if not name:
            return
        aliases = item.get("aliases", [])
        if not isinstance(aliases, list):
            aliases = [str(aliases)] if aliases else []

        level = item.get("level", [])
        if isinstance(level, str):
            level = [level]
        elif not isinstance(level, list):
            level = []

        region = str(item.get("region") or item.get("province") or "").strip()
        official_domain = str(item.get("official_domain") or item.get("official_site") or "").strip()
        graduate_domain = str(item.get("graduate_domain") or "").strip()
        admission_domain = str(item.get("admission_domain") or "").strip()
        departments = item.get("departments") or item.get("pro_majors") or {}
        if not isinstance(departments, dict):
            departments = {}

        entity = UniversityEntity(
            chsi_code=chsi_code or "待查",
            name=name,
            aliases=[str(a) for a in aliases],
            level=[str(l) for l in level],
            region=region,
            official_domain=official_domain,
            graduate_domain=graduate_domain,
            admission_domain=admission_domain,
            departments=departments,
            province=str(item.get("province") or "").strip(),
            city=str(item.get("city") or "").strip(),
            zone=str(item.get("zone") or "").strip(),
            authority=str(item.get("authority") or "").strip(),
            school_type=str(item.get("type") or item.get("school_type") or "").strip(),
            name_eng=str(item.get("name_eng") or "").strip(),
            chsi_sch_id=str(item.get("chsi_sch_id") or "").strip(),
            chsi_url=str(item.get("chsi_url") or "").strip(),
        )
        entity_key = chsi_code if (chsi_code and chsi_code != "待查") else f"UNLISTED_{name}"

        name_key = self._name_map.get(entity.name.lower())
        if name_key and name_key in self._entities:
            # 同名已注册（可能来自更高优先级的精品库）→ 并入既有实体，避免同校两份
            target = self._entities[name_key]
            self._merge_entity(target, entity)
            if entity_key != name_key:
                self._entities[entity_key] = target
            entity = target
        elif entity_key in self._entities:
            target = self._entities[entity_key]
            self._merge_entity(target, entity)
            entity = target
        else:
            self._entities[entity_key] = entity

        self._name_map[entity.name.lower()] = name_key or entity_key
        for alias in entity.aliases:
            if alias:
                self._alias_map[alias.lower().strip()] = self._name_map[entity.name.lower()]

    @staticmethod
    def _merge_entity(base: UniversityEntity, incoming: UniversityEntity) -> None:
        """把 incoming 的非空事实补进 base，base 已有值一律保留（base 优先级更高）。"""
        for fld in _SCALAR_FIELDS:
            if _is_blank(getattr(base, fld, "")) and not _is_blank(getattr(incoming, fld, "")):
                setattr(base, fld, getattr(incoming, fld))

        for fld in ("aliases", "level"):
            merged = list(getattr(base, fld) or [])
            for val in (getattr(incoming, fld) or []):
                if val and val not in merged:
                    merged.append(val)
            setattr(base, fld, merged)

        merged_depts = dict(base.departments or {})
        for dept_key, dept_val in (incoming.departments or {}).items():
            if dept_key not in merged_depts:
                merged_depts[dept_key] = dept_val
                continue
            cur = merged_depts[dept_key]
            if isinstance(cur, dict) and isinstance(dept_val, dict):
                for sub_key, sub_val in dept_val.items():
                    if _is_blank(cur.get(sub_key)) and not _is_blank(sub_val):
                        cur[sub_key] = sub_val
        base.departments = merged_depts

    def load(self) -> None:
        """加载高校注册表 (优先从 JSON 底座加载，并递归加载各省份 YAML 档案增强覆盖)"""
        self._entities.clear()
        self._alias_map.clear()
        self._name_map.clear()

        # 1. 尝试从 registry.json 加载
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    raw_data = json.load(f)
                for code, item in raw_data.items():
                    self._register_item(item, default_code=str(code))
            except Exception:
                pass

        # 2. 尝试从 national_institutions.json 加载全国高校底座
        # 注意：该文件的键是**校名**（不是招生单位代码），故不可把键当 default_code 传入
        # —— 否则 899 所无教育部代码的院校（如「中国消防救援学院」）的 chsi_code
        # 会被填成校名本身，既污染字段语义，又让下游依赖 "UNLISTED" 前缀判定的
        # 「未核验院校」分支永远不命中。此处一律交给条目自身的 chsi_code 说话。
        if self.national_path.exists():
            try:
                with open(self.national_path, "r", encoding="utf-8") as f:
                    nat_data = json.load(f)
                if isinstance(nat_data, dict):
                    for key, item in nat_data.items():
                        if isinstance(item, dict):
                            self._register_item(item, default_code="")
            except Exception:
                pass

        # 3. 递归扫描分省 YAML 目录 (data/universities/<省份>/*.yaml)
        base_dir = self.file_path.parent
        if base_dir.exists() and base_dir.is_dir():
            for y_file in base_dir.glob("*/*.y*ml"):
                try:
                    with open(y_file, "r", encoding="utf-8") as yf:
                        y_data = yaml.safe_load(yf)
                    if isinstance(y_data, dict):
                        ok, _ = validate_university_yaml(y_data)
                        if ok:
                            self._register_item(y_data)
                except Exception:
                    pass

        # 4. 统一拼装学校简介（必须放在全部来源合并完成后，保证简介反映合并后的最终事实）
        for entity in {id(e): e for e in self._entities.values()}.values():
            entity.intro = compose_intro(entity)

    def export_to_provincial_yamls(self, target_dir: Optional[Path] = None) -> List[Path]:
        return export_to_provincial_yamls(target_dir=target_dir, source_registry=self.file_path)

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

        # 4. 名称包含匹配（双向，按"最长校名优先"择优；长度守卫见助手 docstring）
        ent = self._match_by_name_substring(q_lower)
        if ent is not None:
            return ent

        # 5. 别名匹配（前缀对齐 + q 长度守卫，详见助手 docstring）
        ent = self._match_by_alias_prefix(q_lower)
        if ent is not None:
            return ent

        # 6. 启发式通用高校实体合成（长度收口，详见助手 docstring）
        return self._synthesize_or_none(q, q_lower)

    def _match_by_name_substring(self, q_lower: str) -> UniversityEntity | None:
        """步骤 4：名称双向包含匹配（最长校名优先）。

        [缺陷修复·确定性] 旧实现遍历 dict 返回首个命中，结果依赖字典顺序，
        且未按长度择优；现改为收集全部命中后取最长校名（最具体者胜出）。

        [P0 修复·2026-10-11 第五轮深审] 双向裸子串必须加长度守卫（第 5 步
        曾因同类事故改前缀对齐，此处漏改）：
          * ``q_lower in name``（简称方向）：q 至少 4 字符。实测 resolve("华")
            命中「中国兵器科学研究院(陕西青华机电研究所)」、resolve("理")
            命中「中国兵器科学研究院(陕西应用物理化学研究所)」—— 单汉字
            查询会静默换成另一所完全无关的学校，下游 dynamic_fallback_profile
            还会对误命中实体贴 [LOCAL_DB_VERIFIED 本地高校库实录] 信任标签。
            3 字下限不够：「研究院」「研究所」这类通用词仍会命中最长校名；
            2 字简称（清华/北大/华科/南大…44 个）与 3 字简称（北理工/哈工大…
            51 个）已由第 3 步别名表精确覆盖，名称子串方向从 4 字起才有
            区分度（如「华中科技」⊂「华中科技大学」）。
          * ``name in q_lower``（自然语言方向）：q 不超 _MAX_QUERY_LEN。2 万字
            垃圾串只要含一个完整校名就会被当成"自然语言输入"命中。
        """
        name_hits: List[str] = []
        if len(q_lower) >= 4:
            name_hits.extend(code for name, code in self._name_map.items()
                             if q_lower in name)
        if len(q_lower) <= _MAX_QUERY_LEN:
            name_hits.extend(code for name, code in self._name_map.items()
                             if name in q_lower)
        if name_hits:
            name_hits.sort(key=lambda c: -len(self._entities[c].name or ""))
            return self._entities[name_hits[0]]
        return None

    def _match_by_alias_prefix(self, q_lower: str) -> UniversityEntity | None:
        """步骤 5：别名匹配（[P0 修复] 必须前缀对齐，严禁裸子串）。

        旧实现用 ``alias in q_lower`` 做裸子串判断，导致「华北理工大学」因含
        子串「北理工」被解析成「北京理工大学」(10007)，于是双校对标把学员的
        备选院校静默替换成另一所层次完全不同的高校（10058 vs 10007、双非 vs
        985）。中文无词边界，故以"前缀对齐"为准。
        [P0 修复·2026-10-11] 另加 q 长度守卫：``alias.startswith(q_lower)``
        在 q 为单字符时同样会误命中（如 "h" → "hust"）。
        """
        alias_hits = []
        for alias, code in self._alias_map.items():
            if len(alias) < 2 or len(q_lower) < 2:
                continue
            if q_lower.startswith(alias) or alias.startswith(q_lower):
                alias_hits.append((len(alias), code))
        if alias_hits:
            alias_hits.sort(key=lambda t: -t[0])   # 最长别名优先，保证确定性
            return self._entities[alias_hits[0][1]]
        return None

    def _synthesize_or_none(self, q: str, q_lower: str) -> UniversityEntity | None:
        """步骤 6：启发式通用高校实体合成 (Universal Heuristic Entity Synthesizer)。

        支持全国任意非热门双非院校、地方工科/师范/财经本科高校。
        [P0 修复·2026-10-11] 长度收口：超长输入（2 万字垃圾串）不得被当成
        校名合成为实体——旧实现会把整串垃圾写入 name_map 并回填 _entities。
        最小长度守卫：q 至少 4 字符。纯关键词输入（"大学"/"学院"）本身不是
        校名，合成出 name="大学" 的实体毫无意义（实测 resolve("大学") → 合成
        实体「大学」）；最短的真实合成场景是「XX大学/XX学院」4 字形态。
        """
        if 4 <= len(q) <= _MAX_QUERY_LEN and any(
                keyword in q for keyword in ("大学", "学院", "学校", "研究院", "研究所", "中心")):
            synthetic_entity = synthesize_unlisted_school(q)
            if synthetic_entity:
                synthetic_entity.intro = compose_intro(synthetic_entity)
                # 动态回填映射，加速后续查询
                entity_key = synthetic_entity.chsi_code if (synthetic_entity.chsi_code and synthetic_entity.chsi_code != "待查") else f"UNLISTED_{synthetic_entity.name}"
                self._entities[entity_key] = synthetic_entity
                self._name_map[q_lower] = entity_key
                return synthetic_entity
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
