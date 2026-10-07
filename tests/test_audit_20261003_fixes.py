# -*- coding: utf-8 -*-
"""2026-10-03 全量审查修复回归测试（用户视角实测批次）。

覆盖当日审查确认的 6 项缺陷 + 1 项批准功能：
  A1 根/内层 bat 的 python 探测把 WindowsApps 商店替身当解释器
     （`where python` 命中即用）→ 无 py 启动器的机器上构建必败。
     修复：真探测（py -3 优先；PATH python 排除 WindowsApps + 版本门禁）。
  A2 `ky done <关键词>` 跨科目误打卡（固定按 数学→英语→政治→专业课 取
     第一个命中）→ 当前科目优先 / 多科同名拒绝并提示 / --subject 显式指定。
  A3 init_workspace 模板扫描未排除 .pytest_tmp → 误跑新建 162 个游离文件。
  A4 ingest 切片选项边界：标题行「## Part C 英译汉」被当第 5 个选项；
     含 A-D 字母的选项正文（DNA/RNA）被 Rust 路径截断。
  A5 rollback --dry-run 无动作时打印「[!] 快照回滚失败: …（dry-run，未动
     磁盘）」自相矛盾 → 预演三态渲染（不再出现「失败」字样）。
  A6 今日任务.md 跨天不刷新 → 读取侧（CLI/REPL/TUI/看板/GUI）按天自动刷新。

全部用例本地确定性：零 LLM、零联网、不触碰真实工作区（模块级 ROOT 在
导入期固化，测试直接替换模块属性隔离）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ═════════════════════════ 公共工具 ═════════════════════════

def _stub_record_daily_completion(monkeypatch) -> None:
    """打卡成功路径会回调 study_planner.record_daily_completion（写真实
    ky_config.json）。测试一律打桩双导入别名，保证零真实配置写入。"""
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


def _seed_task(ws: Path, folder: str, module: str) -> Path:
    """在临时工作区生成某科今日任务（含指定模块名的一行任务）。"""
    task_dir = ws / folder / "_状态"
    task_dir.mkdir(parents=True, exist_ok=True)
    task_file = task_dir / "今日任务.md"
    task_file.write_text(
        "# 今日任务 (2026-10-03)\n\n"
        "| 模块 | 任务内容 | 预计用时 | 完成状态 |\n"
        "|---|---|---|---|\n"
        f"| {module} | 示例任务内容 | 60 分钟 | [ ] |\n",
        encoding="utf-8")
    return task_file


# ═════════════════════════ A1 bat python 探测 ═════════════════════════

#: 四个启动器（根 bat 为 GBK，内层 bat 为 UTF-8；关键标记均为 ASCII，
#: 统一按 bytes 断言，不依赖编码判定）。
_BAT_FILES = ("更新看板.bat", "ky.bat",
              "05-考研看板/更新看板.bat", "05-考研看板/auto-update.bat")


def test_a1_bat_probe_rejects_windowsapps_stub():
    """四个 bat 都必须排除 WindowsApps 商店替身并统一走 KY_PY 变量。"""
    for rel in _BAT_FILES:
        data = (ROOT / rel).read_bytes()
        assert b"WindowsApps" in data, f"{rel}: 缺 WindowsApps 排除逻辑"
        assert b"findstr" in data, f"{rel}: 缺 findstr 跳过判定"
        assert b"KY_PY" in data, f"{rel}: 缺统一解释器变量 KY_PY"
        assert b"hexversion" in data, f"{rel}: 缺 3.10+ 版本门禁"


def _bat_code_only(data: bytes) -> bytes:
    """剥掉 rem/:: 注释行：阴性对照只针对**可执行命令行**，
    注释里引用旧写法（说明修复缘由）不算残留。"""
    lines = []
    for ln in data.replace(b"\r\n", b"\n").split(b"\n"):
        s = ln.strip()
        if s.lower().startswith(b"rem ") or s.startswith(b"::"):
            continue
        lines.append(ln)
    return b"\n".join(lines)


def test_a1_bat_old_stub_prone_pattern_absent():
    """阴性对照：旧写法（where python 命中即用 / 裸 python 兜底）不得残留。

    `where python >nul 2>nul || set PY=python` 是本次 P1 缺陷的原形态：
    商店替身会被 where 命中而实际不可用，构建必然静默失败。
    """
    for rel in _BAT_FILES:
        data = _bat_code_only((ROOT / rel).read_bytes())
        assert b"where python >nul 2>nul || set PY=python" not in data, rel
        assert b"set PY=python" not in data, f"{rel}: 旧变量 PY 残留"
        assert b"set PY=py" not in data, f"{rel}: 旧变量 PY 残留"


# ═════════════════════════ A2 ky done 打卡落点 ═════════════════════════

def test_a2_done_prefers_active_subject(tmp_path, monkeypatch):
    """同名任务命中数学+英语、当前科目为英语 → 必须打卡英语而非数学。"""
    from tools.cli import shared as cli_shared

    ws = tmp_path / "ws"
    m_file = _seed_task(ws, "01-数学", "核心精讲")
    e_file = _seed_task(ws, "02-英语", "核心精讲")
    monkeypatch.setattr(cli_shared, "ROOT", ws)
    monkeypatch.setattr(cli_shared, "load_config",
                        lambda: {"active_subject": "eng"})
    _stub_record_daily_completion(monkeypatch)

    ok, msg = cli_shared.mark_today_task_done("核心精讲")
    assert ok, msg
    assert "[x]" in e_file.read_text(encoding="utf-8"), "应打卡当前科目（英语）"
    assert "[x]" not in m_file.read_text(encoding="utf-8"), "数学不得被误打卡"
    assert "（英语）" in msg, f"回显必须带科目名：{msg}"
    assert "其他科目也有同名任务" in msg and "数学" in msg, \
        f"其他科目同名任务应附注：{msg}"


def test_a2_done_multi_subject_refuses_with_candidates(tmp_path, monkeypatch):
    """多科同名且当前科目（政治）不在命中中 → 不猜测，拒绝并给候选。"""
    from tools.cli import shared as cli_shared

    ws = tmp_path / "ws"
    m_file = _seed_task(ws, "01-数学", "核心精讲")
    e_file = _seed_task(ws, "02-英语", "核心精讲")
    before_m = m_file.read_bytes()
    before_e = e_file.read_bytes()
    monkeypatch.setattr(cli_shared, "ROOT", ws)
    monkeypatch.setattr(cli_shared, "load_config",
                        lambda: {"active_subject": "pol"})
    _stub_record_daily_completion(monkeypatch)

    ok, msg = cli_shared.mark_today_task_done("核心精讲")
    assert not ok
    assert "在多个科目命中" in msg and "--subject=" in msg, msg
    assert m_file.read_bytes() == before_m, "拒绝路径不得写入数学"
    assert e_file.read_bytes() == before_e, "拒绝路径不得写入英语"


def test_a2_done_explicit_subject_wins(tmp_path, monkeypatch):
    """显式 subject 优先于 active_subject：只在该科目内匹配。"""
    from tools.cli import shared as cli_shared

    ws = tmp_path / "ws"
    m_file = _seed_task(ws, "01-数学", "核心精讲")
    e_file = _seed_task(ws, "02-英语", "核心精讲")
    monkeypatch.setattr(cli_shared, "ROOT", ws)
    monkeypatch.setattr(cli_shared, "load_config",
                        lambda: {"active_subject": "math"})
    _stub_record_daily_completion(monkeypatch)

    ok, msg = cli_shared.mark_today_task_done("核心精讲", subject="eng")
    assert ok, msg
    assert "[x]" in e_file.read_text(encoding="utf-8")
    assert "[x]" not in m_file.read_text(encoding="utf-8")
    assert "（英语）" in msg


def test_a2_done_single_hit_labels_subject(tmp_path, monkeypatch):
    """单科命中：回显带科目名，且不出现「其他科目」附注。"""
    from tools.cli import shared as cli_shared

    ws = tmp_path / "ws"
    p_file = _seed_task(ws, "04-专业课", "核心知识点")
    monkeypatch.setattr(cli_shared, "ROOT", ws)
    _stub_record_daily_completion(monkeypatch)

    ok, msg = cli_shared.mark_today_task_done("核心知识点", subject="pro")
    assert ok, msg
    assert "（专业课）" in msg
    assert "其他科目也有同名任务" not in msg
    assert "[x]" in p_file.read_text(encoding="utf-8")


def test_a2_done_cli_parses_subject_flag(monkeypatch):
    """CLI `ky done --subject=eng 核心精讲`：flag 解析后透传 subject，关键词不含 flag。"""
    from tools.cli.commands import daily as daily_mod

    captured = {}

    def _fake(kw, subject=None):
        captured["kw"] = kw
        captured["subject"] = subject
        return True, "已完成打卡（英语）: 示例"

    monkeypatch.setattr(daily_mod, "mark_today_task_done", _fake)
    with pytest.raises(SystemExit) as ei:
        daily_mod._cmd_done(["done", "--subject=eng", "核心精讲"])
    assert ei.value.code == 0
    assert captured == {"kw": "核心精讲", "subject": "eng"}


# ═════════════════════════ A3 init_workspace 模板扫描 ═════════════════════════

def test_a3_template_scan_excludes_pytest_tmp(tmp_path, monkeypatch, capsys):
    """.pytest_tmp/ 内的模板不得被当作源模板生成 .md 游离文件。"""
    from tools import init_workspace as iw

    ws = tmp_path / "ws"
    (ws / ".pytest_tmp" / "sub").mkdir(parents=True)
    (ws / ".pytest_tmp" / "sub" / "游离.template.md").write_text(
        "# 游离\n", encoding="utf-8")
    normal_dir = ws / "03-思想政治理论" / "_状态"
    normal_dir.mkdir(parents=True)
    (normal_dir / "正常.template.md").write_text("# 正常\n", encoding="utf-8")

    monkeypatch.setattr(iw, "ROOT", ws)
    iw.copy_templates()

    assert (normal_dir / "正常.md").exists(), "正常模板必须照常初始化"
    assert not (ws / ".pytest_tmp" / "sub" / "游离.md").exists(), \
        ".pytest_tmp 内模板必须被排除"
    assert ".pytest_tmp" in iw._TEMPLATE_SCAN_EXCLUDE


# ═════════════════════════ A4 ingest 选项边界 ═════════════════════════

def _make_pipeline(tmp_path):
    from tools.skills.material_ingestion import MaterialIngestionPipeline
    return MaterialIngestionPipeline(workspace_root=tmp_path)


_SECTION_BLEED_DOC = (
    "21. What is the main idea of the passage?\n\n"
    "A. The economy is recovering.\n"
    "B. Technology changes education.\n"
    "C. People prefer online shopping.\n"
    "D. The climate is warming.\n\n"
    "## Part C 英译汉\n\n"
    "46. Translate the following sentence into Chinese.\n"
    "人工智能的快速发展深刻改变了我们的生活方式。\n"
)

_DNA_OPTIONS_DOC = (
    "3. 下列叙述正确的是（ ）\n\n"
    "A. DNA 是遗传物质\n"
    "B. RNA 只存在于细胞质\n"
    "C. 蛋白质由氨基酸组成\n"
    "D. 酶都是蛋白质\n"
)


def test_a4_heading_not_option(tmp_path):
    """「## Part C 英译汉」标题行不得串入上一题的选项（第 5 个伪选项）。"""
    p = _make_pipeline(tmp_path)
    chunks = p.chunk_text(_SECTION_BLEED_DOC, "probe")
    q21 = [c for c in chunks if "main idea" in c.stem]
    assert q21, f"未切出第 21 题：{[c.stem[:30] for c in chunks]}"
    opts = q21[0].options
    assert len(opts) == 4, f"选项数应恰为 4：{opts}"
    assert not any("英译汉" in o for o in opts), f"标题串入选项：{opts}"
    assert not any("Part C" in o for o in opts), f"标题串入选项：{opts}"


