# -*- coding: utf-8 -*-
"""UT4 CLI 修复回归测试（teammate fix-cli）。

覆盖 UT4 三沙箱（西医/通信/物理）实测暴露的 REPL/CLI 交互链缺陷：
  CLI-1 打卡按渲染后模块名匹配失败（tools/cli/shared.py）
       —— 磁盘原文 + 同义别名互认，失败时列出候选并用 difflib 模糊建议。
  CLI-2 「/today」固定口令意外走 Agent LLM 工具链（tools/cli/repl/loop.py）
       —— MSYS 路径改写还原 + /today 保持本地确定性渲染，绝不落入 LLM。
  CLI-3 交作业归档受阻通报缺补救指引（tools/cli/agent/engine.py）
       —— 补「交互重跑弹审批 / --permission=auto / 手动录入」三条路。
  CLI-4 status 同屏双倒计时（tools/cli/repl/renderer.py）
       —— 剥离 AGENTS.md 建档静态「(倒计时约 N 天)」后缀，单口径。
  CLI-5 doctor/TUI「四科」文案残留（tools/doctor.py / tools/tui_navigator.py）
       —— 三科（不考数学）考生视角中性化。

全部用例本地确定性：零 LLM、零联网、不触碰真实 ky_config.json / 真实工作区
（cli.shared.ROOT 在导入期固化，测试直接替换模块属性隔离）。
"""
from __future__ import annotations

import builtins
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ═════════════════════════ 公共工具 ═════════════════════════

_SAFE_CFG = {"onboarding_completed": True, "active_subject": "pro",
             "study_plan": {}, "api_key": ""}


def _seed_pro_task(ws: Path, module_name: str) -> Path:
    """在临时工作区生成 04-专业课 今日任务（含指定模块名的一行任务）。"""
    task_dir = ws / "04-专业课" / "_状态"
    task_dir.mkdir(parents=True, exist_ok=True)
    task_file = task_dir / "今日任务.md"
    task_file.write_text(
        "# 今日专业课任务 (2026-10-01)\n\n"
        "| 模块 | 任务内容 | 预计用时 | 完成状态 |\n"
        "|---|---|---|---|\n"
        f"| {module_name} | 聚焦示例考纲与【示例薄弱点】推导 | 72 分钟 | [ ] |\n"
        "| 习题精练 | 选取白名单【示例真题.md】经典大题 2 道动笔书写 | 120 分钟 | [ ] |\n"
        "| 订正归档 | 输入「交作业」逐步采分并录入错题队列 | 48 分钟 | [ ] |\n",
        encoding="utf-8")
    return task_file


def _stub_record_daily_completion(monkeypatch) -> None:
    """打卡成功路径会回调 study_planner.record_daily_completion（写真实
    ky_config.json 的 completion_history）。测试一律打桩双导入别名，
    保证零真实配置写入。"""
    import importlib

    def _noop(rate, total, completed):
        return None

    for _mod_name in ("study_planner", "tools.study_planner"):
        try:
            _mod = importlib.import_module(_mod_name)
        except ImportError:  # pragma: no cover
            continue
        monkeypatch.setattr(_mod, "record_daily_completion", _noop,
                            raising=False)


def _drive_repl(monkeypatch, answers):
    """以打桩输入驱动 run_repl：逐条喂入 answers，耗尽后抛 EOFError 退出。"""
    from tools.cli.repl import loop as loop_mod

    monkeypatch.setattr(loop_mod, "load_config", lambda: dict(_SAFE_CFG))
    monkeypatch.setattr(loop_mod, "start_background_live_server", lambda *a, **k: 0)
    monkeypatch.setattr(loop_mod, "print_welcome", lambda *a, **k: None)
    monkeypatch.setattr(loop_mod, "AgentRunner", None)

    queue = list(answers)

    def _fake_input(prompt=""):
        if queue:
            return queue.pop(0)
        raise EOFError

    monkeypatch.setattr(builtins, "input", _fake_input)
    return loop_mod


# ═════════════════════════ CLI-1 打卡模块名匹配 ═════════════════════════

