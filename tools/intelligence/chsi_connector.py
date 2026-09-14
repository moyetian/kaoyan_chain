# -*- coding: utf-8 -*-
"""
KaoYan Intelligence · 研招网 (CHSI) 官方专业目录连接器 (CHSI Connector)

研招网 (yz.chsi.com.cn) 为教育部全国硕士研究生统一招生官方信息公开平台，
其专业目录与招生单位数据由各高校官方上报，属于 S 级基准事实 (Ground Truth)。

核心功能：
  1. 标准化构造研招网硕士专业目录检索参数与 URL
  2. 提取初试统考科目、院系所分布与拟招人数
  3. 双轨高可用：在线 HTML 表格智能解析 + 权威标准科目离线知识库双重保障
  4. 产出最高优先级 (S 级) 的 EvidenceObject
"""

import re
import html
import urllib.request
import urllib.parse
from typing import List, Optional
from .models import EvidenceObject, current_exam_year
from .evidence_engine import build_evidence

CHSI_ZSML_URL = "https://yz.chsi.com.cn/zsml/queryAction.do"
CHSI_SCH_URL = "https://yz.chsi.com.cn/sch/search.do"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# 常见省市代码表
PROVINCE_CODES = {
    "北京": "11", "天津": "12", "河北": "13", "山西": "14", "内蒙古": "15",
    "辽宁": "21", "吉林": "22", "黑龙江": "23",
    "上海": "31", "江苏": "32", "浙江": "33", "安徽": "34", "福建": "35", "山东": "37",
    "河南": "41", "湖北": "42", "湖南": "43", "广东": "44", "广西": "45", "重庆": "50",
    "四川": "51", "陕西": "61", "甘肃": "62", "新疆": "65"
}

# 常见全国统考专业标准初试科目底座 (Ground Truth Base)
STANDARD_SUBJECTS_CATALOG = {
    "081200": {
        "name": "计算机科学与技术",
        "degree_type": "学硕",
        "common_subjects": ["(101)思想政治理论", "(201)英语(一)", "(301)数学(一)", "(408)计算机学科专业基础"],
        "national_standard": True
    },
    "085404": {
        "name": "计算机技术",
        "degree_type": "专硕",
        "common_subjects": ["(101)思想政治理论", "(204)英语(二)", "(302)数学(二)", "(408)计算机学科专业基础"],
        "national_standard": True
    },
    "085405": {
        "name": "软件工程",
        "degree_type": "专硕",
        "common_subjects": ["(101)思想政治理论", "(204)英语(二)", "(302)数学(二)", "(408)计算机学科专业基础"],
        "national_standard": True
    },
    "085412": {
        "name": "网络与信息安全",
        "degree_type": "专硕",
        "common_subjects": ["(101)思想政治理论", "(204)英语(二)", "(302)数学(二)", "(408)计算机学科专业基础"],
        "national_standard": True
    },
    "085400": {
        "name": "电子信息",
        "degree_type": "专硕",
        "common_subjects": ["(101)思想政治理论", "(204)英语(二)", "(302)数学(二)", "专业自命题或统考"],
        "national_standard": False
    },
    "085402": {
        "name": "通信工程",
        "degree_type": "专硕",
        "common_subjects": ["(101)思想政治理论", "(204)英语(二)", "(302)数学(二)", "专业自命题(信号与系统/通信原理)"],
        "national_standard": False
    },
    "085410": {
        "name": "人工智能",
        "degree_type": "专硕",
        "common_subjects": ["(101)思想政治理论", "(204)英语(二)", "(302)数学(二)", "专业自命题或统考(408)"],
        "national_standard": False
    },
    "080900": {
        "name": "电子科学与技术",
        "degree_type": "学硕",
        "common_subjects": ["(101)思想政治理论", "(201)英语(一)", "(301)数学(一)", "专业自命题"],
        "national_standard": False
    },
    "081000": {
        "name": "信息与通信工程",
        "degree_type": "学硕",
        "common_subjects": ["(101)思想政治理论", "(201)英语(一)", "(301)数学(一)", "专业自命题(信号与系统)"],
        "national_standard": False
    },
    "085401": {
        "name": "新一代电子信息技术",
        "degree_type": "专硕",
        "common_subjects": ["(101)思想政治理论", "(204)英语(二)", "(302)数学(二)", "专业自命题或统考"],
        "national_standard": False
    },
    "085409": {
        "name": "生物医学工程",
        "degree_type": "专硕",
        "common_subjects": ["(101)思想政治理论", "(204)英语(二)", "(302)数学(二)", "自命题专业综合"],
        "national_standard": False
    },
    "100200": {
        "name": "临床医学",
        "degree_type": "学硕",
        "common_subjects": ["(101)思想政治理论", "(201)英语(一)", "(306)临床医学综合能力(西医)"],
        "national_standard": True
    },
    "105100": {
        "name": "临床医学",
        "degree_type": "专硕",
        "common_subjects": ["(101)思想政治理论", "(201)英语(一)", "(306)临床医学综合能力(西医)"],
        "national_standard": True
    }
}


