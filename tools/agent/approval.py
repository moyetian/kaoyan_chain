# -*- coding: utf-8 -*-
"""
考研学习链 (ky-cli) · 审批通道 (Approval Channels)

把「向用户请求审批」这件事从权限策略里抽出来，是本关注点的**唯一实现处**。

[G13 / A3a 修复] 旧实现把「模式判定」「TTY 探测」「终端渲染」「读输入」全塞在
``PermissionManager.check_permission`` 里，于是：
  * 任何非 TTY 环境（GUI / 网关 / 管道）都只能走"一律拒绝"一条路 —— 桌面端因此
    完全写不了文件（实测 G13）；
  * 想加 GUI 弹窗或网关卡片审批，必须去改权限策略本身。

本模块把交互侧抽成 ``ApprovalChannel``：**策略只管「该不该问」，通道只管「怎么问」**。
A3b 的 ``GuiApproval`` / ``GatewayApproval`` 只需实现同一个 Protocol 即可接入。

通道契约：``request(tool_name, level, tool_args) -> (allowed, reason)``，
**任何实现都不得阻塞等待一个不存在的用户**。
"""

import sys
import fnmatch
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional, Protocol, Tuple

#: headless（无 TTY / 无 GUI / 无网关）环境下的写操作策略
HEADLESS_POLICIES = ("deny_all", "allow_list", "auto_within_workspace")

#: 默认策略：一律拒绝。与接入审批通道之前的既有行为**逐字节一致**。
DEFAULT_HEADLESS_POLICY = "deny_all"

#: 审批通道的默认等待上限（秒）。GUI / 网关都不得无限期阻塞等一个不在场的人。
DEFAULT_APPROVAL_TIMEOUT = 120.0

#: 工具参数里可能承载目标文件路径的键（与 `_prompt_plan_approval` 同口径）。
_PATH_ARG_KEYS = ("path", "file_name", "target_file")

#: [B4] MCP 工具命名约定 ``mcp_{server}_{tool}``（见 ``mcp_client.get_all_mcp_tools``）。
#: 权限侧按前缀识别这一"工具类别"，避免每个外部工具名都单独弹卡/单独配置。
MCP_TOOL_PREFIX = "mcp_"

#: [B4] "本会话记住"对 MCP 工具收口的信任键：一次批准 = 信任本会话的全部 MCP 工具。
#: 为什么不按 server 粒度（``mcp_{server}_*``）：server 名自身可能含下划线
#: （``mcp_my_server_toolA``），字符串解析会歧义地把别的 server 也放进来；
#: ``mcp_*`` 无歧义，且 MCP 工具都由用户自己配置的 server 提供，语义上正是
#: "我信任这一类工具"。
MCP_SESSION_TRUST_KEY = "mcp_*"


def tool_name_matches(tool_name: str, patterns: Iterable[str]) -> bool:
    """工具名是否命中任一模式（支持 ``mcp_*`` 这类 glob；普通名字仍按精确匹配）。

    [B4] 此前 allow_list / 会话信任都是 ``tool_name in set`` 的精确匹配：用户写
    ``mcp_*`` 放行全部 MCP 工具会被当成一个字面工具名，永远不命中；每个外部 MCP
    工具都得逐个点名、逐个弹卡。现统一走 glob 匹配（``fnmatch`` 对普通名字等价于
    精确匹配，既有行为逐字不变）。
    """
    name = str(tool_name or "")
    if not name:
        return False
    for p in patterns or ():
        pat = str(p or "")
        if not pat:
            continue
        if pat == name or fnmatch.fnmatchcase(name, pat):
            return True
    return False


def session_remember_key(tool_name: str) -> str:
    """计算"本会话永久信任"写入信任集的键。

    普通工具按原名记住（保持既有语义）；``mcp_*`` 前缀的外部工具统一收口为
    :data:`MCP_SESSION_TRUST_KEY`，避免同一 server 的多个工具逐个弹卡。
    """
    name = str(tool_name or "")
    if name.startswith(MCP_TOOL_PREFIX):
        return MCP_SESSION_TRUST_KEY
    return name


