# -*- coding: utf-8 -*-
"""review_gate 门禁规则的单元测试（纯函数级，无 IO、无 git）。

覆盖 CODE_REVIEW.md §4.2 红线（R1–R6）与 §4.3 存量保护线（ratchet）的判定，
含两个实测误报缺陷的守护：
  1. [Regress] BAD_TEST_NAME_RE 不得误伤 test_utility_* / test_utf8_* 等以
     "ut" 开头的正常命名（旧实现用裸前缀 "test_ut"，实测误报）。
  2. [Regress] 存量函数被触碰时按 ratchet 判定（只卡变差），不得像旧实现那样
     直接按新增红线误报 ERROR（实测 run_doctor() 等 39 条误报）。

本批（2026-10-12 OCR 代码审查 L/M 级修复）新增守护：
  M1 check_test_naming 段判断（含 "tests/" 子串但无独立段的路径不得崩溃）；
  L1 行数统计为真实内容行数（尾换行不多计）；L2 参数注解统计覆盖全部参数形态；
  L3 git 输出显式 UTF-8 解码（防 Windows ANSI 代码页解码失败被静默吞掉）。
"""

import ast
import subprocess
from pathlib import Path

import pytest

from tools import review_gate as rg
from tools import review_gate_git as rgg


def _func(name: str = "f", lines: int = 10, cc: int = 1, public: bool = True,
          has_doc: bool = True, missing_ann: int = 0, lineno: int = 1) -> dict:
    return {"name": name, "lineno": lineno, "lines": lines, "cc": cc,
            "public": public, "has_doc": has_doc, "missing_ann": missing_ann}


def _rep(lines: int) -> rg.FileReport:
    r = rg.FileReport("x.py")
    r.lines = lines
    return r


# ---------------------------------------------------------------- R4 命名正则

@pytest.mark.parametrize("name", [
    "test_fix_20261005_repl.py",
    "test_fix_20261011_r13_review.py",
    "test_w11_search_cooldown.py",
    "test_r3_stability.py",
    "test_c3_foo.py",
    "test_p3_tui_cli.py",
    "test_ut4_agent_core.py",
    "test_audit_20261003_fixes.py",
    "test_challenger_1_concurrency.py",
])
def test_bad_test_name_regex_rejects_batch_naming(name: str):
    assert rg.BAD_TEST_NAME_RE.match(name), f"{name} 应被判定为批次/日期式命名"


@pytest.mark.parametrize("name", [
    "test_utility_helpers.py",     # 旧实现 "test_ut" 裸前缀误伤的代表
    "test_utf8_encoding.py",
    "test_audit_log_rotation.py",  # audit 后接非数字 → 正常命名
    "test_return_semantics.py",
    "test_w3c_compliance.py",      # w 后接 3 再接 c（非下划线）→ 正常
    "test_python_paths.py",
    "test_review_gate_rules.py",
])
def test_bad_test_name_regex_allows_normal_naming(name: str):
    assert not rg.BAD_TEST_NAME_RE.match(name), f"{name} 不应被误伤"


# ---------------------------------------------------------------- AST 工具

def test_parse_hunk_range():
    assert rgg._parse_hunk_range("@@ -1,3 +10,5 @@") == (10, 5)
    assert rgg._parse_hunk_range("@@ -5 +7 @@") == (7, 1)
    assert rgg._parse_hunk_range("@@ bad @@") is None
    assert rgg._parse_hunk_range("no marker") is None


def test_complexity_counts_decision_points():
    assert rg._complexity(ast.parse("def f(x):\n    return x\n").body[0]) == 1
    assert rg._complexity(
        ast.parse("def f(x):\n    if x:\n        return 1\n    return 0\n").body[0]) == 2
    assert rg._complexity(
        ast.parse("def f(xs):\n    return [x for x in xs if x]\n").body[0]) == 3


def test_func_metrics_from_source():
    src = "def a():\n    pass\n\n\ndef b():\n    if True:\n        pass\n"
    m = rg._func_metrics_from_source(src)
    assert m["a"] == [(2, 1)]
    assert m["b"] == [(3, 2)]


