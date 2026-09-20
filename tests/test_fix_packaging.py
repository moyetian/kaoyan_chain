# -*- coding: utf-8 -*-
"""
P1 回归/阴性测试：发布包骨架部署不得夹带用户私有资料
====================================================
缺陷背景（多角色端到端测试审查报告 P1）：
    tools/build_package.py 的 deploy_workspace_skeleton() 通过 ignore_patterns
    过滤文件，但该过滤器**不含** 参考资料 / 每日笔记 / 错题本 / 每日作业 /
    错题与长难句本 / 作文语料库 / _状态 这 7 个用户私有目录，导致真实私有资料
    （如 27腿姐刷题计划-*.pdf）被原样复制进 dist 发布包。

    更隐蔽的是，若目标目录已存在（Windows 文件占用等），
    shutil.rmtree(..., ignore_errors=True) 会静默失败，旧私有残留与新一轮
    copytree(dirs_exist_ok=True) 叠加，私有数据反而「越修越多」。

本测试在 tmp 目录中构造：
    * 源目录：含私有 PDF/错题/笔记 + 必须随包分发的骨架模板
    * 目标目录：预置上一轮遗留的旧私有文件（模拟静默残留）

断言：(a) 产物中私有目录不含任何用户资料；(b) 旧残留被清理；
      (c) 骨架模板（*.template.md / _模板.md / _索引.md / README.md /
          通用作文语料.md / *核心速记*.md）未被误伤；(d) 私有目录本身仍存在。
"""

from pathlib import Path

import json

import pytest

import tools.build_package as bp

#: 7 个用户私有目录（与 .gitignore 隐私铁律一致）
PRIVATE_DIRS = [
    "参考资料",
    "每日笔记",
    "错题本",
    "每日作业",
    "错题与长难句本",
    "作文语料库",
    "_状态",
]


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def packaged(tmp_path, monkeypatch):
    """构造源工作区 + 预置旧残留的目标目录，执行一次骨架部署。

    返回 (source_root, target_root)。
    """
    src = tmp_path / "src"
    dst = tmp_path / "dst"

    # ---------- 源目录：私有资料（必须被过滤） ----------
    _write(src / "01-数学" / "参考资料" / "李林880题.pdf", "PRIVATE-PDF")
    _write(src / "01-数学" / "每日笔记" / "2026-09-19_每日笔记.md", "PRIVATE")
    _write(src / "01-数学" / "错题本" / "2026-09-19_错题.md", "PRIVATE")
    _write(src / "01-数学" / "_状态" / "今日任务.md", "PRIVATE")
    _write(src / "01-数学" / "_状态" / "01-数学-核心概念.md", "PRIVATE")

    _write(src / "02-英语" / "作文语料库" / "我的私藏语料.md", "PRIVATE")
    _write(src / "02-英语" / "错题与长难句本" / "2026-09-19_长难句.md", "PRIVATE")
    _write(src / "02-英语" / "_状态" / "今日任务.md", "PRIVATE")

    _write(src / "03-思想政治理论" / "参考资料" / "27腿姐刷题计划-解析_1.pdf", "PRIVATE-PDF")
    _write(src / "03-思想政治理论" / "参考资料" / "27腿姐刷题计划-试题_1.pdf", "PRIVATE-PDF")
    _write(src / "03-思想政治理论" / "错题本" / "2026-09-19_帽子题.md", "PRIVATE")
    _write(src / "03-思想政治理论" / "_状态" / "今日任务.md", "PRIVATE")

    _write(src / "04-专业课" / "每日作业" / "2026-09-19_作业.md", "PRIVATE")
    _write(src / "04-专业课" / "_状态" / "今日任务.md", "PRIVATE")

    # ---------- 源目录：骨架模板（必须保留） ----------
    _write(src / "01-数学" / "参考资料" / "README.md", "SKELETON")
    _write(src / "01-数学" / "每日笔记" / "_模板.md", "SKELETON")
    _write(src / "01-数学" / "错题本" / "_模板.md", "SKELETON")
    _write(src / "01-数学" / "错题本" / "_索引.md", "SKELETON")
    _write(src / "01-数学" / "_状态" / "今日任务.template.md", "SKELETON")
    _write(src / "01-数学" / "_状态" / "学员档案.template.md", "SKELETON")

    _write(src / "02-英语" / "作文语料库" / "通用作文语料.md", "SKELETON")
    _write(src / "02-英语" / "错题与长难句本" / "_模板.md", "SKELETON")
    _write(src / "02-英语" / "错题与长难句本" / "_索引.md", "SKELETON")
    _write(src / "02-英语" / "_状态" / "今日任务.template.md", "SKELETON")

    _write(src / "03-思想政治理论" / "参考资料" / "README.md", "SKELETON")
    _write(src / "03-思想政治理论" / "_状态" / "核心速记_帽子词与历史节点.md", "SKELETON")
    _write(src / "03-思想政治理论" / "_状态" / "核心速记_帽子词与历史节点.template.md", "SKELETON")
    _write(src / "03-思想政治理论" / "_状态" / "今日任务.template.md", "SKELETON")

    _write(src / "04-专业课" / "每日作业" / "_模板.md", "SKELETON")
    _write(src / "04-专业课" / "错题本" / "_索引.md", "SKELETON")

    # ---------- 目标目录：上一轮遗留的旧私有残留（必须被清掉） ----------
    _write(dst / "01-数学" / "参考资料" / "旧私有教材.pdf", "STALE")
    _write(dst / "03-思想政治理论" / "参考资料" / "旧版腿姐_试题.pdf", "STALE")
    _write(dst / "03-思想政治理论" / "_状态" / "今日任务.md", "STALE")
    _write(dst / "02-英语" / "作文语料库" / "旧私藏语料.md", "STALE")
    _write(dst / "04-专业课" / "每日作业" / "旧作业.md", "STALE")

    monkeypatch.setattr(bp, "ROOT", src)
    bp.deploy_workspace_skeleton(dst)
    return src, dst


