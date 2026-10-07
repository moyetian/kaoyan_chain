# -*- coding: utf-8 -*-
"""交互入口补齐批次（2026-10-06）：TUI 报到/交作业 + GUI 本地检索/建索引

[背景]
R2/R3 两轮全矩阵仿真（54 格 / 610 条记录 / 0 程序缺陷）把两处记为
「交互方式限制」——**不是缺陷，但是考生视角的能力缺口**：

  * ``TUI`` 菜单十项里没有「报到 / 交作业」：能力在 REPL 私教链路里齐备，
    考生在终端中枢按不到（每考生 17 条「不支持」记录）。
  * ``GUI`` 命令面板搜「rag」「index」「索引」「检索」全部 0 命中（仅命中
    ``tool:variant`` / ``tool:wechat_search``）：``ky rag`` / ``ky index``
    在 CLI 早已注册可用，GUI 却完全无入口。

本文件钉住这两处补齐，并显式钉住两条**不许被顺手破坏**的契约：
  ① TUI 既有 1-10 的编号含义与分组归属不得变动（只允许在末尾追加 11/12）；
  ② GUI 可达/不可达命令声明仍等于 CLI 注册表全集。

[阴性对照]
  * 回退 ``MENU_GROUPS`` 里的 11/12 → 「菜单含报到交作业」用例整体变红；
  * 回退 ``MENU_GROUPS`` 里 1-10 的 key → 「既有编号含义不变」用例变红；
  * 从 ``PALETTE_ENTRIES`` 移除 rag/index → 「四词探查」用例变红；
  * 把 rag/index 塞回 ``GUI_UNREACHABLE_COMMANDS`` → 边界审计用例变红。
"""

from __future__ import annotations

import pytest

from tools import tui_navigator
from tools.gui.views import nav_rail as nav_rail_view


# ════════════════════════════════════════════════════════════════
# 缺口 1 · TUI：报到（11）/ 交作业（12）
# ════════════════════════════════════════════════════════════════

#: 新增两项的 (编号, 别名)
NEW_TUI_ITEMS = (("11", "checkin"), ("12", "homework"))

#: [2026-10-06 之前] 菜单十项的编号 → 别名，逐项锁定。
#: 追加 11/12 是为了**不动**这张表：改既有编号会毁掉考生肌肉记忆，也会让
#: 操作手册 / 看板文案里的「按 6 侦察」这类指引集体失真。
LEGACY_TUI_KEYS = {
    "1": "today", "2": "compose", "3": "variant",
    "4": "diff", "5": "ingest", "6": "scout", "7": "compare",
    "8": "watch", "9": "build", "10": "wechat_search",
    "0": "exit",
}


def test_tui_menu_contains_checkin_and_homework():
    """菜单里必须出现 11 报到 / 12 交作业，且可按别名 dispatch。"""
    by_alias = {alias: key for key, _n, _i, _d, alias in _all_menu_items()}
    for key, alias in NEW_TUI_ITEMS:
        assert alias in by_alias, f"菜单缺少 {alias} 入口"
        assert by_alias[alias] == key, (
            f"{alias} 的编号应为 {key}，实际 {by_alias[alias]}")
    assert "exit" in by_alias, "退出项必须仍在菜单里"


def test_tui_legacy_numbering_unchanged():
    """既有 1-10 与 0 的编号→别名映射逐项未变，且相对顺序未变。"""
    keys = [key for key, _n, _i, _d, _a in _all_menu_items()]
    aliases = [alias for _k, _n, _i, _d, alias in _all_menu_items()]

    actual = dict(zip(keys, aliases))
    for key, alias in LEGACY_TUI_KEYS.items():
        assert actual.get(key) == alias, (
            f"编号 {key} 的含义被改动：{alias} → {actual.get(key)}")

    # 既有项的相对顺序：1..10 必须仍按升序出现在 11/12 之前
    legacy_order = [k for k in keys if k in LEGACY_TUI_KEYS and k != "0"]
    assert legacy_order == sorted(legacy_order, key=int), (
        f"既有项相对顺序被打乱：{legacy_order}")
    # 退出项固定末位（既有惯例：新增入口不得把 [0] 挤到中间）
    assert keys[-1] == "0", f"[0] 退出必须留在菜单最后一行，实际末位为 {keys[-1]}"