def test_func_metrics_same_name_ordered():
    src = ("class A:\n    def m(self):\n        pass\n\n\n"
           "class B:\n    def m(self):\n        pass\n        pass\n")
    m = rg._func_metrics_from_source(src)
    assert [ln for ln, _cc in m["m"]] == [2, 3]


def test_analyze_file_counts_true_lines(tmp_path: Path):
    """[L1] 行数须为真实内容行数（len(splitlines)）：尾换行不多计 1 行。

    旧实现 src.count("\\n") + 1 对本仓库全部文件（尾换行）多计 1 行 ——
    恰为 500 行的文件会被误判"越过上限"，500 行整边界判定失真。
    """
    f = tmp_path / "mod.py"
    f.write_text("a = 1\nb = 2\n", encoding="utf-8")
    assert rg.analyze_file(f).lines == 2
    f.write_text("a = 1\nb = 2", encoding="utf-8")  # 尾无换行
    assert rg.analyze_file(f).lines == 2
    f.write_text("", encoding="utf-8")
    assert rg.analyze_file(f).lines == 0


def test_missing_annotations_covers_all_arg_kinds():
    """[L2] 注解统计须覆盖全部参数形态：仅位置（/）、*args、**kwargs。

    旧实现只数 args.args + kwonlyargs，posonlyargs / vararg / kwarg 三类
    缺注解被漏计（R6 静默漏报）。
    """
    n1 = ast.parse(
        "def f(a, /, b: int, *args, c: str = 'x', **kw) -> None:\n    pass\n"
    ).body[0]
    assert rg._missing_annotations(n1) == 3  # a / *args / **kw 缺注解
    n2 = ast.parse(
        "def m(self, x: int, *args: str) -> None:\n    pass\n"
    ).body[0]
    assert rg._missing_annotations(n2) == 0  # self 剥离，x 与 *args 均有注解


# ---------------------------------------------------------------- git 层

def test_git_path_normalizes_backslash():
    assert rgg._git_path("tools\\ky_io.py") == "tools/ky_io.py"
    assert rgg._git_path(Path("tools") / "a.py") == "tools/a.py"


def test_run_git_forces_utf8_encoding(monkeypatch: pytest.MonkeyPatch):
    """[L3] git 输出须显式 UTF-8 解码。

    text=True 在 Windows 上按 ANSI 代码页（cp936）解码，git 的 UTF-8 输出
    遇到非 GBK 字节序列会 UnicodeDecodeError，且被 _run_git 的 except 吞掉
    → 静默降级为"无变更"（fail-open）。本用例钉住调用契约：必须显式传
    encoding="utf-8" 与 errors="replace"。
    """
    captured: dict = {}

    def fake_run(args: list, **kwargs: object) -> subprocess.CompletedProcess:
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(rgg.subprocess, "run", fake_run)
    rgg._run_git(["git", "status"])
    assert captured.get("encoding") == "utf-8"
    assert captured.get("errors") == "replace"


# ---------------------------------------------------------------- R3 文件规模

def test_file_size_new_file_over_limit_is_error():
    fs = rg._file_size_finding(_rep(600), None, is_new=True)
    assert len(fs) == 1 and fs[0].rule == "R3" and fs[0].level == "ERROR"


def test_file_size_crossing_limit_is_error():
    fs = rg._file_size_finding(_rep(510), 480, is_new=False)
    assert fs[0].level == "ERROR" and "越过" in fs[0].msg


def test_file_size_already_over_growing_is_warn():
    fs = rg._file_size_finding(_rep(610), 600, is_new=False)
    assert fs[0].level == "WARN"


def test_file_size_shrinking_or_flat_is_silent():
    assert rg._file_size_finding(_rep(590), 600, is_new=False) == []
    assert rg._file_size_finding(_rep(600), 600, is_new=False) == []
    assert rg._file_size_finding(_rep(400), 380, is_new=False) == []


# ---------------------------------------------------------------- R1/R2/R5/R6