def test_private_user_files_are_not_packaged(packaged):
    """(a) 源目录中的用户私有资料不得进入发布包"""
    _, dst = packaged
    leaked = [
        dst / "01-数学" / "参考资料" / "李林880题.pdf",
        dst / "01-数学" / "每日笔记" / "2026-09-19_每日笔记.md",
        dst / "01-数学" / "错题本" / "2026-09-19_错题.md",
        dst / "01-数学" / "_状态" / "今日任务.md",
        dst / "01-数学" / "_状态" / "01-数学-核心概念.md",
        dst / "02-英语" / "作文语料库" / "我的私藏语料.md",
        dst / "02-英语" / "错题与长难句本" / "2026-09-19_长难句.md",
        dst / "02-英语" / "_状态" / "今日任务.md",
        dst / "03-思想政治理论" / "参考资料" / "27腿姐刷题计划-解析_1.pdf",
        dst / "03-思想政治理论" / "参考资料" / "27腿姐刷题计划-试题_1.pdf",
        dst / "03-思想政治理论" / "错题本" / "2026-09-19_帽子题.md",
        dst / "03-思想政治理论" / "_状态" / "今日任务.md",
        dst / "04-专业课" / "每日作业" / "2026-09-19_作业.md",
        dst / "04-专业课" / "_状态" / "今日任务.md",
    ]
    present = [str(p.relative_to(dst)) for p in leaked if p.exists()]
    assert not present, f"发布包夹带了用户私有资料: {present}"


def test_stale_private_residue_is_purged(packaged):
    """(b) 目标目录预置的旧私有残留必须被清理（不得静默叠加）"""
    _, dst = packaged
    stale = [
        dst / "01-数学" / "参考资料" / "旧私有教材.pdf",
        dst / "03-思想政治理论" / "参考资料" / "旧版腿姐_试题.pdf",
        dst / "03-思想政治理论" / "_状态" / "今日任务.md",
        dst / "02-英语" / "作文语料库" / "旧私藏语料.md",
        dst / "04-专业课" / "每日作业" / "旧作业.md",
    ]
    left = [str(p.relative_to(dst)) for p in stale if p.exists()]
    assert not left, f"旧私有残留未被清理: {left}"