class ApprovalChannel(Protocol):
    """审批通道协议。"""

    @property
    def is_interactive(self) -> bool:
        """该通道是否具备真实的人机交互能力。"""
        ...

    def request(self, tool_name: str, level: int, tool_args: Dict[str, Any]) -> Tuple[bool, str]:
        """请求审批，返回 ``(是否放行, 理由)``。"""
        ...

    def request_plan(self, tool_name: str, level: int, tool_args: Dict[str, Any]) -> Tuple[bool, str]:
        """Plan 模式的计划审计请求，返回 ``(是否放行, 理由)``。"""
        ...


def is_interactive_environment(interactive: bool = True) -> bool:
    """调用方声明可交互**且** stdin 真的是 TTY。

    两道都要过：GUI / 网关 / 管道里 ``interactive`` 可能仍是默认 True，但
    ``stdin.isatty()`` 为假；反过来在测试里也可能声明 ``interactive=False``。
    """
    if not interactive:
        return False
    try:
        return bool(sys.stdin.isatty())
    except Exception:
        return False


def extract_target_path(tool_args: Dict[str, Any]) -> Optional[str]:
    """从工具参数里取出目标文件路径（取不到返回 None）。"""
    for key in _PATH_ARG_KEYS:
        val = (tool_args or {}).get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def resolve_headless_policy(config: Optional[Dict[str, Any]]) -> str:
    """从配置里解析 headless 写操作策略；非法值一律回落默认（deny_all）。

    非法值**必须**回落 deny_all 而不是某个宽松策略 —— 配置写错时的失败方向
    只能是"更严"。
    """
    cfg = config if isinstance(config, dict) else {}
    agent_cfg = cfg.get("agent")
    raw = agent_cfg.get("headless_write_policy") if isinstance(agent_cfg, dict) else None
    if isinstance(raw, str) and raw.strip().lower() in HEADLESS_POLICIES:
        return raw.strip().lower()
    return DEFAULT_HEADLESS_POLICY


def resolve_headless_allow_tools(config: Optional[Dict[str, Any]]) -> Tuple[str, ...]:
    """解析 ``allow_list`` 策略下允许的工具名清单（仅接受字符串列表）。"""
    cfg = config if isinstance(config, dict) else {}
    agent_cfg = cfg.get("agent")
    raw = agent_cfg.get("headless_allow_tools") if isinstance(agent_cfg, dict) else None
    if isinstance(raw, (list, tuple)):
        return tuple(str(x).strip() for x in raw if isinstance(x, str) and x.strip())
    return ()


def resolve_approval_timeout(config: Optional[Dict[str, Any]]) -> float:
    """解析审批通道的等待上限（秒），取自 ``agent.approval_timeout``。

    非法值（非数字 / 非正数 / 缺失）一律回落 ``DEFAULT_APPROVAL_TIMEOUT``：
    审批超时必须是一个**有限**的数，配置写错不能变成"永远等下去"。
    """
    cfg = config if isinstance(config, dict) else {}
    agent_cfg = cfg.get("agent")
    raw = agent_cfg.get("approval_timeout") if isinstance(agent_cfg, dict) else None
    try:
        val = float(raw)
        if val > 0:
            return val
    except (TypeError, ValueError):
        pass
    return DEFAULT_APPROVAL_TIMEOUT


