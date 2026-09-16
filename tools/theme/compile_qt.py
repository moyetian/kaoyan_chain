# -*- coding: utf-8 -*-
"""
Qt 样式表编译器：Theme → QSS

把「一套结构模板 + 一组 token」渲染成 Qt 可直接 setStyleSheet 的样式表，
取代改造前两份逐条手写的 dark.qss / light.qss（各 163 行，加主题就要再抄一遍）。

未定义的占位符**直接报错**而不是留空 —— 留空会让 Qt 静默忽略整条规则，
表现为"某个控件没样式"，排查成本极高。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable, List, Set

from .tokens import Theme

# 双导入路径兼容（项目同时存在 tools.X 与 X 两种运行方式）
try:  # pragma: no cover - 取决于运行方式
    from ky_io import atomic_write_text
except ImportError:  # pragma: no cover
    from tools.ky_io import atomic_write_text  # type: ignore

_LOG = logging.getLogger(__name__)

_TEMPLATE_NAME = "app.qss.tmpl"
#: 匹配 {{token}}（token 名允许字母数字与连字符）
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_-]+)\s*\}\}")


class TemplateError(ValueError):
    """模板占位符与 token 不匹配。"""


def default_template_path() -> Path:
    return Path(__file__).resolve().parent / "templates" / _TEMPLATE_NAME


def placeholder_names(template: str) -> Set[str]:
    """模板里用到的全部占位符名（供测试与自检比对预设覆盖面）。"""
    return set(_PLACEHOLDER.findall(template))


def render_qss(theme: Theme, template: str | None = None) -> str:
    """把主题渲染为 QSS 文本。

    :param template: 模板文本；不传则读取内置 ``templates/app.qss.tmpl``。
    :raises TemplateError: 模板存在未定义占位符时（避免样式静默失效）。
    """
    tpl = template if template is not None else default_template_path().read_text(encoding="utf-8")
    flat = theme.to_flat()

    missing: List[str] = sorted({n for n in placeholder_names(tpl) if n not in flat})
    if missing:
        raise TemplateError(
            f"QSS 模板引用了未定义的 token: {', '.join(missing)}"
            f"（当前主题 {theme.name} 共 {len(flat)} 个 token）")

    def _sub(match: "re.Match[str]") -> str:
        return str(flat[match.group(1)])

    rendered = _PLACEHOLDER.sub(_sub, tpl)
    # 收尾：注释里保留主题信息，便于排查"当前用的到底是哪套配色"
    header = (f"/* 由 tools/theme 编译生成 · 主题={theme.name}"
              f"（{theme.display_name}）· 来源={theme.source} —— 请勿手工编辑本文件 */\n")
    return header + rendered


def write_qss(theme: Theme, out_path: Path, template: str | None = None) -> Path:
    """把渲染结果原子写入 ``out_path`` 并返回该路径。"""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out, render_qss(theme, template))
    _LOG.debug("QSS 已写出: %s（主题 %s）", out, theme.name)
    return out


def write_preset_qss(theme: Theme, theme_dir: Path) -> List[Path]:
    """按项目既有文件名约定写出 ``<theme_dir>/<preset>.qss``。

    保留 ``dark.qss`` / ``light.qss`` 这些既有路径，使现存的读取代码无需改动
    （门面零破坏）：调用方仍按老路径取文件，内容改由编译器生成。
    """
    out = Path(theme_dir) / f"{theme.name}.qss"
    return [write_qss(theme, out)]


def compare_rendered(theme_a: Theme, theme_b: Theme,
                     template: str | None = None) -> int:
    """返回两套主题渲染结果的差异行数（0 表示完全一致），供测试使用。"""
    a = render_qss(theme_a, template).splitlines()
    b = render_qss(theme_b, template).splitlines()
    if len(a) != len(b):
        return max(len(a), len(b))
    return sum(1 for x, y in zip(a, b) if x != y)


def iter_tokens_used(template: str | None = None) -> Iterable[str]:
    """模板用到的 token 名（有序，便于生成文档）。"""
    tpl = template if template is not None else default_template_path().read_text(encoding="utf-8")
    seen: List[str] = []
    for name in _PLACEHOLDER.findall(tpl):
        if name not in seen:
            seen.append(name)
    return seen


__all__ = [
    "TemplateError",
    "compare_rendered",
    "default_template_path",
    "iter_tokens_used",
    "placeholder_names",
    "render_qss",
    "write_preset_qss",
    "write_qss",
]
