# -*- coding: utf-8 -*-
"""
考研学习链 (ky-cli) · GUI 审批通道（PySide6 弹窗）

[G13 / A3b 修复] 缺陷现场
--------------------------
桌面端 ``AgentWorker`` 是 ``QThread``，它以 ``permission_mode="auto"`` +
``runner.run(..., interactive=False)`` 运行。A3a 之后 Level 0-3 已能自动执行，
但 **Level 4（网络 fetch_url）与 Level 5（破坏性 delete_file）仍被 headless
通道的 ``deny_all`` 静默拒绝** —— 用户只看到工具返回
``PermissionDenied: 操作被拦截 (...非交互环境...)``，明明人就坐在屏幕前，
却没有任何弹窗可点。

修复（A3b）
------------
本模块实现 ``agent.approval.ApprovalChannel`` 协议的 GUI 通道：

* **线程安全**：``request()`` 在 worker 线程被调用，Qt 控件只能在 GUI 主线程
  创建/操作。实现方式是「worker 线程建局部 ``QEventLoop`` + 排队信号把请求
  投递到主线程的桥接槽 + 主线程弹模态对话框 + 排队信号把答复回填到 worker
  线程的答复中转对象 + ``quit()`` 事件循环」，**不用 ``processEvents`` 轮询**；
* **绝不永久阻塞**：``agent.approval_timeout``（默认 120 秒）到点即拒；
  超时后还会通知主线程关掉残留对话框，避免"用户后来点了批准但其实早已拒绝"；
* **无 GUI 环境安全降级**：没有 ``QApplication`` 实例（CI / 无头）→ 直接拒绝，
  绝不崩、绝不阻塞；
* **默认拒绝**：关闭窗口 / Esc / 超时 / 通道异常 → 一律拒绝（fail-closed）；
* Level 5 高危操作**不提供**"本会话信任"选项（与 TTY 卡片一致）；
* 批准后把工具名写进共享的 ``session_allowed_tools``（与 ``PermissionManager``
  是同一个 set 对象，选"本会话信任"后策略层立刻生效）。
"""

import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal, Slot  # noqa: E402

try:  # 双导入路径兼容
    from agent.approval import (  # noqa: E402
        DEFAULT_APPROVAL_TIMEOUT,
        extract_target_path,
        resolve_approval_timeout,
        session_remember_key,
    )
    from agent.permissions import LEVEL_NAMES, PermissionLevel  # noqa: E402
except ImportError:  # pragma: no cover
    from tools.agent.approval import (  # type: ignore # noqa: E402
        DEFAULT_APPROVAL_TIMEOUT,
        extract_target_path,
        resolve_approval_timeout,
        session_remember_key,
    )
    from tools.agent.permissions import LEVEL_NAMES, PermissionLevel  # type: ignore # noqa: E402


class _AnswerRelay(QObject):
    """worker 线程侧的答复中转。

    在调用 ``request()`` 的线程里创建；主线程（对话框）通过 ``answered``
    信号排队投递答复，槽在本对象所属线程（即等待中的 worker 线程）执行，
    填好结果后退出局部事件循环。
    """

    answered = Signal(bool, str, bool)  # allowed, reason, remember

    def __init__(self, loop: QEventLoop):
        super().__init__()
        self._loop = loop
        self.allowed: Optional[bool] = None
        self.reason: str = ""
        self.remember: bool = False
        self.answered.connect(self._deliver)

    @Slot(bool, str, bool)
    def _deliver(self, allowed: bool, reason: str, remember: bool) -> None:
        self.allowed = bool(allowed)
        self.reason = str(reason)
        self.remember = bool(remember)
        self._loop.quit()


