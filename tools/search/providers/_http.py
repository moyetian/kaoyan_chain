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
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Any, Dict, List, Optional, Tuple

from ..providers.base import ProviderError

try:  # 网络访问安全与解压体积上限（双导入路径兼容）
    from net_guard import (
        MAX_DECOMPRESSED_BYTES,
        MAX_HTTP_RESPONSE_BYTES,
        UnsafeURLError,
        safe_urlopen,
        zlib_limited,
    )
except ImportError:  # pragma: no cover - 兼容 tools. 包式导入
    from tools.net_guard import (  # type: ignore
        MAX_DECOMPRESSED_BYTES,
        MAX_HTTP_RESPONSE_BYTES,
        UnsafeURLError,
        safe_urlopen,
        zlib_limited,
    )

# [退避/节流单一真源] 仓库此前只有本模块的「指数+加性抖动」与 fetcher 的 full jitter
# 两套节奏；同机不同步的重试在反爬站点上等于惊群，会加重封禁。统一到 http_backoff。
try:  # 源码脚本式（``py tools/xxx.py``）
    from http_backoff import TokenBucket, retry_after_or_jitter
except ImportError:  # pragma: no cover - 包式导入
    from tools.http_backoff import (  # type: ignore
        TokenBucket,
        retry_after_or_jitter,
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
#: [P2 修复·2026-10-08] 原第三项是裸 ``"verify"`` —— 子串匹配会命中任何含该词的
#: 正常页面（英文引导语「please verify your status」、JS 标识符 ``verifyForm``），
#: 把正常结果页误判成反爬页 → 源被错误记为失败。收紧为「人机验证」专属句式
#: （Cloudflare/Google 拦截页的 "Verify you are human" 类文案）；中文验证页与
#: ``SourceVerifyCode``/``unusual traffic`` 等特征不受影响。
#:
#: [BOT-M1 修复·2026-10-09] 裸 ``"anomaly"`` 同属通用学术词，且本函数是对
#: **整页 HTML**（含每条结果的标题与摘要）做子串匹配——检索「anomaly detection」
#: 这类学术主题时，正常结果页必然命中 → 双端点皆被判反爬 → raise 文案含
#: 「反爬」标记 → health 侧首次即 600s 硬封（学术检索被误封）。
#: 收紧为反爬页**专属**短语（"unusual anomaly" / "anomaly detected"），
#: 既保住真反爬页检出，又不再误伤正常学术结果页。
ANTI_BOT_MARKERS = (
    "captcha", "verify you are", "verify that you",
    "unusual anomaly", "anomaly detected",
    "请协助验证", "请输入验证码",
    "SourceVerifyCode", "访问过于频繁", "unusual traffic", "antispider",
)

#: 瞬时可重试 HTTP 状态码
RETRYABLE_STATUS_CODES = {429, 502, 503, 504}

# ── [节流] 按 host 的最小请求间隔（单一真源：tools/http_backoff.TokenBucket）──
# [为什么要节流] bing/ddg/sogou/tavily 四源共用 ``get_text`` 一个出口，而仓库此前
# **全无请求间隔控制**：多路检索（search_planned 默认 4 路）× 多轮追问会把请求数
# 放大数倍，实测 DDG 约 20 次密集请求后即返回验证页。节流是唯一能在「不减少结果」
# 的前提下降低封禁概率的手段（丢弃结果由 serve-stale 负责）。
#: 各源的放行速率（请求/秒）与突发容量。默认保守 1 req/s、burst=1；反爬更严的源
#: （sogou 微信、bing）单独放慢。**刻意不配 tavily 更高**——它与 bing/ddg 共享
#: 同一批院校站点，放快等于自己踩自己。
THROTTLE_RATES: Dict[str, Tuple[float, int]] = {
    "default": (1.0, 1),
    "www.bing.com": (0.5, 1),
    "weixin.sogou.com": (0.5, 1),
    "html.duckduckgo.com": (1.0, 1),
}

#: host → TokenBucket（进程内单例；跨进程不做持久化——节流是「本进程请求节奏」，
#: 落盘反而会让重启后的第一轮请求凭陈旧节奏排队，语义混乱）。
_THROTTLE_LOCK = threading.Lock()
_THROTTLE_BUCKETS: Dict[str, TokenBucket] = {}

#: 节流等待上限（秒）。宁可偶尔放过一次，也不让调用方的整体时间预算被节流吃掉
#: ——「晚点返回」比「不返回」对考生更有用。
THROTTLE_MAX_WAIT = 5.0


def _throttle_for(host: str) -> TokenBucket:
    """取（或惰性建）该 host 的令牌桶。"""
    key = (host or "").lower()
    rate, burst = THROTTLE_RATES["default"]
    for domain, conf in THROTTLE_RATES.items():
        if domain != "default" and key.endswith(domain):
            rate, burst = conf
            break
    with _THROTTLE_LOCK:
        bucket = _THROTTLE_BUCKETS.get(key)
        if bucket is None:
            bucket = TokenBucket(rate_per_sec=rate, burst=burst)
            _THROTTLE_BUCKETS[key] = bucket
        return bucket


def respect_rate_limit(url: str, *, max_wait: float = THROTTLE_MAX_WAIT) -> float:
    """按 host 施加最小请求间隔，返回实际等待秒数（0 表示无需等待）。

    只在**真正发请求前**调用；不改变任何失败语义与返回结构（超时/重试/异常一律
    原样上抛）。等待超过 ``max_wait`` 时放弃等待并放行——节流是保护措施，不该
    成为新的超时来源。
    """
    host = urllib.parse.urlparse(str(url or "")).netloc
    if not host:
        return 0.0
    bucket = _throttle_for(host)
    if bucket.try_acquire(host):
        return 0.0
    delay = min(bucket.reserve(host), float(max_wait))
    if delay > 0:
        _LOG.debug("节流：%s 请求过快，等待 %.2f 秒", host, delay)
        time.sleep(delay)
        bucket.try_acquire(host)
    return delay


def reset_rate_limit() -> None:
    """清空节流桶（测试用；避免真实等待）。"""
    with _THROTTLE_LOCK:
        _THROTTLE_BUCKETS.clear()


def _retry_after_seconds(headers: Any) -> Optional[float]:
    """读取 ``Retry-After``（仅整数秒形态）；非法时返回 ``None`` 交退避公式处理。

    与 ``intelligence/fetcher._retry_after_seconds`` 同口径（HTTP-date 形态不解析：
    抓取场景不值得为它引入时钟偏差处理）。**保留本模块既有的 Retry-After 解析**，
    只是把它交给单一真源的 ``retry_after_or_jitter`` 统一决策。
    """
    if not headers:
        return None
    try:
        raw = headers.get("Retry-After") or headers.get("retry-after")
    except AttributeError:
        return None
    if raw is None:
        return None
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


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
    #: 上一次失败响应携带的 ``Retry-After``（秒）。反爬站点常用 429/503 告知
    #: 「多久后再来」，忽略它会立刻重试并再被拒 —— 故传给退避公式优先采用。
    _pending_retry_after: Optional[float] = None

    for attempt in range(max_retries + 1):
        if attempt > 0:
            # [退避单一真源] 改为 full jitter（AWS）：``U(0, min(cap, base·2^n))``。
            # [为什么改] 本模块原用「min(cap, base·2^(n-1)) + U(0.05,0.25)」
            # ——**加性抖动**让所有客户端的等待区间高度重叠，同机多路检索几乎齐步
            # 重试，对反爬站点而言等价于惊群，恰好加重封禁。full jitter 把等待打散
            # 到整个区间，是 AWS 论证过的更优口径。Retry-After 仍优先。
            delay = retry_after_or_jitter(attempt - 1, _pending_retry_after,
                                         base=backoff_base, cap=backoff_max)
            _LOG.warning("第 %d 次重试抓取 %s，退避等待 %.2f 秒 (原因: %s)",
                         attempt, url, delay, last_error)
            time.sleep(delay)
            _pending_retry_after = None

        req_headers = get_browser_headers(headers)
        req = urllib.request.Request(url, headers=req_headers)

        # [节流] 真正发请求前按 host 限速。只影响「何时发」，不影响成败语义。
        respect_rate_limit(url)

        try:
            # [审计 2026-09-30 P1-7 出站收敛] 经 net_guard.safe_urlopen 发送：
            # 初始 URL 与每次 3xx 跳转都做 SSRF 校验，并把已校验 IP pin 到连接上；
            # 体积上限（_read_capped）与解压保护（decompress_body）、指数退避重试、
            # 自定义 SSL context（含显式 opt-in 的未验证回退）全部保留不变。
            with safe_urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
                # [S6] 删除重构残留死变量 status_code（赋值后从未使用）。
                resp_headers = dict(resp.headers)
                raw_data = _read_capped(resp)
                raw_data = decompress_body(raw_data, resp_headers)
                return detect_and_decode(raw_data, resp_headers)

        except urllib.error.HTTPError as e:
            last_error = e
            status = e.code
            if status in RETRYABLE_STATUS_CODES and attempt < max_retries:
                _pending_retry_after = _retry_after_seconds(
                    getattr(e, "headers", None))
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
                    respect_rate_limit(url)
                    with safe_urlopen(req, timeout=timeout, context=ssl_fallback_ctx) as resp:
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

        except UnsafeURLError as e:
            # [审计 2026-09-30 P1-7] SSRF 校验未通过（内网/回环/保留地址，或域名
            # 解析失败 fail-closed）：如实报错且不重试 —— 这是确定性拒绝，
            # 重试只会重复校验、白白耗时。
            raise ProviderError(f"URL 未通过安全校验: {e}") from e

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


# ── 搜狗微信结果解析共享件 ─────────────────────────────────────────────
# [P0-7 修复·2026-10-08] 为什么放在 search 层：providers 层不得反向 import skills
# 层（会引入循环与双导入实例分裂风险），而 skills→search 是既有依赖方向
# （wechat_searcher 早已从本模块取 clean_bing_url）。搜狗结果页的 URL 补编码与
# 账号三级回退此前只在 skills 层内联实现、provider 层另有一份残缺实现
# （只认 class="account"），两处对同一页面的解析能力不一致——现单源化到本模块，
# provider 与 skills 两端共用同一实现。


#: 公众号名占位词/噪声词黑名单（命中即视为未识别，避免把「公众号」「获取更多」当成账号名）
ACCOUNT_PLACEHOLDERS: Tuple[str, ...] = (
    "未知", "微信公众号", "公众号", "微信", "蓝字", "上方蓝字",
    "关注", "获取更多", "更多", "阅读全文", "原文", "文章", "点击上方",
    "投稿", "转载", "编辑", "责任编辑",
)

#: 搜狗微信结果块账号名三级选择器：L1 老版 class="account" 锚链（宽容包含匹配，
#: 与 skills 层历史实现同口径）→ L2 现版 <span class="all-time-y2"> →
#: L3 s-p 容器内首个 <a>。实测（2026-10-08 真实结果页）L1 已 0 命中，
#: 账号实际在 L2 节点上，缺 L2/L3 回退时账号字段恒为空。
_SOGOU_ACCOUNT_PATTERNS = (
    re.compile(r'<a[^>]*class="[^"]*account[^"]*"[^>]*>(.*?)</a>',
               re.DOTALL | re.IGNORECASE),
    re.compile(r'<span[^>]*class="[^"]*all-time-y2[^"]*"[^>]*>(.*?)</span>',
               re.DOTALL | re.IGNORECASE),
    re.compile(r'<div[^>]*class="s-p"[^>]*>.*?<a[^>]*>(.*?)</a>',
               re.DOTALL | re.IGNORECASE),
)


def normalize_search_url(url: str) -> str:
    """规范化检索结果链接：给空格等非法字符补百分号编码，不动 URL 结构字符。

    [P0-7 修复·2026-10-08] 为什么必须做：搜狗微信结果页的 href 里 query 参数
    会把搜索词中的空格原样保留（实测 10/10，形如
    ``…&query=%E9%A9%AC%E5%85%8B%E6%80%9D %E8%80%83%E7%A0%94&token=…``：
    中文已编码、词间是字面空格）。这样的 URL 进入 urllib 会抛
    ``InvalidURL: URL can't contain control characters``，整条检索链必失败。
    ``safe`` 保留 ``:/?&=%#-._~`` 等 URL 结构字符，且 ``%`` 在 safe 集合内使
    quote 对已有 ``%XX`` 转义幂等（不会二次编码成 ``%25XX``）。
    """
    if not url:
        return ""
    return urllib.parse.quote(url, safe=":/?&=%#-._~")


def guess_account_from_text(text: str) -> str:
    """从搜索结果摘要/标题中兜底猜测公众号名（如「来源：XXX」「公众号：XXX」）。

    [P0-7 修复·2026-10-08] 自 skills/wechat_searcher.py 的
    ``_AccountNameResolver._guess_account_from_text`` 上移单源化，正则与占位词
    过滤逻辑逐字保留（行为等价）；原方法改为薄委托。
    """
    if not text:
        return ""
    # [P3 修复·D8] 补充「点击上方蓝字关注 XXX」「由 XXX 发布」等列表页常见句式。
    # 采用 finditer 逐个候选校验：左端噪声命中（如「蓝字关注」）不再直接短路返回，
    # 且标记词后必须跟分隔符，避免把「关注」本身捕成账号名。
    for pat in (r'(?:公众号|来源|出品|作者)[：:\s|]*([\u4e00-\u9fa5A-Za-z0-9_·\-]{2,24})',
                r'(?:来自|由)[\s：:]*([\u4e00-\u9fa5A-Za-z0-9_·\-]{2,24})',
                r'(?:蓝字|关注)[\s：:]+([\u4e00-\u9fa5A-Za-z0-9_·\-]{2,24})'):
        for m in re.finditer(pat, text):
            cand = m.group(1).strip("，。,.|")
            if cand and cand not in ACCOUNT_PLACEHOLDERS:
                return cand[:40]
    return ""


def parse_sogou_account(block: str, *, guess_text: str = "") -> str:
    """搜狗微信结果块的公众号名三级回退解析（L1→L2→L3→句式兜底→清洗）。

    [P0-7 修复·2026-10-08] 为什么必须三级回退：实测（2026-10-08 真实结果页）
    ``class="account"`` 已 0 命中——账号迁到 ``<span class="all-time-y2">``，
    只认 L1 的旧实现使账号字段 10/10 为空。三级链与
    ``skills/wechat_searcher.py`` 既有内联实现同源，两端不再各写一份。

    :param block: 单条结果的 HTML 块（txt-box / li 均可）。
    :param guess_text: 三级均未命中时的句式兜底文本（如「摘要 + 标题」）；
        为空则不做兜底（provider 层保持只认结构化字段）。
    """
    for pattern in _SOGOU_ACCOUNT_PATTERNS:
        m = pattern.search(block)
        if m:
            name = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
            if name:
                return name
    if guess_text:
        return guess_account_from_text(guess_text)
    return ""


__all__ = [
    "ACCOUNT_PLACEHOLDERS",
    "ANTI_BOT_MARKERS",
    "BROWSER_HEADERS",
    "RETRYABLE_STATUS_CODES",
    "THROTTLE_MAX_WAIT",
    "THROTTLE_RATES",
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
    "guess_account_from_text",
    "looks_like_anti_bot",
    "normalize_search_url",
    "parse_sogou_account",
    "reset_rate_limit",
    "respect_rate_limit",
]
