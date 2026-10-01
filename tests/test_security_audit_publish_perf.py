# -*- coding: utf-8 -*-
"""审计 2026-09-30 消缺回归：P1-11（看板发布链路脱敏缺口）与 PF-1（GUI 主线程扫盘）。

**P1-11 背景**：``update_dashboard.py --push`` 的直推链路此前只做「脱敏构建」
（剥离私人学习记录正文），不跑 ``sync_publish`` 的内容级身份替换与残留自检 ——
卡片正面手写的校名 / 科目组合会明文进 GitHub Pages。修复把同一套
privacy_policy 引擎接到直推链路：构建(脱敏) → 产物内容级替换 → 残留自检 →
发现残留则**阻断提交与推送**。

**PF-1 背景**：``MainWindow._refresh_all`` 一次刷新经 services 多条路径触发
5 次 ``load_state``（约 20 次状态文件读）+ 2 次错题本全扫。修复在
``gui/services/dashboard.py`` 加输入文件 mtime 指纹缓存（调用结构不变）。

测试数据一律使用**中性占位**（示例农业大学 / 示例理论 …），不含任何真实身份 ——
``tests/`` 对导出脱敏免疫的不变量由 ``test_privacy_identity_rules`` 钉住。
推送路径全部通过打桩 ``subprocess.run`` 完成，**绝不真的执行 git / 构建 / 网络**。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# GUI 测试离屏运行（与 test_gui_redesign / test_gui_smoke 同口径）
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import update_dashboard as ud  # noqa: E402

# 与 main_window 同一条导入路径取 services：双导入会让 gui.* 与 tools.gui.*
# 成为两份模块对象，而 main_window 优先走 gui.* —— patch 必须打在同一个模块上。
try:  # pragma: no cover - 取决于运行方式
    from gui.services import dashboard as dash
except ImportError:  # pragma: no cover
    from tools.gui.services import dashboard as dash  # type: ignore


class _FakeCompleted:
    """最小可用的 ``subprocess.run`` 返回值替身。"""

    def __init__(self, returncode=0):
        self.returncode = returncode


# ═══════════════════ P1-11：直推链路的脱敏 + 自检 + 阻断 ═══════════════════

def _make_repo(tmp_path: Path, docs_files: dict) -> Path:
    """造一个含自造身份配置与指定 docs/ 产物的假仓库根。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "ky_config.json").write_text(json.dumps({
        "onboarding_completed": True,
        "study_plan": {"school": "示例农业大学", "major": "030500 示例理论",
                       "pro_name": "618 示例科目甲 823 示例科目乙"},
    }, ensure_ascii=False), encoding="utf-8")
    for rel, text in docs_files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return repo


def test_sanitize_products_scrubs_identity_and_scan_passes(tmp_path):
    """脱敏：docs/ 产物中的校名/专业被替换；干净产物逐字节不变；自检通过。"""
    clean = "<html>公开说明</html>"
    repo = _make_repo(tmp_path, {
        "docs/index.html": "<html>示例农业大学真题精讲</html>",
        "docs/live.html": clean,
        "docs/state_snapshot.json": json.dumps(
            {"data": {"f": "示例农业大学 030500 示例理论"}}, ensure_ascii=False),
    })

    changed = ud._sanitize_publish_products(repo)

    assert changed == 2, f"应改写 index.html 与 state_snapshot.json（实际 {changed}）"
    html = (repo / "docs" / "index.html").read_text(encoding="utf-8")
    snap = (repo / "docs" / "state_snapshot.json").read_text(encoding="utf-8")
    assert "示例农业大学" not in html, html
    assert "示例农业大学" not in snap, snap
    json.loads(snap)  # 文本级替换不得破坏 JSON 结构
    assert (repo / "docs" / "live.html").read_text(encoding="utf-8") == clean, \
        "无命中的产物不得被改写（逐字节不变）"
    assert ud._scan_publish_residuals(repo) == [], "脱敏后自检应无残留"


