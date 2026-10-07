# -*- coding: utf-8 -*-
"""W13 验收修复 · 路 2（CLI 打磨）回归测试。

对应《kaoyan_chain_W13_验收修复任务书_2026-09-29》路 2 的 R2-3 / R2-4 / R2-5：

* R2-3 根 ``ky``（bash）解释器候选必须逐个**可用性验证**（商店 stub 不得入选，
  全失败给出与 ky.bat 对齐的安装指引）；
* R2-4a TUI 纯文本循环序号提示口径 ``[0-10]``（与菜单实际编号一致）；
* R2-4b ``ky menu <action>`` 传 ``batch=True``：未组卷 EXIT 2（与 ky exam 同口径）；
* R2-4c 指令大盘补 ``/paste``（「交作业」菜单推荐它，此前大盘缺项）；
* R2-4d rag 建索引提示用 ``interpreter_hint()`` 而非写死 ``python``；
* R2-4e ``print_command_help`` 未知命令给出 ``ky commands`` 指引；
* R2-5 ``ky build`` 子进程 env 显式 ``KY_SNAPSHOT_OPT_IN=0``（本地完整模式）。

全部用例只读 / 打桩：不落盘、不联网、不触碰真实 ``ky_config.json`` 与 ``docs/``。
R2-1（help 动态生成）的进程内元测试在 ``test_c6_learning_gain.py``（同族 help 断言）。
"""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

KY_SCRIPT = ROOT / "ky"


# ════════════════════════════════════════════════════════════════
# R2-3 · ky bash 启动器：候选可用性验证
# ════════════════════════════════════════════════════════════════

def test_ky_launcher_verifies_candidate_python():
    """候选解释器必须做 3.10+ 可用性探测，不能只看 ``command -v`` 是否存在。

    本机 python3/python 是微软商店 stub：存在但 exit 49、零输出。旧启动器只看
    存在性 → 选中 stub → ``./ky --version`` 零输出。
    """
    text = KY_SCRIPT.read_text(encoding="utf-8")
    assert "version_info >= (3, 10)" in text, "缺少 3.10+ 可用性探测（stub 会再次入选）"
    assert "for candidate in python3 python py" in text, "候选顺序应为 python3 python py"
    assert 'PYTHON_BIN="$candidate"' in text, "通过验证的候选才应被采用"
    assert "未检测到可用的 Python 3.10+" in text, "全失败须给出与 ky.bat 对齐的提示"
    assert "www.python.org/downloads" in text, "全失败须给出安装指引"
    # 旧的「无条件回退 py」写法不得残留（未验证可用性）
    assert 'else\n    PYTHON_BIN="py"' not in text


def test_ky_launcher_bash_syntax_ok():
    """``bash -n`` 语法检查（无 bash 环境则跳过，如纯 Windows 无 Git Bash）。"""
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("未找到 bash，跳过语法检查")
    r = subprocess.run([bash, "-n", str(KY_SCRIPT)],
                       capture_output=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, f"bash -n 失败: {r.stderr}"


# ════════════════════════════════════════════════════════════════
# R2-4a · TUI 纯文本循环序号提示
# ════════════════════════════════════════════════════════════════

def test_tui_text_loop_prompt_range_matches_menu(monkeypatch, capsys):
    """纯文本循环的序号提示必须与菜单实际编号一致。

    [2026-10-06] 原用例把区间写死成 "0-10"，新增菜单项后必然变红。现改为
    **由 ``menu_key_range()`` 派生后比对**——这才是本用例真正的意图（提示与
    菜单同源），且新增菜单项不再需要手工改断言（该提示历史上已因写死而
    失真过两次：0-9 → 0-10）。
    """
    from tools import tui_navigator

    prompts = []

    def fake_input(prompt=""):
        prompts.append(prompt)
        raise EOFError

    monkeypatch.setattr(tui_navigator, "_is_tty", lambda: False)
    monkeypatch.setattr("builtins.input", fake_input)
    tui_navigator._run_text_loop()
    capsys.readouterr()

    assert prompts, "未触发输入提示，测试前置条件不成立"
    expected = f"[{tui_navigator.menu_key_range()}]"
    assert expected in prompts[0], (
        f"序号提示口径与菜单实际编号 {expected} 不一致: {prompts[0]!r}")
    assert "[0-9]" not in prompts[0], "残留旧口径 [0-9]"


def test_tui_menu_key_range_derives_from_menu_options():
    """``menu_key_range()`` 必须由菜单数据派生，且覆盖新增的 11/12 与末位 0。"""
    from tools import tui_navigator

    nums = sorted(int(k) for k, _, _, _ in tui_navigator.MENU_OPTIONS
                  if str(k).isdigit())
    assert tui_navigator.menu_key_range() == f"{nums[0]}-{nums[-1]}"
    assert nums == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], \
        "菜单编号应为 0-12（新增报到 11 / 交作业 12，退出 0 保持末位）"
    # render_menu 的尾部提示与纯文本循环提示必须同源（同一派生函数）
    assert f"[{tui_navigator.menu_key_range()}]" in tui_navigator.render_menu()


