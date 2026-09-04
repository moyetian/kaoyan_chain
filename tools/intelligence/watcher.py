# -*- coding: utf-8 -*-
"""
KaoYan Intelligence · 招生动态监控与指纹比对引擎 (Admission Watcher)

核心功能：
  1. 跟踪指定高校研究生院与招生办主页的最新动态与公告列表
  2. 计算页面正文与标题指纹哈希 (SHA256)
  3. 自动发现 2027/2026 新简章、专业目录、自命题大纲、复试分数线变动
  4. 状态持久化于 .memory/admission_watch.json
"""

import os
import re
import json
import hashlib
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

from .models import UniversityEntity
from .registry import get_registry, resolve_university
from .fetcher import HTTPFetcher

ROOT = Path(__file__).resolve().parent.parent.parent
WATCH_FILE = ROOT / ".memory" / "admission_watch.json"


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
            except Exception:
                self.watch_data = {}
        else:
            self.watch_data = {}

    def _save(self) -> None:
        """保存监控配置"""
        WATCH_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(WATCH_FILE, "w", encoding="utf-8") as f:
            json.dump(self.watch_data, f, ensure_ascii=False, indent=2)

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
        content_hash = hashlib.sha256(fetch_res.content.encode("utf-8", errors="replace")).hexdigest() if fetch_res.is_valid else ""
        extracted_titles = self._extract_recent_titles(fetch_res.content) if fetch_res.is_valid else []

        record = {
            "name": entity.name,
            "chsi_code": entity.chsi_code,
            "url": target_url,
            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "last_check": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "last_hash": content_hash,
            "recent_titles": extracted_titles[:8],
            "updates": []
        }

        self.watch_data[entity.chsi_code] = record
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
        target_code = entity.chsi_code if entity else school_query
        if target_code in self.watch_data:
            del self.watch_data[target_code]
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
            target_code = entity.chsi_code if entity else school_query
            if target_code in self.watch_data:
                targets.append((target_code, self.watch_data[target_code]))
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

            new_hash = hashlib.sha256(fetch_res.content.encode("utf-8", errors="replace")).hexdigest()
            new_titles = self._extract_recent_titles(fetch_res.content)
            
            # 检测新出现的标题
            newly_added_titles = [t for t in new_titles if t not in old_titles]

            # 过滤高关注度招考关键词
            alert_titles = []
            for t in newly_added_titles:
                if any(kw in t for kw in ["2027", "2026", "招生简章", "专业目录", "大纲", "自命题", "复试", "调整"]):
                    alert_titles.append(t)

            has_change = (new_hash != old_hash) or bool(alert_titles)

            if has_change:
                finding_item = {
                    "school": item.get("name"),
                    "status": "UPDATED",
                    "alert_titles": alert_titles if alert_titles else newly_added_titles[:3],
                    "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "url": url
                }
                findings.append(finding_item)

                # 记录更新历史
                item["updates"].append({
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "alert_titles": alert_titles
                })
            else:
                findings.append({
                    "school": item.get("name"),
                    "status": "UNCHANGED",
                    "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "url": url
                })

            # 更新当前记录
            item["last_hash"] = new_hash
            item["recent_titles"] = new_titles[:8]
            item["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M")

        self._save()
        return findings

    def _extract_recent_titles(self, html_text: str) -> List[str]:
        """从页面提取通知列表标题"""
        # 常见列表链接匹配模式
        links = re.findall(r"<a[^>]+>(.*?)</a>", html_text, re.DOTALL | re.IGNORECASE)
        clean_titles = []
        for l in links:
            t = re.sub(r"<[^>]+>", "", l).strip()
            t = re.sub(r"\s+", " ", t)
            # 过滤过短或非招考通知的导航文字
            if 8 <= len(t) <= 60 and not any(skip in t for skip in ["版权所有", "网站地图", "关于我们", "联系我们"]):
                if t not in clean_titles:
                    clean_titles.append(t)
        return clean_titles