def test_scan_detects_identity_outside_sanitize_scope(tmp_path):
    """阴性对照：脱敏后缀范围外的文件含身份时，自检必须命中（兜底不是空转）。"""
    repo = _make_repo(tmp_path, {"docs/leak.txt": "示例农业大学真题"})
    assert ud._scan_publish_residuals(repo) == ["leak.txt"]


def test_sanitize_leaves_static_assets_untouched(tmp_path):
    """脱敏范围只含构建产物：静态 assets（非构建产物）逐字节不变。

    静态配图会被推送流程原样保留 —— 改写它们会污染工作区并与生成脚本漂移
    （测试钉住「配图与源脚本同源」）；其中的当前身份由全树自检兜底。
    """
    static = "<svg>【高校 A】华中科技大学</svg>"
    repo = _make_repo(tmp_path, {
        "docs/index.html": "<html>干净</html>",
        "docs/assets/diagram.svg": static,
    })
    assert ud._sanitize_publish_products(repo) == 0
    assert (repo / "docs" / "assets" / "diagram.svg").read_text(encoding="utf-8") == static


def test_scan_and_sanitize_noop_without_docs(tmp_path):
    """无 docs/ 产物时不崩、不改写（对应「无白名单产物直接跳过」路径）。"""
    repo = tmp_path / "empty"
    repo.mkdir()
    assert ud._sanitize_publish_products(repo) == 0
    assert ud._scan_publish_residuals(repo) == []


def _install_fake_subprocess(monkeypatch):
    """打桩 ``ud.subprocess.run``：记录调用，绝不真执行 git。"""
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append([str(c) for c in cmd])
        return _FakeCompleted(0)

    monkeypatch.setattr(ud.subprocess, "run", fake_run)
    return calls


def test_push_blocked_when_residual_remains(tmp_path, monkeypatch, capsys):
    """残留时阻断：不提交、不推送，退出码 3，且打印命中文件。"""
    repo = _make_repo(tmp_path, {
        "docs/index.html": "<html>干净</html>",
        "docs/leak.txt": "示例农业大学真题",   # 脱敏范围外 → 自检兜底拦截
    })
    monkeypatch.setattr(ud, "ROOT", repo)
    monkeypatch.setattr(sys, "argv", ["update_dashboard.py", "--push"])
    monkeypatch.setattr(ud, "_run_build", lambda sanitized: 0)
    calls = _install_fake_subprocess(monkeypatch)

    with pytest.raises(SystemExit) as exc:
        ud.main()

    assert exc.value.code == 3, f"残留未阻断（退出码 {exc.value.code}）"
    assert not any("commit" in c or "push" in c or "add" in c for c in calls), \
        f"残留时仍触发了 git 操作: {calls}"
    out = capsys.readouterr().out
    assert "leak.txt" in out, f"未打印命中文件: {out}"
    assert "阻断" in out, f"未打印可辨识的阻断提示: {out}"


def test_push_proceeds_when_products_clean(tmp_path, monkeypatch):
    """阴性对照：产物干净时照常提交推送（证明上面的「不推送」不是整体空转）。"""
    repo = _make_repo(tmp_path, {"docs/index.html": "<html>干净</html>"})
    monkeypatch.setattr(ud, "ROOT", repo)
    monkeypatch.setattr(sys, "argv", ["update_dashboard.py", "--push"])
    monkeypatch.setattr(ud, "_run_build", lambda sanitized: 0)
    calls = _install_fake_subprocess(monkeypatch)

    ud.main()

    assert any("commit" in c for c in calls), f"干净产物应照常提交: {calls}"
    assert any("push" in c for c in calls), f"干净产物应照常推送: {calls}"


# ═══════════════════ PF-1：GUI 数据层 mtime 缓存 ═══════════════════

def _write_workspace(root: Path) -> Path:
    """造一个「已配置」的最小工作区（含一科今日任务），返回任务文件路径。"""
    (root / "02-英语" / "_状态").mkdir(parents=True)
    task = root / "02-英语" / "_状态" / "今日任务.md"
    task.write_text(
        "| 模块 | 任务内容 | 预计用时 | 完成状态 |\n|---|---|---|---|\n"
        "| 阅读 | 精读一篇真题 | 60min | [ ] |\n", encoding="utf-8")
    (root / "ky_config.json").write_text(json.dumps({
        "onboarding_completed": True,
        "study_plan": {"school": "示例农业大学", "major": "030500 示例理论",
                       "total_hours": 6.5},
    }, ensure_ascii=False), encoding="utf-8")
    return task


