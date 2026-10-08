# -*- coding: utf-8 -*-
"""六领域深度审查 P2 批次（TUI/CLI 域 9 条）回归测试。

覆盖：
1. ``ky session rm`` 交互确认（TTY 询问 / ``--yes`` 跳过 / 非 TTY 兼容脚本）；
2. ``ky rag`` 退出码透传（0/1/2 不再把 2 折叠成 1）；
3. ``ky review`` 模块失败不再误报「没有到期错题」；
4. ``ky exam-submit`` 与 ``ky exam`` 退出码同口径（0/1/2）；
5. spinner 清除宽度按真实显示宽度（49 列）而非写死 48；
6. 剪贴板密钥回显统一走 ``_mask_secret``（短密钥不再几乎整体回显）；
7. TUI 单键 ``q`` 交互模式二次确认（防误触）；
8. ``ky session ls`` 表头按显示宽度对齐（CJK 双宽不再错位）；
9. ``ky ingest`` 多余位置参数报错（不再静默丢弃）。

全部使用合成数据（无真实身份）。
"""

import sys
import types
from pathlib import Path

import pytest

from tools.tui.terminal import display_width

# ────────────────────────────────────────────────────────────────
# 公共辅助
# ────────────────────────────────────────────────────────────────


class _TtyStdin:
    def isatty(self):
        return True


class _NonTtyStdin:
    def isatty(self):
        return False


def _cli_session_mod():
    import tools.cli.commands.session as sess_mod
    return sess_mod


def _seed_session_file(ws: Path, sid: str) -> Path:
    d = ws / ".memory" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sid}.jsonl"
    p.write_text(
        '{"type": "session_start", "ts": "2026-01-01T00:00:00.000", '
        '"payload": {"active_subject": "pol"}}\n'
        '{"type": "user", "ts": "2026-01-01T00:00:01.000", '
        '"content": "样例首条消息"}\n',
        encoding="utf-8",
    )
    return p


def _hide_skill_module(monkeypatch, mod_name: str) -> None:
    """让 ``from tools.skills import X`` 与 ``from skills import X`` 均 ImportError。

    手法：删掉两个包对象上的同名属性（已导入过的缓存路径），并把两个包路径下
    的子模块条目置为 None（子模块 import 路径 → ImportError）。
    """
    for pkg_name in ("tools.skills", "skills"):
        pkg = sys.modules.get(pkg_name)
        if pkg is not None:
            monkeypatch.delattr(pkg, mod_name, raising=False)
        monkeypatch.setitem(sys.modules, f"{pkg_name}.{mod_name}", None)


@pytest.fixture(autouse=True)
def _reset_tui_q_pending():
    """条目 7：复位 TUI 单键 q 的二次确认挂起位，避免跨测试状态泄漏。"""
    from tools import tui_navigator
    tui_navigator._q_exit_pending = False
    yield
    tui_navigator._q_exit_pending = False


# ────────────────────────────────────────────────────────────────
# 1. ky session rm 确认
# ────────────────────────────────────────────────────────────────


def test_session_rm_non_tty_direct_delete(tmp_path, monkeypatch, capsys):
    """非 TTY（脚本/管道/既有自动化）：保持直接删除，不阻塞在 input 上。"""
    sess_mod = _cli_session_mod()
    sid = "20260101-120000-rm0001"
    path = _seed_session_file(tmp_path, sid)
    monkeypatch.setattr(sess_mod, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "stdin", _NonTtyStdin())

    sess_mod._cmd_session(["session", "rm", sid[:8]])
    assert "已删除会话" in capsys.readouterr().out
    assert not path.exists()


def test_session_rm_yes_flag_skips_confirm(tmp_path, monkeypatch, capsys):
    """TTY 下带 --yes/-y 直接删除（与 prune 的 --yes 惯例一致）。"""
    sess_mod = _cli_session_mod()
    sid = "20260101-120000-rm0002"
    path = _seed_session_file(tmp_path, sid)
    monkeypatch.setattr(sess_mod, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "stdin", _TtyStdin())

    def _no_input(*a, **k):  # pragma: no cover - 一旦被调用即测试失败
        raise AssertionError("--yes 不应触发确认 input")

    monkeypatch.setattr("builtins.input", _no_input)
    sess_mod._cmd_session(["session", "rm", sid[:8], "--yes"])
    assert "已删除会话" in capsys.readouterr().out
    assert not path.exists()


