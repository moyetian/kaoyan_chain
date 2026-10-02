# -*- coding: utf-8 -*-
"""
KaoYan Intelligence · 多级抓取器与健康诊断 (Multi-tier Fetcher & Health Diagnostics)

抓取分层设计 (渐进式增强)：
  1. Tier 1 (默认): 纯 Python 标准库 urllib 轻量抓取，支持字符集自动检测、防盗链与超时控制
  2. Tier 2 (官方): 研招网标准化直通连接
  3. Tier 3 (可选插件): Playwright 无头浏览器渲染与 API 流量嗅探 (仅在用户主动安装时激活)

[P1 两级采集] :func:`fetch_with_fallback` 把 Tier 1 与 Tier 3 串成一条链：
HTTP 快路径受阻（SPA 空壳 / 403）且浏览器闸门开启时自动升级，升级失败保留
HTTP 原结果并把原因写进 ``FetchResult.escalation``（方案 v2 §5 阶段 2）。
"""

import logging
import ipaddress
import os
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

# [P1 两级采集] HTTP 层出现这些状态时，若浏览器闸门允许则自动升级渲染。
# 只包含「换浏览器大概率能解」的两类：SPA 空壳（BROWSER_REQUIRED）与 403 反爬；
# 超时/证书错误/内容为空等升级也大概率无解，不在此列（避免无谓开销与噪声）。
ESCALATABLE_STATUSES = ("BROWSER_REQUIRED", "HTTP_403")

#: :func:`fetch_with_fallback` 的合法抓取模式
FETCH_MODES = ("auto", "http", "browser")

