# -*- coding: utf-8 -*-
"""考研学习链 (Kaoyan AI Study Chain) · 判卷明细结构化留痕 (Grading Trace)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
开放题多模型判分（open_grader）此前只在打印文本里留有 rubric / 评审分 /
分歧度 / 仲裁结论 —— 人可读、机器不可读，评测与审计无法批量复核。本模块把
一次真实判分的结构化明细以 **append-only JSONL** 追加到::

    <workspace>/data/grading/traces/<YYYY-MM-DD>.jsonl

设计约束：
  1. **绝不中断判分**：任何落盘异常（目录不可建 / 磁盘满 / 权限失败）静默
     降级为返回 None，调用方（open_grader）另有一层 try/except 兜底；
  2. 只记录**真实判分路径**的明细（open_grader 中早退路径不调用本模块）；
  3. ``data/grading/`` 是**本机运行时产物**：含题面与学员作答原文，已进
     .gitignore 与发布排除清单（privacy_policy.NON_PUBLISH_PATH_PREFIXES），
     不随公开副本出门；
  4. workspace 根解析与项目惯例一致（``resolve_workspace_root``），并支持
     ``KY_WORKSPACE_ROOT`` 环境变量覆盖（测试注入 / 沙箱隔离）。

配置（ky_config.json 的 exam_grading 段）：
    trace_enabled         是否留痕（默认 true；环境变量 ``KY_GRADING_TRACE=0``
                          可强制关闭，供测试会话全局隔离）
    trace_include_answer  留痕是否包含学员作答原文（默认 true）
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

try:  # 双导入路径兼容（源码脚本式 / tools 包式）
    from workspace import resolve_workspace_root
except ImportError:  # pragma: no cover
    from tools.workspace import resolve_workspace_root

#: 留痕相对 workspace 根的目录（本机运行时产物，导出/入库均已排除）。
TRACES_SUBDIR = ("data", "grading", "traces")


def _workspace_root() -> Path:
    """解析 workspace 根：``KY_WORKSPACE_ROOT`` 环境变量优先（测试/沙箱注入），
    否则走项目既有 ``resolve_workspace_root``。调用时解析（非导入时），
    保证测试在 import 之后设置环境变量仍然生效。"""
    env = os.environ.get("KY_WORKSPACE_ROOT")
    if env:
        return Path(env)
    return resolve_workspace_root(__file__)


def trace_enabled(cfg: Optional[Dict[str, Any]] = None) -> bool:
    """是否留痕（``exam_grading.trace_enabled``，默认 True）。

    环境变量 ``KY_GRADING_TRACE=0``（或 false/off/no）**强制关闭**，优先级高于
    cfg —— 供测试会话全局隔离（tests/conftest.py 的 autouse fixture），避免
    真实判分路径的测试把 traces 写进开发者真实工作区（2026-10-06 实测：test_c4
    回放每次运行向真实工作区写约 119 条合成 trace）。
    """
    env = os.environ.get("KY_GRADING_TRACE")
    if env is not None and env.strip().lower() in ("0", "false", "off", "no"):
        return False
    return bool((cfg or {}).get("trace_enabled", True))


def trace_include_answer(cfg: Optional[Dict[str, Any]] = None) -> bool:
    """留痕是否包含学员作答原文（``exam_grading.trace_include_answer``，默认 True）。"""
    return bool((cfg or {}).get("trace_include_answer", True))


def record_grading_trace(payload: Dict[str, Any]) -> Optional[Path]:
    """追加一条判卷明细到 ``<workspace>/data/grading/traces/<日期>.jsonl``。

    返回写入的文件路径；**任何失败静默降级**返回 None（绝不抛异常、
    绝不中断判分）。行序列化失败时以 ``default=str`` 兜底，保证整条可写出。
    """
    try:
        day = datetime.now().strftime("%Y-%m-%d")
        path = _workspace_root().joinpath(*TRACES_SUBDIR, f"{day}.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False, default=str)
        # 用内置 open（而非 Path.open）：测试可 monkeypatch builtins.open 模拟
        # 磁盘满/权限失败；newline="\n" 保证跨平台写出稳定的 LF 字节。
        with open(str(path), "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
        return path
    except Exception:
        return None
