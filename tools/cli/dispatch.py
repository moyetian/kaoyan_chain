# -*- coding: utf-8 -*-
"""
考研学习链 CLI 命令注册表与分发中枢 (dispatch.py)
表驱动分发、命令元数据注册、参数处理与安全模式门禁
"""

import argparse
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
    "gui",
    "ingest",
    "key",
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
    """判断是否在请求某个子命令的帮助（如 ky exam --help）"""
    return any(a in ("--help", "-h", "help") for a in args[1:])

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

def build_parser() -> argparse.ArgumentParser:
    """生成包含全部已注册子命令的 ArgumentParser"""
    parser = argparse.ArgumentParser(
        prog="ky",
        description="考研学习链专用终端工具 (ky-cli)",
    )
    subparsers = parser.add_subparsers(dest="command")
    for cmd in list_commands():
        subparsers.add_parser(
            cmd.name,
            aliases=list(cmd.aliases),
            help=cmd.desc,
        )
    return parser

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
        else:
            filtered_args.append(a)

    args = filtered_args

    # 严格只读模式闸门
    try:
        from tools import ky_io
        ky_io.set_read_only_mode(permission_mode == "safe")
    except Exception:
        pass

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
        run_repl(permission_mode=permission_mode, gateway_host=gateway_host, gateway_token=gateway_token)
        return 0

    cmd_token = args[0]
    cmd = _REGISTRY.get(cmd_token)
    if cmd is None:
        print(f"未知参数: {cmd_token}，运行 {interpreter_hint()} tools/ky_cli.py --help 查看帮助。")
        sys.exit(1)

    # 主入口此前把 --host/--gateway-token 整体摘除，导致 `ky serve --host=0.0.0.0`
    # 等文档承诺的参数失效（_cmd_serve/_cmd_view 会自行解析）。此处交回给 handler。
    if passthrough_opts:
        args = args + passthrough_opts

    if _wants_command_help(args) and cmd.name not in _HANDLERS_WITH_OWN_HELP:
        print_command_help(cmd.name)
        sys.exit(0)

    if cmd.handler:
        res = cmd.handler(args)
        return res if isinstance(res, int) else 0

    print(f"命令 {cmd.name} 未绑定处理器。")
    return 1
