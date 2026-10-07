# -*- coding: utf-8 -*-
"""2026-10-05 运行时/检索杂项修复批次回归测试。

覆盖 9 项修复（逐项见各测试 docstring）：
  1. 零任务会话不再写 0% 完成率 / 不再推送（假疲劳警报燃料）
  2. safety 类拦截升级文案补 web_search（被拦后转联网检索的指引）
  3. git_status / git_diff 有超时，返回可读错误而非卡死 agent loop
  4. 只读检索不建库：库不存在 → 返回诊断，不产生 data/knowledge/**
  5. `ky index` 子命令注册、--help 可用、调用路径正确
  6. 研究引擎全源冷却连续失败 → 提前终止（阴性对照：关收敛继续空转）
  7. 学习增益报告容忍坏字节（坏行跳过，不再整体崩溃）
  8. FSRS 超大 stage 钳制（不卡死，间隔与钳制档位一致）
  9. `ky mount [目录]` 目录参数透传给 scanner（不传默认行为不变）

隔离约定：全部用 tmp_path / mock；不触碰真实 data/knowledge、真实
ky_config.json、真实错题库，不发起任何网络请求。全程离线。
"""

import importlib
import json
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ════════════════════════════════════════════════════════════════
# 公共工具
# ════════════════════════════════════════════════════════════════

def _import_first(names):
    """按顺序尝试导入，返回第一个成功的模块（双导入环境下取已加载别名）。"""
    for name in names:
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    return None


def _stub_study_planner(monkeypatch, calls):
    """打桩所有 study_planner 别名的 record_daily_completion（hooks 内是
    ``import study_planner`` 顶层名，双导入下可能有 tools.study_planner 副本）。"""
    stub = lambda **kw: calls.append(kw)  # noqa: E731
    for name in ("study_planner", "tools.study_planner"):
        mod = _import_first((name,))
        if mod is not None:
            monkeypatch.setattr(mod, "record_daily_completion", stub, raising=False)


def _stub_webhook_push(monkeypatch, pushes):
    """打桩 ky_cli.broadcast_briefing（hooks 内是 ``import ky_cli``）。"""
    stub = lambda cfg, custom_msg="": pushes.append(custom_msg)  # noqa: E731
    for name in ("ky_cli", "tools.ky_cli"):
        mod = _import_first((name,))
        if mod is not None:
            monkeypatch.setattr(mod, "broadcast_briefing", stub, raising=False)


def _stub_error_logger(monkeypatch):
    """打桩到期错题统计，避免读真实错题库（只读，但保持测试完全隔离）。"""
    for name in ("skills.error_logger", "tools.skills.error_logger"):
        mod = _import_first((name,))
        if mod is not None:
            monkeypatch.setattr(mod, "get_due_reviews", lambda *a, **kw: [],
                                raising=False)


# ════════════════════════════════════════════════════════════════
# 1. 零任务会话：不写 completion、不推送、摘要不出现 0%
# ════════════════════════════════════════════════════════════════

def test_session_end_zero_tasks_skips_completion_and_push(tmp_path, monkeypatch):
    """0 任务：record_daily_completion 不被调用、webhook 不推送、无 0%。

    修复前：total_tasks=0 时 rate=0.0 且无条件写 completion_history ——
    study_planner.check_fatigue_alert 连续 2 天 <60% 即弹「防疲劳减负」面板
    （建议 /relieve 降 25% 任务量），空会话被误判为「任务全线未完成」。

    阴性对照：把 record_daily_completion 调用移出 ``total_tasks > 0`` 守卫，
    本用例的 ``calls == []`` 立即变红（实测已验证）。
    """
    calls, pushes = [], []
    _stub_study_planner(monkeypatch, calls)
    _stub_webhook_push(monkeypatch, pushes)
    _stub_error_logger(monkeypatch)
    (tmp_path / "ky_config.json").write_text(
        json.dumps({"webhooks": {"dingtalk": "https://example.test/hook"}}),
        encoding="utf-8")

    from tools.agent.hooks import HookManager
    hm = HookManager(workspace_root=tmp_path)
    ctx = {}
    hm.trigger_session_end(ctx)

    assert calls == [], "0 任务时不得写 completion_history（假疲劳警报燃料）"
    assert pushes == [], "0 任务时不得推送「今日复习圆满收工」"
    summary = ctx.get("debrief_summary", "")
    assert "今日无任务记录" in summary, f"摘要行应明示无任务，实际: {summary!r}"
    assert "0%" not in summary, "0 任务时摘要不得出现 0% 完成率"


