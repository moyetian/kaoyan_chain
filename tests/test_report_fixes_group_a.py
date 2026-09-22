# -*- coding: utf-8 -*-
"""A 组审查消缺回归测试（编号 G2 / G10 / G5 / S4）。

每条用例都针对一处**已核验成立**的缺陷，且能在修复被回退时变红：

  * G2  —— ``MCPProcessClient.start()`` 未回收旧 reader 线程（改为先 ``stop()``）；
  * G10 —— ``_note_lock_error`` 兜底只判 ``assert_writable``，漏判 ``NoteLockedError``；
  * G5  —— ``comparator`` / ``scout_engine`` 在 ``except`` 体内 ``import``，
          改为模块顶部统一双路径导入 ``PermissionDeniedError``；
  * S4  —— ``agentic_research`` 在线分支 ``chsi_code`` 可能被设为 ``None``。

测试数据一律使用中性占位，不含任何真实身份信息。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestG2StartReclaimsOldReader:
    def test_start_delegates_to_stop(self, monkeypatch):
        """start() 必须先回收旧进程/reader 线程（调用 stop），再拉起新进程。"""
        from tools.agent.mcp_client import MCPProcessClient

        client = MCPProcessClient("s", "definitely-not-a-real-cmd-xyz", [])
        called = []
        monkeypatch.setattr(client, "stop", lambda: called.append(True))

        # 命令不存在 -> Popen 抛 FileNotFoundError -> start() 优雅降级返回 False
        assert client.start() is False
        assert called, "start() 必须先调用 stop() 回收旧 reader 线程/进程"


class TestG10NoteLockGuardFallback:
    def test_missing_note_locked_error_class_short_circuits(self, monkeypatch):
        """assert_writable 存在但 NoteLockedError 缺失时，应直接放行而非崩溃。

        旧实现只判 assert_writable，靠前置早退绕过 ``except NoteLockedError``；
        一旦两者状态不一致，``except None`` 会抛 TypeError。
        """
        from tools.agent import tools_impl as ti

        def _boom(_path):
            raise RuntimeError("模拟写入被拒")

        monkeypatch.setattr(ti, "assert_writable", _boom)
        monkeypatch.setattr(ti, "NoteLockedError", None)

        assert ti._note_lock_error(Path("示例笔记.md")) is None


class TestG5ModuleLevelKyIoImport:
    def test_comparator_exposes_permission_denied_error(self):
        """PermissionDeniedError 应在模块顶部导入，而非藏在 except 体内。"""
        from tools.intelligence import comparator

        assert issubclass(comparator.PermissionDeniedError, Exception)

    def test_scout_engine_exposes_permission_denied_error(self):
        from tools.intelligence import scout_engine

        assert issubclass(scout_engine.PermissionDeniedError, Exception)


class TestS4ChsiCodeCoercion:
    def test_null_chsi_code_falls_back_to_code(self, monkeypatch, tmp_path):
        """LLM 显式返回 ``"chsi_code": null`` 时应回落 ``code``，不得留 None。"""
        from tools.intelligence.agentic_research import AgenticResearchEngine

        engine = AgenticResearchEngine(workspace_root=tmp_path)
        raw = (
            "研究完成：\n"
            "```json\n"
            '{"name": "示例测试院校", "code": "12345", "chsi_code": null, '
            '"majors": ["示例科目"], "region": "示例地区"}\n'
            "```\n"
        )
        monkeypatch.setattr(engine, "execute_loop", lambda *a, **k: raw)

        profile = engine.research_university_profile(
            "示例测试院校",
            "示例专业",
            api_config={"api_key": "test-fake-key-12345678"},
        )
        assert profile.get("chsi_code") == "12345"

    def test_empty_string_chsi_code_falls_back_to_code(self, monkeypatch, tmp_path):
        """chsi_code 为空串（falsy）时同样回落 ``code``（or 链口径）。"""
        from tools.intelligence.agentic_research import AgenticResearchEngine

        engine = AgenticResearchEngine(workspace_root=tmp_path)
        raw = (
            "```json\n"
            '{"name": "示例测试院校", "code": "12345", "chsi_code": "", '
            '"majors": ["示例科目"]}\n'
            "```\n"
        )
        monkeypatch.setattr(engine, "execute_loop", lambda *a, **k: raw)

        profile = engine.research_university_profile(
            "示例测试院校",
            "示例专业",
            api_config={"api_key": "test-fake-key-12345678"},
        )
        assert profile.get("chsi_code") == "12345"
