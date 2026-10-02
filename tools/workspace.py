# -*- coding: utf-8 -*-
"""运行时工作区根解析（单一真源）。

[缺陷修复·exe 双根分裂] 源码模式下各模块用 ``Path(__file__).resolve().parent…``
反推仓库根没有问题；但 PyInstaller 打包后模块的 ``__file__`` 落在 ``_internal``
下，且按模块命名空间（``gui.*`` 与 ``tools.*``）推导出的层数不同，导致
``gui.*`` 算出的根是 exe 目录、而 ``tools.*`` 算出的根是 ``_internal`` ——
设置写 A、agent 读 B，备考信息"配置了却不生效"（实测：向导写的 ky_config /
参考资料 / 考试大纲在 exe 目录，而报到时 agent 读的是 ``_internal`` 下的
打包骨架模板）。

现统一：

- frozen（PyInstaller）：工作区根 = exe 所在目录。安装器已在其下释放
  ``01-数学/02-英语/…`` 骨架，是用户数据的唯一落点；
- 源码：从调用方文件向上找到含 ``pyproject.toml`` 的目录（即仓库根）。

所有需要"用户工作区根"的模块一律调用本函数，禁止再按 ``__file__`` 层数推导。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Union


def resolve_workspace_root(start: Optional[Union[str, Path]] = None) -> Path:
    """解析当前运行环境的工作区根目录。

    :param start: 源码模式下的起始路径（通常传 ``__file__``）；frozen 模式忽略。
    """
    if getattr(sys, "frozen", False):
        # PyInstaller 打包：exe 所在目录即用户工作区（安装器释放的骨架目录）。
        return Path(sys.executable).resolve().parent

    here = Path(start) if start is not None else Path(__file__)
    try:
        here = here.resolve()
    except OSError:  # pragma: no cover - 极端环境（路径不可解析）
        here = Path(start) if start is not None else Path(__file__)
    if here.is_file():
        here = here.parent
    # Prefer the nearest self-contained workspace when a test, packaged
    # fixture, or nested checkout lives below another repository. The old
    # pyproject-first search picked the outer checkout and mixed its config,
    # syllabus and generated artifacts into the inner workspace.
    for cand in (here, *here.parents):
        if (cand / "tools").is_dir() and (cand / "ky_config.json").is_file():
            return cand
    for cand in (here, *here.parents):
        if (cand / "pyproject.toml").exists():
            return cand
    # 兜底：向上找含 ``tools/`` 的祖先 —— 覆盖「tools/xxx.py」与
    # 「tools/子包/xxx.py」两种深度。旧兜底只回退一级：在**没有 pyproject.toml**
    # 的临时工作区（测试夹具把工具文件拷进 tmp 再运行）里，深层文件
    # （如 tools/skills/knowledge_map.py）会错算到 tmp/tools 而不是工作区根。
    for cand in (here, *here.parents):
        if (cand / "tools").is_dir():
            return cand
    # 再兜底：``tools/`` 目录不存在（测试把 ``__file__`` 指向纯字符串假路径，
    # 如 ``tmp/tools/cli/shared.py`` 而无真实目录）时，按路径段结构回退 ——
    # 命中名为 ``tools`` 的路径段则返回其父目录（即工作区根）。
    parts = here.parts
    if "tools" in parts:
        idx = len(parts) - 1 - parts[::-1].index("tools")
        if idx > 0:
            return Path(*parts[:idx])
    return here.parent


__all__ = ["resolve_workspace_root"]
