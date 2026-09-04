# -*- coding: utf-8 -*-
"""
KaoYan Intelligence · 多级抓取器与健康诊断 (Multi-tier Fetcher & Health Diagnostics)

抓取分层设计 (渐进式增强)：
  1. Tier 1 (默认): 纯 Python 标准库 urllib 轻量抓取，支持字符集自动检测、防盗链与超时控制
  2. Tier 2 (官方): 研招网标准化直通连接
  3. Tier 3 (可选插件): Playwright 无头浏览器渲染与 API 流量嗅探 (仅在用户主动安装时激活)
"""

import sys
import ssl
import gzip
import urllib.request
import urllib.parse
import urllib.error
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Tuple

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# 允许忽略非严格自签名或过期高校 SSL 证书（很多高校证书配置不全）
_SSL_CONTEXT = ssl.create_default_context()
_SSL_CONTEXT.check_hostname = False
_SSL_CONTEXT.verify_mode = ssl.CERT_NONE


@dataclass
class FetchResult:
    url: str
    status_code: int                  # 200, 403, 404, 500 等
    content: str                      # 解码后的正文文本
    is_valid: bool                    # 是否成功获取有效 HTML/JSON
    access_status: str                # "OK", "HTTP_403", "BLOCKED", "TIMEOUT", "BROWSER_REQUIRED", "ERROR"
    headers: Dict[str, str]           # 响应头
    raw_bytes_len: int = 0
    api_captured: Optional[List[str]] = None # Playwright 嗅探到的 API 列表


class HTTPFetcher:
    """轻量标准库 HTTP 抓取器 (零外部依赖)"""

    def __init__(self, timeout: int = 6):
        self.timeout = timeout

    def fetch(self, url: str, referer: Optional[str] = None) -> FetchResult:
        """安全抓取 URL 并诊断页面访问健康状态"""
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "close"
        }
        if referer:
            headers["Referer"] = referer

        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=_SSL_CONTEXT) as resp:
                status_code = resp.status
                resp_headers = dict(resp.headers)
                raw_data = resp.read()

                # 处理 gzip 解压
                if resp_headers.get("Content-Encoding") == "gzip":
                    try:
                        raw_data = gzip.decompress(raw_data)
                    except Exception:
                        pass

                # 智能编码解析
                content = self._decode_content(raw_data, resp_headers)
                
                # 判断是否需要无头浏览器渲染 (例如空 div、单页应用 SPA)
                access_status = "OK"
                if len(content.strip()) < 300 and ("<div id=\"app\">" in content or "<div id=\"root\">" in content):
                    access_status = "BROWSER_REQUIRED"

                return FetchResult(
                    url=url,
                    status_code=status_code,
                    content=content,
                    is_valid=True,
                    access_status=access_status,
                    headers=resp_headers,
                    raw_bytes_len=len(raw_data)
                )

        except urllib.error.HTTPError as e:
            status = "HTTP_403" if e.code == 403 else f"HTTP_{e.code}"
            return FetchResult(
                url=url,
                status_code=e.code,
                content="",
                is_valid=False,
                access_status=status,
                headers={}
            )
        except urllib.error.URLError as e:
            reason_str = str(e.reason).lower()
            status = "TIMEOUT" if "timed out" in reason_str else "BLOCKED"
            return FetchResult(
                url=url,
                status_code=0,
                content="",
                is_valid=False,
                access_status=status,
                headers={}
            )
        except Exception as e:
            return FetchResult(
                url=url,
                status_code=0,
                content="",
                is_valid=False,
                access_status="ERROR",
                headers={}
            )

    def _decode_content(self, data: bytes, headers: Dict[str, str]) -> str:
        """从响应头或正文中检测并解码字符集"""
        content_type = headers.get("Content-Type", "").lower()
        if "gbk" in content_type:
            return data.decode("gbk", errors="replace")
        elif "gb2312" in content_type:
            return data.decode("gb2312", errors="replace")
        
        # 尝试 UTF-8
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            pass

        # 尝试 GB18030 / GBK
        try:
            return data.decode("gb18030", errors="replace")
        except Exception:
            return data.decode("latin1", errors="replace")


class BrowserPluginManager:
    """
    Playwright 浏览器可选插件管理器 (Progressive Enhancement)
    仅当用户环境中存在 playwright 库时激活无头浏览器渲染与 API 流量嗅探。
    未安装时平滑降级，给出清晰安装指引，绝不抛崩程序。
    """

    @staticmethod
    def is_available() -> bool:
        """检测 playwright 是否可用"""
        try:
            import playwright  # noqa: F401
            return True
        except ImportError:
            return False

    @staticmethod
    def fetch_with_browser(url: str, timeout_sec: int = 15) -> FetchResult:
        """
        使用 Playwright 渲染页面并监听后台 API 流量
        """
        if not BrowserPluginManager.is_available():
            return FetchResult(
                url=url,
                status_code=0,
                content="",
                is_valid=False,
                access_status="BROWSER_NOT_INSTALLED",
                headers={},
                api_captured=[]
            )

        try:
            from playwright.sync_api import sync_playwright
            captured_apis = []

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(user_agent=USER_AGENT)
                
                # 监听网络响应中的 API 请求
                def handle_response(response):
                    r_url = response.url.lower()
                    if any(kw in r_url for kw in ["/api/", "json", "list", "query", "article"]):
                        captured_apis.append(response.url)

                page.on("response", handle_response)
                page.goto(url, timeout=timeout_sec * 1000, wait_until="domcontentloaded")
                content = page.content()
                browser.close()

                return FetchResult(
                    url=url,
                    status_code=200,
                    content=content,
                    is_valid=True,
                    access_status="OK",
                    headers={},
                    raw_bytes_len=len(content.encode("utf-8", errors="replace")),
                    api_captured=captured_apis
                )
        except Exception as e:
            return FetchResult(
                url=url,
                status_code=0,
                content="",
                is_valid=False,
                access_status=f"BROWSER_ERROR: {e}",
                headers={},
                api_captured=[]
            )