# ════════════════════════════════════════════════════════════════
# R2-4b · ky menu <action> 未组卷退出码
# ════════════════════════════════════════════════════════════════

def test_ky_menu_action_exits_2_when_not_composed(monkeypatch, capsys):
    """``ky menu 2`` 未组卷必须 EXIT 2（与 ``ky exam`` 同口径），不得吞成 0。

    走完整分发链：``dispatch.main(["menu", "2"])`` → ``_cmd_menu`` →
    ``execute_action(..., batch=True)``；只把组卷器打桩为「空题库未产卷」。
    """
    from cli import dispatch

    composer = importlib.import_module("skills.exam_composer")
    monkeypatch.setattr(composer, "compose_exam_paper",
                        lambda **kw: {"success": False}, raising=True)

    with pytest.raises(SystemExit) as ei:
        dispatch.main(["menu", "2"])
    assert ei.value.code == 2, f"未组卷退出码应为 2，实际 {ei.value.code!r}"
    out = capsys.readouterr().out
    assert "未组卷" in out, f"缺少「未组卷」文案：{out[-300:]}"


# ════════════════════════════════════════════════════════════════
# R2-4c · 指令大盘补 /paste
# ════════════════════════════════════════════════════════════════

def test_command_palette_lists_paste():
    """「交作业」菜单第 1 步推荐 /paste，指令大盘必须能找到它。"""
    from cli.repl import renderer

    cmds = [c for _title, rows in renderer._palette_sections(False) for c, _d in rows]
    assert any(c.startswith("/paste") for c in cmds), "指令大盘未列 /paste"


# ════════════════════════════════════════════════════════════════
# R2-4d · rag 建索引提示不得写死解释器名
# ════════════════════════════════════════════════════════════════

def test_rag_usage_points_to_ky_index(capsys):
    """``ky rag`` 帮助里的建索引指引必须是可直接照敲的 ``ky index`` 命令。

    [2026-10-05 演进] R2-4d 当时把写死的 `python` 换成 interpreter_hint()；
    现在建索引已补上 CLI 子命令，指引直接给 `ky index` —— 从根上免疫
    「本机 python 是商店 stub」问题，也不再引导用户手敲脚本路径。
    """
    from cli.commands import search as cmd_search

    assert cmd_search.run_rag_search("") == 1, "空查询应打印用法并返回 1"
    out = capsys.readouterr().out
    assert "ky index" in out, "用法应给出 ky index 建索引入口"
    assert "tools/search/indexer.py" not in out, "不得再引导手敲脚本路径"
    assert "python tools/search/indexer.py" not in out, "残留写死的 python 命令"


def test_rag_missing_db_hint_points_to_ky_index(tmp_path, monkeypatch, capsys):
    """知识库缺失提示（run_rag_search 返回 2 的分支）同样指向 ky index。"""
    from cli.commands import search as cmd_search

    try:
        ks_mod = importlib.import_module("tools.search.knowledge_store")
    except ImportError:  # pragma: no cover - 取决于导入路径
        ks_mod = importlib.import_module("search.knowledge_store")

    monkeypatch.setattr(ks_mod, "DEFAULT_DB_PATH", tmp_path / "nope.db", raising=False)
    assert cmd_search.run_rag_search("测试关键词") == 2
    out = capsys.readouterr().out
    assert "ky index" in out, "缺失提示应给出 ky index 建索引入口"
    assert "indexer.py" not in out, "不得再引导手敲脚本路径"


