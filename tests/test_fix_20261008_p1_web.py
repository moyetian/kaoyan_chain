# -*- coding: utf-8 -*-
"""P1 批次 · Web 看板组修复回归（W1-W4，2026-10-08）。

四条缺陷与修法：

* **W1 公式竖线破表**：``md2html`` 表格分支此前用裸 ``split("|")`` 切列，
  ``$|A|$`` 这类线代公式的竖线被当列分隔符（实测 2 列变 4 列、行列错位；
  抽卡链 ``parse_tables`` 因用 ``split_row`` 而无此问题）。现正文链改用与
  抽卡链同一份 ``split_row``（切分前先把 ``$...$`` 段占位保护）。
* **W2 表格改列表静默降级**：``build()`` 的 ``body>40`` 分支只把内容塞进
  ``notes_html``（补充说明折叠区，脱敏模式还不渲染）、零告警——实测马原
  13 张卡全丢而告警为 0。现记 ``cards_degraded`` 告警并在页脚渲染告警计数。
* **W3 裸跑误导 + 模板回落静默**：docstring 修正（默认离线、默认脱敏、
  如何跑完整模式）；模板回落时 ``sections_status`` 记 ``template_fallback``
  并同步解析告警（此前 21 条全 ok，诊断者看不出内容来自模板）。
* **W4 四科完整判据缺陷**：根 docs 同步此前用
  ``(ROOT.parent / "01-数学").exists()`` 代理「完整工作区」——不考数学的
  考生删除数学目录后 ``docs/index.html`` 静默不同步。现换 ``ky_config.json``
  稳定锚点（``root_docs_sync_enabled()``），跳过时打印原因不再静默。

夹具全部合成（``tmp_path`` + 最小 SUBJECTS/SECTIONS 打桩），不依赖真实
考生数据文件；构建类用例与 ``test_dashboard_build_placeholders.py`` 同套路，
并打桩 ``refresh_stale_today_tasks`` 隔离本地完整模式的落盘副作用（tripwire）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "05-考研看板"
for _p in (str(ROOT), str(DASHBOARD)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build  # noqa: E402
from web.markdown import md2html, parse_tables  # noqa: E402


@pytest.fixture()
def no_today_refresh(monkeypatch):
    """[tripwire 隔离] 打桩本地完整模式的今日任务刷新——否则 build() 会改写
    真实四科「今日任务.md」。打桩 study_planner 模块属性：build.py 函数内
    import 每次重新取（先 ``tools.study_planner`` 后裸名，与 build.py 同序）。
    """
    try:
        import tools.study_planner as _sp
    except ImportError:  # pragma: no cover
        import study_planner as _sp
    monkeypatch.setattr(_sp, "refresh_stale_today_tasks", lambda **k: None)


def _stub_single_section(monkeypatch, tmp_path, *, rel="学情档案.md", kw="马原",
                         tab="memo", ov=None):
    """把 build 的 SUBJECTS/SECTIONS 收敛为「单科目单章节」最小夹具。

    科目 key 用 pro2（不在 math/eng/pol/pro 内）→ 跳过知识图谱真实读取；
    科目目录为真实临时目录（``is_dir()`` 通过）；源文件内容由各用例自行
    打桩 ``build.read`` 或落盘（模板回落用例）。
    """
    subj_dir = tmp_path / "科目目录"
    subj_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(build, "SUBJECTS", [{
        "key": "pro2", "name": "测试科目", "icon": "", "color": "#7c3aed",
        "dark": "#a78bfa", "dir": subj_dir, "full": 150, "target": 100,
        "notes": None, "target_text": None,
    }])
    monkeypatch.setattr(build, "SECTIONS", {"pro2": [(rel, kw, tab, ov or {"front": 0})]})
    return subj_dir


_LIST_BODY = "## 马原\n\n" + "\n".join(
    f"- 第{i}条：这是一个足够长的列表条目，用于模拟表格被改写为列表后的章节内容"
    for i in range(1, 6)) + "\n"

_TPL_TABLE_BODY = ("## 马原\n\n| 概念 | 内容 |\n|---|---|\n"
                   "| 帽子词A | 模板演示内容一（骨架部署示例） |\n"
                   "| 帽子词B | 模板演示内容二（骨架部署示例） |\n")


# ══════════════════════════════════════════════════════════════
# W1 公式竖线破表
# ══════════════════════════════════════════════════════════════

class TestW1FormulaPipe:
    """W1：$|A|$ 公式竖线不得被正文表格渲染吞掉（修复前 2 列变 4 列）。"""

    def test_md2html_protects_formula_pipe(self):
        out = md2html("| 公式 | 说明 |\n|---|---|\n| $|A|$ | 行列式 |\n")
        assert out.count("<th>") == 2 and out.count("<td>") == 2, \
            f"公式竖线被当列分隔符（列数错）: {out}"
        assert "$|A|$" in out, "公式原文被切碎"

    def test_md2html_protects_backslash_variant(self):
        """``$\\|A\\|$``（反斜杠转义写法）同样整体保护。"""
        out = md2html("| 公式 | 说明 |\n|---|---|\n| $\\|A\\|$ | 行列式 |\n")
        assert out.count("<td>") == 2, out
        assert "$\\|A\\|$" in out, out

    def test_plain_table_rendering_unchanged(self):
        """无公式的普通表格渲染不受影响（零破坏）。"""
        out = md2html("| a | b |\n|---|---|\n| 1 | 2 |\n")
        assert out.count("<th>") == 2 and "<td>1</td>" in out, out

    def test_parse_tables_behavior_unchanged(self):
        """抽卡链（cards 渲染）行为不得因 W1 修复而改变——两链同源后一致。"""
        tables = parse_tables("| 公式 | 说明 |\n|---|---|\n| $|A|$ | 行列式 |\n")
        assert tables and tables[0][1][0] == ["$|A|$", "行列式"], tables


# ══════════════════════════════════════════════════════════════
# W2 表格改列表静默降级
# ══════════════════════════════════════════════════════════════

class TestW2DegradeWarning:
    """W2：表格改列表 → 卡片降级必须可见（告警 + 页脚计数），不再静默。"""

    def test_degraded_body_is_warned_and_footer_counts(
            self, monkeypatch, tmp_path, no_today_refresh):
        monkeypatch.setenv("KY_SNAPSHOT_OPT_IN", "1")
        _stub_single_section(monkeypatch, tmp_path)
        monkeypatch.setattr(build, "read", lambda p, allow_fallback=True: _LIST_BODY)

        html, _data, warns, _secs = build.build(offline=True)

        degraded = [w for w in warns if w.get("status") == "cards_degraded"]
        assert len(degraded) == 1, f"降级未记 parse_warnings: {warns}"
        assert degraded[0]["severity"] == "warn"
        assert "条解析告警" in html, "页脚未渲染解析告警计数"

    def test_normal_cards_do_not_trigger_degrade_warning(
            self, monkeypatch, tmp_path, no_today_refresh):
        """阴性对照：正常表格抽卡时不得误报降级告警。"""
        monkeypatch.setenv("KY_SNAPSHOT_OPT_IN", "1")
        _stub_single_section(monkeypatch, tmp_path)
        monkeypatch.setattr(
            build, "read",
            lambda p, allow_fallback=True:
            "## 马原\n\n| 概念 | 内容 |\n|---|---|\n| 帽子词A | 演示内容 |\n")

        html, data, warns, _secs = build.build(offline=True)

        assert not [w for w in warns if w.get("status") == "cards_degraded"], warns
        assert data["memo"] and data["memo"][0]["cards"], "正常卡片未进入渲染链"


# ══════════════════════════════════════════════════════════════
# W3 裸跑误导 + 模板回落静默
# ══════════════════════════════════════════════════════════════

class TestW3TemplateFallbackMark:
    """W3：模板回落须如实标记（template_fallback），不再静默当 ok。"""

    def test_fallback_marked_in_status_and_warnings(
            self, monkeypatch, tmp_path, no_today_refresh):
        monkeypatch.setenv("KY_SNAPSHOT_OPT_IN", "1")  # 脱敏模式保留回落能力
        subj_dir = _stub_single_section(monkeypatch, tmp_path, rel="核心速记.md")
        (subj_dir / "核心速记.template.md").write_text(_TPL_TABLE_BODY, encoding="utf-8")

        html, _data, warns, secs = build.build(offline=True)

        assert [s for s in secs if s.get("status") == "template_fallback"], \
            f"回落未标记 template_fallback（sections_status 仍报 ok）: {secs}"
        assert any(w.get("status") == "template_fallback" for w in warns), \
            f"回落未记解析告警: {warns}"
        assert "帽子词A" in html, "回落内容应照常进入渲染链（公开演示能力）"

    def test_full_mode_does_not_fallback_to_template(
            self, monkeypatch, tmp_path, no_today_refresh):
        """本地完整模式：缺文件不回落（file_missing 空态），模板内容不得冒充考生数据。"""
        monkeypatch.setenv("KY_SNAPSHOT_OPT_IN", "0")
        subj_dir = _stub_single_section(monkeypatch, tmp_path, rel="核心速记.md")
        (subj_dir / "核心速记.template.md").write_text(_TPL_TABLE_BODY, encoding="utf-8")

        html, _data, _warns, secs = build.build(offline=True)

        assert any(s.get("status") == "file_missing" for s in secs), secs
        assert "帽子词A" not in html, "完整模式回落到模板（假数据进入真实看板）"

    def test_docstring_documents_default_sanitized_and_full_mode(self):
        """docstring 不得再宣称「常规构建（第三方资源走 CDN）」，且须说明默认脱敏。"""
        head = (DASHBOARD / "build.py").read_text(encoding="utf-8")[:2200]
        assert "常规构建（第三方资源走 CDN）" not in head, "误导性用法说明残留"
        assert "脱敏快照" in head and "KY_SNAPSHOT_OPT_IN=0" in head, \
            "docstring 未说明默认脱敏与完整模式跑法"


# ══════════════════════════════════════════════════════════════
# W4 根 docs 同步判据
# ══════════════════════════════════════════════════════════════

class TestW4RootDocsSyncJudgment:
    """W4：判据不得依赖 01-数学（不考数学考生删目录会静默不同步）。"""

    def _fake_repo(self, tmp_path, monkeypatch, *, with_cfg=True):
        repo = tmp_path / "repo"
        pkg = repo / "05-考研看板"
        pkg.mkdir(parents=True)
        if with_cfg:
            (repo / "ky_config.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(build, "ROOT", pkg)
        monkeypatch.setattr(build, "ROOT_DOCS", repo / "docs" / "index.html")
        return repo

    def test_no_math_dir_still_syncs(self, tmp_path, monkeypatch):
        """不考数学的考生删除 01-数学 目录后，根 docs 同步照常判定为真。"""
        self._fake_repo(tmp_path, monkeypatch, with_cfg=True)
        assert build.root_docs_sync_enabled() is True, \
            "缺 01-数学 目录不应阻断根 docs 同步（W4 回归）"

    def test_uninitialized_repo_skips(self, tmp_path, monkeypatch):
        self._fake_repo(tmp_path, monkeypatch, with_cfg=False)
        assert build.root_docs_sync_enabled() is False

    def test_redirected_output_skips(self, tmp_path, monkeypatch):
        """KY_DASHBOARD_OUTPUT_DIR 隔离输出（ROOT_DOCS 不在仓库根）时不回写。"""
        self._fake_repo(tmp_path, monkeypatch, with_cfg=True)
        monkeypatch.setattr(build, "ROOT_DOCS", tmp_path / "elsewhere" / "index.html")
        assert build.root_docs_sync_enabled() is False

    def test_main_uses_single_source_judgment(self):
        """__main__ 两处同步点共用 root_docs_sync_enabled()，旧 01-数学 判据清零。"""
        src = (DASHBOARD / "build.py").read_text(encoding="utf-8")
        assert src.count("if root_docs_sync_enabled():") == 2, \
            "index.html / state_snapshot.json 两处同步点未统一走新判据"
        assert 'if ROOT_DOCS.parent.parent == ROOT.parent and (ROOT.parent / "01-数学").exists():' \
            not in src, "旧 01-数学 判据残留"


# ══════════════════════════════════════════════════════════════
# PRIV-C1：本地完整模式（未脱敏）不得回写 Git 跟踪的根 docs/
# ══════════════════════════════════════════════════════════════


class TestPrivC1UnsanitizedSnapshotMustNotSync:
    """[PRIV-C1]根 docs/ 是 Git 跟踪目录且 origin 直连公开仓库。

    本地完整模式（KY_SNAPSHOT_OPT_IN=0）产物含真实学情（真实校名/自命题科目），
    写入根 docs/ 后一次 ``add -A`` 提交推送即构成隐私泄漏。
    发布出口的 ensure_sanitized_docs_for_publish() 只保护**公开副本**，
    不保护主仓库工作区，故须在此处自拦。
    """

    def _fake_repo(self, tmp_path, monkeypatch, *, with_cfg=True):
        repo = tmp_path / "repo"
        pkg = repo / "05-考研看板"
        pkg.mkdir(parents=True)
        if with_cfg:
            (repo / "ky_config.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(build, "ROOT", pkg)
        monkeypatch.setattr(build, "ROOT_DOCS", repo / "docs" / "index.html")
        return repo

    def test_local_full_mode_does_not_sync(self, tmp_path, monkeypatch):
        """本地完整模式（opt_in=False）下判据必须为假。修复前恒为真。"""
        self._fake_repo(tmp_path, monkeypatch, with_cfg=True)
        monkeypatch.setattr(build, "snapshot_opt_in", lambda: False)
        assert build.root_docs_sync_enabled() is False, \
            "本地完整模式的未脱敏快照被写进 Git 跟踪的根 docs/（隐私泄漏）"

    def test_sanitized_mode_still_syncs(self, tmp_path, monkeypatch):
        """脱敏模式（默认 opt_in=True）下同步照常，闸门不得把正常路径也拦掉。"""
        self._fake_repo(tmp_path, monkeypatch, with_cfg=True)
        monkeypatch.setattr(build, "snapshot_opt_in", lambda: True)
        assert build.root_docs_sync_enabled() is True

    def test_judgment_calls_snapshot_opt_in(self):
        """判据必须真的读脱敏状态（钉住调用点，防被摘掉后静默失效）。"""
        src = (DASHBOARD / "build.py").read_text(encoding="utf-8")
        fn_start = src.index("def root_docs_sync_enabled()")
        # 函数体到下一个顶层 def 之前（build.py 里该函数是文件最后一个顶层定义，
        # 故用「再下一个 def 或 EOF」兜底，避免 src.index 抛 ValueError）
        rest = src[fn_start + 10:]
        nxt = rest.find("\ndef ")
        fn_body = rest if nxt == -1 else rest[:nxt]
        assert "snapshot_opt_in()" in fn_body, \
            "root_docs_sync_enabled 未校验脱敏状态（PRIV-C1 回归）"

