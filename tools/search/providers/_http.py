# -*- coding: utf-8 -*-
"""
Provider 共用的抓取与文本清洗 (Robust HTTP Layer)

核心能力：
1. 指数退避重试 (Exponential Backoff with Jitter)：
   对网络超时、连接重置 (ConnectionReset) 以及 HTTP 429/502/503/504 瞬时错误实施平滑重试与抖动，
   避免高频重试引发雪崩，同时提升网络波动下的可用性。
2. 字符集自适应解析 (Adaptive Encoding)：
   结合 Content-Type 响应头与 HTML <meta charset> 标签嗅探，
   按 UTF-8 -> GB18030 -> GBK -> GB2312 -> CP936 顺序自适应解码并带 replace 兜底，
   坚决杜绝高校与研招网中文乱码问题。
3. 现代化浏览器请求头与 User-Agent 轮换：
   模拟真实浏览器特征，规避反爬特征指纹。
4. 深度文本清洗 (HTML Cleaning & Entity Unescaping)：
   剥除脚本、样式、注释与 HTML 标签，双重反转义 HTML 实体，清洗窄空格与零宽控制符。
"""

from __future__ import annotations

import base64
import html
import http.client
import logging
import random
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Any, Dict, List, Optional, Tuple

from ..providers.base import ProviderError

try:  # 网络访问安全与解压体积上限（双导入路径兼容）
    from net_guard import MAX_DECOMPRESSED_BYTES, MAX_HTTP_RESPONSE_BYTES, zlib_limited
except ImportError:  # pragma: no cover - 兼容 tools. 包式导入
    from tools.net_guard import (  # type: ignore
        MAX_DECOMPRESSED_BYTES,
        MAX_HTTP_RESPONSE_BYTES,
        zlib_limited,
    )

_LOG = logging.getLogger(__name__)

#: 浏览器 UA 轮换池
USER_AGENTS: List[str] = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) "
        "Gecko/20100101 Firefox/126.0"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
]

#: 兼容旧版单一常量导入
USER_AGENT = USER_AGENTS[0]


def get_random_user_agent() -> str:
    """从 UA 轮换池中获取随机 User-Agent。"""
    return random.choice(USER_AGENTS)


def get_browser_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """获取标准浏览器请求头，包含 User-Agent 随机轮换。"""
    headers = {
        "User-Agent": get_random_user_agent(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Ch-Ua": '"Chromium";v="125", "Not.A/Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }
    if extra:
        headers.update(extra)
    return headers


#: 通用浏览器请求头（兼容旧版常量）
BROWSER_HEADERS: Dict[str, str] = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Upgrade-Insecure-Requests": "1",
}

#: 反爬/验证页特征（命中即说明「不是没结果，是被挡了」）
ANTI_BOT_MARKERS = (
    "anomaly", "captcha", "verify", "请协助验证", "请输入验证码",
    "SourceVerifyCode", "访问过于频繁", "unusual traffic", "antispider",
)

#: 瞬时可重试 HTTP 状态码
RETRYABLE_STATUS_CODES = {429, 502, 503, 504}


def looks_like_anti_bot(html_text: str) -> str:
    """判断响应是否像反爬/验证页，返回命中的特征词（无则空串）。"""
    low = str(html_text or "").lower()
    for marker in ANTI_BOT_MARKERS:
        if marker.lower() in low:
            return marker
    return ""


