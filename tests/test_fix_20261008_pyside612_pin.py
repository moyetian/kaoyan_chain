# -*- coding: utf-8 -*-
"""PySide6 6.12.0 段错误回归的依赖上限守护（2026-10-08）。

背景（CI 实测，2026-10-08）：
  * PySide6 6.12.0（2026-10-08 时 PyPI 最新）存在「退出阶段段错误」回归：
    pytest 全量测试通过后、解释器关闭期在纯 C++ 侧崩溃（POSIX exit 139 /
    Windows exit 1，faulthandler 无 Python 帧）。CI 全平台 9/9 红；此前
    同一工作流装 6.11.2 时 9/9 绿（两次 run 的安装日志为直接证据）。
  * 最小复现（MainWindow 创建/关闭 ×2，offscreen，隔离 venv 实测）：
    6.12.0 崩 7/10 次、6.11.2 为 0/10。
  * 修复 = 四处安装入口全部 pin ``<6.12``（requirements.txt / pyproject.toml
    的 gui+full extra / GUI.bat 兜底自动安装）；requirements.lock 本就锁
    6.11.2，无需改动。

本文件锁定上限不被误放开（防 6.12.0 段错误复发）；待上游发布修复版并实测
（如 6.12.1 最小复现 0/10 + 全量套件干净退出）后再同步更新四处 pin 与本文件。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PINNED_SPEC = "PySide6>=6.6.0,<6.12"


def test_requirements_pyside6_has_upper_bound():
    """requirements.txt 必须 pin <6.12，且保留回归说明（防说明被清理）。"""
    raw = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert PINNED_SPEC in raw, (
        "requirements.txt 的 PySide6 未 pin <6.12 —— 6.12.0 会致退出阶段段错误"
    )
    assert "退出阶段段错误" in raw, (
        "requirements.txt 缺少 6.12.0 回归的说明注释"
    )


def test_pyproject_pyside6_has_upper_bound():
    """pyproject.toml 的 gui / full 两处 extra 都必须 pin <6.12。"""
    raw = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert raw.count(f'"{PINNED_SPEC}"') == 2, (
        "pyproject.toml 的 gui/full 两处 extra 必须同时 pin <6.12"
    )


def test_gui_bat_fallback_install_has_upper_bound():
    """GUI.bat 兜底自动安装必须 pin <6.12（GBK 编码，按字节解码核对）。"""
    raw = (ROOT / "GUI.bat").read_bytes().decode("gbk")
    assert f'pip install -q "{PINNED_SPEC}"' in raw, (
        "GUI.bat 兜底自动安装未 pin <6.12 —— 用户侧会装到 6.12.0"
    )