def test_skeleton_templates_are_preserved(packaged):
    """(c) 必须随包分发的骨架模板不得被误伤"""
    _, dst = packaged
    required = [
        dst / "01-数学" / "参考资料" / "README.md",
        dst / "01-数学" / "每日笔记" / "_模板.md",
        dst / "01-数学" / "错题本" / "_模板.md",
        dst / "01-数学" / "错题本" / "_索引.md",
        dst / "01-数学" / "_状态" / "今日任务.template.md",
        dst / "01-数学" / "_状态" / "学员档案.template.md",
        dst / "02-英语" / "作文语料库" / "通用作文语料.md",
        dst / "02-英语" / "错题与长难句本" / "_模板.md",
        dst / "02-英语" / "错题与长难句本" / "_索引.md",
        dst / "02-英语" / "_状态" / "今日任务.template.md",
        dst / "03-思想政治理论" / "参考资料" / "README.md",
        dst / "03-思想政治理论" / "_状态" / "核心速记_帽子词与历史节点.md",
        dst / "03-思想政治理论" / "_状态" / "核心速记_帽子词与历史节点.template.md",
        dst / "03-思想政治理论" / "_状态" / "今日任务.template.md",
        dst / "04-专业课" / "每日作业" / "_模板.md",
        dst / "04-专业课" / "错题本" / "_索引.md",
    ]
    missing = [str(p.relative_to(dst)) for p in required if not p.exists()]
    assert not missing, f"骨架模板被误删: {missing}"


def test_private_dirs_still_exist(packaged):
    """(d) 私有目录本身必须仍被重建（供学员写入）"""
    _, dst = packaged
    for subj in ["01-数学", "02-英语", "03-思想政治理论", "04-专业课"]:
        for sub in PRIVATE_DIRS:
            p = dst / subj / sub
            if not p.exists():
                continue  # 该科目本就不含此目录（如 01-数学 无「每日作业」）
            assert p.is_dir(), f"私有目录未重建: {p}"


def test_assert_guard_rejects_leaked_private_content(packaged):
    """(e) 产物断言必须能拦住「私有目录里混入用户资料」"""
    _, dst = packaged
    guard = getattr(bp, "assert_private_dirs_clean", None)
    assert guard is not None, "缺少打包后产物断言 assert_private_dirs_clean()"

    # 正常产物：断言应通过
    guard(dst)

    # 人为注入一份私有资料，断言必须报错
    leak = dst / "03-思想政治理论" / "参考资料" / "偷偷夹带.pdf"
    _write(leak, "LEAK")
    with pytest.raises(RuntimeError):
        guard(dst)


# ══════════════════════════════════════════════════════════════════════════
# R2-C1 回归：PyInstaller 的 _internal/ 副本必须同样受隐私门禁保护
# ══════════════════════════════════════════════════════════════════════════
# 缺陷背景（第二轮复测 R2-C1）：
#   collect_data_specs() 曾把 01-数学~05-考研看板 整棵目录直接交给 --add-data，
#   PyInstaller 会原样复制到 dist/<app>/_internal/<科目>/（冻结后 sys._MEIPASS）。
#   这条路径完全绕过 deploy_workspace_skeleton() 的隐私过滤；而
#   assert_private_dirs_clean() 只扫 target_dir/<科目>/<私有目录>，看不见
#   _internal/，于是「教材 PDF / 错题本 / _状态 真实学情 / *_backup_*」照发，
#   断言却打印 [√]。下面三个用例分别锁死「断源头」「堵断言」「不误伤第三方」。


