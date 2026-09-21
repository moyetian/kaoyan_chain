# -*- coding: utf-8 -*-
"""
KaoYan Intelligence · 多级抓取器与健康诊断 (Multi-tier Fetcher & Health Diagnostics)

抓取分层设计 (渐进式增强)：
  1. Tier 1 (默认): 纯 Python 标准库 urllib 轻量抓取，支持字符集自动检测、防盗链与超时控制
  2. Tier 2 (官方): 研招网标准化直通连接
  3. Tier 3 (可选插件): Playwright 无头浏览器渲染与 API 流量嗅探 (仅在用户主动安装时激活)
"""

import logging
import ssl
import urllib.request
import urllib.parse
import urllib.error
from dataclasses import dataclass
from typing import Dict, Any, List, Optional

try:  # 网络访问安全与解压体积上限（双导入路径兼容）
    from net_guard import (
        MAX_HTTP_RESPONSE_BYTES,
        TRUNCATION_MARKER,
        UnsafeURLError,
        assert_url_safe,
        decompress_limited,
        safe_urlopen,
    )
except ImportError:  # pragma: no cover - 兼容 tools. 包式导入
    from tools.net_guard import (  # type: ignore
        MAX_HTTP_RESPONSE_BYTES,
        TRUNCATION_MARKER,
        UnsafeURLError,
        assert_url_safe,
        decompress_limited,
        safe_urlopen,
    )

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

_LOG = logging.getLogger(__name__)

# 默认启用标准受信任 SSL 证书验证，确保研招网与高校官方页面证据真实可信
_DEFAULT_SSL_CONTEXT = ssl.create_default_context()

# 未验证（CERT_NONE）备用上下文。**只在调用方显式 opt-in**（``allow_insecure_ssl=True``）
# 时才被使用；默认路径下证书校验失败即如实失败，绝不自动降级（口径与
# tools/search/providers/_http.py 的 ``ssl_fallback_ctx`` 完全一致）。
_FALLBACK_UNVERIFIED_SSL_CONTEXT = ssl.create_default_context()
_FALLBACK_UNVERIFIED_SSL_CONTEXT.check_hostname = False
_FALLBACK_UNVERIFIED_SSL_CONTEXT.verify_mode = ssl.CERT_NONE


@dataclass
class FetchResult:
    url: str
    status_code: int                  # 200, 403, 404, 500 等
    content: str                      # 解码后的正文文本
    is_valid: bool                    # 是否成功获取有效 HTML/JSON
    access_status: str                # "OK", "HTTP_403", "BLOCKED", "TIMEOUT", "BROWSER_REQUIRED", "ERROR", "TLS_CERT_ERROR", "UNVERIFIED_SSL"
    headers: Dict[str, str]           # 响应头
    raw_bytes_len: int = 0
    api_captured: Optional[List[str]] = None # Playwright 嗅探到的 API 列表
    ssl_verified: bool = True         # 本次抓取是否**未发生** TLS 降级（默认 True；仅显式 opt-in 降级后为 False）
    truncated: bool = False           # 正文是否因体积上限被截断（内容尾部已带 TRUNCATION_MARKER）


