# -*- coding: utf-8 -*-
"""
杂项、辅助与集成命令模块 (misc.py)
包含 notify / rollback / memory / fatigue / relieve / style / clawbot / gui / menu / bridge / serve / view
"""

import sys
import webbrowser
from pathlib import Path
from typing import List

try:
    from tools.cli.dispatch import Command, register
    from tools.cli.shared import (
        COACHING_STYLES,
        ROOT,
        interpreter_hint,
        load_config,
        manage_coaching_style,
    )
    from tools.cli.repl.renderer import C, colorize
    from tools.cli.notify import broadcast_briefing
    from tools.cli.config import run_wechat_clawbot_install
    from tools.cli.gateway import run_server, show_bridge_guide, start_background_live_server
except ImportError:
    from cli.dispatch import Command, register
    from cli.shared import (
        COACHING_STYLES,
        ROOT,
        interpreter_hint,
        load_config,
        manage_coaching_style,
    )
    from cli.repl.renderer import C, colorize
    from cli.notify import broadcast_briefing
    from cli.config import run_wechat_clawbot_install
    from cli.gateway import run_server, show_bridge_guide, start_background_live_server


def _cmd_notify(args: List[str]) -> None:
    cfg = load_config()
    custom = " ".join(args[1:]) if len(args) > 1 else None
    broadcast_briefing(cfg, custom_msg=custom)


def _cmd_rollback(args: List[str]) -> None:
    try:
        from tools.agent import PermissionManager
        pm = PermissionManager(workspace_root=ROOT)
        res = pm.restore_last_checkpoint()
        if res.get("success"):
            print(colorize(f"\n[√ 快照回滚成功] {res.get('message')}\n", C.GREEN))
        else:
            print(colorize(f"\n[!] 快照回滚失败: {res.get('message')}\n", C.YELLOW))
    except Exception as e:
        print(f"回滚执行失败: {e}")


def _cmd_memory(args: List[str]) -> None:
    sub = args[1].lower() if len(args) > 1 else "status"
    try:
        from tools.agent import MemoryManager
        mem_mgr = MemoryManager(workspace_root=ROOT)
        if sub in ("status", "health", "check"):
            health = mem_mgr.get_memory_health()
            print(colorize("\n=== 🧠 三级分层记忆健康度诊断 ===", C.BOLD))
            print(f"总容量消耗: {health['total_tokens']} tokens ({health['total_chars']} 字符)")
            print("-" * 55)
            print(f"{'记忆层级':<12} {'文件路径':<20} {'字符数':<8} {'Tokens':<8} {'健康状态'}")
            print("-" * 55)
            for scope, info in health.get("details", {}).items():
                st = info.get("status", "良好")
                st_code = info.get("status_code", "ok")
                st_color = C.GREEN if (st_code == "ok" or st in ("ok", "良好")) else (C.YELLOW if ("偏大" in st or "需修剪" in st or st_code == "warning") else C.RED)
                raw_p = info.get("path", "")
                if raw_p:
                    try:
                        p_rel = str(Path(raw_p).relative_to(ROOT))
                    except ValueError:
                        try:
                            p_rel = "~/" + str(Path(raw_p).relative_to(Path.home())).replace("\\", "/")
                        except Exception:
                            p_rel = str(raw_p)
                else:
                    p_rel = "-"
                print(f"{scope:<12} {p_rel:<20} {info.get('chars', 0):<8} {info.get('tokens', 0):<8} {colorize(st, st_color)}")
            print("-" * 55)
            print("💡 提示：若某一记忆层膨胀过大，可运行 ky memory prune 进行滚动修剪与决策归档。\n")
        elif sub in ("prune", "trim", "clean"):
            target_scopes = [args[2]] if len(args) > 2 else ["session", "decisions"]
            any_pruned = False
            for target_scope in target_scopes:
                res = mem_mgr.prune_memory(scope=target_scope, max_items=50, archive_to_decisions=True)
                if res.get("pruned"):
                    any_pruned = True
                    print(colorize(f"\n[√ 记忆修剪完成] 作用域: {target_scope}", C.GREEN))
                    print(f"  • 修剪条目: {res.get('pruned_count')} 条")
                    print(f"  • 归档至决策库: {res.get('archived_count')} 条")
                    print(f"  • 剩余条目: {res.get('remaining_count')} 条")
            if not any_pruned:
                print(colorize(f"\n[i] 各层记忆条目未超限，无需修剪。\n", C.CYAN))
            else:
                print()
        else:
            print(colorize(f"未知 memory 子命令: {sub}，支持 status / prune", C.YELLOW))
    except Exception as e:
        print(f"记忆管理执行失败: {e}")