def test_internal_subtree_is_guarded(tmp_path):
    """_internal/ 下的私有资料必须被断言拦下（R2-C1 核心回归）"""
    guard = bp.assert_private_dirs_clean
    product = tmp_path / "KaoyanStudyChain"

    # 顶层骨架合法
    _write(product / "04-专业课" / "参考资料" / "README.md", "SKELETON")
    guard(product)  # 不应抛错

    leaked = [
        product / "_internal" / "04-专业课" / "参考资料" / "私有资料_假.pdf",
        product / "_internal" / "01-数学" / "_状态" / "今日任务.md",
        product / "_internal" / "03-思想政治理论" / "错题本" / "2026-09-19_帽子题.md",
        product / "_internal" / "01-数学" / "_状态" / "今日任务_backup_2026-09-15_110243.md",
        product / "_internal" / "04-专业课" / "目标院校情报_目标院校_报考专业_backup_2026-09-19_113713.md",
        product / "_internal" / "ky_config.json",
    ]
    for p in leaked:
        _write(p, "LEAK")

    with pytest.raises(RuntimeError) as exc:
        guard(product)
    msg = str(exc.value)
    assert "_internal/04-专业课/参考资料/私有资料_假.pdf" in msg
    assert "_internal/01-数学/_状态/今日任务.md" in msg
    assert "_internal/ky_config.json" in msg


def test_internal_third_party_is_not_false_positive(tmp_path):
    """_internal/ 下的第三方包/缓存/静态资源不得被误判为泄漏（防误伤）"""
    product = tmp_path / "KaoyanStudyChain"
    benign = [
        # PyInstaller 自带的 Qt 运行时
        product / "_internal" / "PySide6" / "Qt6Core.dll",
        product / "_internal" / "PySide6" / "plugins" / "platforms" / "qwindows.dll",
        # 第三方 vendor 内部的 dist/ 结构（.gitignore C7 教训）
        product / "_internal" / "docs" / "assets" / "vendor" / "katex" / "0.16.9" / "dist" / "katex.min.js",
        product / "_internal" / "docs" / "assets" / "vendor" / "katex" / "0.16.9" / "fonts" / "KaTeX_Main.woff2",
        # 冻结程序真正需要的静态资源
        product / "_internal" / "data" / "universities" / "national_institutions.json",
        product / "_internal" / "tools" / "theme" / "templates" / "theme.html",
        # 缓存产物（不是用户资料）
        product / "_internal" / "tools" / "__pycache__" / "ky_io.cpython-312.pyc",
        # 骨架白名单文件在 _internal 里也应放行
        product / "_internal" / "01-数学" / "参考资料" / "README.md",
        product / "_internal" / "01-数学" / "_状态" / "今日任务.template.md",
        product / "_internal" / "03-思想政治理论" / "_状态" / "核心速记_帽子词与历史节点.md",
    ]
    for p in benign:
        _write(p, "OK")

    bp.assert_private_dirs_clean(product)  # 不应抛错


