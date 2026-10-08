# -*- coding: utf-8 -*-
"""[P0-8 修复·2026-10-08] 本机 DNS 劫持（198.18.0.0/15）下日常入口检索全灭。

[缺陷现场]
本机 DNS（Clash 类 fake-ip 代理）把**公网域名**解析到 RFC 2544 基准测试网段
198.18.0.0/15。``tools/net_guard.py`` 的 SSRF 防护按逐 IP 判定拦截保留段，
而放行分支又被 ``KY_ALLOW_BENCHMARK_DNS`` 环境开关挡住 —— 该开关在生产入口
（ky CLI / GUI / TUI）**全仓零设置**，于是日常检索全灭（实测 sogou 报
「安全拦截 - 禁止访问内网/回环/保留地址 [weixin.sogou.com -> 198.18.0.104]」）。

[修复口径]（本文件钉住的不变量）
* **域名**解析到 198.18.0.0/15 → **无条件放行**（DNS 劫持 / 代理环境兼容）：
  连接目标即 198.18.x.x 不可路由地址，无 SSRF 价值；DNS 重绑定窗口已由
  ``PinRegistry`` 关闭（校验解析的 IP 被 pin 到连接，无二次解析）。
* **IP 字面量**（``http://198.18.0.1/``）仍拦 —— 那才是必须防的目标。
* 旧 ``KY_ALLOW_BENCHMARK_DNS`` 开关已移除：函数不存在，设置任何值都无效果。
* 例外**仅**限该网段：回环 / 私网 / 链路本地地址（域名与字面量）一律照拦。

所有用例打桩 ``socket.getaddrinfo``（monkeypatch 退出自动恢复），不联网。
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import net_guard  # noqa: E402

#: 缺陷现场实测的解析结果（sogou → 198.18.0.104）
BENCHMARK_IP = "198.18.0.104"
#: 缺陷现场被拦的检索入口（第三方搜索站点，非身份信息）
SOGOU_URL = "https://weixin.sogou.com/x"


@pytest.fixture
def stub_dns(monkeypatch):
    """把任意主机名解析到指定 IP（monkeypatch 退出时恢复全局 socket，零污染）。"""
    def _set(ip: str):
        def _fake_getaddrinfo(host, port, *args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (str(ip), 0))]
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    return _set


# ══════════════════ ① 修复核心：域名 → 基准网段 无条件放行 ══════════════════

def test_domain_resolving_to_benchmark_net_allowed_without_env(stub_dns, monkeypatch):
    """缺陷现场复现：sogou → 198.18.0.104，不设任何环境变量也必须放行。"""
    monkeypatch.delenv("KY_ALLOW_BENCHMARK_DNS", raising=False)
    stub_dns(BENCHMARK_IP)
    assert net_guard.assert_url_safe(SOGOU_URL) == SOGOU_URL


def test_any_domain_resolving_to_benchmark_net_allowed(stub_dns, monkeypatch):
    """不限特定域名：DNS 劫持是整网行为，解析落进 198.18/15 就放行。"""
    monkeypatch.delenv("KY_ALLOW_BENCHMARK_DNS", raising=False)
    stub_dns("198.18.1.226")      # 缺陷现场另一实测值（同一 /15 网段）
    url = "https://www.example.com/"
    assert net_guard.assert_url_safe(url) == url


# ══════════════════ ② 安全边界：IP 字面量仍拦 ══════════════════

def test_benchmark_ip_literal_still_blocked(stub_dns):
    """``http://198.18.0.1/`` 是 IP 字面量：解析结果（连接目标）就是本机网卡
    所在网段，必须继续拦截 —— 放行只对「域名解析结果」生效。"""
    stub_dns("198.18.0.1")
    with pytest.raises(net_guard.UnsafeURLError):
        net_guard.assert_url_safe("http://198.18.0.1/")


def test_benchmark_literal_blocked_even_with_legacy_env(stub_dns, monkeypatch):
    """即便按旧文档设置了开关 env，字面量也绝不放行（开关已无任何豁免通道）。"""
    monkeypatch.setenv("KY_ALLOW_BENCHMARK_DNS", "1")
    stub_dns("198.18.0.1")
    with pytest.raises(net_guard.UnsafeURLError):
        net_guard.assert_url_safe("http://198.18.0.1/")


# ══════════════════ ③ 例外不外溢：回环 / 私网 / 链路本地仍拦 ══════════════════

@pytest.mark.parametrize("url,ip", [
    ("http://127.0.0.1/", "127.0.0.1"),                               # 回环字面量
    ("http://169.254.169.254/latest/meta-data/", "169.254.169.254"),  # 云元数据（链路本地）
    ("http://192.168.1.1/", "192.168.1.1"),                           # 私网字面量
    ("http://10.0.0.5/", "10.0.0.5"),                                 # 私网字面量
])
def test_internal_literals_still_blocked(stub_dns, url, ip):
    """内部地址字面量必须继续拦截（回归：例外不得扩大成任意内网访问）。"""
    stub_dns(ip)
    with pytest.raises(net_guard.UnsafeURLError):
        net_guard.assert_url_safe(url)


@pytest.mark.parametrize("ip", ["127.0.0.1", "192.168.1.1", "169.254.169.254"])
def test_domains_resolving_to_internal_still_blocked(stub_dns, ip):
    """域名解析到回环 / 私网 / 链路本地 —— 一律照拦（例外只认 198.18/15）。"""
    stub_dns(ip)
    with pytest.raises(net_guard.UnsafeURLError):
        net_guard.assert_url_safe("https://attacker.example/")


# ══════════════════ ④ 开关移除：env 不再有任何效果 ══════════════════

def test_legacy_env_switch_function_is_gone():
    """``_benchmark_dns_allowed`` 已删除 —— 开关不存在，设置任何值都无效。"""
    assert not hasattr(net_guard, "_benchmark_dns_allowed")


@pytest.mark.parametrize("value", ["0", "1", "true", "yes", "on"])
def test_legacy_env_values_do_not_change_decision(stub_dns, monkeypatch, value):
    """旧开关的任何取值（含开启值）都不再影响判定：基准网段放行照旧、
    私网拦截照旧 —— 修复后行为不再依赖任何 env。"""
    monkeypatch.setenv("KY_ALLOW_BENCHMARK_DNS", value)
    stub_dns(BENCHMARK_IP)
    assert net_guard.assert_url_safe(SOGOU_URL) == SOGOU_URL
    stub_dns("192.168.1.1")
    with pytest.raises(net_guard.UnsafeURLError):
        net_guard.assert_url_safe("https://intranet.example/x")


# ══════════════════ 阴性对照：放行确实来自基准网段分支 ══════════════════

def test_negative_control_benchmark_branch_is_what_allows(stub_dns, monkeypatch):
    """阴性对照：把 ``_BENCHMARK_NETS`` 置空（等效删掉该例外）后，同一域名
    必须重新被拦 —— 证明放行确实由这条分支产生，而不是断言没指向它。"""
    stub_dns(BENCHMARK_IP)
    monkeypatch.setattr(net_guard, "_BENCHMARK_NETS", ())
    with pytest.raises(net_guard.UnsafeURLError):
        net_guard.assert_url_safe(SOGOU_URL)
