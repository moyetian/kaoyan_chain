# -*- coding: utf-8 -*-
"""2026-10-10 R11 隐私/发布域修复回归测试（E#1 + E#2 + sk- 凭证形态）。

**E#1（critical）** ``tools/update_dashboard.py._scan_publish_residuals`` 以
``docs/`` 为根全树扫描，把未跟踪的 ``docs/.local/``（本地完整模式看板落点，
.gitignore 已忽略、不进 Pages、含真实学情）计入残留 → ``--push`` 必然误阻断；
且 ``--push`` 的 finally 分支必重建 .local → 下一次 --push 必被拦死。
修复：返回处过滤 ``.local/`` 前缀条目；docstring 同步修正「git 全量跟踪 =
Pages 公开面」的错误假设。

**E#2（high）** ``should_publish()`` 的 ``ROOT_ONLY_EXCLUDE_DIRS`` 判定只在根
层级，而 ``build_package`` 没有 ``sync_publish`` 那样的任意深度兜底 —— 嵌套的
``.config_backup``（明文 api_key 快照）/ ``.checkpoint``（本机绝对路径）/
``.sim_logs`` / ``.sim_tools`` 可静默进分发包。修复：``DEV_SCRATCH_DIRS``
下沉到任意层级（``BUILD_ARTIFACT_DIRS`` 保持根层级，防误伤
``docs/assets/vendor/katex/<ver>/dist/``）；``build_package.leak_reason`` 与
``deploy_workspace_skeleton`` 同步同口径。

**附加** 通用 PII 规则表新增 ``sk-`` 凭证形态（≥20 位字母数字）。

测试夹具身份一律中性占位（「示例农业大学」），与 test_security_audit_publish_perf
同口径；sk- 样本用字符串拼接构造（避免本文件自身被导出脱敏改写）。
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_package as bp  # noqa: E402
import privacy_policy as pp  # noqa: E402
import update_dashboard as ud  # noqa: E402


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_repo(tmp_path: Path) -> Path:
    """含自造身份配置的假仓库根（身份为中性占位，非真实值）。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "ky_config.json").write_text(json.dumps({
        "onboarding_completed": True,
        "study_plan": {"school": "示例农业大学", "major": "030500 示例理论",
                       "pro_name": "618 示例科目甲 823 示例科目乙"},
    }, ensure_ascii=False), encoding="utf-8")
    return repo


# ═══════════ E#1：--push 残留自检不得把 docs/.local 计入公开面 ═══════════

def test_scan_publish_residuals_ignores_docs_local(tmp_path):
    """docs/.local（本地完整模式产物）不计入残留 —— --push 不得被必然误阻断。"""
    repo = _make_repo(tmp_path)
    _write(repo / "docs" / "index.html", "<html>公开说明</html>")
    _write(repo / "docs" / ".local" / "index.html",
           "<html>示例农业大学 完整学情</html>")
    _write(repo / "docs" / ".local" / "state_snapshot.json",
           json.dumps({"data": "示例农业大学"}, ensure_ascii=False))

    assert ud._scan_publish_residuals(repo) == [], \
        "docs/.local（已忽略、不进 Pages）被计入残留，--push 会被必然误阻断"


def test_scan_publish_residuals_still_flags_public_leak(tmp_path):
    """阴性对照：docs/ 公开面里的身份残留仍必须被返回（闸门不失效）。"""
    repo = _make_repo(tmp_path)
    _write(repo / "docs" / "index.html", "<html>示例农业大学真题精讲</html>")
    _write(repo / "docs" / ".local" / "index.html",
           "<html>示例农业大学 完整学情</html>")

    assert ud._scan_publish_residuals(repo) == ["index.html"], \
        "公开面真实泄漏被放过（闸门失效）或 .local 过滤未生效"


def test_scan_publish_residuals_filter_is_prefix_scoped(tmp_path):
    """阴性对照：过滤只认 ``.local/`` 前缀，不误伤名字含 local 的公开文件。"""
    repo = _make_repo(tmp_path)
    _write(repo / "docs" / "foo.local.md", "示例农业大学 说明")
    _write(repo / "docs" / "local.html", "示例农业大学 说明")

    assert ud._scan_publish_residuals(repo) == ["foo.local.md", "local.html"]


# ═══════════ E#2：嵌套 DEV_SCRATCH 目录（build_package 出口无兜底） ═══════════

@pytest.mark.parametrize("rel", [
    "04-专业课/.config_backup/ky_config.auto.x.json",
    "tools/.checkpoint/ckpt_1/manifest.json",
    "tools/.sim_logs/x.txt",
    "05-考研看板/.sim_tools/SIM_PROTOCOL.md",
    "01-数学/.config_backup/ky_config.current.json",
])
def test_should_publish_rejects_nested_dev_scratch(rel):
    """嵌套层级的开发脚手架目录必须被剔除（build_package 出口没有兜底）。"""
    assert pp.should_publish(rel) is False, f"嵌套开发脚手架会被发布: {rel}"


def test_should_publish_negative_controls():
    """阴性对照：BUILD_ARTIFACT 保持根层级（第三方 dist/ 不误伤）；根级行为不变。"""
    assert pp.should_publish(
        "docs/assets/vendor/katex/0.16.9/dist/katex.min.js") is True
    assert pp.should_publish("docs/assets/vendor/katex/0.16/dist/x.js") is True
    assert pp.should_publish("dist/x.js") is False
    assert pp.should_publish("build/out.txt") is False
    assert pp.should_publish("tools/ky_cli.py") is True


