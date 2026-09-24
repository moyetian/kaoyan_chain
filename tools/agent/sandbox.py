# -*- coding: utf-8 -*-
r"""
考研学习链 (ky-cli) · 沙箱与安全防护网 (Sandbox & Security Guard)
职责:
1. 路径越界防御 (阻止访问系统敏感目录如 C:\Windows, ~/.ssh 等)
2. 黑名单高危命令硬拦截 (阻止 rm -rf, del /f /s /q, format 等系统破坏性命令)
3. 严格运行于工具层，绝不依赖 Prompt 自觉
"""

import logging
import os
import re
from pathlib import Path
from typing import Optional

# 系统敏感路径黑名单 (Windows & POSIX)
# 敏感凭据目录分量与文件名
SENSITIVE_CREDENTIAL_PARTS = {".ssh", ".aws", ".gnupg"}
SENSITIVE_KEY_PREFIXES = ("id_rsa", "id_ed25519", "id_ecdsa")

# POSIX 系统敏感根目录
POSIX_SENSITIVE_ROOTS = {"/etc", "/boot", "/sys", "/proc"}

# Windows 系统敏感目录前缀
WINDOWS_SENSITIVE_DIRS = (
    r"c:\windows",
    r"c:\program files",
    r"c:\program files (x86)",
    r"c:\boot",
    r"c:\recovery",
    r"c:\system volume information",
)

# 高危命令黑名单 (正则表达式)
DANGEROUS_COMMAND_PATTERNS = [
    # 破坏性删除
    r"\brm\s+-(?:r|f|rf|fr)\b",                   # rm -rf / 或 ~ 或 ./* 或 .
    r"\bdel\s+/[fFsSqQ]+",                        # del /f /s /q
    r"\b(?:rd|rmdir)\s+/[sS]",                    # rd /s, rmdir /s
    r"\bformat\s+[a-zA-Z]:",                      # format c:
    r"\bmkfs\b",                                  # 格式化文件系统
    r"\bdd\s+if=.*of=/dev/",                      # dd 写磁盘
    # 危险重置与提权
    r"\bgit\s+reset\s+--hard\b",                  # git reset --hard 清空工作区
    r"\bgit\s+clean\s+-[fxd]+\b",                 # git clean -f/-fd/-fx 删文件
    r"\bchmod\s+.*777\b",                         # 危险全局提权 (777 权限)
    # 远程管道即时执行
    r"(?:curl|wget)\s+.*\|\s*(?:bash|sh|zsh|powershell|cmd)\b",
    # 系统启停与破坏
    r"\b(?:shutdown|reboot|init\s+0|init\s+6)\b",  # 关机重启
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", # Fork bomb
    r"\bdrop\s+(?:database|schema)\b",            # 删库
    r"\b(?:net\s+user|net\s+localgroup)\b",       # 修改系统用户
]

#: 允许「工作区外只读访问」的扩展名白名单（**授权后可放行**）。
#: [B2b] 语义已收紧为「默认拒绝 + 授权后放行」：命中本白名单只是**可授权候选**，
#: 必须再满足「本会话已授权该目录」或「目录在 allowed_extra_paths 里」才真正放行。
#:   保留的能力：/img <绝对路径> 拍照批改、直接读取桌面上的真题 PDF —— 但首次
#:   读取需在交互终端弹卡授权（同目录后续免问），headless 一律拒绝。
#:   收口手段：.json 被刻意排除（防凭据/密钥外泄）、仅对 read_only=True 生效、
#:   相对路径穿越与敏感凭据路径一律拒绝且**永不可授权**、每次放行都留审计日志。
EXTERNAL_READ_EXTS = {".pdf", ".txt", ".md", ".docx", ".doc",
                      ".png", ".jpg", ".jpeg", ".csv"}

#: [B2a] 进程级「本会话写入」集合：被写类工具（write_file / edit_file）写过或改过
#: 的文件的规范化绝对路径键（``os.path.normcase``，Windows 下大小写/分隔符不敏感）。
#:
#: 为什么是**进程级**而不是 Sandbox 实例级：GUI 的 agent_worker 每处理一条用户消息
#: 就新建一个 AgentRunner（连带新 Sandbox），实例级集合会在消息之间被清空 ——
#: 「第 1 条消息写脚本、第 2 条消息执行」即可绕过闸门。CLI REPL 虽在会话内复用同一个
#: runner，但「本会话」的语义应与 GUI 的「本会话信任」一致（后者挂在跨 runner 复用的
#: 审批通道上）—— 因此统一取进程生命周期（重启进程即清空）。
#:
#: 只存在于**内存**、绝不落盘：agent 的写工具能写工作区内任意路径，工作区内不存在
#: 它写不到的「安全存储位置」（理由详见
#: ``permissions.PermissionManager.check_session_script_exec`` 的 docstring）。
_SESSION_WRITTEN_FILES: set = set()

