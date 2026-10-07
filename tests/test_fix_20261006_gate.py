# -*- coding: utf-8 -*-
"""门禁轮修复回归（2026-10-06 仿真修复批次验收阶段发现，2 项）。

发现背景（pytest 全量门禁，长 basetemp 下 2 红）：
    ① tests/test_fix_20261005_data.py::test_ingest_long_source_name_truncated
       —— 目录路径较深时，``atomic_write_text`` 的 mkstemp 临时名（前缀 =
       ".目标名." 比目标名长 2 + 8 随机 + ".tmp"）比目标名长 14 字符：
       目标名尚在 Windows MAX_PATH 内、临时名先越界，``_os.open`` 报
       FileNotFoundError。用例随 basetemp 长度翻转（短路径过、长路径红）。
       修复：长目标名（>76 字符）截断临时前缀，保证临时名不长于目标名。
    ② tests/test_privacy_identity_rules.py::test_tests_dir_is_immune_to_py_sanitization
       —— W1 新增测试文件里残留真实校名（当前 ky_config 的 school 值），导出
       脱敏会改写该文件致公开副本断言自毁。修复：中性化为"测试大学"（由元测试
       钉住，本文件不再重复覆盖）。
"""
from __future__ import annotations


def _spy_mkstemp(monkeypatch, ky_io):
    seen = {}
    real_mkstemp = ky_io.tempfile.mkstemp

    def spy(*args, **kwargs):
        seen["prefix"] = kwargs.get("prefix")
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr(ky_io.tempfile, "mkstemp", spy)
    return seen


def test_atomic_write_text_long_name_tmp_prefix_bounded(tmp_path, monkeypatch):
    """长目标名 → 临时名前缀被截断，临时名（前缀+12）不长于目标名。

    契约：目标名可写 ⇒ 临时名必可写（深路径下不再先于目标名越界）。

    [POSIX 字节上限修复 2026-10-07] 名字长度上限在 POSIX 是 **字节制**
    （NAME_MAX=255 字节；Windows 才是字符/UTF-16 单元）。用 4 字节字符
    （emoji）构造唯一能区分新旧实现的探针：60 个「😀」+".md" = 61 字符 /
    243 字节 —— 旧字符制逻辑按「61 ≤ 76」判定不截断，tmp 名 = 245+12 =
    257 字节直接 Errno 36（原用例 130 个「深」= 390 字节，目标名本身在
    POSIX 就不可写，场景无法构造）；新字节制截断后 tmp ≤ 目标名
    （实测 241 ≤ 243）可写。纯 CJK（3 字节）在 ≤76 字符时 tmp ≤ 242 字节
    恰好安全，故必须用 4 字节字符才能钉住字节制。
    """
    from tools import ky_io

    seen = _spy_mkstemp(monkeypatch, ky_io)
    long_name = "😀" * 60 + ".md"  # 61 字符 = 243 字节（emoji 每字 4 字节）
    target = tmp_path / long_name
    ky_io.atomic_write_text(target, "payload")
    assert target.read_text(encoding="utf-8") == "payload", "长名写入内容不符"

    prefix = seen["prefix"]
    assert prefix.startswith("."), "临时文件应保持点前缀隐藏约定"
    # 临时名 = 前缀 + 8 随机字符 + ".tmp"；按 **字节** 断言（POSIX 是字节制上限）
    assert len(prefix.encode("utf-8")) + 12 <= len(long_name.encode("utf-8")), \
        (f"临时名前缀未截断：prefix={len(prefix.encode('utf-8'))}B "
         f"name={len(long_name.encode('utf-8'))}B")


def test_atomic_write_text_short_name_prefix_unchanged(tmp_path, monkeypatch):
    """阴性对照：短目标名不截断，前缀保持 ".目标名." 便于残留排障。"""
    from tools import ky_io

    seen = _spy_mkstemp(monkeypatch, ky_io)
    name = "错题本_数学_20260101.md"
    ky_io.atomic_write_text(tmp_path / name, "x")
    assert seen["prefix"] == f".{name}."