def test_a4_heading_not_option_force_python(tmp_path):
    """Python 参考路径同口径（两条路径统一出口兜底）。"""
    p = _make_pipeline(tmp_path)
    p._force_python = True
    chunks = p.chunk_text(_SECTION_BLEED_DOC, "probe")
    q21 = [c for c in chunks if "main idea" in c.stem]
    assert q21
    opts = q21[0].options
    assert len(opts) == 4, f"选项数应恰为 4：{opts}"
    assert not any("英译汉" in o or "Part C" in o for o in opts), opts


def test_a4_option_content_with_letters_not_truncated(tmp_path):
    """含 A-D 字母的选项正文（DNA/RNA）不得被截断（Rust 路径经路由/兜底）。"""
    p = _make_pipeline(tmp_path)
    chunks = p.chunk_text(_DNA_OPTIONS_DOC, "probe")
    q3 = [c for c in chunks if "叙述正确" in c.stem]
    assert q3, f"未切出第 3 题：{[c.stem[:30] for c in chunks]}"
    opts = q3[0].options
    assert len(opts) == 4, f"选项数应恰为 4：{opts}"
    assert any("DNA 是遗传物质" in o for o in opts), f"DNA 选项被截断：{opts}"
    assert any("RNA 只存在于细胞质" in o for o in opts), f"RNA 选项被截断：{opts}"