def test_tui_menu_group_count_and_rendered_text():
    """新增组把菜单变为 4 组 13 项，且两项在 render_menu 文本里可见。"""
    total = sum(len(items) for _t, _c, items in tui_navigator.MENU_GROUPS)
    assert total == 13, f"菜单应为 13 项（新增 11/12），实际 {total}"
    assert len(tui_navigator.MENU_GROUPS) == 4

    rendered = tui_navigator.render_menu()
    assert "学科报到" in rendered
    assert "交作业" in rendered
    # 既有入口文案不得消失
    for name in ("今日任务", "靶向组卷", "院校侦察", "看板更新", "公众号检索"):
        assert name in rendered, f"既有菜单项文案丢失：{name}"


def test_tui_checkin_dispatches_to_existing_brief(capsys, tmp_path, monkeypatch):
    """按 11 能走通：复用既有报到播报实现，不新写业务逻辑。"""
    monkeypatch.setattr(tui_navigator, "ROOT", tmp_path)
    monkeypatch.setattr(tui_navigator, "CONFIG_FILE", tmp_path / "ky_config.json")
    # [隐私·2026-10-06] 夹具数据一律用**中性值**，不得照抄考生真实配置里的
    # 目标分 / 弱点描述——它们同属身份指纹，会被 test_privacy_identity_rules 判红，
    # 且测试文件同样会被发布流水线脱敏改写（导致公开副本断言自毁）。
    (tmp_path / "ky_config.json").write_text(
        '{"study_plan": {"pro_target": "100 分", "pro_weakness": "知识点记不牢"},'
        ' "active_subject": "pro"}',
        encoding="utf-8",
    )

    keep = tui_navigator.execute_action("11", interactive=False,
                                        extra={"subject": "pro"})
    out = capsys.readouterr().out
    assert keep is True, "报到动作应返回「继续主循环」，不得退出 TUI"
    # build_subject_checkin_brief 的真实输出特征（单一真源复用）
    assert "私教报到就绪" in out
    assert "100 分" in out
    # 必须把考生引到真正能派题/批改的私教会话，而不是假装 TUI 自己能讲题
    assert "专业课报到" in out


def test_tui_checkin_rejects_math_when_math_disabled(capsys, tmp_path, monkeypatch):
    """不考数学的方案选数学报到时必须友好拒绝，不得静默派发数学任务。"""
    monkeypatch.setattr(tui_navigator, "ROOT", tmp_path)
    monkeypatch.setattr(tui_navigator, "CONFIG_FILE", tmp_path / "ky_config.json")
    (tmp_path / "ky_config.json").write_text(
        '{"study_plan": {"math_key": "none", "math_name": "不考数学"},'
        ' "active_subject": "eng"}',
        encoding="utf-8",
    )

    tui_navigator.execute_action("11", interactive=False,
                                 extra={"subject": "math"})
    out = capsys.readouterr().out
    assert "不考数学" in out
    assert "私教报到就绪" not in out, "不考数学方案不得进入数学报到播报"


