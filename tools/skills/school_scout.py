# -*- coding: utf-8 -*-
"""
考研学习链 (Kaoyan AI Study Chain) · 目标高校与社媒考研情报侦察引擎 (School Scout)

核心功能架构：
  1. 权威考情知识库 (Authoritative School DB)：
     内置全国 30+ 所 985/211 顶流名校深度招考情报（办学层次、院系代码、初试科目、复试线、
     报录比、一志愿保护机制、知乎/B站/小红书实名口碑、专硕学硕住宿政策与核心避坑红黑榜）。
  2. 全国高校通用智能推断引擎 (General School Inferencer)：
     覆盖全国所有高校与专业，自动识别办学层级、匹配研招网官方目录、专业统考代码与避坑战术。
  3. 三大社交平台精准直通车 (Direct Social Connectors)：
     一键直达知乎就读体验、B站备考经验、小红书避坑与压分排查专题。
  4. 动态多引擎网络抓取与容错 (Bing + DDG) & LLM 深度研报生成。
  5. 备考闭环联动：支持 --save 保存到 04-专业课/目标院校情报.md，支持 --apply 同步到 ky_config.json。
"""

import json
import logging
import re
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Dict, Any, List, Optional

try:  # 双导入路径兼容（项目同时存在 tools.X 与 X 两种导入方式）
    from ky_io import atomic_write_text  # noqa: E402
except ImportError:  # pragma: no cover
    from tools.ky_io import atomic_write_text  # noqa: E402

try:  # [B1 同类] LLM 请求经安全通道发送
    from net_guard import safe_urlopen  # noqa: E402
except ImportError:  # pragma: no cover
    from tools.net_guard import safe_urlopen  # type: ignore

try:  # 双导入路径兼容：高校情报库已从本文件拆分至独立模块 school_db.py
    from .school_db import TARGET_SCHOOLS_DB  # noqa: E402
except ImportError:  # pragma: no cover
    from school_db import TARGET_SCHOOLS_DB  # noqa: E402

# [拆分] 社媒经验档案逻辑已独立成模块（见 experience_dossier.py）；
# 此处重新导出，保证内部调用与 wechat_searcher 的既有引用零改动。
try:
    from .experience_dossier import (
        append_experience_to_dossier,
        apply_scout_to_config,
        filter_community_experiences,
        save_experience_dossier,
    )
except ImportError:  # pragma: no cover
    from experience_dossier import (  # type: ignore
        append_experience_to_dossier,
        apply_scout_to_config,
        filter_community_experiences,
        save_experience_dossier,
    )

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_FILE = ROOT / "ky_config.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ─────────────────────────────────────────────────────────────
# 1. 全国主流高校考研深度情报权威知识库 (Authoritative Knowledge Base)
#    数据源已拆分至独立模块 school_db.py（见文件顶部导入区）
# ─────────────────────────────────────────────────────────────



def raw_web_search(query: str, max_results: int = 5, timeout: int = 8) -> List[Dict[str, str]]:
    """通用网页检索（统一走 tools/search 的检索运行时）。

    [缺陷修复·返回无关内容] 旧实现自建一套 Bing→DuckDuckGo 抓取与解析。实测
    2026-09-16：Bing 对裸 urllib 请求返回 **HTTP 200 但内容完全无关**的页面
    （查询「南方医科大学 085409 招生」拿到的是 Glodon CAD 用户协议页），而
    HTML 结构正常、正则照样匹配出 10 条「结果」—— 于是这些垃圾被当成检索结果，
    直接污染下游的院校研报与 Agent 回答。

    现统一委托 `tools/search`：多源联邦 + 相关性守门（全不相关即判为失败并丢弃）
    + 来源权威度标注，且与 Agent 的 `web_search` 工具同源，两端行为一致。

    :param timeout: 保留仅为兼容既有调用点；实际超时由 provider 的 HTTP 层控制。
    """
    try:
        try:
            from search import SearchQuery, SearchService
        except ImportError:  # pragma: no cover
            from tools.search import SearchQuery, SearchService  # type: ignore

        response = SearchService.default().search(
            SearchQuery(text=str(query), limit=max(1, int(max_results or 5))))
        return [{"title": r.title, "url": r.url, "snippet": r.snippet}
                for r in response.results]
    except Exception as exc:                       # pragma: no cover - 兜底不阻断侦察
        logging.getLogger(__name__).warning("检索失败，返回空结果: %s", exc)
        return []


