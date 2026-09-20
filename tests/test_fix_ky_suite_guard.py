# -*- coding: utf-8 -*-
"""R2-D7 回归：独立测试套件不得把残缺配置写进真实工作区。

事故背景（实测，2026-09-19）：
    ``tools/test_ky_suite.py`` 是自带 runner 的**独立脚本**，跑在 pytest 之外，
    因此 ``tests/conftest.py`` 的 CONFIG_FILE 绊线保护不到它。它会真实改写工作区
    用户数据：
      * 测试组 10 的 ``run_study_plan_wizard()`` 把 ``ky_config.json`` 的
        ``study_plan`` 换成默认模板（目标院校 / math2 / 408 / 8.5h）；
      * 测试组 18 的 ``apply_scout_to_config`` 曾把真实配置冲成
        ``{浙江大学/人工智能/408}``（该函数 ``config_path`` 只隔离读、不隔离写）。
    旧兜底是「内存快照 + ``finally`` + ``atexit``」，SIGKILL / 超时 / 关控制台时
    三者都不执行 → 残缺配置留在盘上；下一次运行又会在快照阶段拍到这份已污染内容，
    还原时「忠实地」写回 → 自锁。

本文件锁三件事：
    1. 真实用户工作区**默认拒跑**（exit 2），且**一个字节都不写**；
    2. 模板 / CI 工作区**不被误拦**（仍能正常开跑）；
    3. 被强杀留下的落盘快照，能在**下一次启动时自动还原**。
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SUITE_SRC = REPO_ROOT / "tools" / "test_ky_suite.py"

REAL_CFG = {
    "study_plan": {
        "school": "目标院校",
        "major": "目标专业 (专业代码)",
        "math_key": "none",
        "eng_key": "eng1",
        "pro_name": "自命题专业课科目",
        "total_hours": 6.5,
    },
    "api_key": "sk-REAL-LOOKING-KEY-abcdef",
    "model": "mimo-v2.5",
}
TEMPLATE_CFG = {
    "study_plan": {"school": "目标院校", "major": "报考专业", "math_key": "math2"},
    "api_key": "YOUR_API_KEY_HERE",
}


def _make_fake_workspace(tmp_path: Path, cfg: dict) -> Path:
    """造一个只有 tools/test_ky_suite.py + ky_config.json 的最小工作区。

    脚本用 ``ROOT = Path(__file__).resolve().parent.parent`` 定位工作区，所以把脚本
    放进 ``<tmp>/tools/`` 就能让它的 ROOT 指向 tmp，而不必复制整个仓库；
    项目模块靠 PYTHONPATH 从真实仓库解析。
    """
    (tmp_path / "tools").mkdir(parents=True, exist_ok=True)
    shutil.copy2(SUITE_SRC, tmp_path / "tools" / "test_ky_suite.py")
    (tmp_path / "ky_config.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return tmp_path


def _env() -> dict:
    env = dict(os.environ)
    env.pop("KY_TEST_ALLOW_REAL_WORKSPACE", None)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT), str(REPO_ROOT / "tools"),
         env.get("PYTHONPATH", "")]).strip(os.pathsep)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def test_real_workspace_is_refused_and_config_untouched(tmp_path):
    """阴性对照：真实工作区必须 exit 2，且 ky_config.json 逐字节不变。"""
    ws = _make_fake_workspace(tmp_path, REAL_CFG)
    cfg_path = ws / "ky_config.json"
    before = cfg_path.read_bytes()

    res = subprocess.run(
        [sys.executable, "-u", "tools/test_ky_suite.py"],
        cwd=str(ws), env=_env(), capture_output=True, text=True, timeout=120,
    )

    assert res.returncode == 2, f"应拒绝运行(exit 2)，实际 {res.returncode}\n{res.stdout[-2000:]}"
    assert "已拒绝运行" in res.stdout
    assert cfg_path.read_bytes() == before, "拒跑路径不得改动用户配置"
    # 拒跑发生在任何快照/写入之前，连守卫目录都不该产生
    assert not (ws / ".pytest_tmp").exists()


def test_refusal_message_shows_root_and_verdict_basis(tmp_path):
    """拒跑文案必须给出 ROOT 绝对路径与判定依据，用户才能核对「是否认错地方」。

    同时锁定三种情形的指引：忠实副本（数据可丢弃 → 显式放行）、git archive 副本
    （守卫不触发但计数环境不同）、真实工作区（先备份再放行）。
    """
    ws = _make_fake_workspace(tmp_path, REAL_CFG)

    res = subprocess.run(
        [sys.executable, "-u", "tools/test_ky_suite.py"],
        cwd=str(ws), env=_env(), capture_output=True, text=True, timeout=120,
    )

    assert res.returncode == 2, f"应拒绝运行(exit 2)，实际 {res.returncode}\n{res.stdout[-2000:]}"
    out = res.stdout
    assert f"ROOT = {ws}" in out, f"未输出 ROOT 绝对路径，用户无法核对是否认错地方:\n{out[:1500]}"
    assert "判定依据" in out and "目标院校" in out, (
        f"判定依据必须点名命中的字段值:\n{out[:1500]}")
    assert "忠实副本" in out, "缺少「忠实副本会被同样拒跑」的说明"
    assert "git archive" in out, "缺少 git archive 副本情形的指引"
    assert "KY_TEST_ALLOW_REAL_WORKSPACE=1" in out


def test_template_workspace_is_not_refused(tmp_path):
    """防过度拦截：模板 / CI 工作区必须照常开跑（不能一刀切拒跑）。"""
    ws = _make_fake_workspace(tmp_path, TEMPLATE_CFG)
    proc = subprocess.Popen(
        [sys.executable, "-u", "tools/test_ky_suite.py"],
        cwd=str(ws), env=_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    try:
        time.sleep(8)
    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()

    assert "已拒绝运行" not in (out or ""), f"模板工作区被误拦：\n{(out or '')[:1500]}"
    assert "开始对 考研学习链" in (out or ""), f"套件未正常开跑：\n{(out or '')[:1500]}"


def test_workspace_without_config_is_not_refused(tmp_path):
    """防过度拦截（CI / 全新检出）：没有 ky_config.json 时必须照常开跑。

    `.github/workflows/test.yml:70` 就是直接跑这个脚本，干净检出里没有 ky_config.json。
    """
    (tmp_path / "tools").mkdir(parents=True, exist_ok=True)
    shutil.copy2(SUITE_SRC, tmp_path / "tools" / "test_ky_suite.py")
    assert not (tmp_path / "ky_config.json").exists()

    proc = subprocess.Popen(
        [sys.executable, "-u", "tools/test_ky_suite.py"],
        cwd=str(tmp_path), env=_env(), stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
    )
    try:
        time.sleep(8)
    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()

    assert "已拒绝运行" not in (out or ""), f"无配置(CI)场景被误拦：\n{(out or '')[:1500]}"
    assert "开始对 考研学习链" in (out or ""), f"套件未正常开跑：\n{(out or '')[:1500]}"


def test_killed_run_is_healed_on_next_start(tmp_path):
    """强杀安全：落盘的 running.lock + 快照必须在下一次启动时被用来还原现场。"""
    import base64

    ws = _make_fake_workspace(tmp_path, TEMPLATE_CFG)
    cfg_path = ws / "ky_config.json"

    # 现状 = 上次被强杀后留下的残缺态（向导默认模板）
    cfg_path.write_text(json.dumps(
        {"study_plan": {"school": "目标院校", "major": "报考专业", "math_key": "math2",
                        "pro_name": "408 计算机学科专业基础", "total_hours": 8.5},
         "api_key": "sk-REAL-LOOKING-KEY-abcdef"},
        ensure_ascii=False, indent=2), encoding="utf-8")

    # 落盘快照 = 被强杀前的 good 态
    guard = ws / ".pytest_tmp" / "ky_suite_guard"
    guard.mkdir(parents=True, exist_ok=True)
    good_bytes = json.dumps(REAL_CFG, ensure_ascii=False, indent=2).encode("utf-8")
    (guard / "snapshot.json").write_text(json.dumps(
        {"pid": 999999,
         "files": {"ky_config.json": base64.b64encode(good_bytes).decode("ascii")}},
        ensure_ascii=False), encoding="utf-8")
    (guard / "running.lock").write_text("999999", encoding="utf-8")

    res = subprocess.run(
        [sys.executable, "-u", "tools/test_ky_suite.py"],
        cwd=str(ws), env=_env(), capture_output=True, text=True, timeout=120,
    )

    out = res.stdout
    assert "已自动还原" in out, f"未触发自愈：\n{out[:1500]}"
    restored = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert restored["study_plan"]["school"] == "目标院校"
    assert restored["study_plan"]["math_key"] == "none"
    assert restored["study_plan"]["total_hours"] == 6.5
    # 还原后变成真实工作区 → 本次运行应被拒跑；守卫文件应被清空
    assert res.returncode == 2
    assert not (guard / "running.lock").exists()
    assert not (guard / "snapshot.json").exists()


def test_apply_scout_to_config_honors_config_path(tmp_path):
    """R2-D7 根因：``config_path`` 必须同时隔离读与写（真实配置一个字节不动）。"""
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    try:
        from skills import experience_dossier as ed
    except ImportError:
        pytest.skip("experience_dossier 不可导入")
    finally:
        try:
            sys.path.remove(str(REPO_ROOT / "tools"))
        except ValueError:
            pass

    real = tmp_path / "real_ky_config.json"
    real.write_text(json.dumps(
        {"study_plan": {"school": "目标院校", "major": "目标专业 (专业代码)"},
         "api_key": "sk-REAL-LOOKING-KEY-abcdef"},
        ensure_ascii=False, indent=2), encoding="utf-8")
    real_before = real.read_bytes()

    target = tmp_path / "target.json"
    target.write_text(json.dumps({"study_plan": {"school": "原目标", "major": "原专业"}},
                                 ensure_ascii=False), encoding="utf-8")

    orig_config_file = getattr(ed, "CONFIG_FILE", None)
    ed.CONFIG_FILE = real
    try:
        ok = ed.apply_scout_to_config(
            "浙江大学", "人工智能",
            metrics={"subjects_hint": ["408 计算机学科专业基础 (全国统考)"]},
            config_path=target)
    finally:
        if orig_config_file is not None:
            ed.CONFIG_FILE = orig_config_file

    assert ok is True
    assert real.read_bytes() == real_before, "真实配置被改写了 —— config_path 只隔离了读"
    assert json.loads(real.read_text(encoding="utf-8"))["study_plan"]["school"] == "目标院校"
    assert json.loads(target.read_text(encoding="utf-8"))["study_plan"]["school"] == "浙江大学"