def test_session_rm_tty_cancel_keeps_file(tmp_path, monkeypatch, capsys):
    """TTY 下默认拒绝（回车/N）：文件保留、给出取消提示。"""
    sess_mod = _cli_session_mod()
    sid = "20260101-120000-rm0003"
    path = _seed_session_file(tmp_path, sid)
    monkeypatch.setattr(sess_mod, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "stdin", _TtyStdin())
    monkeypatch.setattr("builtins.input", lambda *a, **k: "")

    sess_mod._cmd_session(["session", "rm", sid[:8]])
    out = capsys.readouterr().out
    assert "已取消删除" in out
    assert path.exists(), "取消后不得删除文件"


def test_session_rm_tty_confirm_deletes(tmp_path, monkeypatch, capsys):
    """TTY 下回答 y：执行删除。"""
    sess_mod = _cli_session_mod()
    sid = "20260101-120000-rm0004"
    path = _seed_session_file(tmp_path, sid)
    monkeypatch.setattr(sess_mod, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "stdin", _TtyStdin())
    monkeypatch.setattr("builtins.input", lambda *a, **k: "y")

    sess_mod._cmd_session(["session", "rm", sid[:8]])
    assert "已删除会话" in capsys.readouterr().out
    assert not path.exists()


# ────────────────────────────────────────────────────────────────
# 2. ky rag 退出码透传
# ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("code", [0, 1, 2])
def test_rag_exit_code_passthrough(monkeypatch, code):
    """_cmd_rag 必须透传 run_rag_search 的约定退出码（此前 2 被折叠成 1）。"""
    import tools.cli.commands.search as search_mod
    monkeypatch.setattr(search_mod, "run_rag_search", lambda *a, **k: code)
    with pytest.raises(SystemExit) as ei:
        search_mod._cmd_rag(["rag", "示例关键词"])
    assert ei.value.code == code


