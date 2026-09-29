# -*- coding: utf-8 -*-
"""W12 报告修复补充回归 —— 此前仅有真机验收、无独立单测的 9 项。

对应护理三端实测报告（模拟实例 `D:\\测试\\考研学习chain`）中尚无单测锁定的修复：

* P0-1 ``resolve_intel_import`` 三端统一（TUI 直跑不再报 No module named 'tools.intelligence'）
* P0-2 ``compare --quick`` 离线兜底（不触在线研究、含「未核验/本地」标注）
* P0-3 ``init_workspace --help`` 零落盘
* P1-4 TUI action 6 院校侦察透传 ``--school1/--major``
* P1-5 ``infer_diff_naming`` 报告命名随实（英语 Diff 不得张冠李戴成旧志愿）
* P1-7 ``watch`` 无官网域名高校降级研招网占位（显式标注未核验），不再硬拒
* P2-9 ``wechat --no-fetch`` 报告头含检索源清单
* P2-10 空题库退出码统一 EXIT 2（CLI 与 TUI 同一文案）

测试数据一律中性占位；沙箱通过 ``shutil.copytree`` 复制 tools/，真实工作区零触碰。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ───────────────────────── 沙箱（真实 CLI 子进程） ─────────────────────────

@pytest.fixture(scope="module")
def sandbox_ws(tmp_path_factory):
    """把 tools/ 复制到独立沙箱：各 CLI 的 ROOT 由 ``__file__`` 推导，指向沙箱根。

    与 ``test_fix_safe_mode_write_gate.sandbox_workspace`` 同一模式；本文件所有
    用例只做只读/拒绝路径（--help、空题库未组卷、--no-fetch），不写沙箱数据。
    """
    root = tmp_path_factory.mktemp("w12fix") / "ws"
    root.mkdir()
    shutil.copytree(ROOT / "tools", root / "tools",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (root / "ky_config.json").write_text(json.dumps({
        "api_provider": "deepseek",
        "active_subject": "pro",
        "study_plan": {
            "school": "沙箱院校", "major": "030100 法学",
            "math_key": "none", "eng_key": "eng1", "pro_type": "custom",
            "pro_name": "610 法学基础", "pro_books": "沙箱原始值",
        },
        "completion_history": {},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "AGENTS.md").write_text(
        "# 顶层总控协议\n" + "沙箱占位正文。\n" * 40, encoding="utf-8")
    return root


def _run_in_sandbox(root: Path, argv, timeout=180):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable] + argv, cwd=str(root), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, timeout=timeout)


def _snapshot(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            out[str(p.relative_to(root))] = p.stat().st_size
    return out


# ───────────────────────── P0-3 init_workspace --help ─────────────────────────

def test_init_workspace_help_zero_write_in_sandbox(sandbox_ws):
    """--help 必须打印用法后短路退出；若回退成真跑向导，沙箱必产生新文件/报错。"""
    before = _snapshot(sandbox_ws)
    r = _run_in_sandbox(sandbox_ws, ["tools/init_workspace.py", "--help"])
    assert r.returncode == 0, f"EXIT={r.returncode}\n{r.stdout[-500:]}\n{r.stderr[-500:]}"
    assert "用法" in r.stdout, f"未打印用法：{r.stdout[-300:]}"
    assert _snapshot(sandbox_ws) == before, "init_workspace --help 改动了沙箱文件"


# ───────────────────────── P2-10 空题库退出码统一 ─────────────────────────

def test_cli_exam_empty_bank_exits_2_in_sandbox(sandbox_ws):
    """ky exam 在无题源工作区必须 EXIT 2 且给出「未组卷」引导（脚本可判定）。"""
    r = _run_in_sandbox(sandbox_ws, ["tools/ky_cli.py", "exam", "pro", "--count=2"])
    assert r.returncode == 2, f"got {r.returncode}\n{r.stdout[-500:]}"
    assert "未组卷" in r.stdout, f"缺少「未组卷」文案：{r.stdout[-300:]}"


def test_tui_exam_empty_bank_exits_2_in_sandbox(sandbox_ws):
    """TUI --action 2 与 CLI 同场景必须同一退出码 2 与同一「未组卷」文案。"""
    r = _run_in_sandbox(sandbox_ws, ["tools/tui_navigator.py", "--action", "2"])
    assert r.returncode == 2, f"got {r.returncode}\n{r.stdout[-500:]}"
    assert "未组卷" in r.stdout, f"缺少「未组卷」文案：{r.stdout[-300:]}"


# ───────────────────────── P0-1 intelligence 三端统一解析 ─────────────────────────

def test_intel_import_helper_is_single_source():
    """resolve_intel_import 返回 tools.intelligence 本体，且四端均引用同一助手。"""
    from tools import intel_imports
    import tools.intelligence as ti
    assert intel_imports.resolve_intel_import() is ti, \
        "解析结果必须是 tools.intelligence 本体（防双份模块对象）"

    for rel in ("tools/tui_navigator.py", "tools/cli/commands/intel.py",
                "tools/gui/services/actions.py", "tools/skills/material_scanner.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "resolve_intel_import" in src, f"{rel} 未走三端统一解析"
    tui_src = (ROOT / "tools" / "tui_navigator.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*from intelligence import", tui_src, re.M), \
        "TUI 仍残留裸 from intelligence import（直跑必崩的原始写法）"


# ───────────────────────── P0-2 compare --quick 离线兜底 ─────────────────────────

def test_compare_quick_skips_online_research(monkeypatch):
    """quick=True 不得触发在线研究；返回完整报告且标注「未核验/本地」。"""
    from tools.intelligence.comparator import get_school_comparator
    comp = get_school_comparator()

    def _boom(*a, **k):
        raise AssertionError("quick 模式不应调用在线研究 _get_two_profiles")

    monkeypatch.setattr(comp, "_get_two_profiles", _boom)
    r = comp.compare("中国医科大学", "哈尔滨医科大学", "护理", quick=True)
    assert r.get("terminal_report") and r.get("markdown_report"), "quick 模式必须出报告"
    blob = r["terminal_report"] + r["markdown_report"]
    assert ("未核验" in blob) or ("本地" in blob), "降级报告必须显式标注数据来源属性"


# ───────────────────────── P1-5 Diff 命名随实 ─────────────────────────

def test_infer_diff_naming_follows_subject_paths():
    """公共课目录 → (全国统考, 科目)；专业课/无科目路径 → (None, None) 交回调用方。"""
    from tools.intelligence.syllabus_diff import infer_diff_naming
    assert infer_diff_naming("02-英语/考试大纲.md", "02-英语/考试大纲.md") == ("全国统考", "英语")
    assert infer_diff_naming(None, "03-思想政治理论/考试大纲.md") == ("全国统考", "思想政治理论")
    # 专业课与志愿绑定、路径不含科目目录：一律回退 None，不得硬编码校名
    assert infer_diff_naming("04-专业课/考试大纲.md", "04-专业课/2027.md") == (None, None)
    assert infer_diff_naming("a.md", "b.md") == (None, None)


# ───────────────────────── P1-7 watch 无域名降级占位 ─────────────────────────

class _StubFetcher:
    """离线替身：任何 URL 都返回无效内容（不触网，is_valid=False 跳过指纹提取）。"""

    class _Res:
        content = ""
        is_valid = False

    def fetch(self, url):        # noqa: D102 - 测试替身
        return self._Res()


def _patch_watch_env(monkeypatch, tmp_path, entity):
    from tools.intelligence import watcher as watcher_mod
    monkeypatch.setattr(watcher_mod, "WATCH_FILE", tmp_path / "watch.json")
    monkeypatch.setattr(watcher_mod, "resolve_university", lambda q: entity)
    return watcher_mod


def _entity(**kw):
    from types import SimpleNamespace
    base = dict(name="测试院校丙", chsi_code="00000", chsi_url="",
                official_domain="", graduate_domain="", admission_domain="")
    base.update(kw)
    return SimpleNamespace(**base)


def test_watch_falls_back_to_chsi_placeholder_url(monkeypatch, tmp_path):
    """三域名全空但 chsi_url 存在 → 降级占位可监控，且记录显式标注未核验。"""
    watcher_mod = _patch_watch_env(monkeypatch, tmp_path, _entity(
        chsi_url="https://yz.chsi.com.cn/sch/schoolInfo--schId-000.dhtml"))
    w = watcher_mod.AdmissionWatcher(fetcher=_StubFetcher())
    res = w.add_watch("测试院校丙")
    assert res.get("success") is True, f"占位降级仍被拒：{res}"
    rec = w.watch_data.get("00000") or {}
    assert rec.get("url") == "https://yz.chsi.com.cn/sch/schoolInfo--schId-000.dhtml"
    assert "未核验" in str(rec.get("source_note") or ""), "占位来源必须显式标注未核验"


def test_watch_prefers_real_domain_and_rejects_fully_empty(monkeypatch, tmp_path):
    """阴性对照：有官网域名时用真实域名且无占位标注；全空（无域名无 chsi_url）仍拒绝。"""
    watcher_mod = _patch_watch_env(monkeypatch, tmp_path, _entity(
        official_domain="https://www.example-univ.test"))
    w = watcher_mod.AdmissionWatcher(fetcher=_StubFetcher())
    res = w.add_watch("测试院校丙")
    assert res.get("success") is True
    rec = w.watch_data.get("00000") or {}
    assert rec.get("url") == "https://www.example-univ.test"
    assert not rec.get("source_note"), "真实域名不得带占位标注"

    watcher_mod2 = _patch_watch_env(monkeypatch, tmp_path, _entity())
    w2 = watcher_mod2.AdmissionWatcher(fetcher=_StubFetcher())
    res2 = w2.add_watch("测试院校丙")
    assert res2.get("success") is False and "域名" in res2.get("msg", ""), \
        "无任何可用 URL 时必须仍拒绝并说明原因"


# ───────────────────────── P2-9 wechat 源清单 ─────────────────────────

def test_wechat_no_fetch_lists_sources_in_sandbox(sandbox_ws):
    """--no-fetch 报告头必须列出检索源清单（区分「没有结果」与「源异常」）。"""
    r = _run_in_sandbox(
        sandbox_ws,
        ["tools/ky_cli.py", "wechat", "测试关键词", "--no-fetch", "--max=3"],
        timeout=180)
    assert r.returncode == 0, f"EXIT={r.returncode}\n{r.stderr[-400:]}"
    assert "检索源" in r.stdout, f"报告头缺检索源清单：{r.stdout[-400:]}"


# ───────────────────────── P1-4 TUI 院校侦察透传 ─────────────────────────

def test_tui_scout_uses_explicit_school(monkeypatch, capsys):
    """--action 6 --school1/--major 必须透传到 scout（此前恒用 config 旧志愿）。"""
    import importlib
    for p in (str(ROOT), str(ROOT / "tools")):
        if p not in sys.path:
            sys.path.insert(0, p)
    tui = importlib.import_module("tui_navigator")
    scout_mod = importlib.import_module("skills.school_scout")

    calls = {}

    def _fake_scout(**kw):
        calls.update(kw)
        return {"saved_path": "沙箱研报.md"}

    monkeypatch.setattr(scout_mod, "scout_school", _fake_scout)
    monkeypatch.setattr(sys, "argv", [
        "tui_navigator.py", "--action", "6",
        "--school1", "测试院校乙", "--major", "030100 测试专业"])
    tui.main()
    capsys.readouterr()          # 消化输出，保持测试干净
    assert calls.get("school") == "测试院校乙", f"传入学校未透传：{calls}"
    assert calls.get("major") == "030100 测试专业", f"传入专业未透传：{calls}"
