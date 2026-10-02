# -*- coding: utf-8 -*-
"""看板构建的跨端统一执行器（frozen 进程内 / 源码子进程 双模式）。

[问题7 根因修复] PyInstaller 打包后 ``sys.executable`` 是 GUI 主程序自身：
旧实现各处用 ``subprocess.run([sys.executable, "05-考研看板/build.py"])``
触发构建，在 exe 版里等价于**再启动一个 GUI 主界面** —— 实测考生点击
「更新看板」/「看板更新」卡片后弹出第二个主窗口，构建从未发生。本模块把
「如何执行构建」收口为单一实现：

  * frozen（exe 版）→ 进程内 ``runpy`` 执行 build.py，不产生新进程；
  * 源码模式       → 保留子进程（隔离、可透传 argv、行为与历史一致）。

新增调用点一律走本模块，不要再直接写
``subprocess.run([sys.executable, ...build.py])``。
"""

from __future__ import annotations

import contextlib
import io
import os
import runpy
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Optional

try:  # 双导入路径兼容（源码脚本式 / tools 包式）
    from workspace import resolve_workspace_root
except ImportError:  # pragma: no cover
    from tools.workspace import resolve_workspace_root


def _find_build_script(workspace_root: Optional[Path] = None) -> Path:
    root = Path(workspace_root) if workspace_root is not None else resolve_workspace_root(__file__)
    return root / "05-考研看板" / "build.py"


def _run_inprocess(build_script: Path, extra_args: list,
                   env_overrides: dict, capture_output: bool) -> int:
    """在当前进程内执行 build.py（frozen 专用路径）。

    需要完整模拟一次「独立子进程」的执行环境：
      * ``sys.argv`` / cwd / 目标环境变量临时替换，执行后还原；
      * ``web`` 包隔离 —— build.py 依赖 ``05-考研看板/web/``，若宿主进程
        已导入过其它同名顶层包，``import web`` 会命中缓存。执行前移出、
        执行后原样还原（先清 build 期间新导入的 web*，再恢复外部同名包）。
    """
    old_argv = sys.argv[:]
    old_cwd = os.getcwd()
    saved_env = {}
    for key, value in env_overrides.items():
        saved_env[key] = os.environ.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    local_web_root = build_script.parent / "web"
    saved_web = {n: m for n, m in list(sys.modules.items())
                 if n == "web" or n.startswith("web.")}
    # 只清理可能遮蔽目标看板的模块：保留宿主进程加载的其它 web
    # 命名空间，减少 frozen GUI 每次构建的无谓重载。
    for n, module in saved_web.items():
        module_file = getattr(module, "__file__", None)
        if n == "web" or (module_file and
                           Path(module_file).resolve().is_relative_to(local_web_root.resolve())):
            del sys.modules[n]

    sys.argv = [str(build_script), *extra_args]
    try:
        os.chdir(str(build_script.parent))
        if capture_output:
            with contextlib.redirect_stdout(io.StringIO()):
                runpy.run_path(str(build_script), run_name="__main__")
        else:
            runpy.run_path(str(build_script), run_name="__main__")
        return 0
    except SystemExit as exc:
        code = exc.code
        if isinstance(code, int):
            return code
        return 0 if code is None else 1
    except Exception as exc:
        print(f"[!] 看板构建异常: {exc}")
        return 1
    finally:
        sys.argv = old_argv
        try:
            os.chdir(old_cwd)
        except OSError:  # pragma: no cover - 原 cwd 被删除等极端情况
            pass
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        for n, module in list(sys.modules.items()):
            if n == "web" or (n.startswith("web.") and
                               getattr(module, "__file__", None) and
                               Path(module.__file__).resolve().is_relative_to(local_web_root.resolve())):
                del sys.modules[n]
        sys.modules.update(saved_web)


def run_dashboard_build(extra_args: Iterable[str] = (), workspace_root: Optional[Path] = None,
                        snapshot_opt_in: Optional[bool] = None,
                        capture_output: bool = False) -> int:
    """执行看板构建并返回退出码（127 = 未找到构建脚本）。

    :param extra_args: 透传给 build.py 的参数（如 ``--cdn``）。
    :param workspace_root: 工作区根；None 时自动解析（frozen → exe 目录）。
    :param snapshot_opt_in: True/False → 显式设置 ``KY_SNAPSHOT_OPT_IN=1/0``
        （本地完整模式 = False）；None → 不干预、继承当前环境。
    :param capture_output: True 时丢弃构建输出（报到等后台静默场景）；
        False 时输出透传给调用方 stdout（GUI 场景由外层 redirect_stdout 捕获回显）。
    """
    build_script = _find_build_script(workspace_root)
    if not build_script.exists():
        return 127

    env_overrides = {}
    if snapshot_opt_in is not None:
        env_overrides["KY_SNAPSHOT_OPT_IN"] = "1" if snapshot_opt_in else "0"
        if not snapshot_opt_in:
            # 完整模式含私人学情，所有统一构建入口都落到未跟踪的本地目录。
            # 发布模式（True）继续写入 Pages 的 docs/ 真源。
            env_overrides["KY_DASHBOARD_OUTPUT_DIR"] = "docs/.local"
        else:
            env_overrides["KY_DASHBOARD_OUTPUT_DIR"] = None

    if getattr(sys, "frozen", False):
        return _run_inprocess(build_script, list(extra_args), env_overrides, capture_output)

    env = {**os.environ, **{k: v for k, v in env_overrides.items() if v is not None}}
    for key, value in env_overrides.items():
        if value is None:
            env.pop(key, None)
    result = subprocess.run([sys.executable, str(build_script), *extra_args],
                            cwd=str(build_script.parent), env=env,
                            capture_output=capture_output)
    return result.returncode


__all__ = ["run_dashboard_build"]