class TtyApproval:
    """真实终端通道：渲染审批卡片并读用户输入。

    渲染与读入逻辑从 ``PermissionManager._prompt_user_approval`` /
    ``_prompt_plan_approval`` 原样迁入，**输出逐字节不变**（既有测试与用户肌肉
    记忆都依赖这套卡片）。
    """

    def __init__(self, session_allowed_tools: Optional[set] = None,
                 checkpoint_fn: Optional[Callable[[str], Optional[str]]] = None):
        self.session_allowed_tools = session_allowed_tools if session_allowed_tools is not None else set()
        self.checkpoint_fn = checkpoint_fn

    @property
    def is_interactive(self) -> bool:
        return True

    def request(self, tool_name: str, level: int, tool_args: Dict[str, Any]) -> Tuple[bool, str]:
        return self._prompt_user_approval(tool_name, level, tool_args)

    def request_plan(self, tool_name: str, level: int, tool_args: Dict[str, Any]) -> Tuple[bool, str]:
        """Plan 模式专用卡片（含写前 Checkpoint 快照提示）。"""
        return self._prompt_plan_approval(tool_name, level, tool_args)

    # ── 以下两个卡片渲染方法由 permissions.py 原样迁入，勿改文案 ──────────

    def _prompt_user_approval(self, tool_name: str, level: int, tool_args: Dict[str, Any]) -> Tuple[bool, str]:
        """渲染 Codex 风格的优雅审批卡片"""
        args_preview = []
        for k, v in tool_args.items():
            val_str = str(v)
            if len(val_str) > 60:
                val_str = val_str[:57] + "..."
            args_preview.append(f"{k}='{val_str}'")
        param_line = ", ".join(args_preview)

        from .permissions import LEVEL_NAMES, PermissionLevel
        level_desc = LEVEL_NAMES.get(level, f"Level {level}")
        is_danger = (level >= PermissionLevel.DANGEROUS)

        border_color = "\033[91m" if is_danger else "\033[93m"
        reset = "\033[0m"
        bold = "\033[1m"

        print(f"\n{border_color}╭────────────────────────────────────────────────────────────────────────╮{reset}")
        print(f"{border_color}│{reset}  {bold}🛡️ [权限审批] 智能私教请求调用外部工具:{reset}")
        print(f"{border_color}│{reset}  • 目标工具: {bold}{tool_name}{reset}")
        print(f"{border_color}│{reset}  • 权限级别: {level_desc}")
        print(f"{border_color}│{reset}  • 传入参数: {param_line}")
        print(f"{border_color}│{reset}")
        if is_danger:
            print(f"{border_color}│{reset}  ⚠️  {bold}此操作包含文件删除或系统破坏风险，请极其谨慎核对!{reset}")
            print(f"{border_color}│{reset}  选项: [y] 仅批准本次执行  /  [n] 拒绝执行 (默认)")
        else:
            print(f"{border_color}│{reset}  选项: [y] 批准本次  /  [a] 本会话记住并信任此类操作  /  [n] 拒绝 (默认)")
        print(f"{border_color}╰────────────────────────────────────────────────────────────────────────╯{reset}")

        try:
            choice = input("👉 请选择审批决定 [y/a/n] (默认 n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消执行。")
            return False, "用户中断审批"

        if choice == "y":
            return True, "用户批准单次执行"
        elif choice == "a" and not is_danger:
            self.session_allowed_tools.add(session_remember_key(tool_name))
            return True, "用户批准本会话永久信任此工具"
        else:
            return False, "用户拒绝执行该操作"

    def _prompt_plan_approval(self, tool_name: str, level: int, tool_args: Dict[str, Any]) -> Tuple[bool, str]:
        """Plan Mode 专用计划审计与确认卡片 (S3-3)"""
        args_preview = []
        target_file = None
        for k, v in tool_args.items():
            if k in ("path", "file_name", "target_file"):
                target_file = str(v)
            val_str = str(v)
            if len(val_str) > 60:
                val_str = val_str[:57] + "..."
            args_preview.append(f"{k}='{val_str}'")
        param_line = ", ".join(args_preview)

        backup_hint = ""
        if target_file and self.checkpoint_fn:
            try:
                ckpt_file = self.checkpoint_fn(target_file)
                if ckpt_file:
                    backup_hint = f"\n\033[96m│\033[0m  📦 \033[1m[Checkpoint 安全快照已创建]\033[0m: {Path(ckpt_file).name} (随时输入 ky rollback 一键还原)"
            except Exception:
                pass

        print(f"\n\033[96m╭── 📋 [Plan Mode 行动计划审计] ────────────────────────────────────────╮\033[0m")
        print(f"\033[96m│\033[0m  \033[1m智能私教请求执行文件写入或环境变更操作:\033[0m")
        print(f"\033[96m│\033[0m  • 计划调用: \033[1m{tool_name}\033[0m (Level {level})")
        print(f"\033[96m│\033[0m  • 涉及参数: {param_line}{backup_hint}")
        print(f"\033[96m│\033[0m")
        print(f"\033[96m│\033[0m  选项: [y] 批准计划并执行变更  /  [n] 拒绝本次计划 (默认)")
        print(f"\033[96m╰────────────────────────────────────────────────────────────────────────╯\033[0m")

        try:
            choice = input("👉 是否批准此项行动计划? [y/n] (默认 n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消执行。")
            return False, "用户中断 Plan 审批"

        if choice == "y":
            return True, "用户批准 Plan 模式执行变更"
        else:
            return False, "用户拒绝 Plan 模式该项执行计划"