def test_session_end_with_tasks_still_records_and_pushes(tmp_path, monkeypatch):
    """>0 任务：行为完全不变 —— 照常落盘 completion 并推送。"""
    calls, pushes = [], []
    _stub_study_planner(monkeypatch, calls)
    _stub_webhook_push(monkeypatch, pushes)
    _stub_error_logger(monkeypatch)
    (tmp_path / "ky_config.json").write_text(
        json.dumps({"webhooks": {"dingtalk": "https://example.test/hook"}}),
        encoding="utf-8")
    task_dir = tmp_path / "02-英语" / "_状态"
    task_dir.mkdir(parents=True)
    (task_dir / "今日任务.md").write_text(
        "| 模块 | 任务 | 完成状态 |\n"
        "|---|---|---|\n"
        "| 阅读 | 真题精读 | [x] |\n"
        "| 单词 | 核心词复习 | [ ] |\n",
        encoding="utf-8")

    from tools.agent.hooks import HookManager
    hm = HookManager(workspace_root=tmp_path)
    ctx = {}
    hm.trigger_session_end(ctx)

    assert len(calls) == 1, "有任务时 completion 必须照常写入"
    assert calls[0]["total"] == 2
    assert calls[0]["completed"] == 1
    assert calls[0]["rate"] == 50.0
    assert len(pushes) == 1, "有任务时 webhook 照常推送"
    summary = ctx.get("debrief_summary", "")
    assert "50.0%" in summary
    assert "保持节奏" in pushes[0]


# ════════════════════════════════════════════════════════════════
# 2. safety 升级文案含 web_search
# ════════════════════════════════════════════════════════════════

def test_safety_escalation_guide_mentions_web_search(tmp_path):
    """连续 3 次命令安全拦截：升级文案必须含 web_search 替代路径。

    修复前升级文案只列了 read_file / grep / write_file / read_exam_paper，
    漏掉 web_search —— 「被拦后应转联网检索」场景没有指引（search 类文案
    早已含 web_search，safety 类属补漏）。
    """
    from tools.agent.hooks import HookManager
    hm = HookManager(workspace_root=tmp_path)
    ctx = {"block_streaks": {"safety": 0, "search": 0},
           "block_streak_decisions": [], "block_escalate_threshold": 3}
    blocked = ("安全拦截：命令 `cd` 不在白名单内。允许：cat, git, grep, head, "
               "ls, python, pytest, tail, wc。")

    for _ in range(2):
        hm.trigger_post_tool_use("run_command", {"command": "cd x"}, blocked, ctx)
    assert ctx["block_streak_decisions"] == [], "前 2 次不注入（既有行为不变）"

    hm.trigger_post_tool_use("run_command", {"command": "cd x"}, blocked, ctx)
    kinds = [k for _, k in ctx["block_streak_decisions"]]
    assert kinds == ["safety_guard_escalation"]
    guide = ctx["block_streak_decisions"][0][0]
    assert "web_search" in guide, f"升级文案缺 web_search 指引: {guide}"
    assert "read_file" in guide and "write_file" in guide, "既有替代路径不得丢失"


# ════════════════════════════════════════════════════════════════
# 3. git_status / git_diff 超时
# ════════════════════════════════════════════════════════════════

