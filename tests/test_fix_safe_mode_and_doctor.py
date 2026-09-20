# -*- coding: utf-8 -*-
"""R2-D1 / R2-D2 回归测试（第二轮交叉验证报告）。

R2-D1【中·safe 模式仍静默写 ky_config.json】
    ``--permission=safe`` 声称「严格只读」，但会话结束时 SessionEnd 钩子会调用
    ``study_planner.record_daily_completion()`` 写 ``completion_history``。
    根因不是「忘了判断 mode」，而是**双导入**：``ky_io`` 与 ``tools.ky_io`` 是两个
    独立模块对象、各持一份 ``_READ_ONLY_MODE``；``tools/cli/dispatch.py:189`` 用
    ``from tools import ky_io`` 设标志，而 ``study_planner`` 历史上
    ``from ky_io import atomic_write_text`` 取的是另一个别名 —— 于是写盘闸门形同虚设。
    修复：``record_daily_completion`` 内部显式过一遍 ``ky_io.guard_write``
    （只读判定仍以 ky_io 全局标志为单一事实源），hooks 侧把异常翻译成可读中文提示，
    不再 ``except Exception: pass`` 静默吞掉。

R2-D2【低·doctor 模型探活假告警】
    探活请求体用 ``max_tokens=1``，被上游直接拒绝（kuaipao.ai 回 HTTP 400
    ``max_tokens must be greater than 2``），于是「探活参数不合法」被误报成
    「模型/路由不兼容，建议更换模型」。修复：``max_tokens`` 提到 16，并把上游
    ``error.message`` 透传到诊断结论里；状态码分类语义保持不变。
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ───────────────────────── 公共工具 ─────────────────────────

def _ky_io_modules():
    """返回已加载的 ky_io 别名（双导入下可能有两个模块对象）。"""
    import importlib
    mods = []
    for name in ("tools.ky_io", "ky_io"):
        try:
            mods.append(importlib.import_module(name))
        except ImportError:
            continue
    return mods


def _study_planner_modules():
    """返回已加载的 study_planner 别名（hooks 里是 ``import study_planner``）。"""
    import importlib
    mods = []
    for name in ("study_planner", "tools.study_planner"):
        try:
            mods.append(importlib.import_module(name))
        except ImportError:
            continue
    return mods


@pytest.fixture
def read_only(monkeypatch):
    """模拟 ``tools/cli/dispatch.py`` 的 safe 模式：只给 tools.ky_io 置只读标志。

    这正是 R2-D1 的现场 —— 只置一个别名，另一个别名（study_planner 用的那个）
    必须仍能被拦住。
    """
    mods = _ky_io_modules()
    assert mods, "ky_io 未加载"
    target = next((m for m in mods if m.__name__ == "tools.ky_io"), mods[0])
    monkeypatch.setattr(target, "_READ_ONLY_MODE", True, raising=False)
    yield target
    monkeypatch.setattr(target, "_READ_ONLY_MODE", False, raising=False)


def _write_cfg(path: Path) -> bytes:
    payload = {
        "api_key": "sk-test-fake", "model": "mimo-v2.5",
        "base_url": "https://api.example.test/v1",
        "active_subject": "pro",
        "study_plan": {"school": "目标院校", "total_hours": 6.5},
        "webhooks": {"dingtalk": ""},
        "completion_history": {},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path.read_bytes()


# ───────────────────────── R2-D1 ─────────────────────────

def test_record_daily_completion_refuses_when_read_only(tmp_path, monkeypatch, read_only):
    """safe 模式下必须拒绝写盘，且抛的是可识别的 PermissionDeniedError。"""
    cfg = tmp_path / "ky_config.json"
    before = _write_cfg(cfg)
    for sp in _study_planner_modules():
        monkeypatch.setattr(sp, "ROOT", tmp_path, raising=False)

    with pytest.raises(Exception) as ei:
        _study_planner_modules()[0].record_daily_completion(rate=0.0, total=3, completed=0)

    assert type(ei.value).__name__ == "PermissionDeniedError", (
        f"只读模式下应被写权限闸门拒绝，实际: {type(ei.value).__name__}: {ei.value}")
    assert cfg.read_bytes() == before, "只读模式下 ky_config.json 必须逐字节不变"


def test_record_daily_completion_still_writes_by_default(tmp_path, monkeypatch):
    """反向对照：默认（非只读）模式下 completion_history 必须照常写入。"""
    cfg = tmp_path / "ky_config.json"
    before = _write_cfg(cfg)
    for sp in _study_planner_modules():
        monkeypatch.setattr(sp, "ROOT", tmp_path, raising=False)

    _study_planner_modules()[0].record_daily_completion(
        rate=66.7, total=3, completed=2, date_str="2026-09-19")

    assert cfg.read_bytes() != before, "默认模式下应写入 completion_history"
    after = json.loads(cfg.read_text(encoding="utf-8"))
    assert after["completion_history"]["2026-09-19"]["completed"] == 2
    assert after["study_plan"]["school"] == "目标院校", "写入不得丢键"


def test_session_end_hook_notice_when_read_only(tmp_path, monkeypatch, read_only, capsys):
    """safe 模式会话结束：不得静默 —— 必须给出可读中文提示，且配置不变。"""
    cfg = tmp_path / "ky_config.json"
    before = _write_cfg(cfg)
    for sp in _study_planner_modules():
        monkeypatch.setattr(sp, "ROOT", tmp_path, raising=False)

    from tools.agent.hooks import HookManager
    hm = HookManager(workspace_root=tmp_path)
    hm.trigger_session_end({"active_subject": "pro"})

    out = capsys.readouterr().out
    assert "已跳过「今日完成度」写入" in out, f"缺少只读提示，实际输出: {out[-400:]}"
    assert "ky_config.json 保持原样" in out
    assert cfg.read_bytes() == before, "safe 模式下会话结束不得改动 ky_config.json"


def test_session_end_hook_writes_when_not_read_only(tmp_path, monkeypatch, capsys):
    """反向对照：默认模式下会话结束仍要落盘 completion_history。"""
    cfg = tmp_path / "ky_config.json"
    before = _write_cfg(cfg)
    for sp in _study_planner_modules():
        monkeypatch.setattr(sp, "ROOT", tmp_path, raising=False)

    from tools.agent.hooks import HookManager
    hm = HookManager(workspace_root=tmp_path)
    hm.trigger_session_end({"active_subject": "pro"})

    out = capsys.readouterr().out
    assert "已跳过「今日完成度」写入" not in out
    assert cfg.read_bytes() != before, "默认模式下会话结束应写入 completion_history"
    after = json.loads(cfg.read_text(encoding="utf-8"))
    assert after["completion_history"], "completion_history 不应为空"
    assert after["study_plan"]["school"] == "目标院校"


# ───────────────────────── R2-D2 ─────────────────────────

def test_chat_probe_max_tokens_above_upstream_floor():
    """探活 max_tokens 必须大于上游下限 2，否则会被 400 误判成「模型不兼容」。"""
    import doctor

    body = json.loads(doctor._build_chat_probe_body("mimo-v2.5").decode("utf-8"))
    assert body["max_tokens"] > 2, f"max_tokens={body['max_tokens']} 会被上游直接拒绝"
    assert body["model"] == "mimo-v2.5"
    assert body["stream"] is False


@pytest.mark.parametrize("code,expect_ok,expect_status", [
    (429, True, "ratelimit"),      # 限流：接口是通的，不算故障
    (401, False, "auth"),
    (403, False, "auth"),
    (400, None, "incompatible"),   # 无法判定 → 提示人工确认
    (404, None, "incompatible"),
    (500, False, "unavailable"),
    (502, False, "unavailable"),
])
def test_classify_chat_error_keeps_existing_semantics(code, expect_ok, expect_status):
    """状态码分类语义必须与历史实现逐条一致（R2-D2 只改参数与透传，不改分类）。"""
    import doctor

    ok, status, _detail = doctor._classify_chat_error(code, "some upstream message")
    assert (ok, status) == (expect_ok, expect_status)


def test_classify_chat_error_surfaces_upstream_message():
    """上游 error.message 必须出现在诊断结论里，别让用户只能看到笼统结论。"""
    import doctor

    _ok, _status, detail = doctor._classify_chat_error(400, "max_tokens must be greater than 2")
    assert "max_tokens must be greater than 2" in detail
    assert "400" in detail

    # 无上游信息时不应留下多余的分隔符
    _ok2, _s2, detail2 = doctor._classify_chat_error(400, "")
    assert detail2 == "HTTP 400 参数/路由不兼容"


@pytest.mark.parametrize("code,body,expect", [
    (400, '{"error":{"message":"max_tokens must be greater than 2"}}', "max_tokens must be greater than 2"),
    (401, '{"error":{"message":"Invalid API key"}}', "Invalid API key"),
    (403, '{"error":"forbidden"}', "forbidden"),
    (500, "<html>oops</html>", "<html>oops</html>"),
    (400, "not json at all", "not json at all"),
])
def test_read_upstream_error_extracts_message(code, body, expect):
    """各种上游错误体形态都要能尽力提取出可读信息（含非 JSON / HTML 兜底）。"""
    import io
    import urllib.error

    import doctor

    err = urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(body.encode("utf-8")))
    assert expect in doctor._read_upstream_error(err)
