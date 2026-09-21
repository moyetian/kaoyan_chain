# -*- coding: utf-8 -*-
"""
考研学习链 (Kaoyan AI Study Chain) · 专有智能终端 CLI (ky-cli)
顶层门面模块 (Facade & Entrypoint Shim)
所有具体功能与命令实现已重构解耦至 tools/cli/ 子系统
"""

import sys
from pathlib import Path

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 保证 tools 目录与项目根目录加入 sys.path
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
for _p in (str(_ROOT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from tools.cli.shared import (
        CONFIG_FILE, HISTORY_FILE, ROOT, SUBJECT_DIRS, COACHING_STYLES,
        atomic_write_text, detect_repl_safe_mode_violation,
        detect_safe_mode_violation, get_today_tasks_data, load_config,
        manage_coaching_style, mark_today_task_done, read_text_fallback,
        read_text_safe, save_config, resolve_major_keyword, resolve_profile_schools
    )
    from tools.cli.repl.renderer import C, colorize, print_status_summary, print_today_tasks_summary, print_command_palette
    from tools.cli.repl.session import grab_clipboard_image, get_clipboard_text
    from tools.cli.repl.router import build_homework_menu, build_weakness_scan_report, build_subject_checkin_brief
    from tools.cli.repl.loop import run_repl
    from tools.cli.agent.engine import build_system_prompt, build_demo_syllabus_text, stream_chat, normalize_openai_url
    from tools.cli.notify import send_to_dingtalk, send_to_feishu, send_to_wechat, send_to_qq, _dingtalk_sign, broadcast_briefing
    from tools.cli.gateway import run_server, create_gateway_handler, start_background_live_server, show_bridge_guide
    from tools.cli.config import configure_llm, configure_vision_model, configure_webhooks, manage_syllabi_cli, interactive_config, run_wechat_clawbot_install
    from tools.cli.dispatch import Command, CommandSpec, _REGISTRY, get_command, list_commands, main, register
except ImportError:
    from cli.shared import (
        CONFIG_FILE, HISTORY_FILE, ROOT, SUBJECT_DIRS, COACHING_STYLES,
        atomic_write_text, detect_repl_safe_mode_violation,
        detect_safe_mode_violation, get_today_tasks_data, load_config,
        manage_coaching_style, mark_today_task_done, read_text_fallback,
        read_text_safe, save_config, resolve_major_keyword, resolve_profile_schools
    )
    from cli.repl.renderer import C, colorize, print_status_summary, print_today_tasks_summary, print_command_palette
    from cli.repl.session import grab_clipboard_image, get_clipboard_text
    from cli.repl.router import build_homework_menu, build_weakness_scan_report, build_subject_checkin_brief
    from cli.repl.loop import run_repl
    from cli.agent.engine import build_system_prompt, build_demo_syllabus_text, stream_chat, normalize_openai_url
    from cli.notify import send_to_dingtalk, send_to_feishu, send_to_wechat, send_to_qq, _dingtalk_sign, broadcast_briefing
    from cli.gateway import run_server, create_gateway_handler, start_background_live_server, show_bridge_guide
    from cli.config import configure_llm, configure_vision_model, configure_webhooks, manage_syllabi_cli, interactive_config, run_wechat_clawbot_install
    from cli.dispatch import Command, CommandSpec, _REGISTRY, get_command, list_commands, main, register

try:
    from tools.skills import list_skills
except ImportError:
    try:
        from skills import list_skills
    except ImportError:
        def list_skills(): return {}

# 向后兼容映射
try:
    from tools.cli.commands import load_all_commands
    load_all_commands()
except ImportError:
    try:
        from cli.commands import load_all_commands
        load_all_commands()
    except ImportError:
        pass

COMMAND_SPECS = tuple(list_commands())
COMMAND_ALIASES = {a: cmd.name for cmd in COMMAND_SPECS for a in (cmd.aliases + (cmd.name,))}
COMMAND_HANDLERS = {cmd.name: cmd.handler for cmd in COMMAND_SPECS if cmd.handler}

if __name__ == "__main__":
    sys.exit(main() or 0)
