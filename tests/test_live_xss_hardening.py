# -*- coding: utf-8 -*-
"""
docs/live.html 前端 XSS 加固回归（P1-3）

[缺陷] ``docs/live.html`` 里 ``marked.parse()`` 默认保留裸 HTML，渲染结果又直接
``innerHTML`` 上屏；LLM 回复与群聊机器人原始消息（``tools/cli/gateway.py`` 把
``user_msg`` 原样写入 ``LIVE_SESSION_MESSAGES``）中的 ``<img src=x onerror=...>``
即可执行 JS。另有两条后门：``bubble.innerHTML = content``（raw HTML 分支）与
``m.content.startsWith('<img')`` 判定。

[本测试] 从 ``docs/live.html`` 的 ``[KY-SEC-LAYER-BEGIN]``/``[KY-SEC-LAYER-END]``
标记之间**抽取真实安全层代码**，在 node 里配合仓库 vendored 的 marked 12.0.2 执行，
断言危险输入不会产出可执行节点、且正常 Markdown 不被破坏。
抽取真实代码（而非复制一份）可保证「改了页面忘了改测试」不会静默发生。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LIVE = ROOT / "docs" / "live.html"
MARKED_JS = ROOT / "docs" / "assets" / "vendor" / "marked" / "12.0.2" / "marked.min.js"

_BEGIN = "// [KY-SEC-LAYER-BEGIN]"
_END = "// [KY-SEC-LAYER-END]"


def _security_layer_source() -> str:
    text = LIVE.read_text(encoding="utf-8")
    assert _BEGIN in text and _END in text, "live.html 缺少安全层 BEGIN/END 标记"
    start = text.index(_BEGIN)
    end = text.index(_END, start)
    layer = text[start:end]
    assert "function escapeHtml" in layer and "function sanitizeRawBubble" in layer, layer[:200]
    return layer


def _run_node(body: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("本机无 Node.js，跳过 live.html 安全层单测")
    script = textwrap.dedent(
        f"""
        const marked = require({json.dumps(str(MARKED_JS))});
        global.marked = marked;
        global.window = {{ marked: marked }};
        """
    ) + _security_layer_source() + textwrap.dedent(
        """
        const out = {};
        out.parse = {
          img: marked.parse('<img src=x onerror=alert(1)>'),
          script: marked.parse('<script>alert(1)</script>'),
          anchor: marked.parse('<a href="javascript:alert(1)">click</a>'),
          md_link: marked.parse('[x](javascript:alert(1))'),
          md_image: marked.parse('![x](javascript:alert(1))'),
          md_link_ok: marked.parse('[x](https://a.com)'),
          bold: marked.parse('**bold** and `code`'),
          table: marked.parse('| a | b |\\n|---|---|\\n| 1 | 2 |\\n'),
        };
        out.raw = {
          evil_img: sanitizeRawBubble('<img src=x onerror=alert(1)>'),
          ok_bubble: sanitizeRawBubble('<img src="data:image/png;base64,AAAA" class="bubble-uploaded-img" alt="手写草稿" />'),
          bubble_extra_attr: sanitizeRawBubble('<img src="data:image/png;base64,AAAA" class="bubble-uploaded-img" alt="x" onerror="alert(1)">'),
          bubble_nested: sanitizeRawBubble('<img src="data:image/png;base64,AAAA" class="bubble-uploaded-img" alt="x"><div><img src=y onerror=alert(1)></div>'),
          script: sanitizeRawBubble('<script>alert(1)</script>'),
          text: sanitizeRawBubble('普通文本 <b>粗</b>'),
        };
        process.stdout.write(JSON.stringify(out));
        """
    )
    res = subprocess.run([node, "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert res.returncode == 0, f"node 执行失败: {res.stderr}"
    return json.loads(res.stdout)


# ── 主路径：marked 渲染器加固 ────────────────────────────────────

def test_bare_html_is_escaped_to_text():
    """裸 HTML 必须被转义为纯文本显示（不丢弃），不得产生真实标签。"""
    d = _run_node("")["parse"]
    assert d["img"] == "&lt;img src=x onerror=alert(1)&gt;", d["img"]
    assert d["script"] == "&lt;script&gt;alert(1)&lt;/script&gt;", d["script"]
    # 转义后的锚点：没有真实 <a ...> 标签，也就没有可点的 javascript: 链接
    assert "<a " not in d["anchor"] and "&lt;a href=" in d["anchor"], d["anchor"]


def test_markdown_link_and_image_urls_are_whitelisted():
    """Markdown 语法的 javascript: 链接/图片必须被中和为 #。"""
    d = _run_node("")["parse"]
    assert '<a href="#">x</a>' in d["md_link"], d["md_link"]
    assert "javascript:" not in d["md_link"], d["md_link"]
    assert '<img src="#" alt="x">' in d["md_image"], d["md_image"]
    assert "javascript:" not in d["md_image"], d["md_image"]