def find_school_in_db(school_name: str) -> Optional[Dict[str, Any]]:
    """
    在内置权威考情数据库中模糊匹配高校
    """
    s_clean = school_name.strip()
    if not s_clean:
        return None
    for name, data in TARGET_SCHOOLS_DB.items():
        if s_clean == name or s_clean in name or name in s_clean:
            return {"name": name, "data": data}
        for alias in data.get("alias", []):
            if s_clean.lower() == alias.lower():
                return {"name": name, "data": data}
    return None


def infer_general_school_intel(school: str, major: str = "") -> Dict[str, Any]:
    """
    通用高校考研情报智能推断器：对未在预置库中的高校进行画像推导
    """
    school = school.strip()
    major = (major or "").strip()

    # 判断办学层次推断
    level = "教育部直属 / 省属重点本科院校"
    region = "全国"
    official_site = f"https://yz.chsi.com.cn/sch/search.do?xxmc={urllib.parse.quote(school)}"
    try:
        from tools.intelligence.registry import get_registry
        ent = get_registry().resolve(school)
        if ent:
            level = " / ".join(ent.level) if isinstance(ent.level, list) else str(ent.level)
            region = ent.region
            official_site = ent.official_domain or ent.graduate_domain or official_site
    except Exception:
        pass

    top_985 = ["清华", "北大", "浙大", "复旦", "上交", "南大", "中科大", "人大学", "北航", "北理", "哈工大", "同济", "南开", "天津大学", "大连理工", "吉林大学", "东北大学", "华东师大", "东南大学", "中南大学", "湖南大学", "华南理工", "四川大学", "重庆大学", "电子科大", "西安交大", "西北工大", "兰州大学", "中国农大", "国防科大", "中山大学", "厦门大学", "山东大学", "海洋大学", "中国地大", "矿大", "石油大"]
    for k in top_985:
        if k in school:
            level = "985工程 / 211工程 / 双一流建设重点高校"
            break
    if "大学" in school and "211" not in level and "双一流" not in level and "骨干" not in level:
        level += "（硕士学位授权重点高校）"

    # 初试科目特征启发推导
    subjects = []
    if any(w in major for w in ("马克思主义理论", "0305", "思想政治", "马理论")):
        if "河南" in school:
            subjects.append("统考科目：101思想政治理论、201英语(一)")
            subjects.append("专业课：自命题科目1、自命题科目2（院校自命题）")
        elif "湖南" in school:
            subjects.append("统考科目：101思想政治理论、201英语(一)")
            subjects.append("专业课：622马克思主义基本原理、826中国化马克思主义理论与实践（院校自命题）")
        else:
            subjects.append("统考科目：101思想政治理论、201英语(一)")
            subjects.append("专业课：自命题业务课一(马克思主义基本原理)、自命题业务课二(中国化马克思主义理论与实践)")
    elif any(w in major for w in ("计算机", "软件", "网络", "信息安全", "人工智能", "大数据", "物联网")):
        subjects.append("统考科目：101思想政治理论、201英语(一)或204英语(二)、301数学(一)或302数学(二)")
        subjects.append("专业课：408计算机学科专业基础（全国统考）或院校自命题（如数据结构、操作系统、C/C++）")
    elif any(w in major for w in ("金融", "应用统计", "国际商务", "保险", "资产评估")):
        subjects.append("初试科目：政治、英语(二)、396经济类综合能力 / 数学(三)、专业课自命题 (如431金融学综合)")
    elif any(w in major for w in ("机械", "自动化", "电气", "通信", "土木")):
        subjects.append("初试科目：政治、英语(一/二)、数学(一/二)、专业课自命题 (如控制工程、电路、理论力学)")
    else:
        subjects.append("【待核验】未命中国家统考或大类专业特征，请核验该校研究生院当期公布的《硕士研究生招生专业目录》以锁定准确科目。")

    return {
        "level": level,
        "region": region,
        "official_site": official_site,
        "subjects": subjects,
        "reputation_summary": f"该校在【{major or '相关学科'}】方向具备扎实培养体系，历年毕业生主要面向本省及周边区域企事业单位。",
        "protect_first": "复试录取通常按教育部统一规程执行，建议密切关注目标院系官方复试细则中是否有校外调剂前科。",
        "pitfalls": [
            f"⚠️ 及时核对大纲变动：每年 9 月初务必第一时间核实 {school} 研究生院最新公布的《专业目录》，警惕自命题改考统考或参考书更换。",
            "⚠️ 紧盯复试差额比：复试比超过 1:1.5 的院校需格外防范初试高分滑铁卢，务必全力准备综合面试与专业课笔试。",
            "⚠️ 提前了解调剂与歧视：在考研论坛与知乎提前排查目标学院是否存在‘压一志愿给优质生源留调剂名额’的不良风评。"
        ]
    }