def decompress_body(raw_data: bytes,
                    headers: Optional[Dict[str, str]] = None,
                    resp_headers: Optional[Dict[str, str]] = None) -> bytes:
    """处理 HTTP 响应 payload 的 gzip / deflate 解压缩 (统一处理 gzip / deflate 及 magic bytes 自动嗅探)。

    [P2 修复] 解压改为**带体积上限**（``net_guard.zlib_limited``）：旧实现直接
    ``gzip.decompress`` / ``zlib.decompress``，几十 KB 的「解压炸弹」能膨胀成几十 MB。
    这里保留旧实现「流损坏/不完整就原样返回 raw_data」的语义（``require_eof=True``，
    与 ``gzip.decompress`` 的严格判定一致），只有**正常收尾**的流才接受解压结果；
    正常流解压后仍超限则截断到 ``MAX_DECOMPRESSED_BYTES``。
    """
    if not raw_data:
        return raw_data

    hdrs = headers if headers is not None else resp_headers
    encoding = ""
    if hdrs:
        encoding = (hdrs.get("Content-Encoding") or hdrs.get("content-encoding") or "").lower().strip()

    if encoding in ("gzip", "x-gzip") or raw_data.startswith(b"\x1f\x8b"):
        try:
            out, _truncated = zlib_limited(
                raw_data, 16 + zlib.MAX_WBITS, MAX_DECOMPRESSED_BYTES, require_eof=True)
            return out
        except Exception as e:
            _LOG.debug("gzip 解压回退: %s", e)
    elif encoding in ("deflate", "zlib"):
        try:
            out, _truncated = zlib_limited(
                raw_data, zlib.MAX_WBITS, MAX_DECOMPRESSED_BYTES, require_eof=True)
            return out
        except Exception:
            try:
                out, _truncated = zlib_limited(
                    raw_data, -zlib.MAX_WBITS, MAX_DECOMPRESSED_BYTES, require_eof=True)
                return out
            except Exception as e:
                _LOG.debug("deflate 解压回退: %s", e)

    return raw_data


def _read_capped(resp, limit: int = MAX_HTTP_RESPONSE_BYTES) -> bytes:
    """带体积上限读取响应体。

    真实 ``http.client.HTTPResponse.read(n)`` 支持上限；少数轻量响应对象（含测试
    替身）只实现了无参 ``read()``，此时退化为整读并在此处告警 —— 上限由调用方
    在解压阶段兜底。
    """
    try:
        return resp.read(limit)
    except TypeError:
        _LOG.debug("响应对象不支持 read(n)，退化为无参 read()")
        return resp.read()


decompress_response = decompress_body  # 别名兼容


def detect_and_decode(raw_bytes: bytes,
                      headers: Optional[Dict[str, str]] = None) -> str:
    """自适应编码嗅探与解码。

    步骤：
    1. 检查 Content-Type 响应头中的 charset
    2. 检查 HTML 前 4096 字节中的 <meta charset="..."> 或 <meta http-equiv="Content-Type" ...>
    3. 优先尝试检测出的 charset
    4. 逐个尝试 utf-8, gb18030, gbk, gb2312, cp936
    5. 最后以 replace 兜底解码，彻底杜绝中文乱码导致程序崩溃
    """
    if not raw_bytes:
        return ""

    # 1. 响应头检测
    header_charset = ""
    if headers:
        ct = headers.get("Content-Type") or headers.get("content-type") or ""
        m_hdr = re.search(r"charset=([\w\-]+)", ct, re.IGNORECASE)
        if m_hdr:
            header_charset = m_hdr.group(1).strip().lower()

    # 2. HTML meta 标签嗅探
    meta_charset = ""
    sample = raw_bytes[:4096].decode("latin1", errors="ignore")
    m1 = re.search(r'<meta[^>]+charset=["\']?([\w\-]+)', sample, re.IGNORECASE)
    if m1:
        meta_charset = m1.group(1).strip().lower()
    else:
        m2 = re.search(
            r'<meta[^>]+content=["\'][^"\']*charset=([\w\-]+)',
            sample,
            re.IGNORECASE
        )
        if m2:
            meta_charset = m2.group(1).strip().lower()

    # 规范化别名
    def _normalize(enc: str) -> str:
        enc = enc.lower()
        if enc in ("utf8", "utf-8"):
            return "utf-8"
        if enc in ("gb2312", "gbk", "cp936", "windows-936"):
            return "gb18030"
        return enc

    candidate_encodings: List[str] = []
    for cand in (header_charset, meta_charset):
        if cand:
            norm = _normalize(cand)
            if norm not in candidate_encodings:
                candidate_encodings.append(norm)

    # 默认候选链：utf-8 -> gb18030 (超集覆盖 gbk, gb2312, cp936) -> gbk -> gb2312 -> cp936
    default_chain = ["utf-8", "gb18030", "gbk", "gb2312", "cp936"]
    for enc in default_chain:
        if enc not in candidate_encodings:
            candidate_encodings.append(enc)

    # 严格解码尝试
    for enc in candidate_encodings:
        try:
            return raw_bytes.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue

    # 兜底：尝试以 gb18030 替换解码，若失败则 utf-8 替换解码
    try:
        return raw_bytes.decode("gb18030", errors="replace")
    except Exception:
        return raw_bytes.decode("utf-8", errors="replace")


