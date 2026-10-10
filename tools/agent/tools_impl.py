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
7. [K5] 技能桥接工具 (grade_exam_paper, grade_open_question, solve_vision,
   search_wechat, map_knowledge, diagnose_exam —— 经 skill_bridge 集中声明)

[K5 工具分级] 每个 ToolDefinition 带 tier（essential 常驻 / extended 低频按需）
与 source（builtin / skill / mcp）元数据；get_openai_tools 默认返回全部（本批零
行为变化），可经 ky_config.json 的 agent.tool_tier 收紧为 essential。
"""

import os
import re
import sys
import json
import fnmatch
import shutil
import subprocess
import urllib.request
from urllib.parse import urlparse, parse_qs
from pathlib import Path

try:  # 双导入路径兼容（源码脚本式 / tools 包式）
    from workspace import resolve_workspace_root
except ImportError:  # pragma: no cover
    from tools.workspace import resolve_workspace_root
from typing import Dict, Any, Callable, List, Optional, Sequence

from .sandbox import Sandbox, SecurityException
from .permissions import LEVEL_NAMES, PermissionLevel, PermissionManager

try:  # [K8] 工具输出预算：截断上限单一真源 + execute_tool 后置兜底
    from .output_budget import (
        DEFAULT_TOOL_OUTPUT_BUDGET,
        TOOL_OUTPUT_LIMITS,
        apply_output_budget,
    )
except ImportError:  # pragma: no cover - 脚本式直跑兼容
    from output_budget import (  # type: ignore
        DEFAULT_TOOL_OUTPUT_BUDGET,
        TOOL_OUTPUT_LIMITS,
        apply_output_budget,
    )

try:  # [K5] 技能桥接器：6 项领域技能 → Agent 工具（集中声明，见 skill_bridge.py）
    from .skill_bridge import register_skill_tools
except ImportError:  # pragma: no cover - 脚本式直跑兼容
    from skill_bridge import register_skill_tools  # type: ignore

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
ROOT = resolve_workspace_root(__file__)
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
    """路径是否落在工作区内或用户显式授权的额外目录内。

    [审计 2026-09-30 P1-1] 授权目录判定改为 ``Path.is_relative_to``（按路径
    分量、带分隔符边界、Windows 大小写不敏感）：此前字符串前缀比较会把授权
    目录的同前缀兄弟目录一并放行（已实测：授权 ``.../refs`` 后可读写
    ``.../refs-secret/victim.md``）。
    """
    try:
        resolved = resolved.resolve()
    except OSError:
        return False
    try:
        resolved.relative_to(sandbox.workspace_root)
        return True
    except ValueError:
        pass
    return any(resolved.is_relative_to(extra) for extra in sandbox.allowed_extra_paths)


# ─────────────────────────────────────────────────────────────────────
# [B2a ①] 脚本执行 allowlist（硬闸门）
# 「写脚本再执行」是权限模型里唯一一条能绕过审批执行任意代码的路径：
# agent 先用 write_file 在工作区内落一个 evil.py，再 `python evil.py` ——
# 既有校验只查"是否在工作区内"，全部通过。因此 python/python3 可执行的脚本
# 被收紧为**受控目录白名单**：tools/ / tests/ / rust_ext/ 是版本受控、可审计
# 的项目代码目录；工作区其他位置（如 01-数学/、05-考研看板/）里的 .py 一律
# 拒绝执行。越界是硬拒绝 —— 不走审批通道，不给"批准后放行"的口子。
# ─────────────────────────────────────────────────────────────────────

#: 允许被 run_command 执行的脚本所在目录前缀（相对工作区根，POSIX 风格、小写）。
_SCRIPT_EXEC_ALLOWED_PREFIXES = (
    "tools/",
    "tests/",
    "rust_ext/",
)

#: 允许被执行的根级脚本（相对工作区根，小写）。宁缺毋滥：仅收显式点名的入口脚本。
#: 当前仓库根目录没有 .py 入口（入口在 tools/ky_cli.py），故为空。
_SCRIPT_EXEC_ALLOWED_ROOT_SCRIPTS: tuple = ()


def _is_script_exec_allowed(resolved: Path, workspace_root: Path) -> bool:
    """[B2a] 脚本是否落在「受控目录」白名单内（硬闸门，见上方常量说明）。

    判定基于**解析后的工作区相对路径**：``./tools/x.py``、``tools/../tools/x.py``
    等写法在解析后归一，无法靠拼路径绕过；Windows 上经 ``normcase`` 归一大小写，
    ``TOOLS/x.py`` 也不能冒充白名单目录。
    """
    try:
        rel = Path(resolved).resolve().relative_to(Path(workspace_root).resolve())
    except (ValueError, OSError):
        return False
    rel_key = os.path.normcase(rel.as_posix()).replace("\\", "/")
    if rel_key in _SCRIPT_EXEC_ALLOWED_ROOT_SCRIPTS:
        return True
    return any(rel_key.startswith(pfx) for pfx in _SCRIPT_EXEC_ALLOWED_PREFIXES)


def _is_script_exec_allowed_tree(dir_path: Path, workspace_root: Path) -> bool:
    """[审计 2026-09-30 P0-2] 目录是否位于（或等于）受控目录白名单内。

    与 :func:`_is_script_exec_allowed` 的差别：目录自身 ``tests`` 不带尾斜杠，
    按文件版的前缀匹配会漏判（``"tests".startswith("tests/")`` 为假）。
    pytest 以目录为位置参数时（``pytest tests/``）用本函数判定。
    """
    try:
        rel = Path(dir_path).resolve().relative_to(Path(workspace_root).resolve())
    except (ValueError, OSError):
        return False
    rel_key = os.path.normcase(rel.as_posix()).replace("\\", "/")
    return any(rel_key == pfx.rstrip("/") or rel_key.startswith(pfx)
               for pfx in _SCRIPT_EXEC_ALLOWED_PREFIXES)


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


#: [W10 检索行为引导] 搜索引擎域名清单（判定「搜索引擎直抓」的第一条件）。
_SEARCH_ENGINE_HOSTS = (
    "bing.com", "baidu.com", "sogou.com", "so.com", "360.cn",
    "duckduckgo.com", "google.com", "google.com.hk", "ecosia.org",
    "mojeek.com", "brave.com", "marginalia.nu", "searx.be",
    "yandex.com", "yandex.ru", "startpage.com", "qwant.com",
)

#: 搜索行为特征：查询参数名（精确匹配）或路径片段。
_SEARCH_QUERY_KEYS = frozenset(("q", "wd", "query", "keyword", "word", "p"))
_SEARCH_PATH_HINTS = ("/search", "/web", "/html/", "/lite", "/results")


def _search_engine_host(url: str) -> Optional[str]:
    """[W10] 若 URL 是「搜索引擎直抓」（域名 + 搜索行为），返回命中的域名，否则 None。

    双重条件：① host 命中搜索引擎清单；② 带搜索查询参数（q/wd/query/… 精确
    匹配参数名）或搜索路径片段。仅命中「打开引擎的静态页面」（如首页、某条已
    选定结果）不拦截 —— 拦的是「用 fetch 代替搜索」这一行为。
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    host = (parsed.hostname or "").lower()
    if not host:
        return None
    hit = next((h for h in _SEARCH_ENGINE_HOSTS
                if host == h or host.endswith("." + h)), None)
    if not hit:
        return None
    try:
        keys = {k.lower() for k in parse_qs(parsed.query, keep_blank_values=True)}
    except Exception:
        keys = set()
    if keys & _SEARCH_QUERY_KEYS:
        return hit
    path = (parsed.path or "").lower()
    if any(hint in path for hint in _SEARCH_PATH_HINTS):
        return hit
    return None


