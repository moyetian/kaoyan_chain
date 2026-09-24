# -*- coding: utf-8 -*-
"""
系统级命令模块 (system.py)
包含 version / help / commands / config / doctor / status / subject / build / plan
"""

import sys
from pathlib import Path
from typing import List

try:
    from tools.cli.dispatch import Command, register, get_command, print_command_help, print_commands_index
    from tools.cli.shared import ROOT, load_config, interpreter_hint
    from tools.cli.repl.renderer import C, colorize, print_status_summary
    from tools.cli.config import interactive_config, manage_syllabi_cli
except ImportError:
    from cli.dispatch import Command, register, get_command, print_command_help, print_commands_index
    from cli.shared import ROOT, load_config, interpreter_hint
    from cli.repl.renderer import C, colorize, print_status_summary
    from cli.config import interactive_config, manage_syllabi_cli

try:
    from tools.version import get_version
except ImportError:
    try:
        from version import get_version
    except ImportError:
        def get_version() -> str:
            return "3.0.0"


def _cmd_version(args: List[str]) -> None:
    print(f"考研学习链专用终端工具 (ky-cli) v{get_version()} · Python {sys.version.split()[0]}")
    sys.exit(0)


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
  {_py} tools/ky_cli.py --permission=safe    严格只读安全模式 (禁止文件写入与执行)
  {_py} tools/ky_cli.py --host=0.0.0.0        网关对外暴露（需配合 KY_GATEWAY_TOKEN）
  {_py} tools/ky_cli.py --gateway-token=xxx   显式传入网关 token

子命令：
  gui                                         启动 GUI 可视化操作端 (基于 PySide6)
  wechat <关键词> [--max=N] [--save]          多源检索微信公众号考研文章与经验沉淀 (别名: wx)
  menu [action] / tui                         启动终端交互中枢导航器 (TUI) 或执行指定动作
  status                                      查看考研总战役大盘态势、倒计时、打卡天数与作息节律
  memory [status|prune]                       三级分层记忆健康度诊断与滚动修剪归档
  rollback                                    快速回滚 Plan Mode 写入前备份的最近一次文件快照
  today [--json]                              查看今日四科任务清单；加 --json 输出结构化数据
  done <关键词>                               快速将包含关键词的今日任务标记为完成并回写状态
  review [math|eng|pol|pro]                   查看 FSRS 待复测错题列表
  calc <表达式>                               基于 SymPy 高精度数学符号验算 (极限/导数/积分/ODE/矩阵，别名: verify)
  diff [选项]                                 新旧考纲版本变化与动荡率对比研报 (简写=ky fetch diff；帮助见 ky fetch --help)
  style [1/2/3/4]                             查看或动态切换 4 种私教辅导风格
  doctor                                      一键系统健康诊断 (Python环境/依赖/状态/连通性)
  plan                                        启动个人专属定制化必考方案向导
  fetch [info|diff|watch]                     考研招考情报与考纲变动抓取中枢 (研招网/官网/考纲Diff/监控雷达)
  ingest <试题文件路径> [--subject=pro/math]    外部真题/试卷智能切片入库管道 (题型识别/采分点提取/白名单归档)
  admission <高校名> [专业] [--year=2027] [--save] 精准调取研招网与高校官方招考指标与证据链
  watch [高校名] [--check] [--list] [--remove]        高校研究生院最新简章与自命题动态指纹监控雷达
  compare <校1> <校2> [专业] [--save]          双校招考关键指标横向深度对标 (408/自命题/复试线/保护)
  scout <高校名> [专业名] [--save] [--apply]  定向侦察目标高校招生简章、考试大纲、报录比与知乎/B站口碑
  exam [科目] [--count=N] [--save]            基于错题库与核心考点反向靶向组卷
  exam-submit <试卷路径> <作答文本>           自动判卷并输出正答率、采分点与错题归因
  key [list|set] <试卷编号> [题号] ["标准答案"]  管理自测卷的加密标准答案（判卷自动采分依赖它）
  variant <考点关键词>                        四科白名单同类真题变式检索与防幻觉溯源
  map [科目] [--json]                         官方考试大纲知识点图谱与掌握度映射
  diagnose <答题卡文本或文件>                 整卷级多题诊断引擎 (章节失分排行与薄弱处方)
  fatigue                                     检查疲劳度与完成率监控警报
  relieve                                     一键启动智能减负模式 (任务下调 25%，切换为鼓励型)
  notify [内容]                               一键推送今日任务/晨报到微信、QQ、钉钉、飞书群
  build                                       一键重新编译并刷新本地与移动端看板
  subject                                     选择考研科目(数一/二/三/396、英一/二)并加载考纲
  config                                      配置大模型 API Key、视觉模型与机器人 Webhook
  serve [port]                                启动 Webhook 网关与实时 Web 伴侣
                                              (选项: --host=IP --gateway-token= --webhook-token=)
  clawbot                                     启动微信个人号 ClawBot 扫码连接器
  bridge                                      查看各平台双向讲题网关接入指南
""")


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
        ok = doctor.run_doctor()
        sys.exit(0 if ok else 1)
    except ImportError:
        try:
            import doctor
            ok = doctor.run_doctor()
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
        result = subprocess.run([sys.executable, str(build_py)] + list(args[1:]),
                                cwd=str(ROOT / "05-考研看板"))
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


# 注册系统级命令
register(Command('version', ("--version", "-v", "version"), '', '查看当前版本号（与 pyproject.toml 保持一致）', handler=_cmd_version))
register(Command('help', ("help", "--help", "-h"), '[命令]', '显示帮助；`ky help <命令>` 查看单个命令用法', handler=_cmd_help))
register(Command('commands', ("commands", "cmds"), '', '列出全部子命令', handler=_cmd_commands))
register(Command('config', ("config", "--config"), '', '配置大模型 API Key、视觉模型与机器人 Webhook', handler=_cmd_config, write=True))
register(Command('doctor', ("doctor", "--doctor", "check", "--check"), '', '一键系统健康诊断 (Python环境/依赖/状态/连通性)', handler=_cmd_doctor))
register(Command('status', ("status", "--status"), '', '查看考研总战役大盘态势、倒计时、打卡天数与作息节律', handler=_cmd_status))
register(Command('subject', ("subject", "--subject", "syllabus", "--syllabus"), '', '选择考研科目(数一/二/三/396、英一/二)并加载考纲', handler=_cmd_subject, write=True))
register(Command('build', ("build", "--build"), '', '一键重新编译并刷新本地与移动端看板', handler=_cmd_build, write=True))
register(Command('plan', ("plan", "--plan", "profile", "--profile", "onboarding"), '', '启动个人专属定制化必考方案向导', handler=_cmd_plan, write=True))