class HeadlessApproval:
    """无交互环境通道：按 ``headless_write_policy`` 决策，**绝不阻塞等输入**。

    三种策略：
      * ``deny_all``（默认）—— 一律拒绝，理由文案与接入通道前逐字节一致；
      * ``allow_list`` —— 只放行 ``headless_allow_tools`` 里点名的工具；
      * ``auto_within_workspace`` —— 只放行**写入类（Level 1）且目标路径落在
        工作区内**的调用；路径越界、非写入类、取不到路径一律拒绝。
    """

    def __init__(self, mode: str = "ask", policy: str = DEFAULT_HEADLESS_POLICY,
                 allow_tools: Iterable[str] = (), workspace_root=None):
        self.mode = mode
        self.policy = policy if policy in HEADLESS_POLICIES else DEFAULT_HEADLESS_POLICY
        self.allow_tools = tuple(allow_tools or ())
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None

    @property
    def is_interactive(self) -> bool:
        return False

    def request(self, tool_name: str, level: int, tool_args: Dict[str, Any]) -> Tuple[bool, str]:
        if self.policy == "allow_list":
            # [B4] glob 匹配：白名单可写 ``mcp_*`` 一次放行全部外部 MCP 工具；
            # 普通工具名（write_file 等）仍按精确匹配，语义不变。
            if tool_name_matches(tool_name, self.allow_tools):
                return True, f"headless 白名单放行 [{tool_name}]"
            return False, self._deny_reason(tool_name)

        if self.policy == "auto_within_workspace":
            from .permissions import PermissionLevel
            if level != PermissionLevel.SAFE_EDIT:
                return False, self._deny_reason(tool_name)
            target = extract_target_path(tool_args)
            if not target or self.workspace_root is None:
                return False, self._deny_reason(tool_name)
            try:
                resolved = (self.workspace_root / target).resolve()
                resolved.relative_to(self.workspace_root)
            except (ValueError, OSError):
                return False, (f"headless 策略 auto_within_workspace 拒绝越界写入 "
                               f"[{tool_name}] → {target}（目标不在工作区内）")
            return True, f"headless 策略 auto_within_workspace 放行工作区内写入 [{tool_name}]"

        return False, self._deny_reason(tool_name)

    def _deny_reason(self, tool_name: str) -> str:
        """默认拒绝理由 —— 与接入审批通道之前的文案**逐字节一致**。"""
        return (f"当前模式 ({self.mode}) 下非交互环境无法请求用户审批写操作 "
                f"[{tool_name}]，请在交互终端运行或指定 --permission=auto")

    def request_plan(self, tool_name: str, level: int, tool_args: Dict[str, Any]) -> Tuple[bool, str]:
        """Plan 模式在无交互环境下**一律拒绝**：没有可确认行动计划的用户。

        文案与接入审批通道之前逐字节一致。此处刻意不受 ``headless_write_policy``
        影响 —— plan 模式的语义就是"先出计划再批准"，无人可批时只能拒绝。
        """
        return False, (f"Plan 模式 (--permission=plan) 下非交互环境禁止自动执行写操作 "
                       f"[{tool_name}]，需出具并确认行动计划")


