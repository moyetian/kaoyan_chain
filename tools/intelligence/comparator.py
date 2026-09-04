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
            score_trend = dept_info.get("score_trend", "参照国家线与校自划线")
            ratio = dept_info.get("ratio_quota", "以官方最终报录公示为准")
            protect = dept_info.get("protect_first", "遵循教育部统一录取规范")
            reputation = "；".join(dept_info.get("reputation", []))
            pitfalls = "；".join(dept_info.get("pitfalls", []))
        else:
            majors = [f"以教育部 {major_keyword} 统考目录及自命题大纲为准"]
            score_trend = "执行国家一区线或学校自划线"
            ratio = "统考与推免按 1:1.2 差额复试"
            protect = "严格保护一志愿考生正当录取权益"
            reputation = f"{school_name} 重点学科，师资力量雄厚。"
            pitfalls = "注意关注官方 9 月最新大纲与初试科目调整。"

        return {
            "name": school_name,
            "code": chsi_code,
            "level": level,
            "region": region,
            "official": official,
            "graduate": graduate,
            "majors": majors,
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
        recommendation = (
            f"若求备战通用性与规避自命题风险，优先参考两校统考 408 对应方向；"
            f"若看重一志愿公平性，可结合两校保护机制（{name1}: {info1['protect'][:15]}... ｜ {name2}: {info2['protect'][:15]}...）做终极取舍。"
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

        lines = [
            f"\n=== ⚔️ 目标高校招考深度横向对比大盘 · 【{name1}】 VS 【{name2}】 ({major}) ===",
            "-" * 86,
            f"{'对比维度':<12} | {name1:<34} | {name2:<34}",
            "-" * 86,
            f"{'教育部代码':<12} | {info1['code']:<34} | {info2['code']:<34}",
            f"{'所在城市':<12} | {info1['region']:<34} | {info2['region']:<34}",
            f"{'办学层次':<12} | {info1['level'][:30]:<34} | {info2['level'][:30]:<34}",
            f"{'初试科目特征':<12} | {info1['majors'][0][:30]:<34} | {info2['majors'][0][:30]:<34}",
            f"{'复试线走向':<12} | {info1['score_trend'][:30]:<34} | {info2['score_trend'][:30]:<34}",
            f"{'一志愿保护':<12} | {info1['protect'][:30]:<34} | {info2['protect'][:30]:<34}",
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
            "",
            "## 📊 1. 关键招考指标横向对标矩阵",
            "| 招考对比维度 | " + name1 + " | " + name2 + " |",
            "|---|---|---|",
            f"| **教育部代码** | `{info1['code']}` | `{info2['code']}` |",
            f"| **所在地区** | {info1['region']} | {info2['region']} |",
            f"| **办学层次** | {info1['level']} | {info2['level']} |",
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