def test_collect_data_specs_filters_into_staging(tmp_path, monkeypatch):
    """--add-data 的源必须是「已过滤的 staging」，私有资料不得进入（断源头）"""
    src = tmp_path / "repo"
    staging = tmp_path / "staging"

    _write(src / "01-数学" / "参考资料" / "李林880题.pdf", "PRIVATE-PDF")
    _write(src / "01-数学" / "_状态" / "今日任务.md", "PRIVATE")
    _write(src / "01-数学" / "_状态" / "今日任务_backup_2026-09-15_110243.md", "PRIVATE")
    _write(src / "01-数学" / "参考资料" / "README.md", "SKELETON")
    _write(src / "01-数学" / "_状态" / "今日任务.template.md", "SKELETON")
    _write(src / "04-专业课" / "错题本" / "2026-09-19_帽子题.md", "PRIVATE")
    _write(src / "04-专业课" / "错题本" / "_索引.md", "SKELETON")
    _write(src / "data" / "universities" / "national_institutions.json", "DB")
    _write(src / "tools" / "theme" / "templates" / "theme.html", "TPL")
    _write(src / "tools" / "__pycache__" / "ky_io.cpython-312.pyc", "CACHE")
    # [R2-C1 补漏] CLI 粘贴图片的运行时落盘 = 学员真实屏幕内容，必须进不了包
    _write(src / "tools" / "scratch" / "uploads" / "clip_1789375408728.png", "SCREENSHOT")
    _write(src / "ky_config.json", "SECRET")

    monkeypatch.setattr(bp, "ROOT", src)
    staging.mkdir()
    datas = bp.collect_data_specs(staging)

    # 所有 --add-data 的源都必须落在 staging 内，不得再指向仓库目录
    sep = ";" if __import__("sys").platform == "win32" else ":"
    for d in datas:
        origin = d.split(sep)[0]
        assert not origin.startswith(str(src)), f"--add-data 仍直指仓库目录: {d}"
        assert origin.startswith(str(staging)), f"--add-data 源不在 staging: {d}"

    # staging 中私有资料与个人配置必须消失，骨架与静态资源必须保留
    assert not (staging / "01-数学" / "参考资料" / "李林880题.pdf").exists()
    assert not (staging / "01-数学" / "_状态" / "今日任务.md").exists()
    assert not (staging / "01-数学" / "_状态" / "今日任务_backup_2026-09-15_110243.md").exists()
    assert not (staging / "04-专业课" / "错题本" / "2026-09-19_帽子题.md").exists()
    assert not (staging / "tools" / "__pycache__").exists()
    assert not (staging / "tools" / "scratch").exists(), \
        "tools/scratch（CLI 粘贴截图落盘）进了 staging"
    assert (staging / "01-数学" / "参考资料" / "README.md").exists()
    assert (staging / "01-数学" / "_状态" / "今日任务.template.md").exists()
    assert (staging / "04-专业课" / "错题本" / "_索引.md").exists()
    assert (staging / "data" / "universities" / "national_institutions.json").exists()
    assert (staging / "tools" / "theme" / "templates" / "theme.html").exists()


def test_build_cleans_staging_on_exit(tmp_path, monkeypatch):
    """staging 不得落在仓库里，且构建结束必须清理（含提前 return 分支）"""
    created = []

    real_mkdtemp = bp.tempfile.mkdtemp

    def spy_mkdtemp(*a, **kw):
        p = real_mkdtemp(*a, **kw)
        created.append(p)
        return p

    monkeypatch.setattr(bp.tempfile, "mkdtemp", spy_mkdtemp)
    # 入口不存在 → 走提前 return 分支，staging 仍必须被清理
    monkeypatch.setattr(bp, "ROOT", tmp_path / "nonexistent_repo")

    rc = bp.build(dry_run=True)
    assert rc == 1
    assert created, "build() 未创建 staging"
    for p in created:
        assert not Path(p).exists(), f"staging 未被清理: {p}"
        assert str(tmp_path) not in p  # 不落在仓库/测试目录内


# ══════════════════════════════════════════════════════════════════════════
# R2-D5 回归：真实身份文件名（既不在私有目录内、也不是备份/个人配置）
# ══════════════════════════════════════════════════════════════════════════
# 缺陷背景（R2-D5 实锤）：
#   dist/<app>/_internal/04-专业课/
#     目标院校情报_<真实校名>_<专业代码 专业名>_backup_<时间戳>.md
#   文件名里直接写着学员真实校名与专业。它既不在私有目录内、也不是个人配置，
#   于是「扫目录」「扫内容」两道闸门都看不见 —— R2-C1 修好之后它照样进 _internal/。
#   本组用例锁死三层：staging 剔除、骨架部署剔除、产物断言第 5 类红线。

#: 自造身份配置（不依赖会被其它角色改写的本机 ky_config.json）
IDENTITY_PLAN = {
    "school": "示例农业大学",
    "major": "030500 示例理论",
    "pro_name": "618 示例科目甲 823 示例科目乙",
}

