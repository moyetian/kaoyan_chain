# -*- coding: utf-8 -*-
"""P13 回归测试：PyInstaller spec 不得写死本机绝对路径。

背景（多角色端到端测试审查报告 P13）：
    提交入库的 KaoyanStudyChain.spec 里 datas / Analysis / EXE(icon) 三处
    共 4 行硬编码了作者本机路径 ``C:/Users/29652/Desktop/考研学习chain``。
    任何其它用户 clone 后按文档执行该 spec 打包，都会指向不存在的目录而失败，
    属于「只有作者能跑」的隐性单机依赖。

本测试锁定该文件必须保持路径无关（基于 SPECPATH 推导）。
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "KaoyanStudyChain.spec"

# 常见本机绝对路径特征：Windows 盘符根、用户目录、macOS/Linux 家目录
_ABS_PATH_PATTERNS = [
    re.compile(r"[A-Za-z]:[\\/]{1,2}Users[\\/]", re.IGNORECASE),
    re.compile(r"[A-Za-z]:[\\/]{1,2}Desktop[\\/]", re.IGNORECASE),
    re.compile(r"/home/[A-Za-z0-9_.-]+/"),
    re.compile(r"/Users/[A-Za-z0-9_.-]+/"),
]


@pytest.mark.skipif(not SPEC.exists(), reason="未提交 spec 文件，跳过")
def test_spec_has_no_hardcoded_absolute_paths():
    """spec 中不得出现任何指向个人目录的绝对路径。"""
    text = SPEC.read_text(encoding="utf-8")
    for pat in _ABS_PATH_PATTERNS:
        m = pat.search(text)
        assert m is None, f"spec 残留本机绝对路径: {m.group(0) if m else ''}"


@pytest.mark.skipif(not SPEC.exists(), reason="未提交 spec 文件，跳过")
def test_spec_uses_specpath_root():
    """spec 必须基于 SPECPATH 推导根目录，而不是写死路径。"""
    text = SPEC.read_text(encoding="utf-8")
    assert "SPECPATH" in text, "spec 未使用 SPECPATH 推导根目录，跨机不可用"