def test_cli1_checkin_matches_disk_alias_forward(tmp_path, monkeypatch):
    """磁盘名「核心精讲」+ 建档名「核心知识点」：同义别名必须能打卡成功。"""
    from tools.cli import shared as cli_shared

    ws = tmp_path / "ws"
    task_file = _seed_pro_task(ws, "核心精讲")
    monkeypatch.setattr(cli_shared, "ROOT", ws)
    _stub_record_daily_completion(monkeypatch)

    ok, msg = cli_shared.mark_today_task_done("核心知识点", "pro")
    assert ok, f"同义别名打卡应成功：{msg}"
    assert "已完成打卡" in msg
    assert "[x]" in task_file.read_text(encoding="utf-8")


def test_cli1_checkin_matches_disk_alias_reverse(tmp_path, monkeypatch):
    """反向同族：磁盘「核心知识点」+ 输入「核心精讲」同样命中（别名对称）。"""
    from tools.cli import shared as cli_shared

    ws = tmp_path / "ws"
    task_file = _seed_pro_task(ws, "核心知识点")
    monkeypatch.setattr(cli_shared, "ROOT", ws)
    _stub_record_daily_completion(monkeypatch)

    ok, msg = cli_shared.mark_today_task_done("核心精讲", "pro")
    assert ok, f"反向别名打卡应成功：{msg}"
    assert "[x]" in task_file.read_text(encoding="utf-8")


def test_cli1_checkin_exact_match_still_wins(tmp_path, monkeypatch):
    """回归保底：磁盘原名直接打卡不受别名机制影响。"""
    from tools.cli import shared as cli_shared

    ws = tmp_path / "ws"
    task_file = _seed_pro_task(ws, "核心精讲")
    monkeypatch.setattr(cli_shared, "ROOT", ws)
    _stub_record_daily_completion(monkeypatch)

    ok, _ = cli_shared.mark_today_task_done("核心精讲", "pro")
    assert ok
    assert "[x]" in task_file.read_text(encoding="utf-8")


def test_cli1_checkin_failure_lists_modules_and_suggests(tmp_path, monkeypatch):
    """匹配失败必须列出候选模块并给出 difflib 最近似建议（不再冷失败）。"""
    from tools.cli import shared as cli_shared

    ws = tmp_path / "ws"
    task_file = _seed_pro_task(ws, "核心精讲")
    before = task_file.read_bytes()
    monkeypatch.setattr(cli_shared, "ROOT", ws)

    ok, msg = cli_shared.mark_today_task_done("核心精读", "pro")  # 记错名，非别名
    assert not ok
    assert "当前可打卡模块" in msg
    assert "核心精讲" in msg and "习题精练" in msg and "订正归档" in msg
    assert "你是否想打卡「核心精讲」" in msg, f"缺模糊建议：{msg}"
    assert task_file.read_bytes() == before, "失败路径必须零落盘"


def test_cli1_checkin_failure_without_close_match_still_lists(tmp_path, monkeypatch):
    """完全不相关的关键词：不编造建议，但候选清单仍要给出。"""
    from tools.cli import shared as cli_shared

    ws = tmp_path / "ws"
    _seed_pro_task(ws, "核心精讲")
    monkeypatch.setattr(cli_shared, "ROOT", ws)

    ok, msg = cli_shared.mark_today_task_done("甲乙丙丁戊", "pro")
    assert not ok
    assert "当前可打卡模块" in msg
    assert "你是否想打卡" not in msg, "无近似候选时不得编造建议"


def test_cli1_negative_control_cold_fail_is_discriminated(tmp_path, monkeypatch):
    """阴性对照：旧实现（无别名无建议）对本场景只会输出裸「未找到」——
    正向断言的关键词在旧文案里全部缺席，证明用例有区分度。"""
    old_msg = "未找到包含关键词「核心知识点」的今日任务"
    for needle in ("当前可打卡模块", "你是否想打卡", "核心精讲"):
        assert needle not in old_msg, f"对照前提不成立：旧文案已含「{needle}」"


# ═════════════════════════ CLI-2 /today 本地确定性渲染 ═════════════════════════

