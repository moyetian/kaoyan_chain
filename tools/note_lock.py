# -*- coding: utf-8 -*-
"""
考研学习链 · 笔记锁定与 Frontmatter 解析 (Note Lock & Frontmatter)

本模块解决一件事：**让学员能把某份笔记「钉死」**，防止被自动流程覆盖。

背景：错题卡片、每日笔记这类 Markdown 会由私教自动回写（补状态、改复测节奏、
追加解析）。当学员手工精修过某篇笔记后，希望系统不再动它 —— 只需在文件开头
写入 YAML frontmatter 声明 ``locked: true``：

    ---
    title: 泰勒展开三大陷阱
    type: note
    locked: true
    ---
    # 正文...

之后 :func:`assert_writable` 会拒绝任何对该文件的改写（抛 :class:`NoteLockedError`）。

与 ``ky_io`` 的分工（两者都别混用）
-----------------------------------
* ``ky_io``      —— 文件**写入原子性**（同目录临时文件 + os.replace）与并发锁（filelock）
* ``note_lock``  —— 文件**业务语义**上的只读标记（frontmatter 的 ``locked`` 字段）

``note_lock`` **不做**跨进程文件锁，那由 ``ky_io`` 完成。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

__all__ = [
    "NoteLockedError",
    "has_frontmatter",
    "parse_frontmatter",
    "note_is_locked",
    "assert_writable",
]

#: 判定为「锁定」的字段取值（YAML 的 true / "true" / yes 等统一归一化）
_TRUTHY = {"true", "yes", "y", "1", "on"}


class NoteLockedError(PermissionError):
    """目标笔记被 frontmatter 标记为 ``locked: true``，拒绝改写。"""


def has_frontmatter(md_content: str) -> bool:
    """内容是否以 YAML frontmatter 起始（``---`` 开头，兼容 CRLF）。"""
    return md_content.startswith("---\n") or md_content.startswith("---\r\n")


def parse_frontmatter(md_content: str) -> Dict[str, Any]:
    """解析 YAML frontmatter 并**保证返回 dict**。

    历史实现直接返回 ``yaml.safe_load`` 的结果，而 YAML 顶层完全可以是标量或列表
    （``---\\njust a string\\n---`` 会返回 ``str``），调用方一旦 ``.get()`` 就
    ``AttributeError``。现在统一收敛：非映射类型、解析失败、无 frontmatter
    三种情况一律返回空字典。
    """
    if not md_content or not has_frontmatter(md_content):
        return {}

    # 同时兼容 \n 与 \r\n 两种行尾
    separator = "---\r\n" if md_content.startswith("---\r\n") else "---\n"
    parts = md_content.split(separator, 2)
    if len(parts) < 3:
        return {}
    try:
        loaded = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return {}
    # 只有映射才具备"字段"语义；标量/列表/None 一律视为无有效 frontmatter
    return loaded if isinstance(loaded, dict) else {}


def _to_bool(value: Any) -> bool:
    """把 YAML 反序列化出的各种"真值"写法归一化为 bool。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in _TRUTHY


def note_is_locked(md_content: str) -> bool:
    """内容是否被标记为锁定（``locked`` 字段为真值）。"""
    return _to_bool(parse_frontmatter(md_content).get("locked"))


def assert_writable(path: "str | Path", *, force: bool = False) -> None:
    """写入前的锁定闸门：目标笔记被锁定且未显式 ``force`` 时抛 :class:`NoteLockedError`。

    文件不存在（新建）或没有 frontmatter 时直接放行 —— 锁定只对"已存在的、
    学员显式声明过的"笔记生效，不会影响正常流程。

    Args:
        path: 目标笔记路径。
        force: 为 True 时跳过锁定检查（供学员显式执行的"强制覆盖"入口使用）。

    Raises:
        NoteLockedError: 目标存在且 frontmatter 中 ``locked`` 为真值。
    """
    if force:
        return
    target = Path(path)
    if not target.is_file():
        return
    try:
        content = target.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return  # 读不到就不拦，交由下游的写入错误正常暴露
    if note_is_locked(content):
        raise NoteLockedError(
            f"笔记已被锁定（frontmatter: locked: true），拒绝改写: {target.name}。"
            "如需修改，请先移除该文件开头的 locked 字段，或使用显式强制入口。"
        )
