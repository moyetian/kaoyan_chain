# -*- coding: utf-8 -*-
"""发布前隐私审查回归测试：``.checkpoint/`` 必须进入导出排除名单。

**缺陷（W12 推送前全量隐私审查实测）**

``.checkpoint/`` 是 D0 写前快照目录（``tools/agent/permissions.py`` 的
``checkpoint_dir``，Agent 工具写盘前自动留档）。每个 ``ckpt_*`` 子目录含
``manifest.json`` / ``_meta.json`` 与**被改文件的内容副本**（按原相对路径
落盘，如 ``ckpt_x/04-专业课/学情档案.md``）；两个 json 内嵌
``C:\\Users\\<用户名>\\...`` 的**本机绝对路径**（实测本机 350 个 json 中 348
条记录含该形态），而 ``SANITIZED_SUFFIXES`` 不含 ``.json`` —— 内容脱敏对
它们完全不生效。它此前只被 ``.gitignore`` 忽略（git 层），而导出副本
（``tools/sync_publish.py``）走的是**文件系统遍历**，只认
``dir_should_exclude`` / ``file_should_exclude`` —— 于是整棵被镜像进公开
副本（dry-run 实测 copy 清单首屏全是 ``.checkpoint\\ckpt_*\\...``）。
修复前实测：

    pp.should_publish('.checkpoint/ckpt_x/manifest.json')   -> True
    sp.dir_should_exclude(('.checkpoint',), '.checkpoint')  -> False

修复：``.checkpoint`` 进入 ``privacy_policy.DEV_SCRATCH_DIRS``（单一事实源，
自动进 ``ROOT_ONLY_EXCLUDE_DIRS``）；``sync_publish.EXCLUDE_DIRS`` 另加一层
任意深度的纵深防御（理由见该处注释）。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import privacy_policy as pp  # noqa: E402
import sync_publish as sp  # noqa: E402

#: 快照目录下的真实成员形态（实测：两个元数据 json + 按原相对路径落盘的
#: 内容副本）。用户名一律用中性占位，不得写本机真实值。
SNAPSHOT_FILES = (
    "ckpt_20260926_174531/manifest.json",
    "ckpt_20260926_174531/_meta.json",
    "ckpt_20260926_174531/04-专业课/notes.md",
    "ckpt_20260926_174531/_状态/今日任务.md",
)


# ─────────────── 第 1 层：策略单一事实源 ───────────────
def test_checkpoint_in_privacy_policy_source_of_truth():
    """``.checkpoint`` 必须由 privacy_policy（单一事实源）收录并进 ROOT_ONLY。"""
    assert ".checkpoint" in pp.DEV_SCRATCH_DIRS, \
        "D0 快照目录未进入 privacy_policy.DEV_SCRATCH_DIRS（单一事实源）"
    assert ".checkpoint" in pp.ROOT_ONLY_EXCLUDE_DIRS, \
        "DEV_SCRATCH_DIRS 未汇入 ROOT_ONLY_EXCLUDE_DIRS，排除判定不会生效"


@pytest.mark.parametrize("name", SNAPSHOT_FILES)
def test_should_publish_rejects_checkpoint(name):
    """``should_publish`` 对 ``.checkpoint/**`` 必须返回 False。"""
    assert pp.should_publish(f".checkpoint/{name}") is False, \
        f"快照文件会被发布: .checkpoint/{name}"
    assert pp.should_publish((".checkpoint",) + tuple(name.split("/"))) is False


def test_should_publish_is_root_only_by_design():
    """语义边界：``should_publish`` 的 ROOT_ONLY 判定**只在根层级**生效。

    与 ``.config_backup`` 同款设计（同一份 ``ROOT_ONLY_EXCLUDE_DIRS`` 里还有
    ``dist``，下沉任意深度会误伤 ``docs/assets/vendor/katex/<ver>/dist/`` 这类
    第三方包内部结构）。``.checkpoint`` 的**嵌套**兜底由
    ``sync_publish.EXCLUDE_DIRS``（任意深度）承担，见下一条测试。
    """
    assert pp.should_publish(".checkpoint/ckpt_x/manifest.json") is False
    assert pp.should_publish("01-数学/.checkpoint/ckpt_x/manifest.json") is True, \
        "ROOT_ONLY 判定被下沉到任意深度，会误伤第三方包内部的 dist/ 结构"


def test_nested_checkpoint_caught_by_export_gate():
    """嵌套层级的 ``.checkpoint`` 由 ``dir_should_exclude``（任意深度）兜住。"""
    assert sp.dir_should_exclude(("01-数学", ".checkpoint"), ".checkpoint") is True
    assert ".checkpoint" in sp.EXCLUDE_DIRS, \
        "EXCLUDE_DIRS 未收录 .checkpoint，嵌套层级将失去纵深防御"


# ─────────────── 第 2 层：sync_publish 目录/文件闸门 ───────────────
def test_dir_gate_excludes_checkpoint():
    """``dir_should_exclude`` 对 ``.checkpoint`` 必须返回 True（根级与嵌套）。"""
    assert sp.dir_should_exclude((".checkpoint",), ".checkpoint") is True
    assert sp.dir_should_exclude(("01-数学", ".checkpoint"), ".checkpoint") is True


@pytest.mark.parametrize("name", SNAPSHOT_FILES)
def test_file_gate_excludes_checkpoint(name):
    """文件级闸门同样不得放行快照目录内的任何文件。"""
    parts = (".checkpoint",) + tuple(name.split("/"))
    assert sp.file_should_exclude(parts, parts[-1]) is True, \
        f"文件级闸门放行快照文件: {name}"


# ─────────────── 第 3 层：端到端真实导出遍历 ───────────────
def test_real_export_traversal_drops_checkpoint(tmp_path, monkeypatch):
    """跑**真实导出遍历**：产物里不得有 ``.checkpoint``，正常文件必须在。"""
    src = tmp_path / "repo"
    ckpt = src / ".checkpoint" / "ckpt_20260926_174531"
    ckpt.mkdir(parents=True)
    # manifest 形态与实测一致：内嵌本机绝对路径（占位用户名，非真实值）
    (ckpt / "manifest.json").write_text(
        '{\n  "checkpoint": "ckpt_20260926_174531",\n'
        '  "entries": [{"relative_file": "x.md", '
        '"original_file": "C:\\\\Users\\\\example\\\\repo\\\\x.md"}]\n}\n',
        encoding="utf-8")
    (ckpt / "_meta.json").write_text(
        '{"timestamp": "20260926_174531", "relative_file": "x.md"}\n',
        encoding="utf-8")
    # 内容副本：按原相对路径落盘（快照的核心泄漏面）
    (ckpt / "04-专业课").mkdir()
    (ckpt / "04-专业课" / "notes.md").write_text("示例正文\n", encoding="utf-8")

    # 正常文件：必须继续被复制（防止修复过头把整棵导出关掉）
    (src / "README.md").write_text("# 正常文件\n", encoding="utf-8")
    (src / "tools").mkdir()
    (src / "tools" / "ky_cli.py").write_text("print('ok')\n", encoding="utf-8")

    dst = tmp_path / "dst"
    monkeypatch.setattr(sp, "SRC", src)
    monkeypatch.setattr(sp, "DST", dst)
    monkeypatch.setattr(sp, "DRY_RUN", False)

    sp.python_mirror()  # 真实的 os.scandir 递归遍历 + 闸门判定

    assert not (dst / ".checkpoint").exists(), \
        "导出产物里出现了 .checkpoint（写前快照随公开副本出门）"
    leaked = [p.relative_to(dst).as_posix() for p in dst.rglob("*")
              if p.is_file() and p.name in ("manifest.json", "_meta.json")]
    assert leaked == [], f"快照元数据泄漏到产物: {leaked}"

    assert (dst / "README.md").exists(), "正常文件被误剔除，修复过头"
    assert (dst / "tools" / "ky_cli.py").exists(), "正常源码被误剔除"


def test_real_export_traversal_is_not_vacuous(tmp_path, monkeypatch):
    """反证：同一棵目录树里，**未被排除**的目录确实会被复制 —— 上面的断言不是空转。"""
    src = tmp_path / "repo"
    keep = src / "04-专业课"
    keep.mkdir(parents=True)
    (keep / "note.md").write_text("示例内容\n", encoding="utf-8")

    dst = tmp_path / "dst"
    monkeypatch.setattr(sp, "SRC", src)
    monkeypatch.setattr(sp, "DST", dst)
    monkeypatch.setattr(sp, "DRY_RUN", False)

    sp.python_mirror()
    assert (dst / "04-专业课" / "note.md").exists(), \
        "导出遍历整体失效，端到端断言无法证伪"