# ─────────────────────────────────────────────────────────────────────
# [K5] 工具档位（tier）与来源（source）
#   * essential：高频基础能力，常驻 schema（文件/检索/执行/基础考研工具）；
#   * extended ：低频或带副作用的领域能力（破坏性操作、技能桥接工具、MCP 工具），
#                新工具一律排尾 —— 降低对 flash 级模型工具选择的锚定干扰；
#   * all      ：两档全给（**本批默认**，与历史行为逐字节一致，零行为风险）。
# 逃生门：ky_config.json 的 agent.tool_tier = "all"（默认）/"essential"。
# ─────────────────────────────────────────────────────────────────────
TIER_ESSENTIAL = "essential"
TIER_EXTENDED = "extended"
TIER_ALL = "all"
VALID_TIERS = (TIER_ESSENTIAL, TIER_EXTENDED, TIER_ALL)

#: ToolDefinition.source 取值（供 ky tools list 审计与"默认跳过 MCP"判定）
SOURCE_BUILTIN = "builtin"
SOURCE_SKILL = "skill"
SOURCE_MCP = "mcp"


class ToolDefinition:
    # level 可为 int，也可为 Callable[[dict], int]（按 action 动态定级）
    def __init__(self, name: str, desc: str, params_schema: Dict[str, Any], func: Callable, level,
                 tier: str = TIER_ESSENTIAL, source: str = SOURCE_BUILTIN,
                 budget: int = DEFAULT_TOOL_OUTPUT_BUDGET):
        self.name = name
        self.desc = desc
        self.params_schema = params_schema
        self.func = func
        self.level = level
        # [K5] tier/source 为元数据（不参与执行），供 schema 分级与工具审计
        self.tier = tier
        self.source = source
        # [K8] 输出预算（字符）：execute_tool 出口超过该值即截断 + 落盘。
        # 默认取 output_budget.DEFAULT_TOOL_OUTPUT_BUDGET（当前 50 KB），
        # 远大于所有工具自带截断，默认零行为变化。
        # [三审修复·2026-10-08 注释对齐] 此前注释写死的旧值与实际不符。
        self.budget = budget

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
    def __init__(self, sandbox: Sandbox, permissions: PermissionManager, memory_manager=None,
                 config: Optional[Dict[str, Any]] = None):
        self.sandbox = sandbox
        self.permissions = permissions
        self.memory_manager = memory_manager
        # [K5 逃生门] agent.tool_tier 可把常驻 schema 从 all 收紧到 essential。
        # 显式传入的 config 优先；未传时惰性读工作区 ky_config.json（默认 all）。
        self.config = config if isinstance(config, dict) else None
        self._tool_tier_cache: Optional[str] = None
        self.tools: Dict[str, ToolDefinition] = {}
        self._register_all_tools()

    def register(self, name: str, desc: str, params_schema: Dict[str, Any], level,
                 *, tier: str = TIER_ESSENTIAL, source: str = SOURCE_BUILTIN):
        # [K5] tier/source 为 keyword-only 新增参数，既有调用签名逐字节兼容。
        def decorator(func: Callable):
            self.tools[name] = ToolDefinition(name, desc, params_schema, func, level,
                                              tier=tier, source=source)
            return func
        return decorator

    def _resolve_effective_tier(self, tier: Optional[str]) -> str:
        """解析本次要呈现的 tier 档位（显式参数 > 配置逃生门 > 默认 all）。

        非法值一律回落 ``all`` —— 配置写错时宁可多给工具，也绝不静默丢工具。
        """
        if tier is not None:
            t = str(tier).strip().lower()
            return t if t in VALID_TIERS else TIER_ALL
        if self._tool_tier_cache is None:
            self._tool_tier_cache = self._config_tool_tier()
        return self._tool_tier_cache

    def _config_tool_tier(self) -> str:
        """读配置里的 ``agent.tool_tier``（逃生门；任何异常/非法值一律 all）。"""
        try:
            cfg = self.config
            if cfg is None:
                cfg_file = Path(self.sandbox.workspace_root) / "ky_config.json"
                if not cfg_file.exists():
                    return TIER_ALL
                cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
            agent_cfg = cfg.get("agent") if isinstance(cfg, dict) else None
            raw = agent_cfg.get("tool_tier") if isinstance(agent_cfg, dict) else None
            t = str(raw).strip().lower() if raw is not None else ""
            return t if t in VALID_TIERS else TIER_ALL
        except Exception:  # noqa: BLE001 - 配置读取失败绝不牵连工具注册表
            return TIER_ALL

    def get_openai_tools(self, names: Optional[Sequence[str]] = None,
                         tier: Optional[str] = None) -> List[Dict[str, Any]]:
        """返回工具的 OpenAI schema 列表。

        ``names`` 非 None 时**优先**：只返回清单内的工具（收尾兜底等场景需要受限
        工具集；此时忽略 ``tier``，语义与历史一致）。清单内不存在的名字静默忽略。

        ``tier`` 为 None 时走配置逃生门（agent.tool_tier，默认 ``all`` = 全部工具，
        本批零行为变化）；``"essential"`` 只回 essential；``"extended"`` 只回
        extended。

        [W10 检索行为引导] ``web_search`` 置顶：flash 级模型对「搜索=手动拼接
        搜索引擎 URL」有强训练惯性（实测两工具极简环境下仍首选 fetch_url 抓
        Bing），工具顺序对选择有锚定效应——把检索首选工具放在最前。
        [K5] 其后按 essential → extended 排序（同档内保持注册顺序），新工具排尾。
        """
        if names is not None:
            wanted = [str(n) for n in names]
            return [self.tools[n].to_openai_dict() for n in wanted if n in self.tools]
        eff = self._resolve_effective_tier(tier)
        _rank = {TIER_ESSENTIAL: 1, TIER_EXTENDED: 2}
        tools = [t for t in self.tools.values() if eff == TIER_ALL or t.tier == eff]
        tools.sort(key=lambda t: (0 if t.name == "web_search" else _rank.get(t.tier, 2)))
        return [t.to_openai_dict() for t in tools]

    def list_tools(self, tier: Optional[str] = None,
                   source: Optional[str] = None) -> List[ToolDefinition]:
        """[B2 动态发现] 实时枚举当前注册表中的工具（活注册表读取，非静态清单）。

        * ``tier``：None=不过滤；``essential`` / ``extended`` 精确过滤，
          ``all`` 与非法值等价于不过滤（与 get_openai_tools 的宽容语义一致）；
        * ``source``：None=不过滤；``builtin`` / ``skill`` / ``mcp`` 精确匹配。

        返回注册顺序（呈现层自行排序）；绝不抛异常。
        """
        items = list(self.tools.values())
        if tier is not None:
            t = str(tier).strip().lower()
            if t in (TIER_ESSENTIAL, TIER_EXTENDED):
                items = [td for td in items if td.tier == t]
        if source is not None:
            s = str(source).strip().lower()
            items = [td for td in items if td.source == s]
        return items

    def describe_tools(self, tier: Optional[str] = None,
                       source: Optional[str] = None) -> List[Dict[str, Any]]:
        """[B2 动态发现] 工具摘要行（``ky tools list`` 与 list_tools 工具共用的单一真源）。

        每行字段：name / desc / level（int 或 ``"dynamic"``）/ level_name /
        tier / source。动态定级（callable）的展示口径此前内联在 CLI 命令里，
        现收敛到此，避免审计输出与模型自省输出两套漂移。
        """
        rows: List[Dict[str, Any]] = []
        for td in self.list_tools(tier=tier, source=source):
            if isinstance(td.level, int):
                level_value: Any = td.level
                level_name = LEVEL_NAMES.get(td.level, str(td.level))
            else:
                level_value = "dynamic"
                level_name = "动态（按调用参数定级）"
            rows.append({
                "name": td.name,
                "desc": td.desc,
                "level": level_value,
                "level_name": level_name,
                "tier": td.tier,
                "source": td.source,
            })
        return rows

    def execute_tool(self, name: str, args: Dict[str, Any], interactive: bool = True,
                     call_id: str = "") -> str:
        """统一执行入口: 经过沙箱与权限验证

        [K8] ``call_id`` 为可选的调用标识（loop 传模型给出的 tool_call id），
        仅用于输出超预算时的落盘文件名；缺省时落盘名用 ``nocall``。
        """
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
            # [B2a/B2b] 需要知道调用方是否声明可交互的闸门（headless 一律拒绝、
            # 交互通道才弹审批卡）显式透传该上下文：B2a 的 run_command「会话污染」
            # 闸门与 B2b 的 read_file / read_exam_paper「工作区外读取」闸门共用。
            # 该参数不在工具 schema 里，模型无法通过 args 影响它（此处强制覆盖）。
            call_args = args
            # [K5] solve_vision（图片路径）与 grade_exam_paper（试卷路径）同样要过
            # 工作区外读取授权闸门，与上述三工具同一规则显式透传交互上下文。
            # [B3/B4] search_in_materials 的 pdf_path 参数同理（自描述工具经
            # skill_bridge 包装后从 ctx 取 interactive）。
            if name in ("run_command", "read_file", "read_exam_paper",
                        "solve_vision", "grade_exam_paper", "search_in_materials"):
                call_args = {**args, "interactive": interactive}
            result = tool_def.func(**call_args)
            # 3. [K8] 输出预算兜底：超过 ToolDefinition.budget（默认
            # DEFAULT_TOOL_OUTPUT_BUDGET，当前 50 KB）时截断 + 完整原文落盘
            # .memory/tool_outputs/<日期>/（落盘失败静默降级「未落盘」）。
            # 默认预算远大于各工具自带截断 → 零行为变化。
            return apply_output_budget(
                str(result), name,
                call_id=call_id,
                budget=getattr(tool_def, "budget", DEFAULT_TOOL_OUTPUT_BUDGET),
                workspace_root=self.sandbox.workspace_root,
            )
        except SecurityException as se:
            return f"SecurityError: {se}"
        except Exception as e:
            return f"ExecutionError in [{name}]: {type(e).__name__} - {str(e)}"

    def _resolve_read_path(self, raw_path, interactive: bool = True) -> Path:
        """[B2b] 读取入口统一前置：工作区外文件先过外部读取授权闸门。

        流程：``classify_external_read`` 判定是否为「可授权的工作区外只读候选」——
          * 不是（工作区内 / 白名单目录内 / 已授权目录内，或属于永不可授权的拒绝）
            → 直接走原有 ``resolve_safe_path``（该拒绝就拒绝，该放行就放行）；
          * 是 → 先请求授权；被拒则抛 ``SecurityException``（文案含出路引导）；
            批准则把文件所在目录登记进本会话授权集，再重试解析 ——
            此时沙箱豁免分支放行，并留下审计日志与会话读取记录。
        """
        candidate = self.sandbox.classify_external_read(raw_path)
        if candidate is None:
            return self.sandbox.resolve_safe_path(raw_path, read_only=True)
        allowed, reason = self.permissions.check_external_read(
            str(raw_path), {"path": str(raw_path)}, interactive=interactive)
        if not allowed:
            raise SecurityException(
                f"沙箱拦截: 拒绝读取工作区外文件 [{raw_path}] —— {reason}")
        self.sandbox.register_authorized_read_dir(candidate.parent)
        return self.sandbox.resolve_safe_path(raw_path, read_only=True)

    def _register_all_tools(self):
        # ─────────────────────────────────────────────────────────────
        # 1. 文件工具
        # ─────────────────────────────────────────────────────────────

        @self.register(
            name="read_file",
            desc=("读取本地文本或PDF文件。文本文件用 offset(起始行)/limit(最大行数) 分页；"
                  "PDF 文件用 offset(起始页, 1-based)/limit(最大字符数) 分页，"
                  "默认提取第 1 页起最多 20 页，可按页续读长文档。"),
            params_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目标文件相对或绝对路径"},
                    "offset": {"type": "integer", "description": "文本: 起始行；PDF: 起始页 (默认 0/第 1 页)"},
                    "limit": {"type": "integer", "description": "文本: 最大行数 (默认2000)；PDF: 最大字符数"}
                },
                "required": ["path"]
            },
            level=PermissionLevel.READ_ONLY
        )
        def read_file(path: str, offset: int = 0,
                      limit: int = TOOL_OUTPUT_LIMITS["read_file_text_lines"],
                      interactive: bool = True) -> str:
            # [B2b] 工作区外文件先过外部读取授权闸门（批准后同目录免再问）
            p = self._resolve_read_path(path, interactive)
            self.sandbox.assert_no_symlink_components(p)
            if not p.exists():
                return f"Error: 文件不存在 [{p}]"

            # 智能 PDF 格式处理
            if p.suffix.lower() == ".pdf":
                pdf_extractor = _get_pdf_extractor()
                if pdf_extractor:
                    # [W6 工具改进] offset 对 PDF 解释为「起始页」（1-based），
                    # limit 解释为最大字符数；默认页数上限 8→20、默认字符上限
                    # 2000→12000 —— 评测实测 20 页 PDF 的关键字段分布在后段页面，
                    # 「前 8 页 + 2000 字符」会成建制漏读（PDF-005 类任务因此失分，
                    # 模型被迫写脚本绕行并撞上脚本执行权限墙）。
                    try:
                        start_page = max(1, int(offset) if offset else 1)
                    except (TypeError, ValueError):
                        start_page = 1
                    try:
                        char_limit = (int(limit)
                                      if int(limit) != TOOL_OUTPUT_LIMITS["read_file_text_lines"]
                                      else TOOL_OUTPUT_LIMITS["read_file_pdf_chars"])
                    except (TypeError, ValueError):
                        char_limit = TOOL_OUTPUT_LIMITS["read_file_pdf_chars"]
                    pdf_info = pdf_extractor.extract_pdf_pages(
                        str(p), max_pages=20, start_page=start_page)
                    if pdf_info.get("success"):
                        pages_txt = "\n".join([f"--- 第 {pg['page']} 页 ---\n{pg['text']}" for pg in pdf_info.get("pages", [])])
                        return (f"【PDF文档自动提取: {p.name} (共 {pdf_info.get('total_pages', 0)} 页，"
                                f"本次提取第 {start_page} 页起)】\n\n{pages_txt[:char_limit]}")
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
            self.sandbox.assert_no_symlink_components(p)
            if p.exists() and not overwrite:
                return f"Error: 文件已存在且 overwrite=False [{p}]"
            # [P2 修复] 学员标注 locked: true 的笔记此前可被 write_file 静默覆盖
            # （assert_writable 只接在 error_logger 一条路径上）。
            locked_err = _note_lock_error(p)
            if locked_err:
                return locked_err
            p.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(p, content)
            # [B2a] 登记会话污染：本会话写过的文件禁止被 run_command 直接执行
            # （除非经审批通道显式批准，见 run_command 的会话污染闸门）。
            self.sandbox.register_written_file(p)
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
            self.sandbox.assert_no_symlink_components(p)
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
            # [B2a] 同 write_file：被改过的既有文件同样计入会话污染集合 ——
            # 「改一行再执行」与「整文件写入再执行」是同一类风险。
            self.sandbox.register_written_file(p)
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
            level=PermissionLevel.DANGEROUS,
            tier=TIER_EXTENDED,  # [K5] 破坏性操作不常驻 schema
        )
        def delete_file(path: str) -> str:
            p = self.sandbox.resolve_safe_path(path, read_only=False)
            self.sandbox.assert_no_symlink_components(p)
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
            desc="按文件名模式通配搜索工作区内的文件 (例如 *.pdf, *真题*, *中值定理*, "
                 "**/今日任务.md)。支持文件名与相对路径两种匹配。",
            params_schema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "文件名或相对路径通配符 (如 *.pdf、*真题*、**/今日任务.md)"},
                    "path": {"type": "string", "description": "搜索起始目录 (默认当前目录)"}
                },
                "required": ["pattern"]
            },
            level=PermissionLevel.READ_ONLY
        )
        def search_files(pattern: str, path: str = ".") -> str:
            start_dir = self.sandbox.resolve_safe_path(path, read_only=True)
            # [W11 路径 glob] 原实现只对**文件名**做 fnmatch，带路径的 glob
            # （如 `**/今日任务.md`）必然失配——多角色实测：模型调
            # search_files('**/今日任务.md') 得到「未找到」后绕道 list_directory
            # 才找到文件。现对「文件名」与「相对路径」同时匹配（fnmatch 的 `*`
            # 天然跨 `/`，故 `*.md`、`*真题*` 等既有行为不变）。
            pat = str(pattern or "").lower()
            if pat.startswith("./"):
                pat = pat[2:]
            matched = []
            for root, dirs, files in os.walk(start_dir):
                for f in files:
                    full_p = Path(root) / f
                    try:
                        rel_str = str(full_p.relative_to(self.sandbox.workspace_root))
                    except ValueError:
                        rel_str = str(full_p)
                    rel_norm = rel_str.replace("\\", "/").lower()
                    hit = (fnmatch.fnmatch(f.lower(), pat)
                           or fnmatch.fnmatch(rel_norm, pat))
                    if not hit and pat.startswith("**/"):
                        # `**/x.md` 的 `**/` 前缀要求至少一层目录——对根级文件
                        # 再试一次去前缀匹配（`今日任务.md` 在根时也能命中）。
                        hit = fnmatch.fnmatch(rel_norm, pat[3:])
                    if hit:
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
        def run_command(command: str, timeout: int = 30, interactive: bool = True) -> str:
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

            prog_raw = argv[0]
            prog = os.path.basename(prog_raw).lower()
            if prog.endswith(".exe"):
                prog = prog[:-4]

            _allowed_cmds = {"python", "python3", "pytest", "git", "ls", "cat", "head", "tail", "wc", "grep"}
            if prog not in _allowed_cmds:
                return (
                    f"安全拦截：命令 `{prog}` 不在白名单内。"
                    f"允许：{', '.join(sorted(_allowed_cmds))}。"
                    f"如需执行其他命令，请在宿主机终端手动运行。"
                    f"【替代路径】读取/查看文件内容请直接用 read_file 工具"
                    f"（PDF 会自动提取文本），无需切换目录或执行外部命令。"
                )

            # basename 命中白名单不等于「执行的是受信程序」：
            # ``C:\\tmp\\evil\\python.exe`` 也会得到 basename=python。含路径的
            # 可执行文件必须解析到 PATH 中同名程序（或当前解释器），否则拒绝。
            if os.path.dirname(prog_raw):
                candidate = Path(prog_raw)
                if not candidate.is_absolute():
                    candidate = self.sandbox.workspace_root / candidate
                try:
                    candidate = candidate.resolve(strict=True)
                except (OSError, RuntimeError):
                    return "安全拦截：拒绝执行不存在或无法解析的可执行文件路径"
                trusted = []
                # 只解析去掉路径后的裸命令名；绝不能把 ``which(prog_raw)``
                # 的任意命中当成“受信”，否则恶意绝对路径会把自己加入信任集。
                path_prog = shutil.which(prog)
                if path_prog:
                    try:
                        trusted.append(Path(path_prog).resolve(strict=True))
                    except (OSError, RuntimeError):
                        pass
                try:
                    trusted.append(Path(sys.executable).resolve(strict=True))
                except (OSError, RuntimeError):
                    pass
                if not any(os.path.normcase(str(candidate)) == os.path.normcase(str(p))
                           for p in trusted):
                    return "安全拦截：拒绝执行非受信路径的可执行文件"
                argv[0] = str(candidate)

            # [审计 2026-09-30 P0-2] B2a 两道闸门（受控目录白名单 + 会话写入审批）
            # 抽为闭包，按「最终被执行的脚本文件」判定 —— 此前它们只写在
            # python/python3 分支内部，pytest 等同样能执行代码的入口绕过了闸门
            # （write_file 落 tests/test_x.py → pytest tests/test_x.py 零审批执行）。
            def _enforce_script_gates(script_display: str, script_path) -> str:
                """返回空串 = 放行；否则返回应回给模型的拦截文案。"""
                if not _is_script_exec_allowed(script_path, self.sandbox.workspace_root):
                    return (
                        f"安全拦截：仅允许执行受控目录"
                        f"（{', '.join(_SCRIPT_EXEC_ALLOWED_PREFIXES)}）下的脚本，"
                        f"已拒绝 [{script_display}]。工作区其他位置的脚本需人工核对后执行。"
                        f"【替代路径】读取文件内容请直接用 read_file 工具"
                        f"（PDF 会自动提取文本，offset 为起始页、可续读长文档），"
                        f"不要为此编写并执行脚本。"
                    )
                if self.sandbox.is_session_written(script_path):
                    _approved, _reason = self.permissions.check_session_script_exec(
                        script_display, {"command": command}, interactive=interactive)
                    if not _approved:
                        return (f"PermissionDenied: 本会话写入的脚本 [{script_display}] "
                                f"执行未获批准 —— {_reason}"
                                f"【替代路径】请改用内置工具完成任务"
                                f"（如 read_file 直接读取文件内容），不要反复重试脚本执行。")
                return ""

            def _dir_has_session_written_py(dir_path) -> bool:
                """目录内是否存在「本会话写入」的 .py（pytest 收集/裸跑场景用）。"""
                for _key in self.sandbox.session_written_files:
                    try:
                        _p = Path(_key)
                        if _p.suffix.lower() == ".py" and _p.is_relative_to(dir_path):
                            return True
                    except (OSError, ValueError):
                        continue
                return False

            def _enforce_ancestor_conftest_gate(display: str, target) -> str:
                """[审计 2026-10-10 A#1] 收集路径**祖先链**上的 conftest.py 闸门。

                pytest 会自动加载从 rootdir 到收集路径的每一级 conftest.py ——
                会话写入工作区根的 conftest.py 后执行 ``pytest tests/``，它同样
                会被加载执行，但既不在 ``tests/`` 子树内（目录检查覆盖不到）、
                也不是收集参数本身（文件检查覆盖不到）→ 闸门缺口（任意代码执行）。
                这里对每个收集路径补查祖先链：若某会话写入的 conftest.py 是它的
                祖先或自身，必须过会话写入审批。返回空串 = 放行。
                """
                for _key in self.sandbox.session_written_files:
                    try:
                        _written = Path(_key)
                        if _written.name != "conftest.py":
                            continue
                        target_path = Path(target)
                        # 「祖先或自身」判据必须落在**父目录**上：目标路径不可能
                        # 是某个 conftest.py 文件的子路径，`is_relative_to(文件)`
                        # 对目录/文件参数永远为 False（不落地的判据=闸门形同虚设）。
                        if not (target_path == _written
                                or target_path.is_relative_to(_written.parent)):
                            continue
                        # [第2轮 N3] 去重：本闸门只在「既有检查覆盖不到」时才弹卡 ——
                        # ① 目标就是这份 conftest 自身（文件参数）：文件分支的
                        #    _enforce_script_gates 会对同一路径发起同一审批；
                        # ② 目标目录内已有会话写入 .py（含 conftest）：目录分支的
                        #    _dir_has_session_written_py 会审批。
                        # 两者均跳过，避免同一次命令弹两张卡。
                        if target_path == _written:
                            continue
                        if target_path.is_dir() and _dir_has_session_written_py(target_path):
                            continue
                        _approved, _reason = self.permissions.check_session_script_exec(
                            display, {"command": command}, interactive=interactive)
                        if not _approved:
                            return (
                                f"PermissionDenied: 本会话写入的 conftest.py 位于收集路径"
                                f" [{display}] 的祖先链上，pytest 会自动加载执行，"
                                f"执行未获批准 —— {_reason}"
                                f"【替代路径】请改用内置工具完成任务"
                                f"（如 read_file 直接读取文件内容），不要反复重试脚本执行。")
                    except (OSError, ValueError):
                        continue
                return ""

            # [P0 修复·增强] shell=False 挡不住 Python 自身的任意代码执行：
            # `python -c "import shutil;shutil.rmtree('/')"` 既在白名单内又不命中参数黑名单。
            # 因此对 python 收紧为「只允许运行工作区内的 .py 脚本」，其余调用形式一律拒绝。
            if prog in ("python", "python3"):
                first_arg = argv[1] if len(argv) > 1 else ""
                if first_arg in ("-c", "--command"):
                    # [W7b 死路修复] 旧文案引导「写入 .py 脚本后运行」，但评测环境
                    # 的受控目录闸门会再拒绝工作区脚本（output/xxx.py）——模型被
                    # 引导进「写脚本 → 再被拦」的循环（PDF-003 实测三层拦截后答案
                    # 残缺）。改为直接引导内置工具，一步到位。
                    return (
                        "安全拦截：`python -c` 可执行任意代码，已被禁用。"
                        "【替代路径】本环境不提供脚本执行；请改用内置工具完成任务："
                        "读文件用 read_file（PDF 自动提取文本、offset 为起始页可续读），"
                        "搜索用 grep / search_files，写产物用 write_file，"
                        "真题抽题用 read_exam_paper。"
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
                        _script_path = self.sandbox.resolve_safe_path(first_arg, read_only=True)
                    except SecurityException as e:
                        return (f"安全拦截：python 脚本必须位于工作区内或已授权目录，"
                                f"已拒绝 {first_arg}（{e}）")

                    # [B2a ①②] 两道闸门（受控目录 + 会话写入审批），见上方闭包。
                    _gate_msg = _enforce_script_gates(first_arg, _script_path)
                    if _gate_msg:
                        return _gate_msg

            # [审计 2026-09-30 P0-2] pytest 纳入「脚本执行」闸门：它同样会收集并
            # 执行任意 Python 测试文件，此前只过白名单、不进 B2a 闸门 ——
            # `write_file tests/test_x.py` → `pytest tests/test_x.py` 可在 auto
            # 模式零审批执行任意 Python。判定规则：
            #   * 位置参数（存在的路径；``file.py::test_name`` 取 ``::`` 前段）
            #     全部过受控目录 + 会话写入闸门；目录参数额外检查其内是否有
            #     本会话写入的 .py；
            #   * 裸 pytest（无位置参数，收集整棵工作区）：会话写入集合含
            #     工作区内 .py 时同样触发会话写入闸门；
            #   * 危险选项直接拒绝：``--pyargs``（按模块名执行代码）、
            #     ``-p``（加载任意插件，仅允许 ``no:`` 前缀）、``-c``/``--config``
            #     与 ``-o``/``--override-ini``（外部 ini/覆盖项可经 addopts
            #     注入插件加载）。
            if prog == "pytest":
                _pytest_info_only = any(
                    _strip_token_quotes(_t) in ("--version", "-V", "-h", "--help")
                    for _t in argv[1:]
                )
                if not _pytest_info_only:
                    for _tok in argv[1:]:
                        _cand = _strip_token_quotes(_tok)
                        if _cand == "--pyargs":
                            return "安全拦截：pytest --pyargs 以模块名执行代码，不在允许范围内。"
                        if _cand == "-c" or _cand.startswith("--config"):
                            return "安全拦截：pytest 禁止指定外部配置文件（可经 addopts 注入代码加载）。"
                        if _cand == "-o" or _cand.startswith("--override-ini"):
                            return "安全拦截：pytest 禁止 --override-ini（可经 addopts 注入代码加载）。"
                    # -p 插件选项：分离（-p name）与粘连（-pname）两种写法都检查
                    for _i, _tok in enumerate(argv[1:]):
                        _cand = _strip_token_quotes(_tok)
                        _val = ""
                        if _cand == "-p":
                            _val = _strip_token_quotes(argv[_i + 2]) if _i + 2 < len(argv) else ""
                        elif _cand.startswith("-p") and not _cand.startswith("--") and len(_cand) > 2:
                            _val = _cand[2:]
                        else:
                            continue
                        if not _val.startswith("no:"):
                            return ("安全拦截：pytest 仅允许 `-p no:...`（禁用插件）；"
                                    "加载任意插件不在允许范围内。")
                    _pytest_checked = 0
                    for _tok in argv[1:]:
                        _cand = _strip_token_quotes(_tok)
                        if not _cand or _cand.startswith("-"):
                            continue
                        _cand = _cand.split("::", 1)[0]      # node id 语法：取文件部分
                        try:
                            _p = self.sandbox.resolve_safe_path(_cand, read_only=True)
                        except SecurityException:
                            continue     # 非路径 token（-k/-m 的值等），交由下方通用检查
                        if not _p.exists():
                            continue
                        _pytest_checked += 1
                        if _p.is_dir():
                            if not _is_script_exec_allowed_tree(_p, self.sandbox.workspace_root):
                                return (
                                    f"安全拦截：仅允许在受控目录"
                                    f"（{', '.join(_SCRIPT_EXEC_ALLOWED_PREFIXES)}）下运行测试，"
                                    f"已拒绝 [{_cand}]。"
                                )
                            # [审计 2026-10-10 A#1] 祖先链 conftest.py 闸门：目录与
                            # 文件参数统一覆盖（pytest 对两者都会加载祖先链 conftest）。
                            # [第2轮 N5] 顺序调整：本闸门移到白名单检查之后 ——
                            # 注定被白名单拒绝的目录（如 01-数学/）不再先弹一张
                            # 「批准了也会被拒」的审批卡。
                            _gate_msg = _enforce_ancestor_conftest_gate(_cand, _p)
                            if _gate_msg:
                                return _gate_msg
                            if _dir_has_session_written_py(_p):
                                _approved, _reason = self.permissions.check_session_script_exec(
                                    _cand, {"command": command}, interactive=interactive)
                                if not _approved:
                                    return (f"PermissionDenied: 本会话写入的测试脚本位于 [{_cand}]，"
                                            f"执行未获批准 —— {_reason}")
                        else:
                            # [审计 2026-10-10 A#1] 文件参数同样覆盖祖先链 conftest
                            # （pytest 会加载从 rootdir 到该文件的每一级 conftest）。
                            _gate_msg = _enforce_ancestor_conftest_gate(_cand, _p)
                            if _gate_msg:
                                return _gate_msg
                            _gate_msg = _enforce_script_gates(_cand, _p)
                            if _gate_msg:
                                return _gate_msg
                    if _pytest_checked == 0 and _dir_has_session_written_py(self.sandbox.workspace_root):
                        # 裸 pytest：无位置参数 → 收集整棵工作区，本会话写入的 .py
                        # 同样会被收集执行，与「写脚本再跑」同一语义。
                        _approved, _reason = self.permissions.check_session_script_exec(
                            ".", {"command": command}, interactive=interactive)
                        if not _approved:
                            return (f"PermissionDenied: 本会话写入的脚本可能被 pytest 收集执行，"
                                    f"执行未获批准 —— {_reason}")

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

            # [审计 2026-09-30 P1-4] git 配置注入收口：`git -c key=value` 可注入
            # alias.<x>=!cmd、core.fsmonitor / core.sshCommand 等 —— 实测
            # `git -c alias.kyprobe=!pwd kyprobe` 与 `git -c core.fsmonitor=<hook>`
            # 均能执行任意程序（配置键可指向外部程序）。收口规则：
            #   * 值以 `!` 开头（alias 执行 shell）→ 拒绝；
            #   * 键命中执行类黑名单（alias./core.fsmonitor/core.sshcommand/
            #     core.pager/core.editor/core.hookspath/core.askpass/credential./
            #     diff.external/uploadpack./receive./pager. 等）→ 拒绝；
            #   * --upload-pack / --receive-pack / --template 指定外部程序/目录 → 拒绝。
            if prog == "git":
                _git_deny_key_prefixes = (
                    "alias.", "core.fsmonitor", "core.sshcommand", "core.pager",
                    "core.editor", "core.hookspath", "core.askpass", "core.gitproxy",
                    "credential.", "diff.external", "uploadpack.", "receive.",
                    "sequence.editor", "pager.",
                )
                for _i, _tok in enumerate(argv[1:]):
                    _cand = _strip_token_quotes(_tok)
                    if (_cand.startswith("--upload-pack") or _cand.startswith("--receive-pack")
                            or _cand.startswith("--template")):
                        return ("安全拦截：git 的 --upload-pack/--receive-pack/--template "
                                "可加载外部程序或目录，已被禁用。")
                    _kv = None
                    if _cand == "-c":
                        _kv = argv[_i + 2] if _i + 2 < len(argv) else ""
                    elif _cand.startswith("-c") and not _cand.startswith("--") and len(_cand) > 2:
                        _kv = _cand[2:]
                    if _kv is None:
                        continue
                    _kv = _strip_token_quotes(str(_kv))
                    _key, _eq, _val = _kv.partition("=")
                    if not _eq:
                        return f"安全拦截：git -c 参数格式非法 [{_kv}]（应为 key=value）。"
                    if _val.strip().startswith("!"):
                        return "安全拦截：git -c 配置值以 `!` 开头会执行 shell 命令，已被禁用。"
                    _key_l = _key.strip().lower()
                    if any(_key_l.startswith(_k) for _k in _git_deny_key_prefixes):
                        return (f"安全拦截：git -c 配置键 [{_key}] 可触发外部程序执行，"
                                f"已被禁用。")

            # [审计 2026-09-30 P1-5] 执行加固：
            #   * timeout 由模型参数控制且无上限 —— clamp 到 [1, 300]（300s 覆盖
            #     跑测试套件等正常长任务，同时阻断「极大 timeout + 挂起命令」）；
            #   * capture_output 会把全部输出读进内存后才在下方截断 —— 改为把
            #     stdout/stderr 重定向到临时文件，只读回前 1MB，内存占用有界
            #     （`cat /dev/zero` 类无限输出不再撑爆内存）。
            try:
                timeout = max(1, min(int(timeout), 300))
            except (TypeError, ValueError):
                timeout = 30
            _MAX_OUT_BYTES = 1024 * 1024
            import tempfile
            try:
                with tempfile.TemporaryFile() as _fout, tempfile.TemporaryFile() as _ferr:
                    proc = subprocess.run(
                        argv,
                        shell=False,
                        cwd=str(self.sandbox.workspace_root),
                        stdout=_fout,
                        stderr=_ferr,
                        timeout=timeout,
                    )
                    _fout.seek(0)
                    _ferr.seek(0)
                    out = _fout.read(_MAX_OUT_BYTES + 1).decode("utf-8", errors="replace")
                    err = _ferr.read(_MAX_OUT_BYTES + 1).decode("utf-8", errors="replace")
                _out_note = "\n[... 输出超过 1MB 上限，已截断 ...]" if len(out) > _MAX_OUT_BYTES else ""
                _err_note = "\n[... 输出超过 1MB 上限，已截断 ...]" if len(err) > _MAX_OUT_BYTES else ""
                out = out.strip()
                err = err.strip()
                return (f"ReturnCode: {proc.returncode}\n"
                        f"Stdout: {out[:TOOL_OUTPUT_LIMITS['run_command_stdout_chars']]}{_out_note}"
                        f"\nStderr: {err[:TOOL_OUTPUT_LIMITS['run_command_stderr_chars']]}{_err_note}")
            except subprocess.TimeoutExpired:
                return f"Error: 命令执行超时 ({timeout}秒)"
            except Exception as e:
                return f"Error: 执行异常 - {e}"

        # ─────────────────────────────────────────────────────────────
        # 4. Git 工具
        # ─────────────────────────────────────────────────────────────

        # [修复 2026-10-05·git 工具无超时] 与 run_command 的 [1, 300] clamp
        # 同口径（不超过 300s 上限）：git status/diff 正常亚秒级返回，但出现
        # 锁等待 / 凭据提示 / 巨型 diff 等异常时会无限挂起，卡死整个 agent loop。
        # 这里取 60s（比 run_command 上限更保守），超时返回可读错误。
        _GIT_QUERY_TIMEOUT_SECONDS = 60

        @self.register(
            name="git_status",
            desc="查看当前工作区 Git 版本控制状态与未暂存修改。",
            params_schema={"type": "object", "properties": {}},
            level=PermissionLevel.READ_ONLY
        )
        def git_status() -> str:
            try:
                res = subprocess.run(["git", "status", "--short"], shell=False,
                                     cwd=str(self.sandbox.workspace_root),
                                     capture_output=True, text=True, errors="replace",
                                     timeout=_GIT_QUERY_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                return (f"Error: git status 执行超时 "
                        f"({_GIT_QUERY_TIMEOUT_SECONDS}秒)，已中止（工作区可能被锁或 git 异常挂起）")
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
            try:
                res = subprocess.run(cmd, shell=False, cwd=str(self.sandbox.workspace_root),
                                     capture_output=True, text=True, errors="replace",
                                     timeout=_GIT_QUERY_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                return (f"Error: git diff 执行超时 "
                        f"({_GIT_QUERY_TIMEOUT_SECONDS}秒)，已中止（工作区可能被锁或 git 异常挂起）")
            return res.stdout[:TOOL_OUTPUT_LIMITS["git_diff_chars"]].strip() or "无 Diff 差异"

        # ─────────────────────────────────────────────────────────────
        # 5. 网络工具 (fetch_url & web_search)
        # ─────────────────────────────────────────────────────────────

        @self.register(
            name="fetch_url",
            desc=("获取公开网络 URL 的网页文本内容 (例如查询真题解析或考纲最新动态)。自动过滤脚本与排版噪点。"
                  "【检索信息请优先使用 web_search 工具】——本工具适合打开已知/已选定的具体网页；"
                  "不要拼接搜索引擎 URL 再抓取（主流引擎对直接抓取反爬，返回验证码或无关页）。"
                  "mode=auto(默认): HTTP 直连优先，被 403/SPA 空壳挡住时，若浏览器闸门已开启则自动升级渲染；"
                  "mode=http: 只直连不升级；mode=browser: 只用浏览器渲染。"),
            params_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "目标网页 HTTP/HTTPS URL"},
                    "mode": {
                        "type": "string",
                        "description": "抓取模式：auto(默认，两级自动升级) / http(仅直连) / browser(仅浏览器渲染)"
                    }
                },
                "required": ["url"]
            },
            level=PermissionLevel.NETWORK
        )
        def fetch_url(url: str, mode: str = "auto") -> str:
            if not url.startswith(("http://", "https://")):
                return "Error: 仅支持 http:// 或 https:// 协议"
            # [W10 检索行为引导] 搜索引擎直抓拦截：模型（尤其 flash 级）有
            # 「搜索=手动拼接搜索引擎 URL」的强训练惯性——评测实测 50 题
            # web_search 调用为 0、fetch_url 560 次且大量打在搜索引擎上
            # （Bing/百度/DDG 反爬返回验证码或无关页），既浪费步数又拿不到
            # 有效结果。提示词级引导压不住该惯性，故在工具层拦截并明确引导
            # 到 web_search（多源联邦 + 真实链接 + 来源标注）。
            engine = _search_engine_host(url)
            if engine:
                return (f"Error: 已拦截搜索引擎直抓（{engine}）。本环境已禁用搜索引擎"
                        f"直接抓取——主流引擎对程序化抓取普遍反爬（验证码/无关页），"
                        f"直抓既浪费步数又拿不到有效结果；更换其他搜索引擎重试同样会被"
                        f"拦截。检索信息的正确方式是 **web_search 工具**，请立即改用，"
                        f"例如：\n"
                        f"  web_search(query=\"你要搜索的关键词\")\n"
                        f"它返回多源联邦的真实链接与来源标注，结果 URL 可再用 fetch_url 打开。")
            mode_n = str(mode or "auto").strip().lower()
            if mode_n not in ("auto", "http", "browser"):
                return "Error: mode 仅支持 auto / http / browser"
            # [P1 两级采集] 统一走 fetch_with_fallback：
            #   * SSRF 防护（解析真实 IP + 每跳复检）由 net_guard 与 fetcher 共同保证；
            #   * 浏览器层额外过 S1 入口复检与 S2 route 全量拦截；
            #   * 升级只在闸门开启且 HTTP 层显式受阻时发生，失败保留 HTTP 原结果。
            try:
                try:
                    from intelligence import fetcher as _fetcher
                except ImportError:  # pragma: no cover - 兼容 tools. 包式导入
                    from tools.intelligence import fetcher as _fetcher  # type: ignore

                res = _fetcher.fetch_with_fallback(url, mode=mode_n, timeout=12,
                                                   browser_timeout_sec=15)
            except ValueError as e:
                return f"Error: {e}"
            except UnsafeURLError as e:
                return f"Error: {e}"
            except Exception as e:
                return f"Error 访问网页失败: {e}"

            if not res.is_valid:
                reason = res.escalation or res.access_status
                detail = f"Error 访问网页失败: access_status={res.access_status}"
                if res.status_code:
                    detail += f", http_status={res.status_code}"
                detail += f", tier={res.tier}, 升级尝试={reason}"
                if reason == "BROWSER_DISABLED":
                    detail += "（如需浏览器渲染，请设置环境变量 KY_BROWSER_ACQUISITION=on 并安装 playwright）"
                return detail

            if getattr(res, "evidence_eligible", True) is False or getattr(res, "ssl_verified", True) is False:
                return ("Error 访问网页失败：TLS 证书未验证，正文仅可人工查看，"
                        "不得作为官方证据入库")

            text = res.content or ""
            # 深度过滤 script, style, nav, footer 噪点
            text = re.sub(r"<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", text, flags=re.DOTALL | re.IGNORECASE)
            clean_txt = re.sub(r"<[^>]+>", " ", text)
            clean_txt = re.sub(r"\s+", " ", clean_txt).strip()
            out = clean_txt[:TOOL_OUTPUT_LIMITS["fetch_url_chars"]]
            # [P1 资源嗅探] 页面里若有 PDF/媒体/接口，给 Agent 一条可行动的线索
            # （只登记 URL 元信息，绝不含响应体）
            if res.resources:
                kinds: Dict[str, int] = {}
                for item in res.resources:
                    kinds[item.get("kind", "?")] = kinds.get(item.get("kind", "?"), 0) + 1
                summary = ", ".join(f"{k}×{v}" for k, v in sorted(kinds.items()))
                out += (f"\n[页面资源线索] {summary}；其中 pdf/doc 可用 download_file 落盘"
                        f"（示例：{res.resources[0].get('url', '')}）")
            return out


        # [P1] 二进制产物落盘约定：只做「下载 + 落盘 + 引用登记」，不解析内容。
        # 扩展名白名单是硬约束 —— Agent 不得用本工具把任意可执行文件写进工作区。
        _DOWNLOAD_EXTS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                          ".csv", ".txt", ".md", ".json", ".zip")
        _DOWNLOAD_DIR = "data/downloads"
        _DOWNLOAD_MAX_BYTES = 15 * 1024 * 1024

        @self.register(
            name="download_file",
            desc=("下载公开网络文件（招生简章 / 专业目录 PDF、考纲附件等）到工作区 "
                  "data/downloads/ 并返回落盘相对路径。仅允许文档类扩展名、单文件上限 15MB；"
                  "音视频一律拒（短视频只做元数据与链接，媒体下载默认关闭）。"),
            params_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "文件 URL（http/https，路径需带文档类扩展名）"},
                    "filename": {"type": "string",
                                 "description": "可选：保存文件名或子路径（相对 data/downloads/），默认取 URL 末段"}
                },
                "required": ["url"]
            },
            level=PermissionLevel.NETWORK
        )
        def download_file(url: str, filename: str = "") -> str:
            import urllib.parse as _uparse

            if not url.startswith(("http://", "https://")):
                return "Error: 仅支持 http:// 或 https:// 协议"

            url_path = _uparse.urlsplit(url).path
            url_suffix = Path(url_path).suffix.lower()

            name_raw = (filename or "").strip() or Path(url_path).name
            if not name_raw:
                return "Error: 无法从 URL 推断文件名，请显式传入 filename"
            # 显式穿越 / 绝对路径先拒（不静默净化，避免 Agent 以为写到了指定位置）
            _name_parts = Path(str(name_raw).replace("\\", "/")).parts
            if ".." in _name_parts:
                return "Error: 非法的 filename（禁止路径穿越）"
            if str(name_raw).startswith(("/", "\\")) or re.match(r"^[a-zA-Z]:", str(name_raw)):
                return "Error: 非法的 filename（必须是不带盘符的相对路径）"
            # 净化：只取最后一段文件名，目录分隔符与控制字符一律替换
            name_clean = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_",
                                Path(str(name_raw).replace("\\", "/")).name).strip()
            name_clean = name_clean.strip(".") or "download"
            if ".." in Path(name_clean).parts:
                return "Error: 非法的 filename（禁止路径穿越）"

            suffix = Path(name_clean).suffix.lower() or url_suffix
            if suffix not in _DOWNLOAD_EXTS:
                return (f"Error: 仅允许落盘文档类文件（{'/'.join(_DOWNLOAD_EXTS)}），"
                        f"收到后缀 [{suffix or '无'}]")
            if not name_clean.lower().endswith(suffix):
                name_clean += suffix

            try:
                base = self.sandbox.resolve_safe_path(_DOWNLOAD_DIR, allow_create=True)
                dest = self.sandbox.resolve_safe_path(
                    f"{_DOWNLOAD_DIR}/{name_clean}", allow_create=True)
            except SecurityException as e:
                return f"Error: {e}"
            except Exception as e:
                return f"Error 路径校验失败: {e}"
            if base.resolve() not in dest.resolve().parents:
                return "Error: 非法的 filename（必须落在 data/downloads/ 内）"

            try:
                try:
                    from intelligence import fetcher as _fetcher
                except ImportError:  # pragma: no cover - 兼容 tools. 包式导入
                    from tools.intelligence import fetcher as _fetcher  # type: ignore
                ok = _fetcher.HTTPFetcher(timeout=12).download_file(
                    url, dest, max_bytes=_DOWNLOAD_MAX_BYTES)
            except Exception as e:
                return f"Error 下载失败: {e}"

            if not ok or not dest.exists():
                # 失败原因不外泄细节（download_file 内部已删除半截文件）
                return "Error 下载失败: 目标非 200 / 超过 15MB 上限 / 被 SSRF 拦截"
            size = dest.stat().st_size
            try:
                self.sandbox.register_written_file(dest)  # [B2a] 引用登记
            except Exception:
                pass
            rel = dest.relative_to(self.sandbox.workspace_root)
            return f"已下载: {rel.as_posix()} ({size} 字节)"

        @self.register(
            name="web_search",
            desc=("联网检索（多源联邦）。**任何检索/搜索类需求的首选工具**——查资料、"
                  "找网页或文件（含 PDF 直链）、考研资讯、院校信息。返回带【真实链接】"
                  "的结果（可直接用 fetch_url 打开），并标注来源类型（官方/公众号/社区）"
                  "与失败源，便于判断证据强弱。"),
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
                # [W6 检索接线] 默认走多路查询规划（改写 → 按来源类别分路检索 →
                # 融合去重）：单路原始 query 的召回取决于运气，多路覆盖不同来源
                # 类别（官方站内 / 研招网 / 简章 / 专业目录）。多路失败时回退单路。
                # show_scores=True 把相关性/权威分显式喂给模型（便于选源）。
                service = SearchService.default()
                try:
                    response = service.search_planned(
                        str(query), limit=max(1, int(num_results or 5)),
                        domains=domain_tuple, max_queries=4)
                except Exception:
                    response = service.search(SearchQuery(
                        text=str(query), limit=max(1, int(num_results or 5)),
                        domains=domain_tuple))
                if response.has_results:
                    return format_results(response, show_scores=True)
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
        def read_exam_paper(pdf_path: str, year: str = "", question_no: str = "",
                            keyword: str = "", interactive: bool = True) -> str:
            # [B2b] 工作区外真题 PDF 先过外部读取授权闸门（批准后同目录免再问）
            p = self._resolve_read_path(pdf_path, interactive)
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
            # [审计 2026-09-30 P0-1] 此前标注 READ_ONLY(0)，配合权限层的
            # 「Level 0 无条件放行」使本工具成为零审批执行通道（sympify RCE）。
            # 解析侧已换安全白名单（math_verifier._safe_sympify），此处再提级到
            # SHELL_EXEC(3)：safe 模式下不再被 Level 0 短路放行，auto 模式行为不变。
            level=PermissionLevel.SHELL_EXEC
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
            level=PermissionLevel.READ_ONLY,
            tier=TIER_EXTENDED,  # [K5] 变式检索低频，按需调用
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
            level=PermissionLevel.SAFE_EDIT,
            tier=TIER_EXTENDED,  # [K5] 组卷低频，按需调用
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
            level=PermissionLevel.NETWORK,
            tier=TIER_EXTENDED,  # [K5] 院校侦察低频，按需调用
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
            level=PermissionLevel.SAFE_EDIT,
            tier=TIER_EXTENDED,  # [K5] 考纲比对低频，按需调用
        )
        def diff_syllabus_tool(school: str, major: str, old_text: str = "", new_text: str = "", save_report: bool = True) -> str:
            if not intelligence:
                return "Error: 未加载 intelligence 模块"
            try:
                diff_gen = intelligence.get_syllabus_diff_generator()

                def _resolve_side(raw: str):
                    """解析一侧入参 → (path|None, text|None, err|None)。

                    形似文件路径（无换行且短）时尝试解析为工作区内文件；
                    以考纲常见扩展名结尾却读不到 → 报错，不再把路径字符串
                    当考纲文本静默比对（产出垃圾研报）。
                    """
                    if not raw:
                        return None, None, None
                    if "\n" not in raw and len(raw) < 260:
                        try:
                            p = self.sandbox.resolve_safe_path(raw)
                            if p.is_file():
                                return p, None, None
                        except Exception:
                            pass
                        if re.search(r"\.(md|markdown|txt)\s*$", raw.strip(), re.IGNORECASE):
                            return None, None, f"找不到考纲文件: {raw}"
                    return None, raw, None

                p_old, t_old, err_old = _resolve_side(old_text)
                p_new, t_new, err_new = _resolve_side(new_text)
                if err_old or err_new:
                    return f"Error 考纲比对失败: {err_old or err_new}"

                if p_old and p_new:
                    res = diff_gen.compare_files(p_old, p_new, school=school, major=major)
                else:
                    # [2026-10-08 六领域审查 P0-9 修复·agent 路径假研报]
                    # 此前无 old_text/new_text 时用 408 大纲常量当基准 + 伪造变动
                    # （「图的遍历」改掌握、追加「红黑树插入」），且 compare_texts
                    # 无 is_demo 通道 → save_diff_report 读不到演示标志，假研报以
                    # 正式文件落 04-专业课/考纲变动分析_*.md（无演示前缀/免责首行），
                    # 还会被 context_engine 挂进后续会话当权威引用。
                    # 现改为：无真实文本直接拒绝；演示比对仅在 CLI/REPL
                    # （ky fetch diff，有红色警示 + 演示样例目录隔离）提供。
                    ot = t_old if t_old is not None else (
                        p_old.read_text(encoding="utf-8", errors="ignore") if p_old else "")
                    nt = t_new if t_new is not None else (
                        p_new.read_text(encoding="utf-8", errors="ignore") if p_new else "")
                    if not ot or not nt:
                        _missing = []
                        if not ot:
                            _missing.append("old_text（基准考纲文本或文件路径）")
                        if not nt:
                            _missing.append("new_text（新版考纲文本或文件路径）")
                        return (
                            "Error 考纲比对需要真实文本，缺少参数: " + "、".join(_missing) +
                            "。请先读取考生的考纲文件（如 04-专业课/考试大纲.md）再传入；"
                            "本工具不生成演示数据（防止假研报污染备考资料）。"
                            "如需演示 Diff 功能，请提示考生在终端运行 `ky fetch diff`。")
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
            level=PermissionLevel.SAFE_EDIT,
            tier=TIER_EXTENDED,  # [K5] 资料入库低频，按需调用
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

        # ─────────────────────────────────────────────────────────────
        # 7.5 [B2] 工具自省：list_tools —— 模型运行时可查询的「动态发现」入口
        #      （注册表枚举/过滤走 describe_tools 单一真源；extended 档，只读，
        #      不影响现有工具的注册与 schema 注入行为）
        # ─────────────────────────────────────────────────────────────
        @self.register(
            "list_tools",
            "列出当前可用的 Agent 工具清单（名称 / 权限级别 / 来源 / 一句话说明）。"
            "当你不确定有哪些工具可用、或想找某类能力（如检索、判卷、资料盘点）时调用；"
            "支持按 tier（essential/extended）或 source（builtin/skill/mcp）过滤。",
            {
                "type": "object",
                "properties": {
                    "tier": {"type": "string",
                             "description": "按档位过滤: essential / extended（缺省=全部）"},
                    "source": {"type": "string",
                               "description": "按来源过滤: builtin / skill / mcp（缺省=全部）"},
                },
            },
            PermissionLevel.READ_ONLY,
            tier=TIER_EXTENDED,
        )
        def _list_tools(tier: str = "", source: str = "") -> str:
            rows = self.describe_tools(tier=tier or None, source=source or None)
            if not rows:
                return "（没有匹配的工具；请检查过滤参数 tier/source）"
            lines = [f"当前可用工具 {len(rows)} 个（名称 | 档位 | 权限级别 | 来源）:"]
            for r in rows:
                lines.append(f"- {r['name']} | {r['tier']} | {r['level_name']} | {r['source']}")
                lines.append(f"    {r['desc']}")
            return "\n".join(lines)

        # ─────────────────────────────────────────────────────────────
        # 8. [K5 + B3/B4] 技能桥接工具（extended 档）：
        #    集中声明 6 项（见 skill_bridge.build_skill_specs）
        #    + 自描述 4 项（TOOL_SPEC 契约，见 skill_bridge.build_self_described_specs）
        # ─────────────────────────────────────────────────────────────
        register_skill_tools(self)

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
                level=PermissionLevel.SHELL_EXEC,
                # [K5] MCP 属动态外部能力，归 extended；默认 tier=None 仍返回全部
                # （行为不变），仅 agent.tool_tier=essential 时随 extended 一起让位。
                tier=TIER_EXTENDED,
                source=SOURCE_MCP,
            )