class CHSIConnector:
    """研招网数据连接与解析器"""

    def __init__(self, timeout: int = 5):
        self.timeout = timeout

    def build_catalog_url(
        self,
        school_name: str,
        major_keyword: Optional[str] = None,
        province: Optional[str] = None
    ) -> str:
        """
        生成研招网专业目录精确查询 URL
        """
        ssdm = ""
        if province:
            for p_name, p_code in PROVINCE_CODES.items():
                if p_name in province:
                    ssdm = p_code
                    break

        params = {
            "ssdm": ssdm,
            "dwmc": school_name,
            "mldm": "",
            "mlmc": "",
            "yjxkdm": "",
            "zymc": major_keyword or "",
            "xxfs": "1" # 全日制
        }
        return f"{CHSI_ZSML_URL}?{urllib.parse.urlencode(params)}"

    def query_catalog(
        self,
        school_name: str,
        major_keyword: Optional[str] = None,
        # [P0 修复] 默认参数改为运行时求值，避免跨年长驻进程把目标年份锁死在启动时刻
        target_year: Optional[int] = None
    ) -> List[EvidenceObject]:
        """
        查询研招网专业目录，并返回标准 EvidenceObject 列表
        采用网络抓取 + 离线权威基准双轨保障
        """
        target_year = target_year or current_exam_year()
        query_url = self.build_catalog_url(school_name, major_keyword)
        evidences: List[EvidenceObject] = []

        # 1. 优先尝试向研招网获取真实目录页面
        # 注：_fetch_chsi_html 使用系统默认 SSL 上下文，证书校验失败即抛异常回落
        # 离线基准，不存在"降级仍标 VERIFIED"的通道，故此处恒为完整核验。
        html_content = self._fetch_chsi_html(query_url)
        if html_content:
            parsed_items = self._parse_catalog_html(
                html_content, school_name, query_url, target_year, ssl_verified=True
            )
            if parsed_items:
                evidences.extend(parsed_items)

        # 2. 若研招网当期页面未渲染（或新一年目录未正式上线），利用离线标准库做 S 级基准兜底
        if not evidences:
            evidences = self._generate_ground_truth_evidences(school_name, major_keyword, query_url, target_year)

        return evidences

    def _fetch_chsi_html(self, url: str) -> Optional[str]:
        """安全请求研招网 HTML"""
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                    "Referer": "https://yz.chsi.com.cn/zsml/"
                }
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status == 200:
                    raw = resp.read()
                    return raw.decode("utf-8", errors="replace")
        except Exception:
            return None
        return None

    def _parse_catalog_html(
        self,
        html_text: str,
        school_name: str,
        source_url: str,
        target_year: int,
        ssl_verified: bool = True
    ) -> List[EvidenceObject]:
        """解析研招网目录表格"""
        evidences = []
        # 匹配研招网 table.ch-table 中的专业行
        # 表格列通常包含：招生单位, 院系所, 专业, 研究方向, 学习方式, 拟招人数等
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html_text, re.DOTALL | re.IGNORECASE)
        for row in rows:
            cols = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL | re.IGNORECASE)
            if len(cols) >= 6:
                clean_cols = [re.sub(r"<[^>]+>", "", c).strip() for c in cols]
                # 寻找专业代码与专业名称，例如 "085404 计算机技术"
                major_match = re.search(r"(\d{6})\s*([^\s<]+)", clean_cols[2] if len(clean_cols) > 2 else "")
                if major_match:
                    code, m_name = major_match.groups()
                    college = clean_cols[1] if len(clean_cols) > 1 else "相关学院"
                    
                    ev = build_evidence(
                        field_name="专业目录与统考初试科目",
                        value={
                            "school": school_name,
                            "college": college,
                            "major_code": code,
                            "major_name": m_name,
                            "direction": clean_cols[3] if len(clean_cols) > 3 else "不区分研究方向"
                        },
                        unit="条",
                        exam_year=target_year,
                        source_type="chsi",
                        source_name="中国研究生招生信息网 (研招网官方专业目录)",
                        source_url=source_url,
                        target_year=target_year,
                        ssl_verified=ssl_verified
                    )
                    evidences.append(ev)
        return evidences

    def _generate_ground_truth_evidences(
        self,
        school_name: str,
        major_keyword: Optional[str],
        source_url: str,
        target_year: int
    ) -> List[EvidenceObject]:
        """
        基于全国标准研招目录库，输出 S 级权威基准证据
        """
        evidences = []
        kw = (major_keyword or "").strip()

        # [P1 修复·兜底专业张冠李戴] 此前判据是 `kw in code or kw in info["name"]`，
        # 即要求「整个关键词」是目录代码/名称的子串。但真实查询词往往更长，如
        #   kw = "085400 医学电子信息工程（085400-01）"
        # 它既不是 "085400" 的子串、也不是 "电子信息" 的子串 → 匹配恒为空，
        # 于是落到下方硬编码兜底，把「计算机科学与技术 / 计算机技术」当成考生的
        # 电子信息专业输出（首轮验收中考生查 085400 却拿到 081200 的科目组合）。
        # 现改为「专业代码精确匹配 + 专业名双向包含」。
        #   [审查修正] 不做「前缀匹配」(nc.startswith(code))：短代码如 "0854" 会命中
        #   全部 0854xx 专业，属于误命中；只保留精确匹配（含 6 位归一后的精确匹配）。
        import re as _re
        # 抽取 4-6 位、且不与相邻数字/中文黏连的专业代码（避免把 "2027年" 之类误抽）
        codes_in_kw = _re.findall(r"(?<!\d)(\d{4,6})(?![\d])", kw)
        norm_codes = set()
        for c in codes_in_kw:
            norm_codes.add(c)
            if len(c) > 6:            # "085400-01" 抽到 085400/01 → 只留 6 位主体
                norm_codes.add(c[:6])

        matched_codes = []
        for code, info in STANDARD_SUBJECTS_CATALOG.items():
            hit = False
            if not kw:
                hit = True                                   # 无关键词 → 全量（保持原行为）
            elif code in norm_codes:
                hit = True                                   # 专业代码精确命中
            elif info["name"] and info["name"] in kw:
                hit = True                                   # 专业名 ⊂ 查询串
            if hit:
                matched_codes.append((code, info))

        if not matched_codes:
            # [P1 修复] 兜底不再硬编码「计算机」专业：如实告知未能匹配，并列出可供参考的
            # 电子信息大类底座位。宁可少给，也不把无关专业当成本考生专业。
            print(f"  [i] 未能从【{kw or '（空关键词）'}】匹配到标准专业目录项，"
                  f"以下提供电子信息大类通用底座供参考。")
            matched_codes = [("085400", STANDARD_SUBJECTS_CATALOG["085400"])]

        for code, info in matched_codes:
            # 1. 专业基本信息
            # 注意：此处是「联网失败时的离线兜底」，严禁伪造成 S 级研招网权威证据。
            # 来源名不拼接校名、级别降为 C、状态标为未核验，避免学员误当作官方核实数据。
            ev_sub = build_evidence(
                field_name=f"初试科目组合 ({code} {info['name']})",
                value=info["common_subjects"],
                unit="门",
                exam_year=target_year,
                source_type="offline_baseline",
                source_name="【离线基准·未联网核验】全国硕士研究生统考科目标准模板",
                source_url=source_url,
                target_year=target_year,
                extra_confidence_decay=0.1
            )
            evidences.append(ev_sub)

        return evidences
