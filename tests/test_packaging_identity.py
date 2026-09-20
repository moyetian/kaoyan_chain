# -*- coding: utf-8 -*-
"""打包产物的**内容级身份脱敏**（R2-E4）。

背景（实测）：``dist/KaoyanStudyChain/`` 里有 40 处真实身份字面量散在 8 个文件里 ——
``AGENTS.md``、四科 ``AGENTS.md`` / ``00_*备考总规划.md`` / ``考试大纲.md``，
以及 ``05-考研看板/web/snapshot.py`` 这个**代码文件**。而构建全程打印「隐私断言通过」。

根因：``build_package`` 原有四道隐私防线（staging 过滤 / ``leak_reason`` 五类红线 /
``assert_private_dirs_clean`` / ``purge_junk_from_product``）**全部只看路径与文件名，
从不读文件内容** —— 路径与文件名都「干净」、内容却写着真名的文件可以 100% 通过。

本文件锁定新加的内容级脱敏与两道闸门，并配阴性对照（见 ``tests/README`` 的约定：
把修复注释掉，对应用例必须变红）。
"""
import json

import pytest

import tools.build_package as bp

SCHOOL = "目标院校"
MAJOR = "目标专业 (专业代码)"
PRO_NAME = "自命题专业课科目"
WEAKNESS = "待诊断薄弱点"

PLAN = {
    "school": SCHOOL,
    "major": MAJOR,
    "pro_name": PRO_NAME,
    "eng_weakness": WEAKNESS,
    "eng_baseline": "摸底水平",
    "math_baseline": "不考数学",
}

#: 身份与通用词同时出现，用来验证「该改的改、不该改的不改」。
AGENTS_BODY = (
    "# 总控协议\n"
    f"- 目标院校：`{SCHOOL}`\n"
    f"- 报考专业：`{MAJOR}`\n"
    f"- 英语薄弱点: `{WEAKNESS}`\n"
    "\n"
    "## 错因五分类\n"
    "概念漏洞 / 审题偏差 / 公式记错 / 计算失误 / 书写丢分\n"
)

#: 身份出现在**代码文件**里（真实泄漏清单里的 05-考研看板/web/snapshot.py）。
SNAPSHOT_PY = (
    "# -*- coding: utf-8 -*-\n"
    f'DEFAULT_SUBJECT = "{PRO_NAME}"\n'
    f'DEFAULT_SCHOOL = "{SCHOOL}"\n'
    "\n"
    "def subjects():\n"
    "    return [DEFAULT_SUBJECT, DEFAULT_SCHOOL]\n"
)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def product(tmp_path, monkeypatch):
    """造 tmp 源工作区 + tmp 产物目录，返回 ``(src, dst)``。

    手法与 ``tests/test_fix_packaging.py`` 的 ``packaged`` fixture 一致：
    monkeypatch ``bp.ROOT`` 后调 ``deploy_workspace_skeleton``。
    注意 ``bp.ROOT`` 被替换后，脱敏规则必须**按新 ROOT 现算** —— 这也是引擎
    下沉 privacy_policy（而不是 import sync_publish）的理由。
    """
    src = tmp_path / "src"
    dst = tmp_path / "dst"

    _write(src / "ky_config.json",
           json.dumps({"study_plan": PLAN}, ensure_ascii=False))
    _write(src / "AGENTS.md", AGENTS_BODY)
    _write(src / "05-考研看板" / "web" / "snapshot.py", SNAPSHOT_PY)
    _write(src / "05-考研看板" / "README.md", "看板说明\n")
    _write(src / "01-数学" / "参考资料" / "README.md", "SKELETON\n")
    for subj in ("02-英语", "03-思想政治理论", "04-专业课"):
        _write(src / subj / "参考资料" / "README.md", "SKELETON\n")

    monkeypatch.setattr(bp, "ROOT", src)
    bp.deploy_workspace_skeleton(dst)
    return src, dst


def test_product_identity_scrubbed(product):
    _src, dst = product
    text = (dst / "AGENTS.md").read_text(encoding="utf-8")
    assert SCHOOL not in text
    assert WEAKNESS not in text
    assert MAJOR not in text
    assert "目标院校" in text
    assert "待诊断薄弱点" in text


def test_generic_reason_words_are_preserved(product):
    """防过度脱敏：错因五分类是公开文档正文，必须原样保留。"""
    _src, dst = product
    text = (dst / "AGENTS.md").read_text(encoding="utf-8")
    for word in ("概念漏洞", "审题偏差", "公式记错", "计算失误", "书写丢分"):
        assert word in text, word


def test_code_file_content_scrubbed_and_still_compiles(product):
    """身份写在 .py 的字符串里也要脱敏，且脱敏后必须仍能编译。"""
    _src, dst = product
    f = dst / "05-考研看板" / "web" / "snapshot.py"
    text = f.read_text(encoding="utf-8")
    assert SCHOOL not in text
    assert PRO_NAME not in text
    compile(text, str(f), "exec")  # 语法闸门的本地复核


def test_internal_copy_is_scrubbed_too(tmp_path, monkeypatch):
    """`--add-data` 会在产物根的 _internal/ 下再放一份，两处都要处理。"""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _write(src / "ky_config.json",
           json.dumps({"study_plan": PLAN}, ensure_ascii=False))
    _write(src / "AGENTS.md", AGENTS_BODY)
    _write(src / "01-数学" / "参考资料" / "README.md", "SKELETON\n")
    # 预置一份 _internal/ 副本（deploy 不会清产物根，故能存活到脱敏阶段）
    _write(dst / "_internal" / "AGENTS.md", AGENTS_BODY)

    monkeypatch.setattr(bp, "ROOT", src)
    bp.deploy_workspace_skeleton(dst)
    text = (dst / "_internal" / "AGENTS.md").read_text(encoding="utf-8")
    assert SCHOOL not in text and WEAKNESS not in text
    assert "目标院校" in text


