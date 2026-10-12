#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
review_gate_git.py —— review_gate 的 git 交互层（零依赖，仅标准库 + git CLI）

职责边界：本模块只做「执行 git 命令 + 解析输出」，不做任何规则判定。
单独成文件的理由：
  1. tools/review_gate.py 自身受 R3（单文件 <= 500 行）门禁约束，git 层
     移出以保持其自合规（拆分时 review_gate.py 已接近上限）；
  2. 规则判定与 git 解析的测试面不同（前者纯函数、后者需临时仓库），
     分开后各自的测试组织更清晰。

导入兼容：本模块被 review_gate.py 以两种方式导入 ——
  包式 ``from tools.review_gate_git import ...``（pytest / 其它工具引用）；
  脚本式 ``from review_gate_git import ...``（``python tools/review_gate.py``
  直跑时 sys.path[0] 为 tools/ 目录）。两种路径指向同一份源码文件，
  模块内无状态，重复加载无害。
"""

from __future__ import annotations

import subprocess


def _git_path(path: str) -> str:
    """规范化为 git 认识的仓库相对路径（正斜杠）。

    [缺陷修复] Windows 下 ``str(Path)`` 是反斜杠形式，直接拼进
    ``git show <base>:<path>`` 会因"路径不存在"失败，旧实现据此把**所有**
    >500 行的存量文件误判为"由 None 行越过上限"的 ERROR（100% 复现误报）。
    """
    return str(path).replace("\\", "/")


def _run_git(args: list[str]) -> subprocess.CompletedProcess | None:
    """执行 git 命令；异常时返回 None（调用方按失败处理）。

    [L3 修复] 显式 ``encoding="utf-8"`` + ``errors="replace"``：``text=True``
    在 Windows 上按 ANSI 代码页（cp936）解码，git 的 UTF-8 输出遇到非 GBK
    字节序列会抛 UnicodeDecodeError 并被本函数的 except 吞掉 → 静默降级为
    "无变更"（fail-open）。``errors="replace"`` 保证任何字节流都可解析。
    """
    try:
        return subprocess.run(args, capture_output=True, text=True, check=False,
                              encoding="utf-8", errors="replace")
    except Exception:
        return None


def _run_git_out(args: list[str]) -> str:
    r = _run_git(args)
    return r.stdout if r is not None else ""


def ref_exists(base_ref: str) -> bool:
    """base_ref 是否为可解析的提交。

    [缺陷修复] 旧实现对无效基线静默返回空 diff，或（配合旧文件读取失败）
    把全仓文件误判为新增。预检失败时给出明确提示而不是继续跑。
    """
    r = _run_git(["git", "rev-parse", "--verify", "--quiet", f"{base_ref}^{{commit}}"])
    return r is not None and r.returncode == 0


def _parse_hunk_range(line: str) -> tuple[int, int] | None:
    """解析 ``@@ -a,b +c,d @@`` 的 c/d（新增侧行号与行数）。"""
    parts = line.split("+", 1)
    if len(parts) < 2:
        return None
    start, _, count = parts[1].split("@@")[0].strip().partition(",")
    try:
        return int(start), int(count or "1")
    except ValueError:
        return None


def changed_lines(base_ref: str) -> dict[str, set[int]]:
    """返回 {文件路径: 本次新增/修改的行号集合}。"""
    out = _run_git_out(["git", "diff", "-U0", "--no-color", base_ref, "--", "*.py"])
    result: dict[str, set[int]] = {}
    current: str | None = None
    for line in out.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:].strip()
            result.setdefault(current, set())
        elif line.startswith("@@") and current is not None:
            rng = _parse_hunk_range(line)
            if rng:
                result[current].update(range(rng[0], rng[0] + max(rng[1], 1)))
    return result


def added_files(base_ref: str) -> set[str]:
    out = _run_git_out(["git", "diff", "--name-only", "--diff-filter=A", base_ref])
    return {l.strip() for l in out.splitlines() if l.strip().endswith(".py")}


def renamed_old_paths(base_ref: str) -> dict[str, str]:
    """{新路径: 旧路径} —— 本次变更中发生重命名的 .py 文件。

    [M2 修复] ``git diff`` 默认开启 rename 检测：``git mv`` + 修改的文件
    不出现在 ``--diff-filter=A``（is_new=False），而 ``git show <base>:<新路径>``
    必然失败（基线中该文件叫旧路径）→ old_lines=None → 被误判为"新增文件"
    按红线报 ERROR（实测：重命名 + 改一行触发 R3/R1 双 ERROR 误报）。
    解析 ``--name-status`` 的 ``R<score>`` 行（制表符分隔：状态/旧路径/新路径）
    建立映射，基线源码按旧路径读取。
    """
    out = _run_git_out(["git", "diff", "-M", "--name-status", base_ref, "--", "*.py"])
    result: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].startswith("R"):
            result[parts[2].strip()] = parts[1].strip()
    return result


def old_file_source(path: str, base_ref: str) -> str | None:
    """基线中该文件的源码文本；文件不在基线中（= 新增文件）→ None。"""
    r = _run_git(["git", "show", f"{base_ref}:{_git_path(path)}"])
    if r is None or r.returncode != 0:
        return None
    return r.stdout
