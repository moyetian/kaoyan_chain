# -*- coding: utf-8 -*-
"""
社媒经验档案：清洗、落盘与增量追加

[拆分] 原属 `school_scout.py`。这组函数（约 370 行）与「检索 + 研报合成」是两件
不同的事：前者管「把社媒内容洗成可信经验条目并归档」，后者管「拿到数据写研报」。
分开后 school_scout 回到 800 行硬红线以内，且这组逻辑可以脱离检索被单独调用/测试。

来源说明：条目来自知乎/B站/小红书/公众号等**用户生成内容**，权威度天然低于官方，
落盘时一律带来源标注，不得被当作官方数据引用（见 tools/search/source_registry.py
的 Source Quality ≠ Claim Correctness）。
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:  # 双导入路径兼容
    from ky_io import atomic_write_text
except ImportError:  # pragma: no cover
    from tools.ky_io import atomic_write_text  # type: ignore

_LOG = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_FILE = ROOT / "ky_config.json"


def apply_scout_to_config(school: str, major: str, metrics: Dict[str, Any] = None,
                          config_path: Optional[Path] = None) -> bool:
    """把侦察到的院校与专业信息同步回写至 ky_config.json。

    :param config_path: 显式指定配置文件；不传则用工作区的 ky_config.json。
        显式参数是为了让调用方（尤其是测试）能指定隔离路径，
        而不必去 patch 模块级全局变量 —— 那样一旦实现搬到别的模块就会失效。
    """
    target = Path(config_path) if config_path else CONFIG_FILE
    if not target.exists():
        return False
    try:
        cfg = json.loads(target.read_text(encoding="utf-8"))
        study_plan = cfg.setdefault("study_plan", {})
        study_plan["school"] = school
        if major:
            study_plan["major"] = major

        # 若识别到专业课科目，自动同步
        if metrics and metrics.get("subjects_hint"):
            for s in metrics["subjects_hint"]:
                if "408" in s:
                    cfg["pro_name"] = "408 计算机学科专业基础"
                    study_plan["pro_name"] = "408 计算机学科专业基础"
                    break
                elif "自命题" in s:
                    clean_name = s.replace("专业课自命题:", "").strip()
                    cfg["pro_name"] = clean_name
                    study_plan["pro_name"] = clean_name

        atomic_write_text(CONFIG_FILE, json.dumps(cfg, ensure_ascii=False, indent=2))
        return True
    except Exception:
        return False


def filter_community_experiences(social_data: Dict[str, Any], school: str = "", major: str = "") -> List[Dict[str, Any]]:
    """
    社媒经验贴智能降噪与置信度量化过滤引擎
    依据时间新鲜度 (近1-2年加权)、初试各科分数明细、真实就读/避坑特征，
    严密过滤商业卖课、淘宝代写、引流微信等低质噪声。
    
    返回按置信度从高到低排序的高价值真实经验条目。
    """
    if not isinstance(social_data, dict):
        return []

    school_lower = (school or "").strip().lower()
    major_lower = (major or "").strip().lower()

    raw_items = []
    platform_map = {
        "zhihu": "知乎",
        "bilibili": "哔哩哔哩",
        "xiaohongshu": "小红书"
    }
    for plat_key, plat_name in platform_map.items():
        items = social_data.get(plat_key, [])
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict):
                    raw_items.append({
                        "platform": plat_name,
                        "platform_key": plat_key,
                        "title": it.get("title", "").strip(),
                        "url": it.get("url", "").strip(),
                        "snippet": it.get("snippet", "").strip()
                    })

    if "items" in social_data and isinstance(social_data["items"], list):
        for it in social_data["items"]:
            if isinstance(it, dict):
                raw_items.append({
                    "platform": it.get("platform", "社交网络"),
                    "platform_key": it.get("platform_key", "custom"),
                    "title": it.get("title", "").strip(),
                    "url": it.get("url", "").strip(),
                    "snippet": it.get("snippet", "").strip()
                })

    SPAM_PATTERNS = [
        r"(?:加[vV微]|微信|VX|vx|咨询微信|私聊|私信|加群)[：:\s]*[a-zA-Z0-9_\-]+",
        r"(?:买资料|卖资料|出售资料|学姐资料|独家笔记|无偿分享|留邮箱|点赞送)",
        r"(?:淘宝|闲鱼|拼多多|转转|买课|报名咨询|保过|包过|内部渠道)",
        r"(?:代写|代做|接单|枪手|押题准|密卷)",
        r"(?:关注公众号|公众号后台回复)",
    ]

    SCORE_PATTERNS = [
        r"(?:初试|总分|初试成绩|考了|总成绩)[：:\s]*([34]\d{2})",
        r"(?:政治|英语|数学|专业课|408)[：:\s]*(\d{2,3})分?",
        r"(?:排名|专业第|第)(\d+)名",
        r"400\+?",
    ]

    RECENCY_PATTERNS = [
        (r"202[5-7]|2[5-7]考研", 20),
        (r"202[3-4]|2[3-4]考研", 15),
        (r"202[1-2]|2[1-2]考研", 5),
        (r"201\d|2020", -15),
    ]

    SUBSTANTIVE_KEYWORDS = [
        ("保护一志愿", 10), ("不歧视双非", 10), ("复试线", 8), ("差额复试", 8),
        ("调剂", 6), ("机试", 8), ("面试细节", 8), ("专业课难", 6), ("压分", 8),
        ("不压分", 8), ("真题风格", 6), ("导师评价", 6), ("就读体验", 6),
        ("学长建议", 5), ("踩坑", 6), ("避坑", 8), ("上岸", 5), ("复习规划", 5)
    ]

    filtered_results = []
    seen_urls = set()

    for item in raw_items:
        url = item["url"]
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)

        full_text = f"{item['title']} {item['snippet']}"
        full_text_lower = full_text.lower()

        score = 50.0
        tags = []
        is_spam = False

        # 1. 商业推销判定
        spam_hits = 0
        for sp in SPAM_PATTERNS:
            if re.search(sp, full_text, re.IGNORECASE):
                spam_hits += 1
        if spam_hits >= 1:
            score -= spam_hits * 35.0
            tags.append("含引流或营销嫌疑")
            is_spam = True

        # 2. 院校与专业匹配
        if school_lower:
            if school_lower in full_text_lower:
                score += 15.0
                tags.append("院校精确匹配")
            else:
                score -= 10.0

        if major_lower:
            if major_lower in full_text_lower:
                score += 10.0
                tags.append("专业精确匹配")

        # 3. 考研年份时效性
        recency_awarded = False
        for rp, delta in RECENCY_PATTERNS:
            if re.search(rp, full_text):
                score += delta
                if delta > 10:
                    tags.append("高时效近期贴")
                recency_awarded = True
                break
        if not recency_awarded:
            score -= 5.0

        # 4. 分数或排名明细
        score_hits = 0
        for sp in SCORE_PATTERNS:
            if re.search(sp, full_text):
                score_hits += 1
        if score_hits > 0:
            score += min(score_hits * 8.0, 20.0)
            tags.append("含分数或排名明细")

        # 5. 备考就读高频实质语义
        for kw, weight in SUBSTANTIVE_KEYWORDS:
            if kw in full_text:
                score += weight
                if kw in ("保护一志愿", "不歧视双非", "不压分"):
                    tags.append("正面口碑")
                elif kw in ("压分", "踩坑", "避坑", "差额复试"):
                    tags.append("避坑预警")
                elif kw in ("机试", "面试细节"):
                    tags.append("复试干货")

        score = max(0.0, min(100.0, round(score, 1)))

        if score >= 75.0:
            quality = "HIGH"
        elif score >= 50.0:
            quality = "MEDIUM"
        else:
            quality = "LOW_OR_SPAM"

        clean_snippet = item["snippet"] or item["title"]

        filtered_results.append({
            "platform": item["platform"],
            "title": item["title"],
            "url": item["url"],
            "snippet": clean_snippet,
            "confidence": score,
            "quality": quality,
            "is_spam": is_spam,
            "tags": list(dict.fromkeys(tags))
        })

    filtered_results.sort(key=lambda x: x["confidence"], reverse=True)
    return filtered_results


def save_experience_dossier(
    school: str,
    major: str,
    exp_data: List[Dict[str, Any]],
    metrics: Optional[Dict[str, Any]] = None,
    output_dir: Optional[Path] = None
) -> Path:
    """
    将经置信度过滤降噪后的真实考研经验与就读体验生成规范化档案，
    落盘至 .memory/experiences/<学校>_<专业>.md (本地隐私目录)
    """
    school = (school or "通用院校").strip()
    major = (major or "").strip()
    # [P0 修复] 默认落地到 .memory/experiences/ 目录，避免学员考情隐私泄露至 Pages
    out_dir = output_dir or (ROOT / ".memory" / "experiences")
    out_dir.mkdir(parents=True, exist_ok=True)

    file_stem = f"{school}_{major}" if major else school
    clean_stem = re.sub(r'[\\/:*?"<>|]', '_', file_stem)
    target_file = out_dir / f"{clean_stem}.md"

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    high_items = [x for x in exp_data if x.get("quality") == "HIGH"]
    med_items = [x for x in exp_data if x.get("quality") == "MEDIUM"]
    spam_count = sum(1 for x in exp_data if x.get("is_spam"))

    lines = [
        f"# 🎓 考研社媒真实经验与就读体验档案 · {school}" + (f" ({major})" if major else ""),
        "",
        f"> **生成时间**：`{now_str}`  ",
        f"> **数据沉淀**：本档案聚合知乎、哔哩哔哩、小红书等实名备考与就读评价，经 AI 降噪置信度过滤算法清洗。",
        "",
        "## 📊 1. 院校口碑与考情画像",
        f"- **目标高校**：`{school}`",
        f"- **办学层次**：`{metrics.get('level', '全国重点本科高校')}`",
        f"- **学科专业**：`{major if major else '未指定'}`",
        f"- **经验条目概况**：有效经验贴 `{len(high_items) + len(med_items)}` 篇 (高置信度 `{len(high_items)}` 篇，中置信度 `{len(med_items)}` 篇，已自动拦截营销中介 `{spam_count}` 篇)",
        "",
        "### 🟢 正向口碑与亮点信号",
    ]

    pos_signals = metrics.get("positive_signals", [])
    if pos_signals:
        for ps in pos_signals:
            lines.append(f"- ✅ **{ps}**")
    else:
        lines.append("- ✅ 历年录取出分公开，导师信息详实")

    lines.append("")
    lines.append("### 🔴 核心避坑防线与风险信号")
    risk_signals = metrics.get("risk_signals", [])
    if risk_signals:
        for rs in risk_signals:
            lines.append(f"- ⚠️ **{rs}**")
    else:
        lines.append("- ⚠️ 警惕复试自命题大纲调整与复试差额变动")

    lines.append("")
    lines.append("## 💡 2. 精选高置信度学长学姐实名经验 (Top Experiences)")

    top_list = (high_items + med_items)[:8]
    if top_list:
        for idx, it in enumerate(top_list, 1):
            tag_str = " ".join([f"`{t}`" for t in it.get("tags", [])])
            lines.append(f"### {idx}. [{it['platform']}] {it['title']} (置信度: {it['confidence']}分)")
            if tag_str:
                lines.append(f"> 标签特征: {tag_str}")
            lines.append(f"**核心摘录**：*“{it['snippet']}”*")
            if it.get("url"):
                lines.append(f"**原文链接**：[{it['url']}]({it['url']})")
            lines.append("")
    else:
        lines.append("> 暂无抓取到的社媒经验条目，请在有网络连接时执行 `ky fetch info` 检索或参考内置直达专题。")
        lines.append("")

    lines.append("## 🔗 3. 实名社媒讨论直通车 (持续追踪)")
    target_kw = f"{school} {major}".strip()
    zh_url = f"https://www.zhihu.com/search?type=content&q={urllib.parse.quote(target_kw + ' 考研 就读体验')}"
    bi_url = f"https://search.bilibili.com/all?keyword={urllib.parse.quote(target_kw + ' 考研 备考经验')}"
    xh_url = f"https://www.xiaohongshu.com/search_result?keyword={urllib.parse.quote(target_kw + ' 考研 避坑')}"
    lines.append(f"- 💡 [知乎 · {school} 考研就读体验真实讨论]({zh_url})")
    lines.append(f"- 📺 [B站 · {school} 考研备考经验与高分复盘]({bi_url})")
    lines.append(f"- 📕 [小红书 · {school} 考研避坑与初复试经验]({xh_url})")
    lines.append("")
    lines.append("---")
    lines.append("*本档案由考研学习链 (Kaoyan Study Chain) AI Intelligence 模块自动生成并持续维护。*")

    content = "\n".join(lines)
    atomic_write_text(target_file, content)
    return target_file


def append_experience_to_dossier(
    school_name: str,
    source_or_major: str = "微信公众号",
    author_or_info: Any = "",
    content: str = "",
    url: str = "",
    title: str = "",
    dossier_path: Optional[Path] = None
) -> bool:
    """向目标高校的经验档案 (.memory/experiences/<学校>.md 或指定路径) 追加一条经验"""
    try:
        source = "微信公众号"
        author = ""
        exp_content = content
        exp_url = url
        exp_title = title

        if isinstance(author_or_info, dict):
            # 支持传入字典结构
            exp_title = author_or_info.get("title", "") or exp_title
            author = author_or_info.get("source", "") or author_or_info.get("author", "")
            exp_url = author_or_info.get("url", "") or exp_url
            exp_content = author_or_info.get("content", "") or author_or_info.get("snippet", "") or exp_content
        else:
            source = source_or_major
            author = str(author_or_info)

        school = (school_name or "通用院校").strip()
        if dossier_path:
            target_file = Path(dossier_path)
            target_file.parent.mkdir(parents=True, exist_ok=True)
        else:
            # [P0 修复] 默认落地到 .memory/experiences/
            out_dir = ROOT / ".memory" / "experiences"
            out_dir.mkdir(parents=True, exist_ok=True)
            target_file = out_dir / f"{school}.md"
            old_file = ROOT / "docs" / "experiences" / f"{school}.md"
            if not target_file.exists() and old_file.exists():
                # [P0 修复] 旧目录文件一次性迁移到隐私目录后再追加，
                # 严禁继续向公开发布目录 (docs/) 写入任何内容
                try:
                    atomic_write_text(target_file,
                        old_file.read_text(encoding="utf-8"), encoding="utf-8"
                    )
                except OSError:
                    pass  # 迁移失败则按新建档案处理，不影响联动主流程

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        block = [
            f"\n### 📱 [{source}] {exp_title or '公众号精选备考经验'}",
            f"> **作者/来源**: {author or '公众号学长'} | **记录时间**: {now_str}",
            f"> **原文链接**: [{exp_url}]({exp_url})" if exp_url else "",
            f"\n{exp_content}\n"
        ]
        text_to_append = "\n".join([line for line in block if line])

        if target_file.exists():
            orig = target_file.read_text(encoding="utf-8")
            if "## 💡 2. 精选高置信度学长学姐实名经验" in orig:
                updated = orig.replace(
                    "## 💡 2. 精选高置信度学长学姐实名经验 (Top Experiences)",
                    "## 💡 2. 精选高置信度学长学姐实名经验 (Top Experiences)\n" + text_to_append
                )
                atomic_write_text(target_file, updated)
                return True
            else:
                atomic_write_text(target_file, orig + "\n" + text_to_append)
                return True
        else:
            header = f"# 🎓 考研社媒真实经验与就读体验档案 · {school}\n\n## 💡 2. 精选高置信度学长学姐实名经验 (Top Experiences)\n"
            atomic_write_text(target_file, header + text_to_append)
            return True
    except Exception:
        return False
