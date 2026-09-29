# -*- coding: utf-8 -*-
"""[W11 search_files 路径 glob] 带路径的通配模式可命中嵌套文件。

背景（多角色实测）
------------------
模型调 ``search_files(pattern='**/今日任务.md')`` 返回「未找到匹配模式」，
而文件真实存在（随后 list_directory 找到）——原实现只对**文件名**做
``fnmatch``，带路径的 glob 必然失配。现对「文件名」与「相对路径」同时匹配：
  * ``*.md`` / ``*真题*`` 等既有文件名模式行为不变（fnmatch 的 ``*`` 跨 ``/``）；
  * ``**/x.md`` 命中任意深度的 x.md；
  * ``**/x.md`` 对**根级**文件同样命中（去 ``**/`` 前缀兜底）；
  * ``04-专业课/*.md`` 这类相对路径模式可命中。

全程离线：tmp_path 工作区 + 真实 ToolRegistry。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))


def _tool(name, tmp_path):
    from tools.agent.permissions import PermissionManager
    from tools.agent.sandbox import Sandbox
    from tools.agent.tools_impl import ToolRegistry
    reg = ToolRegistry(Sandbox(workspace_root=tmp_path),
                       PermissionManager(mode="auto", workspace_root=tmp_path))
    return reg.tools[name].func


@pytest.fixture
def ws(tmp_path):
    """造一个带嵌套目录的工作区。"""
    (tmp_path / "04-专业课" / "_状态").mkdir(parents=True)
    (tmp_path / "04-专业课" / "_状态" / "今日任务.md").write_text("nested", encoding="utf-8")
    (tmp_path / "02-英语" / "参考资料").mkdir(parents=True)
    (tmp_path / "02-英语" / "参考资料" / "历年真题2019.pdf").write_text("pdf", encoding="utf-8")
    (tmp_path / "今日任务.md").write_text("root", encoding="utf-8")
    return tmp_path


# ── 1. 路径 glob（新增能力） ───────────────────────────────────────────


def test_path_glob_matches_nested_file(ws):
    fn = _tool("search_files", ws)
    out = fn("**/今日任务.md")
    assert "未找到" not in out
    assert "04-专业课/_状态/今日任务.md" in out.replace("\\", "/")


def test_path_glob_matches_root_file(ws):
    """`**/x.md` 对根级文件同样命中（去前缀兜底）。"""
    fn = _tool("search_files", ws)
    out = fn("**/今日任务.md")
    assert "今日任务.md" in out
    lines = [ln for ln in out.splitlines() if ln.endswith("今日任务.md")]
    assert any(not ln.startswith("04-") for ln in lines), "根级今日任务.md 应被命中"


def test_relative_path_pattern(ws):
    fn = _tool("search_files", ws)
    out = fn("04-专业课/*.md")
    assert "未找到" not in out
    assert "今日任务.md" in out


def test_dot_slash_prefix_tolerated(ws):
    fn = _tool("search_files", ws)
    out = fn("./*.md")
    assert "未找到" not in out
    assert "今日任务.md" in out


# ── 2. 既有文件名模式行为不变（回归） ──────────────────────────────────


def test_filename_pattern_unchanged(ws):
    fn = _tool("search_files", ws)
    out = fn("*.md")
    assert "未找到" not in out
    assert "04-专业课/_状态/今日任务.md" in out.replace("\\", "/")
    assert "今日任务.md" in out


def test_substring_pattern_unchanged(ws):
    fn = _tool("search_files", ws)
    out = fn("*真题*")
    assert "未找到" not in out
    assert "历年真题2019.pdf" in out


def test_extension_pattern_unchanged(ws):
    fn = _tool("search_files", ws)
    out = fn("*.pdf")
    assert "未找到" not in out
    assert "历年真题2019.pdf" in out
    assert "今日任务.md" not in out


# ── 3. 无匹配仍返回明确文案 ────────────────────────────────────────────


def test_no_match_message(ws):
    fn = _tool("search_files", ws)
    out = fn("*.xyz")
    assert "未找到匹配模式" in out