def _registry(workspace: Path):
    from tools.agent.permissions import PermissionManager
    from tools.agent.sandbox import Sandbox
    from tools.agent.tools_impl import ToolRegistry
    perm = PermissionManager(mode="auto", workspace_root=workspace)
    perm.force_allow_all = True
    return ToolRegistry(Sandbox(workspace_root=workspace), perm)


def test_git_status_and_diff_timeout_returns_readable_error(tmp_path, monkeypatch):
    """git 挂起时：返回可读超时错误（含工具名），且确实传入了 timeout。

    修复前 subprocess.run 无 timeout —— 锁等待/凭据提示会让 agent loop
    永久卡死（run_command 早已 clamp 到 [1,300]）。
    """
    from tools.agent import tools_impl as tim
    reg = _registry(tmp_path)
    seen = {}

    def fake_timeout(cmd, **kw):
        seen[cmd[1]] = kw
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout") or 0)

    monkeypatch.setattr(tim.subprocess, "run", fake_timeout)

    out_status = reg.execute_tool("git_status", {}, interactive=False)
    assert "超时" in out_status and "git status" in out_status
    out_diff = reg.execute_tool("git_diff", {}, interactive=False)
    assert "超时" in out_diff and "git diff" in out_diff

    assert seen["status"].get("timeout") == 60
    assert seen["diff"].get("timeout") == 60


def test_git_tools_normal_path_unchanged(tmp_path, monkeypatch):
    """对照组：正常返回路径行为不变（timeout 参数不干扰既有输出口径）。"""
    from tools.agent import tools_impl as tim
    reg = _registry(tmp_path)

    def fake_ok(cmd, **kw):
        assert kw.get("timeout"), "正常调用也必须带 timeout"
        return subprocess.CompletedProcess(cmd, 0, stdout=" M 样本文件.txt\n", stderr="")

    monkeypatch.setattr(tim.subprocess, "run", fake_ok)

    assert "样本文件.txt" in reg.execute_tool("git_status", {}, interactive=False)
    assert "样本文件.txt" in reg.execute_tool("git_diff", {}, interactive=False)


# ════════════════════════════════════════════════════════════════
# 4. 只读检索不建库
# ════════════════════════════════════════════════════════════════

def test_hybrid_search_does_not_create_db_when_missing(tmp_path, monkeypatch):
    """库文件不存在：hybrid 层返回降级诊断，绝不凭空 mkdir+建库。

    修复前 search_with_diagnostics / _run_hybrid 直接 get_knowledge_store()，
    而 KnowledgeStore() 一构造就建目录建表 —— 空工作区里跑一次 ky rag
    就会多出一个空库（对只读语义是副作用）。
    """
    import tools.search.knowledge_store as ks
    import tools.search.hybrid as hybrid
    db = tmp_path / "data" / "knowledge" / "embeddings.db"
    monkeypatch.setattr(ks, "DEFAULT_DB_PATH", db)
    # 重置单例：防止前序用例留下的 _STORE 指向别处，让「是否建库」的
    # 断言失去意义（单例泄漏是阴性对照的假绿源）。
    monkeypatch.setattr(ks, "_STORE", None)

    outcome = hybrid.search_with_diagnostics("矛盾的普遍性", top_k=3)
    assert outcome.results == []
    assert outcome.degraded is True
    assert "ky index" in outcome.degrade_reason, outcome.degrade_reason

    assert hybrid.hybrid_search("矛盾的普遍性", top_k=3) == []
    assert not db.exists(), "库不存在时不得被创建"
    assert not (tmp_path / "data").exists(), "连目录都不应产生"