def test_a4_option_content_with_letters_force_python(tmp_path):
    """Python 参考路径下 DNA/RNA 选项同样完整（阴性对照另一路径）。"""
    p = _make_pipeline(tmp_path)
    p._force_python = True
    chunks = p.chunk_text(_DNA_OPTIONS_DOC, "probe")
    q3 = [c for c in chunks if "叙述正确" in c.stem]
    assert q3
    opts = q3[0].options
    assert any("DNA 是遗传物质" in o for o in opts), opts
    assert any("RNA 只存在于细胞质" in o for o in opts), opts


# ── [OCR 审查修复] 行内标点式首选项回归 / AD 字母多 token 覆盖 ──────────────

_INLINE_FIRST_OPT_DOC = (
    "1. 下列说法正确的是 A. 甲\n"
    "B. 乙\n"
    "C. 丙\n"
    "D. 丁\n"
    "【答案】A\n"
    "【解析】解析内容。\n"
)

_AD_SECOND_TOKEN_DOC = (
    "3. 下列叙述正确的是（ ）\n\n"
    "A. 酶是 ATP 酶\n"
    "B. 酶都是蛋白质\n"
    "C. 高温使酶失活\n"
    "D. 酶只在细胞内起效\n"
)


def test_a4_inline_first_option_not_merged_into_stem(tmp_path):
    """题干与首选项同行（转录材料常见）：A 选项不得并入题干。

    [OCR 审查修复·行内标点式回归] Rust 行首锚定后不再识别行内标点式选项，
    旧宽正则识别 —— 产物回归。命中 _INLINE_OPT_RE 即路由 Python 参考实现。
    """
    p = _make_pipeline(tmp_path)
    chunks = p.chunk_text(_INLINE_FIRST_OPT_DOC, "probe")
    assert chunks, "未切出题目"
    q1 = chunks[0]
    assert len(q1.options) == 4, f"选项数应恰为 4：{q1.options}"
    assert any("甲" in o for o in q1.options), f"选项 A 丢失：{q1.options}"
    assert "A." not in q1.stem and "甲" not in q1.stem, \
        f"选项混入题干：{q1.stem!r}"