def get_text(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 10,
    max_retries: int = 3,
    backoff_base: float = 0.3,
    backoff_max: float = 3.0,
    allow_insecure_ssl: bool = False,
    ssl_status: Optional[Dict[str, Any]] = None,
) -> str:
    """安全抓取网页 HTML 文本，具备指数退避重试与编码自适应。

    :param url: 目标 URL
    :param headers: 自定义请求头
    :param timeout: 单次请求超时时间（秒）
    :param max_retries: 最大重试次数
    :param backoff_base: 退避基数（秒）
    :param backoff_max: 最大退避时间（秒）
    :param allow_insecure_ssl: **显式** opt-in 开关，默认 ``False``。
        默认语义：TLS 证书校验失败即如实失败（抛 ``ProviderError``），
        **绝不静默降级**为未验证连接。仅当调用方明确传 ``True``（例如确知
        某高校站点使用自签名/过期证书且必须抓取）时，才会在证书错误后以
        **未验证**连接重试一次，并把 ``ssl_verified=False`` 写入 ``ssl_status``
        且打 WARNING 日志。
    :param ssl_status: 可选出参字典；函数会写入 ``{"ssl_verified": bool}``，
        调用方据此判断本次抓取是否走了未验证 TLS。
    :return: 解码清洗后的 HTML 文本
    :raises ProviderError: 请求重试耗尽、证书校验失败或致命错误
    """
    url = str(url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ProviderError(f"不支持的 URL 协议: {url}")

    ssl_ctx = ssl.create_default_context()
    # 未验证上下文仅在调用方**显式** opt-in 时构造，绝不作为失败后的自动降级路径
    ssl_fallback_ctx: Optional[ssl.SSLContext] = None
    if allow_insecure_ssl:
        ssl_fallback_ctx = ssl.create_default_context()
        ssl_fallback_ctx.check_hostname = False
        ssl_fallback_ctx.verify_mode = ssl.CERT_NONE

    if ssl_status is not None:
        ssl_status["ssl_verified"] = True

    last_error: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        if attempt > 0:
            # 指数退避 + 随机抖动 (Jitter)
            jitter = random.uniform(0.05, 0.25)
            delay = min(backoff_max, backoff_base * (2 ** (attempt - 1))) + jitter
            _LOG.warning("第 %d 次重试抓取 %s，退避等待 %.2f 秒 (原因: %s)",
                         attempt, url, delay, last_error)
            time.sleep(delay)

        req_headers = get_browser_headers(headers)
        req = urllib.request.Request(url, headers=req_headers)

        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
                # [S6] 删除重构残留死变量 status_code（赋值后从未使用）。
                resp_headers = dict(resp.headers)
                raw_data = _read_capped(resp)
                raw_data = decompress_body(raw_data, resp_headers)
                return detect_and_decode(raw_data, resp_headers)

        except urllib.error.HTTPError as e:
            last_error = e
            status = e.code
            if status in RETRYABLE_STATUS_CODES and attempt < max_retries:
                continue
            raise ProviderError(f"抓取失败（HTTP {status}）") from e

        except (urllib.error.URLError, socket.timeout, TimeoutError,
                ConnectionResetError, http.client.RemoteDisconnected, ConnectionError) as e:
            last_error = e
            reason_str = str(getattr(e, "reason", e)).lower()

            # TLS 证书错误：默认**如实失败**，不再静默降级为未验证连接。
            # 仅当调用方显式 ``allow_insecure_ssl=True`` 时才做一次未验证重试。
            is_ssl_err = "certificate" in reason_str or "ssl" in reason_str
            if is_ssl_err:
                if ssl_fallback_ctx is None:
                    raise ProviderError(
                        f"TLS 证书校验失败（未降级）: {e}；如确需抓取该站点的"
                        "自签名/过期证书，请显式传入 allow_insecure_ssl=True"
                    ) from e
                _LOG.warning(
                    "TLS 证书校验失败，按调用方显式 opt-in 降级为**未验证**连接"
                    "重试（ssl_verified=False）: %s (%s)", url, e)
                try:
                    with urllib.request.urlopen(req, timeout=timeout, context=ssl_fallback_ctx) as resp:
                        resp_headers = dict(resp.headers)
                        raw_data = _read_capped(resp)
                        raw_data = decompress_body(raw_data, resp_headers)
                        if ssl_status is not None:
                            ssl_status["ssl_verified"] = False
                        return detect_and_decode(raw_data, resp_headers)
                except Exception as ssl_e:
                    raise ProviderError(
                        f"TLS 证书校验失败，且显式启用未验证连接后仍失败: {ssl_e}"
                    ) from ssl_e

            # 判断是否为可重试的网络超时或连接重置
            is_timeout = isinstance(e, (socket.timeout, TimeoutError)) or "timed out" in reason_str
            is_conn_reset = isinstance(e, ConnectionResetError) or "reset" in reason_str or "connection" in reason_str
            if (is_timeout or is_conn_reset) and attempt < max_retries:
                continue

            raise ProviderError(f"请求失败: {e}") from e

        except Exception as e:
            last_error = e
            raise ProviderError(f"抓取发生未预期异常: {e}") from e

    raise ProviderError(f"请求重试耗尽: {last_error}")


def clean_text(raw_text: str) -> str:
    """去标签/实体/控制字符，得到单行可读文本。

    具备双重反转义与零宽字符清洗，防止 GBK 与控制台输出异常。
    """
    if not raw_text:
        return ""

    # 1. 剔除脚本、样式与特殊区块
    text = re.sub(
        r"<(script|style|nav|footer|header|noscript|iframe)[^>]*>.*?</\1>",
        " ",
        raw_text,
        flags=re.DOTALL | re.IGNORECASE
    )
    # 2. 剔除 HTML 注释
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    # 3. 剔除所有 HTML 标签
    text = re.sub(r"<[^>]+>", " ", text)
    # 4. 反转义实体
    text = html.unescape(text)
    # 5. 若反转义暴露出新的 HTML 标签 (如 &lt;b&gt; -> <b>)，二次剥离
    if "<" in text and ">" in text:
        text = re.sub(r"<[^>]+>", " ", text)
    # 6. 二次反转义双重编码实体 (如 &amp;nbsp; -> &nbsp; -> 空格)
    if "&" in text:
        text = html.unescape(text)
    # 7. 清洗窄空格、零宽字符、非破坏空格与控制字符
    text = re.sub(
        r"[\u2000-\u200f\u202f\u205f\u3000\ufeff\u00a0\x00-\x08\x0b\x0c\x0e-\x1f\x7f]",
        " ",
        text
    )
    return re.sub(r"\s+", " ", text).strip()


def clean_ddg_url(raw_url: str) -> str:
    """还原 DuckDuckGo 的 `//duckduckgo.com/l/?uddg=...` 跳转链接。"""
    if not raw_url:
        return ""
    if "uddg=" in raw_url:
        try:
            params = urllib.parse.parse_qs(urllib.parse.urlparse(raw_url).query)
            if params.get("uddg"):
                return params["uddg"][0]
        except Exception:                          # pragma: no cover
            return raw_url
    if raw_url.startswith("//"):
        return "https:" + raw_url
    return raw_url


def clean_bing_url(raw_url: str) -> str:
    """还原 Bing 的 `/ck/a?...&u=a1<base64>` 跳转链接。"""
    if not raw_url:
        return ""
    if "/ck/a?" in raw_url and "u=" in raw_url:
        try:
            m = re.search(r"[?&]u=([^&]+)", raw_url)
            if m:
                val = m.group(1)
                if val.startswith("a1"):
                    val = val[2:]
                val += "=" * (-len(val) % 4)
                decoded = base64.b64decode(val).decode("utf-8", errors="ignore")
                if decoded.startswith("http"):
                    return decoded
        except Exception:                          # pragma: no cover
            return raw_url
    return raw_url


def absolute(url: str, base: str) -> str:
    """把相对链接补成绝对链接（搜狗的 /link?url= 属于此类）。"""
    if not url:
        return ""
    if url.startswith(("http://", "https://")):
        return url
    return urllib.parse.urljoin(base, url)


__all__ = [
    "ANTI_BOT_MARKERS",
    "BROWSER_HEADERS",
    "RETRYABLE_STATUS_CODES",
    "USER_AGENT",
    "USER_AGENTS",
    "absolute",
    "clean_bing_url",
    "clean_ddg_url",
    "clean_text",
    "decompress_body",
    "decompress_response",
    "detect_and_decode",
    "get_browser_headers",
    "get_random_user_agent",
    "get_text",
    "looks_like_anti_bot",
]
