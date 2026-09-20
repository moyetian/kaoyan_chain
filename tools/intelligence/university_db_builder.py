# -*- coding: utf-8 -*-
"""
KaoYan Intelligence · 全国高校数据库构建器 (National University DB Builder)

数据来源（全部为可核验的公开权威源，逐一记录于 data/universities/_sources/SOURCES.md）：
  1. 中国研究生招生信息网「院校库」  https://yz.chsi.com.cn/sch/
     → 全国 935 个硕士招生单位：名称、招生单位代码(dwdm)、所在地、主管部门、
       “双一流”/研究生院/自划线 特性、研招网院校直达页
  2. 研招网 2026 硕士专业目录（计算机类 408 逐校核验结果）
     → 389 校 1571 条「招生单位代码 + 专业 + 四科初试科目」
  3. xioajiumi/Chinese_Universities (MIT)
     → 582 所高校的英文校名、办学类型与**官网链接**（唯一可靠的官网域名来源）
  4. fjw345/Universities-in-China-Mainland
     → 31 省完整本科名单与 985/211/双一流/C9 标签，用于交叉校验
  5. ZsTs119/china-university-database（基于百度地图）
     → 6449 所高校的省/市/区，用于补全省市字段

设计原则（与 AGENTS.md 反幻觉纪律一致）：
  * 只写入真实抓取到的字段；抓不到的字段一律留空字符串，绝不推断填充；
  * 抓取产物先落 `_sources/` 原始快照，再由 merge 阶段生成最终库，全程可复现；
  * 合并阶段对同一高校采取「字段级填空」：标量补空缺、列表取并集，
    已有非空值不被覆盖，避免精品库被批量数据冲掉。

【已废弃的数据源 —— 研招网院校详情页】
  曾计划抓取 `schoolInfo--schId-X,categoryId-Y.dhtml` 的「院校简介/院系设置/联系办法」
  三栏目，从外链中推导官网/研究生院/研招办域名。2026-09-19 实测证伪：
  随机抽 60 所招生单位，**59 所返回的正文与北京大学完全相同**
  （外链恒为 pku.edu.cn / grs.pku.edu.cn / admission.pku.edu.cn / yjsy.bjmu.edu.cn），
  0 所返回该校自有域名 —— 该栏目对绝大多数学校是未填充状态，页面回落到北大样例模板。
  若照此入库，会把「官网 = https://pku.edu.cn」写进 900+ 所高校记录，
  比留空「待查」更具误导性。故该阶段整体删除，官网域名改由 xioajiumi 数据集提供，
  研究生院/研招办域名留空，交由联网 Agent 实时核查。

用法：
  py tools/intelligence/university_db_builder.py crawl-schools
  py tools/intelligence/university_db_builder.py merge
  py tools/intelligence/university_db_builder.py all
"""

from __future__ import annotations

import argparse
import html as _html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT / "data" / "universities"
SOURCES_DIR = DATA_DIR / "_sources"

CHSI_BASE = "https://yz.chsi.com.cn"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