def test_third_party_py_is_untouched(tmp_path, monkeypatch):
    """`_internal/` 下住着 PySide6 / PIL 等第三方包，脱敏**不得**去改它们的源码。

    注意这里直接调 ``sanitize_product()`` 而不是 ``deploy_workspace_skeleton()``：
    后者的残留自检会扫全树，若第三方文件里真出现了当前身份字面量，它会 fail-closed
    中止构建 —— 那是另一条独立防线（「不改第三方代码，但也不发布带身份的包」），
    本用例只锁定「脱敏的作用域」。
    """
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _write(src / "ky_config.json",
           json.dumps({"study_plan": PLAN}, ensure_ascii=False))
    _write(src / "AGENTS.md", AGENTS_BODY)
    third_party = (
        f'# 第三方库文件（模拟）\nBANNER = "University table: {SCHOOL} -> 10466"\n'
    )
    _write(dst / "_internal" / "PySide6" / "sample.py", third_party)
    _write(dst / "AGENTS.md", AGENTS_BODY)

    monkeypatch.setattr(bp, "ROOT", src)
    changed = bp.sanitize_product(dst, skip_rels=("tools/intelligence/registry.py",))

    # 自有文件被改写；第三方文件原样不动。
    assert (dst / "AGENTS.md").read_text(encoding="utf-8") != AGENTS_BODY
    assert (dst / "_internal" / "PySide6" / "sample.py").read_text(
        encoding="utf-8") == third_party
    assert all("PySide6" not in p.as_posix() for p in changed)


def test_keep_identity_escape_hatch(tmp_path, monkeypatch):
    """给自己打「带个人方案」的包时，--keep-identity 跳过脱敏。"""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _write(src / "ky_config.json",
           json.dumps({"study_plan": PLAN}, ensure_ascii=False))
    _write(src / "AGENTS.md", AGENTS_BODY)
    _write(src / "01-数学" / "参考资料" / "README.md", "SKELETON\n")

    monkeypatch.setattr(bp, "ROOT", src)
    bp.deploy_workspace_skeleton(dst, keep_identity=True)
    text = (dst / "AGENTS.md").read_text(encoding="utf-8")
    assert SCHOOL in text and WEAKNESS in text


def test_residual_gate_raises_when_sanitize_is_noop(tmp_path, monkeypatch):
    """阴性闸门：脱敏没生效时，残留自检必须**中止构建**，而不是静默产出。

    这里把 sanitize_product 打成 no-op 来模拟「规则失效 / 脱敏被绕过」。
    """
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _write(src / "ky_config.json",
           json.dumps({"study_plan": PLAN}, ensure_ascii=False))
    _write(src / "AGENTS.md", AGENTS_BODY)
    _write(src / "01-数学" / "参考资料" / "README.md", "SKELETON\n")

    monkeypatch.setattr(bp, "ROOT", src)
    monkeypatch.setattr(bp, "sanitize_product", lambda *a, **k: [])
    with pytest.raises(RuntimeError, match="含当前真实身份"):
        bp.deploy_workspace_skeleton(dst)


def test_no_config_does_not_abort(tmp_path, monkeypatch):
    """贡献者 / CI 没有 ky_config.json 时不该被闸门拦下（无身份可脱敏）。"""
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setattr(bp, "ROOT", src)
    bp._assert_identity_rules_effective(keep_identity=False)  # 不抛异常即通过


def test_effective_gate_aborts_when_config_is_placeholder(tmp_path, monkeypatch):
    """配置存在但 study_plan 是占位值 → 脱敏会静默失效 → 必须中止。"""
    src = tmp_path / "src"
    src.mkdir()
    _write(src / "ky_config.json",
           json.dumps({"study_plan": {"school": "目标院校"}}, ensure_ascii=False))
    monkeypatch.setattr(bp, "ROOT", src)
    with pytest.raises(RuntimeError, match="脱敏规则无效"):
        bp._assert_identity_rules_effective(keep_identity=False)


def test_keep_identity_bypasses_gate(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    _write(src / "ky_config.json",
           json.dumps({"study_plan": {"school": "目标院校"}}, ensure_ascii=False))
    monkeypatch.setattr(bp, "ROOT", src)
    bp._assert_identity_rules_effective(keep_identity=True)  # 不抛异常


def test_pyinstaller_cmd_uses_specpath(tmp_path):
    """`--specpath` 必须存在：否则 PyInstaller 会把仓库里那份手写 spec 覆盖掉。"""
    args = bp._pyinstaller_base_args(tmp_path / "dist", tmp_path / "build")
    specpath = [a for a in args if a.startswith("--specpath=")]
    assert specpath == [f"--specpath={tmp_path / 'build'}"]


def test_sanitize_product_skips_public_index(tmp_path, monkeypatch):
    """公开高校映射表（registry.py）整份跳过 —— 脱敏会把校名解析改坏。"""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _write(src / "ky_config.json",
           json.dumps({"study_plan": PLAN}, ensure_ascii=False))
    _write(src / "AGENTS.md", AGENTS_BODY)
    registry_body = f'"985": ("{SCHOOL}", "10466")\n'
    _write(dst / "tools" / "intelligence" / "registry.py", registry_body)

    monkeypatch.setattr(bp, "ROOT", src)
    bp.sanitize_product(dst, skip_rels=("tools/intelligence/registry.py",))
    assert (dst / "tools" / "intelligence" / "registry.py").read_text(
        encoding="utf-8") == registry_body