def test_new_func_red_lines():
    fs = rg._new_func_findings(
        _func(lines=70, cc=12, has_doc=False, missing_ann=2), "x.py")
    levels = {f.rule: f.level for f in fs}
    assert levels == {"R1": "ERROR", "R2": "ERROR", "R5": "ERROR", "R6": "WARN"}


def test_new_func_within_limits_silent():
    assert rg._new_func_findings(_func(lines=10, cc=2), "x.py") == []


def test_legacy_func_crossing_limit_is_error():
    fs = rg._legacy_func_findings(_func(lines=64), (50, 5), "x.py")
    assert any(f.rule == "R1" and f.level == "ERROR" for f in fs)


def test_legacy_func_growing_over_limit_is_warn():
    fs = rg._legacy_func_findings(_func(lines=750, cc=100), (748, 100), "x.py")
    assert any(f.rule == "R1" and f.level == "WARN" for f in fs)
    assert not any(f.level == "ERROR" for f in fs)


def test_legacy_func_unchanged_or_shrinking_silent():
    assert rg._legacy_func_findings(_func(lines=748, cc=100), (748, 100), "x.py") == []
    assert rg._legacy_func_findings(_func(lines=700, cc=90), (748, 100), "x.py") == []


def test_pair_old_funcs_by_order_and_new_left_unpaired():
    f1 = _func(name="m", lineno=10)
    f2 = _func(name="m", lineno=30)
    f3 = _func(name="brand_new", lineno=50)
    pairs = rg._pair_old_funcs([f2, f1, f3], {"m": [(5, 1), (7, 2)]})
    assert pairs[id(f1)] == (5, 1)
    assert pairs[id(f2)] == (7, 2)
    assert id(f3) not in pairs


# ---------------------------------------------------------------- R4 检查器

def test_check_test_naming_ok_in_layer():
    assert rg.check_test_naming("tests/unit/test_foo_bar.py", is_new=True) == []


def test_check_test_naming_windows_path_ok():
    assert rg.check_test_naming("tests\\unit\\test_foo_bar.py", is_new=True) == []


def test_check_test_naming_flat_root_errors():
    fs = rg.check_test_naming("tests/test_foo_bar.py", is_new=True)
    assert any(f.rule == "R4" and f.level == "ERROR" for f in fs)


def test_check_test_naming_batch_name_errors():
    fs = rg.check_test_naming("tests/unit/test_w11_x.py", is_new=True)
    assert any("批次" in f.msg for f in fs)


def test_check_test_naming_outside_tests_warns_only():
    fs = rg.check_test_naming("tools/test_foo.py", is_new=True)
    assert fs and all(f.level == "WARN" for f in fs)


def test_check_test_naming_tests_substring_without_segment_no_crash():
    """[M1] 含 "tests/" 子串但无独立 tests 段的路径不得崩溃。

    旧实现判定用子串（"mytests/"、"unit_tests/" 均命中 "tests/"），取值却用
    parts.index("tests") —— 段不存在时抛 ValueError，使 --diff 对单个文件
    整体崩溃。修复后统一按路径段判断：视为 tests/ 之外，给迁移 WARN。
    """
    for p in ("mytests/test_probe.py", "tools/unit_tests/test_probe.py"):
        fs = rg.check_test_naming(p, is_new=True)
        assert fs and all(f.level == "WARN" for f in fs), p


def test_check_test_naming_existing_flat_not_blocked():
    assert rg.check_test_naming("tests/test_foo_bar.py", is_new=False) == []


def test_func_findings_dispatch_new_vs_legacy():
    """分发层：old=None 走新增红线；old 存在走 ratchet（不得直接按红线卡）。"""
    fs = rg._func_findings(_func(lines=70), None, "x.py")
    assert any(f.rule == "R1" and f.level == "ERROR" for f in fs)
    fs = rg._func_findings(_func(lines=750, cc=100), (748, 100), "x.py")
    assert any(f.rule == "R1" and f.level == "WARN" for f in fs)
    assert not any(f.level == "ERROR" for f in fs)
