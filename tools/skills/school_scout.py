# -*- coding: utf-8 -*-
"""
考研学习链 (Kaoyan AI Study Chain) · 目标高校与社媒情报侦察引擎 (School Scout)

核心功能：
  1. 官方研招情报侦察：定向检索研招网 (yz.chsi.com.cn) 与高校研究生院官网 (.edu.cn)
     - 招生简章、专业目录、自命题考试大纲、拟招人数、报录比、复试分数线
  2. 社交平台舆情与避坑侦察：知乎 (Zhihu)、哔哩哔哩 (Bilibili)、小红书 (Xiaohongshu)
     - 学长学姐真实就读体验、导师与实验室风评、有无压分、调剂歧视与复试避坑
  3. LLM 结构化情报研报提炼 (五维研报) 与离线降级卡片
  4. 备考闭环联动：支持 --save 保存到 04-专业课/目标院校情报.md，支持 --apply 同步到 ky_config.json
"""

import os
import sys
import json
import re
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from typing import Dict, Any, List, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_FILE = ROOT / "ky_config.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _clean_ddg_url(raw_url: str) -> str:
    """清洗 DuckDuckGo 重定向链接，获取真实目标网址"""
    if not raw_url:
        return ""
    if "uddg=" in raw_url:
        try:
            parsed = urllib.parse.urlparse(raw_url)
            params = urllib.parse.parse_qs(parsed.query)
            if "uddg" in params and params["uddg"]:
                return params["uddg"][0]
        except Exception:
            pass
    if raw_url.startswith("//"):
        return "https:" + raw_url
    return raw_url


