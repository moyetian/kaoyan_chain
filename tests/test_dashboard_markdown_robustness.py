# -*- coding: utf-8 -*-
"""
看板 Markdown 解析健壮性回归（P0-4 无限循环）

[缺陷] ``05-考研看板/web/markdown.py`` 的 ``md2html()`` 段落分支此前为::

    buf = []
    while i < n and lines[i].strip() and not re.match(r"^(#{1,6}\\s|\\s*\\||...)", lines[i]):
        buf.append(lines[i].strip()); i += 1
    if buf:
        out.append(f"<p>{inline(' '.join(buf))}</p>")

当某行以「可选空白 + ``|``」开头、但下一行不是合法表格分隔行（``^\\s*\\|[\\s:|-]+\\|\\s*$``）
时，表格分支不接管、段落守卫 ``\\s*\\|`` 又命中 —— while 体一次都不执行、``buf`` 为空、
``i`` 不推进，外层 ``while i < n`` 原地空转（CPU 100%，实测 8s 超时被强杀）。
``build.py`` 对「今日任务」整份文件调用 ``md2html``，故用户把表格分隔行写错即可让
``tools/update_dashboard.py`` / ``ky build`` 整个卡死。

本文件用**子进程 + 超时**复现并锁定修复：任何畸形输入都必须在数秒内返回，
且不得丢内容（游离竖线行按普通段落显示）。
"""

from __future__ import annotations

import base64
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "05-考研看板"

#: 在子进程里跑真实 md2html（避免死循环把 pytest 主进程一起挂住）
_RUNNER = textwrap.dedent(
    f"""
    import base64, sys
    sys.path.insert(0, {str(DASHBOARD)!r})
    from web.markdown import md2html
    raw = base64.b64decode(sys.argv[1]).decode("utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.write(md2html(raw))
    """
)

#: 会触发原死循环的畸形输入（每一条修复前都会挂死）
MALFORMED_INPUTS = {
    "报告原输入": "| 备注 | 说明 |\n正文\n",
    "孤立竖线行": "\n| 备注 | 说明 |\n\n",
    "两行连续竖线无分隔行": "| a | b |\n| c | d |\n",
    "竖线行后接列表": "| a | b |\n- x\n",
    "空格缩进的竖线行": "  | a | b |\n正文\n",
}


def _run_md2html(text: str, timeout: float = 8.0):
    """子进程执行 md2html；超时抛 ``subprocess.TimeoutExpired``。"""
    b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return subprocess.run(
        [sys.executable, "-c", _RUNNER, b64],
        capture_output=True, text=True, encoding="utf-8", timeout=timeout,
    )


@pytest.mark.parametrize("name,text", list(MALFORMED_INPUTS.items()), ids=list(MALFORMED_INPUTS))
def test_malformed_table_does_not_hang(name, text):
    """畸形表格 / 游离竖线行不得让 md2html 挂死（P0-4 阴性对照）。"""
    try:
        res = _run_md2html(text, timeout=8.0)
    except subprocess.TimeoutExpired:
        pytest.fail(f"[{name}] md2html 挂死（超时 >8s），死循环未修复：{text!r}")
    assert res.returncode == 0, f"[{name}] 子进程异常退出: {res.stderr}"
    assert "<p>" in res.stdout, f"[{name}] 未产出段落: {res.stdout!r}"


def test_stray_pipe_line_is_preserved_not_dropped():
    """游离竖线行应按普通段落显示，不能静默丢内容。"""
    out = _run_md2html("| 备注 | 说明 |\n正文\n").stdout
    assert "备注" in out and "说明" in out and "正文" in out, out


def test_normal_markdown_still_renders():
    """修复不得破坏正常表格 / 引用 / 列表 / 段落聚合。"""
    table = _run_md2html("| a | b |\n|---|---|\n| 1 | 2 |\n").stdout
    assert "<table>" in table and "<td>1</td>" in table, table

    quote = _run_md2html("> 引用\n").stdout
    assert "<blockquote>引用</blockquote>" in quote, quote

    items = _run_md2html("- 甲\n- 乙\n").stdout
    assert "<ul>" in items and "<li>甲</li>" in items, items

    para = _run_md2html("第一行\n第二行\n").stdout
    assert para == "<p>第一行 第二行</p>", para
