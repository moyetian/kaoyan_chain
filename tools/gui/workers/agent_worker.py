# -*- coding: utf-8 -*-
"""
Agent Loop 异步执行 Worker，防止 GUI 主线程阻塞
"""

import sys
from pathlib import Path
from PySide6.QtCore import QThread, Signal

ROOT = Path(__file__).resolve().parent.parent.parent.parent
TOOLS = ROOT / "tools"
for p in (str(ROOT), str(TOOLS)):
    if p not in sys.path:
        sys.path.insert(0, p)


class AgentWorker(QThread):
    finished_signal = Signal(str)

    def __init__(self, config: dict, user_input: str, timeout: float = 60.0):
        super().__init__()
        self.config = config
        self.user_input = user_input
        self.timeout = timeout
        self._is_cancelled = False

    def cancel(self):
        """中止任务"""
        self._is_cancelled = True

    def run(self):
        if self._is_cancelled:
            self.finished_signal.emit("[已取消]: 用户主动取消了任务。")
            return

        # [P0 修复·本地降级] 「XX报到」等纯本地口令不依赖 LLM，直接生成播报文本。
        # 此前一律走 AgentRunner(LLM)，上游 503/限流时 GUI 报到完全不可用，
        # 而同一口令在 CLI 走本地播报正常——现两端共用 build_subject_checkin_brief。
        text = (self.user_input or "").strip()
        _checkin_map = {
            "数学报到": "math", "学数学": "math", "切换数学": "math",
            "英语报到": "eng", "学英语": "eng", "切换英语": "eng",
            "政治报到": "pol", "学政治": "pol", "切换政治": "pol",
            "专业课报到": "pro", "学专业课": "pro", "切换专业课": "pro",
        }
        if text in _checkin_map:
            try:
                from ky_cli import build_subject_checkin_brief, load_config, save_config
                cfg = dict(self.config) if self.config else load_config()
                subj = _checkin_map[text]
                cfg["active_subject"] = subj
                save_config(cfg)
                self.finished_signal.emit(build_subject_checkin_brief(cfg, subj))
            except Exception as e:
                self.finished_signal.emit(f"[报到播报异常]: {e}")
            return

        try:
            from agent import AgentRunner
            runner = AgentRunner(
                config=self.config,
                workspace_root=ROOT,
                permission_mode="acceptEdits",
                max_steps=8,
            )
            reply = runner.run(self.user_input, interactive=False)
            if self._is_cancelled:
                self.finished_signal.emit("[已取消]: 任务已中止。")
            elif not reply or not str(reply).strip():
                self.finished_signal.emit(
                    "\n[Agent 未返回有效回复] 私教服务可能暂时不可用（如上游 LLM 返回 503 / 限流），"
                    "请稍后重试。如果多次失败，可在 CLI 执行 `ky doctor` 检查 API 连通性。"
                )
            else:
                self.finished_signal.emit(reply)
        except Exception as e:
            self.finished_signal.emit(f"[Agent 执行异常]: {e}")
