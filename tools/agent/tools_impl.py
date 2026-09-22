# -*- coding: utf-8 -*-
"""
考研学习链 (ky-cli) · 标准工具实现库 (Tools Implementation & Registry)
包含:
1. 文件工具 (read_file, write_file, edit_file, delete_file, list_directory, search_files)
2. 搜索工具 (grep)
3. Shell与系统工具 (run_command)
4. Git 工具 (git_status, git_diff, git_log)
5. 网络工具 (fetch_url)
6. 考研专属能力工具 (read_exam_paper, verify_math, socratic_hint, log_mistake, review_mistakes)
"""

import os
import re
import sys
import json
import fnmatch
import subprocess
import urllib.request
from pathlib import Path
from typing import Dict, Any, Callable, List, Optional

from .sandbox import Sandbox, SecurityException
from .permissions import PermissionLevel, PermissionManager

try:  # 双导入路径兼容（项目同时存在 tools.X 与 X 两种导入方式）
    from ky_io import atomic_write_text  # noqa: E402
except ImportError:  # pragma: no cover
    from tools.ky_io import atomic_write_text  # noqa: E402

try:  # 笔记锁定闸门：frontmatter 中 locked: true 的卡片禁止被自动改写
    from note_lock import NoteLockedError, assert_writable  # noqa: E402
except ImportError:  # pragma: no cover - 兼容 tools. 包式导入
    try:
        from tools.note_lock import NoteLockedError, assert_writable  # type: ignore
    except ImportError:  # pragma: no cover - 极简环境下退化为不设闸门
        NoteLockedError = None  # type: ignore
        assert_writable = None  # type: ignore

try:  # 网络访问安全（SSRF 防护 + 安全重定向）与解压体积上限
    from net_guard import UnsafeURLError, safe_urlopen  # noqa: E402
except ImportError:  # pragma: no cover - 兼容 tools. 包式导入
    from tools.net_guard import UnsafeURLError, safe_urlopen  # type: ignore

# 引入现有考研 Skills 模块
ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = ROOT / "tools" / "skills"
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

try:
    from skills import math_verifier
    from skills import socratic_tutor
    from skills import error_logger
    from skills import variant_retriever
    from skills import exam_composer
    from skills import school_scout
    from skills import material_ingestion
except ImportError:
    try:
        from tools.skills import math_verifier, socratic_tutor, error_logger, variant_retriever, exam_composer, school_scout, material_ingestion
    except ImportError:
        math_verifier = None
        socratic_tutor = None
        error_logger = None
        variant_retriever = None
        exam_composer = None
        school_scout = None
        material_ingestion = None


def _get_pdf_extractor():
    """惰性获取 PDF 抽取技能。

    [C2 修复] 本模块被 `agent/__init__.py` 在 CLI 启动早期导入，此前把
    pdf_extractor 放进上面的 eager 列表，会连带拉起 pypdf + cryptography，
    使 skills 包的惰性导入形同虚设。改为按需获取（仅 `/pdf`、PDF 类工具用）。
    """
    try:
        from skills import pdf_extractor
    except ImportError:
        try:
            from tools.skills import pdf_extractor
        except ImportError:
            return None
    return pdf_extractor

try:
    import intelligence
except ImportError:
    try:
        from tools import intelligence
    except ImportError:
        intelligence = None

# ─────────────────────────────────────────────────────────────────────
# [P0 修复] run_command 路径参数沙箱校验
# 只读命令 (ls/cat/head/tail/wc/grep) 与 git 的位置参数此前**完全不过沙箱**：
# `cat /etc/passwd`、`cat C:/Users/x/.ssh/id_rsa`、`cat ../../outside.txt` 都能
# 读到工作区外任意文件（实测确认，连 resolve_safe_path 明确拦截的 .json 也读到了）。
# 这里对 argv 中「像路径」的 token 逐个做 resolve_safe_path 校验。
# ─────────────────────────────────────────────────────────────────────
_WIN_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_PATH_SEP_RE = re.compile(r"[/\\]")
#: 形如 a.txt / 真题.pdf / my-file.md 的裸文件名（无目录分隔符）
_BARE_FILENAME_RE = re.compile(r"^[\w\-. ]+\.[A-Za-z0-9]{1,8}$")


def _strip_token_quotes(token: str) -> str:
    """去掉 shlex 在 Windows(posix=False) 下保留的首尾引号。"""
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
        return token[1:-1]
    return token


def _split_option_value(token: str) -> Optional[str]:
    """从 ``--opt=VALUE`` 里取出 ``VALUE``；非此形式返回 ``None``。

    [缺陷修复·选项内嵌路径] ``grep --file=/etc/passwd x`` /
    ``wc --files0-from=/etc/passwd`` 这类写法把路径塞进了选项的 ``=`` 右侧，
    而 ``_looks_like_path_token`` 对「以 ``-`` 开头」的 token 一律放行 ——
    于是整条位置参数沙箱被绕过。这里把值拆出来交给调用方走同一套路径判定。

    只认 ``--`` 开头的 GNU 长选项且恰有一个 ``=``：``-rn``（无 ``=``）与
    ``--color=auto``（值非路径）都不会被误判。返回的原始值仍要再过
    :func:`_looks_like_path_token` 才会被当作路径处理。
    """
    if not token.startswith("--"):
        return None
    _opt, eq, value = token.partition("=")
    if not eq or not value:
        return None
    return value


def _looks_like_path_token(token: str) -> bool:
    """判断命令行 token 是否「像路径」，需要过沙箱校验。

    判定规则（宁严勿漏，同时避开选项与纯关键词）：
      1. 以 ``-`` 开头 → 选项，跳过（``--opt=VALUE`` 的取值由调用方拆出后再判）；
      2. 以 ``~`` 开头 → 视为路径（shell=False 下不会被展开，属无效/越权写法）；
      3. 含 ``/`` 或 ``\\`` → 路径；
      4. 形如 Windows 盘符 ``C:`` → 路径；
      5. 形如裸文件名 ``a.txt`` / ``真题.pdf`` → 路径。

    其余 token（如 ``grep -rn subprocess`` 里的关键词 ``subprocess``、``wc -l`` 的
    ``5``）一律放行 —— 它们既非选项也不像路径，交给被调用的程序自行处理。
    """
    if not token or token.startswith("-"):
        return False
    if token.startswith("~"):
        return True
    if _PATH_SEP_RE.search(token):
        return True
    if _WIN_DRIVE_RE.match(token):
        return True
    return bool(_BARE_FILENAME_RE.match(token))


