# -*- coding: utf-8 -*-
"""
项目版本号 · 单一事实来源 (Single Source of Truth)

[缺陷修复·版本号多源漂移] 修复前项目版本在四处各写一份且互不相同：
  - pyproject.toml          version = "2.7.0"   ← 打包元数据（真源）
  - tools/ky_cli.py:3938    打印 "ky-cli) v2.6.0"
  - tools/gui/__init__.py   __version__ = "2.5.0"
  - tools/tui_navigator.py  Banner 写死 "终端全景智能中枢 v2.5"
用户在任何一端看到的版本都不同，`ky --version` 与 `pip show kaoyan-study-chain`
也会打架，无法据此判断"我装的是哪一版、该不该升级"。

本模块把「项目版本」收敛为唯一入口，供 CLI / TUI / GUI / doctor 共用。

注：子系统自带的版本号（如 tools/intelligence/__init__.py 的 `1.0.0`，
是情报引擎自身的组件版本、不面向用户展示）不在此列 —— 那是组件版本，
不是产品版本，二者不应混为一谈。
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

_LOG = logging.getLogger(__name__)

#: 与 pyproject.toml 的 [project].name 保持一致
DIST_NAME = "kaoyan-study-chain"

#: 全部解析手段都失败时的兜底值（显式带 +unknown，便于一眼看出异常而非误当成真实版本）
UNKNOWN_VERSION = "0.0.0+unknown"


def _project_root() -> Path:
    """从本文件位置反推仓库根（tools/version.py → 仓库根）。"""
    return Path(__file__).resolve().parent.parent


def _version_from_pyproject() -> Optional[str]:
    """从 pyproject.toml 的 [project] 段读取 version。

    开发模式（源码目录直接运行、未 pip 安装）下 importlib.metadata 查不到分发版
    ——实测 `importlib.metadata.version("kaoyan-study-chain")` 抛
    `PackageNotFoundError` —— 因此必须有一条不依赖安装状态的回落路径。

    优先用 tomllib（Python ≥3.11）；3.10 无该模块，故保留正则兜底
    （只在 [project] 段内匹配首个 version 字段，避免误取 [tool.*] 下的同名字段）。
    """
    pyproject = _project_root() / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return None

    try:
        import tomllib  # Python ≥3.11

        with open(pyproject, "rb") as fh:
            data = tomllib.load(fh)
        raw = data.get("project", {}).get("version")
        return str(raw).strip() if raw else None
    except Exception as exc:
        # 预期内的回落：Python 3.10 无 tomllib，或 TOML 语法异常 → 改走正则
        _LOG.debug("tomllib 不可用，改用正则解析 pyproject: %s", exc)

    # Python 3.10 兜底：只截取 [project] 段，再取首个 version
    try:
        section = re.search(r"^\[project\]\s*$(.*?)(?=^\[|\Z)",
                            text, re.MULTILINE | re.DOTALL)
        if not section:
            return None
        match = re.search(r'^\s*version\s*=\s*["\']([^"\']+)["\']',
                          section.group(1), re.MULTILINE)
        return match.group(1).strip() if match else None
    except Exception:
        return None


@lru_cache(maxsize=1)
def get_version() -> str:
    """返回项目版本号（如 "2.7.0"）。

    解析优先级：
      1. 已安装分发版的元数据（wheel / pip install -e 后的权威值）
      2. 仓库内 pyproject.toml（源码/开发模式）
      3. UNKNOWN_VERSION 兜底
    """
    try:
        from importlib.metadata import version

        return str(version(DIST_NAME))
    except Exception as exc:
        # 预期内：源码/开发模式未安装分发版 → 回落 pyproject。
        # 实测本工作区 `importlib.metadata.version("kaoyan-study-chain")` 抛 PackageNotFoundError。
        _LOG.debug("未找到分发版元数据，改用 pyproject.toml: %s", exc)

    return _version_from_pyproject() or UNKNOWN_VERSION


def version_line(component: str = "") -> str:
    """统一的用户可见版本文案，避免各端再各自拼字符串。

    component 形如 "ky-cli" / "ky-gui"；留空则只返回 "vX.Y.Z"。
    """
    tag = f"v{get_version()}"
    return f"{component} {tag}".strip()


if __name__ == "__main__":  # pragma: no cover - 手工排查用
    print(get_version())
