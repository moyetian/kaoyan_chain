# -*- coding: utf-8 -*-
"""
KaoYan Intelligence · 双校考研招考横向对比引擎 (School Comparator)

核心功能：
  1. 支持两所目标高校在同学科方向下的全维度横向对标 (ky compare <高校1> <高校2> [专业])
  2. 涵盖初试科目差异（如 408 统考 vs 自命题）、办学层次、历年复试线走势、一志愿保护机制对比
  3. 智能对比提炼两校相对竞争优势与避坑差异
  4. 支持终端格式化对比大盘与 Markdown 深度研报导出
"""

import json
from typing import Dict, Any, List, Optional
from pathlib import Path

from .models import UniversityEntity
from .registry import get_registry, resolve_university
from .chsi_connector import CHSIConnector

ROOT = Path(__file__).resolve().parent.parent.parent


class SchoolComparator:
    """双校招考横向对比分析器"""

    def __init__(self):
        self.registry = get_registry()
        self.chsi = CHSIConnector()

    def compare(
        self,
        school1_query: str,
        school2_query: str,
        major_keyword: str = "计算机",
        save_report: bool = False
    ) -> Dict[str, Any]:
        """
        对比两所高校在目标专业方向下的关键指标
        """
        entity1 = resolve_university(school1_query)
        entity2 = resolve_university(school2_query)

        name1 = entity1.name if entity1 else school1_query
        name2 = entity2.name if entity2 else school2_query

        # 尝试从内置权威数据库提取深度招考指标 (若有)
        info1 = self._get_school_profile(name1, entity1, major_keyword)
        info2 = self._get_school_profile(name2, entity2, major_keyword)

        # 自动对比分析
        diff_analysis = self._analyze_differences(name1, info1, name2, info2, major_keyword)

        # 格式化输出
        terminal_report = self._format_terminal_table(name1, info1, name2, info2, diff_analysis, major_keyword)
        markdown_report = self._format_markdown_report(name1, info1, name2, info2, diff_analysis, major_keyword)

        saved_path = None
        if save_report:
            out_dir = ROOT / "04-专业课"
            out_dir.mkdir(parents=True, exist_ok=True)
            clean_major = major_keyword.replace("/", "_").replace("\\", "_")
            out_path = out_dir / f"双校考情对比_{name1}_VS_{name2}_{clean_major}.md"
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(markdown_report)
            saved_path = str(out_path)

        return {
            "school1": name1,
            "school2": name2,
            "major": major_keyword,
            "profile1": info1,
            "profile2": info2,
            "analysis": diff_analysis,
            "terminal_report": terminal_report,
            "markdown_report": markdown_report,
            "saved_path": saved_path
        }

    def _get_school_profile(
        self,
        school_name: str,
        entity: Optional[UniversityEntity],
        major_keyword: str
    ) -> Dict[str, Any]:
        """提取高校综合考情画像"""
        try:
            from skills.school_scout import TARGET_SCHOOLS_DB
        except ImportError:
            try:
                from tools.skills.school_scout import TARGET_SCHOOLS_DB
            except ImportError:
                TARGET_SCHOOLS_DB = {}

        level = " / ".join(entity.level) if entity else "全国研招单位"
        region = entity.region if entity else "待查"
        chsi_code = entity.chsi_code if entity else "待查"
        official = entity.official_domain if entity else ""
        graduate = entity.graduate_domain if entity else ""

        # 检查是否命中内置 30+ 权威数据库
        db_item = TARGET_SCHOOLS_DB.get(school_name, {})
        dept_info = None
        if db_item and "pro_departments" in db_item:
            for k, v in db_item["pro_departments"].items():
                if major_keyword in k or k in major_keyword:
                    dept_info = v
                    break

        if dept_info:
            majors = dept_info.get("majors", [])
            catalog_source = "[OFFICIAL_VERIFIED 院校专栏实录]"
            score_trend = dept_info.get("score_trend", "参照国家线与校自划线")
            ratio = dept_info.get("ratio_quota", "以官方最终报录公示为准")
            protect = dept_info.get("protect_first", "遵循教育部统一录取规范")
            reputation = "；".join(dept_info.get("reputation", []))
            pitfalls = "；".join(dept_info.get("pitfalls", []))
        else:
            catalog_source = "[OFFLINE_BASELINE 离线通用基准]"
            majors = [f"[OFFLINE_BASELINE 离线通用基准] 以教育部 {major_keyword} 统考目录及自命题大纲为准"]
            score_trend = "未核验：请以该校当年研究生院复试线公示为准"
            ratio = "未核验：请以该校当年招生简章与录取公示为准"
            protect = "未核验：请以该校当年复试与录取细则为准"
            reputation = "未核验：当前仅生成院校查询入口，不代表学校或专业评价。"
            pitfalls = "请先核验官方招生简章、专业目录、复试细则与录取名单。"

        return {
            "name": school_name,
            "code": chsi_code,
            "level": level,
            "region": region,
            "official": official,
            "graduate": graduate,
            "majors": majors,
            "catalog_source": catalog_source,
            "score_trend": score_trend,
            "ratio": ratio,
            "protect": protect,
            "reputation": reputation,
            "pitfalls": pitfalls
        }

    def _analyze_differences(
        self,
        name1: str,
        info1: Dict[str, Any],
        name2: str,
        info2: Dict[str, Any],
        major: str
    ) -> Dict[str, Any]:
        """提炼两校竞争差异与决策建议"""
        # 1. 科目差异
        m1_str = " ".join(info1["majors"])
        m2_str = " ".join(info2["majors"])
        subject_diff = "两校初试科目相似"
        if "408" in m1_str and "408" not in m2_str:
            subject_diff = f"【{name1}】采用全国统考 408，【{name2}】包含专业自主命题"
        elif "408" in m2_str and "408" not in m1_str:
            subject_diff = f"【{name2}】采用全国统考 408，【{name1}】包含专业自主命题"
        elif "408" in m1_str and "408" in m2_str:
            subject_diff = "两校主流专硕/学硕均统一采用国家统考 408（复习通用度极高）"

        # 2. 地区与资源
        region_diff = f"【{name1}】位于 {info1['region']} ｜ 【{name2}】位于 {info2['region']}"

        # 3. 决策建议
        # [P0 修复] 建议此前无条件推荐「优先参考两校统考 408 对应方向」，
        # 对自命题考生（如院校自命题科目）有误导性；按学员档案动态调整措辞。
        try:
            _cfg = json.loads((ROOT / "ky_config.json").read_text(encoding="utf-8"))
            _pro_name_cfg = ((_cfg.get("study_plan") or {}).get("pro_name") or "").strip()
        except Exception:
            _pro_name_cfg = ""
        if "408" in _pro_name_cfg:
            _exam_tip = "若求备战通用性与规避自命题风险，可优先参考两校统考 408 对应方向"
        else:
            _exam_tip = f"学员专业课为「{_pro_name_cfg or '院校自命题'}」，请分别核验两校该科目大纲与参考书差异"
        recommendation = (
            f"{_exam_tip}；"
            f"若看重一志愿公平性，可结合两校保护机制（{name1}: {info1['protect']} ｜ {name2}: {info2['protect']}）做终极取舍。"
        )

        return {
            "subject_diff": subject_diff,
            "region_diff": region_diff,
            "recommendation": recommendation
        }

    def _format_terminal_table(
        self,
        name1: str,
        info1: Dict[str, Any],
        name2: str,
        info2: Dict[str, Any],
        analysis: Dict[str, Any],
        major: str
    ) -> str:
        """生成彩色终端对比大盘卡片"""
        col1_w = 12
        col2_w = 34
        col3_w = 34

        def _dw(s: str) -> int:
            """估算终端显示宽度：中日韩全角字符按 2 列计"""
            return sum(2 if ord(ch) > 127 else 1 for ch in s)

        def _pad(s: str, width: int) -> str:
            """按显示宽度右侧补空格"""
            gap = width - _dw(s)
            return s + " " * max(0, gap)

        def _col(text: str, width: int) -> str:
            """[P0 修复] 按显示宽度截断并对齐：旧实现按字符数 ljust/截断，
            中文单元格占位失准导致列错位与难看的半截省略号"""
            s = str(text or "")
            if _dw(s) <= width:
                return _pad(s, width)
            out, w = "", 0
            for ch in s:
                cw = 2 if ord(ch) > 127 else 1
                if w + cw > width - 3:
                    break
                out += ch
                w += cw
            return _pad(out + "...", width)

        lines = [
            f"\n=== ⚔️ 目标高校招考深度横向对比大盘 · 【{name1}】 VS 【{name2}】 ({major}) ===",
            "-" * 86,
            f"{_pad('对比维度', 12)} | {_pad(name1, 34)} | {_pad(name2, 34)}",
            "-" * 86,
            f"{_pad('教育部代码', 12)} | {_col(info1['code'], 34)} | {_col(info2['code'], 34)}",
            f"{_pad('所在城市', 12)} | {_col(info1['region'], 34)} | {_col(info2['region'], 34)}",
            f"{_pad('办学层次', 12)} | {_col(info1['level'], 34)} | {_col(info2['level'], 34)}",
            f"{_pad('数据源属性', 12)} | {_col(info1.get('catalog_source', ''), 34)} | {_col(info2.get('catalog_source', ''), 34)}",
            f"{_pad('初试科目特征', 12)} | {_col(info1['majors'][0], 34)} | {_col(info2['majors'][0], 34)}",
            f"{_pad('复试线走向', 12)} | {_col(info1['score_trend'], 34)} | {_col(info2['score_trend'], 34)}",
            f"{_pad('一志愿保护', 12)} | {_col(info1['protect'], 34)} | {_col(info2['protect'], 34)}",
            "-" * 86,
            f"💡 【初试差异】: {analysis['subject_diff']}",
            f"💡 【地区分布】: {analysis['region_diff']}",
            f"🎯 【私教择校建议】: {analysis['recommendation']}",
            "=" * 86 + "\n"
        ]
        return "\n".join(lines)

    def _format_markdown_report(
        self,
        name1: str,
        info1: Dict[str, Any],
        name2: str,
        info2: Dict[str, Any],
        analysis: Dict[str, Any],
        major: str
    ) -> str:
        """生成 Markdown 深度对比研报"""
        lines = [
            f"# ⚔️ 考研目标院校横向对比研报 · {name1} VS {name2} ({major})",
            f"> 深度对标办学层次、自划线特征、初试统考/自命题科目、近三年复试线、一志愿保护机制与备考风险",
            f"> ⚠️ 数据说明：复试线走势、报录比等来自本项目内置经验基准库（非实时抓取核验），仅作量级参考，务必以两校研究生院官方公示为准。",
            "",
            "## 📊 1. 关键招考指标横向对标矩阵",
            "| 招考对比维度 | " + name1 + " | " + name2 + " |",
            "|---|---|---|",
            f"| **教育部代码** | `{info1['code']}` | `{info2['code']}` |",
            f"| **所在地区** | {info1['region']} | {info2['region']} |",
            f"| **办学层次** | {info1['level']} | {info2['level']} |",
            f"| **专业库来源** | `{info1.get('catalog_source', '')}` | `{info2.get('catalog_source', '')}` |",
            f"| **复试分数线走势** | {info1['score_trend']} | {info2['score_trend']} |",
            f"| **招生规模与报录** | {info1['ratio']} | {info2['ratio']} |",
            f"| **一志愿保护机制** | {info1['protect']} | {info2['protect']} |",
            f"| **研究生院官网** | [{name1}研招]({info1['graduate']}) | [{name2}研招]({info2['graduate']}) |",
            "",
            "## 📝 2. 专业方向与初试科目对比",
            f"### 【{name1}】({major})",
        ]
        for m in info1["majors"]:
            lines.append(f"- {m}")
        lines.append(f"\n### 【{name2}】({major})")
        for m in info2["majors"]:
            lines.append(f"- {m}")

        lines.extend([
            "",
            "## 💡 3. 私教深度研判与择校处方",
            f"- **科目与复习通用性**：{analysis['subject_diff']}",
            f"- **就业区位与发展空间**：{analysis['region_diff']}",
            f"- **选校综合权衡**：{analysis['recommendation']}",
            "",
            "## ⚠️ 4. 双方核心避坑红黑榜",
            f"- **{name1} 警示**：{info1['pitfalls']}",
            f"- **{name2} 警示**：{info2['pitfalls']}",
            "",
            "---",
            f"> 💡 **KaoYan Intelligence 对比提示**：可根据自身当前数学与专业课摸底分数，在终端中让私教为你量身推荐更稳妥的冲刺院校。"
        ])
        return "\n".join(lines)


# 全局单例
_default_comparator = None

def get_school_comparator() -> SchoolComparator:
    global _default_comparator
    if _default_comparator is None:
        _default_comparator = SchoolComparator()
    return _default_comparator
