# -*- coding: utf-8 -*-
"""
KaoYan Intelligence · 招生动态监控与指纹比对引擎 (Admission Watcher)

核心功能：
  1. 跟踪指定高校研究生院与招生办主页的最新动态与公告列表
  2. 计算标准化归一化页面指纹哈希 (SHA256/MD5)，过滤动态 session ID、时间戳与访问量计数器
  3. 自动发现 2027/2026 新简章、专业目录、自命题大纲、复试分数线变动
  4. 健壮提取高校通知公告标题 (优先完整 title 属性、剥离嵌套标签)
  5. 状态持久化于 .memory/admission_watch.json
"""

import hashlib
import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .fetcher import HTTPFetcher
from .models import current_exam_year
from .registry import get_registry, resolve_university

try:  # 加速协同层（统一封装"是否走 Rust"的决策与降级）
    import accel as _accel
except ImportError:  # pragma: no cover - 兼容 tools.accel 包式导入
    from tools import accel as _accel

try:  # 双导入路径兼容
    from tools.ky_io import guard_write
except ImportError:  # pragma: no cover
    from ky_io import guard_write

ROOT = Path(__file__).resolve().parent.parent.parent
WATCH_FILE = ROOT / ".memory" / "admission_watch.json"

try:
    import ky_rust_ext as _rust
    _HAS_RUST_EXT = True
except ImportError:
    _rust = None
    _HAS_RUST_EXT = False


def normalize_content_for_fingerprint(html_text: str) -> str:
    """归一化 HTML 文本以计算稳定指纹哈希。

    剔除动态干扰项：
    - 脚本、样式、noscript、iframe
    - HTML 注释
    - 动态 session ID、Token、随机数 (如 jsessionid, PHPSESSID, _csrf, token)
    - 动态访问计数器 (如 浏览次数: 1234, clicks, views)
    - 动态时钟/服务器当前时间 (如 今天是：2026年9月18日, 当前时间: 19:30:00)
    - 统一空白字符与 HTML 实体
    """
    if not html_text:
        return ""

    text = str(html_text)

    # 1. 剔除脚本、样式与无用容器
    text = re.sub(
        r"<(script|style|noscript|iframe)[^>]*>.*?</\1>",
        " ",
        text,
        flags=re.DOTALL | re.IGNORECASE
    )

    # 2. 剔除 HTML 注释
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)

    # 3. 剔除动态 session ID / csrf / token / 时间戳查询参数
    text = re.sub(
        r"(?:jsessionid|phpsessid|sid|sessionid|token|_csrf|csrf_token)=[a-zA-Z0-9_\-]+",
        "",
        text,
        flags=re.IGNORECASE
    )
    text = re.sub(
        r"[?&](?:_t|t|timestamp|v|ver|version|nonce)=\d+",
        "",
        text,
        flags=re.IGNORECASE
    )

    # 4. 剔除动态访问量/点击量计数器
    text = re.sub(
        r"(?:点击量|浏览量|阅读量|浏览次数|访问人次|访问量|点击数|浏览数)[：:\s]*\d+",
        "",
        text,
        flags=re.IGNORECASE
    )
    text = re.sub(
        r"<[^>]+(?:clicks|views|viewcount|visitcount|hits|pv)[^>]*>\s*\d+\s*</[^>]+>",
        "",
        text,
        flags=re.IGNORECASE
    )
    text = re.sub(
        r"var\s+(?:clicks|views|viewcount|visitcount|hits)\s*=\s*\d+;?",
        "",
        text,
        flags=re.IGNORECASE
    )

    # 5. 剔除当前时钟与动态今日日期
    text = re.sub(
        r"今天是[：:\s]*\d{4}年\d{1,2}月\d{1,2}日(?:\s*星期[一二三四五六日天])?",
        "",
        text
    )
    text = re.sub(
        r"当前时间[：:\s]*\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?",
        "",
        text
    )
    text = re.sub(
        r"\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\s+\d{1,2}:\d{2}:\d{2}\b",
        "",
        text
    )

    # 6. 剔除所有 HTML 标签并反转义实体
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    if "&" in text:
        text = html.unescape(text)

    # 7. 规范化连续空白
    return re.sub(r"\s+", " ", text).strip()


