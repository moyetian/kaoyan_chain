#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
review_gate.py —— 代码审查门禁（零依赖，仅用标准库 AST）

定位：与既有 tools/lint_check.py 互补（语法级正确性 vs 可维护性 + 资产卫生）。

红线（作用于**新增文件 / 新增函数**，违反即 ERROR）：
  R1 单函数行数 <= 60            R2 单函数圈复杂度 <= 10
  R3 单文件行数 <= 500           R4 新增测试须入 tests/<层>/，禁日期戳/批次号命名
  R5 公共函数（>30 行）须有 docstring    R6 新增函数参数注解齐全（WARN）

存量保护线（ratchet，作用于**被修改的存量文件 / 函数**：只降不升）：
  L1 单函数行数 <= 150    L2 单函数 CC <= 25    L3 单文件行数 <= 1500

双轨判定（[缺陷修复] 旧实现把"被触碰的存量巨函数"直接按新增红线判 ERROR，
实测对 run_doctor()/search() 等存量函数改一行即误报）：新增（基线中不存在）
→ 直接卡 ERROR；存量越过新增上限 → ERROR；存量原本已越线又增大 → WARN；
存量缩小 / 持平 → 不报（存量债不追溯）。

用法：
  python tools/review_gate.py --scan         # 全量体检，打印报告（只报不卡）
  python tools/review_gate.py --diff HEAD~1  # 只卡本次变更（本地自查 / CI）

基线语义注意（本仓库特有）：主仓库（开发工作区）的 origin 指向**脱敏发布
副本**（独立提交历史），直接 ``--diff origin/main`` 会把"副本排除的文件"误判
为新增。主仓库自查请用 ``--diff HEAD~1`` / ``--diff <tag>`` 等本仓库内基线；
公开副本仓库与 CI（全新检出）中 origin/main 语义正常，可直接使用。
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path


try:  # 包式导入（pytest 与其它工具按 ``from tools import review_gate`` 引用）
    from tools.review_gate_git import (
        _git_path,
        added_files,
        changed_lines,
        old_file_source,
        ref_exists,
        renamed_old_paths,
    )
except ImportError:  # 脚本式直跑（``python tools/review_gate.py``）：sys.path[0] 为 tools/
    from review_gate_git import (
        _git_path,
        added_files,
        changed_lines,
        old_file_source,
        ref_exists,
        renamed_old_paths,
    )


# ---------------------------------------------------------------- 阈值配置

NEW_MAX_FUNC_LINES = 60
NEW_MAX_FUNC_CC = 10
NEW_MAX_FILE_LINES = 500

LEGACY_MAX_FUNC_LINES = 150
LEGACY_MAX_FUNC_CC = 25
LEGACY_MAX_FILE_LINES = 1500

DOC_MIN_LINES = 30

#: R4 禁止新增的测试命名：日期戳型 ``test_fix_YYYYMMDD_*`` 与批次号型
#: ``test_w11_*`` / ``test_r3_*`` / ``test_ut4_*`` 等（见 CODE_REVIEW.md §6.2）。
#: 批次前缀后要求接数字，避免误伤 ``test_audit_log_*`` 这类正常命名。
#: [缺陷修复] 旧实现用裸前缀 "test_ut"，实测把合规命名 test_utility_*.py /
#: test_utf8_*.py 误判为批次号。
BAD_TEST_NAME_RE = re.compile(
    r"^test_(?:fix_\d{8}|audit_\d|challenger_\d|batch_\d|[wrcp]\d+_|ut\d+_)"
)


# ---------------------------------------------------------------- AST 工具

def _complexity(node: ast.AST) -> int:
    """圈复杂度（McCabe 近似）：决策点 + 1。"""
    cc = 1
    for n in ast.walk(node):
        if isinstance(n, (ast.If, ast.For, ast.AsyncFor, ast.While,
                          ast.ExceptHandler, ast.With, ast.AsyncWith,
                          ast.Assert, ast.IfExp)):
            cc += 1
        elif isinstance(n, ast.BoolOp):
            cc += len(n.values) - 1
        elif isinstance(n, ast.comprehension):
            cc += 1 + len(n.ifs)
    return cc


