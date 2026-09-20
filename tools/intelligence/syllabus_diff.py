# -*- coding: utf-8 -*-
"""
KaoYan Intelligence · 官方大纲与自命题考点版本比对引擎 (Syllabus Diff Generator)

核心功能：
  1. 支持两份官方大纲（如 2026 vs 2027 考纲、旧大纲 vs 新简章）的逐级 AST 结构化解析
  2. 精确比对考点增删与考查级别变更：
     - [+] ADDED: 新增考点（红色高危预警，重点攻坚，必须补充真题与变式训练）
     - [-] REMOVED: 删减考点（绿色减负提示，彻底划掉，避免无谓时间消耗）
     - [~] MODIFIED: 考查要求调整（如从“了解”上升为“掌握”，或描述细化）
  3. 量化考纲变动率与稳定性指数 (Volatility Index)
  4. 自动生成考纲异动深度研报 (Markdown / 终端高亮展示)，指导后续派题与错题攻坚
"""

import re
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Tuple, Set
from pathlib import Path
from datetime import datetime

try:  # 双导入路径兼容（项目同时存在 tools.X 与 X 两种导入方式）
    from ky_io import atomic_write_text  # noqa: E402
except ImportError:  # pragma: no cover
    from tools.ky_io import atomic_write_text  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class SyllabusPoint:
    """原子考点对象"""
    module: str             # 所属模块 (如: 高等数学 / 数据结构)
    chapter: str            # 所属章节 (如: 1. 函数、极限、连续)
    requirement: str        # 考查要求 (掌握 / 理解 / 了解 / 熟练应用 / 灵活运用)
    text: str               # 考点具体文本
    raw_line: str = ""      # 原始大纲文本行


@dataclass
class DiffItem:
    """考点变动条目"""
    change_type: str        # ADDED (+), REMOVED (-), MODIFIED (~), UNCHANGED (=)
    point_new: Optional[SyllabusPoint] = None
    point_old: Optional[SyllabusPoint] = None
    detail: str = ""        # 变更说明 (如: 考查要求由 [了解] 提高为 [掌握])


