# -*- coding: utf-8 -*-
"""
考研学习链 CLI 命令注册表与分发中枢 (dispatch.py)
表驱动分发、命令元数据注册、参数处理与安全模式门禁
"""

import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from tools.cli.shared import detect_safe_mode_violation, interpreter_hint, ROOT
except ImportError:
    from cli.shared import detect_safe_mode_violation, interpreter_hint, ROOT

try:
    from tools.cli.repl.renderer import C, colorize
except ImportError:
    try:
        from cli.repl.renderer import C, colorize
    except ImportError:
        class C:
            RESET = "\033[0m"
            BOLD = "\033[1m"
            DIM = "\033[2m"
            BLUE = "\033[94m"
            CYAN = "\033[96m"
            GREEN = "\033[92m"
            YELLOW = "\033[93m"
            RED = "\033[91m"
            MAGENTA = "\033[95m"
        def colorize(t, c): return f"{c}{t}{C.RESET}"

@dataclass
class Command:
    """CLI 命令元信息，支持表驱动注册、--help 渲染与向后兼容 CommandSpec"""
    name: str
    aliases: Tuple[str, ...] = ()
    usage: str = ""
    desc: str = ""
    help: str = ""
    handler: Optional[Callable[[List[str]], Any]] = None
    write: bool = False

    def __post_init__(self):
        if not self.help and self.desc:
            self.help = self.desc
        elif not self.desc and self.help:
            self.desc = self.help

    @property
    def head(self) -> str:
        return f"{self.name} {self.usage}".strip()

CommandSpec = Command

_REGISTRY: Dict[str, Command]
_ALL_COMMANDS: List[Command]

for _mod_name in ("tools.cli.dispatch", "cli.dispatch"):
    if _mod_name in sys.modules and hasattr(sys.modules[_mod_name], "_REGISTRY"):
        _REGISTRY = sys.modules[_mod_name]._REGISTRY
        _ALL_COMMANDS = sys.modules[_mod_name]._ALL_COMMANDS
        break
else:
    _REGISTRY = {}
    _ALL_COMMANDS = []

_HANDLERS_WITH_OWN_HELP: frozenset = frozenset({
    "admission",
    "calc",
    "compare",
    "fetch",
    "gain",
    "gui",
    "ingest",
    "key",
    "rag",
    "scout",
    "watch",
})

def register(cmd: Command) -> None:
    """注册命令到全局注册表"""
    if cmd not in _ALL_COMMANDS:
        _ALL_COMMANDS.append(cmd)
    _REGISTRY[cmd.name] = cmd
    for alias in cmd.aliases:
        _REGISTRY[alias] = cmd

def get_command(name: str) -> Optional[Command]:
    """根据命令名或别名查询 Command 对象"""
    return _REGISTRY.get(name)

def list_commands() -> List[Command]:
    """返回全部已注册的主命令清单"""
    return list(_ALL_COMMANDS)

def _wants_command_help(args: List[str]) -> bool:
    """判断是否在请求某个子命令的帮助（如 `ky exam --help`）。

    [低危修复] 此前对 ``args[1:]`` 做 ``any(a in ("--help","-h","help"))``，
    于是正文里任意位置的字面 ``help``（如 ``ky exam help``）都会被当成帮助请求，
    把本该交给 handler 的参数吞掉。现改为：
      * ``--help`` / ``-h`` 只认**第一个位置参数**或**末尾**（命令行惯例）；
      * 裸词 ``help`` 不再触发（``ky help <命令>`` 由 help 命令自身处理）。
    """
    rest = args[1:]
    if not rest:
        return False
    if rest[0] in ("--help", "-h"):
        return True
    return rest[-1] in ("--help", "-h")

def print_command_help(name: str) -> None:
    """打印单个子命令的用法帮助"""
    cmd = _REGISTRY.get(name)
    if cmd is None:
        print(colorize(f"[!] 未知命令: {name}", C.YELLOW))
        return
    others = [a for a in cmd.aliases if a != cmd.name]
    alias_txt = ("（别名: " + ", ".join(others) + "）") if others else ""
    print(colorize(f"\n=== ky {cmd.head} ===", C.BOLD))
    print(f"  {cmd.desc}")
    if alias_txt:
        print(colorize(f"  {alias_txt}", C.DIM))
    print(f"\n用法:\n  {interpreter_hint()} tools/ky_cli.py {cmd.head}")
    print("  ky " + cmd.head)
    print("\n查看全部命令: ky commands")
    print(f"查看全局帮助: {interpreter_hint()} tools/ky_cli.py --help\n")

def print_commands_index() -> None:
    """列出全部子命令列表 (ky commands)"""
    print(colorize("\n=== 📜 ky 命令总览 ===", C.BOLD))
    all_cmds = list_commands()
    if not all_cmds:
        return
    width = max(len(s.head) for s in all_cmds) + 2
    for cmd in all_cmds:
        print(f"  {cmd.head.ljust(width)}{cmd.desc}")
    print("\n  查看单个命令用法: ky help <命令> 或 ky <命令> --help")
    print(f"  查看全局帮助:     {interpreter_hint()} tools/ky_cli.py --help\n")