def _func_length(node: ast.AST) -> int:
    end = getattr(node, "end_lineno", None) or node.lineno
    return end - node.lineno + 1


def _missing_annotations(node: ast.AST) -> int:
    """缺类型注解的参数个数（R6）。

    [L2 修复] 须覆盖全部参数形态：旧实现只数 ``args.args`` + ``kwonlyargs``，
    仅位置参数（``posonlyargs``，``/`` 之前）与 ``*args`` / ``**kwargs``
    三类被漏计（R6 WARN 静默漏报）。
    """
    a = node.args  # type: ignore[attr-defined]
    args = list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs)
    if args and args[0].arg in ("self", "cls"):
        args = args[1:]
    missing = sum(1 for x in args if x.annotation is None)
    for extra in (a.vararg, a.kwarg):
        if extra is not None and extra.annotation is None:
            missing += 1
    return missing


def iter_py_files(roots: list):
    for root in roots:
        root_path = Path(root)
        if root_path.is_file() and root_path.suffix == ".py":
            yield root_path
            continue
        if not root_path.exists():
            continue
        for p in sorted(root_path.rglob("*.py")):
            if ".git" in p.parts or "__pycache__" in p.parts:
                continue
            yield p


class FileReport:
    def __init__(self, path: str):
        self.path, self.lines, self.parse_error = path, 0, None
        self.funcs: list[dict] = []


def analyze_file(path: Path) -> FileReport:
    rep = FileReport(str(path))
    try:
        src = path.read_text(encoding="utf-8")
    except Exception as e:
        rep.parse_error = f"读取失败: {e}"
        return rep
    rep.lines = len(src.splitlines())
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        rep.parse_error = f"语法错误: {e.msg} (line {e.lineno})"
        return rep
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            rep.funcs.append({
                "name": node.name, "lineno": node.lineno,
                "lines": _func_length(node), "cc": _complexity(node),
                "public": not node.name.startswith("_"),
                "has_doc": ast.get_docstring(node) is not None,
                "missing_ann": _missing_annotations(node),
            })
    return rep


