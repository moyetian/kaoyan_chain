# -*- coding: utf-8 -*-
"""
考研学习链 (ky-cli) · 权限审批系统 (Permission Engine)
借鉴 Codex / Claude Code 审批模型:
Level 0 = Read Only (只读操作，全自动放行)
Level 1 = Safe Edit (安全写入与修改)
Level 2 = Low-risk Exec (低风险执行)
Level 3 = Shell Exec (Shell 命令执行)
Level 4 = Network (网络访问)
Level 5 = Dangerous (删除与破坏性操作)
"""

import json
from typing import Dict, Any, Tuple

try:  # 双导入路径兼容（项目同时存在 tools.X 与 X 两种导入方式）
    from ky_io import atomic_write_text  # noqa: E402
except ImportError:  # pragma: no cover
    from tools.ky_io import atomic_write_text  # noqa: E402

try:  # 双导入路径兼容
    from .approval import (  # noqa: E402
        DEFAULT_HEADLESS_POLICY,
        resolve_headless_allow_tools,
        resolve_headless_policy,
        select_channel,
        tool_name_matches,
    )
except ImportError:  # pragma: no cover
    from agent.approval import (  # type: ignore # noqa: E402
        DEFAULT_HEADLESS_POLICY,
        resolve_headless_allow_tools,
        resolve_headless_policy,
        select_channel,
        tool_name_matches,
    )

#: 合法权限模式（4 档，保持既有语义不变）
VALID_MODES = ("ask", "auto", "safe", "plan")

#: 外部叫法 → 本项目的模式。`acceptEdits` 是 Claude Code 系的命名，语义等价于
#: 本项目的 `auto`（Level 0-3 自动执行、Level 4-5 询问）。
#: [G13 修复] 此前任何不在 4 档里的字符串都被**静默**当成 `ask`，GUI 传的
#: `acceptEdits` 因此悄悄降级，叠加非 TTY 判定后写操作全被拒。
MODE_ALIASES = {"acceptedits": "auto"}

#: [B2b] 「工作区外只读读取」审批用的工具名（不是真实工具名，仅作为审批卡片的标识）。
#: 用户在卡片上选 [a]「本会话记住并信任此类操作」后，该键写入 ``session_allowed_tools``，
#: 本会话内所有外部读取不再弹卡；不选 [a] 时按**目录**粒度记忆（见
#: ``Sandbox.register_authorized_read_dir``）。
EXTERNAL_READ_TOOL_NAME = "read_file@external"


def normalize_mode(mode: Any) -> str:
    """把外部传入的模式名规范化；**未知模式抛 ValueError 而不是静默回退**。

    [G13 修复] 旧实现是 ``mode.lower() if mode in ("ask", "auto", "safe", "plan") else "ask"``：
    判断用的是**原始大小写**，于是
      * ``"SAFE"`` / ``"Auto"`` 这类大小写变体被判为非法 → 静默降级成 ``ask``，
        请求只读却拿到可批准的写权限（安全降级，比 G13 本身更危险）；
      * 任何拼写错误都无声变成 ``ask``，调用方以为自己拿到了别的模式。
    现在：先 strip+lower，再查别名表，最后校验 4 档；都不中则显式报错。
    """
    raw = str(mode if mode is not None else "").strip().lower()
    if raw in MODE_ALIASES:
        return MODE_ALIASES[raw]
    if raw in VALID_MODES:
        return raw
    raise ValueError(
        f"未知权限模式 {mode!r}；合法值为 {', '.join(VALID_MODES)}"
        f"（别名：{', '.join(sorted(MODE_ALIASES))}）"
    )


class PermissionLevel:
    READ_ONLY = 0      # 只读 (read_file, list_dir, grep, read_exam_paper, verify_math)
    SAFE_EDIT = 1      # 安全编辑 (write_file, edit_file, log_mistake)
    LOW_RISK_EXEC = 2  # 低风险执行 (python 验算等)
    SHELL_EXEC = 3     # Shell 执行 (run_command)
    NETWORK = 4        # 网络访问 (fetch_url)
    DANGEROUS = 5      # 破坏性 (delete_file, git reset --hard)

