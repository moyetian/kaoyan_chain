# -*- coding: utf-8 -*-
"""# [Regress] review_gate --diff 端到端回归（临时 git 仓库，隔离运行）。

修复来源：2026-10-12《考研学习链-项目审查与升级改造方案》逐项核实批次。
本文件守护两个实测误报缺陷（修复前下列用例会红）：

  1. [Regress] Windows 反斜杠路径：旧实现把 ``str(Path)``（反斜杠）直接拼进
     ``git show <base>:<path>`` 必然失败 → old_lines=None → 所有 >500 行的存量
     文件被误判"文件由 None → N 行，越过 500 上限"的 ERROR（Windows 上 100%
     复现）。守护：修改存量文件只报 WARN，输出不得含"文件由 None"。
  2. [Regress] 存量函数触碰误报：旧实现把被触碰的存量巨函数直接按新增红线判
     ERROR（实测 39 条误报）。守护：存量函数增大只报 WARN（ratchet）。

本批（2026-10-12 OCR 代码审查 M2 修复）新增守护：
  3. [Regress] rename 误判：``git mv`` + 修改的文件不在 ``--diff-filter=A``
     （is_new=False），而 ``git show <base>:<新路径>`` 必然失败 →
     old_lines=None → 被误判"新增文件"按红线双误报（R3 文件 + R1 函数，
     实测探针复现）。守护：按 ``--name-status`` 的 R 行映射读旧路径基线，
     重命名文件走 ratchet（只报 WARN）。

端到端验证方式：在 pytest ``tmp_path`` 中新建临时 git 仓库（不触碰真实仓库），
以 ``monkeypatch.chdir`` 切换工作目录后直接调用 ``review_gate._run_diff``。
"""

import argparse
import subprocess
from pathlib import Path

import pytest

from tools import review_gate as rg


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                   capture_output=True, text=True)


def _run_diff(repo: Path, monkeypatch: pytest.MonkeyPatch,
              capsys: pytest.CaptureFixture, base: str = "HEAD") -> tuple[int, str]:
    monkeypatch.chdir(repo)
    rc = rg._run_diff(argparse.Namespace(diff=base, scan=False,
                                         roots=["tools", "tests"]))
    return rc, capsys.readouterr().out


@pytest.fixture()
def legacy_repo(tmp_path: Path) -> Path:
    """临时 git 仓库：tools/legacy_mod.py 约 600 行（内含 70 行存量函数）。"""
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "tests").mkdir()
    body = "\n".join(f"    v{i} = {i}" for i in range(68))
    src = ("def run_legacy():\n"
           '    """存量巨函数（约 71 行）。"""\n'
           f"{body}\n"
           "    return v67\n")
    filler = "\n".join(f"# filler {i}" for i in range(520))
    (repo / "tools" / "legacy_mod.py").write_text(
        src + "\n" + filler + "\n", encoding="utf-8")
    _git(repo, "init", "-q")
    # 邮箱按项目约定拆分书写：字面量邮箱会被导出脱敏改写（见
    # test_privacy_identity_rules.py 的 tests/ 免疫守卫），拼接后运行时等价。
    _git(repo, "config", "user.email", "t" + "@example.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def test_e2e_legacy_file_growing_warn_not_none_error(legacy_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
    """[Regress] 存量 >500 行文件 +1 行：报 WARN；不得出现 '文件由 None' 误报。"""
    f = legacy_repo / "tools" / "legacy_mod.py"
    f.write_text(f.read_text(encoding="utf-8") + "# probe\n", encoding="utf-8")
    rc, out = _run_diff(legacy_repo, monkeypatch, capsys)
    assert rc == 0, out
    assert "文件由 None" not in out
    assert "[WARN] R3" in out and "ERROR 0" in out


def test_e2e_legacy_func_growing_warn_not_error(legacy_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
    """[Regress] 存量巨函数 +1 行：ratchet 判 WARN，不得按新增红线报 ERROR。"""
    f = legacy_repo / "tools" / "legacy_mod.py"
    src = f.read_text(encoding="utf-8")
    src = src.replace("    return v67\n",
                      "    v67 += 0  # probe\n    return v67\n", 1)
    f.write_text(src, encoding="utf-8")
    rc, out = _run_diff(legacy_repo, monkeypatch, capsys)
    assert rc == 0, out
    assert "[WARN] R1" in out and "run_legacy" in out
    assert "ERROR 0" in out


def test_e2e_renamed_file_reads_old_baseline(legacy_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
    """[Regress] M2：git mv + 函数内修改，基线须按旧路径读取。

    旧实现实测双误报：R3 "新增文件 N 行 > 500"（新路径不在基线）+ R1
    存量函数按新增红线 "run_legacy() 长 71 行 > 60"（旧函数配不上对）。
    修复后两者均走 ratchet，只报 WARN。
    """
    _git(legacy_repo, "mv", "tools/legacy_mod.py", "tools/legacy_mod2.py")
    f = legacy_repo / "tools" / "legacy_mod2.py"
    src = f.read_text(encoding="utf-8")
    src = src.replace("    return v67\n",
                      "    v67 += 0  # probe\n    return v67\n", 1)
    f.write_text(src, encoding="utf-8")
    rc, out = _run_diff(legacy_repo, monkeypatch, capsys)
    assert rc == 0, out
    assert "[ERROR]" not in out and "ERROR 0" in out
    assert "[WARN] R1" in out and "run_legacy" in out
    assert "[WARN] R3" in out


def test_e2e_legacy_func_untouched_silent(legacy_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
    """改文件尾部填充行（不碰函数）：函数级零报。"""
    f = legacy_repo / "tools" / "legacy_mod.py"
    src = f.read_text(encoding="utf-8")
    f.write_text(src.replace("# filler 519", "# filler 519 (probe)"), encoding="utf-8")
    rc, out = _run_diff(legacy_repo, monkeypatch, capsys)
    assert rc == 0, out
    assert "run_legacy" not in out


def test_e2e_new_batch_named_test_reports_r4(legacy_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
    """新增日期戳命名测试文件 → R4 ERROR（命名 + 未分层，两条）。"""
    t = legacy_repo / "tests" / "test_fix_20261012_probe.py"
    t.write_text("def test_x():\n    assert True\n", encoding="utf-8")
    _git(legacy_repo, "add", "tests/test_fix_20261012_probe.py")
    rc, out = _run_diff(legacy_repo, monkeypatch, capsys)
    assert rc == 1, out
    assert out.count("[ERROR] R4") == 2


def test_e2e_new_long_function_reports_r1(legacy_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
    """新增 67 行函数 → R1 ERROR（新增函数按红线卡）。"""
    body = "\n".join(f"    w{i} = {i}" for i in range(65))
    (legacy_repo / "tools" / "new_mod.py").write_text(
        f'def probe_long():\n    """x."""\n{body}\n    return w64\n',
        encoding="utf-8")
    _git(legacy_repo, "add", "tools/new_mod.py")
    rc, out = _run_diff(legacy_repo, monkeypatch, capsys)
    assert rc == 1, out
    assert "[ERROR] R1" in out and "probe_long" in out


def test_e2e_invalid_ref_exit_2(legacy_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
    """无效基线引用：明确报错并 exit 2（不得静默跳过或误判全仓为新增）。"""
    rc, out = _run_diff(legacy_repo, monkeypatch, capsys, base="no-such-ref")
    assert rc == 2
    assert "无法解析" in out


def test_e2e_no_changes_skips(legacy_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
    """无变更：跳过并 exit 0。"""
    rc, out = _run_diff(legacy_repo, monkeypatch, capsys)
    assert rc == 0
    assert "未检测到" in out