def _init_all_commands() -> None:
    """按需导入并加载所有命令模块"""
    if _ALL_COMMANDS:
        return
    try:
        from tools.cli.commands import load_all_commands
        load_all_commands()
    except ImportError:
        try:
            from cli.commands import load_all_commands
            load_all_commands()
        except ImportError:
            pass

def main(argv: Optional[List[str]] = None) -> int:
    """CLI 主入口分发引擎"""
    _init_all_commands()

    raw_args = sys.argv[1:] if argv is None else argv
    permission_mode = "ask"
    gateway_host = "127.0.0.1"
    gateway_token = ""
    webhook_token = ""
    filtered_args = []
    passthrough_opts = []   # 交给子命令 handler 自行解析（serve/view 等需要）

    for a in raw_args:
        if a.startswith("--permission="):
            permission_mode = a.split("=", 1)[1].strip().lower()
        elif a.startswith("-p="):
            permission_mode = a.split("=", 1)[1].strip().lower()
        elif a.startswith("--host="):
            gateway_host = a.split("=", 1)[1].strip() or "127.0.0.1"
            passthrough_opts.append(a)
        elif a.startswith("--gateway-token="):
            gateway_token = a.split("=", 1)[1].strip()
            passthrough_opts.append(a)
        elif a.startswith("--webhook-token="):
            # [S1 修复·口径一致] 此前只全局解析 --gateway-token=，而 --webhook-token=
            # 会落进 filtered_args → args[0] 变成该选项串 → 命中「未知参数」分支。
            # 与 --gateway-token= 同构处理后，裸 `ky --webhook-token=xxx` 的 REPL
            # 路径也能把回调密钥交给后台伴侣，与 `ky serve` / `ky view` 口径一致。
            webhook_token = a.split("=", 1)[1].strip()
            passthrough_opts.append(a)
        else:
            filtered_args.append(a)

    args = filtered_args

    # 严格只读模式闸门
    try:
        from tools import ky_io
        ky_io.set_read_only_mode(permission_mode == "safe")
    except Exception:
        pass

    # [A3a/G13 修复] 权限模式解析走单一实现处（`normalize_mode`）：别名
    # `acceptEdits` 归一到 `auto`，未知模式**显式报错**而不是静默回退成 ask
    # —— 旧行为下 `--permission=SAFE` 会悄悄变成 `ask`，用户以为只读却拿到了
    # 可批准的写权限。
    try:
        from tools.agent.permissions import normalize_mode
    except ImportError:
        from agent.permissions import normalize_mode  # type: ignore
    try:
        permission_mode = normalize_mode(permission_mode)
    except ValueError as exc:
        print(colorize(f"\n[✘ 已拒绝] {exc}\n", C.RED))
        sys.exit(3)

    if permission_mode == "safe":
        violation = detect_safe_mode_violation(args)
        if violation:
            print(colorize(
                f"\n[✘ 已拒绝] 当前处于严格只读安全模式 (--permission=safe)，"
                f"禁止执行写操作命令: {violation}", C.RED))
            print(colorize(
                "    只读模式仅允许查询类命令（status/today/map/key list/memory status/"
                "fatigue/doctor 等）。\n"
                "    如需执行写操作，请去掉 --permission=safe 或改用 --permission=auto。", C.YELLOW))
            sys.exit(3)

    if args and args[0] in ("diff", "--diff"):
        args = ["fetch", "diff"] + args[1:]

    # 无命令参数：进入交互式 REPL
    if not args:
        try:
            from tools.cli.repl.loop import run_repl
        except ImportError:
            from cli.repl.loop import run_repl
        run_repl(permission_mode=permission_mode, gateway_host=gateway_host,
                 gateway_token=gateway_token, webhook_token=webhook_token)
        return 0

    cmd_token = args[0]
    cmd = _REGISTRY.get(cmd_token)
    if cmd is None:
        print(f"未知参数: {cmd_token}，运行 {interpreter_hint()} tools/ky_cli.py --help 查看帮助。")
        sys.exit(1)

    # 主入口此前把 --host/--gateway-token 整体摘除，导致 `ky serve --host=0.0.0.0`
    # 等文档承诺的参数失效（_cmd_serve/_cmd_view 会自行解析）。此处交回给 handler。
    # [G5 修复·透传污染] 此前无条件追加给**每个**子命令，吞自由文本的处理器
    #（done/calc/notify/diagnose/variant 做 join）会把 token 吃进关键词导致
    # 匹配失败。现仅对真正解析它们的 serve/view 回填。
    if passthrough_opts and cmd.name in ("serve", "view"):
        args = args + passthrough_opts

    if _wants_command_help(args) and cmd.name not in _HANDLERS_WITH_OWN_HELP:
        print_command_help(cmd.name)
        sys.exit(0)

    if cmd.handler:
        res = cmd.handler(args)
        return res if isinstance(res, int) else 0

    print(f"命令 {cmd.name} 未绑定处理器。")
    return 1
