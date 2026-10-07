# -*- coding: utf-8 -*-
"""
系统级命令模块 (system.py)
包含 version / help / commands / config / doctor / status / subject / build / plan
"""

import json
import os
import sys
from pathlib import Path
from typing import List

try:
    from tools.cli.dispatch import (Command, register, get_command, print_command_help,
                                    print_commands_index, _init_all_commands, list_commands)
    from tools.cli.shared import ROOT, load_config, interpreter_hint
    from tools.cli.repl.renderer import C, colorize, print_status_summary
    from tools.cli.config import interactive_config, manage_syllabi_cli
except ImportError:
    from cli.dispatch import (Command, register, get_command, print_command_help,
                              print_commands_index, _init_all_commands, list_commands)
    from cli.shared import ROOT, load_config, interpreter_hint
    from cli.repl.renderer import C, colorize, print_status_summary
    from cli.config import interactive_config, manage_syllabi_cli

try:
    from tools.version import get_version
except ImportError:
    try:
        from version import get_version
    except ImportError:
        # [K2 版本工程] 兜底与 tools/version.py 的 UNKNOWN_VERSION 同口径：
        # 宁可显式暴露"版本未知"，也不硬编码一个会随版本升级而过期的具体版本号。
        def get_version() -> str:
            return "0.0.0+unknown"


def _cmd_version(args: List[str]) -> None:
    print(f"考研学习链专用终端工具 (ky-cli) v{get_version()} · Python {sys.version.split()[0]}")
    sys.exit(0)


#: ``ky help`` 子命令区的展示顺序：沿用历史手写清单的排列（常用命令保序在前），
#: 未列入名单的新命令自动补到末尾 —— 新增命令不改本表也会出现在帮助里（防漂移）。
_HELP_ORDER = (
    "gui", "wechat", "menu", "status", "memory", "rollback", "today", "done", "tools",
    "review", "calc", "style", "doctor", "plan", "fetch", "ingest", "admission",
    "watch", "compare", "scout", "exam", "exam-submit", "key", "variant", "rag",
    "gain", "map", "diagnose", "fatigue", "relieve", "notify", "build",
    "subject", "config", "serve", "clawbot", "bridge",
)


def _cmd_help(args: List[str]) -> None:
    if len(args) > 1:
        cmd = get_command(args[1])
        if cmd is None:
            print(f"未知参数: {args[1]}，运行 {interpreter_hint()} tools/ky_cli.py --help 查看帮助。")
            sys.exit(1)
        print_command_help(cmd.name)
        return
    _py = interpreter_hint()
    print(f"""
考研学习链专用终端工具 (ky-cli)
用法：
  {_py} tools/ky_cli.py                       启动交互式 Agent 私教终端 (默认 --permission=ask)
  {_py} tools/ky_cli.py --permission=plan    计划模式 (写操作前出具变更计划卡片并创建快照备份)
  {_py} tools/ky_cli.py --permission=auto    全自动沙箱模式 (免交互确认)
  {_py} tools/ky_cli.py --permission=safe    严格只读安全模式 (工作区只读；配置留证快照/审计日志除外)
  {_py} tools/ky_cli.py --host=0.0.0.0        网关对外暴露（需配合 KY_GATEWAY_TOKEN）
  {_py} tools/ky_cli.py --gateway-token=xxx   显式传入网关 token

子命令：""")
    # [W13 R2-1 修复·清单漂移] 子命令区改为从命令注册表动态生成：手写清单每新增
    # 一个命令都要改两处，且旧 ``_init_all_commands`` 的 early-return 会让「只导入
    # 过部分模块」的场景列出残缺清单。现统一走注册表：常用命令按 ``_HELP_ORDER``
    # 保序在前，未列入的新命令自动补末尾，格式与 ``ky commands`` 一致。
    _init_all_commands()
    cmds = list_commands()
    cmds.sort(key=lambda c: _HELP_ORDER.index(c.name)
              if c.name in _HELP_ORDER else len(_HELP_ORDER))
    width = max((len(c.head) for c in cmds), default=0) + 2
    for c in cmds:
        print(f"  {c.head.ljust(width)}{c.desc}")
    print("  diff 为简写转发（等价 ky fetch diff）")
    print("\n查看单个命令用法: ky help <命令>")


def _cmd_commands(args: List[str]) -> int:
    if args[1:]:
        cmd = get_command(args[1])
        print_command_help(cmd.name if cmd else args[1])
        # [低危修复] 未知子命令此前静默返回 0，脚本无法据退出码判断失败。
        return 0 if cmd else 1
    print_commands_index()
    return 0


def _cmd_config(args: List[str]) -> None:
    interactive_config()


def _cmd_doctor(args: List[str]) -> None:
    try:
        from tools import doctor
        ok = doctor.run_doctor(check_persistence="--check-persistence" in args)
        sys.exit(0 if ok else 1)
    except ImportError:
        try:
            import doctor
            ok = doctor.run_doctor(check_persistence="--check-persistence" in args)
            sys.exit(0 if ok else 1)
        except Exception as e:
            print(f"体检执行异常: {e}")
            sys.exit(1)
    except Exception as e:
        print(f"体检执行异常: {e}")
        sys.exit(1)


