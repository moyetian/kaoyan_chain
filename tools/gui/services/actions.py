# -*- coding: utf-8 -*-
"""
GUI 后端动作服务（不依赖 Qt 控件）

把「调用后端模块并整理回显文本」从 MainWindow 抽出来：
  * 可在无图形环境下单测（CI 里不必起窗口）
  * 每个动作返回**可读文本**而不是直接往控件里写，
    MainWindow 只负责把文本贴到哪个面板

所有函数都只做「执行 + 返回文本」，异常一律转成可读文本，
不让 GUI 因后端异常弹异常栈。
"""

from __future__ import annotations

import contextlib
import io
import logging
from pathlib import Path
from typing import Optional, Tuple

_LOG = logging.getLogger(__name__)


def run_action_capture(alias: str, interactive: bool = False) -> str:
    """执行 TUI 中枢的某个动作并捕获其标准输出。

    GUI 复用 TUI 的动作分发（单实现），而不是另写一套后端调用 ——
    这也是「三端不一致」类问题的结构性解法。
    """
    try:
        try:
            from tui_navigator import execute_action
        except ImportError:  # pragma: no cover
            from tools.tui_navigator import execute_action  # type: ignore

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            execute_action(alias, interactive=interactive)
        return buf.getvalue().strip()
    except Exception as exc:
        _LOG.warning("动作执行失败: %s -> %s", alias, exc)
        return f"❌ 模块 [{alias}] 执行异常: {exc}"


def ingest_file(workspace_root: Path, path: str, subject: str = "pro") -> str:
    """把一份真题/讲义切片入库，返回可读结果文本。"""
    try:
        try:
            from skills import material_ingestion
        except ImportError:  # pragma: no cover
            from tools.skills import material_ingestion  # type: ignore

        pipe = material_ingestion.MaterialIngestionPipeline(workspace_root=workspace_root)
        res = pipe.ingest_file(Path(path), subject=subject)
        if res.get("success"):
            return (f"✔ 切片入库成功：识别 {res['count']} 道题目 "
                    f"(选择 {res['choices']} / 填空 {res['blanks']} / 大题 {res['essays']})\n"
                    f"   生成路径: {res['target_path']}")
        return f"❌ 切片入库失败: {res.get('msg')}"
    except Exception as exc:
        return f"❌ 切片入库异常: {exc}"


def diff_syllabus(workspace_root: Path, old_path: str, new_path: str) -> str:
    """比对两份考纲并落盘研报，返回可读结果文本。

    严禁在未提供新大纲时伪造变动 —— 本函数要求两个真实文件路径。
    """
    try:
        try:
            from intelligence.syllabus_diff import get_syllabus_diff_generator
            from intelligence.models import current_exam_year
        except ImportError:  # pragma: no cover
            from tools.intelligence.syllabus_diff import get_syllabus_diff_generator  # type: ignore
            from tools.intelligence.models import current_exam_year  # type: ignore

        y_new = current_exam_year()
        gen = get_syllabus_diff_generator()
        target_school, target_major = _target_labels(workspace_root, Path(new_path))
        rep = gen.compare_files(
            old_file=Path(old_path), new_file=Path(new_path),
            school=target_school, major=target_major,
            year_old=y_new - 1, year_new=y_new,
        )
        saved = gen.save_diff_report(rep)
        m = rep["metrics"]
        return (f"✔ 考纲 Diff 完成 (动荡率 {m['volatility_percentage']}% / "
                f"{m['stability_grade']})：新增 {m['added_count']} | "
                f"剔除 {m['removed_count']} | 调整 {m['modified_count']} | "
                f"不变 {m['unchanged_count']}\n   研报路径: {saved}")
    except Exception as exc:
        return f"❌ 考纲比对异常: {exc}"


def compare_schools(workspace_root: Path, school1: str, school2: str,
                    major: str = "") -> Tuple[str, str]:
    """双校对标，返回 ``(研报文本, 落盘路径或空串)``。"""
    try:
        try:
            from intelligence import get_school_comparator
        except ImportError:  # pragma: no cover
            from tools.intelligence import get_school_comparator  # type: ignore

        comp = get_school_comparator().compare(
            school1_query=school1, school2_query=school2,
            major_keyword=major, save_report=True,
        )
        return str(comp.get("terminal_report", "")), str(comp.get("saved_path") or "")
    except Exception as exc:
        return f"❌ 双校对标执行异常: {exc}", ""


def make_error_quiz(workspace_root: Path, subject: str = "pro",
                    count: int = 3) -> Tuple[str, str]:
    """生成错题盲盒自测卷，返回 ``(展示文本, 落盘路径或空串)``。"""
    try:
        try:
            from skills import exam_composer
        except ImportError:  # pragma: no cover
            from tools.skills import exam_composer  # type: ignore

        res = exam_composer.compose_exam_paper(
            subject=subject, count=count, include_weak=True, save_file=True)
        saved = str(res.get("saved_path", "") or "")
        paper_text = res.get("formatted_paper") or res.get("content") or ""
        display = f"\n\n🎯 【错题盲盒自测卷】已生成！\n{'=' * 50}\n{paper_text}\n"
        if saved:
            display += f"\n> 💾 自测卷已落盘: `{saved}`"
        return display, saved
    except Exception as exc:
        return f"❌ 组卷异常: {exc}", ""


def _target_labels(workspace_root: Path, new_path: Path) -> Tuple[str, str]:
    """从配置取目标院校/专业（失败则用占位符与文件名）。"""
    try:
        try:
            from state import load_config
        except ImportError:  # pragma: no cover
            from tools.state import load_config  # type: ignore

        cfg = load_config(workspace_root)
        sp = cfg.get("study_plan", {}) if isinstance(cfg, dict) else {}
        school = sp.get("school") or cfg.get("target_school") or "目标院校"
        major = sp.get("major") or cfg.get("target_major") or new_path.stem
        return str(school), str(major)
    except Exception:                       # pragma: no cover
        return "目标院校", new_path.stem


__all__ = [
    "compare_schools",
    "diff_syllabus",
    "ingest_file",
    "make_error_quiz",
    "run_action_capture",
]