def test_normal_markdown_still_renders():
    """加固不得破坏正常 Markdown 与合法链接。"""
    d = _run_node("")["parse"]
    assert "<strong>bold</strong>" in d["bold"] and "<code>code</code>" in d["bold"], d["bold"]
    assert '<a href="https://a.com">x</a>' in d["md_link_ok"], d["md_link_ok"]
    assert "<table>" in d["table"] and "<td>1</td>" in d["table"], d["table"]


# ── 后门：raw HTML 气泡白名单 ────────────────────────────────────

def test_raw_bubble_backdoor_is_closed():
    """原始 HTML 分支只放行看板自产的 data:image 气泡，其余一律转义。"""
    d = _run_node("")["raw"]
    assert "<img" not in d["evil_img"], d["evil_img"]
    assert "&lt;img src=x onerror=alert(1)&gt;" in d["evil_img"], d["evil_img"]
    assert "<script" not in d["script"], d["script"]


def test_raw_bubble_allows_own_upload_bubble():
    """服务端/前端自产的上传图片气泡必须照常渲染（不能把正常功能一起收掉）。"""
    d = _run_node("")["raw"]
    assert d["ok_bubble"].startswith('<img src="data:image/png;base64,AAAA"'), d["ok_bubble"]
    assert "bubble-uploaded-img" in d["ok_bubble"], d["ok_bubble"]
    assert "onerror" not in d["ok_bubble"], d["ok_bubble"]


def test_raw_bubble_rejects_smuggled_attributes_and_nested_html():
    """多带 onerror= 或嵌套 HTML 的伪造气泡必须被中和。"""
    d = _run_node("")["raw"]
    assert "<img" not in d["bubble_extra_attr"], d["bubble_extra_attr"]
    assert "onerror=" in d["bubble_extra_attr"]                  # 只剩转义后的文本
    assert "<img src=y onerror=alert(1)>" not in d["bubble_nested"], d["bubble_nested"]
    assert "&lt;img src=y onerror=alert(1)&gt;" in d["bubble_nested"], d["bubble_nested"]
    assert "<b>粗</b>" not in d["text"] and "&lt;b&gt;粗&lt;/b&gt;" in d["text"], d["text"]


# ── 静态契约：后门入口不得回潮 ──────────────────────────────────

def test_no_unsanitized_innerhtml_backdoor():
    """三条危险写法都不得再出现（防回潮）。"""
    text = LIVE.read_text(encoding="utf-8")
    assert "bubble.innerHTML = content;" not in text, "raw HTML 后门回潮"
    assert "bubble.innerHTML = sanitizeRawBubble(content);" in text, "raw 分支未走白名单"
    assert "startsWith('<img')" not in text, "旧的 startsWith 判定回潮"
    assert re.search(r"const isHtml = /<\^?img", text) or "/^<img\\s+src=\"data:image\\//i" in text, \
        "isHtml 判定未收紧到 data:image 白名单"


# ── KaTeX 危险宏（\htmlData / \includegraphics）加固契约 ──────────

def test_katex_trust_is_explicitly_disabled():
    """所有 KaTeX 渲染入口都必须显式 ``trust: false``（防回潮）。

    KaTeX 的 ``trust`` 默认即为 ``false``，但显式写出后，任何后续改动都无法
    「顺手打开」危险宏（``\\htmlData`` / ``\\includegraphics`` 可在数学环境里
    注入任意 HTML 属性与 URL）。三处入口：live.html 的块级/行内
    ``renderToString``、看板模板与产物里的 ``renderMathInElement``。
    """
    live = LIVE.read_text(encoding="utf-8")
    assert live.count("trust: false") == 2, "live.html 的两处 renderToString 未显式 trust: false"

    for rel in ("05-考研看板/web/template.html", "docs/index.html"):
        path = ROOT / rel
        assert path.is_file(), f"缺少文件：{rel}"
        assert "trust:false" in path.read_text(encoding="utf-8"), \
            f"{rel} 的 renderMathInElement 未显式 trust:false"
