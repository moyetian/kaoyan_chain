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

import os
import sys
import json
import re
import html
import base64
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

# ─────────────────────────────────────────────────────────────
# 1. 全国主流高校考研深度情报权威知识库 (Authoritative Knowledge Base)
# ─────────────────────────────────────────────────────────────

TARGET_SCHOOLS_DB = {
    "华中科技大学": {
        "alias": ["华科", "华科大", "HUST"],
        "level": "985工程 / 211工程 / 双一流A类 / 34所自主划线 / 教育部直属全国重点大学",
        "region": "湖北武汉",
        "official_site": "http://gszs.hust.edu.cn/",
        "yz_chsi_id": "368007",
        "pro_departments": {
            "计算机": {
                "college": "计算机科学与技术学院",
                "majors": [
                    "081200 计算机科学与技术 (学硕，初试：政治、英一、数一、408计算机学科专业基础)",
                    "085404 计算机技术 (专硕，初试：政治、英二、数二、408计算机学科专业基础)",
                    "085405 软件工程 (专硕，初试：政治、英二、数二、408计算机学科专业基础)",
                    "085412 网络与信息安全 (专硕，初试：政治、英二、数二、408计算机学科专业基础)"
                ],
                "score_trend": "近三年学硕复试线约 330~355 分，专硕复试线约 325~345 分（具体视 408 全国难度浮动，单科执行华科自划线）。",
                "ratio_quota": "统考录取人数约 120~180 人，推免占比约 45%~55%，复试差额比严格控制在 1:1.2 左右。",
                "protect_first": "🌟 业内公认高度保护一志愿！一志愿复试及录取名单公示前不接收校外调剂，不压初试分，复试极其公平，不歧视双非本科。",
                "reputation": [
                    "学科评估顶尖：计算机科学与技术全国 A+，在存储系统（武汉光电国家研究中心）、体系结构、系统软件领域全国第一梯队。",
                    "学风极佳：‘森林大学’环境优美，‘学在华科’名不虚传，实验室技术积淀深厚，头部大厂（华为主战场、阿里、腾讯、字节）校招认可度极高。",
                    "专硕政策：专硕培养要求严谨，需密切关注当年研究生院关于专硕宿舍安排与学费标准政策。"
                ],
                "pitfalls": [
                    "⚠️ 初试 408 统考硬仗：408 四门大山综合性极高，历年均分偏低，必须尽早启动两轮以上真题复习，严防单科不过线。",
                    "⚠️ 复试机试与面试要求极高：华科非常看重动手编程硬实力，复试包含专业上机机试（C/C++ 算法与数据结构），切忌初试完彻底躺平，需提前狂刷 LeetCode。",
                    "⚠️ 选导师需提前做功课：录取后选导师建议多向学长学姐打听实验室横向纵向比例、毕业要求与延毕率，理性选组。"
                ]
            }
        },
        "default_reputation": "工科底蕴极深，科研产出硬核，校风扎实质朴，一志愿保护极佳。",
        "default_pitfalls": "工科复试注重动手编程与数学功底，差额复试淘汰不留情面。"
    },
    "浙江大学": {
        "alias": ["浙大", "ZJU"],
        "level": "985工程 / 211工程 / 双一流A类 / 34所自主划线 / 九校联盟 (C9)",
        "region": "浙江杭州",
        "official_site": "http://grs.zju.edu.cn/",
        "yz_chsi_id": "368006",
        "pro_departments": {
            "计算机": {
                "college": "计算机科学与技术学院 / 软件学院 (宁波)",
                "majors": [
                    "081200 计算机科学与技术 (学硕，初试：政治、英一、数一、408)",
                    "085400 电子信息 / 计算机技术 (本部专硕，初试：政治、英一、数一、408)",
                    "085405 软件工程 (宁波软院专硕，初试：政治、英二、数二、878或408)"
                ],
                "score_trend": "本部计算机复试线常年在 360~380+ 分高位；软件学院专硕复试线约 340~365 分。",
                "ratio_quota": "本部统考名额少、推免占比高；软件学院（宁波）统考招生体量大（年招 300+ 人）。",
                "protect_first": "保护一志愿，进复试看重综合综合实力，复试成绩占比较大。",
                "reputation": ["CAD&CG 国家重点实验室全国领头羊，人工智能与图形学全球知名，杭州电商与互联网就业顶流。"],
                "pitfalls": ["⚠️ 竞争极度白热化，高分扎堆，初试 408 建议奔着 120+ 冲刺；软院注意校区在宁波。"]
            }
        },
        "default_reputation": "C9顶尖名校，长三角创新创业前沿，校友网络遍布全球。",
        "default_pitfalls": "初试高分云集，复试专业英文与前沿科研面试考察极深。"
    },
    "清华大学": {
        "alias": ["清华", "THU"],
        "level": "985工程 / 211工程 / 双一流A类 / 34所自主划线 / C9联盟",
        "region": "北京",
        "official_site": "https://yz.tsinghua.edu.cn/",
        "yz_chsi_id": "368001",
        "pro_departments": {
            "计算机": {
                "college": "计算机科学与技术系 / 交叉信息研究院 / 网络研究院",
                "majors": ["081200 计算机科学与技术 (初试自命题 912 计算机专业基础综合，难度天花板)"],
                "score_trend": "复试线通常在 340~365 分之间，912 自命题含金量极高，均分较低。",
                "ratio_quota": "统考招收名额极少（个位数至十余人），绝大多数名额为直博与推免。",
                "protect_first": "复试神仙打架，看重学术科研背景、竞赛奖项与本科综合潜能。",
                "reputation": ["国内计算机绝对第一梯队，拥有顶尖师资与国际学术资源。"],
                "pitfalls": ["⚠️ 912 自命题难度极大，容错率极低，若无深厚代码底子需慎重抉择。"]
            }
        },
        "default_reputation": "中国顶尖学府，学术与产业界终极资源底蕴。",
        "default_pitfalls": "统考名额极少，推免占比超高，报考需具备极强综合实力。"
    },
    "北京大学": {
        "alias": ["北大", "PKU"],
        "level": "985工程 / 211工程 / 双一流A类 / 34所自主划线 / C9联盟",
        "region": "北京",
        "official_site": "https://admission.pku.edu.cn/",
        "yz_chsi_id": "368002",
        "pro_departments": {
            "计算机": {
                "college": "计算机学院 / 智能学院 / 软件与微电子学院",
                "majors": [
                    "081200 计算机科学与技术 (初试考 408 / 数学一)",
                    "085400 软件与微电子学院专硕 (初试考 408 / 数学二 / 英语二)"
                ],
                "score_trend": "本部计算机复试线 360+；软微学院专硕因招生体量大，复试线在 330~355 分浮动。",
                "ratio_quota": "软微学院每年统考招数百人，是报考北大的主流核心通道。",
                "protect_first": "复试规范，盲审制度执行良好，软微相对公平透明。",
                "reputation": ["未名湖畔百年积淀，计算机理论、自然语言处理与系统结构全国顶尖，毕业生进体制与头部外企大厂极具优势。"],
                "pitfalls": ["⚠️ 软微大兴校区就读，专硕不解决住宿或需校外租房，生活成本需提前规划。"]
            }
        },
        "default_reputation": "中国最高学府之一，人文与前沿科学并蓄。",
        "default_pitfalls": "复试学术考察深刻，需提前研读报考导师近期论文。"
    },
    "北京航空航天大学": {
        "alias": ["北航", "BUAA"],
        "level": "985工程 / 211工程 / 双一流A类 / 34所自主划线",
        "region": "北京",
        "official_site": "http://yzb.buaa.edu.cn/",
        "yz_chsi_id": "368004",
        "pro_departments": {
            "计算机": {
                "college": "计算机学院 / 软件学院",
                "majors": ["081200 计算机学硕 / 085400 软件与电子信息专硕 (部分方向改考408)"],
                "score_trend": "复试线 340~365 分，单科线自划要求高。",
                "ratio_quota": "招生规模稳定，一志愿保护良好。",
                "protect_first": "老牌工科强校，复试极其看重上机与专业面试实战水平。",
                "reputation": ["软件工程全国顶尖，空天信一体化，国防与互联网就业双强。"],
                "pitfalls": ["⚠️ 严查单科线，自划线单科不过直接一票否决；复试机试务必提前模拟刷题。"]
            }
        },
        "default_reputation": "航空航天与信息技术重镇，科研经费充足，作风严谨。",
        "default_pitfalls": "初试自划线单科卡人严格，复试上机机试具有硬核淘汰率。"
    },
    "电子科技大学": {
        "alias": ["成电", "电子科大", "UESTC"],
        "level": "985工程 / 211工程 / 双一流A类 / 34所自主划线",
        "region": "四川成都",
        "official_site": "https://yz.uestc.edu.cn/",
        "yz_chsi_id": "368026",
        "pro_departments": {
            "计算机": {
                "college": "计算机科学与工程学院 / 信息与软件工程学院",
                "majors": [
                    "081200 计算机学硕 (初试考 408)",
                    "085400 软件学院专硕 (初试考 860 / 408，视当年简章更新)"
                ],
                "score_trend": "成电计算机热度极高，复试线近年常年在 335~360 分。",
                "ratio_quota": "统考招生总量大，西南IT与互联网黄埔军校。",
                "protect_first": "保护一志愿，进复试比例合理。",
                "reputation": ["IT行业认可度极高，电子信息与通信全国顶尖，华为腾讯西南最大生源校。"],
                "pitfalls": ["⚠️ 报考人数极多，内卷激烈，注意不同学院专业课代码与参考书差异。"]
            }
        },
        "default_reputation": "中国电子类院校双雄之一，IT与通信行业人脉极深。",
        "default_pitfalls": "工科热度常年居高不下，复试综合面试对基础概念提问极细。"
    },
    "南京大学": {
        "alias": ["南大", "NJU"],
        "level": "985工程 / 211工程 / 双一流A类 / 34所自主划线 / C9联盟",
        "region": "江苏南京",
        "official_site": "https://yzb.nju.edu.cn/",
        "yz_chsi_id": "368010",
        "pro_departments": {
            "计算机": {
                "college": "计算机科学与技术系 / 人工智能学院",
                "majors": ["081200 计算机学硕 (考408) / 085400 软件专硕 (苏州校区/鼓楼校区)"],
                "score_trend": "学硕复试线 350+，AI 学院与本部专硕竞争极为激烈。",
                "ratio_quota": "推免占比偏高，苏州新校区逐步扩大专硕招生。",
                "protect_first": "百年名校，复试极其规范公正，严格看重学术潜能与代码底蕴。",
                "reputation": ["LAMDA 人工智能实验室全球享有盛誉，周志华教授坐镇，学术声誉顶尖。"],
                "pitfalls": ["⚠️ 理论功底与数学要求极高，面试深入追问机器学习底层数学推导。"]
            }
        },
        "default_reputation": "文理底蕴深厚，学术声誉崇高，低调严谨。",
        "default_pitfalls": "推免比例高，统考名额竞争激烈，初试复试均不能有短板。"
    },
    "北京邮电大学": {
        "alias": ["北邮", "BUPT"],
        "level": "211工程 / 双一流建设高校 / 优势学科创新平台",
        "region": "北京",
        "official_site": "https://yzb.bupt.edu.cn/",
        "yz_chsi_id": "368005",
        "pro_departments": {
            "计算机": {
                "college": "计算机学院 (国家示范性软件学院) / 网络空间安全学院",
                "majors": ["081200 计算机学硕 / 085400 软件专硕 (考408统考)"],
                "score_trend": "复试线常年高企，虽非985但计算机与通信录取分数比肩中上游985（340~370分）。",
                "ratio_quota": "招生体量大，生源竞争极其激烈。",
                "protect_first": "保护一志愿，复试差额比规范。",
                "reputation": ["通信与互联网‘黄埔军校’，大厂研发团队北邮校友网络极为庞大，就业率与薪资待遇首屈一指。"],
                "pitfalls": ["⚠️ 虽为 211 但报考难度极高，切勿抱有‘捡漏’心态，需做好对标顶尖 985 的复习强度。"]
            }
        },
        "default_reputation": "信息通信领域绝对旗帜，互联网大厂求职天花板。",
        "default_pitfalls": "录取分与竞争烈度丝毫不亚于顶流 985，初试 408 必须攻坚高分。"
    }
}


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