#: [B2b] 进程级「本会话已授权读取」的工作区外目录集合：用户在审批卡片中批准一次
#: 「读取工作区外文件」后，登记该文件所在目录；本会话内同目录的其他文件不再弹卡。
#: 键与 ``_SESSION_WRITTEN_FILES`` 同款（``os.path.normcase`` 归一）。
#:
#: 为什么是**进程级**而不是 Sandbox 实例级：与 B2a 写入污染集合同理 —— GUI 的
#: agent_worker 每处理一条用户消息就新建一个 AgentRunner（连带新 Sandbox），实例级
#: 集合会在消息之间被清空，「第 1 条消息批准、第 2 条消息再读同目录」就要反复弹卡。
#: CLI REPL 在会话内复用同一个 runner，但「本会话」的语义应与 GUI 的「本会话信任」
#: 一致 —— 因此统一取进程生命周期（重启进程即清空）。
#:
#: 只存在于**内存**、绝不落盘：agent 的写工具能写工作区内任意路径，工作区内不存在
#: 它写不到的「安全存储位置」；且授权状态一旦落盘，就等于把一次交互批准变成跨会话
#: 的持久放行（用户不可见、难以撤销）。
_SESSION_AUTHORIZED_READ_DIRS: set = set()

#: [B2b] 本会话读取过的「工作区外」文件清单（去重保序，供 REPL 退出时汇总展示）。
#: 同样只存在于内存、绝不落盘；只在**实际放行读取**之后登记，被拒的调用不记录。
_SESSION_EXTERNAL_READS: list = []

_LOGGER = logging.getLogger("ky.sandbox")


def _log_external_read(resolved: Path, raw_path) -> None:
    """工作区外只读访问必须留下审计记录。

    默认 WARNING 级（未配置 logging 时会经 lastResort 输出到 stderr），
    这样「谁读了工作区外的什么文件」事后可追溯。
    """
    _LOGGER.warning("沙箱只读豁免：允许读取工作区外文件 %s（原始输入: %s）",
                    resolved, raw_path)


def _record_external_read(resolved: Path) -> None:
    """[B2b] 登记一次**已放行**的「工作区外」读取（去重保序，供会话汇总展示）。"""
    entry = str(resolved)
    if entry not in _SESSION_EXTERNAL_READS:
        _SESSION_EXTERNAL_READS.append(entry)


class SecurityException(PermissionError):
    """沙箱拦截抛出的安全异常"""
    pass