def test_a4_inline_opt_route_regex_precision():
    """行内路由正则必须只命中「非行首标点式」：不误伤标准格式（防全量路由）。"""
    from tools.skills.material_ingestion import _INLINE_OPT_RE

    assert _INLINE_OPT_RE.search("1. 下列说法正确的是 A. 甲")
    assert _INLINE_OPT_RE.search("题干 A、乙")
    # 阴性：行首/跨行选项（Rust 行首锚定已覆盖，不得再路由）
    assert not _INLINE_OPT_RE.search("A. 甲")
    assert not _INLINE_OPT_RE.search("题干内容\nB. 乙")
    # 阴性：行内裸式（Part C 形态，防误吃）
    assert not _INLINE_OPT_RE.search("## Part C 英译汉")
    # 阴性：标准每选项一行格式
    assert not _INLINE_OPT_RE.search("1. 题目（ ）\nA. x\nB. y\nC. z\nD. w")


def test_a4_ad_letter_in_second_token_routes_python(tmp_path):
    """选项正文第二个 token 起含 A-D（「酶是 ATP 酶」）也须触发路由/不截断。"""
    from tools.skills.material_ingestion import _OPT_CONTENT_AD_RE

    assert _OPT_CONTENT_AD_RE.search("A. 酶是 ATP 酶")
    assert _OPT_CONTENT_AD_RE.search("A. DNA 是遗传物质")
    assert not _OPT_CONTENT_AD_RE.search("A. 4")
    assert not _OPT_CONTENT_AD_RE.search("A. The economy is recovering.")

    p = _make_pipeline(tmp_path)
    chunks = p.chunk_text(_AD_SECOND_TOKEN_DOC, "probe")
    q3 = [c for c in chunks if "叙述正确" in c.stem]
    assert q3, f"未切出第 3 题：{[c.stem[:30] for c in chunks]}"
    opts = q3[0].options
    assert len(opts) == 4, f"选项数应恰为 4：{opts}"
    assert any("酶是 ATP 酶" in o for o in opts), f"ATP 选项被截断：{opts}"


