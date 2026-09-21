# -*- coding: utf-8 -*-
r"""
考研学习链 (ky-cli) · 网络访问安全与响应体积上限 (Net Guard)

职责（供 agent 工具与 intelligence 抓取器共用，避免两份漂移的实现）：
1. **SSRF 防护**：先把主机名 ``getaddrinfo`` 解析成真实 IP，再按 ``ipaddress``
   判定回环 / 私网 / 链路本地 / 保留 / 组播 / 未指定，并额外覆盖 IPv6 ULA
   (``fc00::/7``) 与 IPv4-mapped (``::ffff:x.x.x.x`` —— 取其映射的 v4 再判一次)。
   字符串黑名单（只比 ``127.0.0.1``/``localhost``）挡不住 ``2130706433``、
   ``127.1``、``0x7f000001``、``[::ffff:127.0.0.1]``、``fd00::``/``fe80::`` 这些写法。
2. **禁止自动跟随重定向到不安全目标**：``urllib`` 默认跟随 3xx，且**不会**再走一次
   校验 —— 攻击者用一个公网 URL 302 到 ``127.0.0.1``/``169.254.169.254`` 即可绕过。
   这里用自定义 ``HTTPRedirectHandler`` 让每次跳转都重新校验。
3. **带体积上限的解压**：``gzip.decompress`` / ``zlib.decompress`` / ``brotli`` 都是
   无上限的，几十 KB 的「解压炸弹」可膨胀成几十 MB 直接撑爆内存。这里改为分块累计
   到上限即停。
4. **消除 DNS 重绑定（TOCTOU）窗口**：校验时 ``getaddrinfo`` 解析出的 IP 会被
   ``PinRegistry`` 记下来，建连时**直接复用**（见 :class:`_PinnedConnectionHandler`）——
   否则校验与连接是两次独立解析，攻击者控制权威 DNS 即可让第一次返回公网 IP、
   第二次返回 ``127.0.0.1``。
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Dict, Optional

# 双导入路径兼容：项目同时存在 `tools.X` 与 `X` 两种导入方式，
# 不做这层归一化会让同一个模块被加载成两个对象（配置/常量各持一份）。
sys.modules.setdefault("tools.net_guard", sys.modules[__name__])
sys.modules.setdefault("net_guard", sys.modules[__name__])

#: 单次 HTTP 响应体读取上限（防超大响应直接吃满内存）
MAX_HTTP_RESPONSE_BYTES = 16 * 1024 * 1024
#: 解压后体积上限（防解压炸弹）
MAX_DECOMPRESSED_BYTES = 32 * 1024 * 1024
#: 超限截断时追加到文本尾部的标记
TRUNCATION_MARKER = "\n[... 内容超过安全体积上限，已截断 ...]"


class UnsafeURLError(ValueError):
    """URL 未通过 SSRF / 协议安全校验。"""


#: RFC 2544 基准测试网段（198.18.0.0/15，IANA 保留、公网不可路由）。
#: 部分企业/沙箱网络的 DNS 会把**所有**公网域名重定向到该网段（本机实测：
#: ``example.com -> 198.18.1.225``、``www.baidu.com -> 198.18.1.226``）。
#: 若对「域名解析结果」也按私网拦截，等于把整个外网访问一刀切断。
#: 因此：**域名**解析到该网段时放行；**IP 字面量**（``http://198.18.0.1/``）
#: 仍照常拦截 —— 那正是本机网卡地址，属于必须挡住的 SSRF 目标。
_BENCHMARK_NETS = (ipaddress.ip_network("198.18.0.0/15"),)


def _host_is_ip_literal(host: str) -> bool:
    """主机名本身是否就是 IP 字面量（含 ``[::ffff:127.0.0.1]`` 这类写法）。"""
    try:
        ipaddress.ip_address(str(host).strip().strip("[]").split("%", 1)[0])
        return True
    except ValueError:
        return False


def _ip_is_blocked(ip_obj: "ipaddress._BaseAddress") -> bool:
    """判定单个 IP 是否属于「不该被 Agent 访问」的地址空间。"""
    # IPv4-mapped IPv6（::ffff:127.0.0.1）要按映射后的 v4 再判一次，
    # 否则 is_loopback 等判定对 ::ffff:127.0.0.1 不成立，形成绕过。
    mapped = getattr(ip_obj, "ipv4_mapped", None)
    if ip_obj.version == 6 and mapped is not None:
        ip_obj = mapped
    return bool(
        ip_obj.is_private          # 10/8, 172.16/12, 192.168/16, 127/8, fc00::/7 ...
        or ip_obj.is_loopback
        or ip_obj.is_link_local    # 169.254/16, fe80::/10
        or ip_obj.is_reserved
        or ip_obj.is_multicast
        or ip_obj.is_unspecified
    )


def resolve_host_ips(hostname: str) -> list:
    """解析主机名的全部 IP；解析不出任何 IP 时抛 :class:`UnsafeURLError`（fail-closed）。"""
    host = str(hostname or "").strip().strip("[]")
    if not host:
        raise UnsafeURLError("URL 缺少主机名")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise UnsafeURLError(f"域名无法解析，已按拒绝处理: {host} ({e})")
    ips = []
    for info in infos:
        addr = str(info[4][0]).split("%", 1)[0]   # 去掉 IPv6 zone id (fe80::1%eth0)
        try:
            ips.append(ipaddress.ip_address(addr))
        except ValueError:
            continue
    if not ips:
        raise UnsafeURLError(f"域名未解析出有效 IP: {host}")
    return ips


def assert_url_safe(url: str, pin: Optional["PinRegistry"] = None) -> str:
    """校验 URL 是否可安全访问。

    不通过时抛 :class:`UnsafeURLError`；通过时返回原 URL。

    传入 ``pin``（:class:`PinRegistry`）时，会把**本次校验实际解析到的 IP** 记进去，
    供真正建连的那一跳复用 —— 校验与连接共用同一份解析结果，DNS 重绑定窗口即被关闭。
    """
    raw = str(url or "").strip()
    if not raw.startswith(("http://", "https://")):
        raise UnsafeURLError(f"仅支持 http:// 或 https:// 协议: {raw}")
    parsed = urllib.parse.urlparse(raw)
    host = (parsed.hostname or "").lower()
    if not host:
        raise UnsafeURLError(f"URL 缺少主机名: {raw}")
    if host == "localhost" or host.endswith(".localhost"):
        raise UnsafeURLError(f"安全拦截 - 禁止访问本地地址 [{host}]")
    literal = _host_is_ip_literal(host)
    ips = resolve_host_ips(host)
    for ip_obj in ips:
        if not _ip_is_blocked(ip_obj):
            continue
        if not literal and any(ip_obj in net for net in _BENCHMARK_NETS):
            continue      # DNS 重定向到基准网段：放行（见 _BENCHMARK_NETS 说明）
        raise UnsafeURLError(f"安全拦截 - 禁止访问内网/回环/保留地址 [{host} -> {ip_obj}]")
    if pin is not None:
        # 全部 IP 都已通过判定，pin 第一个用于建连
        pin.remember(host, ips)
    return raw


# ──────────────── DNS 重绑定（TOCTOU）缓解：把校验过的 IP pin 到连接上 ────────────────

class PinRegistry:
    """记录「校验时解析到的 IP」，供建连时复用，消除二次解析带来的重绑定窗口。

    为什么需要：``assert_url_safe`` 用 ``getaddrinfo`` 解析主机名并逐个判定 IP，
    但随后 ``urllib`` 建连时会**再独立解析一次**。攻击者控制权威 DNS（或 TTL=0 的
    轮询记录）时，可以让第一次解析返回公网 IP（校验通过）、第二次返回 ``127.0.0.1``
    —— 校验结果没有被 pin 到实际连接上，SSRF 防护形同虚设。

    这里把校验时那份解析结果按主机名记下，建连时直接复用，两次解析合一。
    """

    def __init__(self) -> None:
        self._ips: Dict[str, str] = {}

    def remember(self, host: str, ips) -> None:
        """记录 ``主机名 -> 首个已校验 IP``（``ips`` 中每一个都已在调用侧通过判定）。"""
        key = _bare_host(host)
        if key and ips:
            self._ips[key] = str(ips[0])

    def lookup(self, host: str) -> Optional[str]:
        """取回已校验 IP；没有记录时返回 ``None``（调用方决定回落行为）。"""
        return self._ips.get(_bare_host(host))


def _bare_host(host: str) -> str:
    """``host[:port]`` -> ``host``（去 userinfo、去端口、去 IPv6 方括号、统一小写）。"""
    text = str(host or "").strip().lower()
    if "@" in text:
        text = text.rsplit("@", 1)[1]
    if text.startswith("["):
        end = text.find("]")
        return text[1:end] if end > 0 else text
    if text.count(":") == 1:
        return text.split(":", 1)[0]
    return text


def _pinned_create_connection(ip: str):
    """返回一个「忽略主机名、直连 ip」的 ``socket.create_connection`` 替身。"""
    def _create(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
        _host, port = address
        return socket.create_connection((ip, port), timeout, source_address)

    return _create


def _pinned_connection_class(base_cls, ip: str):
    """构造 ``base_cls`` 的子类，其 ``connect()`` 只连到已校验的 ``ip``。

    只替换 TCP 目标，``self.host`` 保持原样 —— 于是 ``Host`` 请求头与 TLS 的
    ``server_hostname``（SNI + 证书主机名校验）仍是原始域名，不会被 pin 改坏。
    ``http.client`` 把 ``socket.create_connection`` 存在**实例属性**
    ``_create_connection`` 上（其源码注释明确说是为便于替换/测试），这里正用该钩子。
    """

    class _PinnedConnection(base_cls):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._create_connection = _pinned_create_connection(ip)

    _PinnedConnection.__name__ = f"_Pinned{base_cls.__name__}"
    _PinnedConnection.__qualname__ = _PinnedConnection.__name__
    return _PinnedConnection


class _PinnedConnectionHandler:
    """把 ``AbstractHTTPHandler.do_open`` 拿到的连接类换成「pin 过的」版本。"""

    def __init__(self, pin: PinRegistry, **kwargs):
        super().__init__(**kwargs)
        self._pin = pin

    def do_open(self, http_class, req, **http_conn_args):  # noqa: D102
        ip = self._pin.lookup(getattr(req, "host", ""))
        if not ip:
            # 无校验记录 → 保持 urllib 原行为。实际只有一种情况会走到这里：
            # operator 配置了 HTTP 代理，此时 req.host 是代理地址而非被校验的目标域名
            # （代理端点是本机/运维配置的可信端点，不该被当作 SSRF 目标拦掉）。
            return super().do_open(http_class, req, **http_conn_args)
        return super().do_open(
            _pinned_connection_class(http_class, ip), req, **http_conn_args)


class _PinnedHTTPHandler(_PinnedConnectionHandler, urllib.request.HTTPHandler):
    """``HTTPHandler`` + 连接 IP pin。"""

    def __init__(self, pin: PinRegistry):
        super().__init__(pin)


class _PinnedHTTPSHandler(_PinnedConnectionHandler, urllib.request.HTTPSHandler):
    """``HTTPSHandler`` + 连接 IP pin（``context`` 语义与父类完全一致）。"""

    def __init__(self, pin: PinRegistry, context=None):
        super().__init__(pin, context=context)


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """每次 3xx 跳转都重新做 SSRF 校验，堵死「公网 URL 302 到内网」的绕过。

    传入 ``pin`` 时，跳转目标的校验结果同样被记录，供建连时复用。
    """

    def __init__(self, pin: Optional[PinRegistry] = None):
        super().__init__()
        self._pin = pin

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        assert_url_safe(newurl, pin=self._pin)
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None:
            # [B1 修复·跳转泄漏 Bearer] CPython 的 redirect_request 只剥离
            # content-length/content-type，会把 Authorization 原样带到跳转目标。
            # 跨主机跳转时必须剥离敏感头，否则恶意 base_url 回 302 即可收割 Key。
            try:
                old_host = (urllib.parse.urlparse(req.full_url).hostname or "").lower()
                new_host = (urllib.parse.urlparse(new_req.full_url).hostname or "").lower()
            except Exception:
                old_host = new_host = ""
            if old_host != new_host:
                for _h in ("Authorization", "Proxy-Authorization"):
                    try:
                        new_req.remove_header(_h)
                    except KeyError:
                        pass
        return new_req


def build_safe_opener(context=None,
                      pin: Optional[PinRegistry] = None) -> urllib.request.OpenerDirector:
    """构造带「安全重定向 + 连接 IP pin」的 opener（可选自定义 SSL context）。"""
    pin = pin if pin is not None else PinRegistry()
    return urllib.request.build_opener(
        _PinnedHTTPHandler(pin),
        _PinnedHTTPSHandler(pin, context=context),
        SafeRedirectHandler(pin=pin),
    )


def safe_urlopen(req, timeout: float = 12, context=None):
    """带 SSRF 校验、安全重定向与连接 IP pin 的 ``urlopen``。

    与原 ``urllib.request.urlopen`` 一样：4xx/5xx 抛 ``HTTPError``，
    网络错误抛 ``URLError``。
    """
    url = getattr(req, "full_url", None) or str(req)
    pin = PinRegistry()
    assert_url_safe(url, pin=pin)
    opener = build_safe_opener(context=context, pin=pin)
    return opener.open(req, timeout=timeout)


# ─────────────────────────── 解压体积上限 ───────────────────────────

def zlib_limited(data: bytes, wbits: int, max_bytes: int,
                 require_eof: bool = False, mark_incomplete: bool = False):
    """用 ``decompressobj`` 分块解压，超过 ``max_bytes`` 立即截断返回。

    ``wbits`` 传 ``zlib.MAX_WBITS`` 解 zlib/deflate，传 ``16+MAX_WBITS`` 解 gzip，
    传 ``-zlib.MAX_WBITS`` 解 raw deflate。返回 ``(bytes, truncated)``。

    ``require_eof=True`` 时要求流完整收尾，否则抛 ``zlib.error`` —— 这是为了
    对齐一次性 ``zlib.decompress`` 的严格语义：``decompressobj`` 对「不完整的流」
    会静默返回部分输出（如 ``b"\\xc0\\xaf"`` 返回 ``b""`` 而不报错），
    需要严格判定的调用方（把 raw deflate 当最后兜底解释时）必须显式要求。

    ``mark_incomplete=True`` 时不抛异常，而是把「流未正常收尾」如实报成
    ``truncated=True``：上游若用 ``resp.read(MAX)`` 把压缩流截断，``decompressobj``
    既不报错也不置 ``eof``，调用方只有靠这个标记才能区分「完整」与「半截」。
    """
    obj = zlib.decompressobj(wbits)
    out = obj.decompress(data, max_bytes + 1)
    if len(out) > max_bytes:
        return out[:max_bytes], True
    if obj.unconsumed_tail:
        # 还有剩余输入没被消费，说明输出会被 max_length 截断
        return out, True
    if require_eof and not obj.eof:
        raise zlib.error("incomplete or truncated stream")
    if mark_incomplete and not obj.eof:
        return out, True
    return out, False


def _gzip_limited(data: bytes, max_bytes: int):
    """gzip 的带限解压（等价 ``gzip.decompress`` 但带体积上限与截断标记）。"""
    return zlib_limited(data, 16 + zlib.MAX_WBITS, max_bytes, mark_incomplete=True)


def _brotli_limited(data: bytes, max_bytes: int):
    """brotli 的带限解压（分块喂入，累计超限即停）。"""
    import brotli

    decompressor_cls = getattr(brotli, "Decompressor", None)
    if decompressor_cls is None:      # pragma: no cover - 极老的 brotli 绑定
        out = brotli.decompress(data)
        return out[:max_bytes], len(out) > max_bytes
    dec = decompressor_cls()
    out = bytearray()
    step = 65536
    for i in range(0, len(data), step):
        out.extend(dec.process(data[i:i + step]))
        if len(out) > max_bytes:
            return bytes(out[:max_bytes]), True
    return bytes(out), False


def decompress_limited(data: bytes, encoding: str = "", max_bytes: int = MAX_DECOMPRESSED_BYTES):
    """按 ``Content-Encoding``（或 magic bytes）解压，返回 ``(bytes, truncated)``。

    与 ``tools/search/providers/_http.py:decompress_body`` 的 gzip/deflate/magic-bytes
    语义保持一致，但**带体积上限** —— 直接复用 ``decompress_body`` 会因为它的
    ``gzip.decompress`` 无上限而在解压阶段就被炸弹撑爆，所以这里单独实现。
    """
    if not data:
        return data, False
    enc = str(encoding or "").lower().strip()

    if enc in ("gzip", "x-gzip") or data.startswith(b"\x1f\x8b"):
        try:
            return _gzip_limited(data, max_bytes)
        except Exception:
            return data, False
    if enc in ("deflate", "zlib") or (
        len(data) >= 2 and data[0] == 0x78 and (data[0] * 256 + data[1]) % 31 == 0
    ):
        try:
            return zlib_limited(data, zlib.MAX_WBITS, max_bytes)
        except Exception:
            try:
                return zlib_limited(data, -zlib.MAX_WBITS, max_bytes)
            except Exception:
                return data, False
    if enc in ("br", "brotli"):
        try:
            return _brotli_limited(data, max_bytes)
        except Exception:
            return data, False
    return data, False


__all__ = [
    "MAX_DECOMPRESSED_BYTES",
    "MAX_HTTP_RESPONSE_BYTES",
    "PinRegistry",
    "SafeRedirectHandler",
    "TRUNCATION_MARKER",
    "UnsafeURLError",
    "assert_url_safe",
    "build_safe_opener",
    "decompress_limited",
    "resolve_host_ips",
    "safe_urlopen",
    "zlib_limited",
]