@pytest.mark.parametrize(
    "answer, expect_label, expect_cmd",
    [
        ("1", "英语专属私教", "英语报到"),          # 按序号选
        ("专业课", "专业课专属私教", "专业课报到"),    # 按中文科目名选
        ("", "英语专属私教", "英语报到"),# 回车 = 当前激活科目
    ],
    ids=["by_index", "by_chinese_name", "enter_uses_active"],
)
def test_tui_checkin_interactive_subject_picking(
        capsys, tmp_path, monkeypatch, answer, expect_label, expect_cmd):
    """交互模式的三种选科目方式都要走得通（数字选择 + 必要的文本输入）。"""
    monkeypatch.setattr(tui_navigator, "ROOT", tmp_path)
    monkeypatch.setattr(tui_navigator, "CONFIG_FILE", tmp_path / "ky_config.json")
    (tmp_path / "ky_config.json").write_text(
        '{"study_plan": {"math_key": "none", "eng_target": "65+"},'
        ' "active_subject": "eng"}',
        encoding="utf-8",
    )
    monkeypatch.setattr("builtins.input", lambda _prompt="": answer)

    tui_navigator.execute_action("11", interactive=True)
    out = capsys.readouterr().out
    assert "请选择要报到的科目" in out, "交互模式必须先列可报到的科目"
    assert "数学专属私教" not in out, "不考数学方案的科目清单不得含数学"
    assert expect_label in out, f"未报到期望科目 {expect_label}"
    assert f"「{expect_cmd}」" in out, "必须给出正确的私教会话报到口令"


def test_tui_homework_menu_reuses_router_text(capsys):
    """按 12 能走通：交作业指引复用 REPL 的同一份文本（三端话术一致）。"""
    from tools.cli.repl.router import build_homework_menu

    keep = tui_navigator.execute_action("12", interactive=False)
    out = capsys.readouterr().out
    assert keep is True
    # 三条提交方式（截图 / 答题卡 / 推导文字）逐条可见
    assert "/paste" in out
    assert "/batch" in out
    for line in build_homework_menu().strip().splitlines():
        assert line.strip() in out or line in out, (
            f"交作业指引与 router.build_homework_menu 漂移：{line!r}")


def test_tui_unknown_key_hint_derives_from_menu(capsys):
    """未知编号提示按真实菜单派生区间（新增 11/12 后不再是 0-10）。"""
    tui_navigator.execute_action("99", interactive=False)
    out = capsys.readouterr().out
    # 该分支的文案不带方括号（历史格式），故按纯文本比对派生区间
    assert f"{tui_navigator.menu_key_range()} 之间的选项" in out, (
        f"未知编号提示未随菜单扩容：{out!r}")
    assert "0-10" not in out, "残留扩容前的旧区间 0-10"


# ════════════════════════════════════════════════════════════════
# 缺口 2 · GUI：本地检索（rag）/ 建索引（index）
# ════════════════════════════════════════════════════════════════

#: 仿真逐词探查的四条查询：此前 rag/index/索引 各 0 命中
PROBE_WORDS = ("rag", "index", "索引", "检索")


@pytest.mark.parametrize("query", PROBE_WORDS)
def test_gui_palette_probe_words_hit_rag_or_index(query):
    """四词探查必须命中 rag/index（修复前 rag/index/索引 三词全 0 命中）。"""
    hits = [e.key for e in nav_rail_view.PALETTE_ENTRIES
            if query in e.haystack()]
    assert hits, f"命令面板搜 {query!r} 0 命中（仿真实测的既有缺口）"
    assert {"tool:rag", "tool:index"} & set(hits), (
        f"{query!r} 应至少命中 rag/index 之一，实际 {hits}")


def test_gui_rag_and_index_are_executable_actions():
    """两条命令必须落在「GUI 可执行别名」里（不是只搜得到、点不动）。"""
    aliases = set(nav_rail_view.GUI_ACTION_ALIASES)
    assert {"rag", "index"} <= aliases, (
        f"rag/index 未登记为 GUI 可执行别名：{sorted(aliases)}")
    assert nav_rail_view.GUI_ACTION_TO_COMMAND["rag"] == "rag"
    assert nav_rail_view.GUI_ACTION_TO_COMMAND["index"] == "index"
    # 服务层直调集必须显式登记（audit 测试按它做三方对账）
    assert {"rag", "index"} <= set(nav_rail_view.GUI_SERVICE_ONLY_ALIASES)