class Sandbox:
    def __init__(self, workspace_root=None, allowed_extra_paths=None):
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else Path.cwd().resolve()
        self.allowed_extra_paths = [Path(p).resolve() for p in (allowed_extra_paths or [])]

    @property
    def session_written_files(self) -> set:
        """[B2a] 进程级「本会话写入」集合（同进程内所有 Sandbox 共享，见模块级说明）。"""
        return _SESSION_WRITTEN_FILES

    @property
    def session_external_reads(self) -> list:
        """[B2b] 本会话读取过的「工作区外」文件清单（副本；进程级共享，见模块级说明）。"""
        return list(_SESSION_EXTERNAL_READS)

    @staticmethod
    def _path_key(resolved_path) -> str:
        """把路径规范化为集合键：绝对路径 + ``normcase``（Windows 大小写/分隔符归一）。

        大小写变体（``tools/Evil.py`` 与 ``python tools/evil.py``）在 Windows 上
        指向同一文件，键必须一并归一，否则污染集合匹配可被大小写绕过。
        """
        return os.path.normcase(str(Path(resolved_path).resolve()))

    def register_written_file(self, resolved_path) -> None:
        """[B2a] 登记「本会话被写类工具写过/改过」的文件。

        由写类工具在**成功落盘之后**调用；被拒绝/未落盘的调用不登记
        （否则会把"本想写但被拦"的路径也钉成污染，产生无谓的审批噪音）。
        """
        _SESSION_WRITTEN_FILES.add(self._path_key(resolved_path))

    def is_session_written(self, resolved_path) -> bool:
        """[B2a] 该路径是否属于「本会话写入」的污染集合。"""
        return self._path_key(resolved_path) in _SESSION_WRITTEN_FILES

    def register_authorized_read_dir(self, dir_path) -> None:
        """[B2b] 登记「本会话已授权读取」的工作区外目录。

        由读取入口在**用户批准之后**调用（一次批准 = 本会话内该目录免再弹卡）。
        登记的是目录而非文件：读取场景下同一目录（如桌面真题集）会被反复读取，
        按文件粒度记忆等于每个文件都弹一次卡。
        """
        _SESSION_AUTHORIZED_READ_DIRS.add(self._path_key(dir_path))

    def is_authorized_read_dir(self, resolved_path) -> bool:
        """[B2b] 该路径是否落在本会话已授权读取的目录内。

        匹配必须带**分隔符边界**：只做前缀比较的话，授权 ``D:/资料`` 会连
        ``D:/资料私密/x.md`` 一起放行（前缀相同但并非同一目录）。
        """
        key = self._path_key(resolved_path)
        return any(
            key == d or key.startswith(d + os.sep)
            for d in _SESSION_AUTHORIZED_READ_DIRS
        )

    def classify_external_read(self, raw_path) -> Optional[Path]:
        """[B2b] 判定「这是一次可授权的工作区外只读读取」还是「应当直接拒绝」。

        返回非 None = 该路径是**只读豁免候选**（只读 + 已存在 + 是文件 + 后缀在
        :data:`EXTERNAL_READ_EXTS` 内，只是尚未获得本会话授权）：调用方可先请求
        审批、批准后重试解析。
        返回 None = 要么本就能读（工作区内 / 白名单目录内 / 已授权目录内），要么
        属于**永不可授权**的拒绝（敏感路径 / 凭据 / 相对穿越 / 非白名单后缀）。
        """
        try:
            self.resolve_safe_path(raw_path, read_only=True)
        except SecurityException as e:
            if getattr(e, "external_read_candidate", False):
                return getattr(e, "resolved_path", None)
            return None
        return None

    def resolve_safe_path(self, raw_path, allow_create=False, read_only=False) -> Path:
        """
        解析并校验路径安全性:
        1. 允许工作区 root 内部的相对与绝对路径
        2. 允许用户显式指定的参考资料外部路径 (allowed_extra_paths)
        3. 拦截敏感系统目录、凭据目录与私钥文件
        4. 拒绝「相对路径穿越出工作区」(如 ../../x.md)

        关于工作区外的只读访问（第 4 步之后的 EXTERNAL_READ_EXTS 分支）：
        [B2b] 现为「**默认拒绝 + 授权后放行**」—— 当 read_only=True 且文件已存在、
        后缀在白名单内时，还必须满足「所在目录已获本会话授权」才放行（授权由审批
        卡片批准后经 :meth:`register_authorized_read_dir` 登记）；放行同时留审计
        日志并计入会话读取汇总。未授权时抛出的 :class:`SecurityException` 会带上
        ``external_read_candidate`` 标记（仅限「只读 + 已存在 + 是文件 + 白名单后缀」
        的候选），供上层 ``classify_external_read`` 识别并走审批；敏感路径 / 凭据 /
        相对穿越等前置拒绝**一律不带该标记**（安全不变式：这些路径永不可授权）。
        """
        if not raw_path:
            raise SecurityException("路径不能为空")

        raw_str = str(raw_path).strip()
        raw_str_norm = raw_str.replace("\\", "/").lower()

        # 0. 跨平台特征检查 (防止在 Linux/POSIX 环境下 Windows 敏感目录被当作相对路径解析)
        for wsd in WINDOWS_SENSITIVE_DIRS:
            wsd_norm = wsd.replace("\\", "/").lower()
            if raw_str_norm == wsd_norm or raw_str_norm.startswith(wsd_norm + "/"):
                raise SecurityException(f"沙箱拦截: 拒绝访问系统敏感路径 [{raw_path}] (命中敏感特征: {wsd})")

        path_parts_raw = set(part.lower() for part in re.split(r"[/\\]+", raw_str))
        if path_parts_raw & SENSITIVE_CREDENTIAL_PARTS:
            raise SecurityException(f"沙箱拦截: 拒绝访问系统敏感凭据目录 [{raw_path}]")
        if any(part.startswith(pfx) for part in path_parts_raw for pfx in SENSITIVE_KEY_PREFIXES):
            raise SecurityException(f"沙箱拦截: 拒绝访问私钥凭据文件 [{raw_path}]")

        is_windows_abs = bool(re.match(r"^[a-zA-Z]:[/\\]", raw_str))
        p = Path(raw_path)
        if not p.is_absolute() and not is_windows_abs:
            resolved = (self.workspace_root / p).resolve()
        else:
            resolved = p.resolve()

        resolved_str = str(resolved).lower()

        # 1. 检查是否触碰敏感凭据目录分量 (.ssh, .aws, .gnupg)
        parts_lower = set(part.lower() for part in resolved.parts)
        if parts_lower & SENSITIVE_CREDENTIAL_PARTS:
            raise SecurityException(f"沙箱拦截: 拒绝访问系统敏感凭据目录 [{resolved}]")

        # 2. 检查是否触碰私钥文件
        lower_name = resolved.name.lower()
        if any(lower_name.startswith(pfx) for pfx in SENSITIVE_KEY_PREFIXES):
            raise SecurityException(f"沙箱拦截: 拒绝访问私钥凭据文件 [{resolved}]")

        # 3. 检查 Windows 系统目录
        for wsd in WINDOWS_SENSITIVE_DIRS:
            if resolved_str == wsd or resolved_str.startswith(wsd + "\\") or resolved_str.startswith(wsd + "/"):
                raise SecurityException(f"沙箱拦截: 拒绝访问系统敏感路径 [{resolved}] (命中敏感特征: {wsd})")

        # 4. 检查 POSIX 系统根目录
        if resolved.is_absolute() and len(resolved.parts) >= 2 and resolved.parts[0] in ("/", "\\"):
            top_part = "/" + resolved.parts[1].lower()
            if top_part in POSIX_SENSITIVE_ROOTS:
                raise SecurityException(f"沙箱拦截: 拒绝访问系统敏感根目录 [{resolved}] (命中敏感特征: {top_part})")
            if top_part == "/root":
                # 若工作区位于 /root 下，允许工作区内部访问，拒绝超出工作区范围的系统文件
                try:
                    resolved.relative_to(self.workspace_root)
                except ValueError:
                    raise SecurityException(f"沙箱拦截: 拒绝访问系统敏感路径 [{resolved}] (命中敏感特征: /root)")

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

            # [安全修复] 相对路径穿越出工作区（如 "../../secret.txt"）一律拒绝。
            # 理由：外部**绝对**路径是人工显式给出的正常用法（下面的只读豁免需要它），
            # 而相对路径逃出工作区没有任何正常使用场景，是越权/注入尝试的典型特征。
            _segs = [seg for seg in re.split(r"[/\\]+", raw_str) if seg]
            if ".." in _segs and not (is_windows_abs or p.is_absolute()):
                raise SecurityException(
                    f"沙箱拦截: 拒绝相对路径穿越出工作区 [{raw_path}]")

            # 只读豁免：仅对「只读」操作生效，且 .json 不再豁免（防凭据外泄）；
            # edit/delete/write 等修改操作严禁穿越到外部。
            # [B2b] 还要求「本会话已授权该目录」—— 默认拒绝，交互批准一次后同目录免问。
            if (
                read_only
                and not is_in_extra
                and not allow_create
                and resolved.exists()
                and resolved.is_file()
            ):
                if (resolved.suffix.lower() in EXTERNAL_READ_EXTS
                        and self.is_authorized_read_dir(resolved)):
                    _log_external_read(resolved, raw_path)   # 留审计记录
                    _record_external_read(resolved)          # 计入会话读取汇总
                    return resolved

            if not is_in_extra:
                err = SecurityException(
                    f"沙箱拦截: 路径超出工作区范围且未获外部授权 [{resolved}]"
                    f"（仅允许已授权目录，或只读访问白名单扩展名的外部文件）")
                # [B2b] 安全不变式：只有「只读 + 未指定创建 + 已存在 + 是文件 +
                # 后缀在白名单内」的候选才带 external_read_candidate 标记 ——
                # 只有这类路径可经交互授权放行。敏感路径 / 凭据 / 相对穿越等
                # **前置 raise** 一律不带该标记，因此永不可授权。
                err.external_read_candidate = bool(
                    read_only
                    and not allow_create
                    and resolved.exists()
                    and resolved.is_file()
                    and resolved.suffix.lower() in EXTERNAL_READ_EXTS
                )
                err.resolved_path = resolved
                raise err

        # [B2b] 白名单目录（allowed_extra_paths）的读取同样计入会话汇总：
        # 它虽不弹卡，但「本会话读过哪些工作区外文件」的清单必须完整。
        if not is_in_workspace and is_in_extra:
            _log_external_read(resolved, raw_path)
            _record_external_read(resolved)

        return resolved

    def check_command_safety(self, command: str) -> None:
        """
        检查命令行是否包含系统破坏性指令
        """
        if not command or not command.strip():
            return

        for pat in DANGEROUS_COMMAND_PATTERNS:
            if re.search(pat, command, re.IGNORECASE):
                raise SecurityException(f"沙箱拦截: 拒绝执行系统高危指令! 命中安全黑名单规则: [{pat}]")
