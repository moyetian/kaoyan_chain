# -*- coding: utf-8 -*-
"""项目版本三处一致性门禁 (K2)
================================
核对三个来源的版本号必须一致：

  1. ``pyproject.toml`` 的 ``[project].version``（打包元数据 · 真源）
  2. 仓库根 ``installer.iss`` 的 ``#define MyAppVersion``
     （由 ``tools/build_package.py`` 构建时同步生成）
  3. ``tools.version.get_version()``（CLI / TUI / GUI / doctor 的运行时取值）

任一来源缺失、三值不一致、或 ``get_version()`` 回退到兜底值
``0.0.0+unknown``，脚本以退出码 1 判红（CI 门禁用法：
``python tools/check_version_consistency.py``）。

[单一实现] 一致性判定收敛在本模块的 ``check_version_consistency()`` 核心函数，
``tools/doctor.py`` 复用同一函数做体检展示 —— 禁止第二份实现（历史上版本检查
在多处各写一份，正是版本漂移的根源）。

兼容从仓库根（``py tools/check_version_consistency.py``）或 tools/ 目录
（``cd tools && py check_version_consistency.py``）直接执行，也兼容
``tools.check_version_consistency`` 包式导入。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

try:  # 双导入路径兼容（源码脚本式 / tools 包式）
    from version import UNKNOWN_VERSION, get_version
    from workspace import resolve_workspace_root
except ImportError:  # pragma: no cover
    from tools.version import UNKNOWN_VERSION, get_version
    from tools.workspace import resolve_workspace_root


def _read_pyproject_version(path: Path) -> Optional[str]:
    """读取 pyproject.toml 的 [project].version；文件缺失/字段异常返回 None。

    优先 tomllib（Python ≥3.11）；3.10 无该模块，保留正则兜底（只在 [project]
    段内匹配首个 version，避免误取 [tool.*] 下的同名字段）——与
    tools/version.py 的解析口径一致。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    try:
        import tomllib  # Python ≥3.11

        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        raw = data.get("project", {}).get("version")
        return str(raw).strip() if raw else None
    except Exception:
        pass

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


def _read_iss_version(path: Path) -> Optional[str]:
    """读取 Inno Setup 脚本的 ``#define MyAppVersion "x.y.z"``；缺失返回 None。"""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    match = re.search(r'^\s*#define\s+MyAppVersion\s+"([^"]+)"', text, re.MULTILINE)
    return match.group(1).strip() if match else None


def _runtime_version() -> str:
    """运行时版本（tools.version.get_version()），单独包一层便于测试注入。"""
    return str(get_version()).strip()


def check_version_consistency(root: Optional[Path] = None) -> Tuple[bool, Dict[str, object]]:
    """核对三处版本一致性，返回 ``(ok, info)``。

    :param root: 仓库根；None 时按本文件位置解析（兼容从仓库根或 tools/ 执行）。
    :returns: ``(ok, info)``；info 键：``pyproject`` / ``installer_iss`` /
              ``runtime``（三处实际值，缺失为 None）、``detail``（人类可读差异，
              含三处实际值）、``frozen``（是否冻结环境降级核对）。
    """
    base = Path(root) if root is not None else resolve_workspace_root(__file__)
    pyproject_ver = _read_pyproject_version(base / "pyproject.toml")
    iss_ver = _read_iss_version(base / "installer.iss")
    runtime_ver = _runtime_version()
    values_line = (f"pyproject.toml={pyproject_ver} / installer.iss={iss_ver} / "
                   f"get_version()={runtime_ver}")

    # 冻结（PyInstaller 安装版）运行时没有 pyproject.toml / installer.iss 源文件，
    # 三处对照不可得 —— 退化为「运行时版本必须可用（非兜底值）」，与旧版 doctor
    # 的判据一致，避免安装版体检新增误报；源码 / CI 环境（非 frozen）不受影响，
    # 门禁保持三处全核对。
    if (root is None and getattr(sys, "frozen", False)
            and not pyproject_ver and not iss_ver):
        ok = bool(runtime_ver) and runtime_ver != UNKNOWN_VERSION
        detail = ("冻结环境（pyproject.toml / installer.iss 源文件不可得）："
                  + (f"get_version()={runtime_ver}" if ok
                     else f"get_version()={runtime_ver or '空值'} 为兜底值"))
        return ok, {"pyproject": pyproject_ver, "installer_iss": iss_ver,
                    "runtime": runtime_ver, "detail": detail, "frozen": True}

    problems = []
    if not pyproject_ver:
        problems.append("pyproject.toml 缺失 [project].version（文件不存在或字段异常）")
    if not iss_ver:
        problems.append("installer.iss 缺失 #define MyAppVersion（文件不存在或字段异常）")
    if not runtime_ver or runtime_ver == UNKNOWN_VERSION:
        problems.append(f"tools.version.get_version() 返回兜底值（{runtime_ver or '空值'}）")
    if pyproject_ver and iss_ver and runtime_ver and len({pyproject_ver, iss_ver, runtime_ver}) > 1:
        problems.append("三处版本值不一致")

    ok = not problems
    detail = values_line if ok else "；".join(problems) + f"（实际值：{values_line}）"
    return ok, {"pyproject": pyproject_ver, "installer_iss": iss_ver,
                "runtime": runtime_ver, "detail": detail, "frozen": False}


def main() -> int:
    ok, info = check_version_consistency()
    if ok:
        print(f"[OK] 版本一致性通过：pyproject.toml={info['pyproject']} / "
              f"installer.iss={info['installer_iss']} / get_version()={info['runtime']}")
        return 0
    print("[FAIL] 版本一致性检查未通过：")
    print(f"  - pyproject.toml [project].version     = {info['pyproject']}")
    print(f"  - installer.iss  #define MyAppVersion  = {info['installer_iss']}")
    print(f"  - tools.version.get_version()          = {info['runtime']}")
    print(f"  原因：{info['detail']}")
    print("  修复：以 pyproject.toml 为真源统一三处"
          "（installer.iss 由 tools/build_package.py 构建时同步生成）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