def test_gui_reachable_unreachable_still_equals_registry():
    """可达 ∪ 不可达 仍等于 CLI 注册表全集，且 rag/index 已在可达侧。"""
    from tools.cli import dispatch

    dispatch._init_all_commands()
    registered = {cmd.name for cmd in dispatch.list_commands()}

    reachable = nav_rail_view.GUI_REACHABLE_COMMANDS
    unreachable = nav_rail_view.GUI_UNREACHABLE_COMMANDS
    assert reachable.isdisjoint(unreachable), "可达/不可达不得有交集"
    assert reachable | unreachable == registered, (
        "覆盖边界声明与 CLI 注册表漂移：请同步 nav_rail 三处声明")
    assert {"rag", "index"} <= reachable
    assert not ({"rag", "index"} & unreachable)
    # 本任务不新增 CLI 主命令
    # [2026-10-07 同步] 新增 ``ky budget``（输出预算档位）后 46 → 47；
    # 本条断言钉住「命令清单变更需显式确认」——改命令数必须同步此处，防止静默漂移。
    assert len(registered) == nav_rail_view.CLI_MAIN_COMMAND_COUNT == 47


def test_gui_rag_and_index_dispatch_to_existing_backends(monkeypatch):
    """GUI 分发必须落到既有实现（``ky rag`` / ``ky index`` 的 run_* 函数）。

    钉住「不新写业务逻辑」这条硬约束：把后端换成桩函数，若GUI 侧还自己写
    一份检索/索引逻辑，此用例的红灯不会亮——所以同时断言桩被调用过。

    [为什么要patch 两条路径] ``gui.services`` 里这些后端按仓库既有惯例走
    「裸模块名优先、tools.* 兜底」的双导入（``cli.commands.search`` 与
    ``tools.cli.commands.search`` 是**两个不同的模块对象**）。只patch 其中
    一条会因命中另一份而失效——这是全仓已知的坑，故这里显式两条都patch。
    """
    from tools.gui import services

    calls = {}

    # 桩函数必须**打印**而非返回 —— 真实 run_rag_search / run_index_build 的
    # 契约就是「把结果 print 出来、由调用方捕获 stdout」，返回值只是退出码。
    # 桩若改成 return，GUI 侧只会拿到空输出，测的就不是真实契约了。
    def fake_rag(query, top_k=5, source_filter=None):
        calls["rag"] = query
        print("[桩] rag 命中")
        return 0

    def fake_index(enable_vector=True, show_progress=True):
        calls["index"] = (enable_vector, show_progress)
        print("[桩] index 完成")
        return 0

    for mod_name in ("cli.commands.search", "tools.cli.commands.search"):
        mod = pytest.importorskip(mod_name)
        monkeypatch.setattr(mod, "run_rag_search", fake_rag)
        monkeypatch.setattr(mod, "run_index_build", fake_index)

    assert "[桩] rag 命中" in services.rag_search("剩余价值")
    assert calls["rag"] == "剩余价值"

    assert "[桩] index 完成" in services.build_index(show_progress=False)
    assert calls["index"] == (True, False)


def test_gui_rag_service_empty_output_is_honest():
    """检索无输出时必须给一句可理解提示，不得静默返回空串（面板会一片空白）。"""
    from tools.gui import services

    text = services.rag_search("")
    assert text.strip(), "空结果必须给出可读提示"
    assert "[!]" in text or "检索" in text


# ── 命令面板 → MainWindow 分发（离屏真实驱动，后端打桩不触网/不建库）──

@pytest.fixture(scope="module")
def gui_app():
    from PySide6.QtWidgets import QApplication
    inst = QApplication.instance() or QApplication([])
    yield inst
    inst.setStyleSheet("")


