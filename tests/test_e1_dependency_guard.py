# -*- coding: utf-8 -*-
"""E1 依赖兼容守卫：P1（ARC4 弃用警告白名单）+ P2（doctor 探测容错）+ P7（版本矩阵说明）。

背景（外部代码审查报告 P1/P7，2026-09-26 实测复现）：
  * cryptography>=46 把 ARC4 标记为弃用（48 将移除），pypdf 3.x 的 crypt
    provider 导入它时发 CryptographyDeprecationWarning —— 该类别是
    UserWarning 子类（**不是** DeprecationWarning），且经 pypdf 模块发出；
    pyproject 原有两条 `ignore::DeprecationWarning:{cryptography,pypdf}.*`
    按「类别 + 模块」都匹配不到 → filterwarnings=["error"] 把警告升级为
    异常 → 旧依赖组合（pypdf 3.x + cryptography 46/47）整批判红。
  * 修复 = 精确白名单 `ignore::cryptography.utils.CryptographyDeprecationWarning:
    pypdf.*`（限定发出模块，保留「自家代码触发 cryptography 弃用仍判红」）。
  * 隔离 venv（pypdf==3.17.4 + cryptography==46.0.4）实测：修复前
    `test_b4_mcp_skills_health::test_doctor_reports_real_skills_health`
    等 3 用例红；修复后全绿；去白名单的阴性对照 RED-OK。

本文件锁定配置不被误删/误放宽（CI 无 lint，纯配置改动无其他守护）。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PYPROJECT = ROOT / "pyproject.toml"
REQUIREMENTS = ROOT / "requirements.txt"

WHITELIST_LINE = "ignore::cryptography.utils.CryptographyDeprecationWarning:pypdf.*"
GLOBAL_LINE = "ignore::cryptography.utils.CryptographyDeprecationWarning"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_pyproject_has_pypdf_arc4_whitelist():
    """[P1] ARC4 弃用警告白名单必须存在（防被误删 → 旧依赖组合重新判红）。"""
    raw = _read(PYPROJECT)
    assert WHITELIST_LINE in raw, (
        "pyproject.toml 缺少 ARC4 弃用警告白名单：pypdf 3.x + cryptography>=46 "
        "环境会因 filterwarnings=['error'] 整批判红"
    )


def test_pyproject_whitelist_is_module_scoped_not_global():
    """[P1 精确性] 白名单必须限定 pypdf 模块，不得放宽为全局忽略。

    若这条变红：白名单被改成了不限模块版 —— 自家代码触发 cryptography
    弃用 API 时将不再判红（失去「弃用即修复」的护栏）。
    """
    raw = _read(PYPROJECT)
    # 去掉行尾逗号后比较：确保不存在「裸类别」条目（不限模块）
    lines = {line.strip().rstrip(",") for line in raw.splitlines()}
    assert GLOBAL_LINE not in lines, (
        "白名单被放宽为不限模块（全局忽略 CryptographyDeprecationWarning）—— "
        "请保持 `:pypdf.*` 模块限定"
    )


def test_requirements_documents_version_matrix():
    """[P7] requirements.txt 必须记录版本兼容矩阵与升级指引（防说明被清理）。"""
    raw = _read(REQUIREMENTS)
    assert "cryptography 48" in raw, "requirements.txt 缺少 cryptography 48 移除 ARC4 的说明"
    assert "pip install -U pypdf" in raw, "requirements.txt 缺少旧环境升级 pypdf 的指引"


def test_whitelist_category_path_is_importable():
    """[P1 运行时] 白名单引用的类别路径当前环境可导入（防路径拼写漂移）。

    该类别在 cryptography>=42 的历史版本中位于 cryptography.utils；
    若上游迁移（如改到 cryptography.exceptions），本测试变红提示同步。
    """
    from cryptography.utils import CryptographyDeprecationWarning

    assert issubclass(CryptographyDeprecationWarning, UserWarning), (
        "CryptographyDeprecationWarning 的基类变化 —— 白名单的类别匹配语义需复核"
    )


def test_doctor_probes_catch_non_import_errors():
    """[P2] doctor 依赖探测必须容错非导入类异常（防回退为只捕 ImportError）。

    场景：`-W error` 下 ARC4 弃用警告升级为 CryptographyDeprecationWarning
    （非 ImportError）会穿透 `except ImportError` 并中断整份体检。隔离
    venv（pypdf 3.17.4 + cryptography 46.0.4）实测：修复前 `ky doctor`
    输出「体检执行异常: ARC4 has been moved...」exit 1；修复后完整跑完
    并如实报「加载失败 (请重装 pypdf) (CryptographyDeprecationWarning: ...)」。
    """
    src = (ROOT / "tools" / "doctor.py").read_text(encoding="utf-8")
    markers = [
        "加载失败 (请重装 sympy)",
        "加载失败 (请重装 pypdf)",
        "加载失败 (请重装 cryptography)",
        "加载失败 (请重装 Pillow)",
        "加载失败 (可选，仅用于离线 OCR)",
        "加载失败 (请重装 PySide6)",
        "加载失败 (请重装 beautifulsoup4)",
    ]
    missing = [m for m in markers if m not in src]
    assert not missing, f"doctor 依赖探测缺少非导入类异常容错分支: {missing}"