IDENTITY_NAMES = (
    "目标院校情报_示例农业大学_030500 示例理论.md",
    "双校对标_示例农业大学_VS_对比院校B_示例理论.md",
    "双校考情对比_示例农业大学_VS_对比院校B.md",
    "2027考纲_示例农业大学_自命题.md",
)


@pytest.fixture()
def identity_repo(tmp_path, monkeypatch):
    """构造一个「真实身份写在文件名里」的迷你仓库。"""
    src = tmp_path / "repo"
    subj = src / "04-专业课"
    subj.mkdir(parents=True)
    for name in IDENTITY_NAMES:
        _write(subj / name, "IDENTITY")
    # 对照组：正常文件必须保留
    _write(subj / "2027考纲_814信号与系统.md", "OK")
    _write(subj / "AGENTS.md", "OK")
    # 公开高校库：校名在这里是功能数据，绝不能被当身份剔除
    _write(src / "data" / "universities" / "河南" / "示例农业大学.yaml", "PUBLIC-DB")

    (src / "ky_config.json").write_text(
        json.dumps({"study_plan": IDENTITY_PLAN}, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(bp, "ROOT", src)
    bp._identity_tokens_cached.cache_clear()
    try:
        yield src
    finally:
        bp._identity_tokens_cached.cache_clear()


def test_staging_excludes_identity_named_files(identity_repo, tmp_path):
    """staging（→ PyInstaller 的 _internal/）不得收录身份文件名。

    这是「通用规则 + 打包特有规则」两层过滤的核心：只套 should_publish()
    时，这 4 个文件全部会进 staging。
    """
    staging = tmp_path / "staging"
    staging.mkdir()
    bp.collect_data_specs(staging)

    left = [str(p.relative_to(staging)) for p in staging.rglob("*")
            if p.is_file() and any(n in p.name for n in IDENTITY_NAMES)]
    assert not left, f"身份文件名进入了 staging: {left}"

    # 对照组：正常文件与公开高校库必须保留
    assert (staging / "04-专业课" / "2027考纲_814信号与系统.md").exists()
    assert (staging / "04-专业课" / "AGENTS.md").exists()
    assert (staging / "data" / "universities" / "河南" / "示例农业大学.yaml").exists(), \
        "公开高校库被误当身份剔除（校名在 data/ 下是功能数据）"


def test_deploy_skeleton_excludes_identity_named_files(identity_repo, tmp_path):
    """顶层骨架部署同样不得留下身份文件名。"""
    dst = tmp_path / "dst"
    bp.deploy_workspace_skeleton(dst)

    left = [str(p.relative_to(dst)) for p in dst.rglob("*")
            if p.is_file() and any(n in p.name for n in IDENTITY_NAMES)]
    assert not left, f"身份文件名进入了发布包: {left}"
    assert (dst / "04-专业课" / "2027考纲_814信号与系统.md").exists()
    assert (dst / "data" / "universities" / "河南" / "示例农业大学.yaml").exists()


def test_leak_reason_flags_identity_filename():
    """第 5 类红线：文件名夹带当前真实身份即判泄漏。"""
    tokens = ("示例农业大学", "030500 示例理论")
    reason = bp.leak_reason(("_internal", "04-专业课"),
                            "目标院校情报_示例农业大学_030500 示例理论.md", tokens)
    assert reason and "真实身份文件名" in reason, reason

    # 不传 tokens 时保持旧行为（向后兼容）
    assert bp.leak_reason(("04-专业课", "AGENTS.md"), "AGENTS.md") is None


def test_assert_guard_rejects_identity_named_file_in_internal(identity_repo, tmp_path):
    """产物断言必须能拦下 _internal/ 里的身份文件名（第 5 类红线）。"""
    product = tmp_path / "KaoyanStudyChain"
    _write(product / "04-专业课" / "AGENTS.md", "OK")
    bp.assert_private_dirs_clean(product)  # 干净产物不应抛错

    leak = product / "_internal" / "04-专业课" / IDENTITY_NAMES[0]
    _write(leak, "LEAK")
    with pytest.raises(RuntimeError) as exc:
        bp.assert_private_dirs_clean(product)
    assert "真实身份文件名" in str(exc.value)
    assert IDENTITY_NAMES[0] in str(exc.value)


def test_identity_tokens_exclude_generic_discipline_words(tmp_path, monkeypatch):
    """身份标记必须精准：不得把「示例理论」这类学科通用词单独当身份。"""
    src = tmp_path / "repo"
    src.mkdir()
    (src / "ky_config.json").write_text(
        json.dumps({"study_plan": IDENTITY_PLAN}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(bp, "ROOT", src)
    bp._identity_tokens_cached.cache_clear()
    try:
        tokens = bp._current_identity_tokens()
        assert "示例农业大学" in tokens
        assert "030500 示例理论" in tokens
        # 只取「代码 + 名称」组合，绝不单列裸学科词
        assert "示例理论" not in tokens
        assert "示例科目甲" not in tokens
    finally:
        bp._identity_tokens_cached.cache_clear()


# ══════════════════════════════════════════════════════════════════════════
# R2-C1 补漏：--collect-all tools 是第二条绕过 staging 的复制路径
# ══════════════════════════════════════════════════════════════════════════
# 实测：build() 的 PyInstaller 参数含 `--collect-all tools`，它把整个 tools 包
# 再原样收集到 _internal/tools/，不经过 collect_data_specs() 的 staging 过滤。
# 结果 tools/scratch/uploads/ 下 199 张学员剪贴板截图进了发布包，而
# assert_private_dirs_clean() 看不见（scratch 不是私有目录/备份/配置，
# 文件名里也没有身份词），断言照常打印 [√]。


def test_purge_junk_from_product_removes_scaffold(tmp_path):
    """产物清理必须删掉脚手架/缓存目录，且不误伤正常文件。"""
    product = tmp_path / "KaoyanStudyChain"
    _write(product / "_internal" / "tools" / "scratch" / "uploads" / "clip_1.png", "SHOT")
    _write(product / "_internal" / "tools" / "scratch" / "_report.md", "SHOT")
    _write(product / "_internal" / "tools" / "__pycache__" / "ky_io.cpython-312.pyc", "CACHE")
    _write(product / "_internal" / "tools" / "ky_cli.py", "OK")
    _write(product / "_internal" / "tools" / "scratchpad" / "keep.py", "OK")  # 名字相近但非 junk

    removed = bp.purge_junk_from_product(product)
    assert removed >= 2
    assert not (product / "_internal" / "tools" / "scratch").exists()
    assert not (product / "_internal" / "tools" / "__pycache__").exists()
    assert (product / "_internal" / "tools" / "ky_cli.py").exists()
    assert (product / "_internal" / "tools" / "scratchpad" / "keep.py").exists()


def test_deploy_skeleton_purges_collected_scaffold(tmp_path, monkeypatch):
    """骨架部署阶段必须清掉 --collect-all 塞进 _internal/ 的 scratch（覆盖调用点）。"""
    src = tmp_path / "repo"
    (src / "01-数学").mkdir(parents=True)
    monkeypatch.setattr(bp, "ROOT", src)
    bp._identity_tokens_cached.cache_clear()

    dst = tmp_path / "dst"
    _write(dst / "_internal" / "tools" / "scratch" / "uploads" / "clip_1.png", "SHOT")
    _write(dst / "_internal" / "tools" / "ky_cli.py", "OK")

    bp.deploy_workspace_skeleton(dst)

    assert not (dst / "_internal" / "tools" / "scratch").exists(), \
        "tools/scratch（学员剪贴板截图）随 --collect-all 进了发布包且未被清理"
    assert (dst / "_internal" / "tools" / "ky_cli.py").exists()