LEVEL_NAMES = {
    0: "Level 0 [只读探索 - 零风险]",
    1: "Level 1 [文件修改 - 安全]",
    2: "Level 2 [轻量执行 - 低风险]",
    3: "Level 3 [系统Shell - 中风险]",
    4: "Level 4 [网络访问 - 外部流量]",
    5: "Level 5 [高危操作 - 破坏性可能]",
}

class PermissionManager:
    def __init__(self, mode: str = "ask", workspace_root=None, config=None,
                 approval_channel=None):
        """
        mode:
          - 'ask': 默认推荐模式。Level 0 自动执行；Level 1-4 提示用户审批；Level 5 必须高亮确认
          - 'auto': 全自动沙箱模式。Level 0-3 自动执行；Level 4-5 询问
          - 'safe': 严格只读模式。只允许 Level 0，其他全部拒绝
          - 'plan': 计划审计模式。Level 0 自动放行；非只读修改强制呈现 Plan 审查卡片并在写前创建 Checkpoint 备份
          - 'acceptEdits'：`auto` 的别名（Claude Code 系命名）

        未知模式抛 ValueError（不再静默回退 ask）。

        无交互环境（GUI / 网关 / 管道 / CI）下的写操作由 `agent.approval` 的
        headless 通道决策，策略取自 `config['agent']['headless_write_policy']`
        （默认 `deny_all`，即与接入通道前行为一致）。

        [A3b] ``approval_channel``：调用方显式提供的审批通道（如 GUI 弹窗
        ``GuiApproval``）。给了就**直接用**，不再走 ``select_channel`` 的
        TTY 判定；不传（默认）时行为与接入该参数前**逐字节一致**。
        """
        from pathlib import Path
        self.mode = normalize_mode(mode)
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else Path.cwd().resolve()
        self.session_allowed_tools = set()  # 会话内用户选择 [a] 记住允许的工具集合
        self.force_allow_all = False        # 测试与全自动沙箱调试开关
        self.checkpoint_dir = self.workspace_root / ".checkpoint"
        self.config = config if isinstance(config, dict) else {}
        self.headless_policy = resolve_headless_policy(self.config)
        self.headless_allow_tools = resolve_headless_allow_tools(self.config)
        self.approval_channel = approval_channel
        if approval_channel is not None:
            # 通道侧若自带信任集（TtyApproval / GuiApproval 均是），必须**共享同一个
            # set 对象**：用户在弹窗里选"本会话信任"后，策略层的
            # ``tool_name in self.session_allowed_tools`` 要立刻生效。
            shared = getattr(approval_channel, "session_allowed_tools", None)
            if isinstance(shared, set):
                self.session_allowed_tools = shared

    def _select_channel(self, interactive: bool):
        """按当前环境选择审批通道（显式注入 > TTY 卡片 / headless 策略）。"""
        # [A3b 修复·GUI 审批通道] 调用方显式注入的通道优先：GUI 里有人在场，
        # Level 4-5 应该弹审批框，而不是因 stdin 非 TTY 被 headless 静默拒绝。
        if self.approval_channel is not None:
            return self.approval_channel
        return select_channel(
            interactive=interactive,
            mode=self.mode,
            policy=self.headless_policy,
            allow_tools=self.headless_allow_tools,
            workspace_root=self.workspace_root,
            session_allowed_tools=self.session_allowed_tools,
            checkpoint_fn=self._checkpoint_for,
        )

    def _checkpoint_for(self, target_file: str):
        """Plan 卡片用：把相对路径解析成工作区内文件并建快照（不存在则返回 None）。"""
        try:
            full_target = (self.workspace_root / target_file).resolve()
        except Exception:
            return None
        if not full_target.exists():
            return None
        return self.create_checkpoint(full_target)

    def create_checkpoint(self, file_path) -> str:
        """在修改文件前自动创建快照备份 (S3-3 Plan Mode 审计沙箱)"""
        from pathlib import Path
        import shutil
        import time

        fp = Path(file_path).resolve()
        if not fp.exists():
            return ""

        ts = time.strftime("%Y%m%d_%H%M%S")
        target_ckpt_dir = self.checkpoint_dir / f"ckpt_{ts}"
        target_ckpt_dir.mkdir(parents=True, exist_ok=True)

        try:
            rel_p = fp.relative_to(self.workspace_root)
        except Exception:
            rel_p = fp.name

        backup_file = target_ckpt_dir / str(rel_p)
        backup_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fp, backup_file)

        # 记录元数据
        meta_file = target_ckpt_dir / "_meta.json"
        meta = {
            "timestamp": ts,
            "original_file": str(fp),
            "relative_file": str(rel_p),
            "backup_file": str(backup_file)
        }
        atomic_write_text(meta_file, json.dumps(meta, ensure_ascii=False, indent=2))
        return str(backup_file)

    def restore_last_checkpoint(self) -> Dict[str, Any]:
        """回滚最近一次 Checkpoint 快照 (S3-3 Plan Mode)"""
        from pathlib import Path
        import shutil

        if not self.checkpoint_dir.exists():
            return {"success": False, "message": "未找到任何 Checkpoint 快照备份"}

        ckpts = sorted([d for d in self.checkpoint_dir.iterdir() if d.is_dir() and d.name.startswith("ckpt_")])
        if not ckpts:
            return {"success": False, "message": "没有可供回滚的快照目录"}

        latest_ckpt = ckpts[-1]
        meta_file = latest_ckpt / "_meta.json"
        if not meta_file.exists():
            return {"success": False, "message": f"快照 {latest_ckpt.name} 缺失元数据"}

        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            orig_p = Path(meta["original_file"])
            backup_p = Path(meta["backup_file"])
            if not backup_p.exists():
                return {"success": False, "message": f"备份文件不存在: {backup_p}"}

            orig_p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_p, orig_p)
            return {
                "success": True,
                "timestamp": meta.get("timestamp"),
                "restored_file": str(orig_p),
                "message": f"成功将 [{orig_p.name}] 回滚至快照状态 ({meta.get('timestamp')})"
            }
        except Exception as e:
            return {"success": False, "message": f"回滚异常: {e}"}

    def check_permission(self, tool_name: str, level: int, tool_args: Dict[str, Any], interactive: bool = True) -> Tuple[bool, str]:
        """
        评估工具调用权限:
        返回 (is_allowed, reason)

        本方法**只负责策略**（该不该问 / 该不该自动放行）；「怎么问」全部委托给
        `agent.approval` 的审批通道 —— 交互终端走 TTY 卡片，GUI / 网关 / 管道
        走 headless 策略（默认 `deny_all`，与接入通道前逐字节一致）。
        """
        if self.force_allow_all:
            return True, "force_allow_all 开启，测试放行"

        # 1. 如果工具已在本会话中被永久信任 (且非 Level 5 高危)
        # [B4] 用 glob 匹配而非 ``in set``：信任集里可以是 ``mcp_*`` 这样的类别键
        # （用户批准一个 MCP 工具时收口写入，见 approval.session_remember_key）。
        # 普通工具名仍按精确匹配，既有行为不变。
        if tool_name_matches(tool_name, self.session_allowed_tools) and level < PermissionLevel.DANGEROUS:
            return True, "会话已永久信任此工具"

        # 2. Level 0 只读操作：任何模式均全自动放行
        if level == PermissionLevel.READ_ONLY:
            return True, "只读安全操作，自动放行"

        # 3. safe 模式：禁止所有写操作与命令执行
        if self.mode == "safe":
            return False, f"当前处于严格安全模式 (--permission=safe)，已拒绝执行非只读操作 [{tool_name}]"

        # 4. auto 模式：Level 1-3 自动放行（含非交互环境 —— GUI 的 acceptEdits 别名）
        if self.mode == "auto" and level <= PermissionLevel.SHELL_EXEC:
            return True, f"全自动模式 (--permission=auto)，已自动执行 [{tool_name}]"

        # 5. 其余情形交给审批通道：plan 模式用计划审计卡片，其余用普通卡片/策略
        channel = self._select_channel(interactive)
        if self.mode == "plan":
            return channel.request_plan(tool_name, level, tool_args)
        return channel.request(tool_name, level, tool_args)

    def check_session_script_exec(self, script_display: str, tool_args: Dict[str, Any],
                                  interactive: bool = True) -> Tuple[bool, str]:
        """[B2a] 「本会话写入的脚本」执行闸门：**任何模式下都不得自动放行**。

        为什么不能复用 :meth:`check_permission`：``auto`` 模式对 Level 0-3 一律
        自动放行，而「先用 write_file 落一个脚本、再 ``python 脚本.py``」正是靠
        这一步绕过审批执行任意代码的。本闸门因此**不看模式档位**，只按
        「有没有人能批准」决策：
          * 有交互通道（TTY 卡片 / GUI 弹窗 / 网关卡片）→ 弹一次高危审批
            （Level 5 同款卡片：无"本会话记住"选项）；
          * headless（管道 / CI / 无人在场）→ 一律拒绝，文案可辨识。

        **授权记忆不落盘**（刻意不提供"本会话记住"）：批准只在本次调用内生效，
        不写入任何文件。理由：agent 的 ``write_file`` / ``edit_file`` 能写工作区内
        任意路径 —— 工作区内不存在它写不到的"安全存储位置"；而按工具名或路径
        "记住"，会把一次批准放大成"批准该路径之后的任意内容"（脚本可能再次被
        改写）。因此每次执行都重新批准，与 Level 5 高危操作同款语义。
        """
        if self.force_allow_all:
            return True, "force_allow_all 开启，测试放行"

        if self.mode == "safe":
            return False, (f"当前处于严格安全模式 (--permission=safe)，"
                           f"已拒绝执行本会话写入的脚本 [{script_display}]")

        channel = self._select_channel(interactive)
        if not getattr(channel, "is_interactive", False):
            return False, ("本会话写入的脚本禁止在无审批的情况下执行："
                           "非交互（headless）环境无法请求用户审批，"
                           "需在交互终端批准后才能执行。")
        return channel.request("run_command", PermissionLevel.DANGEROUS, tool_args)

    def check_external_read(self, path_display: str, tool_args: Dict[str, Any],
                            interactive: bool = True) -> Tuple[bool, str]:
        """[B2b] 「读取工作区外文件」闸门：**默认拒绝**，交互环境弹一次授权卡。

        与 :meth:`check_session_script_exec` 的异同：
          * 相同：**不看模式档位**（``auto`` 模式不得自动放行）；headless（无人在场）
            一律拒绝且文案可辨识；授权记忆**不落盘**（只存在于进程内存）。
          * 不同：授权粒度是**目录**（批准一次，本会话内同目录的其他文件不再弹卡，
            由 ``Sandbox.register_authorized_read_dir`` 记忆）；卡片按 Level 4 渲染，
            带 [a]「本会话记住并信任此类操作」选项（写入 ``session_allowed_tools``
            后所有外部读取免卡），不是 Level 5 高危卡片。
        """
        if self.force_allow_all:
            return True, "force_allow_all 开启，测试放行"

        # 用户曾选 [a]「本会话记住并信任此类操作」：直接放行，不再弹卡。
        if tool_name_matches(EXTERNAL_READ_TOOL_NAME, self.session_allowed_tools):
            return True, "会话已永久信任外部只读读取"

        if self.mode == "safe":
            return False, (f"当前处于严格安全模式 (--permission=safe)，"
                           f"已拒绝读取工作区外文件 [{path_display}]")

        channel = self._select_channel(interactive)
        if not getattr(channel, "is_interactive", False):
            return False, (
                f"读取工作区外文件 [{path_display}] 需要用户授权，"
                "非交互（headless）环境无法请求审批。两条出路："
                "① 长期授权：在 ky_config.json 的 agent.allowed_extra_paths "
                "中登记该目录，之后直接放行；"
                "② 本次授权：在交互终端中重试，并在弹卡中批准"
                "（批准后本会话内同目录不再询问）。")

        return channel.request(
            EXTERNAL_READ_TOOL_NAME, PermissionLevel.NETWORK,
            {**tool_args,
             "scope": "工作区外只读，批准后本会话内同目录不再询问"})