class _PendingReply:
    """一条在途审批请求的答复槽（读写均由 ``GatewayApproval`` 的锁保护）。"""

    __slots__ = ("event", "answer", "resolved")

    def __init__(self):
        self.event = threading.Event()
        self.answer: Optional[Tuple[bool, bool]] = None
        self.resolved = False


class GatewayApproval:
    """网关 / IM 卡片审批通道：**传输可注入**，超时一律拒绝（fail-closed）。

    [A3b 核实结论] ``tools/cli/gateway.py`` 的 ``/api/ask`` 与
    ``/v1/chat/completions`` 都直连 ``query_llm_reply()``（纯 LLM 问答，
    **没有任何 agent 工具循环**）；全仓 agent 执行器的构造点只有 CLI REPL
    与 GUI worker 两处 —— 网关侧当前**没有消费者**。因此这里刻意不新增
    任何 HTTP 端点（那等于凭空造出一个能授权工具执行的鉴权面），而是把
    "怎么把卡片送出去 / 怎么把答复等回来"抽成两个可注入的可调用对象，
    等真实消费者出现时再接。

    传输契约：
      * ``deliver_card(card: dict) -> None`` —— 把审批卡片投递给用户；
      * ``wait_reply(request_id: str, timeout: float) -> Optional[Tuple[bool, bool]]``
        —— 等待答复，返回 ``(approved, remember)``；无答复返回 None。
        默认实现基于 ``submit_reply`` 写入的 pending 注册表（线程安全）。

    失败方向全部朝"拒绝"：超时、未知请求 id、重复答复（只认第一次）、
    传输异常 —— 一律拒绝。
    """

    def __init__(self, deliver_card: Optional[Callable[[Dict[str, Any]], None]] = None,
                 wait_reply: Optional[Callable[[str, float], Optional[Tuple[bool, bool]]]] = None,
                 *, timeout: Optional[float] = None,
                 session_allowed_tools: Optional[set] = None,
                 checkpoint_fn: Optional[Callable[[str], Optional[str]]] = None):
        self.deliver_card = deliver_card
        self.wait_reply = wait_reply or self._default_wait_reply
        try:
            self.timeout = float(timeout) if timeout is not None else DEFAULT_APPROVAL_TIMEOUT
        except (TypeError, ValueError):
            self.timeout = DEFAULT_APPROVAL_TIMEOUT
        if self.timeout <= 0:
            self.timeout = DEFAULT_APPROVAL_TIMEOUT
        self.session_allowed_tools = session_allowed_tools if session_allowed_tools is not None else set()
        self.checkpoint_fn = checkpoint_fn
        self._lock = threading.Lock()
        self._pending: Dict[str, _PendingReply] = {}

    @property
    def is_interactive(self) -> bool:
        return True

    # ── 传输层回调：IM / HTTP 消费者收到用户答复后调用 ────────────────────

    def submit_reply(self, request_id: str, approved: bool, remember: bool = False) -> bool:
        """登记用户答复；返回是否被接受。

        未知 ``request_id``（乱序/伪造/已超时清理）→ False；重复答复 →
        只认第一次，后续一律 False。
        """
        with self._lock:
            pending = self._pending.get(request_id)
            if pending is None or pending.resolved:
                return False
            pending.resolved = True
            pending.answer = (bool(approved), bool(remember))
        pending.event.set()
        return True

    def _default_wait_reply(self, request_id: str, timeout: float) -> Optional[Tuple[bool, bool]]:
        with self._lock:
            pending = self._pending.get(request_id)
        if pending is None:
            return None
        if not pending.event.wait(timeout):
            return None
        return pending.answer

    # ── ApprovalChannel 协议 ──────────────────────────────────────────────

    def request(self, tool_name: str, level: int, tool_args: Dict[str, Any]) -> Tuple[bool, str]:
        return self._ask(tool_name, level, tool_args, is_plan=False)

    def request_plan(self, tool_name: str, level: int, tool_args: Dict[str, Any]) -> Tuple[bool, str]:
        return self._ask(tool_name, level, tool_args, is_plan=True)

    def _ask(self, tool_name: str, level: int, tool_args: Dict[str, Any],
             is_plan: bool) -> Tuple[bool, str]:
        if self.deliver_card is None:
            return False, "网关审批通道未配置卡片投递传输，已拒绝执行该操作"

        req_id = uuid.uuid4().hex
        pending = _PendingReply()
        with self._lock:
            self._pending[req_id] = pending
        try:
            try:
                self.deliver_card(self._build_card(req_id, tool_name, level, tool_args, is_plan))
            except Exception:
                return False, "网关审批卡片投递失败，已拒绝执行该操作"
            try:
                answer = self.wait_reply(req_id, self.timeout)
            except Exception:
                answer = None
        finally:
            with self._lock:
                self._pending.pop(req_id, None)

        if not answer:
            return False, (f"网关审批超时（{self._timeout_text()} 秒内未收到用户答复），"
                           f"已拒绝执行该操作")

        from .permissions import PermissionLevel
        approved, remember = bool(answer[0]), bool(answer[1])
        if not approved:
            return False, ("用户拒绝 Plan 模式该项执行计划" if is_plan
                           else "用户拒绝执行该操作")
        if remember and level < PermissionLevel.DANGEROUS:
            # [B4] MCP 工具收口为 ``mcp_*`` 信任键（一次批准不再逐工具弹卡）
            self.session_allowed_tools.add(session_remember_key(tool_name))
            return True, "用户批准本会话永久信任此工具"
        return True, ("用户批准 Plan 模式执行变更" if is_plan else "用户批准单次执行")

    def _timeout_text(self) -> str:
        try:
            return str(int(float(self.timeout)))
        except (TypeError, ValueError):  # pragma: no cover - __init__ 已保证为数字
            return str(int(DEFAULT_APPROVAL_TIMEOUT))

    def _build_card(self, req_id: str, tool_name: str, level: int,
                    tool_args: Dict[str, Any], is_plan: bool) -> Dict[str, Any]:
        from .permissions import LEVEL_NAMES, PermissionLevel
        is_danger = level >= PermissionLevel.DANGEROUS
        args_preview = []
        for k, v in (tool_args or {}).items():
            val_str = str(v)
            if len(val_str) > 60:
                val_str = val_str[:57] + "..."
            args_preview.append(f"{k}='{val_str}'")

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
            "timeout": self.timeout,
        }


def select_channel(*, interactive: bool = True, mode: str = "ask",
                   policy: str = DEFAULT_HEADLESS_POLICY,
                   allow_tools: Iterable[str] = (), workspace_root=None,
                   session_allowed_tools: Optional[set] = None,
                   checkpoint_fn: Optional[Callable[[str], Optional[str]]] = None,
                   ) -> ApprovalChannel:
    """按环境选择审批通道。

    只有「调用方声明可交互 + stdin 是 TTY」才给 ``TtyApproval``；其余（GUI、
    网关、管道、CI）一律 ``HeadlessApproval`` —— 后者绝不阻塞。
    """
    if is_interactive_environment(interactive):
        return TtyApproval(session_allowed_tools=session_allowed_tools,
                           checkpoint_fn=checkpoint_fn)
    return HeadlessApproval(mode=mode, policy=policy, allow_tools=allow_tools,
                            workspace_root=workspace_root)