#: 资源嗅探的分类枚举（只登记元信息，不存 body）
RESOURCE_KINDS = ("pdf", "media", "json", "doc")

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
    evidence_eligible: bool = True    # 未验证 TLS 的正文不得作为官方证据入库
    truncated: bool = False           # 正文是否因体积上限被截断（内容尾部已带 TRUNCATION_MARKER）
    resolved_links: Optional[Dict[str, Optional[str]]] = None
    #: [P0 跳转还原] ``{页面内提取的原链接: 会话内跟随后的最终链接}``；
    #: ``None`` = 未请求还原；某条的值 ``None`` = 该条未成功还原（保留原链接使用）。
    tier: str = "http"                 # [P1] 实际产出层："http" / "browser"
    escalation: Optional[str] = None   # [P1] 升级尝试终态：None=未尝试；"OK"=已升级成功；
    #:                                  其余为失败原因（BROWSER_DISABLED / BROWSER_NOT_INSTALLED /
    #:                                  BROWSER_UNAVAILABLE / BLOCKED / TIMEOUT / ABORTED / ...）
    resources: Optional[List[Dict[str, str]]] = None
    #: [P1 资源嗅探] ``[{"url":..., "kind":..., "content_type":...}]``。
    #: 只登记元信息（**绝不存 body**），供上层判断页面里是否有 PDF / 媒体 / 接口
    #: 可进一步获取；``kind`` 取值见 :data:`RESOURCE_KINDS`。


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
        # SSRF 防护由 ``safe_urlopen`` 统一执行（含 DNS pin 与逐跳重定向复检）。
        # 对 IP 字面量/localhost 先做一次无网络的明确拒绝，避免安全 opener 被
        # 测试桩或第三方适配器替换后出现直接访问内网的旁路；普通域名不在这里
        # 重复 DNS 解析，避免企业 DNS 兼容场景被误判，最终校验仍由 safe_urlopen
        # 完成。
        host = (urllib.parse.urlparse(url).hostname or "").strip().lower()
        literal_host = False
        try:
            ipaddress.ip_address(host.strip("[]"))
            literal_host = True
        except ValueError:
            # urllib/getaddrinfo also accepts decimal/hex IPv4 integer forms
            # (e.g. 2130706433 -> 127.0.0.1), which ipaddress does not parse
            # from a bare string. Keep those on the preflight path as well.
            literal_host = (host == "localhost" or host.endswith(".localhost")
                            or host.isdigit() or host.lower().startswith("0x"))
        if literal_host:
            try:
                assert_url_safe(url)
            except UnsafeURLError:
                return FetchResult(
                    url=url, status_code=0, content="", is_valid=False,
                    access_status="BLOCKED", headers={}, evidence_eligible=False)

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
                        evidence_eligible=not is_fallback_ssl,
                        truncated=truncated,
                    )

            except UnsafeURLError:
                return FetchResult(
                    url=url, status_code=0, content="", is_valid=False,
                    access_status="BLOCKED", headers={}, evidence_eligible=False)
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
    """Playwright 浏览器可选插件管理器 (Progressive Enhancement)

    分层降级（任一不满足即如实返回，绝不抛崩）：
      1. 闸门：``KY_BROWSER_ACQUISITION`` 默认 off（评测/headless 环境天然不可达；
         闸门判定在本类自身完成 —— 不依赖工具注册层，见整合方案 v2 §4.4）；
      2. 库探测：playwright 未安装 → ``BROWSER_NOT_INSTALLED``；
      3. 浏览器探测：msedge → chrome → 默认 chromium 全不可用 → ``BROWSER_UNAVAILABLE``。

    [阶段 0 安全加固 S1–S9]（2026-09-28，依据 browser_acquisition_integration_plan.md v2）
      S1 入口 SSRF 复检（assert_url_safe，与 HTTPFetcher 同口径）
      S2 context.route 全量拦截：非 http/https fail-closed；逐 host 复检（会话级缓存）；
         WS 双保险（route_web_socket 优先 + init script 哑实现兜底）；
         service_workers="block"；WebRTC 禁用；accept_downloads=False（并入 S8）
      S3 try/finally 保证 browser.close()
      S4 会话级 deadline（默认 20s）；goto 超时压缩为剩余预算
      S5 content() 2MB 截断 + TRUNCATION_MARKER；嗅探条数上限
      S6 status_code 取真实响应；验证码/反爬特征 → BLOCKED（绝不报 OK）
      S7 两级探测（库 + 浏览器内核），消除「库在浏览器不在」半态静默失败
      S8 accept_downloads=False（防撑盘）
      S9 异常详情只进日志；access_status 仅枚举值

    [P0 扩展]（2026-09-28，微信采集浏览器兜底）
      * ``should_stop`` 停止回调：GUI 关窗可中断（检查点快速退出 → ABORTED，
        finally 照常关闭浏览器，避免孤儿 Edge 进程）
      * ``resolve_links_selector`` 跳转还原：同一会话内提取链接并逐跳跟随
        （搜狗 ``/link?url=`` 需真实会话 cookie 才能还原真链），结果入
        ``FetchResult.resolved_links``
    """

    GATE_ENV = "KY_BROWSER_ACQUISITION"
    DEFAULT_DEADLINE_SEC = 20.0
    PAGE_CONTENT_LIMIT = 2 * 1024 * 1024
    #: 验证码/反爬特征（命中即 BLOCKED，不得当作有效页面；与 sogou provider 同口径）
    _BLOCK_MARKERS = ("SourceVerifyCode", "请协助验证", "请输入验证码",
                      "您的访问过于频繁", "antispider")

    #: [P1] 嗅探上限：接口 URL（沿用 S5 的 100）与资源元信息条目
    MAX_CAPTURED_APIS = 100
    MAX_CAPTURED_RESOURCES = 50

    #: [P1 资源分类] 后缀 → kind（先按 content-type 判定，未命中再按后缀）
    _RESOURCE_SUFFIXES = (
        ("pdf", (".pdf",)),
        ("media", (".mp4", ".m3u8", ".mp3", ".webm", ".mov", ".flv", ".ts", ".aac")),
        ("json", (".json", ".jsonp")),
        ("doc", (".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv")),
    )

    #: [P1 资源分类] content-type 片段 → kind
    _RESOURCE_CONTENT_TYPES = (
        ("application/pdf", "pdf"),
        ("application/json", "json"),
        ("video/", "media"),
        ("audio/", "media"),
    )

    # ---------- 闸门（权限下沉：不依赖工具注册） ----------

    @staticmethod
    def is_enabled() -> bool:
        """浏览器采集闸门。默认 off，必须显式开启（on/1/true/yes）。"""
        val = os.environ.get(BrowserPluginManager.GATE_ENV, "off")
        return str(val).strip().lower() in ("on", "1", "true", "yes")

    # ---------- [P1] 聚合状态与资源分类 ----------

    @staticmethod
    def status() -> str:
        """聚合可用状态（供两级采集判定能否升级）。

        Returns:
            ``"DISABLED"``（闸门关）/ ``"NOT_INSTALLED"``（库缺失）/
            ``"UNAVAILABLE"``（无浏览器内核）/ ``"READY"``。
        """
        if not BrowserPluginManager.is_enabled():
            return "DISABLED"
        if not BrowserPluginManager._has_library():
            return "NOT_INSTALLED"
        if BrowserPluginManager._detect_channel() == "UNAVAILABLE":
            return "UNAVAILABLE"
        return "READY"

    @staticmethod
    def _classify_resource(url: str, content_type: str = "") -> Optional[str]:
        """把一条网络响应归类为 ``pdf`` / ``media`` / ``json`` / ``doc`` / ``None``。

        ``None`` = 与考研取证无关（HTML 页面、图片、字体、CSS 等），**不登记**，
        避免 resources 被噪声淹没。只做字符串判定，绝不读取响应体。
        """
        ctype = (content_type or "").split(";")[0].strip().lower()
        for frag, kind in BrowserPluginManager._RESOURCE_CONTENT_TYPES:
            if frag in ctype:
                return kind
        path = urllib.parse.urlsplit(url or "").path.lower()
        for kind, suffixes in BrowserPluginManager._RESOURCE_SUFFIXES:
            if path.endswith(suffixes):
                return kind
        return None

    # ---------- 两级探测（S7） ----------

    @staticmethod
    def _has_library() -> bool:
        """库级探测：playwright 是否可导入。"""
        try:
            import playwright  # noqa: F401
            return True
        except ImportError:
            return False

    @staticmethod
    def _detect_channel() -> Optional[str]:
        """浏览器级探测：按 msedge → chrome → 默认 chromium 顺序找可用内核。

        返回 ``"msedge"`` / ``"chrome"`` / ``None``（Playwright 自带 chromium）
        / ``"UNAVAILABLE"``。只做文件系统探测，不启动浏览器。
        """
        import sys as _sys
        from pathlib import Path as _Path

        if _sys.platform == "win32":
            candidates = (
                ("msedge", (
                    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                )),
                ("chrome", (
                    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                )),
            )
        else:
            import shutil as _shutil
            _edge = tuple(p for p in (_shutil.which("microsoft-edge"),
                                      _shutil.which("microsoft-edge-stable")) if p)
            _chrome = tuple(p for p in (_shutil.which("google-chrome"),
                                        _shutil.which("chromium"),
                                        _shutil.which("chromium-browser")) if p)
            candidates = (("msedge", _edge), ("chrome", _chrome))

        for channel, paths in candidates:
            for p in paths:
                if p and _Path(p).exists():
                    return channel

        # Playwright 自带 chromium（用户自行 `playwright install` 的缓存目录）
        for cache in (_Path.home() / "AppData" / "Local" / "ms-playwright",
                      _Path.home() / ".cache" / "ms-playwright"):
            try:
                if cache.exists() and any(cache.glob("chromium*")):
                    return None
            except OSError:
                continue
        return "UNAVAILABLE"

    @staticmethod
    def is_available() -> bool:
        """聚合探测（S7）：库 + 浏览器内核都可用才为 True。"""
        if not BrowserPluginManager._has_library():
            return False
        return BrowserPluginManager._detect_channel() != "UNAVAILABLE"

    # ---------- 抓取主入口 ----------

    @staticmethod
    def _result(url: str, status: str) -> FetchResult:
        """统一构造失败结果（S9：access_status 只放枚举值，详情见日志）。"""
        return FetchResult(url=url, status_code=0, content="", is_valid=False,
                           access_status=status, headers={}, api_captured=[])

    @staticmethod
    def _resolve_links(page, selector: str, limit: int, budget_sec: float,
                       deadline: float, stopped) -> Dict[str, Optional[str]]:
        """[P0 跳转还原] 在浏览器会话内提取页面链接并逐跳跟随到最终地址。

        用途：搜狗微信结果的 ``/link?url=...&token=...`` 需要真实会话 cookie 才能
        还原到 ``mp.weixin.qq.com`` 真链，HTTP 直连无法完成。本方法在同一
        context 内逐条 ``goto``（``wait_until="commit"`` 最快拿到最终 URL）。

        预算：``budget_sec`` 为还原子预算（方案 §9：跳转还原 ≤10s），同时受
        会话级 ``deadline`` 约束；每跳超时压缩为剩余预算。绝不抛异常。

        Returns:
            ``{原链接: 最终链接或 None}``；提取不到任何链接时返回空 dict。
        """
        import time as _time
        try:
            hrefs = page.eval_on_selector_all(
                selector, "els => els.map(e => e.href)") or []
        except Exception:
            return {}
        hrefs = [h for h in hrefs
                 if isinstance(h, str) and h.startswith("http")][:max(0, int(limit))]
        if not hrefs:
            return {}

        started = _time.monotonic()
        out: Dict[str, Optional[str]] = {}
        for h in hrefs:
            if stopped():
                out.setdefault(h, None)
                continue
            remain = min(budget_sec - (_time.monotonic() - started),
                         deadline - _time.monotonic())
            if remain <= 0.2:
                out.setdefault(h, None)
                continue
            try:
                page.goto(h, timeout=int(remain * 1000), wait_until="commit")
                final = page.url or ""
                out[h] = final if final.startswith("http") else None
            except Exception:
                out[h] = None
        return out

    @staticmethod
    def fetch_with_browser(url: str, timeout_sec: int = 15,
                           deadline: Optional[float] = None,
                           should_stop=None,
                           resolve_links_selector: Optional[str] = None,
                           resolve_limit: int = 5,
                           resolve_budget_sec: float = 10.0) -> FetchResult:
        """渲染页面并监听网络流量（阶段 0 加固 + P0 中断/跳转还原扩展）。

        分层返回：``BROWSER_DISABLED`` / ``BROWSER_NOT_INSTALLED`` /
        ``BROWSER_UNAVAILABLE`` / ``BLOCKED`` / ``TIMEOUT`` / ``ABORTED`` /
        ``OK`` / ``BROWSER_ERROR``（枚举，不含异常文本）。

        :param should_stop: 可选停止回调（GUI 关窗中断用）。返回 True 时在最近
            检查点快速退出（``ABORTED``），保证 finally 关闭浏览器、不留孤儿进程。
        :param resolve_links_selector: 非空时，抓取完成后在**同一会话内**提取
            匹配选择器的链接并逐跳跟随还原最终地址（见 :meth:`_resolve_links`）。
        :param resolve_limit: 跳转还原的条数上限。
        :param resolve_budget_sec: 跳转还原子预算（秒），另受 ``deadline`` 约束。
        """
        import time as _time

        if not BrowserPluginManager.is_enabled():
            return BrowserPluginManager._result(url, "BROWSER_DISABLED")
        if not BrowserPluginManager._has_library():
            return BrowserPluginManager._result(url, "BROWSER_NOT_INSTALLED")

        # S1: 入口 SSRF（与 HTTPFetcher 同口径；内网 IP 字面量/回环/云元数据即拒）
        try:
            assert_url_safe(url)
        except UnsafeURLError:
            return BrowserPluginManager._result(url, "BLOCKED")

        channel = BrowserPluginManager._detect_channel()
        if channel == "UNAVAILABLE":
            return BrowserPluginManager._result(url, "BROWSER_UNAVAILABLE")

        # S4: 会话级预算
        _deadline = (float(deadline) if deadline is not None
                     else _time.monotonic() + BrowserPluginManager.DEFAULT_DEADLINE_SEC)

        def _remaining(cap: float) -> float:
            return max(0.1, min(float(cap), _deadline - _time.monotonic()))

        def _stopped() -> bool:
            """停止回调（fail-closed：回调自身异常视作要求停止）。"""
            if should_stop is None:
                return False
            try:
                return bool(should_stop())
            except Exception:
                return True

        if _deadline - _time.monotonic() <= 0:
            return BrowserPluginManager._result(url, "TIMEOUT")
        if _stopped():
            return BrowserPluginManager._result(url, "ABORTED")

        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            return BrowserPluginManager._result(url, "BROWSER_NOT_INSTALLED")

        captured_apis: List[str] = []
        captured_resources: List[Dict[str, str]] = []
        verified_hosts: set = set()  # S2 配套：host 解析缓存（getaddrinfo 无超时，必须去重）

        def _guard_route(route, request):
            """S2: 每个请求（含重定向每跳/子资源）先过 SSRF；非 http(s) fail-closed。"""
            try:
                r_url = request.url or ""
                if not (r_url.startswith("http://") or r_url.startswith("https://")):
                    route.abort()
                    return
                host = urllib.parse.urlsplit(r_url).hostname or ""
                if host not in verified_hosts:
                    assert_url_safe(r_url)
                    verified_hosts.add(host)
                route.continue_()
            except Exception:
                route.abort()

        try:
            with sync_playwright() as p:
                browser = None
                try:
                    browser = p.chromium.launch(channel=channel, headless=True)
                    context = browser.new_context(
                        user_agent=USER_AGENT,
                        service_workers="block",   # S2: SW 请求不经 route，直接封
                        accept_downloads=False,    # S8
                    )
                    context.route("**/*", _guard_route)
                    # S2: WS 双保险 —— 新版本 route_web_socket 精确拦截；
                    # 旧版本用 init script 哑实现兜底（构造即抛，连接永不建立）。
                    _ws_route = getattr(context, "route_web_socket", None)
                    if callable(_ws_route):
                        try:
                            _ws_route("**/*", lambda ws: ws.close())
                        except Exception:
                            pass
                    context.add_init_script(
                        "window.WebSocket = function() {"
                        "  throw new Error('blocked by ky browser sandbox');"
                        "};"
                        "if (window.RTCPeerConnection) { window.RTCPeerConnection = undefined; }"
                    )
                    page = context.new_page()
                    page.set_default_timeout(int(_remaining(timeout_sec) * 1000))

                    def handle_response(response):
                        try:
                            r_url = response.url or ""
                            low = r_url.lower()
                            if any(kw in low for kw in ("/api/", "json", "list", "query", "article")):
                                # S5: 嗅探条数上限
                                if len(captured_apis) < BrowserPluginManager.MAX_CAPTURED_APIS:
                                    captured_apis.append(r_url)
                            # [P1] 资源分类：只登记元信息（url/kind/content_type），不存 body
                            if len(captured_resources) < BrowserPluginManager.MAX_CAPTURED_RESOURCES:
                                try:
                                    ctype = (getattr(response, "headers", None) or {}).get(
                                        "content-type", "") or ""
                                except Exception:
                                    ctype = ""
                                kind = BrowserPluginManager._classify_resource(r_url, ctype)
                                if kind:
                                    captured_resources.append(
                                        {"url": r_url, "kind": kind, "content_type": ctype})
                        except Exception:
                            pass

                    page.on("response", handle_response)
                    if _stopped():
                        return BrowserPluginManager._result(url, "ABORTED")
                    resp = page.goto(url, timeout=int(_remaining(timeout_sec) * 1000),
                                     wait_until="domcontentloaded")
                    status_code = getattr(resp, "status", 0) or 0  # S6: 真实状态码
                    content = page.content() or ""
                    # S5: 内容截断（截断如实暴露，与 HTTPFetcher 同语义）
                    _truncated = False
                    if len(content) > BrowserPluginManager.PAGE_CONTENT_LIMIT:
                        content = (content[:BrowserPluginManager.PAGE_CONTENT_LIMIT]
                                   + TRUNCATION_MARKER)
                        _truncated = True
                    # S6: 验证码/反爬特征页不得报 OK（验证页无结果块，不做跳转还原）
                    if any(m in content for m in BrowserPluginManager._BLOCK_MARKERS):
                        return FetchResult(
                            url=url, status_code=status_code, content=content,
                            is_valid=False, access_status="BLOCKED", headers={},
                            raw_bytes_len=len(content.encode("utf-8", "replace")),
                            api_captured=captured_apis, truncated=_truncated,
                            tier="browser", resources=captured_resources or None)
                    # [P0] 跳转还原：会话内逐跳跟随（搜狗 /link 需会话 cookie）
                    resolved_links = None
                    if resolve_links_selector and not _stopped():
                        resolved_links = BrowserPluginManager._resolve_links(
                            page, resolve_links_selector, resolve_limit,
                            resolve_budget_sec, _deadline, _stopped)
                    return FetchResult(
                        url=url, status_code=status_code, content=content,
                        is_valid=True, access_status="OK", headers={},
                        raw_bytes_len=len(content.encode("utf-8", "replace")),
                        api_captured=captured_apis, truncated=_truncated,
                        resolved_links=resolved_links,
                        tier="browser", resources=captured_resources or None)
                finally:
                    if browser is not None:
                        try:
                            browser.close()  # S3
                        except Exception:
                            pass
        except Exception as exc:
            # S9: 详情只进日志；对外仅枚举值
            _LOG.warning("浏览器抓取失败（%s）: %s: %s", url, type(exc).__name__, exc)
            return BrowserPluginManager._result(url, "BROWSER_ERROR")


