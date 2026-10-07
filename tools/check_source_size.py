# -*- coding: utf-8 -*-
"""Enforce the repository's source-file size ceiling.

The architecture policy caps source files at 1,000 lines.  Keeping this as a
small, dependency-free check makes the exception visible in CI instead of
letting the agent loop grow silently.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable


DEFAULT_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".rs", ".go", ".cpp", ".c", ".h"}
DEFAULT_EXCLUDES = {".git", "build", "dist", ".cargo_target", ".pytest_cache", ".pytest_tmp"}


def iter_sources(root: Path, extensions: Iterable[str] = DEFAULT_EXTENSIONS):
    extensions = set(extensions)
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in extensions:
            continue
        if any(part in DEFAULT_EXCLUDES for part in path.parts):
            continue
        yield path


def check_source_sizes(root: Path, ceiling: int = 1000) -> list[tuple[Path, int]]:
    violations = []
    for path in iter_sources(root):
        try:
            lines = len(path.read_text(encoding="utf-8").splitlines())
        except (OSError, UnicodeDecodeError):
            continue
        if lines > ceiling:
            violations.append((path, lines))
    return sorted(violations, key=lambda item: item[1], reverse=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check source-file line ceiling")
    parser.add_argument("root", nargs="?", default=".", type=Path)
    parser.add_argument("--ceiling", type=int, default=1000)
    parser.add_argument("--report-only", action="store_true",
                        help="报告超限文件但不返回失败")
    args = parser.parse_args()
    violations = check_source_sizes(args.root.resolve(), args.ceiling)
    if violations:
        print(f"[FAIL] {len(violations)} source file(s) exceed {args.ceiling} lines:")
        for path, lines in violations:
            print(f"  {lines:5d} {path}")
        return 0 if args.report_only else 1
    print(f"[OK] source files are within the {args.ceiling}-line ceiling")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
