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
import shutil
import time
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

try:  # 双导入路径兼容（项目同时存在 tools.X 与 X 两种导入方式）
    from ky_io import atomic_write_text  # noqa: E402
except ImportError:  # pragma: no cover
    from tools.ky_io import atomic_write_text  # noqa: E402

try:  # 双导入路径兼容
    from .approval import (  # noqa: E402
        DEFAULT_HEADLESS_POLICY,
        extract_target_path,
        resolve_headless_allow_tools,
        resolve_headless_policy,
        select_channel,
        tool_name_matches,
    )
except ImportError:  # pragma: no cover
    from agent.approval import (  # type: ignore # noqa: E402
        DEFAULT_HEADLESS_POLICY,
        extract_target_path,
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

#: [D0] 写前快照覆盖的文件工具：有 ``path`` 语义、会改动工作区文件。
#: ``run_command`` 经 shell 也能改文件，但调用前无法预知它会碰哪些文件 ——
#: 明确不在覆盖范围（文档与帮助文案同步声明）。
SNAPSHOT_TOOL_NAMES = ("write_file", "edit_file", "delete_file")

#: [D0] 检查点目录名前缀（旧版已用同名前缀，保持不变以便向后兼容）。
CHECKPOINT_PREFIX = "ckpt_"


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
    READ_ONLY = 0      # 只读 (read_file, list_dir, grep, read_exam_paper)
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
        # [D0] 当前写入批次（惰性创建，见 begin_write_batch / _ensure_batch_dir）：
        # 批次 = 一次 AgentRunner turn / 一次用户动作；批次内同一文件只快照
        # 首次触碰前的状态。不显式开启时，本进程内隐式共用一个批次。
        self._batch_dir: Optional[Path] = None
        self._batch_label: str = ""
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
        """Plan 卡片用：把相对路径解析成工作区内文件并建快照（不存在则返回 None）。

        [D0] 与通用写前钩子共用同一批次目录：同一文件在同一批次里只会被快照
        一次（首次触碰前的状态），Plan 卡片与权限钩子因此不会重复建快照。
        """
        try:
            full_target = (self.workspace_root / target_file).resolve()
        except Exception:
            return None
        if not full_target.exists():
            return None
        return self.create_checkpoint(full_target)

    # ── [D0] 写前快照与按文件 / 按批回滚（通用能力，不限 plan 模式） ──────

    def begin_write_batch(self, label: str = "") -> None:
        """开启一个写入批次（一次 AgentRunner turn / 一次用户动作）。

        目录**惰性创建**：批次内一次写入都没有时不产生空目录。批次内的多次
        写入共享同一快照目录，同一文件只记录**首次触碰前**的状态 —— 整批
        回滚即回到批次开始前。不显式开启时，本进程内隐式共用一个批次。
        """
        self._batch_dir = None
        self._batch_label = str(label or "")

    def end_write_batch(self) -> None:
        """结束当前写入批次；下一次写入另起一个快照目录。"""
        self._batch_dir = None
        self._batch_label = ""

    def _ensure_batch_dir(self) -> Path:
        """取当前批次目录；没有则按时间戳新建（同秒冲突自动加序号）。"""
        if self._batch_dir is not None:
            return self._batch_dir
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        candidate = self.checkpoint_dir / f"{CHECKPOINT_PREFIX}{ts}"
        seq = 1
        while candidate.exists():
            seq += 1
            candidate = self.checkpoint_dir / f"{CHECKPOINT_PREFIX}{ts}_{seq:02d}"
        candidate.mkdir(parents=True, exist_ok=True)
        self._batch_dir = candidate
        self._write_manifest(candidate, {
            "checkpoint": candidate.name,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "label": self._batch_label,
            "entries": [],
        })
        return candidate

    @staticmethod
    def _manifest_path(ckpt_dir: Path) -> Path:
        return ckpt_dir / "manifest.json"

    def _read_manifest(self, ckpt_dir: Path) -> Dict[str, Any]:
        mf = self._manifest_path(ckpt_dir)
        if mf.is_file():
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
            except Exception:
                data = None
            if isinstance(data, dict) and isinstance(data.get("entries"), list):
                data["entries"] = [e for e in data["entries"] if isinstance(e, dict)]
                return data
        return {"checkpoint": ckpt_dir.name, "created_at": "", "label": "", "entries": []}

    def _write_manifest(self, ckpt_dir: Path, manifest: Dict[str, Any]) -> None:
        atomic_write_text(self._manifest_path(ckpt_dir),
                          json.dumps(manifest, ensure_ascii=False, indent=2))

    def _mirror_meta(self, ckpt_dir: Path, entry: Dict[str, Any]) -> None:
        """镜像写 ``_meta.json``（最新条目）——旧版工具 / 旧版恢复路径仍可读。"""
        meta = {
            "timestamp": ckpt_dir.name[len(CHECKPOINT_PREFIX):],
            "original_file": entry.get("original_file", ""),
            "relative_file": entry.get("relative_file", ""),
            "backup_file": entry.get("backup_file", ""),
        }
        atomic_write_text(ckpt_dir / "_meta.json",
                          json.dumps(meta, ensure_ascii=False, indent=2))

    def _read_entries(self, ckpt_dir: Path) -> list:
        """读检查点条目；兼容旧格式（只有 ``_meta.json`` 的单条记录）。"""
        entries = self._read_manifest(ckpt_dir).get("entries") or []
        if entries:
            return entries
        meta_file = ckpt_dir / "_meta.json"
        if meta_file.is_file():
            try:
                one = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                one = None
            if isinstance(one, dict) and one.get("relative_file"):
                one.setdefault("existed", True)
                return [one]
        return []

    def create_checkpoint(self, file_path, reason: str = "") -> str:
        """在修改文件前创建快照备份（[D0] 通用能力：任何模式、任何调用方）。

        返回备份文件路径；文件尚不存在（本次将新建）时返回 ``""``，但**仍记录
        条目**（``existed=False``），回滚时按「删除该新建文件」处理。
        同一批次内同一文件重复调用只记录一次（首次触碰前的状态），返回既有
        备份路径 —— Plan 卡片与通用权限钩子因此不会重复建快照。
        """
        try:
            fp = Path(file_path).resolve()
        except Exception:
            return ""
        try:
            rel_p = fp.relative_to(self.workspace_root)
        except Exception:
            # 工作区外不快照：沙箱本就会拒绝写，快照也无处安放。
            return ""

        ckpt_dir = self._ensure_batch_dir()
        manifest = self._read_manifest(ckpt_dir)
        rel_key = rel_p.as_posix()
        for entry in manifest["entries"]:
            if str(entry.get("relative_file")) == rel_key:
                return str(entry.get("backup_file") or "")

        entry: Dict[str, Any] = {
            "relative_file": rel_key,
            "original_file": str(fp),
            "existed": fp.is_file(),
            "backup_file": "",
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if entry["existed"]:
            backup_file = ckpt_dir / rel_p
            backup_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(fp, backup_file)
            entry["backup_file"] = str(backup_file)
        manifest["entries"].append(entry)
        self._write_manifest(ckpt_dir, manifest)
        self._mirror_meta(ckpt_dir, entry)
        return entry["backup_file"]

    def list_checkpoints(self) -> list:
        """列出全部检查点（新 → 旧），含条目与批次标签。"""
        if not self.checkpoint_dir.is_dir():
            return []
        out = []
        for d in sorted(self.checkpoint_dir.iterdir(), reverse=True):
            if not (d.is_dir() and d.name.startswith(CHECKPOINT_PREFIX)):
                continue
            manifest = self._read_manifest(d)
            entries = self._read_entries(d)
            out.append({
                "checkpoint": d.name,
                "created_at": manifest.get("created_at", ""),
                "label": manifest.get("label", ""),
                "files": [str(e.get("relative_file") or "") for e in entries],
                "entries": entries,
            })
        return out

    def _resolve_checkpoint(self, name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        for ck in self.list_checkpoints():
            if name is None or ck["checkpoint"] == str(name):
                return ck
        return None

    @staticmethod
    def _norm_rel(text: str) -> str:
        return str(text or "").replace("\\", "/").lstrip("./")

    def _entry_matches(self, entry: Dict[str, Any], wanted: list) -> bool:
        rel = self._norm_rel(entry.get("relative_file") or "")
        if not rel:
            return False
        for w in wanted:
            if rel == w or rel.endswith("/" + w):
                return True
        return False

    def restore_checkpoint(self, checkpoint: Optional[str] = None,
                           files: Optional[list] = None,
                           dry_run: bool = False) -> Dict[str, Any]:
        """按检查点 / 按文件回滚（[D0] 通用能力）。

        * ``checkpoint=None`` → 最近一次检查点；否则按目录名精确指定；
        * ``files`` 给出时只回滚匹配条目（相对路径精确匹配，或以 ``/`` 为界的
          后缀匹配，如 ``loop.py``）；从**新到旧**扫描检查点，取第一个命中的；
        * ``existed=False`` 的条目（快照时尚不存在）回滚 = 删除该文件；
        * ``dry_run=True`` 只报告将执行的动作，不动磁盘。
        """
        if files:
            wanted = [self._norm_rel(f) for f in files if str(f).strip()]
            ck: Optional[Dict[str, Any]] = None
            targets: list = []
            for cand in self.list_checkpoints():
                hits = [e for e in cand["entries"] if self._entry_matches(e, wanted)]
                if hits:
                    ck, targets = cand, hits
                    break
            if ck is None:
                return {"success": False,
                        "message": f"未在任何检查点中找到文件：{', '.join(wanted)}"}
        else:
            ck = self._resolve_checkpoint(checkpoint)
            if ck is None:
                if checkpoint:
                    return {"success": False, "message": f"未找到检查点 {checkpoint}"}
                return {"success": False, "message": "未找到任何 Checkpoint 快照备份"}
            targets = list(ck["entries"])
        if not targets:
            return {"success": False, "message": f"检查点 {ck['checkpoint']} 内没有可回滚条目"}

        restored, deleted, skipped = [], [], []
        for entry in targets:
            rel = str(entry.get("relative_file") or "")
            if not rel:
                skipped.append({"file": rel, "reason": "条目缺少 relative_file"})
                continue
            target = (self.workspace_root / Path(rel)).resolve()
            # 双保险：只动工作区内（防止清单被手工篡改后越界删/写）
            try:
                target.relative_to(self.workspace_root)
            except Exception:
                skipped.append({"file": rel, "reason": "目标在工作区外，已跳过"})
                continue
            if entry.get("existed", True):
                backup = Path(str(entry.get("backup_file") or ""))
                if not backup.is_file():
                    skipped.append({"file": rel, "reason": "备份文件缺失"})
                    continue
                if not dry_run:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, target)
                restored.append(rel)
            else:
                if target.is_file():
                    if not dry_run:
                        target.unlink()
                    deleted.append(rel)
                else:
                    skipped.append({"file": rel, "reason": "文件不存在（无需删除）"})

        parts = []
        if restored:
            parts.append(f"还原 {len(restored)} 个文件")
        if deleted:
            parts.append(f"删除 {len(deleted)} 个新建文件")
        if skipped:
            parts.append(f"跳过 {len(skipped)} 项")
        action = "将" if dry_run else "已"
        message = (f"{action}按检查点 [{ck['checkpoint']}] {'、'.join(parts) or '无动作'}"
                   + ("（dry-run，未动磁盘）" if dry_run else ""))
        return {
            "success": bool(restored or deleted),
            "checkpoint": ck["checkpoint"],
            "restored": restored,
            "deleted": deleted,
            "skipped": skipped,
            "dry_run": bool(dry_run),
            "message": message,
        }

    def restore_last_checkpoint(self) -> Dict[str, Any]:
        """回滚最近一次检查点（兼容旧入口：等价于 ``restore_checkpoint()``）。"""
        res = self.restore_checkpoint()
        if res.get("success"):
            names = [Path(p).name for p in res.get("restored", [])]
            names += [Path(p).name for p in res.get("deleted", [])]
            res["message"] = (f"成功将 [{', '.join(names)}] 回滚至快照状态"
                              f" ({res.get('checkpoint')})")
        return res

    def _snapshot_before_write(self, tool_name: str, tool_args: Dict[str, Any]) -> None:
        """[D0] 文件写工具的通用写前快照（任何模式、任何调用方）。

        快照失败绝不影响审批与执行（尽力而为）；工作区外目标由
        :meth:`create_checkpoint` 自行跳过。
        """
        if tool_name not in SNAPSHOT_TOOL_NAMES:
            return
        target = extract_target_path(tool_args)
        if not target:
            return
        try:
            self.create_checkpoint(self.workspace_root / target)
        except Exception:
            pass

    def check_permission(self, tool_name: str, level: int, tool_args: Dict[str, Any], interactive: bool = True) -> Tuple[bool, str]:
        """
        评估工具调用权限:
        返回 (is_allowed, reason)

        本方法**只负责策略**（该不该问 / 该不该自动放行）；「怎么问」全部委托给
        `agent.approval` 的审批通道 —— 交互终端走 TTY 卡片，GUI / 网关 / 管道
        走 headless 策略（默认 `deny_all`，与接入通道前逐字节一致）。

        [D0] 批准后、执行前，文件写工具（write_file / edit_file / delete_file）
        一律做**写前快照**（任何模式，不限 plan）——「怎么回滚」见
        :meth:`restore_checkpoint` / :meth:`restore_last_checkpoint`。
        """
        allowed, reason = self._decide_permission(tool_name, level, tool_args, interactive)
        if allowed:
            self._snapshot_before_write(tool_name, tool_args)
        return allowed, reason

    def _decide_permission(self, tool_name: str, level: int, tool_args: Dict[str, Any], interactive: bool = True) -> Tuple[bool, str]:
        """策略判定主体（[D0] 拆出：快照钩子统一挂在 :meth:`check_permission`）。

        [审计 2026-09-30 P0-1/P1-2] 判定顺序修复：``safe`` 模式判定**前移**到
        「会话信任集」与「Level 0 短路」之前。此前顺序下：
          * 信任集短路（``level < DANGEROUS`` 即放行）写在 safe 判定之前 ——
            一旦 ``session_allowed_tools`` 被填充（GUI 侧共享 set / 先以 auto
            批准过），切到 safe 后工具仍被放行，safe 语义被绕过；
          * Level 0 无条件放行写在 safe 判定之前 —— 工具被误标为只读时
            （如 verify_math 被标 READ_ONLY）最严格模式也拦不住。
        修复后 safe 只放行 Level 0 只读操作（保留「只读工具在任何模式可用」的
        既有契约），其余一律拒绝且不受信任集影响；信任集只在非 safe 模式生效。
        """
        if self.force_allow_all:
            return True, "force_allow_all 开启，测试放行"

        # 1. safe 模式：最严格档位，先于信任集与 Level 0 短路判定。
        if self.mode == "safe":
            if level == PermissionLevel.READ_ONLY:
                return True, "只读安全操作，自动放行"
            return False, f"当前处于严格安全模式 (--permission=safe)，已拒绝执行非只读操作 [{tool_name}]"

        # 2. 如果工具已在本会话中被永久信任 (且非 Level 5 高危)。信任集仍
        # 支持既有 glob 配置，但新 MCP 审批只写完整工具名，避免一次“本会话
        # 记住”把全部外部 server 工具永久放行。
        if tool_name_matches(tool_name, self.session_allowed_tools) and level < PermissionLevel.DANGEROUS:
            return True, "会话已永久信任此工具"

        # 3. Level 0 只读操作：非 safe 模式均全自动放行
        if level == PermissionLevel.READ_ONLY:
            return True, "只读安全操作，自动放行"

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
                           "需在交互终端批准后才能执行。"
                           "【替代路径】请改用内置工具完成任务（如 read_file 读取文件内容）。")
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

        # [审计 2026-09-30 P1-2] safe 判定前移：此前「会话信任集」短路写在 safe 之前，
        # 一旦 session_allowed_tools 含外部读键（先以其它模式批准过），safe 模式
        # 仍会放行外部读取 —— 最严格档位不得被信任集绕过。非 safe 模式行为不变。
        if self.mode == "safe":
            return False, (f"当前处于严格安全模式 (--permission=safe)，"
                           f"已拒绝读取工作区外文件 [{path_display}]")

        # 用户曾选 [a]「本会话记住并信任此类操作」：直接放行，不再弹卡。
        if tool_name_matches(EXTERNAL_READ_TOOL_NAME, self.session_allowed_tools):
            return True, "会话已永久信任外部只读读取"

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