# ─────────────────────────────────────────────────────────────────────────────
# [P1] 两级采集统一入口 (HTTP 快路径 → 浏览器升级)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_with_fallback(url: str, mode: str = "auto", timeout: int = 6,
                        referer: Optional[str] = None,
                        extra_headers: Optional[Dict[str, str]] = None,
                        allow_insecure_ssl: bool = False,
                        browser_timeout_sec: int = 15,
                        should_stop=None) -> FetchResult:
    """两级采集统一入口：HTTP 快路径优先，受阻时按闸门升级到浏览器渲染。

    设计要点（依据整合方案 v2 §5 阶段 2）
    --------------------------------
    * ``mode="http"``：只走 :class:`HTTPFetcher`，**绝不**启动浏览器（默认行为，
      与历史调用完全等价，零新增开销）。
    * ``mode="browser"``：只走 :class:`BrowserPluginManager`（闸门关闭时如实返回
      ``BROWSER_DISABLED``，不偷偷回落 HTTP —— 调用方显式选了浏览器就该知道结果）。
    * ``mode="auto"``：HTTP 先走；**仅当** HTTP 层 ``is_valid=False`` 且
      ``access_status`` 属于 :data:`ESCALATABLE_STATUSES`（SPA 空壳 / 403 反爬）
      时才尝试升级。升级成功（浏览器结果有效）则整体替换为浏览器结果
      （``tier="browser"``）；升级失败/不可用则**保留 HTTP 结果**并把原因写进
      ``escalation`` —— 绝不因为升级失败把原本可用的 HTTP 结果丢掉。

    升级前后都受闸门 ``KY_BROWSER_ACQUISITION`` 控制（默认 off），且升级前先做
    :meth:`BrowserPluginManager.status` 聚合判定，避免在没有内核的机器上白等。

    :raises ValueError: ``mode`` 不在 :data:`FETCH_MODES` 内（调用方应为工具层）。
    """
    mode_n = str(mode or "auto").strip().lower()
    if mode_n not in FETCH_MODES:
        raise ValueError(f"mode 仅支持 {'/'.join(FETCH_MODES)}，收到: {mode!r}")

    if mode_n == "browser":
        return BrowserPluginManager.fetch_with_browser(
            url, timeout_sec=browser_timeout_sec, should_stop=should_stop)

    result = HTTPFetcher(timeout=timeout).fetch(
        url, referer=referer, extra_headers=extra_headers,
        allow_insecure_ssl=allow_insecure_ssl)
    result.tier = "http"

    needs_upgrade = (mode_n == "auto" and not result.is_valid
                     and result.access_status in ESCALATABLE_STATUSES)
    if not needs_upgrade:
        return result

    st = BrowserPluginManager.status()
    if st != "READY":
        # 闸门/依赖不满足：如实记录原因，保留 HTTP 原结果
        result.escalation = f"BROWSER_{st}"
        return result

    upgraded = BrowserPluginManager.fetch_with_browser(
        url, timeout_sec=browser_timeout_sec, should_stop=should_stop)
    result.escalation = upgraded.access_status
    if upgraded.is_valid:
        upgraded.escalation = "OK"
        return upgraded
    return result
