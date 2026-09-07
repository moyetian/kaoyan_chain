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

    def __init__(self, config: dict, user_input: str):
        super().__init__()
        self.config = config
        self.user_input = user_input

    def run(self):
        try:
            from agent import AgentRunner
            runner = AgentRunner(
                config=self.config,
                workspace_root=ROOT,
                permission_mode="acceptEdits",
                max_steps=8,
            )
            reply = runner.run(self.user_input, interactive=False)
            self.finished_signal.emit(reply)
        except Exception as e:
            self.finished_signal.emit(f"[Agent 执行异常]: {e}")