# ═════════════════════════ A5 rollback dry-run 文案 ═════════════════════════

def test_a5_rollback_dryrun_no_action_not_failure(tmp_path, monkeypatch, capsys):
    """dry-run 无动作（全 skipped）不得打印「失败」，应为中性「预演」报告。"""
    from tools.cli.commands import misc
    from tools.agent import PermissionManager

    ws = tmp_path / "ws"
    ws.mkdir()
    pm = PermissionManager(workspace_root=ws.resolve())
    pm.create_checkpoint(ws.resolve() / "ghost.txt")  # 快照时文件不存在

    monkeypatch.setattr(misc, "ROOT", ws.resolve())
    misc._cmd_rollback(["rollback", "--dry-run", "--file", "ghost.txt"])
    out = capsys.readouterr().out
    assert "失败" not in out, f"dry-run 无动作不应称失败：{out}"
    assert "预演" in out and "未动磁盘" in out, out


def test_a5_rollback_dryrun_with_action_uses_future_tense(tmp_path, monkeypatch, capsys):
    """dry-run 有动作：动作列表用「将还原」而非「已还原」（未动磁盘不得称已）。"""
    from tools.cli.commands import misc
    from tools.agent import PermissionManager

    ws = tmp_path / "ws"
    ws.mkdir()
    target = ws / "real.txt"
    target.write_text("v1", encoding="utf-8")
    pm = PermissionManager(workspace_root=ws.resolve())
    pm.create_checkpoint(target.resolve())
    target.write_text("v2", encoding="utf-8")

    monkeypatch.setattr(misc, "ROOT", ws.resolve())
    misc._cmd_rollback(["rollback", "--dry-run", "--file", "real.txt"])
    out = capsys.readouterr().out
    assert "失败" not in out, out
    assert "将还原" in out, out
    assert "已还原" not in out, f"dry-run 不得称「已还原」：{out}"
    assert target.read_text(encoding="utf-8") == "v2", "dry-run 不得动磁盘"


# ═════════════════════════ A6 今日任务按天刷新 ═════════════════════════

def _seed_stale_pro(ws: Path) -> Path:
    tf = ws / "04-专业课" / "_状态" / "今日任务.md"
    tf.parent.mkdir(parents=True, exist_ok=True)
    tf.write_text(
        "# 今日专业课任务 (2000-01-01)\n\n"
        "| 模块 | 任务内容 | 预计用时 | 完成状态 |\n"
        "|---|---|---|---|\n"
        "| 核心知识点 | 旧任务内容 | 60 分钟 | [x] |\n",
        encoding="utf-8")
    return tf


