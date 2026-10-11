# -*- coding: utf-8 -*-
"""
考研学习链 (Kaoyan AI Study Chain) · 专有智能终端 CLI (ky-cli)
顶层门面模块 (Facade & Entrypoint Shim)
所有具体功能与命令实现已重构解耦至 tools/cli/ 子系统
"""

import sys
from pathlib import Path

try:  # 双导入路径兼容（源码脚本式 / tools 包式）
    from workspace import resolve_workspace_root
except ImportError:  # pragma: no cover
    from tools.workspace import resolve_workspace_root

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 保证 tools 目录与项目根目录加入 sys.path
_ROOT = resolve_workspace_root(__file__)
for _p in (str(_ROOT),):
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
except ImportError as _first_exc:
    try:
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
    except ImportError as _second_exc:
        # [R12 修复·缺依赖双栈] 两条导入路径都失败时，此前直接抛出
        # 「During handling of the above exception...」双栈回溯 —— 缺 rich 等
        # 第三方依赖的新用户无从判断只需 pip install。识别缺失的**第三方**
        # 模块名（cli/tools 是本项目自己的包，缺它们才是安装损坏，保持原样
        # 抛出以便排查），给出可执行的安装指引后退出。
        # [R12 审查补漏·模块式调用] 逐个候选异常取缺失名并跳过布局标记：
        # 模块模式（`python -m tools.ky_cli`）下 tools/ 不在 sys.path，第二条
        # 导入路径必然以「No module named 'cli'」失败 —— 旧实现优先取
        # `_second_exc.name`（"cli"）被判为布局缺失而直接抛出，真正缺失的
        # 第三方模块名（在 _first_exc 里）被遮蔽、提示被跳过。
        for _exc in (_second_exc, _first_exc):
            _top = (getattr(_exc, "name", "") or "").split(".")[0]
            if _top and _top not in ("cli", "tools"):
                print(f"\n[!] 缺少运行依赖：{_top}")
                print(f"    请先安装项目依赖后重试：")
                print(f"      {sys.executable} -m pip install -r requirements.txt")
                print(f"    （或单独安装缺失项：{sys.executable} -m pip install {_top}）\n")
                sys.exit(1)
        raise

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