def _cmd_fatigue(args: List[str]) -> None:
    try:
        try:
            from tools import study_planner
        except ImportError:
            import study_planner
        info = study_planner.check_fatigue_alert()
        if info.get("alert"):
            print(colorize(f"\n[⚠️ 疲劳警报触发] 连续 {info.get('consecutive_low_days')} 天低完成率 (均值 {info.get('avg_rate')}%):", C.YELLOW))
            print(info.get("message"))
            print(colorize("\n💡 提示：输入 ky relieve 可立即一键启动减负模式。\n", C.CYAN))
        else:
            print(colorize(f"\n[√ 复习节奏正常] {info.get('message')}\n", C.GREEN))
    except Exception as e:
        print(f"检查疲劳异常: {e}")


def _cmd_relieve(args: List[str]) -> None:
    try:
        try:
            from tools import study_planner
        except ImportError:
            import study_planner
        # [P2-7 修复] --keep-style 只降时长不改风格；--off 一键恢复时长+风格。
        if "--off" in args or "--restore" in args:
            res = study_planner.restore_relief_mode()
            if res.get("success"):
                print(colorize(f"\n[√ 已退出减负模式]", C.GREEN))
                print(f"  • 每日复习总时间: 恢复为 {C.BOLD}{res.get('new_hours')}h{C.RESET}")
                print(f"  • 辅导风格: {C.GREEN}{res.get('style')}{C.RESET}")
                print(f"  • 说明: {res.get('message')}\n")
            else:
                print(colorize(f"[!] {res.get('message')}", C.YELLOW))
            return
        keep_style = "--keep-style" in args
        res = study_planner.apply_relief_mode(keep_style=keep_style)
        if res.get("success"):
            print(colorize(f"\n[√ 减负模式已成功启动]", C.GREEN))
            print(f"  • 每日复习总时间: {res.get('old_hours')}h ➔ {C.BOLD}{res.get('new_hours')}h{C.RESET}")
            print(f"  • 辅导风格: {C.GREEN}{res.get('style')}{C.RESET}")
            print(f"  • 说明: {res.get('message')}\n")
        else:
            print(colorize(f"[!] 启动减负失败: {res.get('message')}", C.RED))
    except Exception as e:
        print(f"启动减负异常: {e}")


def _cmd_style(args: List[str]) -> None:
    choice = args[1] if len(args) > 1 else None
    if not choice:
        cur_s, _ = manage_coaching_style()
        print(colorize(f"\n=== 🎯 当前激活私教辅导风格 ===", C.BOLD))
        print(f"  • 当前风格: {C.GREEN}{cur_s}{C.RESET}")
        print("\n可选风格清单:")
        for k, (name, desc) in COACHING_STYLES.items():
            active_mark = f" {C.GREEN}[当前激活]{C.RESET}" if name == cur_s else ""
            print(f"  [{k}] {name}{active_mark}\n      {desc}")
        print(f"\n切换方式: {interpreter_hint()} tools/ky_cli.py style <1/2/3/4>\n")
    else:
        new_style, changed = manage_coaching_style(choice)
        if changed:
            print(colorize(f"\n[√ 辅导风格切换成功] 当前已激活：{new_style}\n", C.GREEN))
        else:
            print(colorize(f"\n[!] 未识别的辅导风格选项: {choice}，请输入 1、2、3、4\n", C.YELLOW))
            sys.exit(1)


def _cmd_clawbot(args: List[str]) -> None:
    run_wechat_clawbot_install()


def _cmd_gui(args: List[str]) -> None:
    if any(h in args for h in ("-h", "--help")):
        print("""
用法：ky gui [选项]

启动考研学习链桌面可视化图形界面 (基于 PySide6)。

选项：
  -h, --help       显示此帮助信息并退出
""")
        return
    try:
        from tools import ky_gui
        ky_gui.main()
    except ImportError:
        try:
            import ky_gui
            ky_gui.main()
        except ImportError:
            print(colorize("[!] 启动 GUI 失败，请检查是否已安装 PySide6 (pip install PySide6)", C.RED))
            sys.exit(1)


