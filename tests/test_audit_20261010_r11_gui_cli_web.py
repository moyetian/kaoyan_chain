# -*- coding: utf-8 -*-
"""R11 批次 · GUI/CLI/Web 域六项缺陷回归（D#1~D#6，2026-10-10）。

本文件按「先红后绿」流程编写：修复前逐条失败，修复后全绿。

* **D#1** ``05-考研看板/web/snapshot.py``：脱敏快照 ``subjects[].name`` 原样透传
  真实自命题科目名（``_GENERIC_SUBJECT_NAMES`` 缺 ``pro2``，且 subjects 未按 key
  泛化）——与 45-50 行注释「公开快照永远使用通用短名」自相矛盾。
* **D#2** ``tools/cli/dispatch.py``：全局解析消费 ``--permission=``/``-p=`` 后不回填
  passthrough_opts，``_cmd_view`` 的解析分支是死代码 → ``--permission=safe`` 被
  静默降级成 ``ask``（A3a/G13 同类）。
* **D#3** ``05-考研看板/web/radar.py``：脱敏雷达只替换校名；报告文件名里的自命题
  科目名（空格已归一为下划线）进公开产物，残留自检 token 是空格形态、对下划线失明。
* **D#4** ``tools/cli/commands/misc.py`` + ``tools/cli/repl/loop.py``：网关启动失败
  ``or 8088`` 兜底 → 白开 8088 并宣称「已打开」。
* **D#5** ``tools/cli/repl/renderer.py``：欢迎横幅硬编码 12-19，不读配置，与 /status
  面板（走 resolve_exam_date）同屏矛盾。
* **D#6** ``05-考研看板/web/template.html``：document 级 keydown（Space 翻卡）与
  按钮/页签自身 keydown 互不隔离 → Space 双重触发。

夹具全部合成（占位身份「示例农业大学」「618 示例科目」，tmp_path 工作区），
不触碰真实工作区；CLI 用例打桩 run_repl / 后台网关 / 浏览器，不启端口、不开窗口。
"""

from __future__ import annotations