def search_official_admissions(school: str, major: str = "", max_items: int = 5) -> List[Dict[str, str]]:
    """
    检索目标高校研招官方信息 (官网招生简章、专业目录、研招网直达)
    """
    school = school.strip()
    major = major.strip()
    if not school:
        return []

    combined = []

    # 1. 尝试网络检索目标大学研究生院与招生官网
    q1 = f"{school} 研究生院 招生信息网"
    items_web = raw_web_search(q1, max_results=max_items)
    for it in items_web:
        t = it.get("title", "")
        u = it.get("url", "")
        if (school in t or (school in it.get("snippet", "") and ".edu.cn" in u)):
            # 排除同前缀其他高校噪点 (如搜索华中科技大学时混入华中师大)
            if any(other in t for other in ("师范", "农业", "地质") if other not in school):
                continue
            combined.append(it)

    # 2. 生成研招网（中国研究生招生信息网）权威精准直达链接
    chsi_sch_url = f"https://yz.chsi.com.cn/sch/search.do?ssdm=&yjsy=&xxmc={urllib.parse.quote(school)}"
    chsi_zsml_url = f"https://yz.chsi.com.cn/zsml/queryAction.do"
    
    combined.insert(0, {
        "title": f"【官方直达】{school} 研究生招生官方信息专页 (中国研究生招生信息网)",
        "url": chsi_sch_url,
        "snippet": f"教育部官方研招信息平台：查验 {school} 办学资质、硕士招生简章、院系代码与历年官方通告。"
    })
    combined.insert(1, {
        "title": f"【目录直达】全国硕士研究生招生专业目录查询系统 (教育部研招网)",
        "url": chsi_zsml_url,
        "snippet": f"精确按招生单位【{school}】与门类【{major if major else '工学/理学'}】查询最新拟招人数、初试科目代码、自命题大纲与研究方向。"
    })

    # 去重
    seen_urls = set()
    unique_items = []
    for it in combined:
        u = it.get("url", "")
        if u not in seen_urls:
            seen_urls.add(u)
            unique_items.append(it)
        if len(unique_items) >= max_items + 2:
            break

    return unique_items


