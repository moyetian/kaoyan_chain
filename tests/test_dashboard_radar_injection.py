# -*- coding: utf-8 -*-
"""
看板「考情雷达」外部内容注入回归（P2-6）

[缺陷] ``05-考研看板/web/radar.py`` 逐条渲染监控院校卡片时，``last_check`` 与 ``url``
取自外部巡检写入的 ``.memory/admission_watch.json``，却未做 ``html.escape`` 与协议白名单：
``url`` 写成 ``javascript:alert(...)`` 会生成可点击的 XSS 链接；``last_check`` 里的
单引号可闭合 ``style`` 属性并注入 ``<img onerror>``。

[本测试] 构造临时 root（不触碰仓库）写入恶意 JSON，在**非脱敏模式**（``KY_SNAPSHOT_OPT_IN=0``，
只有该模式才逐条渲染院校卡片）下调用 ``build_radar_html``，断言注入被中和。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "05-考研看板"
if str(DASHBOARD) not in sys.path:
    sys.path.insert(0, str(DASHBOARD))


@pytest.fixture()
def radar_html(tmp_path, monkeypatch):
    monkeypatch.setenv("KY_SNAPSHOT_OPT_IN", "0")  # 非脱敏：逐条渲染院校卡片
    from web.radar import build_radar_html

    (tmp_path / ".memory").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".memory" / "admission_watch.json").write_text(json.dumps({
        "X": {
            "school": "某大学",
            "status": "UPDATED",
            "last_check": "2026-09-21'><img src=x onerror=alert(1)>",
            "url": "javascript:alert(document.cookie)",
            "alert_titles": ["正常标题"],
        }
    }, ensure_ascii=False), encoding="utf-8")
    return build_radar_html(tmp_path)


def test_javascript_url_is_neutralized(radar_html):
    """javascript: 协议必须被白名单挡掉，回退为 #。"""
    assert "javascript:" not in radar_html, radar_html
    assert "href='#'" in radar_html, radar_html


def test_attribute_breakout_is_escaped(radar_html):
    """单引号闭合 + <img onerror> 必须被转义，不产生真实标签。"""
    assert "<img src=x onerror=alert(1)>" not in radar_html, radar_html
    assert "&lt;img src=x onerror=alert(1)&gt;" in radar_html, radar_html
    assert "'><img" not in radar_html, radar_html


def test_safe_https_url_is_kept(radar_html, tmp_path, monkeypatch):
    """合法 https 官网链接必须照常渲染（不能把正常功能一起收掉）。"""
    monkeypatch.setenv("KY_SNAPSHOT_OPT_IN", "0")
    from web.radar import build_radar_html

    (tmp_path / ".memory" / "admission_watch.json").write_text(json.dumps({
        "X": {"school": "某大学", "status": "UNCHANGED",
              "last_check": "2026-09-21", "url": "https://yz.chsi.com.cn"}
    }, ensure_ascii=False), encoding="utf-8")
    html = build_radar_html(tmp_path)
    assert "href='https://yz.chsi.com.cn'" in html, html


def test_explicit_null_fields_do_not_crash_build(tmp_path, monkeypatch):
    """[P2-6b 回归] 外部 JSON 里的**显式 null** 不得让 build_radar_html 抛异常。

    修复前 ``html.escape(it.get('school', '高校'))``：默认值只对「缺键」生效，
    对显式 ``"school": null`` 返回 ``None`` → ``html.escape(None)`` 抛
    ``AttributeError``；而 ``05-考研看板/build.py`` 调用本函数时没有 try 包裹，
    于是整个看板构建失败（``last_check`` 已加 ``str()``，``school`` 漏改）。

    阴性对照：把 ``str(it.get('school') or '高校')`` 改回
    ``it.get('school', '高校')``，本用例变红（AttributeError）。
    """
    monkeypatch.setenv("KY_SNAPSHOT_OPT_IN", "0")
    from web.radar import build_radar_html

    (tmp_path / ".memory").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".memory" / "admission_watch.json").write_text(json.dumps({
        "X": {"school": None, "status": None, "last_check": None,
              "url": None, "alert_titles": [None, "正常标题"]}
    }, ensure_ascii=False), encoding="utf-8")

    html = build_radar_html(tmp_path)  # 修复前在此抛 AttributeError

    assert "高校" in html, html          # school 回退默认值
    assert "未巡检" in html, html        # last_check 回退默认值
    assert "正常标题" in html, html      # 列表项中的 null 不影响其余项
    assert "None" not in html, html      # 不得把 Python 的 None 字样写进页面
