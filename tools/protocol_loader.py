# -*- coding: utf-8 -*-
"""
考研学习链 · 私教协议加载器 (Protocol Loader)

统一解析 ``AGENTS.md`` / ``GEMINI.md`` / ``00_考研全科总战役规划.md`` 这类
「顶层协议文件」的位置差异：

* **源码模式**：协议文件位于仓库根目录（``<project>/AGENTS.md``）
* **安装模式**：``pyproject.toml`` 通过 ``force-include`` 把协议文件放进
  wheel 内的 ``tools/protocol/``（见 ``[tool.hatch.build.targets.wheel.force-include]``），
  因此根目录下**并不存在**这些文件

历史问题：``ky_cli`` 直接读 ``ROOT / "AGENTS.md"``。源码模式正常，但装成
wheel 后 ``ROOT`` 变成 ``site-packages``，顶层协议会**静默丢失**（私教失去
总控约束却不报错）。本模块把两种模式收敛为一次调用。

查找顺序（返回**第一个命中**）：
  1. 包内资源 ``tools.protocol/<name>.md``（安装模式）
  2. 工作区根目录 ``<root>/<name>.md``（源码模式）
  3. 均未命中 -> 抛 :class:`FileNotFoundError`（**显式失败**，不返回空串）
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

__all__ = ["load_protocol", "protocol_path", "PROTOCOL_PACKAGE", "DEFAULT_PROTOCOL"]

#: wheel 打包时协议文件所在的包
PROTOCOL_PACKAGE = "tools.protocol"

#: CLI 缺省加载的顶层协议名（不含 .md 后缀）
DEFAULT_PROTOCOL = "AGENTS"


def _workspace_root() -> Path:
    """本文件位于 ``tools/protocol_loader.py``，其父目录的父目录即工作区根。"""
    return Path(__file__).resolve().parent.parent


def protocol_path(name: str) -> "Path | None":
    """返回协议文件的真实路径；两个候选位置都不存在时返回 ``None``。"""
    filename = f"{name}.md"
    try:
        resource = resources.files(PROTOCOL_PACKAGE) / filename
        if resource.is_file():
            return Path(str(resource))
    except (ModuleNotFoundError, ImportError, TypeError):
        # tools.protocol 包不存在（源码模式）—— 属预期情况，静默落到下一候选
        pass

    local = _workspace_root() / filename
    return local if local.is_file() else None


def load_protocol(name: str = DEFAULT_PROTOCOL) -> str:
    """读取并返回协议文件正文（UTF-8）。

    Raises:
        FileNotFoundError: 包内资源与工作区根目录均未找到该协议文件。
    """
    path = protocol_path(name)
    if path is None:
        raise FileNotFoundError(
            f"协议文件 {name}.md 既不在包资源 {PROTOCOL_PACKAGE}/ 内，也不在工作区根目录 "
            f"{_workspace_root()} 下。请确认项目已正确安装或工作区结构完整。"
        )
    return path.read_text(encoding="utf-8")