def search_social_sentiment(school: str, major: str = "", max_per_platform: int = 3) -> Dict[str, List[Dict[str, str]]]:
    """
    检索社交平台 (知乎、B站、小红书) 上的学生评价、就读体验与避坑指南
    同时生成 100% 可用的实名讨论专区直通车
    """
    school = school.strip()
    major = major.strip()
    target_kw = f"{school} {major}".strip()

    social_data = {
        "zhihu": [],
        "bilibili": [],
        "xiaohongshu": [],
        "direct_links": {
            "zhihu_topic": f"https://www.zhihu.com/search?type=content&q={urllib.parse.quote(target_kw + ' 考研 就读体验')}",
            "bili_topic": f"https://search.bilibili.com/all?keyword={urllib.parse.quote(target_kw + ' 考研 备考经验')}",
            "xhs_topic": f"https://www.xiaohongshu.com/search_result?keyword={urllib.parse.quote(target_kw + ' 考研 避坑')}"
        }
    }

    # 尝试 Bing 定向搜索社交动态 (带严格域名与学校匹配过滤)
    try:
        items_zh = raw_web_search(f"{school} {major} 考研 就读体验 site:zhihu.com", max_results=max_per_platform)
        social_data["zhihu"] = [it for it in items_zh if "zhihu.com" in it.get("url", "") and (school in it.get("title", "") or school in it.get("snippet", ""))]
    except Exception:
        pass

    try:
        items_bi = raw_web_search(f"{school} {major} 考研 经验贴 site:bilibili.com", max_results=max_per_platform)
        social_data["bilibili"] = [it for it in items_bi if "bilibili.com" in it.get("url", "") and (school in it.get("title", "") or school in it.get("snippet", ""))]
    except Exception:
        pass

    try:
        items_xh = raw_web_search(f"{school} {major} 考研 避坑 site:xiaohongshu.com", max_results=max_per_platform)
        social_data["xiaohongshu"] = [it for it in items_xh if "xiaohongshu.com" in it.get("url", "") and (school in it.get("title", "") or school in it.get("snippet", ""))]
    except Exception:
        pass

    return social_data


def extract_key_metrics(school: str, major: str, official_items: List[Dict], social_items: Dict) -> Dict[str, Any]:
    """
    结合内置库与规则启发式，提取核心招生指标与高频预警词
    """
    matched = find_school_in_db(school)
    metrics = {
        "school": school,
        "major": major,
        "level": "未知",
        "quota_hint": "",
        "subjects_hint": [],
        "risk_signals": [],
        "positive_signals": []
    }

    if matched:
        s_data = matched["data"]
        metrics["school"] = matched["name"]
        metrics["level"] = s_data.get("level", "")
        pro_deps = s_data.get("pro_departments", {})
        # 查找匹配的专业方向
        dep_data = None
        if major:
            for k, v in pro_deps.items():
                if k in major or major in k:
                    dep_data = v
                    break

        if dep_data:
            metrics["quota_hint"] = dep_data.get("ratio_quota", "")
            metrics["subjects_hint"] = dep_data.get("majors", [])
            metrics["positive_signals"].append("业内公认高度保护一志愿")
            metrics["positive_signals"].append("复试公开透明、不歧视双非")
            metrics["risk_signals"].append("初试408/统考高分竞争激烈")
            metrics["risk_signals"].append("复试机试或专业面试有硬淘汰率")
        else:
            inferred = infer_general_school_intel(school, major)
            metrics["subjects_hint"] = inferred["subjects"]
            metrics["positive_signals"].append("正规教育部备案招生单位")
            metrics["risk_signals"].append("需在9月前防范自命题大纲更换")
            metrics["risk_signals"].append("需警惕复试差额过高或调剂占用")
    else:
        inferred = infer_general_school_intel(school, major)
        metrics["level"] = inferred["level"]
        metrics["subjects_hint"] = inferred["subjects"]
        metrics["positive_signals"].append("正规教育部备案招生单位")
        metrics["risk_signals"].append("需在9月前防范自命题大纲更换")
        metrics["risk_signals"].append("需警惕复试差额过高或调剂占用")

    # 扫描输入或检索出的正文语料进行动态增强
    text_corpus = " ".join([it.get("title", "") + " " + it.get("snippet", "") for it in official_items])
    for plat_items in social_items.values():
        if isinstance(plat_items, list):
            text_corpus += " " + " ".join([it.get("title", "") + " " + it.get("snippet", "") for it in plat_items])

    quota_match = re.findall(r"(?:拟招生?|招生总?人数?|计划招生)[：:\s]*(\d+)\s*人?", text_corpus)
    if quota_match:
        if not metrics["quota_hint"]:
            metrics["quota_hint"] = f"约 {quota_match[0]} 人 (来自官方简章线索)"
        else:
            metrics["quota_hint"] += f" (最新简章线索: 拟招 {quota_match[0]} 人)"

    risk_keywords = ["压分", "歧视", "不保护一志愿", "临时改大纲", "换专业课", "缩招", "差额比高", "复试晚", "导师push", "延毕"]
    for rk in risk_keywords:
        if rk in text_corpus and rk not in metrics["risk_signals"]:
            metrics["risk_signals"].append(rk)

    pos_keywords = ["保护一志愿", "复试公平", "盲审", "不看本科出身", "老师好", "奖学金丰厚", "就业好", "不压分"]
    for pk in pos_keywords:
        if pk in text_corpus and pk not in metrics["positive_signals"]:
            metrics["positive_signals"].append(pk)

    return metrics