def _clean_html_text(raw_html: str) -> str:
    """清理 HTML 标签与多余空白字符"""
    if not raw_html:
        return ""
    text = re.sub(r"<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", raw_html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-zA-Z0-9#]+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def raw_web_search(query: str, max_results: int = 5, timeout: int = 8) -> List[Dict[str, str]]:
    """
    轻量通用网页检索 (基于 DuckDuckGo HTML，零第三方依赖)
    返回字段: [{'title': '...', 'url': '...', 'snippet': '...'}]
    """
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
    headers = {"User-Agent": USER_AGENT}
    req = urllib.request.Request(url, headers=headers)
    results = []

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

            # DDG HTML 结果解析
            titles = re.findall(r'<a[^>]+class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>', html, re.DOTALL)
            snippets = re.findall(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)

            limit = min(len(titles), max_results)
            for i in range(limit):
                href, t_html = titles[i]
                t_clean = _clean_html_text(t_html)
                s_clean = _clean_html_text(snippets[i]) if i < len(snippets) else ""
                real_url = _clean_ddg_url(href)
                results.append({
                    "title": t_clean or f"检索结果 {i+1}",
                    "url": real_url,
                    "snippet": s_clean
                })
    except Exception:
        # 网络异常时返回空列表
        return []

    return results


def search_official_admissions(school: str, major: str = "", max_items: int = 5) -> List[Dict[str, str]]:
    """
    检索目标高校研招官方信息 (招生简章、专业目录、拟招人数、报录比、考试科目)
    优先命中 site:edu.cn 与 yz.chsi.com.cn
    """
    school = school.strip()
    major = major.strip()
    if not school:
        return []

    # 1. 官方招生简章与专业目录
    q1 = f'"{school}" {major} 硕士研究生 招生简章 OR 招生专业目录 site:edu.cn'
    items1 = raw_web_search(q1, max_results=max_items)

    # 2. 研招网或报录比/招生人数
    q2 = f'"{school}" {major} 硕士 拟招生人数 OR 报录比 OR 考试大纲'
    items2 = raw_web_search(q2, max_results=max_items)

    # 去重合并 (按 URL 或标题)
    seen_urls = set()
    combined = []
    for item in items1 + items2:
        u = item.get("url", "")
        if u and u in seen_urls:
            continue
        if u:
            seen_urls.add(u)
        combined.append(item)
        if len(combined) >= max_items:
            break

    # 若搜索无网络或未返回结果，提供权威官方直达链接
    if not combined:
        combined.append({
            "title": f"【官方直达】{school} 研究生招生信息网 / 研招网目录查询",
            "url": f"https://yz.chsi.com.cn/sch/search.do?ssdm=&yjsy=&xxmc={urllib.parse.quote(school)}",
            "snippet": f"可通过中国研究生招生信息网查询 {school} {major} 官方专业目录、初试自命题科目大纲及招生院系联系方式。"
        })

    return combined


def search_social_sentiment(school: str, major: str = "", max_per_platform: int = 3) -> Dict[str, List[Dict[str, str]]]:
    """
    检索社交平台 (知乎、B站、小红书) 上的学生评价、就读体验与避坑指南
    """
    school = school.strip()
    major = major.strip()
    target_str = f'"{school}"' + (f' "{major}"' if major else '')

    social_data = {
        "zhihu": [],
        "bilibili": [],
        "xiaohongshu": []
    }

    # 1. 知乎 (就读体验 / 导师红黑 / 专硕学硕待遇 / 调剂)
    q_zhihu = f'site:zhihu.com {target_str} (考研 OR 就读体验 OR 导师 OR 压分 OR 复试)'
    social_data["zhihu"] = raw_web_search(q_zhihu, max_results=max_per_platform)

    # 2. 哔哩哔哩 (备考经验贴 / 考情分析 / 调剂避坑)
    q_bili = f'site:bilibili.com {target_str} (考研 OR 经验贴 OR 备考 OR 复试线 OR 避坑)'
    social_data["bilibili"] = raw_web_search(q_bili, max_results=max_per_platform)

    # 3. 小红书 (避坑 / 报录比 / 调剂 / 歧视)
    q_xhs = f'site:xiaohongshu.com {target_str} (考研 OR 避坑 OR 复试 OR 调剂 OR 压分)'
    social_data["xiaohongshu"] = raw_web_search(q_xhs, max_results=max_per_platform)

    return social_data


def extract_key_metrics(school: str, major: str, official_items: List[Dict], social_items: Dict) -> Dict[str, Any]:
    """
    基于规则启发式提取核心招生指标与高频预警词
    """
    text_corpus = " ".join([it.get("title", "") + " " + it.get("snippet", "") for it in official_items])
    for plat_items in social_items.values():
        text_corpus += " " + " ".join([it.get("title", "") + " " + it.get("snippet", "") for it in plat_items])

    metrics = {
        "school": school,
        "major": major,
        "quota_hint": "",
        "subjects_hint": [],
        "risk_signals": [],
        "positive_signals": []
    }

    # 提取拟招人数模式 (如 "拟招 25 人", "招生人数 30", "计划招生 50")
    quota_match = re.findall(r"(?:拟招生?|招生总?人数?|计划招生)[：:\s]*(\d+)\s*人?", text_corpus)
    if quota_match:
        metrics["quota_hint"] = f"约 {quota_match[0]} 人 (来自检索线索，以最新研招网目录为准)"

    # 提取常见初试科目
    if "408" in text_corpus or "计算机学科专业基础" in text_corpus:
        metrics["subjects_hint"].append("408 计算机学科专业基础 (全国统考)")
    if "数学一" in text_corpus or "301" in text_corpus:
        metrics["subjects_hint"].append("数学一 (301)")
    elif "数学二" in text_corpus or "302" in text_corpus:
        metrics["subjects_hint"].append("数学二 (302)")
    if "英语一" in text_corpus or "201" in text_corpus:
        metrics["subjects_hint"].append("英语一 (201)")
    elif "英语二" in text_corpus or "204" in text_corpus:
        metrics["subjects_hint"].append("英语二 (204)")

    # 提取自命题代码 (8xx / 9xx)
    custom_sub = re.findall(r"(?:科目代码|\b)(8\d{2}|9\d{2})\b[^\n,，。]{0,15}", text_corpus)
    for cs in custom_sub[:2]:
        metrics["subjects_hint"].append(f"专业课自命题: {cs.strip()}")

    # 舆情风险词扫描 (避坑检测)
    risk_keywords = ["压分", "歧视", "不保护一志愿", "临时改大纲", "换专业课", "缩招", "差额比高", "复试晚", "导师push", "延毕"]
    for rk in risk_keywords:
        if rk in text_corpus and rk not in metrics["risk_signals"]:
            metrics["risk_signals"].append(rk)

    # 正向评价词扫描
    pos_keywords = ["保护一志愿", "复试公平", "盲审", "不看本科出身", "老师好", "奖学金丰厚", "就业好", "不压分"]
    for pk in pos_keywords:
        if pk in text_corpus and pk not in metrics["positive_signals"]:
            metrics["positive_signals"].append(pk)

    return metrics


def synthesize_report_with_llm(school: str, major: str, official_items: List[Dict], social_items: Dict, cfg: Dict = None) -> Optional[str]:
    """
    调用配置的大模型 API，将采集到的官方通知与社媒舆情提炼为五维深度考研情报研报
    """
    if cfg is None:
        try:
            import ky_cli
            cfg = ky_cli.load_config()
        except Exception:
            cfg = {}

    api_key = cfg.get("api_key", "").strip()
    base_url = cfg.get("base_url", "https://api.deepseek.com/v1").rstrip("/")
    model = cfg.get("model", "deepseek-chat")

    if not api_key:
        return None

    off_text = "\n".join([f"- [{it['title']}]({it['url']}): {it['snippet']}" for it in official_items])
    zhihu_text = "\n".join([f"- {it['title']}: {it['snippet']}" for it in social_items.get("zhihu", [])])
    bili_text = "\n".join([f"- {it['title']}: {it['snippet']}" for it in social_items.get("bilibili", [])])
    xhs_text = "\n".join([f"- {it['title']}: {it['snippet']}" for it in social_items.get("xiaohongshu", [])])

    system_prompt = (
        "你是一位深谙中国考研择校、考情分析与舆情避坑的资深考研规划专家。\n"
        "请根据学员提供的目标高校与搜索抓取到的官方及网络线索，生成一份客观、严谨、排版清晰的《目标院校考研深度情报分析研报》。\n"
        "研报必须包含以下 5 个核心章节：\n"
        "1. 📌【招考基本盘】：办学层次、院系专业、招生人数与推免预估、初试科目组合（标明统考/自命题代码）。\n"
        "2. 📊【竞争态势与分数线】：近年复试线特点、报录比热度、专硕/学硕分流情况、一志愿保护程度。\n"
        "3. 💬【网络口碑与就读体验】：知乎/B站/小红书学长学姐真实就读体验、科研氛围、实验室与导师梯队、就业去向。\n"
        "4. ⚠️【避坑红黑榜与核心警示】：有无压分传闻、是否卡本科双一流、复试差额比是否过高、专业课大纲变动风险。\n"
        "5. 🎯【私教复习战术建议】：针对该校特点，在数学、专业课或长难句上的复习时间分配与防翻车策略。\n"
        "注意：信息缺失处请客观提示‘以学校最新9月官方简章为准’，切忌胡编乱造虚假数据。"
    )

    user_prompt = f"""【目标院校】: {school}
【报考专业】: {major if major else "全专业/计算机/主流方向"}

【官方研招线索】:
{off_text}

【知乎讨论线索】:
{zhihu_text}

【哔哩哔哩经验线索】:
{bili_text}

【小红书避坑线索】:
{xhs_text}

请严格按上述 5 个章节输出完整的 Markdown 研报。"""

    req_body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.4,
        "max_tokens": 2500
    }

    try:
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": USER_AGENT
            },
            data=json.dumps(req_body).encode("utf-8")
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