def test_cli2_mangled_today_normalized():
    """UT4 实测形态：/today 被 Git Bash 改写为 C:/Program Files/Git/today。"""
    from tools.cli.repl.loop import _normalize_mangled_slash_command as norm

    assert norm("C:/Program Files/Git/today") == "/today"
    assert norm("C:\\Program Files\\Git\\today") == "/today"
    assert norm("D:/测试/Git/today") == "/today"
    assert norm("C:/Program Files/Git/tasks") == "/tasks"
    assert norm("C:/Program Files/Git/TODAY") == "/today"  # 大小写不敏感


def test_cli2_normal_inputs_untouched():
    """普通提问、规范指令、带后续内容的路径与命令不得被误改写。"""
    from tools.cli.repl.loop import _normalize_mangled_slash_command as norm

    assert norm("/today") == "/today"
    assert norm("今天英语阅读怎么练") == "今天英语阅读怎么练"
    assert norm("C:/Users/notes/today.md") == "C:/Users/notes/today.md"
    assert norm("C:/Program Files/Git/today 附加说明") == "C:/Program Files/Git/today 附加说明"
    assert norm("") == ""


def test_cli2_today_dispatch_stays_local_no_llm(tmp_path, monkeypatch, capsys):
    """端到端：MSYS 改写形态输入必须命中本地 /today 分支，绝不进入 LLM 链。"""
    from tools.cli.repl import loop as loop_mod

    calls: list = []
    monkeypatch.setattr(loop_mod, "print_today_tasks_summary",
                        lambda *a, **k: calls.append(1))

    def _boom(*_a, **_k):  # 误入 LLM 链即爆，证明「零计费」
        raise AssertionError("/today 不得进入 LLM 交互链")

    monkeypatch.setattr(loop_mod, "stream_chat", _boom)

    _drive_repl(monkeypatch, ["C:/Program Files/Git/today"])
    loop_mod.run_repl(permission_mode="ask")

    assert calls == [1], f"/today 未命中本地渲染分支（调用 {len(calls)} 次）"
    assert "再见" in capsys.readouterr().out  # 会话正常走到 EOF 退出


def test_cli2_plain_today_also_local(tmp_path, monkeypatch, capsys):
    """回归保底：规范 /today 输入仍走本地分支（既有行为不回退）。"""
    from tools.cli.repl import loop as loop_mod

    calls: list = []
    monkeypatch.setattr(loop_mod, "print_today_tasks_summary",
                        lambda *a, **k: calls.append(1))

    def _boom(*_a, **_k):
        raise AssertionError("/today 不得进入 LLM 交互链")

    monkeypatch.setattr(loop_mod, "stream_chat", _boom)

    _drive_repl(monkeypatch, ["/today"])
    loop_mod.run_repl(permission_mode="ask")

    assert calls == [1]
    capsys.readouterr()  # 清空缓冲，避免跨用例残留


# ═════════════════════════ CLI-3 归档受阻补救指引 ═════════════════════════

class _FakeErrorLogger:
    """可编程的 error_logger 打桩：控制「批改前后错题条数」。"""

    def __init__(self, before: int, after: int):
        self.before = before
        self.after = after

    def scan_error_records(self, subject):
        return [{"x": i} for i in range(self.after)]


def test_cli3_mistake_warning_has_three_remedy_paths(monkeypatch, capsys):
    """归档受阻通报必须给出三条可达补救路：组卷判分 / 交互重跑·auto / 手动录入。"""
    from tools.cli.agent import engine

    monkeypatch.setattr(engine, "error_logger", _FakeErrorLogger(0, 0))
    reply = "## 批改总评：得分：6/15\n| 采分点 | ... |\n**[-1分]** 上限丢失（概念漏洞）"
    engine._warn_if_mistake_not_archived("交作业：示例题干 + 作答", reply, "pro", 0)

    out = capsys.readouterr().out
    assert "ky exam pro" in out, "路径①（确定性判分链路）不得缺失"
    assert "--permission=auto" in out, "UT4 实测旧文案未提示 --permission=auto"
    assert "审批" in out, "交互终端重跑可弹审批的指引不得缺失"
    assert "手动" in out and "错题本" in out, "缺手动录入兜底路径"


