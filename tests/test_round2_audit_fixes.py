# -*- coding: utf-8 -*-
"""第二轮审查修复的窄回归：只钉住报告中可复现的断裂点。"""

import inspect

import pytest


def test_loopback_exception_is_narrow_and_redirect_aware():
    from tools import net_guard

    assert net_guard.assert_url_safe(
        "http://127.0.0.1:3000/", allow_loopback=True).startswith("http://127")
    with pytest.raises(net_guard.UnsafeURLError):
        net_guard.assert_url_safe("http://192.168.1.1/", allow_loopback=True)

    handler = net_guard.SafeRedirectHandler(allow_loopback=True)
    req = __import__("urllib.request", fromlist=["Request"]).Request(
        "http://127.0.0.1/")
    # 允许回环是 OneBot 的本地部署例外，不应让跳转到私网地址也获准。
    with pytest.raises(net_guard.UnsafeURLError):
        handler.redirect_request(req, None, 302, "Found", {},
                                 "http://10.0.0.1/private")


def test_external_http_callers_have_no_raw_urlopen_bypass():
    from pathlib import Path

    root = Path(__file__).parents[1]
    for rel in ("tools/skills/wechat_searcher.py", "tools/cli/notify.py"):
        source = (root / rel).read_text(encoding="utf-8")
        assert "urllib.request.urlopen(" not in source


def test_markdown_numbered_question_format_is_split_without_answer_pollution():
    from tools.skills.material_ingestion import MaterialIngestionPipeline

    text = """# 模拟卷
## 选择题
**第1题（4分）**：甲题干内容。
A. 选项甲
B. 选项乙

**第2题（4分）**：乙题干内容。
A. 选项甲
B. 选项乙

## 参考答案
1. A  2. B
"""
    pipeline = MaterialIngestionPipeline()
    pipeline._force_python = True
    chunks = pipeline.chunk_text(text, "formatted")
    assert [c.number for c in chunks] == [1, 2]
    assert [c.answer for c in chunks] == ["A", "B"]
    assert "乙题干" not in chunks[0].stem
    assert "参考答案" not in chunks[0].stem


def test_comparator_prefers_explicit_major_over_workspace_profile(tmp_path, monkeypatch):
    import json
    from tools.intelligence.comparator import SchoolComparator
    import tools.intelligence.comparator as comparator

    (tmp_path / "ky_config.json").write_text(json.dumps({
        "study_plan": {"pro_name": "824 电子信息专业基础综合"}
    }), encoding="utf-8")
    monkeypatch.setattr(comparator, "ROOT", tmp_path)
    info = {"majors": [], "region": "待核验", "protect": "未核验"}
    result = SchoolComparator()._analyze_differences(
        "甲校", info, "乙校", info, "312 心理学专业基础综合")
    assert "312 心理学专业基础综合" in result["recommendation"]
    assert "824 电子信息" not in result["recommendation"]


def test_publish_guard_rejects_invalid_identity_rules(tmp_path):
    import json
    from tools import update_dashboard

    (tmp_path / "ky_config.json").write_text(json.dumps({
        "study_plan": {"school": "目标院校", "major": "", "pro_name": ""}
    }), encoding="utf-8")
    with pytest.raises(RuntimeError, match="动态身份脱敏规则无效"):
        update_dashboard._assert_identity_rules_effective(tmp_path)


def test_vision_response_reader_uses_shared_limits():
    from tools.skills import vision_solver

    source = inspect.getsource(vision_solver._read_and_decompress)
    assert "MAX_HTTP_RESPONSE_BYTES" in source
    assert "decompress_limited" in source


def test_llm_error_text_redacts_credentials_but_keeps_diagnostic_fields():
    from tools.llm_client import _redact_error_text

    out = _redact_error_text(
        '{"api_key":"sk-secret-value", "error":"bad token", '
        '"authorization":"Bearer abcdefghijkl"}')
    assert "sk-secret-value" not in out
    assert "abcdefghijkl" not in out
    assert "api_key" in out and "error" in out


def test_run_command_rejects_path_program_with_allowed_basename(tmp_path):
    from tools.agent.permissions import PermissionManager
    from tools.agent.sandbox import Sandbox
    from tools.agent.tools_impl import ToolRegistry

    evil = tmp_path / "python.exe"
    evil.write_bytes(b"not a trusted interpreter")
    registry = ToolRegistry(
        Sandbox(tmp_path), PermissionManager(mode="auto", workspace_root=tmp_path))
    out = registry.execute_tool(
        "run_command", {"command": f'"{evil}" --version'}, interactive=False)
    assert "非受信路径" in out or "安全拦截" in out


def test_sandbox_protects_prompt_and_memory_sources_from_agent_writes(tmp_path):
    from tools.agent.sandbox import Sandbox, SecurityException

    sandbox = Sandbox(tmp_path)
    for rel in ("AGENTS.md", ".memory/experiences/poison.md"):
        with pytest.raises(SecurityException):
            sandbox.resolve_safe_path(rel, allow_create=True, read_only=False)
    # 人工/只读上下文仍可读取这些来源。
    assert sandbox.resolve_safe_path("AGENTS.md", read_only=True).name == "AGENTS.md"
