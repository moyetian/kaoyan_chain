# -*- coding: utf-8 -*-
"""
考研学习链 · 零依赖静态检查 (Zero-dependency Static Lint)

本环境不保证安装 ruff/flake8 等第三方检查器（CI 也只装 requirements），
故提供一个**仅用标准库**的静态检查器，覆盖本项目真实踩过的坑：

  1. 语法错误                     —— ast.parse 失败
  2. 裸 ``except:``                —— 会吞掉 KeyboardInterrupt/SystemExit
  3. ``except X: pass``            —— 静默吞异常，出问题无迹可循（仅统计与提醒）
  4. 可变默认参数                  —— def f(x=[]) 的经典陷阱
  5. 未使用导入                    —— 含 __all__ 与字符串注解的排除
  6. **导入时副作用**              —— 模块顶层直接执行 IO/子进程（本项目曾因此
                                     在 import 时改写仓库资源、跑 15 条 CLI）
  7. 非 UTF-8 编码文件

用法：``python tools/lint_check.py [--path tools] [--quiet]``
退出码：0 = 无问题；1 = 存在问题（供 CI 卡门禁）。
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent

#: 允许出现在模块顶层（非 def/class/import/常量赋值）的调用白名单。
#: 这些是"显式声明式"的初始化，不会产生文件/进程副作用。
ALLOWED_TOPLEVEL_CALLS = {
    "re.compile", "Path", "logging.getLogger", "getLogger",
    "argparse.ArgumentParser", "dataclass", "lru_cache", "field",
    "set_read_only_mode", "NamedTuple", "Enum", "auto",
}

#: 顶层副作用豁免文件（纯声明式：全部是常量/模板字符串）
SIDE_EFFECT_EXEMPT: set = set()


def iter_py_files(base: Path) -> List[Path]:
    return sorted(
        p for p in base.rglob("*.py")
        if "__pycache__" not in p.parts and not p.name.startswith(".")
    )


def _dotted(node: ast.AST) -> str:
    """把 ast 节点还原成点号形式的名字（如 re.compile）。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _call_name(node: ast.Call) -> str:
    return _dotted(node.func)


def _exported(tree: ast.Module) -> set:
    out = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "__all__":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        out |= {e.value for e in node.value.elts if isinstance(e, ast.Constant)}
    return out


def check_file(path: Path, base: Path) -> List[Tuple[str, int, str]]:
    """返回 [(级别, 行号, 说明)]；级别 ∈ {ERROR, WARN}。"""
    issues: List[Tuple[str, int, str]] = []
    rel = path.relative_to(base)

    try:
        raw = path.read_bytes()
    except OSError as e:
        return [("ERROR", 0, f"无法读取: {e}")]
    try:
        src = raw.decode("utf-8")
    except UnicodeDecodeError:
        issues.append(("ERROR", 0, "文件不是合法 UTF-8 编码"))
        src = raw.decode("utf-8", "replace")

    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        issues.append(("ERROR", e.lineno or 0, f"语法错误: {e.msg}"))
        return issues

    # 2/3. except 相关
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                issues.append(("ERROR", node.lineno, "裸 except: 会吞掉 KeyboardInterrupt/SystemExit"))
            elif node.body and all(isinstance(s, ast.Pass) for s in node.body):
                issues.append(("WARN", node.lineno, f"except {ast.unparse(node.type)}: pass —— 静默吞异常（仅提醒，不卡门禁）"))

    # 4. 可变默认参数
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in list(node.args.defaults) + [x for x in node.args.kw_defaults if x]:
                if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                    issues.append(("ERROR", node.lineno, f"可变默认参数: def {node.name}(...)"))

    # 5. 未使用导入（跳过 __init__.py 与 __all__ 导出名）
    if path.name != "__init__.py":
        exported = _exported(tree)
        body_src = "\n".join(
            "" if re.match(r"^\s*(import|from)\s+", line) else line
            for line in src.splitlines()
        )
        for node in tree.body:
            names = []
            if isinstance(node, ast.Import):
                names = [(a.asname or a.name.split(".")[0], node.lineno) for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.module == "__future__":
                    continue
                names = [(a.asname or a.name, node.lineno) for a in node.names if a.name != "*"]
            for name, lineno in names:
                if name in exported:
                    continue
                if not re.search(rf"\b{re.escape(name)}\b", body_src):
                    issues.append(("WARN", lineno, f"未使用导入: {name}"))

    # 6. 导入时副作用：模块顶层直接执行 IO / 子进程
    #    判定范围有意收窄，避免噪音：只看顶层的 Try 块与顶层裸调用表达式，
    #    且只在这些语句里出现"写文件 / 建目录 / 起进程"类调用时报错。
    if path.name not in SIDE_EFFECT_EXEMPT:
        for node in tree.body:
            if isinstance(node, ast.Try):
                lineno, name = _toplevel_io(node)
                if lineno:
                    issues.append(("ERROR", lineno, f"模块顶层执行 IO/子进程: {name}"))
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                name = _call_name(node.value)
                if name not in ALLOWED_TOPLEVEL_CALLS and _is_io_call(name):
                    issues.append(("ERROR", node.lineno, f"模块顶层执行 IO/子进程: {name}"))

    return issues


#: 判定为"有副作用"的调用后缀
_IO_SUFFIXES = ("write_text", "write_bytes", "mkdir", "makedirs", "copy2", "copy",
                "run", "Popen", "call", "system", "remove", "unlink", "rmtree", "rename")


def _is_io_call(name: str) -> bool:
    if not name:
        return False
    if name in ALLOWED_TOPLEVEL_CALLS:
        return False
    tail = name.split(".")[-1]
    if tail in ("read_text", "read_bytes", "exists", "is_file", "is_dir", "glob", "rglob"):
        return False        # 只读探测不算副作用
    return tail in _IO_SUFFIXES


def _toplevel_io(try_node: ast.Try) -> Tuple[int, str]:
    """检查顶层 try 块里是否直接执行了"写文件 / 建目录 / 起进程"类操作。

    只读探测（read_text / exists / glob 等）不算副作用，避免把环境探测误报。
    """
    for stmt in ast.walk(try_node):
        if isinstance(stmt, ast.Call):
            name = _call_name(stmt)
            if _is_io_call(name):
                return stmt.lineno, name
    return (0, "")


def main() -> int:
    parser = argparse.ArgumentParser(description="考研学习链 · 零依赖静态检查")
    parser.add_argument("--path", default="tools", help="待检查目录（默认 tools）")
    parser.add_argument("--quiet", action="store_true", help="仅输出汇总")
    args = parser.parse_args()

    base = (ROOT / args.path).resolve()
    if not base.exists():
        print(f"[!] 目录不存在: {base}")
        return 1

    files = iter_py_files(base)
    errors = warns = 0
    for path in files:
        issues = check_file(path, base)
        if not issues:
            continue
        rel = path.relative_to(base)
        shown = False
        for level, lineno, msg in issues:
            if level == "ERROR":
                errors += 1
            else:
                warns += 1
            if not args.quiet:
                if not shown:
                    print(f"\n{rel}")
                    shown = True
                mark = "❌" if level == "ERROR" else "⚠️ "
                print(f"  {mark} L{lineno}: {msg}")

    print(f"\n=== 静态检查汇总 ===")
    print(f"  扫描文件: {len(files)}")
    print(f"  错误: {errors}   警告: {warns}")
    if errors:
        print("  ❌ 存在阻塞级问题，门禁不通过")
        return 1
    print("  ✅ 无阻塞级问题")
    return 0


if __name__ == "__main__":
    sys.exit(main())