def scout_school(school: str, major: str = "", include_social: bool = True, save_report: bool = False, apply_to_config: bool = False, use_llm: bool = True) -> Dict[str, Any]:
    """
    全流程执行目标高校研招与社媒情报侦察
    """
    school = school.strip()
    major = major.strip()
    if not school:
        return {"success": False, "message": "高校名称不能为空"}

    # 1. 检索官方招考信息
    official_data = search_official_admissions(school, major, max_items=5)

    # 2. 检索社媒评价 (如果启用)
    social_data = search_social_sentiment(school, major) if include_social else {"zhihu": [], "bilibili": [], "xiaohongshu": []}

    # 3. 提取关键指标与舆情关键词
    metrics = extract_key_metrics(school, major, official_data, social_data)

    # 4. 尝试通过大模型综合提炼
    llm_report = None
    if use_llm:
        llm_report = synthesize_report_with_llm(school, major, official_data, social_data)

    # 5. 生成完整报告文本
    formatted_report = format_scout_report({
        "school": school,
        "major": major,
        "official_data": official_data,
        "social_data": social_data,
        "metrics": metrics,
        "llm_report": llm_report
    })

    saved_path = None
    if save_report:
        pro_dir = ROOT / "04-专业课"
        pro_dir.mkdir(parents=True, exist_ok=True)
        safe_name = f"目标院校情报_{school}" + (f"_{major}" if major else "") + ".md"
        save_file = pro_dir / safe_name
        save_file.write_text(formatted_report, encoding="utf-8")
        saved_path = str(save_file)

    applied = False
    if apply_to_config:
        applied = apply_scout_to_config(school, major, metrics)

    return {
        "success": True,
        "school": school,
        "major": major,
        "official_data": official_data,
        "social_data": social_data,
        "metrics": metrics,
        "llm_report": llm_report,
        "formatted_report": formatted_report,
        "saved_path": saved_path,
        "applied": applied
    }


