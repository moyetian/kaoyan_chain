# -*- coding: utf-8 -*-
r"""
考研学习链 (ky-cli) · 沙箱与安全防护网 (Sandbox & Security Guard)
职责:
1. 路径越界防御 (阻止访问系统敏感目录如 C:\Windows, ~/.ssh 等)
2. 黑名单高危命令硬拦截 (阻止 rm -rf, del /f /s /q, format 等系统破坏性命令)
3. 严格运行于工具层，绝不依赖 Prompt 自觉
"""

import os
import re
from pathlib import Path

# 系统敏感路径黑名单 (Windows & POSIX)
SENSITIVE_PATHS = [
    # Windows
    r"c:\windows",
    r"c:\program files",
    r"c:\program files (x86)",
    r"c:\boot",
    r"c:\recovery",
    r"c:\system volume information",
    # POSIX
    "/etc",
    "/boot",
    "/sys",
    "/proc",
    "/root",
    # 敏感认证目录
    ".ssh",
    ".aws",
    ".gnupg",
    "id_rsa",
    "id_ed25519"
]

# 高危命令黑名单 (正则表达式)
DANGEROUS_COMMAND_PATTERNS = [
    r"\brm\s+-(?:r|f|rf|fr)\s+[/~]",              # rm -rf / 或 ~
    r"\bdel\s+/[fF]\s+/[sS]\s+/[qQ]\s+[cC]:",     # del /f /s /q c:\
    r"\bformat\s+[a-zA-Z]:",                      # format c:
    r"\bmkfs\b",                                  # 格式化文件系统
    r"\bdd\s+if=.*of=/dev/",                      # dd 写磁盘
    r"\b(?:shutdown|reboot|init\s+0|init\s+6)\b",  # 关机重启
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", # Fork bomb
    r"\bdrop\s+(?:database|schema)\b",            # 删库
    r"\b(?:net\s+user|net\s+localgroup)\b",       # 修改系统用户
]

class SecurityException(PermissionError):
    """沙箱拦截抛出的安全异常"""
    pass

class Sandbox:
    def __init__(self, workspace_root=None, allowed_extra_paths=None):
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else Path.cwd().resolve()
        self.allowed_extra_paths = [Path(p).resolve() for p in (allowed_extra_paths or [])]

    def resolve_safe_path(self, raw_path, allow_create=False) -> Path:
        """
        解析并校验路径安全性:
        1. 允许工作区 root 内部的相对与绝对路径
        2. 允许用户显式指定的参考资料外部路径
        3. 拦截敏感系统目录穿越
        """
        if not raw_path:
            raise SecurityException("路径不能为空")

        p = Path(raw_path)
        if not p.is_absolute():
            resolved = (self.workspace_root / p).resolve()
        else:
            resolved = p.resolve()

        resolved_str = str(resolved).lower()

        # 检查是否触碰系统敏感目录
        for sp in SENSITIVE_PATHS:
            sp_norm = sp.lower()
            if sp_norm in resolved_str:
                raise SecurityException(f"沙箱拦截: 拒绝访问系统敏感路径 [{resolved}] (命中敏感特征: {sp})")

        # 检查是否在工作区内部，或在用户额外授权的参考资料路径内
        is_in_workspace = False
        try:
            resolved.relative_to(self.workspace_root)
            is_in_workspace = True
        except ValueError:
            pass

        if not is_in_workspace:
            is_in_extra = any(
                str(resolved).lower().startswith(str(extra_p).lower())
                for extra_p in self.allowed_extra_paths
            )
            # 若是读取已存在的文件（如用户外部放置的考研真题 PDF），且非系统敏感目录，允许只读访问
            if not is_in_extra and not allow_create and resolved.exists() and resolved.is_file():
                # 额外允许考生外部合法的考研复习文件（pdf/doc/txt/md/jpg/png）
                valid_exts = {".pdf", ".txt", ".md", ".docx", ".doc", ".png", ".jpg", ".jpeg", ".csv", ".json"}
                if resolved.suffix.lower() in valid_exts:
                    return resolved

            if not is_in_extra:
                raise SecurityException(f"沙箱拦截: 路径超出工作区范围且未获外部授权 [{resolved}]")

        return resolved

    def check_command_safety(self, command: str) -> None:
        """
        检查命令行是否包含系统破坏性指令
        """
        if not command or not command.strip():
            return

        cmd_lower = command.lower()
        for pat in DANGEROUS_COMMAND_PATTERNS:
            if re.search(pat, cmd_lower):
                raise SecurityException(f"沙箱拦截: 拒绝执行系统高危指令! 命中安全黑名单规则: [{pat}]")