def _cmd_menu(args: List[str]) -> None:
    try:
        from tools import tui_navigator
    except ImportError:
        import tui_navigator
    try:
        from tools.cli.menu_args import parse_menu_args
    except ImportError:
        from cli.menu_args import parse_menu_args
    action, listed, extra = parse_menu_args(args[1:])
    if listed:
        print(tui_navigator.render_header())
        print(tui_navigator.render_menu())
    elif action:
        tui_navigator.execute_action(action, interactive=False, extra=extra)
    else:
        tui_navigator.run_tui_loop()


def _cmd_bridge(args: List[str]) -> None:
    show_bridge_guide()


def _cmd_serve(args: List[str]) -> None:
    port = 8088
    gateway_host = "127.0.0.1"
    gateway_token = ""
    for a in args[1:]:
        if a.isdigit():
            port = int(a)
        elif a.startswith("--host="):
            gateway_host = a.split("=", 1)[1].strip() or "127.0.0.1"
        elif a.startswith("--gateway-token="):
            gateway_token = a.split("=", 1)[1].strip()
    run_server(port=port, host=gateway_host, gateway_token=gateway_token)


def _cmd_view(args: List[str]) -> None:
    gateway_host = "127.0.0.1"
    gateway_token = ""
    permission_mode = "ask"
    for a in args[1:]:
        if a.startswith("--host="):
            gateway_host = a.split("=", 1)[1].strip() or "127.0.0.1"
        elif a.startswith("--gateway-token="):
            gateway_token = a.split("=", 1)[1].strip()
        elif a.startswith("--permission="):
            permission_mode = a.split("=", 1)[1].strip()
    port = start_background_live_server(8088, host=gateway_host) or 8088
    webbrowser.open(f"http://localhost:{port}/live")
    print(f"已在默认浏览器打开实时 LaTeX 伴侣: http://localhost:{port}/live")
    try:
        from tools.cli.repl.loop import run_repl
    except ImportError:
        from cli.repl.loop import run_repl
    run_repl(permission_mode=permission_mode, gateway_host=gateway_host, gateway_token=gateway_token)


# 注册集成辅助命令
register(Command('notify', ("notify", "--notify"), '[内容]', '一键推送今日任务/晨报到微信、QQ、钉钉、飞书群', handler=_cmd_notify, write=True))
register(Command('rollback', ("rollback", "--rollback", "restore", "--restore"), '', '快速回滚 Plan Mode 写入前备份的最近一次文件快照', handler=_cmd_rollback, write=True))
register(Command('memory', ("memory", "--memory"), '[status|prune]', '三级分层记忆健康度诊断与滚动修剪归档', handler=_cmd_memory, write=True))
register(Command('fatigue', ("fatigue", "--fatigue"), '', '检查疲劳度与完成率监控警报', handler=_cmd_fatigue))
register(Command('relieve', ("relieve", "--relieve"), '[--keep-style] [--off]', '一键启动智能减负模式 (任务下调 25%)；--keep-style 不改风格，--off 恢复', handler=_cmd_relieve, write=True))
register(Command('style', ("style", "--style"), '[1/2/3/4]', '查看或动态切换 4 种私教辅导风格', handler=_cmd_style, write=True))
register(Command('clawbot', ("clawbot", "--clawbot"), '', '启动微信个人号 ClawBot 扫码连接器', handler=_cmd_clawbot, write=True))
# [P0 修复·元数据补正] gui/menu 会启动独立进程或执行任意 TUI 动作（可写工作区），
# 此前 write=False 与事实不符，导致只读闸门把它们当只读放行。旧的手写黑名单是拦的，
# 收敛到注册表元数据后必须把这份元数据补对，否则等于安全回退。
register(Command('gui', ("gui", "--gui"), '', '启动 GUI 可视化操作端 (基于 PySide6)', handler=_cmd_gui, write=True))
register(Command('menu', ("menu", "--menu", "tui", "--tui"), '[action] / tui', '启动终端交互中枢导航器 (TUI) 或执行指定动作', handler=_cmd_menu, write=True))
register(Command('bridge', ("bridge", "--bridge", "tunnel", "--tunnel"), '', '查看各平台双向讲题网关接入指南', handler=_cmd_bridge))
register(Command('serve', ("serve", "--serve"), '[port]', '启动 Webhook 网关与实时 Web 伴侣', handler=_cmd_serve))
register(Command('view', ("view", "--view", "--web", "live"), '', '在浏览器打开实时可视化伴侣（Web 伴侣 / Live），需网关端口空闲', handler=_cmd_view))