def test_leak_reason_flags_nested_dev_scratch():
    """build_package 断言出口同口径：嵌套 DEV_SCRATCH 命中即判泄漏。"""
    assert bp.leak_reason(("04-专业课", ".config_backup"),
                          "ky_config.auto.x.json") is not None
    assert bp.leak_reason(("01-数学", ".checkpoint", "ckpt_1"),
                          "manifest.json") is not None
    assert bp.leak_reason(("tools", ".sim_logs"), "x.txt") is not None


def test_leak_reason_keeps_third_party_dist_clean():
    """阴性对照：BUILD_ARTIFACT 不下沉 —— 第三方包内部 dist/ 不误报。"""
    assert bp.leak_reason(("docs", "assets", "vendor", "katex", "0.16", "dist"),
                          "x.js") is None
    assert bp.leak_reason(("04-专业课",), "考试大纲.md") is None


def test_deploy_skeleton_drops_nested_dev_scratch(tmp_path, monkeypatch):
    """骨架部署与 should_publish 同口径：嵌套 .config_backup 不得进发布包。"""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _write(src / "04-专业课" / ".config_backup" / "ky_config.auto.x.json", "{}")
    _write(src / "04-专业课" / "考试大纲.md", "示例正文")

    monkeypatch.setattr(bp, "ROOT", src)
    bp.deploy_workspace_skeleton(dst)

    assert not (dst / "04-专业课" / ".config_backup").exists(), \
        "嵌套 .config_backup（明文密钥快照）随发布包出门"
    assert (dst / "04-专业课" / "考试大纲.md").exists(), "正常文件被误剔除"


# ═══════════ 附加：sk- 凭证形态进入通用 PII 规则表 ═══════════

def test_sk_credential_redacted_and_scanned():
    """sk- 凭证（≥20 位）被 PII 规则脱敏，且残留自检同源命中。"""
    fake_key = "sk-" + "x" * 40  # 拼接构造：避免本文件自身被导出脱敏改写
    out = pp.sanitize_text("api_key: " + fake_key, pp.PII_SUBSTITUTIONS)
    assert fake_key not in out
    assert "[API密钥]" in out
    assert any(p.search(fake_key) for p in pp.PII_RESIDUAL_PATTERNS)


def test_sk_credential_rules_do_not_overreach():
    """阴性对照：短形态与占位 key 不被改写（防过度脱敏）。"""
    for raw in ("sk-SHORT", "sk-placeholder", "sk-test-fake",
                "sk-" + "x" * 10, "sk-abc123"):
        assert pp.sanitize_text(raw, pp.PII_SUBSTITUTIONS) == raw, raw


# ═══════════ N9：sk- 分段前缀（sk-proj- 等）不得绕过脱敏 ═══════════

def test_sk_proj_segmented_prefix_redacted():
    """sk-proj- 分段前缀（OpenAI 形态）必须被识别，不得绕过脱敏。"""
    fake_key = "sk-proj-" + "a" * 24  # 拼接构造：避免本文件自身被导出脱敏改写
    out = pp.sanitize_text("api_key: " + fake_key, pp.PII_SUBSTITUTIONS)
    assert fake_key not in out, "sk-proj- 分段前缀绕过脱敏"
    assert "[API密钥]" in out
    assert any(p.search(fake_key) for p in pp.PII_RESIDUAL_PATTERNS)


def test_sk_proj_short_forms_still_not_overreach():
    """阴性对照：sk-proj- 后不足 20 位的短形态仍不命中（阈值语义不变）。"""
    for raw in ("sk-proj-SHORT", "sk-proj-" + "a" * 10, "sk-proj-"):
        assert pp.sanitize_text(raw, pp.PII_SUBSTITUTIONS) == raw, raw


# ═══════════ R11 补充：.git 元数据不参与公开面，自检须跳过 ═══════════

def test_scan_residual_identity_skips_git_metadata(tmp_path):
    """副本 ``.git/`` 是本地版本控制元数据（不参与 push 的文件树）。

    PRIV-C2 把导出后自检改为**阻断**后，其 config/logs 里的提交身份邮箱
    （本地占位邮箱，已随每个 commit 公开）会被 PII 规则命中 →
    每次 ``--force`` 导出必然失败（实测 4 命中全在 .git 内）。扫描须跳过
    .git 段（任意层级，与 git 自身排除口径一致）。
    """
    (tmp_path / ".git" / "logs").mkdir(parents=True)
    # 邮箱/手机号一律拼接构造：本文件自身必须对导出脱敏免疫（元测试钉住）
    _mail = "29652" + "@" + "kaoyan.local"
    (tmp_path / ".git" / "config").write_text(
        "[user]\n\temail = " + _mail + "\n", encoding="utf-8")
    (tmp_path / ".git" / "logs" / "HEAD").write_text(
        "0000 1111 MoyeTian <" + _mail + "> 1 +0800\tcommit: init\n",
        encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "index.html").write_text("<html>干净</html>", encoding="utf-8")
    hits = pp.scan_residual_identity(tmp_path, tmp_path, include_pii=True)
    assert hits == [], f".git 元数据被误报为残留: {hits}"


def test_scan_residual_identity_git_skip_does_not_weaken_public_face(tmp_path):
    """阴性对照：.git 之外的同形态残留仍被扫出；``.gitignore``（非 .git 段）不误跳过。"""
    _mail = "a" + "@" + "kaoyan.local"
    _phone = "138" + "0013" + "8000"
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text(
        "email = " + _mail + "\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("联系: " + _phone + "\n", encoding="utf-8")
    (tmp_path / "leak.txt").write_text("手机 " + _phone, encoding="utf-8")
    hits = pp.scan_residual_identity(tmp_path, tmp_path, include_pii=True)
    assert ".gitignore" in hits
    assert "leak.txt" in hits
    assert not any(h.startswith(".git/") for h in hits)