class SyllabusDiffGenerator:
    """大纲考点版本比对引擎"""

    # 考查级别重要性排序
    REQUIREMENT_LEVELS = {
        "掌握": 3,
        "熟练应用": 3,
        "熟练掌握": 3,
        "熟练求解": 3,
        "灵活运用": 3,
        "理解": 2,
        "了解": 1,
        "会": 2,
        "能": 2,
    }

    def __init__(self):
        pass

    def clean_text(self, text: str) -> str:
        """清洗文本，统一标点与空白，剥离 Markdown 强调符与行尾掌握等级标签"""
        if not text:
            return ""
        t = text.strip()
        t = re.sub(r"\s+", " ", t)
        t = t.replace(";", "；").replace(",", "，")
        # 剥离 Markdown 强调/代码符号
        t = t.replace("**", "").replace("`", "")
        # 剥离行尾掌握等级标签（如「…… [掌握]」），等级由 requirement 字段承载
        t = re.sub(r"\s*[\[【](掌握|熟练应用|熟练掌握|熟练求解|灵活运用|理解|了解|会|能)[\]】]\s*$", "", t)
        return t.strip()

    def parse_syllabus(self, content: str) -> List[SyllabusPoint]:
        """
        将大纲 Markdown 文本解析为原子考点列表
        """
        points: List[SyllabusPoint] = []
        current_module = "核心考点"
        current_chapter = "未分类章节"
        # 负面清单章节（「绝不超纲」「不考 XXX」）不是正式考点，整段跳过
        skip_section = False

        lines = content.splitlines()
        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue

            # 匹配一级/二级模块标题: ## 一、高等数学 或 ## 线性代数
            m_module = re.match(r"^#{1,2}\s+(?:[一二三四五六七八九十]+[、\.\s]*)?([^#]+)$", line_str)
            if m_module and not line_str.startswith("###"):
                candidate = m_module.group(1).strip()
                # 负面清单 / 提示性标题：进入跳过模式，直到下一个有效标题为止
                # [说明文字误切] "三、2027届调整"这类考纲修订说明小节不是知识章节，
                # 其下"新增：…/剔除：无。"会被误切成考点，故一并跳过。
                if any(k in candidate for k in ["说明", "红线", "准则", "背景", "范围", "不考", "超纲", "参考书目",
                                                "调整", "新增", "剔除", "变动", "修订", "变化说明"]):
                    skip_section = True
                    continue
                skip_section = False
                current_module = re.sub(r"\(.*?\)|（.*?）", "", candidate).strip()
                current_chapter = "未分类章节"
                continue

            # 匹配三级/四级章节标题: ### 1. 函数、极限、连续
            m_chap = re.match(r"^#{3,4}\s+(?:第?[0-9一二三四五六七八九十]+[章讲节、\.\s]*)?([^#]+)$", line_str)
            if m_chap:
                candidate = m_chap.group(1).strip()
                if any(k in candidate for k in ["说明", "红线", "准则", "不考", "超纲",
                                                "调整", "新增", "剔除", "变动", "修订"]):
                    skip_section = True
                    continue
                skip_section = False
                current_chapter = candidate
                continue

            # 处于负面清单章节内：整行跳过
            if skip_section:
                continue

            # 匹配考点行: - **掌握**：... 或 * **掌握**：... 或 1. 掌握：...
            m_point = re.match(r"^[-*0-9\.\s]*\*{0,2}(掌握|熟练应用|熟练掌握|熟练求解|灵活运用|理解|了解|会|能)\*{0,2}\s*[：:]\s*(.+)$", line_str)
            if m_point:
                req = m_point.group(1).strip()
                body = m_point.group(2).strip()

                # 将逗号/分号/顿号分隔的多个子考点切开为原子考点
                sub_items = re.split(r"[；;。]+", body)
                for sub in sub_items:
                    sub = sub.strip()
                    if not sub:
                        continue
                    # [说明文字误切] 同 m_plain 分支：修订说明不是考点
                    _sub_head = re.split(r"[：:]", sub, maxsplit=1)[0].strip()
                    if _sub_head in ("新增", "剔除", "调整", "变动", "修订", "删除", "变化"):
                        continue
                    if sub in ("无", "无。"):
                        continue
                    terms = re.split(r"[、,，]+", sub)
                    if len(terms) <= 1:
                        p = SyllabusPoint(
                            module=current_module,
                            chapter=current_chapter,
                            requirement=req,
                            text=self.clean_text(sub),
                            raw_line=line_str
                        )
                        points.append(p)
                    else:
                        for term in terms:
                            term_clean = self.clean_text(term)
                            if len(term_clean) >= 2:
                                p = SyllabusPoint(
                                    module=current_module,
                                    chapter=current_chapter,
                                    requirement=req,
                                    text=term_clean,
                                    raw_line=line_str
                                )
                                points.append(p)
            else:
                # 兼容普通无前缀但属于列表的知识点行
                m_plain = re.match(r"^[-*]\s+(.+)$", line_str)
                if m_plain and not line_str.startswith("<!--"):
                    raw_text = m_plain.group(1).strip()
                    # [说明文字误切] "新增：现代新儒家…""剔除：无。"是修订说明，
                    # 不是考点（此前被当成"掌握"级考点计入动荡率）。冒号头为元词
                    # 或正文仅"无"时整行丢弃。
                    _meta_head = re.split(r"[：:]", raw_text, maxsplit=1)[0].replace("*", "").strip()
                    if _meta_head in ("新增", "剔除", "调整", "变动", "修订", "删除", "变化"):
                        continue
                    if raw_text.strip() in ("无", "无。", "无变化", "略"):
                        continue
                    # 行尾 [掌握]/[理解] 等等级标签优先作为考查要求
                    req_tag = None
                    tag_m = re.search(r"[\[【](掌握|熟练应用|熟练掌握|熟练求解|灵活运用|理解|了解|会|能)[\]】]\s*$", raw_text)
                    if tag_m:
                        req_tag = tag_m.group(1)
                        raw_text = raw_text[:tag_m.start()].strip()
                    if "：" in raw_text or ":" in raw_text:
                        parts = re.split(r"[：:]", raw_text, maxsplit=1)
                        maybe_req = parts[0].replace("*", "").strip()
                        maybe_body = parts[1].strip()
                        if maybe_req in self.REQUIREMENT_LEVELS:
                            p = SyllabusPoint(
                                module=current_module,
                                chapter=current_chapter,
                                requirement=maybe_req,
                                text=self.clean_text(maybe_body),
                                raw_line=line_str
                            )
                            points.append(p)
                            continue
                        # 加粗头是考点名而非等级：等级取行尾标签，文本保留「考点名：内容」
                        p = SyllabusPoint(
                            module=current_module,
                            chapter=current_chapter,
                            requirement=req_tag or "掌握",
                            text=self.clean_text(raw_text),
                            raw_line=line_str
                        )
                        points.append(p)
                        continue

                    p = SyllabusPoint(
                        module=current_module,
                        chapter=current_chapter,
                        requirement=req_tag or "掌握",
                        text=self.clean_text(raw_text),
                        raw_line=line_str
                    )
                    points.append(p)

        return points

    def _normalize_key(self, text: str) -> str:
        """归一化考点识别键 (去除标点、符号、LaTeX等以进行鲁棒匹配)"""
        t = re.sub(r"[\$\\\{\}\(\)（）\s、，。；;：:]+", "", text.lower())
        return t

    def compare_points(
        self,
        old_points: List[SyllabusPoint],
        new_points: List[SyllabusPoint]
    ) -> Tuple[List[DiffItem], Dict[str, Any]]:
        """
        对比两组原子考点并生成变动清单
        """
        diff_items: List[DiffItem] = []

        old_map: Dict[str, SyllabusPoint] = {}
        for p in old_points:
            k = self._normalize_key(p.text)
            if k and k not in old_map:
                old_map[k] = p

        new_map: Dict[str, SyllabusPoint] = {}
        for p in new_points:
            k = self._normalize_key(p.text)
            if k and k not in new_map:
                new_map[k] = p

        matched_old_keys: Set[str] = set()

        # 遍历新大纲，判定 ADDED / MODIFIED / UNCHANGED
        for k_new, p_new in new_map.items():
            if k_new in old_map:
                p_old = old_map[k_new]
                matched_old_keys.add(k_new)
                # 检查考查级别是否有变化
                old_level = self.REQUIREMENT_LEVELS.get(p_old.requirement, 2)
                new_level = self.REQUIREMENT_LEVELS.get(p_new.requirement, 2)

                if p_old.requirement != p_new.requirement:
                    direction = "提升" if new_level > old_level else "放宽"
                    diff_items.append(DiffItem(
                        change_type="MODIFIED",
                        point_new=p_new,
                        point_old=p_old,
                        detail=f"考查要求从【{p_old.requirement}】{direction}为【{p_new.requirement}】"
                    ))
                else:
                    diff_items.append(DiffItem(
                        change_type="UNCHANGED",
                        point_new=p_new,
                        point_old=p_old,
                        detail="考点要求与表述基本一致"
                    ))
            else:
                # 模糊子串匹配，防止由于微小字词扩充误报为全量新增
                fuzzy_match = None
                for k_old, p_old in old_map.items():
                    if k_old in matched_old_keys:
                        continue
                    if (len(k_old) >= 4 and k_old in k_new) or (len(k_new) >= 4 and k_new in k_old):
                        fuzzy_match = (k_old, p_old)
                        break

                if fuzzy_match:
                    k_old, p_old = fuzzy_match
                    matched_old_keys.add(k_old)
                    diff_items.append(DiffItem(
                        change_type="MODIFIED",
                        point_new=p_new,
                        point_old=p_old,
                        detail=f"考点表述调整: 原【{p_old.text}】-> 现【{p_new.text}】(要求: {p_new.requirement})"
                    ))
                else:
                    diff_items.append(DiffItem(
                        change_type="ADDED",
                        point_new=p_new,
                        point_old=None,
                        detail=f"【新增核心考点】要求: {p_new.requirement}，所属章节: {p_new.chapter}"
                    ))

        # 遍历旧大纲中未匹配的项，判定 REMOVED
        for k_old, p_old in old_map.items():
            if k_old not in matched_old_keys:
                diff_items.append(DiffItem(
                    change_type="REMOVED",
                    point_new=None,
                    point_old=p_old,
                    detail=f"【大纲已剔除】原要求: {p_old.requirement}，所属章节: {p_old.chapter}"
                ))

        # 计算统计量
        added_count = sum(1 for d in diff_items if d.change_type == "ADDED")
        removed_count = sum(1 for d in diff_items if d.change_type == "REMOVED")
        modified_count = sum(1 for d in diff_items if d.change_type == "MODIFIED")
        unchanged_count = sum(1 for d in diff_items if d.change_type == "UNCHANGED")
        total_old = len(old_points)
        total_new = len(new_points)

        if total_new == 0 and total_old == 0:
            volatility = 0.0
        elif total_new == 0 and total_old > 0:
            volatility = 100.0
        else:
            denominator = max(total_old, total_new, 1)
            volatility = min(100.0, round((added_count + removed_count + modified_count) / denominator * 100, 1))

        metrics = {
            "total_old": total_old,
            "total_new": total_new,
            "added_count": added_count,
            "removed_count": removed_count,
            "modified_count": modified_count,
            "unchanged_count": unchanged_count,
            "volatility_percentage": volatility,
            "stability_grade": "稳健微调" if volatility < 10 else ("中度改版" if volatility < 30 else "重大重构")
        }

        return diff_items, metrics

    def compare_texts(
        self,
        old_text: str,
        new_text: str,
        school: str = "全国统考/自命题",
        major: str = "目标科目",
        year_old: int = 2026,
        year_new: int = 2027,
        subject_name: str = ""
    ) -> Dict[str, Any]:
        """
        比对两份大纲文本并输出全套结构化研报数据
        """
        old_points = self.parse_syllabus(old_text)
        new_points = self.parse_syllabus(new_text)

        diff_items, metrics = self.compare_points(old_points, new_points)

        report_data = {
            "school": school,
            "major": major,
            "subject_name": subject_name or major,
            "year_old": year_old,
            "year_new": year_new,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "metrics": metrics,
            "diff_items": diff_items,
            "old_points": old_points,
            "new_points": new_points
        }

        return report_data

    def compare_files(
        self,
        old_file: Path,
        new_file: Path,
        school: str = "",
        major: str = "",
        year_old: int = 2026,
        year_new: int = 2027
    ) -> Dict[str, Any]:
        """
        比对两个大纲文件
        """
        p_old = Path(old_file)
        p_new = Path(new_file)
        if not p_old.exists():
            raise FileNotFoundError(f"基准大纲文件不存在: {old_file}")
        if not p_new.exists():
            raise FileNotFoundError(f"最新大纲文件不存在: {new_file}")

        text_old = p_old.read_text(encoding="utf-8", errors="ignore")
        text_new = p_new.read_text(encoding="utf-8", errors="ignore")

        return self.compare_texts(
            text_old,
            text_new,
            school=school or "目标院校",
            major=major or p_new.stem,
            year_old=year_old,
            year_new=year_new
        )

    def format_diff_markdown(self, report_data: Dict[str, Any]) -> str:
        """
        将比对结果格式化为高可读性的 Markdown 深度研报
        """
        m = report_data.get("metrics") or {
            "stability_grade": report_data.get("summary", "稳定"),
            "volatility_percentage": round(float(report_data.get("volatility", 0.0)) * 100, 1),
            "total_old": len(report_data.get("items", [])),
            "total_new": len(report_data.get("items", [])),
            "added_count": len(report_data.get("added", [])),
            "removed_count": len(report_data.get("removed", [])),
            "modified_count": len(report_data.get("modified", [])),
            "unchanged_count": len(report_data.get("items", [])),
        }
        diff_items: List[DiffItem] = report_data.get("diff_items", [])
        school = report_data.get("school", "全国统考")
        major = report_data.get("major", "专业课")
        y_old = report_data.get("year_old", 2026)
        y_new = report_data.get("year_new", 2027)

        added_items = [d for d in diff_items if d.change_type == "ADDED"]
        removed_items = [d for d in diff_items if d.change_type == "REMOVED"]
        modified_items = [d for d in diff_items if d.change_type == "MODIFIED"]

        lines = [
            f"# 📊 考研大纲考点版本比对研报 · {school} ({y_old} vs {y_new})",
            "",
            f"> **报告生成时间**: `{report_data.get('timestamp')}` | **对标科目**: `{major}` | **大纲变动定性**: `{m['stability_grade']} (波动率: {m['volatility_percentage']}%)`",
            "",
            "---",
            "",
            "## 一、核心变动量化全景看板",
            "",
            f"| 指标项 | {y_old}基准版 | {y_new}最新版 | 异动幅度 | 考情研判 |",
            "|---|---|---|---|---|",
            f"| **总考点数** | `{m['total_old']}` | `{m['total_new']}` | `{m['total_new'] - m['total_old']:+d}` | 大纲知识体量基本盘 |",
            f"| 🚨 **新增考点 (+)** | - | `{m['added_count']}` 处 | `{m['added_count']}` 项 | **今年必考高危预警，需专项补强** |",
            f"| 🍃 **剔除考点 (-)** | `{m['removed_count']}` 处 | - | `-{m['removed_count']}` 项 | **已划出考查范围，立即停止复习** |",
            f"| ⚠️ **要求调整 (~)** | - | `{m['modified_count']}` 处 | `{m['modified_count']}` 项 | **掌握级别或提法变更，需注意考法** |",
            f"| 🔒 **不变考点 (=)** | - | `{m['unchanged_count']}` 处 | 稳定盘 | 历年核心重点，维持常规进度 |",
            "",
            "---",
            "",
            "## 二、高危必看：新增考点清单与专项突破处方 (Added Points)",
            ""
        ]

        if added_items:
            lines.append("| 序号 | 所属模块 | 章节定位 | 考查级别 | 新增考点内容 | 私教应试处方与真题变式要求 |")
            lines.append("|---|---|---|---|---|---|")
            for idx, item in enumerate(added_items, 1):
                p = item.point_new
                lines.append(f"| {idx} | **{p.module}** | {p.chapter} | `<font color=red>**{p.requirement}**</font>` | **{p.text}** | 首年新增大概率出选择或基础大题，严防概念漏洞 |")
        else:
            lines.append("🎉 **本次考纲未见新增知识点，复习范围保持平稳！**")

        lines.extend([
            "",
            "---",
            "",
            "## 三、减负必看：剔除删减考点清单 (Removed Points)",
            ""
        ])

        if removed_items:
            lines.append("| 序号 | 原所属模块 | 原章节定位 | 原要求 | 剔除考点内容 | 备考避坑提示 |")
            lines.append("|---|---|---|---|---|---|")
            for idx, item in enumerate(removed_items, 1):
                p = item.point_old
                lines.append(f"| {idx} | ~{p.module}~ | ~{p.chapter}~ | ~{p.requirement}~ | ~~{p.text}~~ | **已彻底移出考纲，严禁在刷题中纠结** |")
        else:
            lines.append("📌 **本次考纲未剔除既有知识点，无考点瘦身。**")

        lines.extend([
            "",
            "---",
            "",
            "## 四、考法微调：考查要求变更清单 (Modified Points)",
            ""
        ])

        if modified_items:
            lines.append("| 序号 | 所属模块与章节 | 原要求与考点 | 新版要求与考点 | 考查等级异动与题型倾向 |")
            lines.append("|---|---|---|---|---|")
            for idx, item in enumerate(modified_items, 1):
                p_o = item.point_old
                p_n = item.point_new
                lines.append(f"| {idx} | **{p_n.module}** / {p_n.chapter} | [{p_o.requirement}] {p_o.text} | [{p_n.requirement}] **{p_n.text}** | {item.detail} |")
        else:
            lines.append("📌 **无考查要求升降级变动。**")

        # 生成第五部分：指导建议（优先大模型动态深度研判）
        custom_advice = None
        try:
            try:
                from tools.llm_client import is_llm_configured, chat_completion
            except ImportError:
                from llm_client import is_llm_configured, chat_completion
            if is_llm_configured(workspace_root=ROOT):
                added_summary = "、".join([it.point_new.text for it in added_items[:5]]) or "无新增"
                removed_summary = "、".join([it.point_old.text for it in removed_items[:5]]) or "无剔除"
                modified_summary = "、".join([f"{it.point_new.text}({it.detail})" for it in modified_items[:5]]) or "无微调"
                school = report_data.get("school", "目标院校")
                major = report_data.get("major", "专业")
                prompt = (
                    f"你是一位考研命题研究总教练。\n"
                    f"目标院校专业：{school} - {major}。\n"
                    f"大纲变动情况：\n"
                    f"- 新增考点：{added_summary}\n"
                    f"- 剔除考点：{removed_summary}\n"
                    f"- 级别微调考点：{modified_summary}\n"
                    f"请结合以上具体的考点变动，为考生输出 3-4 条极具战术针对性的备考执行建议（包括时间分配、变式练兵、规避无谓消耗与题型防范）。\n"
                    f"以编号列表形式输出，每条标出加粗核心观点，语气严谨专业，直接返回列表文字。"
                )
                llm_advice = chat_completion(prompt, workspace_root=ROOT, timeout=12.0)
                if llm_advice and len(llm_advice.strip()) > 30:
                    custom_advice = llm_advice.strip()
        except Exception:
            pass

        lines.extend([
            "",
            "---",
            "",
            "## 五、考研总教练战役执行指导建议 (Strategic Advice)",
            "",
        ])

        if custom_advice:
            lines.append(custom_advice)
        else:
            lines.extend([
                "1. **新增考点零遗漏**：大纲首次出现的新考点，命题组有极高概率在当年试卷中以客观题或送分小题的形式考察（以示大纲修订价值），必须本周内调用 `ky variant <考点>` 完成 3 道基础变式题练兵。",
                "2. **剔除考点立即止损**：在题集或错题本中遇到被剔除的考点，坚决不做、不背、不纠结，将节省出的宝贵时间倾斜至核心薄弱盘。",
                "3. **级别提升重点防范**：凡由“了解”上升为“掌握”的考点，题型极可能由选择题升格为推导证明或综合解答大题，需规范书写推导步骤。",
            ])

        lines.extend([
            "",
            "---",
            "*本研报由 考研学习链 (kaoyan_chain) · Syllabus Diff Generator 全自动比对生成，杜绝 AI 编造，严守官方大纲。*"
        ])

        return "\n".join(lines)

    def save_diff_report(
        self,
        report_data: Dict[str, Any],
        output_path: Optional[Path] = None
    ) -> Path:
        """将比对报告落盘为 Markdown 文件"""
        # [P1 修复·文件名不一致] 此前 school/major 只把空格换成下划线，
        # GUI 传入的 major 含全角括号（如「085400 医学电子信息工程（085400-01）」），
        # 生成的 md 文件名既过长又含全角符号，与 CLI（专业名取「814 信号与系统」）
        # 完全对不上，同一份考纲在两端口径下产出两个不同文件。
        # 现统一委托 ky_io.safe_filename 做安全化 + 截断，保证三端命名规则一致。
        try:
            from ky_io import safe_filename as _safe_fn
        except ImportError:  # pragma: no cover
            from tools.ky_io import safe_filename as _safe_fn
        school = _safe_fn(str(report_data.get("school", "全国统考")).strip() or "全国统考",
                          max_length=40)
        major = _safe_fn(str(report_data.get("major", "专业课")).strip() or "专业课",
                         max_length=60)
        # 进一步把全角括号内的方向后缀（如「085400-01」）与多余空格压平，
        # 避免同校同专业因写作「085400 医学电子信息工程（085400-01）」
        # 而生成超长且与专业代码口径不一致的文件名。
        major = re.sub(r"[（(][^）)]*[）)]", "", major)   # 去括号内容
        major = re.sub(r"\s+", "_", major).strip("_") or "专业课"
        y_new = report_data.get("year_new", 2027)

        # [P2 修复·演示产物污染正式研报] 无真实新考纲时 CLI/REPL 会跑内置演示样例，
        # 此前与正式研报同名同路径落盘（考纲变动分析_<校>_<专业>_<年>.md），
        # 演示数据（含计算机演示考点）会污染 04-专业课 备考资料并进入后续图谱/Diff 基准。
        # 现改为：演示模式强制写入独立目录「04-专业课/演示样例/」，文件名加显式前缀，
        # 且正文首行插入免责标注，确保与官方研报物理隔离、不可混淆。
        is_demo = bool(report_data.get("is_demo") or report_data.get("demo"))

        if not output_path:
            if is_demo:
                target_dir = ROOT / "04-专业课" / "演示样例"
            else:
                target_dir = ROOT / "04-专业课"
            if not target_dir.exists():
                target_dir.mkdir(parents=True, exist_ok=True)
            prefix = "[演示样例·非官方]" if is_demo else ""
            output_path = target_dir / f"{prefix}考纲变动分析_{school}_{major}_{y_new}.md"
        else:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

        md_content = self.format_diff_markdown(report_data)
        if is_demo:
            md_content = (
                "> ⚠️ **本文件为功能演示样例，非官方考纲，严禁作为备考依据。**\n"
                "> 数据来源：工具内置演示数据（未提供真实新考纲文件）。\n\n"
            ) + md_content
        atomic_write_text(output_path, md_content)
        return output_path


# 单例工厂
_generator_instance: Optional[SyllabusDiffGenerator] = None


def get_syllabus_diff_generator() -> SyllabusDiffGenerator:
    global _generator_instance
    if _generator_instance is None:
        _generator_instance = SyllabusDiffGenerator()
    return _generator_instance