def _func_metrics_from_source(src: str) -> dict[str, list[tuple[int, int]]]:
    """解析源码文本 → {函数名: [(行数, CC), ...]}（同名的按出现顺序排列）。"""
    out: dict[str, list[tuple[int, int]]] = {}
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    found: list[tuple[int, str, int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append((node.lineno, node.name,
                          _func_length(node), _complexity(node)))
    for _lineno, name, lines, cc in sorted(found):
        out.setdefault(name, []).append((lines, cc))
    return out


# ---------------------------------------------------------------- 检查器

class Finding:
    __slots__ = ("level", "lineno", "msg", "path", "rule")

    def __init__(self, rule: str, level: str, path: str, lineno: int, msg: str) -> None:
        self.rule, self.level, self.path, self.lineno, self.msg = \
            rule, level, path, lineno, msg

    def __str__(self):
        return f"[{self.level}] {self.rule} {self.path}:{self.lineno} — {self.msg}"


def _file_size_finding(rep: FileReport, old_lines: int | None,
                       is_new: bool) -> list[Finding]:
    """R3 文件规模（双轨判定，规则见模块 docstring）。"""
    if rep.lines <= NEW_MAX_FILE_LINES:
        return []
    if is_new or old_lines is None:
        return [Finding("R3", "ERROR", rep.path, 1,
                        f"新增文件 {rep.lines} 行 > {NEW_MAX_FILE_LINES}（新增代码上限）")]
    if old_lines <= NEW_MAX_FILE_LINES:
        return [Finding("R3", "ERROR", rep.path, 1,
                        f"文件由 {old_lines} → {rep.lines} 行，越过 {NEW_MAX_FILE_LINES} 上限")]
    if rep.lines > old_lines:
        return [Finding("R3", "WARN", rep.path, 1,
                        f"文件原本 {old_lines} 行已超上限，本次又增至 {rep.lines} 行"
                        f"（存量文件应只减不增）")]
    return []


def _new_func_findings(f: dict, path: str) -> list[Finding]:
    """新增函数（基线中不存在）：直接卡新增红线。"""
    fs: list[Finding] = []
    if f["lines"] > NEW_MAX_FUNC_LINES:
        fs.append(Finding("R1", "ERROR", path, f["lineno"],
                          f"{f['name']}() 长 {f['lines']} 行 > {NEW_MAX_FUNC_LINES}"))
    if f["cc"] > NEW_MAX_FUNC_CC:
        fs.append(Finding("R2", "ERROR", path, f["lineno"],
                          f"{f['name']}() 圈复杂度 {f['cc']} > {NEW_MAX_FUNC_CC}"))
    if f["public"] and f["lines"] > DOC_MIN_LINES and not f["has_doc"]:
        fs.append(Finding("R5", "ERROR", path, f["lineno"],
                          f"{f['name']}() 公共函数 {f['lines']} 行缺 docstring"))
    if f["missing_ann"] > 0:
        fs.append(Finding("R6", "WARN", path, f["lineno"],
                          f"{f['name']}() 有 {f['missing_ann']} 个参数缺类型注解"))
    return fs


def _legacy_func_findings(f: dict, old: tuple[int, int],
                          path: str) -> list[Finding]:
    """存量函数：只卡"变差"（越过新增上限 → ERROR；已越线又增大 → WARN）。"""
    old_lines, old_cc = old
    fs: list[Finding] = []
    if f["lines"] > NEW_MAX_FUNC_LINES:
        if old_lines <= NEW_MAX_FUNC_LINES:
            fs.append(Finding("R1", "ERROR", path, f["lineno"],
                              f"{f['name']}() 由 {old_lines} → {f['lines']} 行，"
                              f"越过 {NEW_MAX_FUNC_LINES} 上限"))
        elif f["lines"] > old_lines:
            fs.append(Finding("R1", "WARN", path, f["lineno"],
                              f"{f['name']}() 原本 {old_lines} 行已超上限，"
                              f"本次又增至 {f['lines']} 行"))
    if f["cc"] > NEW_MAX_FUNC_CC:
        if old_cc <= NEW_MAX_FUNC_CC:
            fs.append(Finding("R2", "ERROR", path, f["lineno"],
                              f"{f['name']}() 由 CC {old_cc} → {f['cc']}，"
                              f"越过 {NEW_MAX_FUNC_CC} 上限"))
        elif f["cc"] > old_cc:
            fs.append(Finding("R2", "WARN", path, f["lineno"],
                              f"{f['name']}() 原本 CC {old_cc} 已超上限，"
                              f"本次又增至 {f['cc']}"))
    return fs


def _func_findings(f: dict, old: tuple[int, int] | None,
                   path: str) -> list[Finding]:
    """单函数判定分发：新增 → 红线；存量 → ratchet。"""
    if old is None:
        return _new_func_findings(f, path)
    return _legacy_func_findings(f, old, path)


def _pair_old_funcs(funcs: list[dict],
                    old_funcs: dict[str, list[tuple[int, int]]]
                    ) -> dict[int, tuple[int, int]]:
    """被检查函数与基线同名函数按出现顺序配对 → {id(f): (旧行数, 旧CC)}。

    配不上的（基线中不存在）视为新增函数。同名多定义（如多个类的
    ``__init__``）按出现顺序一一对应，顺序大幅重排时配对可能错位 —— 属已知
    近似，宁可漏报也不误报。用 ``id(f)`` 作键（dict 不可哈希）。
    """
    by_name: dict[str, list[dict]] = {}
    for f in sorted(funcs, key=lambda x: x["lineno"]):
        by_name.setdefault(f["name"], []).append(f)
    pairs: dict[int, tuple[int, int]] = {}
    for name, flist in by_name.items():
        for f, old in zip(flist, old_funcs.get(name, []), strict=False):
            pairs[id(f)] = old
    return pairs


def check_file_new_code(rep: FileReport, touched: set[int], base_ref: str = "",
                        is_new: bool = False,
                        old_path: str | None = None) -> list[Finding]:
    """新增/修改代码的检查入口（R3 + 函数级 R1/R2/R5/R6）。

    与基线对比取「旧文件源码」一次（``git show``）：旧文件不存在 → 全文件按
    新增红线卡；旧函数不存在 → 该函数按新增红线卡；存在 → ratchet（只卡变差）。

    ``old_path``：重命名场景下该文件在基线中的旧路径（``git mv`` + 修改的
    文件不在 ``--diff-filter=A``，按新路径读基线必然失败 → 会误判为新增；
    由 :func:`review_gate_git.renamed_old_paths` 提供映射）。
    """
    src_path = old_path or rep.path
    old_src = (None if (is_new or not base_ref)
               else old_file_source(src_path, base_ref))
    old_lines = len(old_src.splitlines()) if old_src is not None else None
    old_funcs = _func_metrics_from_source(old_src) if old_src is not None else {}

    fs = _file_size_finding(rep, old_lines, is_new)
    touched_funcs = [
        f for f in rep.funcs
        if not touched or set(range(f["lineno"], f["lineno"] + f["lines"])) & touched
    ]
    pairs = _pair_old_funcs(touched_funcs, old_funcs)
    for f in touched_funcs:
        fs += _func_findings(f, pairs.get(id(f)), rep.path)
    return fs


def check_test_naming(path: str, is_new: bool) -> list[Finding]:
    """R4：测试资产命名规范。

    [M1 修复] 是否位于 tests/ 一律按**路径段**判断：旧实现用子串
    （"mytests/"、"unit_tests/" 均命中 "tests/"）却在取值时
    ``parts.index("tests")`` —— 段不存在时抛 ValueError，使 --diff 对单个
    文件整体崩溃。修复后无独立 tests 段的路径按 tests/ 之外处理（迁移 WARN）。
    """
    fs: list[Finding] = []
    norm = _git_path(path)
    name = Path(norm).name
    if not name.startswith("test_"):
        return fs
    parts = norm.split("/")
    if "tests" not in parts:  # 位于 tests/ 之外（如 tools/test_*.py）：提示迁移
        fs.append(Finding("R4", "WARN", path, 1,
                          "测试文件位于 tests/ 之外，建议迁移至 tests/ 对应分层目录"))
        return fs
    idx = parts.index("tests")
    flat = parts[idx + 1] if len(parts) > idx + 1 else ""
    if is_new and flat.endswith(".py"):  # 直接躺在 tests/ 根下
        fs.append(Finding("R4", "ERROR", path, 1,
                          "新增测试必须放入分层目录 "
                          "(unit/ integration/ e2e/ regression/ benchmarks/)"))
    if BAD_TEST_NAME_RE.match(name) and is_new:
        fs.append(Finding("R4", "ERROR", path, 1,
                          f"禁止新增日期戳/批次号式命名（{name}）；"
                          f"请用 test_<模块>_<行为>.py 并放入 regression/"))
    return fs


def scan_all(roots: list[str]) -> list[FileReport]:
    return [analyze_file(p) for p in iter_py_files(roots)]


def _count(rows: list[tuple], idx: int, threshold: int) -> int:
    return sum(1 for r in rows if r[idx] > threshold)


def _count_files(reports: list[FileReport], threshold: int) -> int:
    return sum(1 for r in reports if r.lines > threshold)


def print_report(reports: list[FileReport], top_n: int = 15) -> None:
    """全量体检报告（只报不卡）：CC TOP / 文件规模 TOP / 汇总。"""
    rows = sorted(
        ((f["cc"], f["lines"], r.path, f["name"], f["lineno"])
         for r in reports for f in r.funcs),
        reverse=True)
    print(f"\n=== 圈复杂度 TOP {top_n} ===")
    for cc, ln, path, name, lineno in rows[:top_n]:
        flag = "!!" if cc > LEGACY_MAX_FUNC_CC else ("! " if cc > NEW_MAX_FUNC_CC else "  ")
        print(f" {flag} CC={cc:<4} 行={ln:<5} {path}:{lineno} {name}()")
    print("\n=== 文件规模 TOP 10 ===")
    for r in sorted(reports, key=lambda x: -x.lines)[:10]:
        flag = "!!" if r.lines > LEGACY_MAX_FILE_LINES else ("! " if r.lines > NEW_MAX_FILE_LINES else "  ")
        print(f" {flag} {r.lines:<6} 行  {r.path}")
    print("\n=== 汇总 ===")
    print(f" 文件 {len(reports)}  函数 {len(rows)}")
    print(f" CC>{NEW_MAX_FUNC_CC}: {_count(rows, 0, NEW_MAX_FUNC_CC)}"
          f"   CC>{LEGACY_MAX_FUNC_CC}: {_count(rows, 0, LEGACY_MAX_FUNC_CC)}")
    print(f" 函数>{NEW_MAX_FUNC_LINES}行: {_count(rows, 1, NEW_MAX_FUNC_LINES)}"
          f"   函数>{LEGACY_MAX_FUNC_LINES}行: {_count(rows, 1, LEGACY_MAX_FUNC_LINES)}")
    print(f" 文件>{NEW_MAX_FILE_LINES}行: {_count_files(reports, NEW_MAX_FILE_LINES)}"
          f"   文件>{LEGACY_MAX_FILE_LINES}行: {_count_files(reports, LEGACY_MAX_FILE_LINES)}")


def _run_scan(args: argparse.Namespace) -> int:
    reports = scan_all(args.roots)
    bad = [r for r in reports if r.parse_error]
    print_report([r for r in reports if not r.parse_error])
    if bad:
        print(f"\n=== 无法解析的文件（{len(bad)}）===")
        for r in bad:
            print(f"  [ERROR] SYNTAX {r.path} — {r.parse_error}")
    return 0


def _collect_diff_findings(reports: list[FileReport], touched_map: dict,
                           new_files: set, rename_map: dict,
                           args: argparse.Namespace) -> list[Finding]:
    findings: list[Finding] = []
    for r in reports:
        key = _git_path(r.path)
        touched = touched_map.get(key, set())
        is_new = key in new_files
        if r.parse_error:
            if touched or is_new:
                findings.append(Finding("SYNTAX", "ERROR", r.path, 1, r.parse_error))
            continue
        if touched or is_new:
            findings += check_file_new_code(r, touched, args.diff, is_new=is_new,
                                            old_path=rename_map.get(key))
        if is_new:
            findings += check_test_naming(r.path, is_new=True)
    return findings


def _print_diff_result(findings: list[Finding], touched_map: dict,
                       new_files: set, base_ref: str) -> int:
    errors = [f for f in findings if f.level == "ERROR"]
    warns = [f for f in findings if f.level == "WARN"]
    print(f"\n=== 变更审查门禁（对比 {base_ref}）===")
    print(f" 变更文件 {len(touched_map)}  新增文件 {len(new_files)}")
    print(f" ERROR {len(errors)}   WARN {len(warns)}")
    for f in errors[:40]:
        print("  " + str(f))
    for f in warns[:20]:
        print("  " + str(f))
    if len(errors) > 40:
        print(f"  ... 另有 {len(errors) - 40} 条 ERROR")
    if errors:
        print("\n❌ 门禁未通过：上述红线项需在合并前处理，"
              "或按 CODE_REVIEW.md「例外申请」流程在 PR 中说明理由。")
        return 1
    print("\n✅ 门禁通过。")
    return 0


def _run_diff(args: argparse.Namespace) -> int:
    if not ref_exists(args.diff):
        print(f"❌ 基线引用 {args.diff} 无法解析（不是有效的提交/分支/标签）。")
        print("   主仓库提示：origin 指向脱敏发布副本（独立提交历史），")
        print("   请改用 --diff HEAD~1 / --diff <tag> 等本仓库内的基线引用。")
        return 2
    touched_map = changed_lines(args.diff)
    new_files = added_files(args.diff)
    rename_map = renamed_old_paths(args.diff)
    if not touched_map and not new_files:
        print(f"未检测到相对 {args.diff} 的 Python 变更，跳过。")
        return 0
    findings = _collect_diff_findings(scan_all(args.roots), touched_map, new_files,
                                      rename_map, args)
    return _print_diff_result(findings, touched_map, new_files, args.diff)


def main() -> int:
    ap = argparse.ArgumentParser(description="代码审查门禁（可维护性 + 资产卫生）")
    ap.add_argument("--scan", action="store_true", help="全量体检（只报不卡）")
    ap.add_argument("--diff", metavar="BASE_REF", help="只卡本次变更（CI 用）")
    ap.add_argument("--roots", nargs="*", default=["tools", "tests"], help="扫描根目录")
    args = ap.parse_args()

    if args.scan:
        return _run_scan(args)
    if args.diff:
        return _run_diff(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