import builtins
import copy
import json
import sys
import webbrowser
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "05-考研看板"
for _p in (str(ROOT), str(DASHBOARD)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ════════════════════════════════════════════════════════════════
# 公共工具
# ════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _reset_read_only_mode():
    """``dispatch.main --permission=safe`` 会置位全局只读标志，逐条复位防污染。"""
    yield
    try:
        import tools.ky_io as _kyio
    except ImportError:  # pragma: no cover
        return
    _kyio.set_read_only_mode(False)


# ════════════════════════════════════════════════════════════════
# D#1 · 脱敏快照 subjects[].name 泛化
# ════════════════════════════════════════════════════════════════

_D1_DATA = {
    "subjects": [
        {"key": "pro", "name": "801 信号与系统", "icon": "", "color": "#111111",
         "dark": "#222222", "notes": None, "ok": True},
        {"key": "pro2", "name": "802 数据结构", "icon": "", "color": "#333333",
         "dark": "#444444", "notes": None, "ok": True},
    ],
    "maps": {
        "pro": {"subject_name": "801 信号与系统", "chapters": []},
        "pro2": {"subject_name": "802 数据结构", "chapters": []},
    },
    "memo": [], "weak": [], "metrics": [], "plan": {}, "trend": [],
}


def test_d1_sanitize_generic_subject_names():
    """脱敏后 subjects[].name 与 maps[].subject_name 均须为通用短名。"""
    from web.snapshot import sanitize_public_data

    safe = sanitize_public_data(copy.deepcopy(_D1_DATA))
    names = {s["key"]: s["name"] for s in safe["subjects"]}
    assert names["pro"] == "专业课"
    assert names["pro2"] == "专业课二", "pro2 的自命题科目全称泄漏进公开快照"
    # maps 泛化机制保持不变（pro2 入表后同样受益）
    assert safe["maps"]["pro"]["subject_name"] == "专业课"
    assert safe["maps"]["pro2"]["subject_name"] == "专业课二"


def test_d1_negative_local_full_mode_keeps_real_names(monkeypatch, tmp_path):
    """阴性对照：本地完整模式（KY_SNAPSHOT_OPT_IN=0）不脱敏，行为不变。"""
    from web.snapshot import write_state_snapshot

    monkeypatch.setenv("KY_SNAPSHOT_OPT_IN", "0")
    out = tmp_path / "state_snapshot.json"
    write_state_snapshot(copy.deepcopy(_D1_DATA), out)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["meta"]["opt_in"] is False
    assert "sanitized" not in payload["meta"]
    assert payload["data"]["subjects"][0]["name"] == "801 信号与系统"
    assert payload["data"]["subjects"][1]["name"] == "802 数据结构"


# ════════════════════════════════════════════════════════════════
# D#2 / D#4 · CLI view 命令
# ════════════════════════════════════════════════════════════════

def _stub_view_deps(monkeypatch, recorded, live_port=None):
    """打桩 _cmd_view 的三个副作用出口：后台网关 / 浏览器 / run_repl。

    [隔离要点] 只打桩**注册表实际持有的 handler 所在模块**（其 globals 才是
    执行时查找 ``start_background_live_server`` 的地方），绝不 importlib 探测
    ``cli.*`` 第二副本 —— 双导入副本在 import 时重新 ``register()``，会把
    注册表里的 handler 换成另一份实现，污染同进程的其他 CLI 测试（实测会让
    ``ky serve`` 真启监听端口）。``run_repl`` 则由 ``_cmd_view`` 函数体内
    ``from tools.cli.repl.loop import run_repl`` 按模块名解析，直接打桩该模块。
    返回本次生效的 view handler，供用例直接调用。
    """
    from tools.cli import dispatch
    import tools.cli.repl.loop  # noqa: F401  _cmd_view 函数体按此名导入 run_repl

    dispatch._init_all_commands()
    cmd = dispatch.get_command("view")
    assert cmd is not None and cmd.handler is not None
    view_mod = sys.modules[cmd.handler.__module__]

    monkeypatch.setattr(webbrowser, "open",
                        lambda url: recorded["opened"].append(url))
    monkeypatch.setattr(view_mod, "start_background_live_server",
                        lambda *a, **k: live_port)

    def _rec_repl(**kw):
        recorded["repl_kwargs"].append(kw)

    monkeypatch.setattr(sys.modules["tools.cli.repl.loop"], "run_repl", _rec_repl)
    return cmd.handler


def test_d2_view_permission_safe_reaches_repl(monkeypatch):
    """`ky view --permission=safe` 必须把 safe 原样交给 REPL，不得静默降级 ask。"""
    from tools.cli import dispatch

    recorded = {"opened": [], "repl_kwargs": []}
    _stub_view_deps(monkeypatch, recorded, live_port=None)
    rc = dispatch.main(["view", "--permission=safe"])
    assert rc == 0
    assert recorded["repl_kwargs"], "view 必须进入 REPL"
    assert recorded["repl_kwargs"][-1]["permission_mode"] == "safe"


def test_d2_view_permission_alias_auto(monkeypatch):
    """`-p=auto` 别名经全局规范化后同样必须回填给 view。"""
    from tools.cli import dispatch

    recorded = {"opened": [], "repl_kwargs": []}
    _stub_view_deps(monkeypatch, recorded, live_port=None)
    rc = dispatch.main(["view", "-p=auto"])
    assert rc == 0
    assert recorded["repl_kwargs"][-1]["permission_mode"] == "auto"


def test_d2_negative_default_ask(monkeypatch):
    """阴性对照：不传 --permission 时仍为默认 ask（行为不变）。"""
    from tools.cli import dispatch

    recorded = {"opened": [], "repl_kwargs": []}
    _stub_view_deps(monkeypatch, recorded, live_port=None)
    rc = dispatch.main(["view"])
    assert rc == 0
    assert recorded["repl_kwargs"][-1]["permission_mode"] == "ask"


def test_d4_view_failure_skips_browser(monkeypatch, capsys):
    """网关启动失败（None）→ 不得打开浏览器、不得宣称「已打开」；REPL 照常进入。"""
    recorded = {"opened": [], "repl_kwargs": []}
    view_handler = _stub_view_deps(monkeypatch, recorded, live_port=None)
    view_handler(["view"])
    out = capsys.readouterr().out
    assert recorded["opened"] == [], "网关失败仍打开了浏览器（or 8088 兜底）"
    assert "未启动" in out
    assert recorded["repl_kwargs"], "网关失败也必须继续进入 REPL"


def test_d4_view_success_opens_browser(monkeypatch, capsys):
    """阳性对照：网关启动成功返回 9000 → 打开对应地址。"""
    recorded = {"opened": [], "repl_kwargs": []}
    view_handler = _stub_view_deps(monkeypatch, recorded, live_port=9000)
    view_handler(["view"])
    out = capsys.readouterr().out
    assert recorded["opened"] == ["http://localhost:9000/live"]
    assert "9000" in out


# ── D#4 · REPL /view 分支 ────────────────────────────────────────

class _FakeErrorLogger:
    """确定性错题本桩：全部空转。"""

    def get_due_reviews(self, *a, **k):
        return []

    def generate_blind_quiz(self, *a, **k):
        return ""

    def __getattr__(self, name):
        return lambda *a, **k: None


def _drive_repl(monkeypatch, inputs, live_port):
    """打桩驱动一次 run_repl（不联网、不启端口、不写工作区）。"""
    from tools.cli.repl import loop as repl_loop

    cfg = {"api_key": "", "model": "deepseek-chat", "active_subject": "pro",
           "onboarding_completed": True, "study_plan": {}}
    monkeypatch.setattr(repl_loop, "load_config", lambda: dict(cfg))
    monkeypatch.setattr(repl_loop, "save_config", lambda c: None)
    monkeypatch.setattr(repl_loop, "print_welcome", lambda *a, **k: None)
    monkeypatch.setattr(repl_loop, "start_background_live_server",
                        lambda *a, **k: live_port)
    monkeypatch.setattr(repl_loop, "AgentRunner", None)
    monkeypatch.setattr(repl_loop, "_stdin_is_tty", lambda: False)
    monkeypatch.setattr(repl_loop, "append_live_message", lambda *a, **k: None)
    monkeypatch.setattr(repl_loop, "stream_chat", lambda *a, **k: "SENTINEL")
    monkeypatch.setattr(repl_loop, "print_followup_toolbar", lambda: None)
    monkeypatch.setattr(repl_loop, "build_system_prompt", lambda s: "SYS")
    monkeypatch.setattr(repl_loop, "infer_subject_from_text", lambda t, cur: cur)
    monkeypatch.setattr(repl_loop, "_count_error_records", lambda s: 0)
    monkeypatch.setattr(repl_loop, "_warn_if_mistake_not_archived", lambda *a, **k: None)
    monkeypatch.setattr(repl_loop, "format_subject_hint", lambda a, b: "")
    monkeypatch.setattr(repl_loop, "error_logger", _FakeErrorLogger())

    queue = list(inputs)

    def _fake_input(prompt=""):
        if queue:
            return queue.pop(0)
        raise EOFError

    monkeypatch.setattr(builtins, "input", _fake_input)
    repl_loop.run_repl(permission_mode="ask")


def test_d4_repl_view_no_gateway_no_browser(monkeypatch, capsys):
    """REPL /view：伴侣未启动（None）时不得回落 8088 打开浏览器。"""
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    _drive_repl(monkeypatch, ["/view", "/exit"], live_port=None)
    out = capsys.readouterr().out
    assert opened == [], "live_port=None 仍打开了 :8088（or 8088 兜底）"
    assert "未启动" in out


def test_d4_repl_view_with_gateway_opens(monkeypatch, capsys):
    """阳性对照：REPL /view 在伴侣就绪（9000）时打开对应地址。"""
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    _drive_repl(monkeypatch, ["/view", "/exit"], live_port=9000)
    out = capsys.readouterr().out
    assert opened == ["http://localhost:9000/live"]
    assert "9000" in out


# ════════════════════════════════════════════════════════════════
# D#3 · 脱敏雷达卡片科目名泄漏
# ════════════════════════════════════════════════════════════════

_D3_FILE = "考纲变动分析_示例农业大学_618_示例科目_2027.md"


def _make_radar_workspace(tmp_path):
    """tmp 工作区：04-专业课 下一份考纲变动报告 + ky_config 身份。"""
    subj = tmp_path / "04-专业课"
    subj.mkdir()
    (subj / _D3_FILE).write_text(
        "# 考纲变动分析\n考点动荡率 12.5%\n新增考点 3\n删减考点 1\n考查微调 2\n",
        encoding="utf-8")
    (tmp_path / "ky_config.json").write_text(
        json.dumps({"study_plan": {"school": "示例农业大学", "major": "618 示例科目"}},
                   ensure_ascii=False),
        encoding="utf-8")
    return subj


def test_d3_radar_sanitize_masks_major_in_filename(monkeypatch, tmp_path):
    """脱敏模式下报告文件名里的自命题科目名（下划线形态）必须一并泛化。"""
    from web.radar import build_radar_html

    _make_radar_workspace(tmp_path)
    monkeypatch.setenv("KY_SNAPSHOT_OPT_IN", "1")
    out = build_radar_html(tmp_path)
    assert "示例农业大学" not in out
    assert "618_示例科目" not in out, "科目名（下划线形态）泄漏进公开产物"
    assert "618 示例科目" not in out
    assert "目标院校" in out and "目标专业" in out


def test_d3_negative_full_mode_keeps_names(monkeypatch, tmp_path):
    """阴性对照：本地完整模式（未脱敏）保留真实校名与科目名。"""
    from web.radar import build_radar_html

    _make_radar_workspace(tmp_path)
    monkeypatch.setenv("KY_SNAPSHOT_OPT_IN", "0")
    out = build_radar_html(tmp_path)
    assert "示例农业大学" in out
    assert "618_示例科目" in out


# ════════════════════════════════════════════════════════════════
# D#5 · 欢迎横幅倒计时读配置
# ════════════════════════════════════════════════════════════════

def test_d5_welcome_countdown_reads_config(monkeypatch, capsys):
    """横幅倒计时必须走 resolve_exam_date（配置 exam_date 优先），不再硬编码 12-19。"""
    from tools.cli.repl import renderer

    monkeypatch.setattr(renderer, "load_config",
                        lambda: {"exam_date": "2027-12-18"})
    monkeypatch.setattr(renderer, "list_skills", lambda: {})
    monkeypatch.setattr(renderer, "read_text_safe", lambda *a, **k: "")
    renderer.print_welcome(live_port=None, animate=False)
    out = capsys.readouterr().out
    expected = (date(2027, 12, 18) - date.today()).days
    assert f"{expected} 天" in out, (
        f"横幅倒计时未按配置 exam_date=2027-12-18 计算（期望 {expected} 天）")


# ════════════════════════════════════════════════════════════════
# D#6 · Space 键双重触发守卫（静态断言，禁启浏览器）
# ════════════════════════════════════════════════════════════════

def test_d6_space_key_guard_present():
    """document 级 keydown 必须带可聚焦元素守卫，且原有翻卡逻辑保留。"""
    tpl = (DASHBOARD / "web" / "template.html").read_text(encoding="utf-8")
    marker = "document.addEventListener('keydown'"
    start = tpl.index(marker)
    end = tpl.index("\n});", start)
    block = tpl[start:end]

    guard = "e.target.closest('button,[role=\"tab\"],input,select,textarea')"
    assert guard in block, "document keydown 缺少可聚焦元素守卫（Space 双重触发）"
    assert block.index(guard) < block.index("ArrowRight"), "守卫必须位于处理器开头"
    # 原有翻卡逻辑保留
    assert "ArrowRight" in block and "ArrowLeft" in block
    assert "classList.toggle('flip')" in block


# ════════════════════════════════════════════════════════════════
# N7 + N8 · 脱敏雷达：专业名归一管道对拍 + exp 段 major 替换
# ════════════════════════════════════════════════════════════════

_N8_MAJOR = "085400 示例专业（085400-01）"
_N8_FILE = "考纲变动分析_示例农业大学_085400_示例专业_2027.md"


def _make_n8_workspace(tmp_path, major=_N8_MAJOR):
    """tmp 工作区：04-专业课 下落盘管道产物名的报告 + 带括号方向码的 major。"""
    subj = tmp_path / "04-专业课"
    subj.mkdir()
    (subj / _N8_FILE).write_text(
        "# 考纲变动分析\n考点动荡率 12.5%\n新增考点 3\n删减考点 1\n考查微调 2\n",
        encoding="utf-8")
    (tmp_path / "ky_config.json").write_text(
        json.dumps({"study_plan": {"school": "示例农业大学", "major": major}},
                   ensure_ascii=False),
        encoding="utf-8")
    return subj


def test_n8_radar_sanitize_strips_parenthesized_major(monkeypatch, tmp_path):
    """N8：major 带括号方向码 → 归一管道与落盘一致，标题不留专业名任何形态。"""
    from web.radar import build_radar_html

    _make_n8_workspace(tmp_path)
    monkeypatch.setenv("KY_SNAPSHOT_OPT_IN", "1")
    out = build_radar_html(tmp_path)

    assert "示例农业大学" not in out
    assert "085400_示例专业" not in out, "落盘归一形态泄漏进公开产物"
    assert "085400 示例专业" not in out, "原空格形态泄漏进公开产物"
    assert "示例专业" not in out
    assert "目标专业" in out


def test_n8_radar_norm_matches_syllabus_diff_landing(tmp_path, monkeypatch):
    """N8 对拍：radar 归一函数 == syllabus_diff 真实落盘文件名的 major 段。"""
    from web.radar import _major_filename_token
    from tools.intelligence import syllabus_diff

    monkeypatch.setattr(syllabus_diff, "ROOT", tmp_path)
    gen = syllabus_diff.SyllabusDiffGenerator()
    monkeypatch.setattr(gen, "format_diff_markdown", lambda rd: "X")

    for major in (_N8_MAJOR, "学科教学（思政）", "618 示例科目", "示例  专业"):
        p = gen.save_diff_report(
            {"school": "示例农业大学", "major": major, "year_new": 2027})
        norm = _major_filename_token(major)
        assert f"_{norm}_2027.md" in p.name, (
            f"归一口径漂移: radar={norm!r} 落盘={p.name!r}")


def test_n7_radar_sanitize_scrubs_major_in_exp_bullets(monkeypatch, tmp_path):
    """N7：exp 档案 bullet 里的专业名（空格形态）必须替换为「目标专业」。"""
    from web.radar import build_radar_html

    exp_dir = tmp_path / ".memory" / "experiences"
    exp_dir.mkdir(parents=True)
    (exp_dir / "示例农业大学_085400 示例专业.md").write_text(
        "# 🎓 示例农业大学 社媒经验档案\n\n"
        "- ✅ **085400 示例专业 就业面广** 正文甲\n"
        "- ⚠️ **085400 示例专业 竞争激烈** 正文乙\n",
        encoding="utf-8")
    (tmp_path / "ky_config.json").write_text(
        json.dumps({"study_plan": {"school": "示例农业大学", "major": _N8_MAJOR}},
                   ensure_ascii=False),
        encoding="utf-8")

    monkeypatch.setenv("KY_SNAPSHOT_OPT_IN", "1")
    out = build_radar_html(tmp_path)

    assert "示例农业大学" not in out
    assert "085400 示例专业" not in out, "exp 段 bullet 专业名泄漏进公开产物"
    assert "目标专业 就业面广" in out
    assert "目标专业 竞争激烈" in out


def test_n7_n8_negative_full_mode_keeps_names(monkeypatch, tmp_path):
    """阴性：非 sanitize 模式（KY_SNAPSHOT_OPT_IN=0）保留原名与括号/归一形态。"""
    from web.radar import build_radar_html

    _make_n8_workspace(tmp_path)
    monkeypatch.setenv("KY_SNAPSHOT_OPT_IN", "0")
    out = build_radar_html(tmp_path)

    assert "示例农业大学" in out
    assert "085400_示例专业" in out
