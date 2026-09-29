# -*- coding: utf-8 -*-
"""三端统一的 intelligence 包解析（CLI / GUI / TUI）。

[W12 P0-1] 此前三端各写一套导入：CLI 用 ``_get_intelligence_module()``（tools 优先）、
GUI 用内联双路（顶层优先）、TUI 用裸 ``from intelligence import ...`` —— 直跑
``py tools/tui_navigator.py`` 时 sys.path[0] 是 tools 目录而非仓库根，
intelligence 包内函数级绝对导入（如 agentic_research 的
``from tools.intelligence.xxx import ...``）会命中 site-packages 的空 tools
命名空间包，双校对标因此报 ``No module named 'tools.intelligence'``。

本模块提供唯一解析函数，三端复用。各端仍需自行把 tools 目录与仓库根插入
sys.path（见各端头部，与 ky_cli.py / init_workspace.py 既有做法一致）——
只有 path 正确，包内绝对导入才有一致的解析结果。
"""


def resolve_intel_import():
    """返回 intelligence 包对象（``tools.intelligence`` 优先，顶层 ``intelligence`` 兜底）。

    两条路径命中同一份文件时以 ``tools.intelligence`` 为准（与包内绝对导入
    口径一致），避免同一包被加载成两个模块对象（单例/冷却表被复制两份）。
    两条路径都不可用时抛 ImportError，由调用方决定降级或报错。
    """
    try:
        from tools import intelligence
        return intelligence
    except ImportError:  # pragma: no cover - 直跑脚本且仓库根不在 sys.path 时
        import intelligence  # type: ignore
        return intelligence