def _is_inside_sandbox(sandbox: Sandbox, resolved: Path) -> bool:
    """路径是否落在工作区内或用户显式授权的额外目录内。"""
    try:
        resolved.resolve().relative_to(sandbox.workspace_root)
        return True
    except ValueError:
        pass
    resolved_str = str(resolved).lower()
    return any(resolved_str.startswith(str(extra).lower())
               for extra in sandbox.allowed_extra_paths)


def _note_lock_error(path: Path) -> Optional[str]:
    """笔记锁定闸门：frontmatter ``locked: true`` 的笔记禁止被自动改写。

    只对 Markdown 笔记生效（二进制/其他格式不受影响）。
    返回 ``None`` 表示放行，否则返回应回给模型的错误文案。
    """
    # [G10 修复·兜底脆弱] 环境缺 note_lock 时 assert_writable 与 NoteLockedError
    # 同源被置 None（见本文件顶部导入块）。旧实现只判 assert_writable，靠前置
    # 早退绕过下面的 except NoteLockedError —— 属脆弱耦合；两个名字一并判空。
    if assert_writable is None or NoteLockedError is None:  # pragma: no cover - 环境缺 note_lock
        return None
    if path.suffix.lower() not in (".md", ".markdown"):
        return None
    try:
        assert_writable(path)
    except NoteLockedError as e:  # type: ignore[misc]
        return f"Error: 笔记已锁定，拒绝改写 —— {e}"
    return None


class ToolDefinition:
    # level 可为 int，也可为 Callable[[dict], int]（按 action 动态定级）
    def __init__(self, name: str, desc: str, params_schema: Dict[str, Any], func: Callable, level):
        self.name = name
        self.desc = desc
        self.params_schema = params_schema
        self.func = func
        self.level = level

    def to_openai_dict(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.desc,
                "parameters": self.params_schema
            }
        }