def test_rag_missing_db_exit_code_is_2_end_to_end(tmp_path, monkeypatch):
    """端到端：库不存在 → run_rag_search 返回 2 → _cmd_rag 退出码 2（不再折叠 1）。"""
    import tools.search.knowledge_store as ks
    import tools.cli.commands.search as search_mod
    db = tmp_path / "data" / "knowledge" / "embeddings.db"
    monkeypatch.setattr(ks, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(ks, "_STORE", None)
    with pytest.raises(SystemExit) as ei:
        search_mod._cmd_rag(["rag", "示例关键词"])
    assert ei.value.code == 2


# ────────────────────────────────────────────────────────────────
# 3. ky review 模块失败误报
# ────────────────────────────────────────────────────────────────


def test_review_module_missing_not_reported_as_no_due(monkeypatch, capsys):
    """error_logger 模块未载入：显式报错，绝不落入「没有到期错题」恭喜分支。"""
    from tools.cli.commands import study
    _hide_skill_module(monkeypatch, "error_logger")

    study._cmd_review(["review"])
    out = capsys.readouterr().out
    assert "未载入" in out
    assert "没有到期" not in out


def test_review_query_exception_not_reported_as_no_due(monkeypatch, capsys):
    """get_due_reviews 抛异常：显式报错，不误报「没有到期」。"""
    from tools.cli.commands import study
    from tools.skills import error_logger as el

    def _boom(*a, **k):
        raise RuntimeError("样例查询失败")

    monkeypatch.setattr(el, "get_due_reviews", _boom)
    study._cmd_review(["review"])
    out = capsys.readouterr().out
    assert "查询 FSRS 待复测错题失败" in out
    assert "没有到期" not in out


def test_review_genuinely_empty_still_congratulates(monkeypatch, capsys):
    """对照组：查询成功且为空才是真「无到期」→ 保留恭喜文案。"""
    from tools.cli.commands import study
    from tools.skills import error_logger as el
    monkeypatch.setattr(el, "get_due_reviews", lambda *a, **k: [])
    study._cmd_review(["review"])
    out = capsys.readouterr().out
    assert "没有到期" in out


# ────────────────────────────────────────────────────────────────
# 4. ky exam-submit 退出码对齐（0/1/2）
# ────────────────────────────────────────────────────────────────


def test_exam_submit_usage_error_returns_1(capsys):
    from tools.cli.commands import study
    assert study._cmd_exam_submit(["exam-submit"]) == 1
    assert "用法" in capsys.readouterr().out


def test_exam_submit_missing_file_returns_1(capsys):
    from tools.cli.commands import study
    assert study._cmd_exam_submit(["exam-submit", "不存在的示例试卷.md", "1. A"]) == 1
    assert "找不到试卷文件" in capsys.readouterr().out


def test_exam_submit_grade_failure_returns_2(monkeypatch, capsys):
    """批改失败（业务上无法完成）→ 2，与 ky exam 的「未组卷 = 2」同口径。"""
    from tools.cli.commands import study
    from tools.skills import exam_composer as ec
    monkeypatch.setattr(ec, "grade_exam_paper",
                        lambda *a, **k: {"success": False, "msg": "样例失败原因"})
    assert study._cmd_exam_submit(["exam-submit", "示例内联试卷文本", "1. A"]) == 2
    assert "样例失败原因" in capsys.readouterr().out


def test_exam_submit_success_returns_0(monkeypatch, capsys):
    from tools.cli.commands import study
    from tools.skills import exam_composer as ec
    monkeypatch.setattr(ec, "grade_exam_paper",
                        lambda *a, **k: {"report": "=== 样例判分报告 ==="})
    assert study._cmd_exam_submit(["exam-submit", "示例内联试卷文本", "1. A"]) == 0
    assert "样例判分报告" in capsys.readouterr().out


def test_exam_submit_module_missing_returns_1(monkeypatch, capsys):
    """exam_composer 未载入 → 1（与 ky exam 的模块未载入 = 1 对齐）。"""
    from tools.cli.commands import study
    _hide_skill_module(monkeypatch, "exam_composer")
    assert study._cmd_exam_submit(["exam-submit", "示例内联试卷文本", "1. A"]) == 1
    assert "未载入" in capsys.readouterr().out


# ────────────────────────────────────────────────────────────────
# 5. spinner 清除宽度
# ────────────────────────────────────────────────────────────────


def test_spinner_clear_width_matches_actual_line():
    """状态行真实显示宽度为 49 列（此前按 48 清除，行尾残留 1 字符）。"""
    from tools.cli.agent import engine
    line = f"  {engine._SPINNER_FRAMES[0]} {engine._SPINNER_LABEL}"
    assert display_width(line) == 49


def test_spinner_clear_no_longer_hardcodes_48():
    """源码回归：engine.py 的清除行不得再写死 48 个空格（注释除外）。"""
    from tools.cli.agent import engine
    src = Path(engine.__file__).read_text(encoding="utf-8")
    assert '"\\r" + " " * 48' not in src, "清除行仍在写死 48 个空格"
    assert '_display_width(f"  {frames[0]} {label}")' in src, "清除行未按显示宽度计算"


# ────────────────────────────────────────────────────────────────
# 6. 剪贴板密钥回显走 _mask_secret
# ────────────────────────────────────────────────────────────────


def _patch_clipboard_env(monkeypatch, clip_text: str):
    from tools.cli import config as config_mod
    monkeypatch.setattr(config_mod, "get_clipboard_text", lambda: clip_text)
    monkeypatch.setattr(config_mod, "webbrowser", types.SimpleNamespace(open=lambda *a, **k: True))
    monkeypatch.setattr(config_mod, "time", types.SimpleNamespace(sleep=lambda *a, **k: None))
    monkeypatch.setattr("builtins.input", lambda *a, **k: "")
    return config_mod


def test_clipboard_short_key_fully_masked(monkeypatch, capsys):
    """≤12 字符短密钥：只报「已设置」，原文绝不回显（此前几乎整体回显）。"""
    config_mod = _patch_clipboard_env(monkeypatch, "sk-abc123")
    result = config_mod.open_provider_console_and_get_key(
        "示例服务商", "https://example.invalid/console")
    assert result == "sk-abc123"  # 内部套用不受影响
    out = capsys.readouterr().out
    assert "sk-abc123" not in out
    assert "已设置" in out


def test_clipboard_long_key_partial_mask_within_limits(monkeypatch, capsys):
    """>12 字符密钥：保留前 6 后 4 的部分掩码，完整原文不回显。"""
    clip = "sk-" + "x" * 30
    config_mod = _patch_clipboard_env(monkeypatch, clip)
    result = config_mod.open_provider_console_and_get_key(
        "示例服务商", "https://example.invalid/console")
    assert result == clip
    out = capsys.readouterr().out
    assert clip not in out
    assert clip[:6] + "***" + clip[-4:] in out


# ────────────────────────────────────────────────────────────────
# 7. TUI 单键 q 二次确认
# ────────────────────────────────────────────────────────────────


def test_q_requires_second_press_in_interactive(capsys):
    from tools import tui_navigator
    assert tui_navigator.execute_action("q", interactive=True) is True
    assert "再输入一次 q" in capsys.readouterr().out
    assert tui_navigator.execute_action("q", interactive=True) is False


def test_q_non_interactive_exits_directly(capsys):
    """textual/GUI/脚本（interactive=False）：保持一次退出，语义不变。"""
    from tools import tui_navigator
    assert tui_navigator.execute_action("q", interactive=False) is False
    assert "再输入一次 q" not in capsys.readouterr().out


def test_q_pending_reset_after_other_input(capsys):
    """中途输入其它键会重置挂起位：下一次 q 仍需重新确认。"""
    from tools import tui_navigator
    assert tui_navigator.execute_action("q", interactive=True) is True
    assert tui_navigator.execute_action("99", interactive=True) is True  # 未知编号
    assert tui_navigator.execute_action("q", interactive=True) is True  # 重新拦截
    assert "再输入一次 q" in capsys.readouterr().out


def test_zero_direct_exit_unchanged(capsys):
    """0 / exit / quit 是显式退出指令，交互模式下不受二次确认影响。"""
    from tools import tui_navigator
    assert tui_navigator.execute_action("0", interactive=True) is False
    assert tui_navigator.execute_action("exit", interactive=True) is False


# ────────────────────────────────────────────────────────────────
# 8. ky session ls 对齐
# ────────────────────────────────────────────────────────────────


def _start_col(line: str, marker: str) -> int:
    return display_width(line[:line.index(marker)])


def test_session_ls_columns_aligned(monkeypatch, capsys):
    """表头与数据行的列起点（显示列）逐列一致；旧实现（字符数对齐）必然错位。"""
    sess_mod = _cli_session_mod()
    monkeypatch.setattr(sess_mod, "list_sessions", lambda root: [{
        "session_id": "20260101-120000-aa11bb",
        "created_at": "2026-01-01 12:00:00",
        "event_count": 13,
        "message_count": 47,
        "first_user_text": "样例首条消息",
    }])
    sess_mod._session_ls()
    lines = capsys.readouterr().out.splitlines()
    hdr = next(l for l in lines if "首条消息 / 来源" in l)
    row = next(l for l in lines if "样例首条消息" in l)

    assert _start_col(hdr, "创建时间") == _start_col(row, "2026-01-01")
    assert _start_col(hdr, "首条消息 / 来源") == _start_col(row, "样例首条消息")

    # 反向对照：旧 f-string 表头（按字符数对齐）的起点与新表头不同 → 修复真实生效
    old_hdr = f"{'会话 id (时间戳)':<18} {'创建时间':<21} {'事件':<6} {'消息':<6} 首条消息 / 来源"
    assert _start_col(old_hdr, "首条消息") != _start_col(hdr, "首条消息 / 来源")


# ────────────────────────────────────────────────────────────────
# 9. ky ingest 多余位置参数
# ────────────────────────────────────────────────────────────────


def test_ingest_extra_positional_rejected(capsys):
    from tools.cli.commands import material
    with pytest.raises(SystemExit) as ei:
        material._cmd_ingest(["ingest", "第一份示例.md", "第二份示例.md"])
    assert ei.value.code == 1
    out = capsys.readouterr().out
    assert "多余的参数" in out
    assert "第二份示例.md" in out


def test_ingest_single_positional_passes_parsing(capsys):
    """单文件路径不触发「多余参数」；走到后续文件存在性检查（此处不存在）。"""
    from tools.cli.commands import material
    with pytest.raises(SystemExit) as ei:
        material._cmd_ingest(["ingest", "不存在的示例.md"])
    assert ei.value.code == 1
    out = capsys.readouterr().out
    assert "多余的参数" not in out
    assert "找不到文件" in out