def synthesize_report_with_llm(school: str, major: str, official_items: List[Dict], social_items: Dict, cfg: Dict = None) -> Optional[str]:
    """
    若配置了大模型 API，调用 LLM 进行五维深度研报提炼
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

    matched = find_school_in_db(school)
    db_context = json.dumps(matched["data"], ensure_ascii=False) if matched else "暂无该校内置档案"

    system_prompt = (
        "你是一位深谙中国考研择校、考情分析与舆情避坑的资深考研规划专家。\n"
        "请根据学员提供的目标高校与背景线索，生成一份客观、严谨、排版清晰的《目标院校考研深度情报分析研报》。\n"
        "研报必须包含以下 5 个核心章节：\n"
        "1. 📌【招考基本盘】：办学层次、院系专业、招生人数与推免预估、初试科目组合（标明统考/自命题代码）。\n"
        "2. 📊【竞争态势与分数线】：近年复试线特点、报录比热度、专硕/学硕分流情况、一志愿保护程度。\n"
        "3. 💬【网络口碑与就读体验】：知乎/B站/小红书学长学姐真实就读体验、科研氛围、实验室与导师梯队、就业去向。\n"
        "4. ⚠️【避坑红黑榜与核心警示】：有无压分传闻、是否卡本科双一流、复试差额比是否过高、专业课大纲变动风险。\n"
        "5. 🎯【私教复习战术建议】：针对该校特点，在数学、专业课或长难句上的复习时间分配与防翻车策略。\n"
        "注意：切忌捏造虚假未核验的数据，缺失处应指导学员如何精准核实。"
    )

    study_plan = (cfg or {}).get("study_plan", {})
    if study_plan.get("math_key") == "none" or "不考数学" in study_plan.get("math_name", ""):
        system_prompt += (
            "\n【学员学情约束】：学员当前备考科目为【不考数学】，"
            "严禁推荐高等数学、线性代数、概率论等数学复习计划或 408/算法题库刷题，应聚焦专业课与政英提分。"
        )

    user_prompt = f"""【目标院校】: {school}
【报考专业】: {major if major else "计算机/软件工程/主流方向"}

【权威知识库档案】:
{db_context}