def _bump_mtime(path: Path) -> None:
    """显式推进文件 mtime（规避文件系统时间戳粒度导致的假「未变」）。"""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def test_load_state_mtime_cache(tmp_path, monkeypatch):
    """连续两次 load_state 只真实装载一次；文件变化后缓存失效重装。"""
    root = tmp_path / "ws"
    task = _write_workspace(root)

    calls = []
    real = dash.load_dashboard_state

    def counting(r, *args, **kwargs):
        calls.append(str(r))
        return real(r, *args, **kwargs)

    monkeypatch.setattr(dash, "load_dashboard_state", counting)

    s1 = dash.load_state(root)
    s2 = dash.load_state(root)
    assert len(calls) == 1, f"第二次 load_state 仍触发真实装载: {calls}"
    assert s2 is s1, "缓存应返回同一状态对象（frozen dataclass，调用方只读）"

    _bump_mtime(task)                      # 任务文件被改写 → 指纹变化
    s3 = dash.load_state(root)
    assert len(calls) == 2, f"文件变化后未重新装载: {calls}"
    assert s3 is not s1


def test_error_queue_cards_memoized(tmp_path, monkeypatch):
    """错题本文件未变时，第二次调用不再读取任何错题文件（memo 生效）。

    对应 ``_refresh_all`` 内 error_queue_cards 被调两次（统计块 +
    ``render_error_cards``）的场景：第二次只做 glob + stat，不重复解析。
    """
    root = tmp_path / "ws"
    mistakes = root / "01-数学" / "错题本"
    mistakes.mkdir(parents=True)
    (mistakes / "错题1.md").write_text(
        "## 📌 [2026-09-01] 示例错题\n\n**掌握状态**：待复测\n", encoding="utf-8")

    reads = []
    orig = Path.read_text

    def counting(self, *args, **kwargs):
        if str(self).startswith(str(mistakes)):
            reads.append(str(self))
        return orig(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting)

    r1 = dash.error_queue_cards(root)
    n1 = len(reads)
    assert n1 >= 1 and r1, "前置：第一次调用必须真实解析错题文件"

    r2 = dash.error_queue_cards(root)
    assert len(reads) == n1, f"第二次调用仍读文件（memo 未生效）: {reads[n1:]}"
    assert r2 == r1


def test_refresh_all_loads_state_once(tmp_path, monkeypatch):
    """PF-1 核心断言：一次 ``_refresh_all`` 内底层状态装载恰好一次（原为 5 次）。"""
    pytest.importorskip("PySide6", reason="未安装 PySide6，跳过 GUI 刷新测试")
    from PySide6.QtWidgets import QApplication

    try:  # 与 main_window 同一条导入路径（见文件头说明）
        from gui.main_window import MainWindow
    except ImportError:  # pragma: no cover
        from tools.gui.main_window import MainWindow  # type: ignore

    app = QApplication.instance() or QApplication(sys.argv)
    root = tmp_path / "ws"
    task = _write_workspace(root)

    calls = []
    real = dash.load_dashboard_state

    def counting(r, *args, **kwargs):
        calls.append(str(r))
        return real(r, *args, **kwargs)

    monkeypatch.setattr(dash, "load_dashboard_state", counting)

    win = MainWindow(workspace_root=root)
    try:
        # 构造期含一次完整 _refresh_all：多条 services 路径只应触发一次真实装载
        assert len(calls) == 1, \
            f"构造期（含一次 _refresh_all）应只真实装载一次: {calls}"

        _bump_mtime(task)                  # 使缓存失效
        before = len(calls)
        win._refresh_all()
        assert len(calls) == before + 1, \
            f"一次刷新应只重装一次（实际 {len(calls) - before} 次）"
    finally:
        win.close()
        app.processEvents()
