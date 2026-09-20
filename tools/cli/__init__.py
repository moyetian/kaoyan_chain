# -*- coding: utf-8 -*-
"""
考研学习链 CLI 模块化架构
重构自 tools/ky_cli.py (5144 行 → 模块化拆分)
"""

# 主入口将由 dispatch.py 提供
__all__ = ["main"]

def main():
    """CLI 主入口（向后兼容）"""
    from tools.cli.dispatch import main as dispatch_main
    return dispatch_main()
