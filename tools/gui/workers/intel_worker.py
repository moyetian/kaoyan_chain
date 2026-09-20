# -*- coding: utf-8 -*-
"""
研招情报与后台耗时动作异步 Worker (防止 GUI 主线程卡死)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict
from PySide6.QtCore import QThread, Signal

ROOT = Path(__file__).resolve().parent.parent.parent.parent
TOOLS = ROOT / "tools"
for p in (str(ROOT), str(TOOLS)):
    if p not in sys.path:
        sys.path.insert(0, p)


class IntelTaskWorker(QThread):
    """异步执行研招情报耗时任务（如单校侦察、双校对标、简章监控）的后台工作线程。"""

    log_signal = Signal(str)
    finished_signal = Signal(str, str)  # (output_text, saved_path)
    error_signal = Signal(str)

    def __init__(self, task_type: str, workspace_root: Path, params: Dict[str, Any]):
        super().__init__()
        self.task_type = task_type
        self.workspace_root = Path(workspace_root)
        self.params = params or {}
        self._is_cancelled = False

    def cancel(self):
        """取消任务"""
        self._is_cancelled = True

    def run(self):
        if self._is_cancelled:
            return

        try:
            try:
                from tools.gui import services
            except ImportError:
                from gui import services

            if self.task_type == "compare":
                s1 = self.params.get("school1", "目标院校")
                s2 = self.params.get("school2", "")
                major = self.params.get("major", "")
                self.log_signal.emit(
                    f"▶ 正在后台联网对标【{s1}】与【{s2}】(专业: {major or '全部'})...\n"
                    f"  [1/3] 查询教育部研招网官方院校代码与办学层次...\n"
                    f"  [2/3] 全网检索历年复试线、初试自命题科目与拟招人数...\n"
                    f"  [3/3] 深度比对一志愿保护机制、调剂政策与复试风评...\n"
                    f"  （分析可能需要 20~40 秒，界面保持流畅，请稍候）\n"
                )
                report, saved = services.compare_schools(self.workspace_root, s1, s2, major)
                if not self._is_cancelled:
                    self.finished_signal.emit(report, saved)

            elif self.task_type == "action":
                alias = self.params.get("alias", "")
                alias_names = {
                    "scout": "目标院校深度侦察",
                    "watch": "动态简章监控巡检",
                    "diff": "考纲智能 Diff",
                    "ingest": "题库切片入库",
                    "admission": "研招网招考事实核验",
                }
                name = alias_names.get(alias, alias)
                self.log_signal.emit(
                    f"▶ 正在后台启动【{name}】模块...\n"
                    f"  （多源官方数据联网检索与深度推理中，界面随时可操作交互）\n"
                )
                out = services.run_action_capture(alias, extra=self.params)
                if not self._is_cancelled:
                    self.finished_signal.emit(out, "")

            elif self.task_type == "ingest_file":
                path = self.params.get("path", "")
                subject = self.params.get("subject", "pro")
                self.log_signal.emit(f"▶ 正在后台切片入库 [{path}]...\n")
                out = services.ingest_file(self.workspace_root, path, subject)
                if not self._is_cancelled:
                    self.finished_signal.emit(out, "")

            elif self.task_type == "diff_syllabus":
                old_path = self.params.get("old_path", "")
                new_path = self.params.get("new_path", "")
                self.log_signal.emit(
                    f"▶ 正在后台比对考纲：\n   基准: {old_path}\n   最新: {new_path}\n"
                    f"  （AST 级逐考点比对，界面保持流畅）\n"
                )
                out = services.diff_syllabus(self.workspace_root, old_path, new_path)
                if not self._is_cancelled:
                    self.finished_signal.emit(out, "")

            elif self.task_type == "error_quiz":
                subject = self.params.get("subject", "pro")
                count = self.params.get("count", 3)
                self.log_signal.emit("▶ 正在后台生成错题盲盒自测卷...\n")
                display, saved = services.make_error_quiz(self.workspace_root, subject, count)
                if not self._is_cancelled:
                    self.finished_signal.emit(display, saved)

            else:
                self.error_signal.emit(f"未知的后台任务类型: {self.task_type}")

        except Exception as exc:
            if not self._is_cancelled:
                self.error_signal.emit(str(exc))
