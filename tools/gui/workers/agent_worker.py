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
    #: 流式片段（每小段一次，避免逐字符刷爆 GUI 主线程）
    chunk_signal = Signal(str)

    def __init__(self, config: dict, user_input: str, timeout: float = 60.0):
        super().__init__()
        self.config = config
        self.user_input = user_input
        self.timeout = timeout
        self._is_cancelled = False

    def cancel(self):
        """中止任务"""
        self._is_cancelled = True

    # 纯本地口令 → 本地处理器映射（不依赖 LLM，上游异常时仍可用）
    # 值可以是 ("fn", 可调用名) 或 ("action", tui别名)
    # [审查修正] 只保留「天然无歧义」的纯本地运维口令。
    # 「今日/打卡」这类既可能是本地查询、也可能是追问（如「今日数学怎么安排」）的口令
    # 不在此拦截，一律交 LLM，避免把用户的提问强行变成本地播报（行为回归）。
    _LOCAL_COMMAND_ALIASES = {
        "查漏": ("fn", "weakness"),
        "查漏补缺": ("fn", "weakness"),
        "薄弱点": ("fn", "weakness"),
        "扫描薄弱点": ("fn", "weakness"),
        "更新看板": ("action", "build"),
        "重编译看板": ("action", "build"),
        "刷新看板": ("action", "build"),
        "看板更新": ("action", "build"),
        # [P1 修复·三端一致] 「交作业」在 CLI 是纯本地三选一指引、秒回；
        # 此前 GUI 未收录，走了 LLM，上游波动时点击后 150s 才报「连接异常」。
        "交作业": ("fn", "homework"),
        "对答案": ("fn", "homework"),
    }

    def _safe_timeout_text(self) -> str:
        """把 self.timeout 安全格式化为显示用的整秒文本（None/非法值回落 60）。"""
        try:
            return str(int(float(self.timeout)))
        except (TypeError, ValueError):
            return "60"

    def _try_local_command(self, text: str):
        """尝试把口令交给本地执行器；命中返回结果文本，未命中返回 None。

        与 CLI/TUI 共用同一套本地实现（ky_cli.build_weakness_scan_report /
        tui_navigator.execute_action），保证「GUI 点什么、CLI 就有什么」的三端一致性。
        """
        spec = self._LOCAL_COMMAND_ALIASES.get(text)
        if not spec:
            return None
        kind, target = spec
        import contextlib, io
        try:
            if kind == "fn" and target == "weakness":
                # [P1 修复] 复用 CLI 的查漏实现，避免 GUI 走 LLM 而在上游异常时恒失败
                from ky_cli import build_weakness_scan_report
                return build_weakness_scan_report()
            if kind == "fn" and target == "homework":
                # [P1 修复] 复用 CLI 的交作业指引，与 CLI 同为纯本地秒回
                from ky_cli import build_homework_menu
                return build_homework_menu()
            if kind == "action":
                from tui_navigator import execute_action
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    execute_action(target, interactive=False)
                out = buf.getvalue().strip()
                return out or f"[本地执行完毕] {text}"
        except Exception as e:
            return f"[本地执行异常] {text}: {e}"
        return None

    def _emit_chunk(self, text: str) -> None:
        """把流式片段转发到 GUI 主线程（QThread 内直接 emit 信号是线程安全的）。"""
        if self._is_cancelled or not text:
            return
        try:
            self.chunk_signal.emit(str(text))
        except RuntimeError:
            # 底层 C++ 对象可能已被释放（窗口关闭），忽略即可
            return

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

        # [P1 修复·本地兜底] 「查漏」「更新看板」也是纯本地运算，此前与报到不同，
        # 未纳入本地分支，一律丢给 LLM → 上游异常时 GUI 点这两个药丸恒失败，
        # 而 CLI 同名命令（ky scan / tui build）本地即可跑通。现补齐本地兜底。
        local_reply = self._try_local_command(text)
        if local_reply is not None:
            self.finished_signal.emit(local_reply)
            return

        try:
            from agent import AgentRunner
            runner = AgentRunner(
                config=self.config,
                workspace_root=ROOT,
                permission_mode="acceptEdits",
                max_steps=8,
                # [S3 改善·流式输出] 此前只有一次性 finished_signal，学员盯着空白等几十秒；
                # 现每小段推送一次。quiet=True：不往 stdout 重复打字机输出。
                # [P2 修复·GUI 卡死] GUI 场景下上限收敛到 60s（此前沿用 120s 默认值，
                # 上游无响应时用户要盯着转圈两分钟）。CLI 仍走 120s。
                request_timeout=self.timeout,
                stream_callback=self._emit_chunk,
                quiet=True,
            )
            reply = runner.run(self.user_input, interactive=False)
            if self._is_cancelled:
                self.finished_signal.emit("[已取消]: 任务已中止。")
            elif not reply or not str(reply).strip():
                self.finished_signal.emit(
                    "\n[Agent 未返回有效回复] 私教服务可能暂时不可用（如上游 LLM 返回 503 / 限流 / 对话接口 502），"
                    "请稍后重试。如果多次失败，可在 CLI 执行 `ky doctor` 检查 API 连通性"
                    "（doctor 现会对对话接口做真实探活，而不仅看模型列表）。"
                )
            else:
                self.finished_signal.emit(reply)
        except Exception as e:
            # 超时会有明确提示，而不是静默转圈
            self.finished_signal.emit(
                f"[Agent 执行异常]: {e}\n"
                f"（若为超时，说明上游在 {self._safe_timeout_text()} 秒内无响应，"
                f"请稍后重试或检查网络/代理）"
            )
