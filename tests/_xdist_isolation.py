# -*- coding: utf-8 -*-
"""xdist 并行时的 per-worker QSettings 隔离（从 conftest.py 拆出）。

背景：QSettings 是**用户级共享存储**（Windows 为注册表，Linux/macOS 为
用户配置目录下的 ini）：``pytest -n N`` 的多个 worker 并发读写同一批键
会互踩 —— 实测 ``ui/rail_collapsed`` / ``ui/preset`` 等断言曾在并行下
随机变红（各 worker 的写/恢复交织）。

演进（两次实测教训）：
  * 首版 ``setDefaultFormat(IniFormat)`` + ``setPath`` —— 无效：Qt6 的
    ``QSettings(org, app)`` 在 Windows 上恒走注册表（最小复现确认）；
  * 次版只改 ``tools.gui.theme_apply`` —— 被「双导入」打回：产品代码按
    ``gui.theme_apply`` / ``tools.gui.theme_apply`` 双路径 try/except
    导入，两个模块对象各持一份 ``SETTINGS_ORG``，只改一份会让「产品
    写入的域」与「测试读取的域」分裂（实测：切换主题后读回 None）。
现两处副本都主动 import 出来、统一改到同一新 org，再用 sys.modules
扫描兜底其它副本。单进程运行（无 ``PYTEST_XDIST_WORKER``）时为 no-op，
行为与原版完全一致。残留：每 worker 在 HKCU 留下测试专属子键
（命名带 ``__xdist_`` 便于识别）。
"""

import contextlib
import importlib
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

_XDIST_WORKER = os.environ.get("PYTEST_XDIST_WORKER")


def _theme_apply_module_copies() -> list:
    """import 出 ``theme_apply`` 的两份副本（双导入），并扫描 sys.modules 兜底。"""
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tools"))
    for mod_name in ("tools.gui.theme_apply", "gui.theme_apply"):
        try:
            importlib.import_module(mod_name)
        except Exception:  # pragma: no cover - 无 Qt 环境等
            continue
    return [
        mod for name, mod in sys.modules.items()
        if (name == "theme_apply" or name.endswith(".theme_apply"))
        and getattr(mod, "SETTINGS_ORG", None)
    ]


def apply_worker_isolation() -> None:
    """把 QSettings 的 org 切到 per-worker 域（单进程时为 no-op）。"""
    if not _XDIST_WORKER:
        return
    copies = _theme_apply_module_copies()
    if not copies:
        return
    new_org = f"{copies[0].SETTINGS_ORG}__xdist_{_XDIST_WORKER}"
    for mod in copies:
        # pragma: no cover - 防御：隔离失败不得炸收集
        with contextlib.suppress(Exception):
            mod.SETTINGS_ORG = new_org


apply_worker_isolation()
