# -*- coding: utf-8 -*-
"""
命令处理层 (commands)
负责各领域命令模块的集中加载与向 dispatch 注册
"""

def load_all_commands() -> None:
    """按序加载命令模块，触发其内部 register() 调用"""
    try:
        from tools.cli.commands import (system, daily, study, intel, material,
                                        misc, session, search, gain)
    except ImportError:
        from cli.commands import (system, daily, study, intel, material,
                                  misc, session, search, gain)
