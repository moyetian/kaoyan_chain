# -*- coding: utf-8 -*-
"""
回归测试 · CLI / 启动器健壮性修复 (P6 / P7 / P8 / P10 / P11 / P20 / P21)

对应缺陷台账「多角色端到端测试审查报告.md」：
  P6  ky doctor 是只读诊断却写回 ky_config.json / AGENTS.md（safe 模式照样写）
  P7  safe 模式下输入「数学报到」抛未捕获 PermissionDeniedError + 完整堆栈
  P8  408 考生 ky map pro 误报「考纲一致性告警」
      （双导入使 knowledge_map.get_subject_name 退化为 lambda）
  P10 自愈「假成功」：pip 返回 0 即宣称 PySide6 安装成功
  P11 GUI.bat 谎报 logs\\gui_crash.log 路径
  P20 doctor 重扫把【思想政治理论】漂移成【政治】（随 P6 一并消失）
  P21 GUI.bat 的 [!] 被 enabledelayedexpansion 吞成 []

本文件只做**只读 / 沙箱内**验证：所有落盘断言均指向 tmp_path，
不会改动工作区里的 ky_config.json、各科 _状态/ 或 .memory/。
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

GUI_BAT = ROOT / "GUI.bat"


def _read_bat(path: Path) -> str:
    """读取 .bat 文本。

    [R2-C3] GUI.bat 等启动脚本已改为 GBK(CP936) 落盘 + ``chcp 936``：**不要**
    再用 ``read_text(encoding="utf-8")``，否则中文断言全部变成乱码而误判失败。
    """
    raw = path.read_bytes()
    for enc in ("gbk", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _load_module_any(name: str):
    """按项目双导入约定加载模块；不存在返回 None。"""
    try:
        return importlib.import_module(name)
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# P6 / P20 —— doctor 必须只读
# ══════════════════════════════════════════════════════════════════════════════

def test_p6_p20_doctor_is_read_only(tmp_path, monkeypatch):
    """doctor 不得做挂载写回：不调用 scan_and_mount_materials，
    也不得改动 ky_config.json / AGENTS.md（P20 的科目名漂移正是写回的副作用）。"""
    from tools import doctor

    # 构造一个"有内容"的沙箱工作区，任何写回都会体现在文件差异上
    agents = tmp_path / "AGENTS.md"
    agents.write_text(
        "# 顶层总控协议\n"
        + "这是一段用于满足文件长度校验的占位正文。\n" * 40
        + "\n  - 英语: `SENTINEL_ENGLISH_WHITELIST`\n"
        + "  - 政治: `SENTINEL_POL_WHITELIST`\n",
        encoding="utf-8",
    )
    cfg_file = tmp_path / "ky_config.json"
    cfg_file.write_text(
        json.dumps({
            "api_key": "",
            "model": "deepseek-chat",
            "active_subject": "eng",
            "study_plan": {"eng_books": "SENTINEL_ENGLISH_WHITELIST",
                           "pol_books": "SENTINEL_POL_WHITELIST"},
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    # 双导入下 material_scanner 可能有两个模块对象，两边都装上"写回探针"，
    # 确保即便发生回归也只会记录调用而不会真的写盘。
    calls = []

    def _recorder(*args, **kwargs):
        calls.append((args, kwargs))
        return {"success": True, "total_files": 0, "details": {}}

    patched = 0
    for mod_name in ("skills.material_scanner", "tools.skills.material_scanner"):
        mod = _load_module_any(mod_name)
        if mod is not None and hasattr(mod, "scan_and_mount_materials"):
            monkeypatch.setattr(mod, "scan_and_mount_materials", _recorder, raising=True)
            patched += 1
    assert patched >= 1, "未能定位 material_scanner 模块，测试前置条件不成立"

    monkeypatch.setattr(doctor, "ROOT", tmp_path)

    before_agents = agents.read_text(encoding="utf-8")
    before_cfg = cfg_file.read_text(encoding="utf-8")

    doctor.run_doctor()

    assert calls == [], "doctor 不应调用 scan_and_mount_materials（挂载写回属于 ky mount）"
    assert agents.read_text(encoding="utf-8") == before_agents, "doctor 改写了 AGENTS.md（白名单/科目名漂移）"
    assert cfg_file.read_text(encoding="utf-8") == before_cfg, "doctor 改写了 ky_config.json"
    assert not (tmp_path / ".memory" / "agents_backups").exists(), \
        "doctor 生成了 AGENTS 备份，说明发生了写回"


# ══════════════════════════════════════════════════════════════════════════════
# P7 —— safe 模式下科目切换不得抛堆栈
# ══════════════════════════════════════════════════════════════════════════════

def _run_repl_once(monkeypatch, tmp_path, inputs, read_only):
    """在沙箱内驱动一次 run_repl，返回 stdout 文本与目标配置路径。"""
    from tools.cli import shared as cli_shared
    from tools.cli.repl import loop as repl_loop

    # 把配置落盘目标重定向到 tmp，任何（意外的）写入都不会碰到真实数据
    sandbox_cfg = tmp_path / "ky_config.json"
    monkeypatch.setattr(cli_shared, "CONFIG_FILE", sandbox_cfg)

    cfg = {
        "api_key": "",
        "model": "deepseek-chat",
        "active_subject": "pol",
        "onboarding_completed": True,
        "study_plan": {},
    }
    monkeypatch.setattr(repl_loop, "load_config", lambda: dict(cfg))
    monkeypatch.setattr(repl_loop, "print_welcome", lambda *a, **k: None)
    monkeypatch.setattr(repl_loop, "start_background_live_server", lambda *a, **k: 8088)
    monkeypatch.setattr(repl_loop, "AgentRunner", None)

    it = iter(list(inputs))

    def fake_input(*args, **kwargs):
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr("builtins.input", fake_input)

    # 只读开关必须同时作用在两个模块对象上（双导入）
    ky_mods = [m for m in (_load_module_any("ky_io"), _load_module_any("tools.ky_io")) if m is not None]
    for m in ky_mods:
        m.set_read_only_mode(read_only)
    try:
        repl_loop.run_repl(permission_mode="safe" if read_only else "ask")
    finally:
        for m in ky_mods:
            m.set_read_only_mode(False)

    return sandbox_cfg


def test_p7_safe_mode_subject_switch_no_traceback(tmp_path, monkeypatch, capsys):
    """safe 模式下输入「数学报到」：给可理解提示，不抛堆栈、不崩溃、不写配置。"""
    sandbox_cfg = _run_repl_once(monkeypatch, tmp_path, ["数学报到"], read_only=True)
    out = capsys.readouterr().out

    assert "Traceback" not in out, "只读模式下不应出现未捕获异常堆栈"
    assert "PermissionDeniedError" not in out
    assert "不可切换科目" in out, "应给出可理解的只读模式提示"
    assert "只读模式" in out
    assert not sandbox_cfg.exists(), "只读模式下不得写盘 ky_config.json"


def test_p7_default_mode_subject_switch_still_persists(tmp_path, monkeypatch, capsys):
    """阴性对照：默认权限下科目切换仍必须正常持久化（修复不能误伤正常路径）。"""
    sandbox_cfg = _run_repl_once(monkeypatch, tmp_path, ["数学报到"], read_only=False)
    out = capsys.readouterr().out

    assert "不可切换科目" not in out
    assert sandbox_cfg.exists(), "默认权限下科目切换应写回 ky_config.json"
    saved = json.loads(sandbox_cfg.read_text(encoding="utf-8"))
    assert saved.get("active_subject") == "math"


# ══════════════════════════════════════════════════════════════════════════════
# P8 —— 408 考纲一致性判定不再受双导入影响
# ══════════════════════════════════════════════════════════════════════════════

_408_SYLLABUS = "\n".join([
    "# 408 官方考试大纲",
    "## 数据结构",
    "- 线性表及其顺序存储实现 (要求：掌握)",
    "- 树与二叉树遍历算法 (要求：掌握)",
    "## 计算机组成原理",
    "- 指令系统与寻址方式 (要求：掌握)",
    "## 操作系统",
    "- 进程管理与调度算法 (要求：掌握)",
    "## 计算机网络",
    "- TCP/IP 协议栈与路由选择 (要求：掌握)",
])


def _make_pro_workspace(tmp_path, pro_name):
    (tmp_path / "ky_config.json").write_text(
        json.dumps({"study_plan": {"pro_name": pro_name, "pro_key": "408"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    d = tmp_path / "04-专业课"
    d.mkdir(parents=True, exist_ok=True)
    (d / "考试大纲.md").write_text(_408_SYLLABUS, encoding="utf-8")


def test_p8_get_subject_name_not_degraded_to_lambda():
    """双导入下 get_subject_name 不得再退化成 lambda。"""
    for mod_name in ("skills.knowledge_map", "tools.skills.knowledge_map"):
        mod = _load_module_any(mod_name)
        if mod is None:
            continue
        fn = getattr(mod, "get_subject_name", None)
        assert callable(fn), f"{mod_name}.get_subject_name 不可调用"
        assert getattr(fn, "__name__", "") != "<lambda>", \
            f"{mod_name}.get_subject_name 退化为 lambda（双导入缺陷回归）"


def test_p8_408_candidate_no_false_consistency_warning(tmp_path, monkeypatch):
    """报考科目名含 408 时，408 大纲不得触发「考纲一致性告警」。"""
    km = _load_module_any("tools.skills.knowledge_map") or _load_module_any("skills.knowledge_map")
    _make_pro_workspace(tmp_path, "408 计算机学科专业基础")
    monkeypatch.setattr(km, "ROOT", tmp_path)

    data = km.build_knowledge_map("pro")

    assert data["subject_name"] == "408 计算机学科专业基础", \
        f"科目名未取到 study_plan.pro_name，实得 {data['subject_name']!r}"
    assert data["syllabus_placeholder"] is False
    assert data["syllabus_warning"] is None, f"误报告警: {data['syllabus_warning']}"


def test_p8_non_408_major_still_warns(tmp_path, monkeypatch):
    """阴性对照：报考科目名不含 408 却挂着 408 大纲时，告警必须仍然生效。"""
    km = _load_module_any("tools.skills.knowledge_map") or _load_module_any("skills.knowledge_map")
    _make_pro_workspace(tmp_path, "814 信号与系统")
    monkeypatch.setattr(km, "ROOT", tmp_path)

    data = km.build_knowledge_map("pro")

    assert data["syllabus_warning"] is not None
    assert "408" in data["syllabus_warning"]
    assert "814 信号与系统" in data["syllabus_warning"]


# ══════════════════════════════════════════════════════════════════════════════
# P10 —— 自愈必须先重新 import 校验，再报「安装成功」
# ══════════════════════════════════════════════════════════════════════════════

def test_p10_self_heal_rejects_fake_success(monkeypatch, capsys):
    """pip 返回 0 但 PySide6 仍不可导入时：不得宣称成功，且返回 False。"""
    from tools import gui_launcher as gl

    monkeypatch.setattr(gl.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0})())
    monkeypatch.setattr(gl, "_pyside6_importable", lambda: False)

    ok = gl.self_heal_pyside6()
    out = capsys.readouterr().out

    assert ok is False, "校验失败时不得返回成功"
    assert "PySide6 安装成功" not in out, "不得在未真正可导入时报「安装成功」"


def test_p10_self_heal_accepts_real_success(monkeypatch, capsys):
    """阴性对照：pip 返回 0 且重新导入校验通过时才报成功。"""
    from tools import gui_launcher as gl

    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(gl.subprocess, "run", _fake_run)
    monkeypatch.setattr(gl, "_pyside6_importable", lambda: True)

    ok = gl.self_heal_pyside6()
    out = capsys.readouterr().out

    assert ok is True
    assert "PySide6 安装成功" in out
    assert len(calls) == 1, "首选镜像成功即应结束，不再尝试其他源"


def test_p10_heal_failure_goes_to_crash_report(monkeypatch, tmp_path, capsys):
    """自愈失败时 main 必须走崩溃报告并返回退出码 2。"""
    from tools import gui_launcher as gl

    reports = []
    monkeypatch.setattr(gl, "run_preflight", lambda verbose=True: gl.PreflightStatus())
    monkeypatch.setattr(gl, "self_heal_pyside6", lambda: False)
    monkeypatch.setattr(gl, "write_crash_report",
                        lambda *a, **k: reports.append(a) or (tmp_path / "gui_crash.log"))

    saved_qt_plugin = os.environ.pop("QT_PLUGIN_PATH", None)
    try:
        ret = gl.main([])
    finally:
        if saved_qt_plugin is not None:
            os.environ["QT_PLUGIN_PATH"] = saved_qt_plugin

    assert ret == 2
    assert reports, "自愈失败必须落盘崩溃报告"
    out = capsys.readouterr().out
    assert "自动修复失败" in out


# ══════════════════════════════════════════════════════════════════════════════
# P11 / P21 —— GUI.bat 文案与转义
# ══════════════════════════════════════════════════════════════════════════════

def test_p11_gui_bat_log_path_is_conditional():
    """日志路径提示必须处于 `if exist` 守卫之内，并给出不存在时的替代指引。"""
    text = _read_bat(GUI_BAT)

    guard = 'if exist "%~dp0logs\\gui_crash.log"'
    msg = "详细诊断日志已保存至: logs\\gui_crash.log"
    fallback = "未生成 logs\\gui_crash.log"

    assert guard in text, "缺少对 logs\\gui_crash.log 存在性的判断"
    assert msg in text
    assert fallback in text, "日志不存在时应给出可行动的替代提示"
    assert text.index(guard) < text.index(msg), "日志路径提示必须出现在存在性判断之后"


def test_p21_gui_bat_has_no_raw_bang_brackets():
    """GUI.bat 在 enabledelayedexpansion 下不得残留会被吞掉的字面量 [!]。"""
    text = _read_bat(GUI_BAT)
    offenders = [ln for ln in text.splitlines() if "[!]" in ln]
    assert offenders == [], f"存在会被吞成 [] 的 [!]：{offenders}"
    assert "[^^!]" in text, "应以 ^^! 转义字面量感叹号"


@pytest.mark.skipif(sys.platform != "win32", reason="仅在 Windows 上验证 cmd 转义行为")
def test_p21_bang_escape_behaviour(tmp_path):
    """行为验证：^^! 在延迟展开下能打印出 [!]，而未转义的 [!] 会被吞成 []。"""
    bat = tmp_path / "bang.bat"
    bat.write_text(
        "@echo off\r\n"
        "setlocal enabledelayedexpansion\r\n"
        "echo TOP_ESCAPED [^^!]\r\n"
        "echo TOP_RAW [!]\r\n"
        "if 1==1 (\r\n"
        "  echo BLOCK_ESCAPED [^^!]\r\n"
        "  echo BLOCK_RAW [!]\r\n"
        ")\r\n",
        encoding="utf-8",
    )
    ret = subprocess.run(["cmd.exe", "/c", str(bat)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=60)
    out = ret.stdout
    assert "TOP_ESCAPED [!]" in out
    assert "BLOCK_ESCAPED [!]" in out
    assert "TOP_RAW []" in out, "未转义时确实会被吞掉（对照）"
    assert "BLOCK_RAW []" in out, "未转义时确实会被吞掉（对照）"