def format_scout_report(data: Dict[str, Any], use_color: bool = False) -> str:
    """
    格式化情报研报为 Markdown
    """
    school = data.get("school", "")
    major = data.get("major", "")
    metrics = data.get("metrics", {})
    llm_report = data.get("llm_report")
    official_data = data.get("official_data", [])
    social_data = data.get("social_data", {})

    if llm_report:
        source_links = ["\n---\n### 🔗 情报检索直达信源"]
        if official_data:
            source_links.append("\n**🏛️ 官方研招直达：**")
            for it in official_data:
                source_links.append(f"- [{it['title']}]({it['url']})")
        if social_data.get("zhihu"):
            source_links.append("\n**💡 知乎讨论：**")
            for it in social_data["zhihu"][:3]:
                source_links.append(f"- [{it['title']}]({it['url']})")
        if social_data.get("bilibili"):
            source_links.append("\n**📺 哔哩哔哩经验：**")
            for it in social_data["bilibili"][:3]:
                source_links.append(f"- [{it['title']}]({it['url']})")
        if social_data.get("xiaohongshu"):
            source_links.append("\n**📕 小红书避坑：**")
            for it in social_data["xiaohongshu"][:3]:
                source_links.append(f"- [{it['title']}]({it['url']})")
        return llm_report + "\n" + "\n".join(source_links)

    # 离线或无 API Key 降级卡片模式
    lines = []
    title_suffix = f" · {school}" + (f" {major}" if major else "")
    lines.append(f"# 🎯 目标院校考研情报侦察卡片{title_suffix}")
    lines.append("> 来源：高校研招网、中国研究生招生信息网、知乎、哔哩哔哩、小红书实名舆情")
    lines.append("")

    # 核心指标提炼
    lines.append("## 📊 核心招考指标透视")
    lines.append(f"- **目标高校**：`{school}`")
    lines.append(f"- **拟报专业**：`{major if major else '待选 / 计算机 / 主流方向'}`")
    if metrics.get("quota_hint"):
        lines.append(f"- **招生人数线索**：`{metrics['quota_hint']}`")
    if metrics.get("subjects_hint"):
        lines.append(f"- **初试科目线索**：`{', '.join(metrics['subjects_hint'])}`")
    if metrics.get("risk_signals"):
        lines.append(f"- **⚠️ 网络高频警示**：`{' / '.join(metrics['risk_signals'])}` (建议详查对应发帖)")
    if metrics.get("positive_signals"):
        lines.append(f"- **✅ 正向口碑信号**：`{' / '.join(metrics['positive_signals'])}`")
    lines.append("")

    # 官方研招列表
    lines.append("## 🏛️ 官方研招与招生简章直达")
    if official_data:
        for idx, item in enumerate(official_data, 1):
            lines.append(f"{idx}. **[{item['title']}]({item['url']})**")
            if item.get("snippet"):
                lines.append(f"   > {item['snippet'][:150]}...")
    else:
        lines.append("- 暂无检索记录，建议直接访问研招网 `yz.chsi.com.cn` 查询。")
    lines.append("")

    # 社交平台学生口碑
    lines.append("## 💬 学长学姐就读体验与避坑指南 (知乎 / B站 / 小红书)")

    # 知乎
    lines.append("### 💡 知乎 (Zhihu) 讨论与导师风评")
    if social_data.get("zhihu"):
        for it in social_data["zhihu"]:
            lines.append(f"- **[{it['title']}]({it['url']})**")
            if it.get("snippet"):
                lines.append(f"  *“{it['snippet'][:120]}...”*")
    else:
        lines.append("- 暂无直达知乎贴，可在知乎搜索框输入对应关键词查看。")
    lines.append("")

    # B站
    lines.append("### 📺 哔哩哔哩 (Bilibili) 备考经验贴")
    if social_data.get("bilibili"):
        for it in social_data["bilibili"]:
            lines.append(f"- **[{it['title']}]({it['url']})**")
            if it.get("snippet"):
                lines.append(f"  *“{it['snippet'][:120]}...”*")
    else:
        lines.append("- 暂无直达视频，可在 Bilibili 搜索考情分析。")
    lines.append("")

    # 小红书
    lines.append("### 📕 小红书 (Xiaohongshu) 避坑与考情")
    if social_data.get("xiaohongshu"):
        for it in social_data["xiaohongshu"]:
            lines.append(f"- **[{it['title']}]({it['url']})**")
            if it.get("snippet"):
                lines.append(f"  *“{it['snippet'][:120]}...”*")
    else:
        lines.append("- 暂无直达贴文，可在小红书搜索避坑经验。")
    lines.append("")

    lines.append("> 💡 **私教提示**：在 `ky config` 中配置 API Key 后，运行本指令可自动获得 AI 深度提炼的 5 维结构化战略研报。")
    return "\n".join(lines)


def apply_scout_to_config(school: str, major: str, metrics: Dict[str, Any] = None) -> bool:
    """
    一键将侦察到的院校与专业信息同步回写至本地 ky_config.json
    """
    if not CONFIG_FILE.exists():
        return False
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        study_plan = cfg.setdefault("study_plan", {})
        study_plan["school"] = school
        if major:
            study_plan["major"] = major

        # 若识别到专业课科目，自动同步
        if metrics and metrics.get("subjects_hint"):
            for s in metrics["subjects_hint"]:
                if "408" in s:
                    cfg["pro_name"] = "408 计算机学科专业基础"
                    break
                elif "自命题" in s:
                    cfg["pro_name"] = s.replace("专业课自命题:", "").strip()

        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False