@pytest.fixture()
def gui_win(gui_app, tmp_path, monkeypatch):
    import tools.gui.main_window as _mw
    from tools.gui.main_window import MainWindow
    # [tripwire 修复 2026-10-07] workspace_root=tmp_path：MainWindow 初始化会写
    # 四科「今日任务.md」，不传时回落真实仓库根 → conftest tripwire 硬失败。
    # is_unconfigured 必须打在 **_mw.services**（main_window 实际引用的对象）上：
    # main_window 内部走 ``from gui import services``（tools/ 在 sys.path 上时），
    # 与 ``tools.gui.services`` 是**两个模块对象**，打错对象则向导照弹 ——
    # 离屏下 wizard.exec() 无用户可交互，永久阻塞整批测试。
    # 打桩目的：tmp_path 无 ky_config，防初始化弹引导向导。
    monkeypatch.setattr(_mw.services, "is_unconfigured",
                        lambda *a, **k: False, raising=False)
    w = MainWindow(workspace_root=tmp_path)
    w.show()
    gui_app.processEvents()
    yield w
    w.close()


def test_palette_rag_activation_prompts_for_query(gui_win, gui_app, monkeypatch):
    """面板执行 ``tool:rag`` 必须先问检索词（rag 需要入参，不能无参执行）。"""
    from PySide6.QtWidgets import QInputDialog

    asked = []

    def fake_get_text(parent, title, label, **kwargs):
        asked.append(label)
        return ("剩余价值", True)

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(fake_get_text))
    started = []
    monkeypatch.setattr(
        gui_win, "_run_rag_from_dialog",
        lambda: started.append("rag") or asked.append("direct"))

    gui_win._open_command_palette()
    gui_app.processEvents()
    palette = gui_win._palette
    palette.refresh("rag")
    assert palette.visible_keys() == ["tool:rag"]
    assert palette.activate_current() == "tool:rag"
    gui_app.processEvents()
    assert started == ["rag"], "面板执行 rag 必须落到 _run_rag_from_dialog"


def test_palette_index_activation_runs_without_dialog(gui_win, gui_app, monkeypatch):
    """面板执行 ``tool:index`` 无入参，应直接起后台建索引（不弹任何对话框）。"""
    started = []
    monkeypatch.setattr(gui_win, "_run_index_action",
                        lambda: started.append("index"))

    gui_win._open_command_palette()
    gui_app.processEvents()
    palette = gui_win._palette
    for query in ("index", "索引"):
        palette.refresh(query)
        assert palette.visible_keys() == ["tool:index"], (
            f"探查 {query!r} 应唯一命中 tool:index")
    assert palette.activate_current() == "tool:index"
    gui_app.processEvents()
    assert started == ["index"], "面板执行 index 必须落到 _run_index_action"


def test_gui_card_click_routes_rag_and_index(gui_win, monkeypatch):
    """rail 工具卡点击同样能到 rag/index（``_on_card_clicked`` 必须认这两个别名）。"""
    hits = []
    monkeypatch.setattr(gui_win, "_run_rag_from_dialog", lambda: hits.append("rag"))
    monkeypatch.setattr(gui_win, "_run_index_action", lambda: hits.append("index"))

    gui_win._on_card_clicked("rag")
    gui_win._on_card_clicked("index")
    assert hits == ["rag", "index"]


def test_gui_rag_cancel_is_reported_not_silent(gui_win, monkeypatch):
    """检索词为空（用户取消）必须给提示，不得静默无反应。"""
    from PySide6.QtWidgets import QInputDialog

    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("", False)))
    gui_win._run_rag_from_dialog()
    assert "已取消" in gui_win.chat_display.toPlainText(), (
        "取消检索必须留痕（考生看不到任何反馈会以为按钮坏了）")


def _all_menu_items():
    """摊平 ``MENU_GROUPS`` 的全部条目为 (key, name, icon, desc, alias)。"""
    return [item for _t, _c, items in tui_navigator.MENU_GROUPS for item in items]