class GuiApproval(QObject):
    """GUI 弹窗审批通道（实现 ``ApprovalChannel`` Protocol）。

    ``dialog_runner`` 是留给测试的注入点：接收请求字典、返回答复字典
    （``{"approved": bool, "remember": bool, "reason": str}`` 或 None 表示不答复）。
    默认实现弹模态 ``QDialog``。无论哪种实现，它都**只在 Qt 主线程被调用**。
    """

    #: worker 线程 → 主线程：请求弹窗
    _ask_requested = Signal(object)
    #: worker 线程 → 主线程：请求作废（超时），关掉残留对话框
    _cancel_requested = Signal(object)

    def __init__(self, session_allowed_tools: Optional[set] = None,
                 checkpoint_fn: Optional[Callable[[str], Optional[str]]] = None,
                 timeout: Optional[float] = None,
                 dialog_runner: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = None,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self.session_allowed_tools = session_allowed_tools if session_allowed_tools is not None else set()
        self.checkpoint_fn = checkpoint_fn
        if timeout is None:
            self.timeout = DEFAULT_APPROVAL_TIMEOUT
        else:
            try:
                self.timeout = float(timeout)
            except (TypeError, ValueError):
                self.timeout = DEFAULT_APPROVAL_TIMEOUT
            if self.timeout <= 0:
                self.timeout = DEFAULT_APPROVAL_TIMEOUT
        self._dialog_runner = dialog_runner or self._run_default_dialog
        self._active_dialogs: Dict[str, Any] = {}  # req_id -> 对话框（仅主线程读写）
        self._cancelled_ids: set = set()           # 超时后应丢弃答复的 req_id
        self._ask_requested.connect(self._handle_ask)        # 排队投递到主线程
        self._cancel_requested.connect(self._handle_cancel)  # 同上
        self._align_to_main_thread()

    # ── ApprovalChannel 协议 ──────────────────────────────────────────────

    @property
    def is_interactive(self) -> bool:
        return True

    def request(self, tool_name: str, level: int, tool_args: Dict[str, Any]) -> Tuple[bool, str]:
        return self._ask(tool_name, level, tool_args, is_plan=False)

    def request_plan(self, tool_name: str, level: int, tool_args: Dict[str, Any]) -> Tuple[bool, str]:
        """Plan 模式在 GUI 下同样要人确认：弹计划审计框（含 Checkpoint 快照提示）。"""
        return self._ask(tool_name, level, tool_args, is_plan=True)

    # ── 内部实现 ──────────────────────────────────────────────────────────

    def _align_to_main_thread(self) -> None:
        """把通道对象挪到 Qt 主线程 —— 排队投递的接收方必须是主线程。

        正常路径（GUI 主线程构造 AgentWorker → 构造本通道）无需搬动；这里
        只是防止调用方在别的线程构造后，弹窗被建到 worker 线程上。
        """
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None and self.thread() is not app.thread():
                self.moveToThread(app.thread())
        except Exception:
            pass

    def _ask(self, tool_name: str, level: int, tool_args: Dict[str, Any],
             is_plan: bool) -> Tuple[bool, str]:
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
        except Exception:
            app = None
        if app is None:
            # CI / 无头环境：没有可弹的窗口，绝不阻塞等输入
            return False, "GUI 审批通道不可用（未检测到图形界面），已拒绝执行该操作"
        if self.thread() is not app.thread():
            # 通道对象不在主线程：排队投递会把对话框建到 worker 线程（Qt 禁止）
            return False, "GUI 审批通道线程归属异常（不在 Qt 主线程），已拒绝执行该操作"

        req_id = uuid.uuid4().hex
        loop = QEventLoop()
        relay = _AnswerRelay(loop)
        request = self._build_request(req_id, tool_name, level, tool_args, relay, is_plan)

        timer = QTimer()
        timer.setSingleShot(True)
        timer.setInterval(max(1, int(round(self.timeout * 1000))))
        timer.timeout.connect(loop.quit)
        timer.start()
        try:
            self._ask_requested.emit(request)
            loop.exec()
        finally:
            fired = not timer.isActive()
            timer.stop()

        if relay.allowed is None:
            # 超时 / 界面关闭 / 通道中止 —— 全部 fail-closed
            self._cancelled_ids.add(req_id)
            try:
                self._cancel_requested.emit(req_id)  # 关掉可能残留的模态框
            except Exception:
                pass
            if fired:
                return False, (f"GUI 审批超时（{self._timeout_text()} 秒内未收到用户答复），"
                               f"已拒绝执行该操作")
            return False, "GUI 审批被中止（界面已关闭），已拒绝执行该操作"

        allowed = bool(relay.allowed)
        reason = relay.reason or ("用户批准单次执行" if allowed else "用户拒绝执行该操作")
        # 防御纵深：即便对话框实现无视协议回传了 remember，Level 5 也绝不记入信任集
        if allowed and relay.remember and level < PermissionLevel.DANGEROUS:
            # [B4] 与 TTY/网关通道同一收口：MCP 工具按 ``mcp_*`` 信任键记住
            self.session_allowed_tools.add(session_remember_key(tool_name))
            reason = "用户批准本会话永久信任此工具"
        return allowed, reason

    def _timeout_text(self) -> str:
        try:
            return str(int(float(self.timeout)))
        except (TypeError, ValueError):  # pragma: no cover - __init__ 已保证为数字
            return str(int(DEFAULT_APPROVAL_TIMEOUT))

    def _build_request(self, req_id: str, tool_name: str, level: int,
                       tool_args: Dict[str, Any], relay: _AnswerRelay,
                       is_plan: bool) -> Dict[str, Any]:
        """组装请求字典（参数预览 >60 字符截断，与 TTY 卡片同口径）。"""
        args_preview = []
        for k, v in (tool_args or {}).items():
            val_str = str(v)
            if len(val_str) > 60:
                val_str = val_str[:57] + "..."
            args_preview.append(f"{k}='{val_str}'")

        is_danger = level >= PermissionLevel.DANGEROUS
        checkpoint_hint = ""
        if is_plan:
            target = extract_target_path(tool_args)
            if target and self.checkpoint_fn:
                try:
                    ckpt_file = self.checkpoint_fn(target)
                    if ckpt_file:
                        checkpoint_hint = (f"[Checkpoint 安全快照已创建]: "
                                           f"{Path(ckpt_file).name} (随时输入 ky rollback 一键还原)")
                except Exception:
                    pass

        return {
            "request_id": req_id,
            "tool_name": tool_name,
            "level": level,
            "level_desc": LEVEL_NAMES.get(level, f"Level {level}"),
            "args_preview": ", ".join(args_preview),
            "is_danger": is_danger,
            # Level 5 高危操作不提供"本会话信任"选项（与 TTY 卡片一致）
            "allow_remember": not is_danger,
            "is_plan": is_plan,
            "checkpoint_hint": checkpoint_hint,
            "relay": relay,
        }

    # ── 主线程槽（Qt 保证这两个方法在通道对象所属线程执行） ────────────────

    @Slot(object)
    def _handle_ask(self, request: Dict[str, Any]) -> None:
        """主线程：弹审批框，把答复排队回填给 worker 线程。"""
        req_id = request.get("request_id")
        if req_id in self._cancelled_ids:
            self._cancelled_ids.discard(req_id)
            return
        try:
            answer = self._dialog_runner(request)
        except Exception:
            answer = {"approved": False, "reason": "GUI 审批对话框异常，已拒绝执行该操作"}
        if req_id in self._cancelled_ids:
            # 对话框还在（或刚弹完）时请求已超时作废：丢弃答复，绝不执行
            self._cancelled_ids.discard(req_id)
            return
        relay = request.get("relay")
        if relay is None or not answer:
            return
        approved = bool(answer.get("approved"))
        is_plan = bool(request.get("is_plan"))
        if approved:
            default_reason = "用户批准 Plan 模式执行变更" if is_plan else "用户批准单次执行"
        else:
            default_reason = "用户拒绝 Plan 模式该项执行计划" if is_plan else "用户拒绝执行该操作"
        reason = str(answer.get("reason") or default_reason)
        try:
            relay.answered.emit(approved, reason, bool(answer.get("remember")))
        except RuntimeError:
            pass  # 通道正在拆除：答复送不到就等同于拒绝

    @Slot(object)
    def _handle_cancel(self, req_id: str) -> None:
        """主线程：超时后关掉残留对话框，避免"用户点了批准却什么都没发生"。"""
        self._cancelled_ids.add(req_id)
        dlg = self._active_dialogs.get(req_id)
        if dlg is not None:
            try:
                dlg.reject()
            except Exception:
                pass

    def _run_default_dialog(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """默认实现：模态审批框。关闭窗口 / Esc → 拒绝。"""
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout

        is_plan = bool(request.get("is_plan"))
        is_danger = bool(request.get("is_danger"))
        title = "📋 行动计划审计" if is_plan else "🛡️ 权限审批"
        dlg = QDialog()
        dlg.setWindowTitle(f"{title} · {request.get('tool_name')}")
        dlg.setModal(True)
        dlg.setMinimumWidth(480)

        layout = QVBoxLayout(dlg)
        lines = [
            "智能私教请求执行文件写入或环境变更操作:" if is_plan
            else "智能私教请求调用外部工具:",
            f"• 目标工具: {request.get('tool_name')}",
            f"• 权限级别: {request.get('level_desc')}",
            f"• 传入参数: {request.get('args_preview') or '(无)'}",
        ]
        if request.get("checkpoint_hint"):
            lines.append(f"📦 {request['checkpoint_hint']}")
        if is_danger:
            lines.append("⚠️ 此操作包含文件删除或系统破坏风险，请极其谨慎核对!")
        # [B2b] 明示能力边界：审批闸门是应用层逻辑隔离，不是 OS 级沙箱
        lines.append("提示：本沙箱为逻辑隔离（非 OS 沙箱）")
        label = QLabel("\n".join(lines))
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(label)

        remember_box = None
        if not is_danger:
            remember_box = QCheckBox("本会话记住并信任此类操作（不再询问）")
            layout.addWidget(remember_box)

        buttons = QDialogButtonBox()
        approve_btn = buttons.addButton("批准本次执行", QDialogButtonBox.AcceptRole)
        deny_btn = buttons.addButton("拒绝执行（默认）", QDialogButtonBox.RejectRole)
        # 默认按钮是"拒绝"：直接回车也走 fail-closed
        deny_btn.setDefault(True)
        deny_btn.setAutoDefault(False)
        approve_btn.setAutoDefault(False)
        layout.addWidget(buttons)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)

        req_id = request.get("request_id")
        self._active_dialogs[req_id] = dlg
        try:
            result = dlg.exec()
        finally:
            self._active_dialogs.pop(req_id, None)

        if result != QDialog.Accepted:
            return {"approved": False,
                    "reason": "用户拒绝 Plan 模式该项执行计划" if is_plan else "用户拒绝执行该操作"}
        if is_plan:
            return {"approved": True, "reason": "用户批准 Plan 模式执行变更"}
        if remember_box is not None and remember_box.isChecked():
            return {"approved": True, "remember": True, "reason": "用户批准本会话永久信任此工具"}
        return {"approved": True, "reason": "用户批准单次执行"}


__all__ = ["GuiApproval", "resolve_approval_timeout", "DEFAULT_APPROVAL_TIMEOUT"]
