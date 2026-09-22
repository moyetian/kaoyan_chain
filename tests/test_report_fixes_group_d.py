# -*- coding: utf-8 -*-
"""审查报告 D 组消缺回归（编号 G8 / B-02）。

覆盖三处缺陷：

* **D1 / D2（G8）** ``tools/update_dashboard.py`` 的 git 提交与推送闸门：
    - ``git commit`` 此前**不带 pathspec** —— 用户此前手动 ``git add`` 过的文件
      会被一并提交；
    - ``git push`` 此前**无条件执行** —— 即使 commit 失败（无增量）也照样推送。
* **D3（G8）** ``.github/workflows/deploy-pages.yml`` 此前只校验
  ``state_snapshot.json``，完全不检查 ``docs/index.html``，页面缺失或未构建
  也会被直接部署。
* **D4（B-02）** 院校库口径不一：README / CONTRIBUTING 曾写「55+」，与实测真值
  （57 所详细档案 + 1,841 所基础名录）不符。

测试数据一律使用**中性占位**（不含任何真实院校名 / 人名 / 自命题科目代码）。
D1 / D2 通过打桩 ``subprocess.run`` 完成，**绝不真的执行任何 git 命令**。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import update_dashboard as ud  # noqa: E402


# ───────────────────────── D1 / D2：提交与推送闸门 ─────────────────────────

#: 与脚本内白名单保持同源（用于断言 pathspec 不越界）
_DOC_WHITELIST = (
    "docs/index.html",
    "docs/live.html",
    "docs/assets/",
    "docs/state_snapshot.json",
)


class _FakeCompleted:
    """最小可用的 ``subprocess.run`` 返回值替身。"""

    def __init__(self, returncode):
        self.returncode = returncode


def _prepare_repo(tmp_path, monkeypatch, present_docs):
    """造一个只含看板构建脚本与指定白名单产物的假仓库根。

    同时把 ``sys.argv`` 设为带 ``--push``，否则 ``main()`` 会在
    ``--push not in sys.argv`` 分支直接 return，测不到提交/推送路径。
    """
    repo = tmp_path / "repo"
    (repo / "05-考研看板").mkdir(parents=True)
    (repo / "05-考研看板" / "build.py").write_text("# stub\n", encoding="utf-8")
    for rel in present_docs:
        p = repo / rel
        if rel.endswith("/"):
            p.mkdir(parents=True, exist_ok=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x", encoding="utf-8")

    monkeypatch.setattr(ud, "ROOT", repo)
    monkeypatch.setattr(sys, "argv", ["update_dashboard.py", "--push"])
    return repo


def _install_fake_subprocess(monkeypatch, commit_rc=0):
    """打桩 ``ud.subprocess.run``：记录调用，绝不真执行 git。

    ``commit`` 子命令返回 ``commit_rc``，其余（build / add / push）返回 0。
    """
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append([str(c) for c in cmd])
        rc = commit_rc if "commit" in cmd else 0
        return _FakeCompleted(rc)

    monkeypatch.setattr(ud.subprocess, "run", fake_run)
    return calls


def test_commit_carries_whitelist_pathspec(tmp_path, monkeypatch):
    """commit 必须带 ``--`` 与白名单 pathspec，不得全量提交工作区。"""
    _prepare_repo(tmp_path, monkeypatch,
                  ["docs/index.html", "docs/state_snapshot.json"])
    calls = _install_fake_subprocess(monkeypatch, commit_rc=0)

    ud.main()

    commit_calls = [c for c in calls if "commit" in c]
    assert len(commit_calls) == 1, f"commit 调用次数异常: {calls}"
    cmd = commit_calls[0]
    assert "--" in cmd, f"commit 缺少 pathspec 分隔符 `--`: {cmd}"
    pathspec = cmd[cmd.index("--") + 1:]
    assert pathspec, "pathspec 为空，等价于全量提交"
    assert "docs/index.html" in pathspec, pathspec
    # 只允许白名单内的 docs/ 产物，绝不携带工作区其它文件
    assert set(pathspec) <= set(_DOC_WHITELIST), pathspec
    assert all(p.startswith("docs/") for p in pathspec), pathspec


def test_commit_failure_skips_push(tmp_path, monkeypatch):
    """commit 失败（无增量）时**不得**推送。"""
    _prepare_repo(tmp_path, monkeypatch, ["docs/index.html"])
    calls = _install_fake_subprocess(monkeypatch, commit_rc=1)

    ud.main()

    assert any("commit" in c for c in calls), f"未走到提交: {calls}"
    assert not any("push" in c for c in calls), \
        f"commit 失败却仍执行了 push: {calls}"


def test_commit_success_triggers_push(tmp_path, monkeypatch):
    """commit 成功时才推送（阴性对照：证明上面的「不推送」不是整体空转）。"""
    _prepare_repo(tmp_path, monkeypatch, ["docs/index.html"])
    calls = _install_fake_subprocess(monkeypatch, commit_rc=0)

    ud.main()

    assert any("push" in c for c in calls), f"commit 成功却未推送: {calls}"


def test_no_whitelist_artifacts_skips_git_entirely(tmp_path, monkeypatch):
    """无任何白名单产物时，不得构造 ``git commit -m <msg> --`` 这种畸形命令。"""
    _prepare_repo(tmp_path, monkeypatch, [])  # 一个白名单产物都没有
    calls = _install_fake_subprocess(monkeypatch, commit_rc=0)

    ud.main()

    assert not any("commit" in c for c in calls), f"构造了畸形 commit: {calls}"
    assert not any("push" in c for c in calls), calls
    assert not any("add" in c for c in calls), calls


# ─────────────────────── D3：部署门禁必须校验页面产物 ───────────────────────

WORKFLOW = ROOT / ".github" / "workflows" / "deploy-pages.yml"
_STEP_BEGIN = "- name: Verify docs/ contains built artifacts"
_STEP_END = "- name: Setup Pages"


def test_workflow_validates_index_html_in_same_step():
    """页面产物必须与快照同处一个校验步骤，且至少两条断言。"""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "state_snapshot.json" in text, "工作流未校验快照"

    start = text.index(_STEP_BEGIN)
    end = text.index(_STEP_END)
    step = text[start:end]

    assert "docs/index.html" in step, "校验步骤未检查 docs/index.html"
    assert "state_snapshot.json" in step, "快照与页面校验必须同处一个步骤"

    index_lines = [ln for ln in step.splitlines() if "docs/index.html" in ln]
    assert len(index_lines) >= 2, \
        f"对 docs/index.html 的校验不足（存在性 + 内容）：{index_lines}"
    assert any(("grep" in ln or "assert" in ln or "test -s" in ln)
               for ln in index_lines), \
        f"docs/index.html 只做了存在性检查，未校验内容: {index_lines}"
    # 看板契约标记必须被真正校验（否则空壳页面也能过门禁）
    assert 'data-p="stat"' in step, "未校验看板契约标记 data-p=stat"


# ──────────────────── D4：院校库口径必须与实测真值一致 ────────────────────


def test_university_counts_are_accurate_in_docs():
    """全仓文档不得再出现「55+」，须为 57 / 1,841 的准确口径。

    实测真值：``data/universities/registry.json`` = 57 所详细档案；
    ``national_institutions.json`` = 1,841 所基础名录；20 个分省 YAML 合计 57 所。
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")

    for name, text in (("README.md", readme), ("CONTRIBUTING.md", contributing)):
        assert "55+" not in text, f"{name} 仍含过时口径 55+"

    assert "1,841 所基础名录" in readme, "README 未给出 1,841 所基础名录口径"
    assert "1,841 所基础名录" in contributing, "CONTRIBUTING 未给出 1,841 所基础名录口径"
    assert "57 所" in readme, "README 未给出 57 所详细档案口径"
    assert "57 所" in contributing, "CONTRIBUTING 未给出 57 所详细档案口径"

    # 同一句「55+」的其它副本（手册 / 看板子 README / README 配图源脚本）
    others = [
        ROOT / "操作手册.md",
        ROOT / "05-考研看板" / "README.md",
        ROOT / "tools" / "build_svg_assets.py",
    ]
    for path in others:
        text = path.read_text(encoding="utf-8")
        assert "55+" not in text, f"{path.relative_to(ROOT)} 仍含过时口径 55+"

    # 配图生成物必须与源脚本同源（改了 .py 就必须重跑脚本，否则两边漂移）
    svg = (ROOT / "docs" / "assets" / "intelligence_architecture.svg").read_text(encoding="utf-8")
    assert "55+" not in svg, "README 配图 SVG 未随源脚本重新生成"
    assert "57 所重点高校注册表" in svg, "README 配图 SVG 未更新为 57 所口径"
