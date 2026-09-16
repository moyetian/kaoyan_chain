# -*- coding: utf-8 -*-
"""
考研学习链 · GUI 可视化操作端 (Kaoyan Study Chain GUI)
"""

# [缺陷修复·版本号多源漂移] 此前这里写死 "2.5.0"，与 pyproject 的 2.7.0 不一致；
# 现统一从 tools/version.py 读取。双路径导入以兼容 `py tools/ky_gui.py`（脚本式，
# sys.path 含 tools/）与 `import tools.gui`（包式）两种运行方式。
try:
    from version import get_version  # noqa: E402
except ImportError:  # pragma: no cover - 兼容 tools.version 包式导入
    try:
        from tools.version import get_version  # type: ignore  # noqa: E402
    except ImportError:  # pragma: no cover - 极端情况不应发生，但不让 GUI 因此起不来
        def get_version() -> str:  # type: ignore[misc]
            return "0.0.0+unknown"

__version__ = get_version()