#: 国家线二区（B 区）省份
B_ZONE_PROVINCES = ("内蒙古", "广西", "海南", "贵州", "云南", "西藏", "甘肃", "青海", "宁夏", "新疆")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def fetch(url: str, *, data: Optional[Dict[str, Any]] = None, retries: int = 3,
          timeout: int = 30) -> Optional[str]:
    """带重试与节流的 GET/POST。失败返回 None，绝不抛异常打断整批抓取。"""
    body = None
    headers = {
        "User-Agent": UA,
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded;charset=utf-8"
        headers["X-Requested-With"] = "XMLHttpRequest"
        headers["Referer"] = CHSI_BASE + "/zsml/"

    last_err: Optional[Exception] = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            for enc in ("utf-8", "gb18030"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 —— 网络异常一律重试，不中断整批
            last_err = exc
            time.sleep(0.8 * (attempt + 1))
    if last_err is not None:
        print(f"  [warn] 抓取失败 {url} :: {last_err}", file=sys.stderr)
    return None


def fetch_json(url: str, data: Dict[str, Any], retries: int = 3) -> Optional[dict]:
    txt = fetch(url, data=data, retries=retries)
    if not txt:
        return None
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        return None


def strip_tags(fragment: str) -> str:
    """HTML 片段转纯文本（保留可读换行）。"""
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", fragment)
    t = re.sub(r"(?s)<[^>]+>", "\n", t)
    t = _html.unescape(t)
    t = re.sub(r"[ \t\u3000]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n", t)
    return t.strip()


# ---------------------------------------------------------------------------
# 阶段 1：研招网院校库列表 → 全国硕士招生单位底座
# ---------------------------------------------------------------------------

_SCHOOL_ANCHOR_RE = re.compile(
    r'<a class="name js-yxk-yxmc[^"]*"\s+href="/sch/schoolInfo--schId-(?P<schid>\d+)\.dhtml"[^>]*>\s*(?P<name>[^<]+?)\s*</a>',
    re.S,
)
_ICONFONT_RE = re.compile(r'<i class="iconfont">.*?</i>', re.S)


def parse_school_list(page_html: str) -> List[Dict[str, Any]]:
    """解析院校库列表页，抽取单个招生单位的权威字段。

    列表页每个条目自带：校名、schId、所在地、主管部门、特性标签（双一流/研究生院/自划线），
    以及「网报公告」链接里的 ``dwdm``——即教育部 5 位招生单位代码。
    """
    out: List[Dict[str, Any]] = []
    # 以每个条目的校名锚点为界切块，块内即为该单位全部信息
    blocks = re.split(r'(?=<a class="name js-yxk-yxmc)', page_html)
    for block in blocks:
        anchor = _SCHOOL_ANCHOR_RE.search(block)
        if not anchor:
            continue
        name = _html.unescape(anchor.group("name")).strip()
        if not name:
            continue

        # 特性标签（双一流 等）
        tags = [strip_tags(x) for x in re.findall(r'<span class="sch-tag">(.*?)</span>', block, re.S)]
        tags = [t for t in tags if t]

        # 所在地 / 主管部门 / 研究生院 / 自划线
        region = ""
        authority = ""
        dept_m = re.search(r'<div class="sch-department">(.*?)</div>', block, re.S)
        if dept_m:
            dept_block = _ICONFONT_RE.sub("", dept_m.group(1))
            dept_lines = [ln.strip() for ln in strip_tags(dept_block).split("\n") if ln.strip()]
            if dept_lines:
                region = dept_lines[0]
            for idx, ln in enumerate(dept_lines):
                if "主管部门" in ln and idx + 1 < len(dept_lines):
                    authority = dept_lines[idx + 1]
                    break
            for flag in ("研究生院", "自划线"):
                if any(ln == flag for ln in dept_lines) and flag not in tags:
                    tags.append(flag)

        # 招生单位代码：网报公告链接里的 dwdm=10001
        dwdm = ""
        dm = re.search(r"/sswbgg/\?dwdm=(\d+)", block)
        if dm:
            dwdm = dm.group(1)

        out.append({
            "name": name,
            "sch_id": anchor.group("schid"),
            "dwdm": dwdm,
            "region": region,
            "authority": authority,
            "tags": tags,
            "source_url": f"{CHSI_BASE}/sch/schoolInfo--schId-{anchor.group('schid')}.dhtml",
        })
    return out


def crawl_schools(out_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """遍历院校库分页，落盘全国硕士招生单位底座。"""
    print("[阶段1] 抓取研招网院校库（全国硕士招生单位）…")
    results: List[Dict[str, Any]] = []
    seen: set = set()
    start = 0
    empty_streak = 0
    while start < 2000:
        url = f"{CHSI_BASE}/sch/?start={start}"
        page = fetch(url)
        if not page:
            empty_streak += 1
            if empty_streak >= 3:
                break
            start += 20
            continue
        items = parse_school_list(page)
        fresh = [it for it in items if it["sch_id"] not in seen]
        for it in fresh:
            seen.add(it["sch_id"])
        results.extend(fresh)
        if not items:
            empty_streak += 1
            if empty_streak >= 3:
                break
        else:
            empty_streak = 0
        if start % 100 == 0:
            print(f"  start={start:<5} 累计 {len(results)} 所")
        start += 20
        time.sleep(0.35)

    results.sort(key=lambda x: x["sch_id"])
    _write_json(out_path or (SOURCES_DIR / "chsi_schools.json"), {
        "source": "中国研究生招生信息网 院校库",
        "sourceUrl": f"{CHSI_BASE}/sch/",
        "syncedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "count": len(results),
        "schools": results,
    })
    print(f"[阶段1] 完成：{len(results)} 个招生单位")
    return results


# ---------------------------------------------------------------------------
# 阶段 2：已废弃 —— 研招网院校详情页无法提供真实的官网/研究生院域名
# ---------------------------------------------------------------------------
#
# 实测证据（2026-09-19）：随机抽 60 所招生单位，逐校抓取
#   /sch/schoolInfo--schId-<id>,categoryId-481342.dhtml（院校简介）
#   /sch/schoolInfo--schId-<id>,categoryId-481347.dhtml（院系设置）
#   /sch/schoolInfo--schId-<id>,categoryId-481381.dhtml（联系办法）
# 结果：59 所返回的正文与北京大学完全相同（外链恒为 pku.edu.cn /
# grs.pku.edu.cn / admission.pku.edu.cn / yjsy.bjmu.edu.cn），0 所返回该校自有域名，
# 1 所（天津地质研究院）无外链。即该栏目对绝大多数学校处于未填充状态，
# 页面回落到北大样例模板 —— 照此入库会把「官网=https://pku.edu.cn」写进 900+ 所高校，
# 比留空更具误导性。故整段删除，不再提供 crawl-details 子命令。
# 官网域名改由 xioajiumi/Chinese_Universities（MIT）的 official_link 提供；
# 研究生院 / 研招办域名一律留空，交由联网 Agent 实时核查。
#


# ---------------------------------------------------------------------------
# 阶段 3：已废弃 —— 研招网专业库分页需要登录态
# ---------------------------------------------------------------------------
#
# 实测证据（2026-09-19）：`POST /zsml/rs/zys.do` 带 `mldm=08`（工学）时，
# 首页正常返回 totalCount=727、totalPage=73，但 `curPage=2&start=10` 一律返回
#   {"msg":"请登录","msg2":true,"flag":...}
# 未登录态下换任何参数名（start / curPage / pages / pageCount / size）、
# 改 GET、改 pageSize=100/1000 均无效；pageSize 被服务端忽略，恒为 10 条。
# 即未登录最多只能拿到**每个学科门类首页的 10 个专业**（14 门类共 119 条），
# 而工学一门就有 727 个 —— 这样的"半张表"若当作专业代码全表入库，
# 会让人误以为数据完整，比不抓更危险。故整段删除，不再提供 crawl-majors 子命令。
# 408 计算机类的 1571 条逐校核验科目已由 chsi_408_offerings.json 单独提供，
# 该路径不受此限制。
#


# ---------------------------------------------------------------------------
# 阶段 4：合并生成最终高校库
# ---------------------------------------------------------------------------

def _load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _norm_name(name: str) -> str:
    return re.sub(r"[\s\u3000]+", "", name or "")


#: 视为「空值」的占位符，与 tools/intelligence/registry.py 的 _BLANK_TOKENS 保持一致
_BLANK_TOKENS = ("", "待查", "待核验", "全国", "未知", "None")


def _is_blank(value: Any) -> bool:
    """判断字段是否等价于「未填写」，用于填空式合并的空值判定。"""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() in _BLANK_TOKENS
    if isinstance(value, (list, dict, tuple)):
        return len(value) == 0
    return False


def _official_domain(link: str) -> str:
    """把 xioajiumi 的 `official_link` 规范成根地址（scheme://host），丢弃路径与查询。

    实测 582 条均为根地址；此处只做防御性归一，保证与既有精品库的书写风格一致
    （如 `https://www.henau.edu.cn`），避免同一字段出现两种形态。
    """
    u = (link or "").strip()
    if not u:
        return ""
    sp = urllib.parse.urlsplit(u)
    if not sp.netloc:
        return ""
    return f"{sp.scheme or 'https'}://{sp.netloc}"


def _region_display(province: str, city: str) -> str:
    """把「省份 + 城市」规范成既有库惯用的「省+市」展示形式（如 湖北武汉）。"""
    prov = (province or "").strip()
    city = (city or "").strip()
    if not prov:
        return city
    if not city:
        return prov
    short = city
    for suffix in ("市", "省", "地区", "自治州", "自治区", "特别行政区"):
        if short.endswith(suffix) and len(short) > len(suffix):
            short = short[: -len(suffix)]
            break
    if not short or short == prov or prov.startswith(short) or short.startswith(prov):
        return prov
    return f"{prov}{short}"


#: 办学层次标签的语义等价族：同一族内只保留最先出现（权威度最高）的那一条。
# 研招网院校库、fjw345 名单与人工精品库对同一事实的写法不同
# （「自划线」/「自划线高校」、「双一流」/「双一流A类」/「“双一流”建设高校」），
# 不去重就会出现「985 / 211 / 双一流A类 / 自划线 / 双一流建设高校 / 设研究生院 /
# 自划线高校 / 双一流 / …」这样的重复堆叠，看板与对标报告里尤其刺眼。
_LEVEL_FAMILIES = (
    ("双一流", "双一流"),
    ("自划线", "自划线"),
    ("研究生院", "研究生院"),
)


def _level_family(tag: str) -> str:
    t = (tag or "").replace("“", "").replace("”", "").replace('"', "").strip()
    for needle, family in _LEVEL_FAMILIES:
        if needle in t:
            return family
    return t


def _dedupe_levels(tags: List[str]) -> List[str]:
    """按语义族去重并保序（先出现者胜出）。"""
    seen: set = set()
    out: List[str] = []
    for tag in tags:
        fam = _level_family(tag)
        if not fam or fam in seen:
            continue
        seen.add(fam)
        out.append(tag)
    return out


def _level_tags(school: Dict[str, Any], extra: Dict[str, Any]) -> List[str]:
    """由已核验的结构化事实拼装办学层次标签，不做任何推断。"""
    levels: List[str] = []
    tags = school.get("tags") or []
    for t in tags:
        t = t.strip()
        if t == "“双一流”建设高校" or t == '"双一流"建设高校':
            levels.append("双一流建设高校")
        elif t == "研究生院":
            levels.append("设研究生院")
        elif t == "自划线":
            levels.append("自划线高校")
        elif t:
            levels.append(t)
    for t in extra.get("fjw_tags") or []:
        if t in ("C9", "985", "211", "双一流") and t not in levels:
            levels.append(t)
    if extra.get("graduate_school") and "设研究生院" not in levels:
        levels.append("设研究生院")
    if extra.get("self_marking") and "自划线高校" not in levels:
        levels.append("自划线高校")
    if extra.get("double_first_class") and "双一流建设高校" not in levels and "双一流" not in levels:
        levels.append("双一流建设高校")
    levels.append("硕士研究生招生单位")
    prov = extra.get("province") or ""
    if prov:
        levels.append("国家二区线高校 (B区)" if prov in B_ZONE_PROVINCES else "国家一区线高校 (A区)")
    return _dedupe_levels(levels)


def _write_sources_manifest(stats: Dict[str, Any], out_dir: Path) -> None:
    """生成数据源清单，保证每一所高校的每一类字段都能追溯到出处。"""
    schools_payload = _load_json(SOURCES_DIR / "chsi_schools.json", {}) or {}
    offerings = _load_json(SOURCES_DIR / "chsi_408_offerings.json", {}) or {}
    fjw = _load_json(SOURCES_DIR / "fjw_universities.json", {}) or {}
    xj = _load_json(SOURCES_DIR / "xioajiumi_universities.json", {}) or {}
    places = _load_json(SOURCES_DIR / "zsts_places.json", {}) or {}

    lines = [
        "# 全国高校数据库 · 数据源清单 (SOURCES)",
        "",
        "> 本文件由 `tools/intelligence/university_db_builder.py merge` 自动生成。",
        "> 所有字段均来自下列可核验来源，**没有任何一条由语言模型推断或生成**。",
        "",
        "## 一、字段 → 来源对照",
        "",
        "| 字段 | 来源 | 说明 |",
        "|---|---|---|",
        "| `chsi_code` | 研招网院校库「网报公告」链接 `dwdm=` | 教育部 5 位招生单位代码 |",
        "| `chsi_sch_id` / `chsi_url` | 研招网院校库 | 院校内部 ID 与直达页 |",
        "| `name` / `region` / `authority` | 研招网院校库 | 校名、所在地、主管部门 |",
        "| `level`（双一流/研究生院/自划线） | 研招网院校库标签 | 官方标注的院校特性 |",
        "| `level`（C9/985/211） | fjw345 高校名单 | 第三方整理的完整名单标签 |",
        "| `official_domain` | xioajiumi/Chinese_Universities (MIT) 的 `official_link` | 该校官网根地址，唯一域名来源 |",
        "| `graduate_domain` / `admission_domain` | 无（一律留空） | 研招网详情页实测为北大样例模板，不可用；交由联网 Agent 现查 |",
        "| `name_eng` / `type` | xioajiumi/Chinese_Universities (MIT) | 英文校名、办学类型 |",
        "| `province` / `city` | ZsTs119/china-university-database | 省市二级行政区 |",
        "| `departments[].subjects` | 研招网 2026 硕士专业目录（408 逐校核验） | 招生单位代码 + 学科专业 + 四科初试科目 |",
        "| `intro` | 本仓库 `compose_intro()` | 由上述已核验字段串联，字段缺失即省略 |",
        "",
        "## 二、原始快照",
        "",
        "| 快照文件 | 来源 | 同步时间 | 记录数 |",
        "|---|---|---|---|",
        f"| `chsi_schools.json` | {schools_payload.get('sourceUrl', '')} | {schools_payload.get('syncedAt', '')} | {schools_payload.get('count', 0)} |",
        f"| `chsi_408_offerings.json` | {offerings.get('sourceUrl', '')} | {offerings.get('syncedAt', '')} | {offerings.get('count', 0)} |",
        f"| `fjw_universities.json` | {fjw.get('sourceUrl', '')} | {fjw.get('syncedAt', '')} | 31 省级分区 |",
        f"| `xioajiumi_universities.json` | {xj.get('sourceUrl', '')}（{xj.get('license', '')}） | {xj.get('syncedAt', '')} | {xj.get('count', 0)} |",
        f"| `zsts_places.json` | {places.get('sourceUrl', '')} | {places.get('syncedAt', '')} | {places.get('count', 0)} |",
        "",
        "## 三、许可与合规",
        "",
        "- 研招网（`yz.chsi.com.cn`）数据由教育部学生服务与素质发展中心主办，属**公开的招生事实信息**，",
        "  本项目仅作个人备考查询用途，并在页面标注来源与抓取时间。",
        "- `xioajiumi/Chinese_Universities` 为 MIT 许可；`fjw345/Universities-in-China-Mainland` 未声明许可，",
        "  仅使用其公开名单中的 985/211/双一流 标签作为交叉校验。",
        "- 抓取行为遵守站点公开接口的常规频率（分页间隔 0.35s，详情页 6 并发）。",
        "",
        "## 四、本次合并统计",
        "",
        "```json",
        json.dumps(stats, ensure_ascii=False, indent=2),
        "```",
        "",
        "## 五、复现方式",
        "",
        "```bash",
        "py tools/intelligence/university_db_builder.py all",
        "```",
        "",
    ]
    (SOURCES_DIR / "SOURCES.md").write_text("\n".join(lines), encoding="utf-8")


def merge(out_dir: Optional[Path] = None) -> Dict[str, Any]:
    """把原始快照合并为 national_institutions.json 与 exam_subjects.json。"""
    out_dir = out_dir or DATA_DIR
    print("[阶段4] 合并生成最终高校数据库…")

    schools_payload = _load_json(SOURCES_DIR / "chsi_schools.json", {}) or {}
    fjw = _load_json(SOURCES_DIR / "fjw_universities.json", {}) or {}
    xj_payload = _load_json(SOURCES_DIR / "xioajiumi_universities.json", {}) or {}
    offerings = _load_json(SOURCES_DIR / "chsi_408_offerings.json", {}) or {}
    places_payload = _load_json(SOURCES_DIR / "zsts_places.json", {}) or {}
    places = {_norm_name(k): v for k, v in (places_payload.get("places") or {}).items()}

    schools = schools_payload.get("schools") or []

    # --- 第三方标签索引 ---
    # 注：fjw_universities.json 的外层是元信息（source/sourceUrl/syncedAt），
    # 真正的分省名单在 "provinces" 键下，形如 {省: {"all": [{"name","tags"}]}}。
    fjw_tags: Dict[str, List[str]] = {}
    fjw_province: Dict[str, str] = {}
    for prov, blk in (fjw.get("provinces") or {}).items():
        for it in (blk.get("all") or []):
            n = _norm_name(it.get("name", ""))
            if n:
                fjw_tags[n] = list(it.get("tags") or [])
                fjw_province.setdefault(n, prov)

    xj_index: Dict[str, Dict[str, Any]] = {}
    for it in (xj_payload.get("universities") or []):
        n = _norm_name(it.get("name", ""))
        if n:
            xj_index[n] = it

    # --- 408 核验数据：院校代码 → 科目 ---
    offerings_by_school: Dict[str, List[Dict[str, Any]]] = {}
    code_by_name: Dict[str, str] = {}
    for row in (offerings.get("items") or []):
        code = (row.get("schoolCode") or "").strip()
        nm = _norm_name(row.get("schoolName", ""))
        if not code:
            continue
        offerings_by_school.setdefault(code, []).append(row)
        code_by_name.setdefault(nm, code)

    records: Dict[str, Dict[str, Any]] = {}
    for s in schools:
        name = _norm_name(s["name"])
        prov = s.get("region") or fjw_province.get(name, "")
        code = (s.get("dwdm") or "").strip() or code_by_name.get(name, "")
        tags = fjw_tags.get(name, [])
        xj = xj_index.get(name) or {}

        extra = {
            "fjw_tags": tags,
            "province": prov,
            "graduate_school": "研究生院" in (s.get("tags") or []),
            "self_marking": "自划线" in (s.get("tags") or []),
            "double_first_class": any("双一流" in t for t in (s.get("tags") or [])),
        }

        rec: Dict[str, Any] = {
            "chsi_code": code,
            "chsi_sch_id": s["sch_id"],
            "name": s["name"],
            "aliases": [a for a in [code, s["sch_id"]] if a],
            "level": _level_tags(s, extra),
            "region": _region_display(prov, (places.get(name) or {}).get("city", "")),
            "province": prov,
            "city": (places.get(name) or {}).get("city", ""),
            "zone": "B区" if prov in B_ZONE_PROVINCES else "A区",
            "authority": s.get("authority") or "",
            "official_domain": _official_domain(xj.get("official_link", "")),
            "graduate_domain": "",
            "admission_domain": "",
            "chsi_url": s.get("source_url") or "",
            "departments": {},
        }
        if xj.get("name_eng"):
            rec["name_eng"] = xj["name_eng"]
        if xj.get("type"):
            rec["type"] = xj["type"]
        records[name] = rec

    # --- 并入 408 核验过的、不在研招网院校库名单中的单位 ---
    for code, rows in offerings_by_school.items():
        sample = rows[0]
        nm = _norm_name(sample.get("schoolName", ""))
        if not nm or nm in records:
            continue
        prov = sample.get("region") or ""
        records[nm] = {
            "chsi_code": code,
            "chsi_sch_id": "",
            "name": sample.get("schoolName", "").strip(),
            "aliases": [code],
            "level": [
                "硕士研究生招生单位",
                "国家二区线高校 (B区)" if prov in B_ZONE_PROVINCES else "国家一区线高校 (A区)",
            ],
            "region": prov,
            "province": prov,
            "city": "",
            "zone": "B区" if prov in B_ZONE_PROVINCES else "A区",
            "authority": "",
            "official_domain": "",
            "graduate_domain": "",
            "admission_domain": "",
            "chsi_url": "",
            "departments": {},
        }

    # --- 并入「其余本科高校」（fjw345 完整本科名单中未被研招网院校库收录者）---
    # 这些院校暂无硕士招生资格或尚未在研招网院校库登记，故不赋予任何研招相关标签，
    # 仅如实记录名称、省份、城市与 985/211/双一流 等已核验标签。
    extra_undergrad = 0
    for nm, tags in fjw_tags.items():
        if nm in records or "本科" not in tags:
            continue
        prov = fjw_province.get(nm, "")
        place = places.get(nm) or {}
        levels = [t for t in ("C9", "985", "211", "双一流") if t in tags]
        levels.append("本科院校")
        levels.append("未收录于研招网院校库 · 硕士招生资格待核验")
        if prov:
            levels.append("国家二区线高校 (B区)" if prov in B_ZONE_PROVINCES else "国家一区线高校 (A区)")
        xj = xj_index.get(nm) or {}
        rec = {
            "chsi_code": "",
            "chsi_sch_id": "",
            "name": nm,
            "aliases": [],
            "level": levels,
            "region": _region_display(prov, place.get("city", "")),
            "province": prov,
            "city": place.get("city", ""),
            "zone": "B区" if prov in B_ZONE_PROVINCES else "A区",
            "authority": "",
            "official_domain": _official_domain(xj.get("official_link", "")),
            "graduate_domain": "",
            "admission_domain": "",
            "chsi_url": "",
            "departments": {},
        }
        if xj.get("name_eng"):
            rec["name_eng"] = xj["name_eng"]
        if xj.get("type"):
            rec["type"] = xj["type"]
        records[nm] = rec
        extra_undergrad += 1

    # --- 挂载 408 科目到 departments ---
    # [键语义] departments 的键在全国库与精品库中统一为「学科/专业」而非「院系」：
    # registry.build_site_graph() 与 agentic_research 都以 discipline_keyword /
    # major_keyword 去匹配 dept_key（关键词「计算机」需命中「(081200)计算机科学与技术」）。
    # 实测 1571 条 408 记录中有 1124 条的院系名并不含专业名（如「信息与管理科学学院」），
    # 若以院系名为键，这些数据在消费侧永远匹配不上，等于白抓。
    # 同一专业跨多个院系（实测 205 组）合并为一条，院系名以「、」并列。
    subject_schools = 0
    for nm, rec in records.items():
        code = rec.get("chsi_code")
        if not code or code not in offerings_by_school:
            continue
        depts: Dict[str, Any] = {}
        for row in offerings_by_school[code]:
            major_code = (row.get("majorCode") or "").strip()
            major_name = (row.get("majorName") or "").strip()
            if not major_name:
                continue
            label = f"({major_code}){major_name}" if major_code else major_name
            entry = depts.setdefault(label, {
                "college_name": "",
                "college_domain": "",
                "default_majors": [],
                "subjects": [],
                "subject_verified_by": "研招网 2026 硕士专业目录（第四科=408 逐校核验）",
            })
            college = (row.get("collegeName") or "").strip()
            if college:
                names = [c for c in entry["college_name"].split("、") if c]
                if college not in names:
                    names.append(college)
                entry["college_name"] = "、".join(names)
            if major_code and major_code not in entry["default_majors"]:
                entry["default_majors"].append(major_code)
            for sub in (row.get("subjects") or []):
                sc = (sub.get("code") or "").strip()
                sn = (sub.get("name") or "").strip()
                if not sc and not sn:
                    continue
                item = f"({sc}){sn}" if sc else sn
                if item not in entry["subjects"]:
                    entry["subjects"].append(item)
        if depts:
            rec["departments"] = depts
            subject_schools += 1

    # --- 与既有手写条目填空式合并（手工条目权威度更高，非空值优先） ---
    # 权威度顺序：既有 national_institutions.json（人工逐条核验） > 本次批量抓取。
    # 标量：手工条目非空即覆盖批量值（如官网书写形态 http/https、城市写法）；
    # 列表：取并集，手工条目排在前面，两边的信息都不丢；
    # 院系：以手工条目为准，批量抓取只补手工没有的专业键。
    existing = _load_json(DATA_DIR / "national_institutions.json", {}) or {}
    preserved = 0
    enriched = 0
    for key, old in existing.items():
        if not isinstance(old, dict):
            continue
        nm = _norm_name(old.get("name") or key)
        target = records.get(nm)
        if target is None:
            # 既有条目不在本次抓取范围内（如科研院所别名），原样保留
            records[nm] = old
            preserved += 1
            continue
        touched = False
        for field, val in old.items():
            if field == "departments":
                continue
            if _is_blank(val):
                continue
            if isinstance(val, list):
                # 列表类字段（aliases / level / tags）取并集而非「非空即跳过」：
                # 批量抓取总会生成非空的 level（如「硕士研究生招生单位」），
                # 若沿用标量规则，精品库手工标注的办学层次会被整段静默覆盖。
                cur = target.get(field)
                if not isinstance(cur, list):
                    cur = []
                merged = list(val) + [x for x in cur if x not in val]
                if field == "level":
                    # level 需按语义族去重（「自划线」与「自划线高校」是同一事实），
                    # 否则手工库与批量库的写法差异会堆成一长串重复标签。
                    merged = _dedupe_levels(merged)
                if merged != cur:
                    target[field] = merged
                    touched = True
            elif target.get(field) != val:
                target[field] = val
                touched = True
        old_depts = old.get("departments") or {}
        if old_depts:
            merged = dict(target.get("departments") or {})
            for k, v in old_depts.items():
                if k not in merged:
                    merged[k] = v
                    touched = True
            target["departments"] = merged
        if touched:
            enriched += 1

    _write_json(out_dir / "national_institutions.json", records)

    # --- 408 科目独立文件（便于其他模块直接消费） ---
    subjects_doc = {
        "source": offerings.get("source") or "中国研究生招生信息网 2026 年硕士专业目录",
        "sourceUrl": offerings.get("sourceUrl") or f"{CHSI_BASE}/zsml/",
        "syncedAt": offerings.get("syncedAt") or time.strftime("%Y-%m-%dT%H:%M:%S"),
        "subjectCode": offerings.get("subjectCode") or "408",
        "coverage": offerings.get("coverage") or "",
        "schoolCount": offerings.get("schoolCount"),
        "count": offerings.get("count"),
        "items": offerings.get("items") or [],
    }
    _write_json(out_dir / "exam_subjects.json", subjects_doc)

    stats = {
        "schools": len(records),
        "with_code": sum(1 for r in records.values() if r.get("chsi_code")),
        "with_official": sum(1 for r in records.values() if r.get("official_domain")),
        "with_subjects": subject_schools,
        "preserved_manual": preserved,
        "enriched_manual": enriched,
    }
    print(f"[阶段4] 完成：{stats}")
    _write_sources_manifest(stats, out_dir)
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="全国高校数据库构建器")
    parser.add_argument(
        "command",
        choices=["crawl-schools", "merge", "all"],
        help="执行阶段",
    )
    args = parser.parse_args(argv)

    SOURCES_DIR.mkdir(parents=True, exist_ok=True)

    if args.command in ("crawl-schools", "all"):
        crawl_schools()
    if args.command in ("merge", "all"):
        merge()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