请严格按上述 5 个章节输出完整的 Markdown 研报。"""

    req_body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.3,
        "max_tokens": 2500
    }

    try:
        try:
            from tools.agent.loop import normalize_openai_url
        except ImportError:
            from agent.loop import normalize_openai_url
    except ImportError:
        def normalize_openai_url(b: str, endpoint: str = "chat/completions") -> str:
            b = (b or "").strip().rstrip("/")
            if b.endswith("/chat/completions"):
                return b
            if b.endswith("/v1") or "/v1/" in b:
                return f"{b}/{endpoint.lstrip('/')}"
            return f"{b}/v1/{endpoint.lstrip('/')}"

    try:
        req = urllib.request.Request(
            normalize_openai_url(base_url, "chat/completions"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": USER_AGENT,
                "Connection": "close",
                "Accept-Encoding": "gzip, deflate, identity"
            },
            data=json.dumps(req_body).encode("utf-8")
        )
        # [B1 同类·跳转泄漏 Bearer] 安全通道发送。
        with safe_urlopen(req, timeout=60) as resp:
            raw = resp.read()
            headers = getattr(resp, "headers", None)
            enc = headers.get("Content-Encoding", "").lower() if headers and hasattr(headers, "get") else ""
            if enc == "gzip":
                import gzip
                try: raw = gzip.decompress(raw)
                except Exception: pass
            elif enc == "deflate":
                import zlib
                try: raw = zlib.decompress(raw)
                except Exception:
                    try: raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                    except Exception: pass
            data = json.loads(raw.decode("utf-8", errors="ignore"))
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

    # 1. 获取内置数据库或推导档案
    db_match = find_school_in_db(school)

    # 2. 检索官方招考入口与专业目录
    official_data = search_official_admissions(school, major, max_items=4)

    # 3. 聚合社媒评价与直通车专题链接
    social_data = search_social_sentiment(school, major) if include_social else {
        "zhihu": [], "bilibili": [], "xiaohongshu": [],
        "direct_links": {}
    }

    # 4. 提取核心指标与风险关键词
    metrics = extract_key_metrics(school, major, official_data, social_data)

    # 5. 尝试通过大模型综合提炼 (若配置了 API Key)
    llm_report = None
    if use_llm:
        llm_report = synthesize_report_with_llm(school, major, official_data, social_data)

    # 6. 生成完整报告文本 (保证即便无 API 也 100% 输出详尽硬核研报)
    formatted_report = format_scout_report({
        "school": school,
        "major": major,
        "db_match": db_match,
        "official_data": official_data,
        "social_data": social_data,
        "metrics": metrics,
        "llm_report": llm_report
    })

    # 7. 社媒经验降噪过滤
    filtered_experiences = filter_community_experiences(social_data, school, major)

    saved_path = None
    experience_dossier_path = None
    if save_report:
        pro_dir = ROOT / "04-专业课"
        pro_dir.mkdir(parents=True, exist_ok=True)
        safe_name = f"目标院校情报_{school}" + (f"_{major}" if major else "") + ".md"
        save_file = pro_dir / safe_name
        # [P0 修复] 与 admission(证据链版) 共用同名文件，写入前备份旧报告避免互相覆盖
        if save_file.exists():
            try:
                from syllabus_manager import backup_syllabus_file
            except Exception:
                try:
                    from tools.syllabus_manager import backup_syllabus_file
                except Exception:
                    backup_syllabus_file = None
            if backup_syllabus_file:
                backup_syllabus_file(save_file)
        atomic_write_text(save_file, formatted_report)
        saved_path = str(save_file)

        # 沉淀社媒真实经验档案至 docs/experiences/<学校>_<专业>.md
        dossier_file = save_experience_dossier(school, major, filtered_experiences, metrics)
        experience_dossier_path = str(dossier_file)

    applied = False
    if apply_to_config:
        applied = apply_scout_to_config(school, major, metrics)

    return {
        "success": True,
        "school": school,
        "major": major,
        "db_match": db_match,
        "official_data": official_data,
        "social_data": social_data,
        "metrics": metrics,
        "llm_report": llm_report,
        "formatted_report": formatted_report,
        "filtered_experiences": filtered_experiences,
        "saved_path": saved_path,
        "experience_dossier_path": experience_dossier_path,
        "applied": applied
    }


def format_scout_report(data: Dict[str, Any], use_color: bool = False) -> str:
    """
    格式化情报研报为内容详尽的结构化 Markdown
    """
    school = data.get("school", "")
    major = data.get("major", "")
    db_match = data.get("db_match")
    metrics = data.get("metrics", {})
    llm_report = data.get("llm_report")
    official_data = data.get("official_data", [])
    social_data = data.get("social_data", {})
    direct_links = social_data.get("direct_links", {})

    # 若已由大模型提炼出完整研报，优先呈现高密度研报并附上直达信源
    if llm_report:
        _auth_level = ""
        try:
            from tools.intelligence.registry import get_registry
            _ent = get_registry().resolve(school)
            if _ent:
                _lv = " / ".join(_ent.level) if isinstance(_ent.level, list) else str(_ent.level)
                _code = _ent.chsi_code if (_ent.chsi_code and _ent.chsi_code != "待查") else f"UNLISTED_{school}"
                _region = _ent.region
                if _lv:
                    _auth_level = f"> ✅ **权威核验（高校名录库）**：`{school}` 教育部代码 `{_code}`，办学层次 `{_lv}`，地区 `{_region}`。下文 LLM 研报中若出现不一致的层次表述，请以本行为准。\n\n"
        except Exception:
            _auth_level = ""
        links_block = [
            "\n---\n### 🔗 权威官方与实名社媒直通车",
            f"- 🏛️ [中国研究生招生信息网目录查询]({official_data[1]['url'] if len(official_data)>1 else 'https://yz.chsi.com.cn/zsml/queryAction.do'})",
            f"- 💡 [知乎 {school} {major} 就读体验专区]({direct_links.get('zhihu_topic', 'https://www.zhihu.com')})",
            f"- 📺 [B站 {school} {major} 高分备考经验贴]({direct_links.get('bili_topic', 'https://www.bilibili.com')})",
            f"- 📕 [小红书 {school} {major} 考研避坑专区]({direct_links.get('xhs_topic', 'https://www.xiaohongshu.com')})"
        ]
        return _auth_level + llm_report + "\n" + "\n".join(links_block)

    # 深度离线/无 API 架构卡片模式：绝不留空，全维度呈现干货
    lines = []
    title_suffix = f" · {school}" + (f" {major}" if major else "")
    lines.append(f"# 🎯 目标院校考研深度情报研报{title_suffix}")
    lines.append("> 汇集研招网官方目录、高校研究生院官网、知乎实名就读体验、B站高分复盘与小红书避坑数据")
    lines.append("> ⚠️ 数据说明：招生规模、复试线、报录比等指标来自本项目内置经验基准库（非实时核验），仅作量级参考，最终务必以院校研究生院官方公示为准。")
    lines.append("")

    # 1. 办学层次与核心指标透视
    lines.append("## 📊 1. 目标院校核心招考指标透视")
    lines.append(f"- **目标高校**：`{school}`")
    lines.append(f"- **办学层次**：`{metrics.get('level', '全国重点本科高校')}`")
    lines.append(f"- **拟报方向**：`{major if major else '计算机 / 软件工程 / 主流方向'}`")
    if metrics.get("quota_hint"):
        lines.append(f"- **招生规模与报录比**：`{metrics['quota_hint']}`")
    
    lines.append("- **初试科目组合与专业代码**：")
    if metrics.get("subjects_hint"):
        for sub in metrics["subjects_hint"]:
            lines.append(f"  • {sub}")
    else:
        lines.append("  • 统考或自命题（以教育部 9 月最新《专业目录》为准）")
    lines.append("")

    # 2. 深度考情分析 (若为内置高校，展开全量考情档案)
    if db_match:
        s_name = db_match["name"]
        s_data = db_match["data"]
        pro_deps = s_data.get("pro_departments", {})
        dep_data = None
        for k, v in pro_deps.items():
            if k in major or major in k:
                dep_data = v
                break
        if not dep_data and pro_deps:
            dep_data = list(pro_deps.values())[0]

        lines.append("## 📈 2. 历年竞争态势与复试线走向")
        if dep_data:
            if dep_data.get("score_trend"):
                lines.append(f"- **复试分数线特点**：{dep_data['score_trend']}")
            if dep_data.get("ratio_quota"):
                lines.append(f"- **统考名额与推免比例**：{dep_data['ratio_quota']}")
            if dep_data.get("protect_first"):
                lines.append(f"- **一志愿保护机制**：{dep_data['protect_first']}")
        lines.append("")

        lines.append("## 💬 3. 社交舆情、就读体验与培养质量 (知乎 / B站实名反馈)")
        if dep_data and dep_data.get("reputation"):
            for rep in dep_data["reputation"]:
                lines.append(f"- {rep}")
        else:
            lines.append(f"- {s_data.get('default_reputation', '学风扎实，学术科研声誉良好。')}")
        lines.append("")

        lines.append("## ⚠️ 4. 核心避坑红黑榜与关键警示 (重点防翻车)")
        if dep_data and dep_data.get("pitfalls"):
            for pit in dep_data["pitfalls"]:
                lines.append(f"- {pit}")
        else:
            lines.append(f"- {s_data.get('default_pitfalls', '注意复试差额与专业基础课深度提问。')}")
        lines.append("")
    else:
        # 通用推导展示
        inferred = infer_general_school_intel(school, major)
        lines.append("## 📈 2. 历年竞争态势与备战战术")
        lines.append(f"- **竞争格局**：{inferred['reputation_summary']}")
        lines.append(f"- **一志愿机制**：{inferred['protect_first']}")
        lines.append("")
        lines.append("## ⚠️ 3. 核心避坑红黑榜与关键警示")
        for pit in inferred["pitfalls"]:
            lines.append(f"- {pit}")
        lines.append("")

    # 3. 权威官方研招入口
    lines.append("## 🏛️ 官方研招与招生简章直达")
    if db_match and db_match["data"].get("official_site"):
        lines.append(f"1. **[{school} 研究生招生官方信息网]({db_match['data']['official_site']})**")
        lines.append(f"   > 高校官方研究生院入口：第一时间获取最新招生简章、自命题考试大纲及复试细则。")
    if official_data:
        for idx, item in enumerate(official_data, 2 if (db_match and db_match["data"].get("official_site")) else 1):
            lines.append(f"{idx}. **[{item['title']}]({item['url']})**")
            if item.get("snippet"):
                lines.append(f"   > {item['snippet'][:150]}")
    lines.append("")

    # 4. 实名社媒直通车与真实讨论抓取
    lines.append("## 🔗 实名社媒专题直通车 (知乎 / B站 / 小红书)")
    lines.append("> 点击下方直达专题链接，可一键跳转阅读对应高校学长学姐真实就读体验、导师评价与避坑避雷原帖：")
    lines.append(f"- 💡 **知乎讨论专区**：[{school} {major} 考研就读体验真实讨论]({direct_links.get('zhihu_topic', 'https://www.zhihu.com')})")
    lines.append(f"- 📺 **哔哩哔哩经验专区**：[{school} {major} 高分备考经验贴与真题复盘]({direct_links.get('bili_topic', 'https://www.bilibili.com')})")
    lines.append(f"- 📕 **小红书避坑专区**：[{school} {major} 考研避坑、压分与复试经验]({direct_links.get('xhs_topic', 'https://www.xiaohongshu.com')})")
    lines.append("")

    # 5. 若有动态搜索抓取到的社媒条目，追加展示
    has_social_snippets = False
    if social_data.get("zhihu"):
        has_social_snippets = True
        lines.append("### 💡 知乎精选讨论条目")
        for it in social_data["zhihu"]:
            lines.append(f"- **[{it['title']}]({it['url']})**")
            if it.get("snippet"):
                lines.append(f"  *“{it['snippet'][:120]}...”*")
        lines.append("")
    if social_data.get("bilibili"):
        has_social_snippets = True
        lines.append("### 📺 哔哩哔哩精选经验贴")
        for it in social_data["bilibili"]:
            lines.append(f"- **[{it['title']}]({it['url']})**")
            if it.get("snippet"):
                lines.append(f"  *“{it['snippet'][:120]}...”*")
        lines.append("")
    if social_data.get("xiaohongshu"):
        has_social_snippets = True
        lines.append("### 📕 小红书精选考情")
        for it in social_data["xiaohongshu"]:
            lines.append(f"- **[{it['title']}]({it['url']})**")
            if it.get("snippet"):
                lines.append(f"  *“{it['snippet'][:120]}...”*")
        lines.append("")

    lines.append("---")
    lines.append("> 💡 **私教战略提示**：在 `ky config` 中配置 API Key 后，私教可根据上述线索与你的复习现状，自动出具定制化的《个人攻坚时间表》。")
    return "\n".join(lines)