def test_cli_search_does_not_create_db_when_missing(tmp_path, monkeypatch):
    """cli_integration.cli_search（旧链路直接调 hybrid_search）同样不建库。"""
    import tools.search.knowledge_store as ks
    import tools.search.cli_integration as ci
    db = tmp_path / "data" / "knowledge" / "embeddings.db"
    monkeypatch.setattr(ks, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(ks, "_STORE", None)

    assert ci.cli_search("测试关键词", top_k=3) == []
    assert not (tmp_path / "data").exists()


def test_rag_search_missing_db_points_to_ky_index(tmp_path, monkeypatch, capsys):
    """ky rag 入口：库不存在 → 退出码 2 + 明确指向 ky index，且零副作用。"""
    import tools.search.knowledge_store as ks
    from tools.cli.commands.search import run_rag_search
    db = tmp_path / "data" / "knowledge" / "embeddings.db"
    monkeypatch.setattr(ks, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(ks, "_STORE", None)

    code = run_rag_search("矛盾的普遍性")
    assert code == 2
    out = capsys.readouterr().out
    assert "知识库尚未建立" in out
    assert "ky index" in out
    assert not (tmp_path / "data").exists()


def test_existing_db_still_opens_normally(tmp_path, monkeypatch):
    """对照组：库已存在时守卫放行，正常走检索链路（不误报「未建立」）。"""
    import tools.search.knowledge_store as ks
    import tools.search.hybrid as hybrid
    db = tmp_path / "data" / "knowledge" / "embeddings.db"
    monkeypatch.setattr(ks, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(ks, "_STORE", None)
    store = ks.KnowledgeStore(db)
    store.close()

    outcome = hybrid.search_with_diagnostics("测试关键词", top_k=3)
    assert "尚未建立" not in outcome.degrade_reason


def test_negative_control_without_guard_store_is_created(tmp_path, monkeypatch):
    """阴性对照：跳过守卫、直接走 get_knowledge_store()（等价于修复前链路）
    → 知识库文件确实会被凭空创建。证明守卫是唯一的阻断点。"""
    import tools.search.knowledge_store as ks
    db = tmp_path / "data" / "knowledge" / "embeddings.db"
    monkeypatch.setattr(ks, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(ks, "_STORE", None)

    store = ks.get_knowledge_store()
    store.close()
    monkeypatch.setattr(ks, "_STORE", None)

    assert db.exists(), "无守卫时构造即建库（正是被修掉的副作用）"


# ════════════════════════════════════════════════════════════════
# 5. ky index 子命令
# ════════════════════════════════════════════════════════════════

def test_index_command_registered():
    """ky index 已注册进命令注册表（命令总数 44 → 45）。"""
    from tools.cli.commands import load_all_commands
    load_all_commands()
    from tools.cli.dispatch import get_command, list_commands

    cmd = get_command("index")
    assert cmd is not None, "ky index 未注册"
    assert cmd.name == "index"
    assert cmd.handler is not None
    assert "index" in [c.name for c in list_commands()]


def test_index_command_blocked_in_safe_mode():
    """ky index 会写库：--permission=safe 下必须被命令级闸门拒绝；
    --help/-h 仍按纯读路径放行。"""
    from tools.cli.shared import detect_safe_mode_violation

    assert detect_safe_mode_violation(["index"]) is not None, \
        "建索引是写操作，严格只读模式必须拦截"
    assert detect_safe_mode_violation(["index", "--help"]) is None, \
        "--help 是纯读路径，任何命令都应放行"


def test_index_help_lists_usage_without_side_effects(capsys):
    """ky index --help：打印用法说明（含「不联网、不代建资料」承诺），退出码 0。"""
    from tools.cli.commands.search import _cmd_index

    with pytest.raises(SystemExit) as ei:
        _cmd_index(["index", "--help"])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    assert "ky index" in out
    assert "不联网" in out
    assert "--no-vector" in out


def test_index_build_calls_indexer_with_flags(monkeypatch):
    """调用路径：run_index_build 把 --no-vector / --quiet 转成 build_index 实参。"""
    import tools.search.indexer as indexer
    calls = []

    def fake_build(enable_vector=True, show_progress=True):
        calls.append((enable_vector, show_progress))

    monkeypatch.setattr(indexer, "build_index", fake_build)
    from tools.cli.commands.search import run_index_build

    assert run_index_build(enable_vector=False, show_progress=True) == 0
    assert run_index_build() == 0
    assert calls == [(False, True), (True, True)]


def test_rag_usage_text_points_to_ky_index():
    """自述文案同步：ky rag 说明不再引导手敲脚本路径。"""
    from tools.cli.commands.search import _USAGE

    assert "ky index" in _USAGE
    assert "tools/search/indexer.py" not in _USAGE


# ════════════════════════════════════════════════════════════════
# 6. 研究引擎全源冷却收敛
# ════════════════════════════════════════════════════════════════

class _FakeHTTPResponse:
    """safe_urlopen 的最小鸭子类型替身（read(n) + context manager）。"""

    def __init__(self, payload: bytes):
        self._payload = payload
        self.headers = {}

    def read(self, n=-1):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_tool_web_search_marks_all_failed_cooling(tmp_path):
    """全源冷却时 web_search 返回带 all_failed_cooling 标记的错误条目。

    修复前只透出 results（[]）—— 模型误以为「没搜到」并反复换词重试
    （W11 实测 19 次 Bing 反爬页空转）。
    """
    from unittest.mock import patch
    from tools.intelligence.agentic_research import ToolDispatcher
    from tools.search.models import SearchResponse

    dispatcher = ToolDispatcher(workspace_root=tmp_path)
    cooling = (("bing", "反爬拦截"), ("duckduckgo", "反爬拦截"))
    resp = SearchResponse(query="q", results=(),
                          providers_failed=cooling, providers_cooling=cooling)
    assert resp.all_failed_cooling is True  # 判据来自 W11（models.py）

    with patch("tools.search.service.SearchService.default") as mock_default:
        mock_default.return_value.search.return_value = resp
        out = dispatcher.dispatch("web_search", query="q", limit=3)

    assert isinstance(out, list) and out
    assert out[0].get("all_failed_cooling") is True
    assert "冷却" in out[0]["error"]


def _install_always_search_llm(monkeypatch):
    """每轮 LLM 都返回一次 web_search tool_call（模拟模型换词重试空转）。"""
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(1)
        n = len(calls)
        body = {"choices": [{"message": {
            "role": "assistant", "content": "",
            "tool_calls": [{
                "id": f"call_{n}", "type": "function",
                "function": {"name": "web_search",
                             "arguments": json.dumps({"query": f"换词重试 {n}"})},
            }],
        }}]}
        return _FakeHTTPResponse(json.dumps(body).encode("utf-8"))

    monkeypatch.setattr("tools.intelligence.agentic_research.safe_urlopen",
                        fake_urlopen)
    return calls


_API_CFG = {"api_key": "sk-mock-valid-key",
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat"}


def test_research_loop_stops_on_consecutive_all_source_cooling(tmp_path, monkeypatch):
    """连续 3 次全源冷却 → 提前终止，返回降级说明（不再空转）。

    阈值 3（与 block_escalate_threshold 同口径）：第 3 次全冷却即返回，
    LLM 请求总数必须是 3（而非 max_steps=6）。
    """
    from tools.intelligence.agentic_research import AgenticResearchEngine

    engine = AgenticResearchEngine(workspace_root=tmp_path)
    engine.dispatcher.dispatch = lambda name, **kw: [  # type: ignore[method-assign]
        {"error": "全部检索源处于反爬冷却期", "all_failed_cooling": True}]
    calls = _install_always_search_llm(monkeypatch)

    res = engine.execute_loop("研究目标院校", api_config=_API_CFG)

    assert len(calls) == 3, f"应在第 3 次全冷却后终止，实际请求 {len(calls)} 次"
    assert "冷却" in res and "降级" in res, f"缺少明确降级说明: {res!r}"


def test_negative_control_cooling_disabled_keeps_spinning(tmp_path, monkeypatch):
    """阴性对照：关掉收敛（retrieval_fail_limit=0）→ 继续空转到步数耗尽。

    证明「请求数从 6 降到 3」确由收敛逻辑造成，而非其他提前返回路径。
    """
    from tools.intelligence.agentic_research import AgenticResearchEngine

    engine = AgenticResearchEngine(workspace_root=tmp_path)
    engine.retrieval_fail_limit = 0  # 禁用收敛
    engine.dispatcher.dispatch = lambda name, **kw: [  # type: ignore[method-assign]
        {"error": "全部检索源处于反爬冷却期", "all_failed_cooling": True}]
    calls = _install_always_search_llm(monkeypatch)

    res = engine.execute_loop("研究目标院校", api_config=_API_CFG)

    assert len(calls) == engine.max_steps, "关收敛后应一直空转到 max_steps"
    assert "降级说明" not in res


def test_successful_search_resets_fail_streak(tmp_path, monkeypatch):
    """检索恢复（正常返回）即重置连续计数：全冷却 2 次 → 成功 1 次 →
    再全冷却 2 次也不触发收敛（「连续」语义）。"""
    from tools.intelligence.agentic_research import AgenticResearchEngine

    engine = AgenticResearchEngine(workspace_root=tmp_path)
    seq = [
        [{"error": "冷却", "all_failed_cooling": True}],
        [{"error": "冷却", "all_failed_cooling": True}],
        [{"title": "正常结果", "snippet": "s", "url": "https://example.test/a",
          "provider": "bing"}],
        [{"error": "冷却", "all_failed_cooling": True}],
        [{"error": "冷却", "all_failed_cooling": True}],
        [{"error": "冷却", "all_failed_cooling": True}],
    ]
    counter = {"i": 0}

    def fake_dispatch(name, **kw):
        item = seq[min(counter["i"], len(seq) - 1)]
        counter["i"] += 1
        return item

    engine.dispatcher.dispatch = fake_dispatch  # type: ignore[method-assign]
    calls = _install_always_search_llm(monkeypatch)

    res = engine.execute_loop("研究目标院校", api_config=_API_CFG)

    # 第 6 次（恢复后的第 3 次全冷却）才触发收敛 → 共 6 次请求
    assert len(calls) == 6
    assert "冷却" in res


# ════════════════════════════════════════════════════════════════
# 7. 学习增益报告容忍坏字节
# ════════════════════════════════════════════════════════════════

def test_collect_review_events_tolerates_bad_bytes(tmp_path):
    """复测日志含损坏字节：坏行跳过、有效行照常统计，不再整体崩溃。

    修复前 read_text(encoding="utf-8") 直接抛 UnicodeDecodeError，与模块
    「坏行跳过」承诺不符（与错题本读取的 errors="replace" 口径不一致）。
    """
    from benchmarks import learning_gain as lg

    p = tmp_path / ".memory" / "review_log.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    good = json.dumps({"ts": "2026-09-21 09:00:00", "subject": "math",
                       "title": "测试题", "rating": "good"},
                      ensure_ascii=False).encode("utf-8")
    p.write_bytes(good + b"\n" + b"\xff\xfe\x00 broken bytes \xff\n" + good + b"\n")

    events, invalid = lg.collect_review_events(p)
    assert len(events) == 2, "有效行必须照常统计"
    assert invalid, "坏字节行应计入 invalid（坏行跳过）"

    # 阴性对照：原始字节确实无法按严格 UTF-8 解码（修复前必崩）
    with pytest.raises(UnicodeDecodeError):
        p.read_text(encoding="utf-8")


# ════════════════════════════════════════════════════════════════
# 8. FSRS 超大 stage 钳制
# ════════════════════════════════════════════════════════════════

def test_fsrs_huge_stage_is_clamped_and_fast():
    """stage=10**9：不卡死也不溢出（钳到 50 档），间隔与 50 档完全一致。

    修复前按 stage 逐档快进无上限 —— 损坏/被篡改的错题卡片字段会让
    compute_next_interval 长时间卡死（每次 review_card 都是完整 FSRS 计算）；
    实测未钳制的 stage>=90 还会让 FSRS 内部日期运算抛 OverflowError。
    """
    from tools.fsrs_scheduler import _MAX_STAGE, compute_next_interval

    assert _MAX_STAGE == 50
    today = date(2026, 1, 1)

    t0 = time.monotonic()
    stage, due, days = compute_next_interval(10 ** 9, "good", today)
    elapsed = time.monotonic() - t0
    assert elapsed < 10.0, f"超大 stage 不得卡死（实测 {elapsed:.1f}s）"

    assert stage == 10 ** 9 + 1, "new_stage 仍按原始 stage 递推（可复算不变式）"
    _, due_cap, days_cap = compute_next_interval(_MAX_STAGE, "good", today)
    assert (due, days) == (due_cap, days_cap), "间隔应与钳制档位（50）一致"


def test_fsrs_overflow_zone_no_longer_raises():
    """钳制同时消灭了 stage 90-99 的 OverflowError（本函数承诺不抛异常）。"""
    from tools.fsrs_scheduler import compute_next_interval
    today = date(2026, 1, 1)

    for st in (90, 99, 10 ** 9):
        stage, due, days = compute_next_interval(st, "hard", today)
        assert stage == st + 1
        assert days > 0


def test_fsrs_normal_stages_unchanged():
    """对照组：正常档位（0-5）行为与校准表逐档一致（钳制不影响）。"""
    from tools.fsrs_scheduler import compute_next_interval
    today = date(2026, 1, 1)

    assert compute_next_interval(0, "good", today) == (1, date(2026, 1, 3), 2)
    assert compute_next_interval(0, "again", today) == (0, date(2026, 1, 2), 1)
    stage, due, days = compute_next_interval(3, "easy", today)
    assert (stage, days) == (4, 265)


# ════════════════════════════════════════════════════════════════
# 9. ky mount 目录参数透传
# ════════════════════════════════════════════════════════════════

def _patch_scanner(monkeypatch, calls):
    """打桩 material_scanner.scan_and_mount_materials（双导入两个别名都打）。"""
    def fake_scan(workspace_root=None, auto_scout_school=True, apply=False):
        calls.append((workspace_root, apply))
        return {"success": True, "total_files": 0, "details": {}, "changes": []}

    patched = 0
    for name in ("tools.skills.material_scanner", "skills.material_scanner"):
        mod = _import_first((name,))
        if mod is not None:
            monkeypatch.setattr(mod, "scan_and_mount_materials", fake_scan)
            patched += 1
    assert patched >= 1, "material_scanner 未加载"


def test_mount_directory_arg_is_passed_through(tmp_path, monkeypatch, capsys):
    """ky mount <目录>：scanner 必须收到该目录（修复前被静默丢弃）。"""
    from tools.cli.commands import material as material_cmd

    calls = []
    _patch_scanner(monkeypatch, calls)
    target = tmp_path / "我的资料目录"
    target.mkdir()

    material_cmd._cmd_mount(["mount", str(target)])

    assert len(calls) == 1
    assert calls[0][0] == target.resolve(), "scanner 应收到用户传入的目录"
    assert calls[0][1] is False, "默认仍为只读盘点"
    capsys.readouterr()


def test_mount_without_directory_keeps_default(tmp_path, monkeypatch, capsys):
    """不传目录：workspace_root=None（scanner 内回落默认工作区），行为不变。"""
    from tools.cli.commands import material as material_cmd

    calls = []
    _patch_scanner(monkeypatch, calls)

    material_cmd._cmd_mount(["mount"])

    assert calls == [(None, False)]
    capsys.readouterr()


def test_mount_missing_directory_reports_error(tmp_path, monkeypatch, capsys):
    """目录不存在：明确报错退出（不再静默回落默认目录）。"""
    from tools.cli.commands import material as material_cmd

    calls = []
    _patch_scanner(monkeypatch, calls)

    with pytest.raises(SystemExit) as ei:
        material_cmd._cmd_mount(["mount", str(tmp_path / "不存在的目录")])
    assert ei.value.code == 1
    out = capsys.readouterr().out
    assert "目录不存在" in out
    assert calls == [], "报错时不得调用 scanner"
