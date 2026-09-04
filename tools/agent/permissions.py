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

import sys
import json
from typing import Dict, Any, Tuple

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
    def __init__(self, mode: str = "ask", workspace_root=None):
        """
        mode:
          - 'ask': 默认推荐模式。Level 0 自动执行；Level 1-4 提示用户审批；Level 5 必须高亮确认
          - 'auto': 全自动沙箱模式。Level 0-3 自动执行；Level 4-5 询问
          - 'safe': 严格只读模式。只允许 Level 0，其他全部拒绝
          - 'plan': 计划审计模式。Level 0 自动放行；非只读修改强制呈现 Plan 审查卡片并在写前创建 Checkpoint 备份
        """
        from pathlib import Path
        self.mode = mode.lower() if mode in ("ask", "auto", "safe", "plan") else "ask"
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else Path.cwd().resolve()
        self.session_allowed_tools = set()  # 会话内用户选择 [a] 记住允许的工具集合
        self.force_allow_all = False        # 测试与全自动沙箱调试开关
        self.checkpoint_dir = self.workspace_root / ".checkpoint"

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
        meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
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
        """
        if self.force_allow_all:
            return True, "force_allow_all 开启，测试放行"

        # 1. 如果工具已在本会话中被永久信任 (且非 Level 5 高危)
        if tool_name in self.session_allowed_tools and level < PermissionLevel.DANGEROUS:
            return True, "会话已永久信任此工具"

        # 2. Level 0 只读操作：任何模式均全自动放行
        if level == PermissionLevel.READ_ONLY:
            return True, "只读安全操作，自动放行"

        # 3. safe 模式：禁止所有写操作与命令执行
        if self.mode == "safe":
            return False, f"当前处于严格安全模式 (--permission=safe)，已拒绝执行非只读操作 [{tool_name}]"

        # 4. auto 模式：Level 1-3 自动放行
        if self.mode == "auto" and level <= PermissionLevel.SHELL_EXEC:
            return True, f"全自动模式 (--permission=auto)，已自动执行 [{tool_name}]"

        # 5. plan 模式：强制 Plan 审查与 Checkpoint 备份
        if self.mode == "plan":
            if not interactive or not sys.stdin.isatty():
                return False, f"Plan 模式 (--permission=plan) 下非交互环境禁止自动执行写操作 [{tool_name}]，需出具并确认行动计划"
            return self._prompt_plan_approval(tool_name, level, tool_args)

        # 6. 非交互模式 (如单测或后端调用)
        if not interactive or not sys.stdin.isatty():
            if self.mode in ("auto", "ask") and level <= PermissionLevel.SAFE_EDIT:
                return True, "非交互环境安全放行"
            return False, f"非交互环境下无法请求用户批准 Level {level} 操作"

        # 7. 交互式提示用户审批
        return self._prompt_user_approval(tool_name, level, tool_args)

    def _prompt_user_approval(self, tool_name: str, level: int, tool_args: Dict[str, Any]) -> Tuple[bool, str]:
        """渲染 Codex 风格的优雅审批卡片"""
        args_preview = []
        for k, v in tool_args.items():
            val_str = str(v)
            if len(val_str) > 60:
                val_str = val_str[:57] + "..."
            args_preview.append(f"{k}='{val_str}'")
        param_line = ", ".join(args_preview)

        level_desc = LEVEL_NAMES.get(level, f"Level {level}")
        is_danger = (level >= PermissionLevel.DANGEROUS)

        # 终端卡片展示
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
            choice = input(f"👉 请选择审批决定 [y/a/n] (默认 n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消执行。")
            return False, "用户中断审批"

        if choice == "y":
            return True, "用户批准单次执行"
        elif choice == "a" and not is_danger:
            self.session_allowed_tools.add(tool_name)
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

        # 若操作涉及文件修改，写前自动创建快照
        backup_hint = ""
        if target_file and self.workspace_root:
            try:
                full_target = (self.workspace_root / target_file).resolve()
                if full_target.exists():
                    ckpt_file = self.create_checkpoint(full_target)
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
            choice = input(f"👉 是否批准此项行动计划? [y/n] (默认 n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消执行。")
            return False, "用户中断 Plan 审批"

        if choice == "y":
            return True, "用户批准 Plan 模式执行变更"
        else:
            return False, "用户拒绝 Plan 模式该项执行计划"