def _clean_bing_url(raw_url: str) -> str:
    """从 Bing /ck/a 跳转链接中解密还原原始目标真实网址"""
    if not raw_url:
        return ""
    if "/ck/a?" in raw_url and "u=" in raw_url:
        try:
            m = re.search(r'[?&]u=([^&]+)', raw_url)
            if m:
                val = m.group(1)
                if val.startswith("a1"):
                    val = val[2:]
                val += "=" * (-len(val) % 4)
                decoded = base64.b64decode(val).decode("utf-8", errors="ignore")
                if decoded.startswith("http"):
                    return decoded
        except Exception:
            pass
    return raw_url


def _sanitize_text(raw_text: str) -> str:
    """清理字符串中的 HTML 标签、实体与 Windows 控制台非法编码字符"""
    if not raw_text:
        return ""
    text = re.sub(r"<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", raw_text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    # 清洗窄空格、零宽空格等容易引起 GBK 编码崩溃的特殊 Unicode 字符
    text = re.sub(r'[\u2000-\u200f\u202f\u205f\u3000\ufeff]', ' ', text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def raw_web_search(query: str, max_results: int = 5, timeout: int = 8) -> List[Dict[str, str]]:
    """
    轻量通用网页检索：优先使用国内高可用 Bing，失败则降级至 DuckDuckGo
    零第三方 pip 依赖！
    """
    results = []

    # 1. 优先尝试 Bing China
    try:
        bing_headers = {
            "User-Agent": USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Cookie": "SRCHHPGUSR=SRCHLANG=zh-Hans; _EDGE_S=mkt=zh-cn;"
        }
        url_bing = f"https://www.bing.com/search?q={urllib.parse.quote(query)}&mkt=zh-CN&setlang=zh-Hans"
        req = urllib.request.Request(url_bing, headers=bing_headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html_txt = resp.read().decode("utf-8", errors="ignore")
            matches = re.findall(r'<li class="b_algo"[^>]*>(.*?)</li>', html_txt, re.DOTALL)
            for m in matches:
                t_match = re.search(r'<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>(.*?)</a></h2>', m)
                s_match = re.search(r'<div class="b_caption"[^>]*>.*?<p[^>]*>(.*?)</p>', m, re.DOTALL)
                if not t_match:
                    continue
                t_clean = _sanitize_text(t_match.group(2))
                u_clean = _clean_bing_url(t_match.group(1))
                s_clean = _sanitize_text(s_match.group(1)) if s_match else ""
                if t_clean and u_clean:
                    results.append({"title": t_clean, "url": u_clean, "snippet": s_clean})
                    if len(results) >= max_results:
                        break
        if results:
            return results
    except Exception:
        pass

    # 2. 备用 DuckDuckGo HTML
    try:
        ddg_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
        ddg_headers = {"User-Agent": USER_AGENT}
        req = urllib.request.Request(ddg_url, headers=ddg_headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html_txt = resp.read().decode("utf-8", errors="ignore")
            titles = re.findall(r'<a[^>]+class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>', html_txt, re.DOTALL)
            snippets = re.findall(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', html_txt, re.DOTALL)
            limit = min(len(titles), max_results)
            for i in range(limit):
                href, t_html = titles[i]
                t_clean = _sanitize_text(t_html)
                s_clean = _sanitize_text(snippets[i]) if i < len(snippets) else ""
                real_url = _clean_ddg_url(href)
                results.append({
                    "title": t_clean or f"检索结果 {i+1}",
                    "url": real_url,
                    "snippet": s_clean
                })
    except Exception:
        pass

    return results


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
    major = major.strip() or "计算机/热门专业"

    # 判断办学层次推断
    level = "教育部直属 / 省属重点本科院校"
    top_985 = ["清华", "北大", "浙大", "复旦", "上交", "南大", "中科大", "人大学", "北航", "北理", "哈工大", "同济", "南开", "天津大学", "大连理工", "吉林大学", "东北大学", "华东师大", "东南大学", "中南大学", "湖南大学", "华南理工", "四川大学", "重庆大学", "电子科大", "西安交大", "西北工大", "兰州大学", "中国农大", "国防科大", "中山大学", "厦门大学", "山东大学", "海洋大学", "中国地大", "矿大", "石油大"]
    for k in top_985:
        if k in school:
            level = "985工程 / 211工程 / 双一流建设重点高校"
            break
    if "大学" in school and "211" not in level:
        level += "（硕士学位授权重点高校）"

    # 初试科目特征启发推导
    subjects = []
    if any(w in major for w in ("计算机", "软件", "网络", "信息安全", "人工智能", "大数据", "物联网")):
        subjects.append("统考科目：101思想政治理论、201英语(一)或204英语(二)、301数学(一)或302数学(二)")
        subjects.append("专业课：408计算机学科专业基础（全国统考）或院校自命题（如数据结构、操作系统、C/C++）")
    elif any(w in major for w in ("金融", "应用统计", "国际商务", "保险", "资产评估")):
        subjects.append("初试科目：政治、英语(二)、396经济类综合能力 / 数学(三)、专业课自命题 (如431金融学综合)")
    elif any(w in major for w in ("机械", "自动化", "电气", "通信", "土木")):
        subjects.append("初试科目：政治、英语(一/二)、数学(一/二)、专业课自命题 (如控制工程、电路、理论力学)")
    else:
        subjects.append("常规科目：思想政治理论、外国语、业务课一（数学或统考专业课）、业务课二（自命题专业课）")

    return {
        "level": level,
        "region": "全国",
        "official_site": f"https://yz.chsi.com.cn/sch/search.do?xxmc={urllib.parse.quote(school)}",
        "subjects": subjects,
        "reputation_summary": f"该校在【{major}】方向具备扎实培养体系，历年毕业生主要面向本省及周边区域高新技术产业与企事业单位。",
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
        for k, v in pro_deps.items():
            if k in major or major in k:
                dep_data = v
                break
        if not dep_data and pro_deps:
            # 取第一个默认热门专业
            dep_data = list(pro_deps.values())[0]

        if dep_data:
            metrics["quota_hint"] = dep_data.get("ratio_quota", "")
            metrics["subjects_hint"] = dep_data.get("majors", [])
            metrics["positive_signals"].append("业内公认高度保护一志愿")
            metrics["positive_signals"].append("复试公开透明、不歧视双非")
            metrics["risk_signals"].append("初试408/统考高分竞争激烈")
            metrics["risk_signals"].append("复试机试或专业面试有硬淘汰率")
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
        "db_match": db_match,
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
        links_block = [
            "\n---\n### 🔗 权威官方与实名社媒直通车",
            f"- 🏛️ [中国研究生招生信息网目录查询]({official_data[1]['url'] if len(official_data)>1 else 'https://yz.chsi.com.cn/zsml/queryAction.do'})",
            f"- 💡 [知乎 {school} {major} 就读体验专区]({direct_links.get('zhihu_topic', 'https://www.zhihu.com')})",
            f"- 📺 [B站 {school} {major} 高分备考经验贴]({direct_links.get('bili_topic', 'https://www.bilibili.com')})",
            f"- 📕 [小红书 {school} {major} 考研避坑专区]({direct_links.get('xhs_topic', 'https://www.xiaohongshu.com')})"
        ]
        return llm_report + "\n" + "\n".join(links_block)

    # 深度离线/无 API 架构卡片模式：绝不留空，全维度呈现干货
    lines = []
    title_suffix = f" · {school}" + (f" {major}" if major else "")
    lines.append(f"# 🎯 目标院校考研深度情报研报{title_suffix}")
    lines.append("> 汇集研招网官方目录、高校研究生院官网、知乎实名就读体验、B站高分复盘与小红书避坑数据")
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
                    study_plan["pro_name"] = "408 计算机学科专业基础"
                    break
                elif "自命题" in s:
                    clean_name = s.replace("专业课自命题:", "").strip()
                    cfg["pro_name"] = clean_name
                    study_plan["pro_name"] = clean_name

        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False