def compute_content_fingerprint(html_text: str, algorithm: str = "sha256") -> str:
    """计算归一化内容指纹 (支持 SHA256 与 MD5)。"""
    normalized = normalize_content_for_fingerprint(html_text)
    if algorithm.lower() == "md5":
        return hashlib.md5(normalized.encode("utf-8", errors="replace")).hexdigest()
    return _accel.sha256_hash(normalized)


class AdmissionWatcher:
    """高校招考动态监控器"""

    def __init__(self, fetcher: Optional[HTTPFetcher] = None):
        self.fetcher = fetcher or HTTPFetcher(timeout=5)
        self.registry = get_registry()
        self.watch_data: Dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        """加载监控配置文件"""
        if WATCH_FILE.exists():
            try:
                with open(WATCH_FILE, "r", encoding="utf-8") as f:
                    self.watch_data = json.load(f)
            except Exception as e:
                import shutil
                bak_path = WATCH_FILE.with_suffix(".json.bak")
                try:
                    shutil.copy2(WATCH_FILE, bak_path)
                except Exception:
                    pass
                print(f"[!] 读取监控配置文件失败 ({e})，已备份原文件至 {bak_path}")
                self.watch_data = {}
        else:
            self.watch_data = {}

    def _save(self) -> None:
        """保存监控配置（原子写入，防止进程意外退出导致文件截断损坏）

        [safe 模式收口] 这里是本模块唯一的写盘点，此前用裸 ``open(...,"w")``，
        绕开了 ky_io 的统一写闸门 —— 实测 ``ky watch --check`` 只是「比对指纹」，
        却会经 :meth:`check_updates` 末尾的 ``self._save()`` 落盘
        ``.memory/admission_watch.json``。现先过 ``guard_write``，只读模式下
        抛 ``PermissionDeniedError``，不再静默写盘。
        """
        guard_write("更新招生监控指纹库", WATCH_FILE)
        WATCH_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = WATCH_FILE.with_suffix(".json.tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(self.watch_data, f, ensure_ascii=False, indent=2)
        tmp_file.replace(WATCH_FILE)

    def add_watch(self, school_query: str) -> Dict[str, Any]:
        """添加或更新监控目标"""
        entity = resolve_university(school_query)
        if not entity:
            return {"success": False, "msg": f"未能识别高校【{school_query}】，请核对校名或代码"}

        target_url = entity.admission_domain or entity.graduate_domain or entity.official_domain
        if not target_url:
            return {"success": False, "msg": f"高校【{entity.name}】未配置有效招生官方域名"}

        # 立即拉取一次基线指纹
        fetch_res = self.fetcher.fetch(target_url)
        content_hash = self._compute_sha256(fetch_res.content) if fetch_res.is_valid else ""
        extracted_titles = self._extract_recent_titles(fetch_res.content) if fetch_res.is_valid else []

        record = {
            "name": entity.name,
            "chsi_code": entity.chsi_code,
            "url": target_url,
            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "last_check": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "last_hash": content_hash,
            "recent_titles": extracted_titles,
            "baseline_complete": bool(fetch_res.is_valid),
            "updates": []
        }

        watch_key = entity.chsi_code if (entity.chsi_code and entity.chsi_code != "待查") else f"UNLISTED_{entity.name}"
        self.watch_data[watch_key] = record
        self._save()

        return {
            "success": True,
            "name": entity.name,
            "url": target_url,
            "titles_count": len(extracted_titles),
            "msg": f"已成功将【{entity.name}】纳入动态招生监控雷达"
        }

    def remove_watch(self, school_query: str) -> bool:
        """移除监控"""
        entity = resolve_university(school_query)
        target_code = entity.chsi_code if (entity and entity.chsi_code and entity.chsi_code != "待查") else (f"UNLISTED_{entity.name}" if entity else school_query)
        if target_code in self.watch_data:
            del self.watch_data[target_code]
            self._save()
            return True
        elif school_query in self.watch_data:
            del self.watch_data[school_query]
            self._save()
            return True
        return False

    def list_watched(self) -> List[Dict[str, Any]]:
        """获取当前正在监控的高校列表"""
        return list(self.watch_data.values())

    def check_updates(self, school_query: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        比对页面指纹与最新标题列表，发现新增简章或通告
        """
        targets = []
        if school_query:
            entity = resolve_university(school_query)
            target_code = entity.chsi_code if (entity and entity.chsi_code and entity.chsi_code != "待查") else (f"UNLISTED_{entity.name}" if entity else school_query)
            if target_code in self.watch_data:
                targets.append((target_code, self.watch_data[target_code]))
            elif school_query in self.watch_data:
                targets.append((school_query, self.watch_data[school_query]))
        else:
            targets = list(self.watch_data.items())

        findings = []

        for code, item in targets:
            url = item.get("url")
            old_hash = item.get("last_hash", "")
            old_titles = set(item.get("recent_titles", []))

            fetch_res = self.fetcher.fetch(url)
            if not fetch_res.is_valid:
                findings.append({
                    "school": item.get("name"),
                    "status": "FETCH_FAILED",
                    "msg": f"访问超时或受阻 ({fetch_res.access_status})"
                })
                continue

            new_hash = self._compute_sha256(fetch_res.content)
            new_titles = self._extract_recent_titles(fetch_res.content)
            
            # 检测新出现的标题
            # 旧版本只保存前八条标题；迁移时先建立完整基线，不把历史公告当新增。
            baseline_ready = bool(old_hash and item.get("baseline_complete"))
            # [P2-4 修复·首检误报] 存量基线标题过少（<10 条，多为旧版本只存 8 条
            # 或 ky mount 自动建档时的快照）时，本次只做基线补全、不告警，并打上
            # baseline_v2 标记。否则首检即把 2017–2026 历史公告当"新动态"误报，
            # 用户很快会学会忽视这个功能。标记只补一次，后续真正的新增照常告警。
            needs_rebaseline = (baseline_ready and len(old_titles) < 10
                                and not item.get("baseline_v2"))
            newly_added_titles = [t for t in new_titles if t not in old_titles] if baseline_ready else []

            # 过滤高关注度招考关键词 (动态计算考研年份窗口)
            target_yr = current_exam_year()
            year_kws = [str(target_yr), str(target_yr - 1), str(target_yr + 1)]
            alert_kws = year_kws + ["招生简章", "专业目录", "大纲", "自命题", "复试", "调整", "硕士", "考研", "招考", "录取", "初试", "调剂"]
            alert_titles = []
            for t in newly_added_titles:
                years = [int(y) for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", t)]
                if years and max(years) < target_yr - 1:
                    continue
                if any(kw in t for kw in alert_kws):
                    alert_titles.append(t)

            # 仅在检测到真正的新增招考警报/简章标题时判定为 UPDATED，避免纯动态 HTML 漂移引发假阳性
            has_change = bool(alert_titles) and not needs_rebaseline

            if has_change:
                finding_item = {
                    "school": item.get("name"),
                    "status": "UPDATED",
                    "alert_titles": alert_titles[:10],
                    "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "url": url
                }
                findings.append(finding_item)

                # 记录更新历史
                item.setdefault("updates", []).append({
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "alert_titles": alert_titles
                })
            else:
                findings.append({
                    "school": item.get("name"),
                    "status": ("BASELINED" if (not baseline_ready or needs_rebaseline)
                               else "UNCHANGED"),
                    "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "url": url
                })

            # 更新当前记录
            item["last_hash"] = new_hash
            item["recent_titles"] = new_titles
            item["baseline_complete"] = True
            item["baseline_v2"] = True
            item["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M")

        self._save()
        return findings

    def _compute_sha256(self, content: str) -> str:
        """计算标准化归一化文本指纹 (SHA256)。"""
        return compute_content_fingerprint(content, algorithm="sha256")

    def _compute_md5(self, content: str) -> str:
        """计算标准化归一化文本指纹 (MD5)。"""
        return compute_content_fingerprint(content, algorithm="md5")

    def _extract_recent_titles(self, html_text: str) -> List[str]:
        """从页面健壮提取通知列表标题 (权威原生 Python 实现，深度嗅探 title 属性并剥离多层嵌套与噪音)"""
        if not html_text:
            return []

        noise_skips = [
            "版权所有", "网站地图", "关于我们", "联系我们", "常用下载",
            "旧版网站", "友情链接", "English", "办事大厅", "博士", "系统登录",
            "管理系统", "平台入口", "登录入口", "查看更多", "查看详情",
            "返回首页", "设为首页", "加入收藏", "下一页", "上一页", "尾页",
            "首页", "更多", "more", "index", "login", "sitemap"
        ]

        def _clean_date_affixes(s: str) -> str:
            if not s:
                return ""
            s = re.sub(r'^[\[(【]\s*\d{2,4}[-/.]\d{1,2}[-/.]\d{1,2}\s*[\])】]\s*', '', s)
            s = re.sub(r'\s*[\[(【]\s*\d{2,4}[-/.]\d{1,2}[-/.]\d{1,2}\s*[\])】]$', '', s)
            s = re.sub(r'^[\[(【]\s*\d{1,2}[-/.]\d{1,2}\s*[\])】]\s*', '', s)
            s = re.sub(r'\s*[\[(【]\s*\d{1,2}[-/.]\d{1,2}\s*[\])】]$', '', s)
            s = re.sub(r'^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\s*', '', s)
            s = re.sub(r'\s*\d{4}[-/.]\d{1,2}[-/.]\d{1,2}$', '', s)
            return s.strip()

        clean_titles: List[str] = []
        # 统一匹配所有 <a ...>...</a> 结构
        a_tags = re.findall(r'<a([^>]*?)>(.*?)</a>', str(html_text), re.DOTALL | re.IGNORECASE)
        for attrs, inner in a_tags:
            # 1. 优先嗅探 title 属性 (支持单双引号或无引号，处理二次实体转义)
            attr_title = ""
            m_title = re.search(r'''title\s*=\s*(?:["']([^"']+)["']|([^\s>]+))''', attrs, re.IGNORECASE)
            if m_title:
                raw_attr = m_title.group(1) or m_title.group(2) or ""
                attr_title = html.unescape(raw_attr).strip()
                if "&" in attr_title:
                    attr_title = html.unescape(attr_title).strip()
                attr_title = _clean_date_affixes(attr_title)

            # 2. 清洗内部 HTML 文本 (剥离子标签、反转义实体、归一化空白)
            inner_cleaned = re.sub(r'<[^>]+>', ' ', inner)
            inner_cleaned = html.unescape(inner_cleaned)
            if "&" in inner_cleaned:
                inner_cleaned = html.unescape(inner_cleaned)
            inner_cleaned = re.sub(r'\s+', ' ', inner_cleaned).strip()
            inner_cleaned = _clean_date_affixes(inner_cleaned)

            # 3. 候选决策：若 title 属性完整且长度充足，或 inner 文本存在截断省略号时，优先采用 title 属性
            candidate = ""
            if attr_title and (len(attr_title) >= len(inner_cleaned) or "..." in inner_cleaned or "…" in inner_cleaned):
                if len(attr_title) >= 6:
                    candidate = attr_title
            if not candidate and inner_cleaned:
                candidate = inner_cleaned

            candidate = _clean_date_affixes(candidate)

            # 4. 长度与黑名单检查 (放宽上限至 120 字符，严密拦截导航噪音词与纯符号)
            if 6 <= len(candidate) <= 120 and not any(skip in candidate for skip in noise_skips):
                if not re.match(r'^[\d\-./: ]+$', candidate):
                    if candidate not in clean_titles:
                        clean_titles.append(candidate)

        return clean_titles


__all__ = [
    "AdmissionWatcher",
    "compute_content_fingerprint",
    "normalize_content_for_fingerprint",
]