class HTTPFetcher:
    """轻量标准库 HTTP 抓取器 (零外部依赖)"""

    def __init__(self, timeout: int = 6):
        self.timeout = timeout

    def fetch(self, url: str, referer: Optional[str] = None,
              extra_headers: Optional[Dict[str, str]] = None,
              allow_insecure_ssl: bool = False) -> FetchResult:
        """安全抓取 URL 并诊断页面访问健康状态。

        :param extra_headers: 追加/覆盖请求头。搜索引擎对 Cookie 与 Accept 敏感
            （缺少时会返回 200 但内容是无关垃圾），故允许调用方补充。
        :param allow_insecure_ssl: **显式** opt-in 开关，默认 ``False``。
            默认语义与 ``tools/search/providers/_http.py`` 完全一致：TLS 证书校验
            失败即**如实失败**（``is_valid=False``、``access_status="TLS_CERT_ERROR"``），
            **绝不静默降级**为未验证连接。仅当调用方明确传 ``True``（例如确知某
            高校站点使用自签名/过期证书且必须抓取）时，才会在证书错误后以未验证
            连接重试一次，并把 ``ssl_verified=False`` 写进结果且打 WARNING 日志。
        """
        # [P1 修复] SSRF 防护：先解析真实 IP 再判定，并禁止重定向到内网/回环。
        # 旧实现完全没有这一层，且 urlopen 默认跟随 3xx（公网 URL 302 到
        # 127.0.0.1 / 169.254.169.254 即可打到本机与云元数据）。
        try:
            assert_url_safe(url)
        except UnsafeURLError:
            return FetchResult(
                url=url,
                status_code=0,
                content="",
                is_valid=False,
                access_status="BLOCKED",
                headers={},
            )

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "close"
        }
        if referer:
            headers["Referer"] = referer
        if extra_headers:
            headers.update({str(k): str(v) for k, v in extra_headers.items()})

        req = urllib.request.Request(url, headers=headers)
        ssl_ctx = _DEFAULT_SSL_CONTEXT
        is_fallback_ssl = False
        # [TLS 降级口径对齐 _http.py] 重试 ≠ 降级：重试同一 TLS 配置是合理的，
        # 降级不行。因此重试次数不再与「自动降级」耦合 ——
        #   * 默认（未 opt-in）：只有 1 次尝试，证书错误即如实失败；
        #   * 显式 allow_insecure_ssl=True：最多 2 次，第 2 次才是未验证重试。
        max_attempts = 2 if allow_insecure_ssl else 1

        for attempt in range(max_attempts):
            try:
                with safe_urlopen(req, timeout=self.timeout, context=ssl_ctx) as resp:
                    status_code = resp.status
                    resp_headers = dict(resp.headers)
                    # [P2-4 修复] 读取与解压都加上体积上限（防超大响应 / 解压炸弹）
                    # 多读 1 字节用于判断「是否真的还有后续数据」，否则无法区分
                    # 「响应恰好 16MB」与「被 read(n) 截断」。
                    raw_data = resp.read(MAX_HTTP_RESPONSE_BYTES + 1)
                    read_truncated = len(raw_data) > MAX_HTTP_RESPONSE_BYTES
                    if read_truncated:
                        raw_data = raw_data[:MAX_HTTP_RESPONSE_BYTES]

                    # 处理 gzip / deflate 解压
                    # [P2-9 修复] 请求头声明了 `Accept-Encoding: gzip, deflate`，
                    # 但旧实现只解 gzip —— deflate 回包会被当成乱码正文。
                    # 现按 Content-Encoding（含 magic bytes 嗅探）统一处理，
                    # 且带解压体积上限（直接复用 _http.decompress_body 会因为它的
                    # gzip.decompress 无上限而在解压阶段被炸弹撑爆）。
                    raw_data, decompress_truncated = decompress_limited(
                        raw_data, resp_headers.get("Content-Encoding", ""))
                    truncated = read_truncated or decompress_truncated

                    # 智能编码解析
                    content = self._decode_content(raw_data, resp_headers)

                    # [P2-6 修复] 截断必须如实暴露：打上 TRUNCATION_MARKER 并置
                    # truncated=True，否则「半截正文」会被下游当成完整证据使用。
                    if truncated:
                        content += TRUNCATION_MARKER

                    # 判断是否需要无头浏览器渲染 (例如空 div、单页应用 SPA)
                    access_status = "UNVERIFIED_SSL" if is_fallback_ssl else "OK"
                    if len(content.strip()) < 300 and ("<div id=\"app\">" in content or "<div id=\"root\">" in content):
                        access_status = "BROWSER_REQUIRED"

                    return FetchResult(
                        url=url,
                        status_code=status_code,
                        content=content,
                        is_valid=True,
                        access_status=access_status,
                        headers=resp_headers,
                        raw_bytes_len=len(raw_data),
                        ssl_verified=not is_fallback_ssl,
                        truncated=truncated,
                    )

            except urllib.error.HTTPError as e:
                status = "HTTP_403" if e.code == 403 else f"HTTP_{e.code}"
                return FetchResult(
                    url=url,
                    status_code=e.code,
                    content="",
                    is_valid=False,
                    access_status=status,
                    headers={},
                    ssl_verified=not is_fallback_ssl
                )
            except urllib.error.URLError as e:
                # TLS 证书错误：默认**如实失败**，不再静默降级为未验证连接。
                # 仅当调用方显式 ``allow_insecure_ssl=True`` 时才做一次未验证重试。
                reason_str = str(e.reason).lower()
                is_ssl_err = isinstance(e.reason, ssl.SSLError) or "certificate" in reason_str or "ssl" in reason_str
                if is_ssl_err:
                    if not allow_insecure_ssl or is_fallback_ssl:
                        _LOG.warning(
                            "TLS 证书校验失败（未降级）: %s (%s)；如确需抓取该站点的"
                            "自签名/过期证书，请显式传入 allow_insecure_ssl=True", url, e)
                        return FetchResult(
                            url=url,
                            status_code=0,
                            content="",
                            is_valid=False,
                            access_status="TLS_CERT_ERROR",
                            headers={},
                            ssl_verified=not is_fallback_ssl
                        )
                    _LOG.warning(
                        "TLS 证书校验失败，按调用方显式 opt-in 降级为**未验证**连接"
                        "重试（ssl_verified=False）: %s (%s)", url, e)
                    ssl_ctx = _FALLBACK_UNVERIFIED_SSL_CONTEXT
                    is_fallback_ssl = True
                    continue

                status = "TIMEOUT" if "timed out" in reason_str else "BLOCKED"
                return FetchResult(
                    url=url,
                    status_code=0,
                    content="",
                    is_valid=False,
                    access_status=status,
                    headers={},
                    ssl_verified=not is_fallback_ssl
                )
            except Exception as e:
                # [G-log 修复] 原先吞掉原始异常，watcher 只能看到 ERROR 状态无法归因。
                import logging
                logging.getLogger(__name__).warning("抓取未预期异常 %s: %s", url, e)
                return FetchResult(
                    url=url,
                    status_code=0,
                    content="",
                    is_valid=False,
                    access_status="ERROR",
                    headers={}
                )

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
            # 预期内的回落：继续尝试 GB18030 / GBK（留痕便于排查乱码来源）
            import logging
            logging.getLogger(__name__).debug("UTF-8 解码失败，回落到 GB18030/GBK 分支")

        # 尝试 GB18030 / GBK
        try:
            return data.decode("gb18030", errors="replace")
        except Exception:
            return data.decode("latin1", errors="replace")

    def download_file(self, url: str, dest_path: Any, max_bytes: int = 15 * 1024 * 1024) -> bool:
        """安全下载文件（如招生专业目录与大纲 PDF），防超大文件滥用 (默认限额 15MB)"""
        # [P1 修复] 与 fetch 同源：下载入口同样要过 SSRF 校验，否则可被当作
        # 「把内网文件写到本地」的探针。
        # [P1-② 修复] 必须走 safe_urlopen，不能用裸 urlopen：裸 urlopen 默认跟随
        # 3xx 且**不再复检**，公网 URL 302 到 127.0.0.1 / 169.254.169.254 即可把
        # 内网内容写进本地文件。safe_urlopen 每跳重新校验，并把已校验 IP pin 到
        # 实际连接上（关闭 DNS 重绑定窗口）。
        try:
            assert_url_safe(url)
        except UnsafeURLError:
            return False
        try:
            headers = {
                "User-Agent": USER_AGENT,
                "Accept": "*/*"
            }
            req = urllib.request.Request(url, headers=headers)
            with safe_urlopen(req, timeout=self.timeout * 2, context=_DEFAULT_SSL_CONTEXT) as resp:
                if resp.status != 200:
                    return False
                from pathlib import Path
                dest = Path(dest_path)
                dest.parent.mkdir(parents=True, exist_ok=True)
                downloaded = 0
                too_big = False
                with open(dest, "wb") as f:
                    while True:
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        downloaded += len(chunk)
                        if downloaded > max_bytes:
                            too_big = True
                            break
                        f.write(chunk)
                if too_big:
                    # [G4 修复·半截文件] 旧实现超限直接 return False，已写入的
                    # 截断文件留在磁盘，下次可能被当成完整 PDF 使用。现删残留。
                    try:
                        dest.unlink(missing_ok=True)
                    except OSError:
                        pass
                    return False
                return True
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception("download_file 失败: %s", url)
            return False


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