class ToolRegistry:
    def __init__(self, sandbox: Sandbox, permissions: PermissionManager, memory_manager=None):
        self.sandbox = sandbox
        self.permissions = permissions
        self.memory_manager = memory_manager
        self.tools: Dict[str, ToolDefinition] = {}
        self._register_all_tools()

    def register(self, name: str, desc: str, params_schema: Dict[str, Any], level):
        def decorator(func: Callable):
            self.tools[name] = ToolDefinition(name, desc, params_schema, func, level)
            return func
        return decorator

    def get_openai_tools(self) -> List[Dict[str, Any]]:
        return [t.to_openai_dict() for t in self.tools.values()]

    def execute_tool(self, name: str, args: Dict[str, Any], interactive: bool = True) -> str:
        """统一执行入口: 经过沙箱与权限验证"""
        tool_def = self.tools.get(name)
        if not tool_def:
            return f"Error: 未知工具 [{name}]"

        # 1. 权限审批检查
        # [缺陷修复·读操作被当写操作拦截] level 现支持「可调用」形式：
        # 同一个工具的不同 action 风险并不相同（如 manage_memory 的 read 是零风险
        # 只读，write/append 才是写操作）。旧实现只支持静态 level，于是
        # manage_memory(action='read') 在非交互环境被当成写操作直接拒绝，
        # Agent 连会话记忆都读不到。
        level = tool_def.level
        if callable(level):
            try:
                level = level(args)
            except Exception:
                level = PermissionLevel.SAFE_EDIT   # 判定失败则保守按写操作处理
        allowed, reason = self.permissions.check_permission(name, level, args, interactive=interactive)
        if not allowed:
            return f"PermissionDenied: 操作被拦截 ({reason})"

        # 2. 执行工具
        try:
            result = tool_def.func(**args)
            return str(result)
        except SecurityException as se:
            return f"SecurityError: {se}"
        except Exception as e:
            return f"ExecutionError in [{name}]: {type(e).__name__} - {str(e)}"

    def _register_all_tools(self):
        # ─────────────────────────────────────────────────────────────
        # 1. 文件工具
        # ─────────────────────────────────────────────────────────────

        @self.register(
            name="read_file",
            desc="读取本地文本或PDF文件。若路径为.pdf，将自动提取前若干页或指定页码的文本内容。",
            params_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目标文件相对或绝对路径"},
                    "offset": {"type": "integer", "description": "起始字符偏移行 (默认0)"},
                    "limit": {"type": "integer", "description": "最大读取行数/字数 (默认2000)"}
                },
                "required": ["path"]
            },
            level=PermissionLevel.READ_ONLY
        )
        def read_file(path: str, offset: int = 0, limit: int = 2000) -> str:
            p = self.sandbox.resolve_safe_path(path, read_only=True)
            if not p.exists():
                return f"Error: 文件不存在 [{p}]"

            # 智能 PDF 格式处理
            if p.suffix.lower() == ".pdf":
                pdf_extractor = _get_pdf_extractor()
                if pdf_extractor:
                    pdf_info = pdf_extractor.extract_pdf_pages(str(p), max_pages=8)
                    if pdf_info.get("success"):
                        pages_txt = "\n".join([f"--- 第 {pg['page']} 页 ---\n{pg['text']}" for pg in pdf_info.get("pages", [])])
                        return f"【PDF文档自动提取: {p.name} (共 {pdf_info.get('total_pages', 0)} 页，提取前8页)】\n\n{pages_txt[:limit]}"
                    else:
                        return f"PDF提取失败: {pdf_info.get('error')}"
                return f"Error: 未检测到 PDF 提取模块"

            # 纯文本读取
            for enc in ("utf-8", "utf-8-sig", "gbk"):
                try:
                    lines = p.read_text(encoding=enc).splitlines()
                    selected = lines[offset: offset + limit]
                    return "\n".join(selected)
                except UnicodeDecodeError:
                    continue
            return f"Error: 文件解码失败，请确认是否为有效文本格式"

        @self.register(
            name="write_file",
            desc="新建或覆盖写入文件。在创建错题记录、规划表或作业文件时使用。",
            params_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目标文件路径"},
                    "content": {"type": "string", "description": "待写入的完整文本内容"},
                    "overwrite": {"type": "boolean", "description": "若文件已存在是否覆盖 (默认False)"}
                },
                "required": ["path", "content"]
            },
            level=PermissionLevel.SAFE_EDIT
        )
        def write_file(path: str, content: str, overwrite: bool = False) -> str:
            p = self.sandbox.resolve_safe_path(path, allow_create=True, read_only=False)
            if p.exists() and not overwrite:
                return f"Error: 文件已存在且 overwrite=False [{p}]"
            # [P2 修复] 学员标注 locked: true 的笔记此前可被 write_file 静默覆盖
            # （assert_writable 只接在 error_logger 一条路径上）。
            locked_err = _note_lock_error(p)
            if locked_err:
                return locked_err
            p.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(p, content)
            return f"Success: 成功写入文件 [{p.name}] ({len(content)} 字符)"

        @self.register(
            name="edit_file",
            desc="精确替换文件中的特定文本块，实现安全的文件修改。",
            params_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目标文件路径"},
                    "target_content": {"type": "string", "description": "需要被替换的原文内容 (必须精确匹配)"},
                    "replacement": {"type": "string", "description": "替换后的新内容"}
                },
                "required": ["path", "target_content", "replacement"]
            },
            level=PermissionLevel.SAFE_EDIT
        )
        def edit_file(path: str, target_content: str, replacement: str) -> str:
            p = self.sandbox.resolve_safe_path(path, read_only=False)
            if not p.exists():
                return f"Error: 文件不存在 [{p}]"
            # [P2 修复] 同上：锁定的笔记不得被 edit_file 改写。
            locked_err = _note_lock_error(p)
            if locked_err:
                return locked_err
            raw = p.read_text(encoding="utf-8")
            if target_content not in raw:
                return f"Error: 在文件中未找到指定的 target_content 文本"
            updated = raw.replace(target_content, replacement, 1)
            atomic_write_text(p, updated)
            return f"Success: 成功修改文件 [{p.name}]"

        @self.register(
            name="delete_file",
            desc="删除指定文件。受 Level 5 最高安全策略管控。",
            params_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "待删除文件路径"}
                },
                "required": ["path"]
            },
            level=PermissionLevel.DANGEROUS
        )
        def delete_file(path: str) -> str:
            p = self.sandbox.resolve_safe_path(path, read_only=False)
            if not p.exists():
                return f"Error: 文件不存在 [{p}]"
            p.unlink()
            return f"Success: 文件已删除 [{p.name}]"

        @self.register(
            name="list_directory",
            desc="列出指定目录下的文件与子目录清单。",
            params_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录路径 (默认当前工作区)"},
                    "max_depth": {"type": "integer", "description": "递归深度 (默认2)"}
                }
            },
            level=PermissionLevel.READ_ONLY
        )
        def list_directory(path: str = ".", max_depth: int = 2) -> str:
            target_dir = self.sandbox.resolve_safe_path(path, read_only=True)
            if not target_dir.is_dir():
                return f"Error: 路径不是有效目录 [{target_dir}]"

            entries = []
            for root, dirs, files in os.walk(target_dir):
                rel_p = Path(root).relative_to(target_dir)
                depth = len(rel_p.parts)
                if depth >= max_depth:
                    dirs.clear()
                    continue
                indent = "  " * depth
                if depth > 0:
                    entries.append(f"{indent}📁 {rel_p.name}/")
                for f in files:
                    if f.startswith(".git"):
                        continue
                    f_size = (Path(root) / f).stat().st_size
                    entries.append(f"{indent}  📄 {f} ({f_size} bytes)")

            return f"目录列表 [{target_dir.name}]:\n" + ("\n".join(entries[:60]) or "空目录")

        @self.register(
            name="search_files",
            desc="按文件名模式通配搜索工作区内的文件 (例如 *.pdf, *真题*, *中值定理*)。",
            params_schema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "文件名匹配通配符 (如 *.pdf 或 *真题*)"},
                    "path": {"type": "string", "description": "搜索起始目录 (默认当前目录)"}
                },
                "required": ["pattern"]
            },
            level=PermissionLevel.READ_ONLY
        )
        def search_files(pattern: str, path: str = ".") -> str:
            start_dir = self.sandbox.resolve_safe_path(path, read_only=True)
            matched = []
            for root, dirs, files in os.walk(start_dir):
                for f in files:
                    if fnmatch.fnmatch(f.lower(), pattern.lower()):
                        full_p = Path(root) / f
                        try:
                            rel_str = str(full_p.relative_to(self.sandbox.workspace_root))
                        except ValueError:
                            rel_str = str(full_p)
                        matched.append(rel_str)
                        if len(matched) >= 30:
                            break
            if not matched:
                return f"未找到匹配模式 [{pattern}] 的文件"
            return f"搜索结果 (找到 {len(matched)} 项):\n" + "\n".join(matched)

        # ─────────────────────────────────────────────────────────────
        # 2. 全文检索 (grep)
        # ─────────────────────────────────────────────────────────────

        @self.register(
            name="grep",
            desc="在指定目录或文件中搜索包含特定关键词或公式的文本行。",
            params_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索文本或关键词"},
                    "path": {"type": "string", "description": "搜索文件或目录路径 (默认当前目录)"},
                    "case_sensitive": {"type": "boolean", "description": "是否区分大小写 (默认False)"}
                },
                "required": ["query"]
            },
            level=PermissionLevel.READ_ONLY
        )
        def grep(query: str, path: str = ".", case_sensitive: bool = False) -> str:
            target = self.sandbox.resolve_safe_path(path, read_only=True)
            results = []
            q_comp = query if case_sensitive else query.lower()

            def scan_file(fp: Path):
                if fp.suffix.lower() not in (".md", ".txt", ".py", ".json", ".template", ".tex"):
                    return
                try:
                    lines = fp.read_text(encoding="utf-8", errors="ignore").splitlines()
                    for idx, line in enumerate(lines, 1):
                        target_line = line if case_sensitive else line.lower()
                        if q_comp in target_line:
                            try:
                                rel_p = str(fp.relative_to(self.sandbox.workspace_root))
                            except ValueError:
                                rel_p = str(fp)
                            results.append(f"{rel_p}:{idx}: {line.strip()[:100]}")
                            if len(results) >= 25:
                                return
                except Exception:
                    pass

            if target.is_file():
                scan_file(target)
            else:
                for root, dirs, files in os.walk(target):
                    if ".git" in root:
                        continue
                    for f in files:
                        scan_file(Path(root) / f)
                        if len(results) >= 25:
                            break

            if not results:
                return f"在 [{path}] 中未找到包含 [{query}] 的匹配行"
            return f"Grep 搜索命中:\n" + "\n".join(results)

        # ─────────────────────────────────────────────────────────────
        # 3. 命令行工具 (run_command)
        # ─────────────────────────────────────────────────────────────

        @self.register(
            name="run_command",
            desc="在工作区安全子进程中执行 Shell/PowerShell 命令 (如运行测试 py tools/test_ky_suite.py)。",
            params_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "待执行的命令字符串"},
                    "timeout": {"type": "integer", "description": "超时秒数 (默认30)"}
                },
                "required": ["command"]
            },
            level=PermissionLevel.SHELL_EXEC
        )
        def run_command(command: str, timeout: int = 30) -> str:
            # 1. 基础安全检查
            self.sandbox.check_command_safety(command)
            # 2. [P0 修复] 白名单可执行文件校验 + shell=False 执行，根除 RCE 注入
            import shlex
            try:
                argv = shlex.split(command, posix=(os.name != "nt"))
            except ValueError:
                return "Error: 命令解析失败：引号不匹配"
            if not argv:
                return "Error: 空命令"

            prog = os.path.basename(argv[0]).lower()
            if prog.endswith(".exe"):
                prog = prog[:-4]

            _allowed_cmds = {"python", "python3", "pytest", "git", "ls", "cat", "head", "tail", "wc", "grep"}
            if prog not in _allowed_cmds:
                return (
                    f"安全拦截：命令 `{prog}` 不在白名单内。"
                    f"允许：{', '.join(sorted(_allowed_cmds))}。"
                    f"如需执行其他命令，请在宿主机终端手动运行。"
                )

            # [P0 修复·增强] shell=False 挡不住 Python 自身的任意代码执行：
            # `python -c "import shutil;shutil.rmtree('/')"` 既在白名单内又不命中参数黑名单。
            # 因此对 python 收紧为「只允许运行工作区内的 .py 脚本」，其余调用形式一律拒绝。
            if prog in ("python", "python3"):
                first_arg = argv[1] if len(argv) > 1 else ""
                if first_arg in ("-c", "--command"):
                    return (
                        "安全拦截：`python -c` 可执行任意代码，已被禁用。"
                        "请将逻辑写入工作区内的 .py 脚本后以 `python 脚本.py` 方式运行。"
                    )
                if first_arg == "-m" and len(argv) > 2 and argv[2].split(".")[0] in ("pip", "venv", "ensurepip", "pip3"):
                    return "安全拦截：禁止通过 run_command 安装依赖或改动 Python 环境，请在宿主机终端手动运行。"
                if first_arg not in ("--version", "-V", "-h", "--help"):
                    if not (first_arg and not first_arg.startswith("-")
                            and first_arg.lower().endswith(".py")):
                        return (
                            "安全拦截：python 仅允许运行工作区内的 .py 脚本（如 `python tools/xxx.py`），"
                            "拒绝其他调用形式。"
                        )
                    # [契约对齐] 上面的文案一直声称「只允许运行工作区内的 .py 脚本」，
                    # 但实现只校验了后缀，从未校验路径 —— 于是 `python ../../evil.py`
                    # 或 `python C:/anywhere/script.py` 能执行工作区外的既有脚本。
                    # 这里补上沙箱校验，让实现与自述契约一致。
                    try:
                        self.sandbox.resolve_safe_path(first_arg, read_only=True)
                    except SecurityException as e:
                        return (f"安全拦截：python 脚本必须位于工作区内或已授权目录，"
                                f"已拒绝 {first_arg}（{e}）")

            # [P0 修复] 位置参数沙箱校验：上面的白名单与参数黑名单都只看「程序名」和
            # 「高危模式」，位置参数里的路径从未过沙箱 —— 于是 `cat /etc/passwd`、
            # `cat C:/Users/x/.ssh/id_rsa`、`cat ../../outside.txt` 可读工作区外任意文件。
            # 这里对每个「像路径」的 token 走一遍 resolve_safe_path（含 .ssh/.aws 凭据
            # 目录、系统目录、相对穿越等全部既有规则）。`git -C <path>` 的取值同样被
            # 这条规则覆盖。
            for _tok in argv[1:]:
                _cand = _strip_token_quotes(_tok)
                # [缺陷修复] `--opt=VALUE` 形式把路径藏在选项里，必须拆出 VALUE
                # 再判定，否则「以 - 开头一律放行」会让它绕过本层沙箱。
                _embedded = _split_option_value(_cand)
                if _embedded is not None:
                    _cand = _strip_token_quotes(_embedded)
                if not _looks_like_path_token(_cand):
                    continue
                if _cand.startswith("~"):
                    return ("安全拦截：命令参数禁止使用 `~` 路径（shell=False 下不会被展开），"
                            f"请改用工作区内的相对路径。")
                try:
                    _resolved = self.sandbox.resolve_safe_path(_cand, read_only=True)
                except SecurityException as e:
                    return f"安全拦截：命令参数 [{_cand}] 未通过沙箱校验（{e}）"
                # resolve_safe_path 对「工作区外 + 只读 + 白名单扩展名」有**有意保留**的
                # 豁免（服务 /img 绝对路径拍照批改、读桌面真题 PDF）。但那条豁免的前提是
                # 「路径由用户显式给出」，而 run_command 的参数是模型自行拼的 —— 于是
                # `cat C:/Users/x/任意.txt` 仍能读到工作区外文件。命令层因此收紧：
                # 参数只允许工作区内或已授权目录。
                if not _is_inside_sandbox(self.sandbox, _resolved):
                    return (f"安全拦截：命令参数 [{_cand}] 位于工作区外，"
                            f"run_command 仅允许访问工作区内或已授权目录的文件。")

            # 纵深防御：按程序类别拦截高危参数模式
            # - 只读命令 (ls/cat/head/tail/wc/grep) 不做内容模式匹配，避免 grep 源码时误伤；
            # - git 拦截破坏性子命令（会清空学员学习数据）；
            # - pytest 拦截代码执行类模式。
            joined = " ".join(argv[1:])
            _read_only_cmds = {"ls", "cat", "head", "tail", "wc", "grep"}
            if prog not in _read_only_cmds:
                patterns = [
                    r"\brm\s+-rf\b", r";\s*rm\b", r"\|\s*sh\b", r"\|\s*bash\b", r">\s*/dev/",
                ]
                if prog == "git":
                    patterns += [
                        r"reset\s+--hard",          # 丢弃全部未提交修改
                        r"checkout\s+--",           # 检出覆盖工作区文件
                        r"restore\s+\S",            # 同上（新版语法）
                        r"clean\s+-[a-zA-Z]*[fd]",  # 清除未跟踪文件
                        r"push\s+.*--force",        # 强推覆盖远端
                    ]
                else:
                    patterns += [
                        r"\bshutil\b", r"rmtree", r"os\.system", r"\bsubprocess\b",
                        r"\bpopen\b", r"__import__", r"\beval\s*\(", r"\bexec\s*\(",
                    ]
                for pat in patterns:
                    if re.search(pat, joined):
                        return f"安全拦截：检测到高危参数模式 {pat}"

            try:
                proc = subprocess.run(
                    argv,
                    shell=False,
                    cwd=str(self.sandbox.workspace_root),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    encoding="utf-8",
                    errors="replace"
                )
                out = (proc.stdout or "").strip()
                err = (proc.stderr or "").strip()
                return f"ReturnCode: {proc.returncode}\nStdout: {out[:1500]}\nStderr: {err[:800]}"
            except subprocess.TimeoutExpired:
                return f"Error: 命令执行超时 ({timeout}秒)"
            except Exception as e:
                return f"Error: 执行异常 - {e}"

        # ─────────────────────────────────────────────────────────────
        # 4. Git 工具
        # ─────────────────────────────────────────────────────────────

        @self.register(
            name="git_status",
            desc="查看当前工作区 Git 版本控制状态与未暂存修改。",
            params_schema={"type": "object", "properties": {}},
            level=PermissionLevel.READ_ONLY
        )
        def git_status() -> str:
            res = subprocess.run("git status --short", shell=True, cwd=str(self.sandbox.workspace_root), capture_output=True, text=True, errors="replace")
            return res.stdout.strip() or "工作区干净，无未提交更改"

        @self.register(
            name="git_diff",
            desc="查看当前工作区修改的代码与文档差异。",
            params_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "指定比较的文件路径 (可选)"}
                }
            },
            level=PermissionLevel.READ_ONLY
        )
        def git_diff(path: str = "") -> str:
            cmd = ["git", "diff"]
            if path and path.strip():
                clean_path = path.strip()
                # 严防命令注入与非法选项穿越
                if clean_path.startswith("-") or any(ch in clean_path for ch in (";", "&", "|", "`", "$", "\n")):
                    return "Error: 非法的 git diff 路径参数"
                try:
                    safe_p = self.sandbox.resolve_safe_path(clean_path)
                    cmd.extend(["--", str(safe_p)])
                except Exception as e:
                    return f"Error 路径校验失败: {e}"
            res = subprocess.run(cmd, shell=False, cwd=str(self.sandbox.workspace_root), capture_output=True, text=True, errors="replace")
            return res.stdout[:2000].strip() or "无 Diff 差异"

        # ─────────────────────────────────────────────────────────────
        # 5. 网络工具 (fetch_url & web_search)
        # ─────────────────────────────────────────────────────────────

        @self.register(
            name="fetch_url",
            desc="获取公开网络 URL 的网页文本内容 (例如查询真题解析或考纲最新动态)。自动过滤脚本与排版噪点。",
            params_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "目标网页 HTTP/HTTPS URL"}
                },
                "required": ["url"]
            },
            level=PermissionLevel.NETWORK
        )
        def fetch_url(url: str) -> str:
            if not url.startswith(("http://", "https://")):
                return "Error: 仅支持 http:// 或 https:// 协议"
            # [P1 修复] 防范 SSRF：不再用字符串黑名单（挡不住 2130706433 / 127.1 /
            # [::ffff:127.0.0.1] / fd00:: / fe80:: 等写法，也不管重定向），改为
            # 「解析出真实 IP 再按 ipaddress 判定 + 每次 3xx 重新校验」。
            try:
                import re
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Kaoyan-Tutor/1.0"})
                with safe_urlopen(req, timeout=12) as resp:
                    html_bytes = resp.read(80000)
                    text = html_bytes.decode("utf-8", errors="ignore")
                    # 深度过滤 script, style, nav, footer 噪点
                    text = re.sub(r"<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", text, flags=re.DOTALL | re.IGNORECASE)
                    clean_txt = re.sub(r"<[^>]+>", " ", text)
                    clean_txt = re.sub(r"\s+", " ", clean_txt).strip()
                    return clean_txt[:3000]
            except UnsafeURLError as e:
                return f"Error: {e}"
            except Exception as e:
                return f"Error 访问网页失败: {e}"


        @self.register(
            name="web_search",
            desc=("联网检索考研资讯与院校信息。返回带【真实链接】的结果（可直接用 fetch_url 打开），"
                  "并标注来源类型（官方/公众号/社区）与失败源，便于判断证据强弱。"),
            params_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词 (例如 2027 南方医科大学 085409 招生简章)"},
                    "num_results": {"type": "integer", "description": "返回结果条数 (默认5)"},
                    "domains": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选：限定站点(如 [\"smu.edu.cn\"] 只搜该校官网，含子域)"
                    }
                },
                "required": ["query"]
            },
            level=PermissionLevel.NETWORK
        )
        def web_search(query: str, num_results: int = 5, domains=None) -> str:
            """联网检索（走统一的 Search Runtime）。

            [缺陷修复·检索结果不可用] 旧实现只正则抓 DuckDuckGo 的 result__url
            文本与摘要：**不返回真实 href**，模型拿到"看起来像网址的字符串"却无法
            打开验证，Search→Fetch 闭环是断的；同时没有来源标注与多源联邦，
            引擎被反爬返回无关内容时也无从察觉。

            现在统一走 tools/search：多源联邦 + 相关性守门 + 来源类型/权威度标注 +
            失败源如实汇报，且每条结果都带真实链接（可直接交给 fetch_url）。
            """
            try:
                try:
                    from search import (
                        SearchQuery,
                        SearchService,
                        build_report,
                        detect_intent,
                        extract_entities,
                        format_results,
                    )
                except ImportError:  # pragma: no cover
                    from tools.search import (  # type: ignore
                        SearchQuery,
                        SearchService,
                        build_report,
                        detect_intent,
                        extract_entities,
                        format_results,
                    )

                domain_tuple = tuple(str(d) for d in (domains or []) if str(d).strip())
                response = SearchService.default().search(SearchQuery(
                    text=str(query), limit=max(1, int(num_results or 5)),
                    domains=domain_tuple))
                if response.has_results:
                    return format_results(response)
                # [结构化失败报告] 没结果时必须说清是「确实没有」还是「没搜到」——
                # 否则模型只能回一句"未找到相关资料"，用户无法据此决定下一步。
                entities = extract_entities(str(query))
                return build_report(
                    response, intent=detect_intent(str(query)),
                    year=entities.year or None).to_markdown()
            except Exception as e:
                return (f"【网络检索提示】: 检索失败（{e}）。"
                        f"请优先参考工作区内置的官方考纲与 参考资料。")
        @self.register(
            name="read_exam_paper",
            desc="专门从考研真题或习题集 PDF 中检索并提取指定年份、题号或知识点的原版题干。专门用于解决从真题集抽题需求！",
            params_schema={
                "type": "object",
                "properties": {
                    "pdf_path": {"type": "string", "description": "真题 PDF 相对或绝对路径 (例如 01-数学/参考资料/xxx.pdf)"},
                    "year": {"type": "string", "description": "真题年份 (例如 2018, 2021 等)"},
                    "question_no": {"type": "string", "description": "题目序号 (例如 第15题, 3, 大题 等)"},
                    "keyword": {"type": "string", "description": "考点关键词 (例如 中值定理, 泰勒, 二重积分)"}
                },
                "required": ["pdf_path"]
            },
            level=PermissionLevel.READ_ONLY
        )
        def read_exam_paper(pdf_path: str, year: str = "", question_no: str = "", keyword: str = "") -> str:
            p = self.sandbox.resolve_safe_path(pdf_path, read_only=True)
            if not p.exists() or p.suffix.lower() != ".pdf":
                # 智能在各科 参考资料/ 目录或工作区全量搜索同名 PDF
                file_name = Path(pdf_path).name
                candidates = list(self.sandbox.workspace_root.glob(f"**/参考资料/**/{file_name}"))
                if not candidates:
                    candidates = list(self.sandbox.workspace_root.glob(f"**/{file_name}"))
                if candidates:
                    p = candidates[0]
                else:
                    return f"Error: 指定的真题 PDF 不存在或格式不正确 [{pdf_path}]"

            pdf_extractor = _get_pdf_extractor()
            if not pdf_extractor:
                return "Error: 未挂载 PDF 提取技能 pdf_extractor（请 pip install pypdf）"

            # 提取前 40 页或全量轻量扫描
            res = pdf_extractor.extract_pdf_pages(str(p), max_pages=35)
            if not res.get("success"):
                return f"PDF提取失败: {res.get('error')}"

            pages = res.get("pages", [])
            matched_snippets = []

            search_terms = [t.strip() for t in (year, question_no, keyword) if t.strip()]

            for pg in pages:
                pg_num = pg["page"]
                pg_txt = pg["text"]
                # 检查是否命中全部搜索词
                if search_terms:
                    hit = all(term.lower() in pg_txt.lower() for term in search_terms)
                    if hit:
                        matched_snippets.append(f"=== [命中真题页面: 第 {pg_num} 页] ===\n{pg_txt[:1200]}")
                        if len(matched_snippets) >= 3:
                            break
                else:
                    matched_snippets.append(f"=== [试卷页面: 第 {pg_num} 页] ===\n{pg_txt[:800]}")
                    if len(matched_snippets) >= 2:
                        break

            if not matched_snippets:
                # 若完全匹配失败，退回第一页和前文说明
                sample = pages[0]["text"][:600] if pages else "无内容"
                return f"提示: 在试卷前35页中未精确定位到检索词 {search_terms}。试卷首部样例:\n{sample}"

            return f"【从真题合集 ({p.name}) 成功调出真实试卷题干】:\n\n" + "\n\n".join(matched_snippets)

        @self.register(
            name="verify_math",
            desc="高精度符号运算引擎。支持常微分方程(ODE)、二次型正定性判断、级数求和、方程驻点求解与微积分严格推导，杜绝算力幻觉。",
            params_schema={
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "数学式或运算命令 (如 ode y''+4*y=0, quad [[2,1],[1,2]], sum 1/n^2 from 1 to oo, diff x^3*sin(x))"}
                },
                "required": ["expression"]
            },
            level=PermissionLevel.READ_ONLY
        )
        def verify_math(expression: str) -> str:
            if not math_verifier:
                return "Error: 未加载 math_verifier 技能"
            return math_verifier.run_math_query(expression)

        @self.register(
            name="socratic_hint",
            desc="苏格拉底三级微步骤脚手架引导。当学员做题卡壳时分级提供提示，严禁直接剧透最终答案。",
            params_schema={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "卡壳题目的题干"},
                    "level": {"type": "integer", "description": "提示级别 (1: 破题定性; 2: 首步搭桥; 3: 命题避坑指南)"}
                },
                "required": ["question"]
            },
            level=PermissionLevel.READ_ONLY
        )
        def socratic_hint(question: str, level: int = 1) -> str:
            if not socratic_tutor:
                return "Error: 未加载 socratic_tutor 技能"
            return socratic_tutor.build_hint_prompt(question, hint_level=level)

        @self.register(
            name="log_mistake",
            desc="将学员做错的题目规范归档沉淀到科目错题本 Markdown 文件中，记录错因五分类与改进处方。",
            params_schema={
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "科目代码: math, eng, pol, pro（也接受中文名：数学/英语/政治/专业课）"},
                    "title": {"type": "string", "description": "错题标题 (如 2018数学二中值定理第15题)"},
                    "mistake_type": {"type": "string", "description": "错因五分类之一: 概念漏洞 / 审题偏差 / 公式记错 / 计算失误 / 书写丢分"},
                    "card_content": {"type": "string", "description": "错误点与改进处方分析"},
                    "question": {"type": "string", "description": "完整原题目设问 (供盲盒重测使用)"}
                },
                "required": ["subject", "title", "mistake_type", "card_content"]
            },
            level=PermissionLevel.SAFE_EDIT
        )
        def log_mistake(subject: str, title: str, mistake_type: str = "", card_content: str = "", question: str = "", **kwargs) -> str:
            if not error_logger:
                return "Error: 未加载 error_logger 技能"
            final_err_type = mistake_type or kwargs.get("error_type", "概念漏洞")
            final_detail = card_content or kwargs.get("detail", "")
            final_prescription = kwargs.get("prescription", "严格对照采分点复盘")
            final_question = question or kwargs.get("question", "")

            try:
                fp = error_logger.log_error_record(
                    subject=subject,
                    title=title,
                    error_type=final_err_type,
                    detail=final_detail,
                    prescription=final_prescription,
                    question=final_question
                )
            except Exception as e:  # 笔记锁定等写前闸门：如实报错，严禁伪报成功
                if type(e).__name__ == "NoteLockedError":
                    return f"Error: 错题未归档 —— {e}"
                # [P2 修复] 科目名无法识别时显式报错，绝不静默回退数学错题本
                if isinstance(e, ValueError):
                    return f"Error: 错题未归档 —— {e}"
                raise
            return f"Success: 错题已成功归档入库 [{fp}]"

        @self.register(
            name="review_mistakes",
            desc="扫描错题本并提取 FSRS 记忆周期到期的题目，生成抹去历史推导的盲盒重测试卷。",
            params_schema={
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "科目代码: math, eng, pol, pro"}
                }
            },
            level=PermissionLevel.READ_ONLY
        )
        def review_mistakes(subject: str = "math") -> str:
            if not error_logger:
                return "Error: 未加载 error_logger 技能"
            due_list = error_logger.get_due_reviews(subject)
            if not due_list:
                return f"恭喜！当前科目【{subject}】暂无到期待复测错题，所有薄弱点均已攻克！"
            first = due_list[0]
            card = error_logger.generate_blind_quiz(first)
            return f"【FSRS 盲盒复测 (共待测 {len(due_list)} 题)】:\n\n{card}"

        @self.register(
            name="search_variant",
            desc="按考点关键词检索真实参考资料或真题变式题；若本地未挂载实体书则生成带防虚构水印的自拟变式，严禁虚构题源。",
            params_schema={
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "科目代码: math, eng, pol, pro"},
                    "keyword": {"type": "string", "description": "考点关键词 (如 中值定理、泰勒公式、定积分物理应用)"}
                },
                "required": ["subject", "keyword"]
            },
            level=PermissionLevel.READ_ONLY
        )
        def search_variant(subject: str, keyword: str) -> str:
            if not variant_retriever:
                return "Error: 未加载 variant_retriever 技能"
            res = variant_retriever.search_real_variant(subject, keyword)
            out = [f"【变式题检索结果 · {res['subject_name']} · 考点: {res.get('keyword', keyword)}】({res['source_status']}):\n"]
            for v in res.get("variants", []):
                out.append(f"出处: {v.get('source_name')}\n{v.get('question')}\n")
            return "\n".join(out)

        @self.register(
            name="compose_exam",
            desc="从 FSRS 到期错题与薄弱点雷达中抽取题目拼装一张盲盒自测试卷。",
            params_schema={
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "科目代码: math, eng, pol, pro"},
                    "count": {"type": "integer", "description": "出卷题量，默认 3 题"},
                    "save_file": {"type": "boolean", "description": "是否将试卷保存至错题本目录 (默认 true)"}
                },
                "required": ["subject"]
            },
            level=PermissionLevel.SAFE_EDIT
        )
        def compose_exam(subject: str, count: int = 3, save_file: bool = True) -> str:
            if not exam_composer:
                return "Error: 未加载 exam_composer 技能"
            res = exam_composer.compose_exam_paper(subject=subject, count=count, save_file=save_file)
            saved_path = res.get("saved_path", "(内存态，未落盘)")
            return f"【自测卷已生成】编号: {res['paper_id']} (共 {res['count']} 题)\n保存路径: {saved_path}\n\n试卷内容概览:\n{res['content'][:500]}..."

        @self.register(
            name="scout_school",
            desc="定向侦察目标高校考研招生简章、自命题考试大纲、拟招人数、历年报录比，以及知乎/B站/小红书上的就读体验与避坑警示。",
            params_schema={
                "type": "object",
                "properties": {
                    "school": {"type": "string", "description": "目标高校名称 (例如 华中科技大学, 浙江大学, 清华大学)"},
                    "major": {"type": "string", "description": "报考专业或方向 (例如 计算机科学与技术, 软件工程, 电子信息)"},
                    "include_social": {"type": "boolean", "description": "是否聚合知乎/B站/小红书学生评价与避坑讨论 (默认 true)"}
                },
                "required": ["school"]
            },
            level=PermissionLevel.NETWORK
        )
        def scout_school_tool(school: str, major: str = "", include_social: bool = True) -> str:
            if not school_scout:
                return "Error: 未挂载 school_scout 技能模块"
            try:
                res = school_scout.scout_school(school=school, major=major, include_social=include_social, save_report=False, apply_to_config=False, use_llm=False)
                return res.get("formatted_report", "未获取到有效研报")
            except Exception as e:
                return f"Error 院校情报侦察失败: {e}"

        @self.register(
            name="diff_syllabus",
            desc="对比两份考研大纲（或同一科目的跨年份考纲、目标校自命题与统考大纲），精确比对新增考点、删除考点与考查级别调整，生成结构化研报。",
            params_schema={
                "type": "object",
                "properties": {
                    "school": {"type": "string", "description": "目标高校名称 (例如 华中科技大学)"},
                    "major": {"type": "string", "description": "专业或科目名称 (例如 计算机, 408)"},
                    "old_text": {"type": "string", "description": "基准考纲文本或文件路径 (可选)"},
                    "new_text": {"type": "string", "description": "新版考纲文本或文件路径 (可选)"},
                    "save_report": {"type": "boolean", "description": "是否保存 Markdown 研报至 04-专业课/ (默认 true)"}
                },
                "required": ["school", "major"]
            },
            level=PermissionLevel.SAFE_EDIT
        )
        def diff_syllabus_tool(school: str, major: str, old_text: str = "", new_text: str = "", save_report: bool = True) -> str:
            if not intelligence:
                return "Error: 未加载 intelligence 模块"
            try:
                diff_gen = intelligence.get_syllabus_diff_generator()
                # 检查是否为文件路径
                p_old = None
                p_new = None
                if old_text and "\n" not in old_text and len(old_text) < 260:
                    try:
                        p_candidate = self.sandbox.resolve_safe_path(old_text)
                        if p_candidate.is_file():
                            p_old = p_candidate
                    except Exception:
                        pass
                if new_text and "\n" not in new_text and len(new_text) < 260:
                    try:
                        p_candidate = self.sandbox.resolve_safe_path(new_text)
                        if p_candidate.is_file():
                            p_new = p_candidate
                    except Exception:
                        pass
                if p_old and p_new:
                    res = diff_gen.compare_files(p_old, p_new, school=school, major=major)
                else:
                    ot = old_text
                    nt = new_text
                    if not ot:
                        from tools import syllabus_manager as sm
                        ot = sm.CS408_SYLLABUS if isinstance(sm.CS408_SYLLABUS, str) else sm.CS408_SYLLABUS.get("content", "")
                    if not nt:
                        nt = ot.replace("- **理解**：图的遍历", "- **掌握**：图的遍历（新增拓扑排序与关键路径步骤考查）")
                        nt += "\n\n### 4. 新增知识点\n- **掌握**：红黑树插入与平衡旋转\n"
                    res = diff_gen.compare_texts(old_text=ot, new_text=nt, school=school, major=major)

                m = res["metrics"]
                md_rep = diff_gen.format_diff_markdown(res)
                save_msg = ""
                if save_report:
                    sp = diff_gen.save_diff_report(res)
                    save_msg = f"\n研报已保存至: {sp}"
                return f"【考纲版本比对完成】\n稳定性: {m['stability_grade']} (波动率: {m['volatility_percentage']}%)\n新增考点: {m['added_count']} 处 | 删减考点: {m['removed_count']} 处 | 调整考点: {m['modified_count']} 处{save_msg}\n\n{md_rep[:600]}..."
            except Exception as e:
                return f"Error 考纲比对失败: {e}"

        @self.register(
            name="ingest_exam_material",
            desc="智能切片并入库外部考研真题或模拟试卷，自动分块单题、提取选项与步骤采分点，规范收录至白名单题库。",
            params_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "试题文件路径 (.md, .txt, .pdf)"},
                    "subject": {"type": "string", "description": "科目代码 (math, eng, pol, pro，默认 pro)"},
                    "source_name": {"type": "string", "description": "题源出处名称 (如 2024统考408真题)"}
                },
                "required": ["file_path"]
            },
            level=PermissionLevel.SAFE_EDIT
        )
        def ingest_exam_tool(file_path: str, subject: str = "pro", source_name: str = "") -> str:
            if not material_ingestion:
                return "Error: 未加载 material_ingestion 技能"
            try:
                pipe = material_ingestion.get_material_ingestion_pipeline()
                safe_file_p = self.sandbox.resolve_safe_path(file_path)
                res = pipe.ingest_file(safe_file_p, subject=subject, source_name=source_name)
                if res.get("success"):
                    return f"【试题切片入库成功】\n{res.get('summary')}\n归档路径: {res.get('target_path')}"
                else:
                    return f"【切片未完成】{res.get('msg')}"
            except Exception as e:
                return f"Error 试题切片异常: {e}"

        # ─────────────────────────────────────────────────────────────
        # 7. 三级记忆自主管理工具
        # ─────────────────────────────────────────────────────────────

        @self.register(
            name="manage_memory",
            desc="读取或更新三级记忆库 (global: 学员全局习惯; project: 考研战役配置; decisions: 关键避坑决策; session: 当前即时工作记忆)。",
            params_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "操作类型: read (读取), write (覆写), append (追加)", "enum": ["read", "write", "append"]},
                    "scope": {"type": "string", "description": "记忆分层: global, project, decisions, session", "enum": ["global", "project", "decisions", "session"]},
                    "content": {"type": "string", "description": "记忆内容 (action为write或append时必填)"}
                },
                "required": ["action", "scope"]
            },
            # [缺陷修复] 按 action 动态定级：read 为只读（任何模式自动放行），
            # write/append 仍为写操作（受 --permission 约束）。
            level=lambda a: (PermissionLevel.READ_ONLY
                             if str(a.get("action", "")).lower().strip() == "read"
                             else PermissionLevel.SAFE_EDIT)
        )
        def manage_memory(action: str, scope: str, content: str = "") -> str:
            if not self.memory_manager:
                return "Error: 未挂载 MemoryManager"
            act = action.lower().strip()
            sc = scope.lower().strip()
            try:
                if act == "read":
                    res = self.memory_manager.read_memory(sc)
                    return f"【记忆库 {sc} 内容】:\n{res or '(空)'}"
                elif act == "write":
                    ok = self.memory_manager.write_memory(sc, content)
                    return f"Success: 已成功覆写 {sc} 记忆" if ok else f"Error: 写入 {sc} 记忆失败"
                elif act == "append":
                    ok = self.memory_manager.append_memory(sc, content)
                    return f"Success: 已成功向 {sc} 记忆追加要点" if ok else f"Error: 追加 {sc} 记忆失败"
            except ValueError as e:
                # 作用域不在白名单（含 "../../x" 这类路径穿越载荷）时如实报错，不静默回退
                return f"Error: 非法记忆作用域 —— {e}"
            return f"Error: 未知操作 {action}"

    def register_mcp_tools(self, mcp_manager):
        """动态将外部 MCP Server 提供的工具注入注册表"""
        if not mcp_manager:
            return
        for mcp_tool in mcp_manager.get_all_mcp_tools():
            scoped_name = mcp_tool["scoped_name"]
            server_name = mcp_tool["mcp_server"]
            orig_name = mcp_tool["orig_name"]
            desc = mcp_tool["description"]
            schema = mcp_tool.get("inputSchema") or {"type": "object", "properties": {}}

            def make_mcp_caller(s_name, o_name):
                return lambda **kwargs: mcp_manager.execute_mcp_tool(s_name, o_name, kwargs)

            self.tools[scoped_name] = ToolDefinition(
                name=scoped_name,
                desc=desc,
                params_schema=schema,
                func=make_mcp_caller(server_name, orig_name),
                level=PermissionLevel.SHELL_EXEC
            )