def _cmd_status(args: List[str]) -> None:
    print_status_summary()


def _cmd_subject(args: List[str]) -> None:
    cfg = load_config()
    try:
        manage_syllabi_cli(cfg)
    except EOFError:
        print(colorize("\n[!] 检测到输入流结束 (EOF)，科目配置菜单已安全退出，未做任何修改。", C.YELLOW))
        print(colorize("    提示：请在交互式终端运行 `ky subject` 选择菜单项；脚本化场景可直接编辑 ky_config.json。", C.DIM))


def _cmd_build(args: List[str]) -> None:
    build_py = ROOT / "05-考研看板" / "build.py"
    if build_py.exists():
        import subprocess
        # 透传额外参数（如 --cdn）；build.py 通过 sys.argv 判断 CDN 模式。
        # 此前未透传，导致 README/操作手册 承诺的 `ky build --cdn` 静默失效。
        # [W13 R2-5 修复·入口分模式] 本地入口（ky build / 更新看板.bat）默认走
        # **完整模式**：显式 KY_SNAPSHOT_OPT_IN=0，覆盖父进程环境里可能残留的
        # =1（脱敏），保证考生本机看板不丢个人数据。发布副本的脱敏重建由
        # sync_publish 负责（见 tools/sync_publish.py）。
        result = subprocess.run([sys.executable, str(build_py)] + list(args[1:]),
                                cwd=str(ROOT / "05-考研看板"),
                                env={**os.environ, "KY_SNAPSHOT_OPT_IN": "0",
                                     "KY_DASHBOARD_OUTPUT_DIR": "docs/.local"})
        if result.returncode != 0:
            print(colorize("[!] 看板构建失败", C.RED))
            sys.exit(result.returncode or 1)
    else:
        print(colorize("[!] 未找到看板构建脚本", C.RED))
        sys.exit(1)


def _cmd_plan(args: List[str]) -> None:
    try:
        try:
            from tools import study_planner
        except ImportError:
            import study_planner
        study_planner.run_study_plan_wizard(interactive=True)
    except EOFError:
        print(colorize("\n[!] 输入流提前结束 (EOF)，方案设计向导已安全中止，本次填写未保存。", C.YELLOW))
        print(colorize("    向导会按你的报考画像逐项提问（不考数学等情形会自动跳过相应问项），请在交互式终端运行 `ky plan` 完整作答；", C.DIM))
        print(colorize("    脚本化场景请核对输入行数后重试，或在 `ky subject` 中单项调整。", C.DIM))
    except Exception as e:
        print(f"方案设计提示: {e}")


def _cmd_tools(args: List[str]) -> int:
    """[K5] ``ky tools list`` —— Agent 工具注册表审计（只列清单，不执行任何工具）。

    数据源即运行时真源 ToolRegistry：内置工具 + skill_bridge 技能工具（默认），
    MCP 工具需 ``--with-mcp`` 才现场加载（审计命令默认不拉起外部子进程）。
    """
    if len(args) < 2 or args[1] != "list":
        print(colorize("用法: ky tools list [--tier=essential|extended|all] [--json] [--with-mcp]", C.YELLOW))
        print("  列出 Agent 工具注册表（名称 | tier | Level | source | 描述）")
        print("  --with-mcp: 现场加载并附加外部 MCP 工具（默认跳过）")
        return 1

    as_json = "--json" in args or "-j" in args
    with_mcp = "--with-mcp" in args
    tier = "all"
    for a in args[2:]:
        if a.startswith("--tier="):
            tier = a.split("=", 1)[1].strip().lower()
    if tier not in ("essential", "extended", "all"):
        print(colorize(f"[!] 未知 tier: {tier}（可选 essential / extended / all）", C.RED))
        return 1

    try:
        from tools.agent.permissions import LEVEL_NAMES, PermissionManager
        from tools.agent.sandbox import Sandbox
        from tools.agent.tools_impl import ToolRegistry
    except ImportError:
        from agent.permissions import LEVEL_NAMES, PermissionManager
        from agent.sandbox import Sandbox
        from agent.tools_impl import ToolRegistry

    sandbox = Sandbox(workspace_root=ROOT)
    # 只审计注册表、不执行任何工具；safe 模式仅表达「本次不写」的语义。
    permissions = PermissionManager(mode="safe", workspace_root=ROOT)
    registry = ToolRegistry(sandbox=sandbox, permissions=permissions)

    mcp_mgr = None
    if with_mcp:
        try:
            from tools.agent.mcp_client import MCPClientManager
        except ImportError:
            from agent.mcp_client import MCPClientManager
        mcp_mgr = MCPClientManager(workspace_root=ROOT)
        cfg = load_config() or {}
        servers = cfg.get("mcp_servers") if isinstance(cfg.get("mcp_servers"), dict) else {}
        mcp_mgr.load_from_config(servers)
        registry.register_mcp_tools(mcp_mgr)

    try:
        rows = []
        for name, td in registry.tools.items():
            if not with_mcp and td.source == "mcp":
                continue
            if tier != "all" and td.tier != tier:
                continue
            rows.append((name, td))
        _rank = {"essential": 0, "extended": 1}
        rows.sort(key=lambda r: (_rank.get(r[1].tier, 2), r[0]))

        def _level_cell(td):
            """(level 值, 展示文案)；动态定级（callable）显示为"动态"。"""
            if isinstance(td.level, int):
                return td.level, LEVEL_NAMES.get(td.level, str(td.level))
            return "dynamic", "动态（按调用参数定级）"

        if as_json:
            tools_payload = []
            for name, td in rows:
                lv, lv_name = _level_cell(td)
                tools_payload.append({
                    "name": name, "tier": td.tier, "level": lv, "level_name": lv_name,
                    "source": td.source, "desc": td.desc,
                })
            print(json.dumps({"tier": tier, "count": len(tools_payload),
                              "tools": tools_payload}, ensure_ascii=False, indent=2))
            return 0

        title = f"Agent 工具注册表（{len(rows)} 项 · tier={tier}"
        title += " · 含 MCP）" if with_mcp else "）"
        print(colorize(f"\n=== 🧰 {title} ===", C.BOLD))
        width = max((len(n) for n, _ in rows), default=4) + 2
        for name, td in rows:
            _, lv_name = _level_cell(td)
            desc = td.desc if len(td.desc) <= 60 else td.desc[:59] + "…"
            print(f"  {name.ljust(width)}| {td.tier.ljust(9)} | {lv_name.ljust(22)} | "
                  f"{td.source.ljust(7)} | {desc}")
        print(colorize("\n  MCP 工具默认跳过（--with-mcp 现场加载）；"
                       "tier 过滤: --tier=essential|extended|all", C.DIM))
        return 0
    finally:
        if mcp_mgr is not None:
            try:
                mcp_mgr.close_all()
            except Exception:
                pass