# ════════════════════════════════════════════════════════════════
# R2-4e · 未知命令帮助给出出路
# ════════════════════════════════════════════════════════════════

def test_print_command_help_unknown_points_to_commands(capsys):
    """未知命令帮助必须给出 ``ky commands`` 指引，而不是只报错。"""
    from cli import dispatch

    dispatch.print_command_help("definitely-not-a-command")
    out = capsys.readouterr().out
    assert "未知命令" in out
    assert "ky commands" in out, "未知命令未给出全量清单指引"


# ════════════════════════════════════════════════════════════════
# R2-5 · ky build 子进程 env（本地完整模式）
# ════════════════════════════════════════════════════════════════

def test_cmd_build_forces_full_mode_env(tmp_path, monkeypatch):
    """``ky build`` 子进程 env 必须显式 ``KY_SNAPSHOT_OPT_IN=0``（完整模式）。"""
    from cli.commands import system as system_cmd

    fake_root = tmp_path
    (fake_root / "05-考研看板").mkdir()
    (fake_root / "05-考研看板" / "build.py").write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(system_cmd, "ROOT", fake_root)

    calls = {}

    class _Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        return _Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    system_cmd._cmd_build(["build", "--cdn"])

    assert calls, "subprocess.run 未被调用，测试前置条件不成立"
    assert str(fake_root / "05-考研看板" / "build.py") in calls["cmd"][1]
    assert calls["cmd"][2] == "--cdn", "额外参数须继续透传（--cdn 回归）"
    env = calls["kwargs"].get("env") or {}
    assert env.get("KY_SNAPSHOT_OPT_IN") == "0", \
        f"env 未显式完整模式，实际 {env.get('KY_SNAPSHOT_OPT_IN')!r}"
    # 其余环境变量必须原样透传（不能只留一个键，否则子进程 PATH/编码全丢）
    assert os.environ.get("PATH", "") in env.get("PATH", "") or "PATH" in env


# ════════════════════════════════════════════════════════════════
# R2-1 · help 全量动态生成（子进程端到端 + early-return 单元钉）
# ════════════════════════════════════════════════════════════════

def test_init_all_commands_has_no_early_return(monkeypatch):
    """注册表非空时 ``_init_all_commands`` 也必须继续走 ``load_all_commands``。

    旧实现开头 ``if _ALL_COMMANDS: return``：只要任一命令模块先被导入过，
    其余模块就永不加载，help 清单残缺。此处把注册表置为非空、把 loader 打桩
    为记录调用，直接钉住「不得提前返回」这一契约。
    """
    import types

    from tools.cli import dispatch

    calls = []
    fake_mod = types.ModuleType("tools.cli.commands")
    fake_mod.load_all_commands = lambda: calls.append(1)

    monkeypatch.setattr(dispatch, "_ALL_COMMANDS",
                        [dispatch.Command("zzz-placeholder", (), "", "占位")])
    monkeypatch.setitem(sys.modules, "tools.cli.commands", fake_mod)
    dispatch._init_all_commands()

    assert calls == [1], "注册表非空时 _init_all_commands 提前返回（early-return 回归）"


def test_ky_help_complete_in_fresh_process():
    """全新进程 ``ky help`` 端到端：子命令清单必须覆盖全部命令（含后加载模块）。

    覆盖真实用户路径（不经过 pytest 的模块缓存）：只导入 ``cli.commands.system``
    后直接渲染 help，``session/mount/view`` 等后加载模块的命令必须出现。
    """
    code = (
        "import sys; sys.path.insert(0, 'tools')\n"
        "import cli.commands.system as s\n"
        "s._cmd_help(['help'])\n"
    )
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                       capture_output=True, encoding="utf-8", errors="replace",
                       timeout=120)
    assert r.returncode == 0, f"EXIT={r.returncode}\n{r.stderr[-500:]}"
    for name in ("session", "mount", "view", "version", "help", "commands", "rag", "gain"):
        assert name in r.stdout, f"全新进程 help 缺命令: {name}"