def test_a6_stale_task_refreshed(tmp_path):
    """过期任务文件被重写为当日；刷新列表如实返回该科。"""
    from tools import study_planner as sp

    ws = tmp_path / "ws"
    tf = _seed_stale_pro(ws)
    refreshed = sp.refresh_stale_today_tasks({}, workspace_root=ws)
    today = sp.datetime.now().strftime("%Y-%m-%d")
    assert today in tf.read_text(encoding="utf-8").splitlines()[0]
    assert any(r["subject"] == "pro" for r in refreshed), refreshed


def test_a6_current_day_task_untouched(tmp_path):
    """当日文件（含勾选）字节级不动 —— 阴性对照。"""
    from tools import study_planner as sp

    ws = tmp_path / "ws"
    tf = ws / "04-专业课" / "_状态" / "今日任务.md"
    tf.parent.mkdir(parents=True)
    today = sp.datetime.now().strftime("%Y-%m-%d")
    tf.write_text(
        f"# 今日专业课任务 ({today})\n\n"
        "| 模块 | 任务内容 | 预计用时 | 完成状态 |\n"
        "|---|---|---|---|\n"
        "| 核心知识点 | 今日任务 | 60 分钟 | [x] |\n",
        encoding="utf-8")
    before = tf.read_bytes()
    refreshed = sp.refresh_stale_today_tasks({}, workspace_root=ws)
    assert tf.read_bytes() == before, "当日文件不得被改写"
    assert refreshed == [], refreshed


def test_a6_missing_task_file_not_created(tmp_path):
    """文件不存在不动：保持「报到/建档才生成」语义，不给未报到科目造文件。"""
    from tools import study_planner as sp

    ws = tmp_path / "ws"
    ws.mkdir()
    refreshed = sp.refresh_stale_today_tasks({}, workspace_root=ws)
    assert refreshed == []
    assert not (ws / "01-数学" / "_状态" / "今日任务.md").exists()
    assert not (ws / "02-英语" / "_状态" / "今日任务.md").exists()


def test_a6_renderer_hook_triggers_refresh(tmp_path, monkeypatch, capsys):
    """ky today / REPL /today / TUI 共用入口：渲染前触发刷新。"""
    from tools import study_planner as sp
    from tools.cli.repl import renderer

    ws = tmp_path / "ws"
    tf = _seed_stale_pro(ws)
    # [2026-10-07 双根修复同步] 刷新已改走 renderer.ROOT（与任务读取同源，见
    # renderer.print_today_tasks_summary 的「根一致性修复」注释），隔离对象
    # 同步从 sp.ROOT 改为 renderer.ROOT：若实现回退为无参回落 sp.ROOT，本用例
    # 会改写真实仓库（tripwire）且 ws 下文件不刷新（断言失败），双重拦截。
    monkeypatch.setattr(renderer, "ROOT", ws)
    monkeypatch.setattr(renderer, "get_today_tasks_data", lambda: {})
    renderer.print_today_tasks_summary(as_json=True)
    today = sp.datetime.now().strftime("%Y-%m-%d")
    assert today in tf.read_text(encoding="utf-8").splitlines()[0]


def test_a6_gui_load_state_triggers_refresh(tmp_path, monkeypatch):
    """GUI load_state：读盘前触发刷新（过期文件在状态加载时已被重写）。"""
    from tools import study_planner as sp
    from tools.gui.services import dashboard as dash

    ws = tmp_path / "ws"
    tf = _seed_stale_pro(ws)
    dash._STATE_CACHE.clear()
    dash.load_state(ws)
    today = sp.datetime.now().strftime("%Y-%m-%d")
    assert today in tf.read_text(encoding="utf-8").splitlines()[0]


def test_a6_dashboard_build_hook_present():
    """看板 build 挂接存在且仅在本地完整模式（非 opt-in）下刷新。

    发布安全模式构建须保持对源文件只读（公开快照不按日刷新）。
    """
    text = (ROOT / "05-考研看板" / "build.py").read_text(encoding="utf-8")
    idx_guard = text.index("if not snapshot_opt_in():")
    idx_refresh = text.index("refresh_stale_today_tasks")
    assert idx_guard < idx_refresh, "刷新调用必须在本地模式判断之内"