def test_cli3_no_warning_when_archived(monkeypatch, capsys):
    """阴性对照：错题确实入库（after > before）时不得再告警。"""
    from tools.cli.agent import engine

    monkeypatch.setattr(engine, "error_logger", _FakeErrorLogger(0, 1))
    reply = "## 批改总评：得分：9/15\n[-1分] 采分点缺失"
    engine._warn_if_mistake_not_archived("交作业：示例题干 + 作答", reply, "pro", 0)

    out = capsys.readouterr().out
    assert "补救路径" not in out, "已归档场景误报补救指引"


# ═════════════════════════ CLI-4 status 倒计时单口径 ═════════════════════════

_FAKE_AGENTS_MD = (
    "### 一、学员基本盘与总战役目标（用户自定义配置区）\n"
    "\n"
    "- **目标院校**：`示例大学`\n"
    "- **报考专业**：`000000 示例专业`\n"
    "- **初试日期**：`2026-12-19` (倒计时约 80 天)\n"
    "- **当前备考阶段**：`强化题型攻坚阶段`\n"
)


def _seed_agents(tmp_path: Path) -> Path:
    agents = tmp_path / "AGENTS.md"
    agents.write_text(_FAKE_AGENTS_MD, encoding="utf-8")
    return agents


def test_cli4_status_strips_static_countdown_suffix(
        tmp_path, monkeypatch, capsys):
    """status 同屏双倒计时：快照行的静态「(倒计时约 80 天)」必须被剥离，
    只保留标题按当日重算的动态口径。"""
    from tools.cli.repl import renderer

    _seed_agents(tmp_path)
    monkeypatch.setattr(renderer, "ROOT", tmp_path)
    monkeypatch.setattr(renderer, "load_config", lambda: {})
    monkeypatch.delenv("FORCE_COLOR", raising=False)

    renderer.print_status_summary()
    out = capsys.readouterr().out

    assert "(倒计时约" not in out and "（倒计时约" not in out, \
        f"静态倒计时快照泄漏到状态盘：{out!r}"
    assert "2026-12-19" in out, "初试日期本体必须保留"
    assert "倒计时" in out, "面板标题的动态倒计时口径必须仍在"


def test_cli4_strip_helper_variants():
    """单元覆盖：全/半角括号、负数天数、无后缀值不受影响。"""
    from tools.cli.repl.renderer import _strip_static_countdown_suffix as strip

    assert strip("`2026-12-19` (倒计时约 80 天)") == "`2026-12-19`"
    assert strip("`2026-12-19`（倒计时约 80 天）") == "`2026-12-19`"
    assert strip("`2027-01-05` (倒计时约 -3 天)") == "`2027-01-05`"
    assert strip("`2026-12-19`") == "`2026-12-19`"
    assert strip("`2026-12-19` 及备注 (倒计时约 80 天) 后续文字") == \
        "`2026-12-19` 及备注 (倒计时约 80 天) 后续文字"  # 仅剥离尾部


def test_cli4_negative_control_noop_strip_leaks_suffix(
        tmp_path, monkeypatch, capsys):
    """阴性对照：剥离函数换成恒等时静态后缀必然出现在输出里 —— 主断言有区分度。"""
    from tools.cli.repl import renderer

    _seed_agents(tmp_path)
    monkeypatch.setattr(renderer, "ROOT", tmp_path)
    monkeypatch.setattr(renderer, "load_config", lambda: {})
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setattr(renderer, "_strip_static_countdown_suffix",
                        lambda value: value)

    renderer.print_status_summary()
    out = capsys.readouterr().out
    assert "(倒计时约" in out, "阴性对照失效：恒等剥离也未出现后缀，主断言无区分度"


# ═════════════════════════ CLI-5 四科文案中性化 ═════════════════════════

def test_cli5_doctor_copy_neutral():
    """doctor 第 3 节标题与 docstring 不得再出现「四科」视角残留。"""
    src = (ROOT / "tools" / "doctor.py").read_text(encoding="utf-8")
    assert "四科目录" not in src
    assert "科目目录架构" in src and "科目目录体系" in src


def test_cli5_tui_copy_neutral():
    """TUI「今日四科任务」→「今日各科任务」。"""
    src = (ROOT / "tools" / "tui_navigator.py").read_text(encoding="utf-8")
    assert "今日四科任务" not in src
    assert "今日各科任务" in src