def _cmd_audit(args: List[str]) -> int:
    """Read-only permission approval audit query."""
    if len(args) < 2 or args[1] not in ("approvals", "approval"):
        print(colorize("用法: ky audit approvals [--limit=N] [--json]", C.YELLOW))
        return 1
    limit = 100
    as_json = "--json" in args
    for item in args[2:]:
        if item.startswith("--limit="):
            try:
                limit = max(1, min(1000, int(item.split("=", 1)[1])))
            except ValueError:
                print(colorize("[!] --limit 必须是整数", C.RED))
                return 1
    try:
        from tools.agent.approval_audit import read_approval_events, resolve_audit_root
    except ImportError:
        from agent.approval_audit import read_approval_events, resolve_audit_root
    rows = read_approval_events(resolve_audit_root(ROOT), limit)
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    print(colorize(f"\n=== 审批审计（{len(rows)} 条）===", C.BOLD))
    for row in rows:
        decision = "ALLOW" if row.get("allowed") else "DENY"
        print(f"  {decision:5} {row.get('tool', '')} | level={row.get('level')} | "
              f"mode={row.get('mode')} | {row.get('reason', '')}")
    return 0


# 注册系统级命令
register(Command('version', ("--version", "-v", "version"), '', '查看当前版本号（与 pyproject.toml 保持一致）', handler=_cmd_version))
register(Command('help', ("help", "--help", "-h"), '[命令]', '显示帮助；`ky help <命令>` 查看单个命令用法', handler=_cmd_help))
register(Command('commands', ("commands", "cmds"), '', '列出全部子命令', handler=_cmd_commands))
register(Command('config', ("config", "--config"), '', '配置大模型 API Key、视觉模型与机器人 Webhook', handler=_cmd_config, write=True))
register(Command('doctor', ("doctor", "--doctor", "check", "--check"), '[--check-persistence]', '一键系统健康诊断 (Python环境/依赖/状态/连通性)', handler=_cmd_doctor))
register(Command('status', ("status", "--status"), '', '查看考研总战役大盘态势、倒计时、打卡天数与作息节律', handler=_cmd_status))
register(Command('subject', ("subject", "--subject", "syllabus", "--syllabus"), '', '选择考研科目(数一/二/三/396、英一/二)并加载考纲', handler=_cmd_subject, write=True))
register(Command('build', ("build", "--build"), '', '一键重新编译并刷新本地与移动端看板', handler=_cmd_build, write=True))
register(Command('plan', ("plan", "--plan", "profile", "--profile", "onboarding"), '', '启动个人专属定制化必考方案向导', handler=_cmd_plan, write=True))
register(Command('tools', ("tools", "--tools"), 'list [--tier=essential|extended|all] [--json] [--with-mcp]', '审计 Agent 工具注册表（名称 | tier | Level | 来源 | 描述）', handler=_cmd_tools))
register(Command('audit', ("audit", "--audit"), 'approvals [--limit=N] [--json]', '查询权限审批 append-only 审计记录', handler=_cmd_audit))
