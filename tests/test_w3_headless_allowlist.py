# -*- coding: utf-8 -*-
"""W3 headless 受控放行 —— 回归测试。

背景：评测（KaoyanBench）在 headless 环境驱动 AgentRunner。旧机制靠
``permissions.force_allow_all = True`` 整体绕过审批（连 L5 高危都放行）——
「工具成功率 100%」是假象。W3 改为**评测受控配置**：

    agent.headless_write_policy = "allow_list"
    agent.headless_allow_tools  = ["fetch_url", "web_search", "scout_school"]

即：只点名放行评测链路必需的 L4 网络工具；其余（含 L5 delete_file、
本会话写入的脚本执行）保持 headless 默认拒绝。产品默认 deny_all 不变。

覆盖：
1. 受控配置：点名网络工具放行（正对照，理由不含拒绝文案）；
2. 受控配置：未点名高危工具（delete_file，L5）仍拒（负对照）；
3. 默认配置（无 agent 段）：网络工具仍拒（deny_all 回归，负对照）；
4. 越界写入仍被 sandbox 拒（落盘限工作区内，与审批层无关）；
5. 受控配置不放行「本会话写入的脚本执行」（B2a 闸门 headless 一律拒）；
6. 端到端 trace：受控配置下真跑一轮 fetch_url 工具链（mock 网络），
   工具结果与事件流均无「无法请求用户审批」类拒绝文案。

全程离线：mock 网络层（loop.safe_urlopen 与 tools_impl.safe_urlopen），绝不联网。
"""

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import tools.agent.loop as loop_module  # noqa: E402
import tools.agent.tools_impl as tools_impl_module  # noqa: E402
from tools.agent.loop import AgentRunner  # noqa: E402
from tools.agent.session_log import load_events  # noqa: E402

#: 评测受控配置（与 KaoyanBench shim 注入的一致；改动需两处同步）。
CONTROLLED_AGENT_CFG = {
    "agent": {
        "headless_write_policy": "allow_list",
        "headless_allow_tools": ["fetch_url", "web_search", "scout_school"],
    }
}

#: headless 拒绝文案的可辨识片段（验收：trace 里不得出现）。
DENIAL_MARKERS = ("无法请求用户审批", "PermissionDenied", "headless 策略")


def _make_runner(tmp_path, extra_cfg=None, mode="auto"):
    cfg = {"api_key": "sk-test-fake", "model": "样本模型", "active_subject": "pol"}
    if extra_cfg:
        cfg.update(extra_cfg)
    return AgentRunner(config=cfg, workspace_root=tmp_path,
                       permission_mode=mode, quiet=True)


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body
        self.headers = {}
        # [P1] fetch_url 改走两级采集入口（HTTPFetcher 需要 status 判定健康度）
        self.status = 200

    def read(self, n=-1):
        if n is None or n < 0:
            return self._body
        return self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ── 1. 受控配置：点名放行（正对照） ─────────────────────────────────────


def test_controlled_config_allows_named_network_tools(tmp_path):
    """受控配置下 fetch_url / web_search / scout_school（L4）全部放行。"""
    runner = _make_runner(tmp_path, CONTROLLED_AGENT_CFG)
    for tool in ("fetch_url", "web_search", "scout_school"):
        allowed, reason = runner.permissions.check_permission(
            tool, 4, {"url": "https://example.com"}, interactive=False)
        assert allowed is True, f"{tool} 应放行，实际拒绝: {reason}"
        for marker in DENIAL_MARKERS:
            assert marker not in reason, f"{tool} 放行理由混入拒绝文案: {reason}"


def test_controlled_config_reason_is_allowlist(tmp_path):
    """放行理由可辨识为白名单放行（不是 force_allow_all）。"""
    runner = _make_runner(tmp_path, CONTROLLED_AGENT_CFG)
    allowed, reason = runner.permissions.check_permission(
        "fetch_url", 4, {"url": "https://example.com"}, interactive=False)
    assert allowed is True
    assert "白名单" in reason, f"应走 allow_list 分支，实际: {reason}"


# ── 2. 受控配置：高危仍拒（负对照） ─────────────────────────────────────


def test_controlled_config_denies_dangerous_tool(tmp_path):
    """delete_file（L5）未点名 → 仍拒，且文案可辨识。"""
    runner = _make_runner(tmp_path, CONTROLLED_AGENT_CFG)
    allowed, reason = runner.permissions.check_permission(
        "delete_file", 5, {"path": "样本.txt"}, interactive=False)
    assert allowed is False
    assert "无法请求用户审批" in reason


def test_controlled_config_denies_unnamed_tool(tmp_path):
    """未点名的普通工具（如 run_command 若不在白名单）在 L4 及以上仍拒。"""
    runner = _make_runner(tmp_path, CONTROLLED_AGENT_CFG)
    # 构造一个不在白名单里的 L4 调用
    allowed, reason = runner.permissions.check_permission(
        "未知网络工具", 4, {}, interactive=False)
    assert allowed is False


# ── 3. 默认配置：deny_all 回归（负对照） ────────────────────────────────


def test_default_config_denies_network_tools(tmp_path):
    """无 agent 段（产品默认 deny_all）→ 网络工具仍拒。"""
    runner = _make_runner(tmp_path)  # 不注入受控配置
    allowed, reason = runner.permissions.check_permission(
        "fetch_url", 4, {"url": "https://example.com"}, interactive=False)
    assert allowed is False
    assert "无法请求用户审批" in reason


def test_default_config_ask_mode_denies_write(tmp_path):
    """ask 模式 + 默认配置（deny_all）：headless 下写操作被拒（产品默认语义）。

    注：``auto`` 模式对 L1-3 是**模式级**自动放行（与 headless policy 无关）——
    评测 shim 正是靠这一点获得写文件能力；``deny_all`` 管的是「走到审批通道
    的调用」（ask 模式的 L1+、auto 模式的 L4/L5）。
    """
    runner = _make_runner(tmp_path, mode="ask")
    result = runner.tool_registry.execute_tool(
        "write_file", {"path": "样本.txt", "content": "x"}, interactive=False)
    assert result.startswith("PermissionDenied"), result
    assert not (tmp_path / "样本.txt").exists()


# ── 4. 越界写入：sandbox 兜底（落盘限工作区内） ─────────────────────────


def test_workspace_escape_write_still_denied(tmp_path):
    """受控配置只放行点名工具；越界写入仍被 sandbox 拒（与审批层无关）。"""
    runner = _make_runner(tmp_path, CONTROLLED_AGENT_CFG)
    result = runner.tool_registry.execute_tool(
        "write_file", {"path": "../越界样本.txt", "content": "x"}, interactive=False)
    assert "SecurityError" in result or "Error" in result, result
    assert not (tmp_path.parent / "越界样本.txt").exists(), "越界文件不得落盘"


def test_workspace_inside_write_allowed(tmp_path):
    """工作区内写入在受控配置（auto 模式 L1）正常放行（正对照）。"""
    runner = _make_runner(tmp_path, CONTROLLED_AGENT_CFG)
    result = runner.tool_registry.execute_tool(
        "write_file", {"path": "output/报告.json", "content": "{}"}, interactive=False)
    assert result.startswith("Success"), result
    assert (tmp_path / "output" / "报告.json").is_file()


# ── 5. 脚本执行闸门（B2a）：headless 一律拒 ─────────────────────────────


def test_controlled_config_denies_session_script_exec(tmp_path):
    """受控配置不放行「本会话写入的脚本」执行（force_allow_all 已移除）。"""
    runner = _make_runner(tmp_path, CONTROLLED_AGENT_CFG)
    allowed, reason = runner.permissions.check_session_script_exec(
        "evil.py", {"cmd": "py evil.py"}, interactive=False)
    assert allowed is False
    assert "无法请求用户审批" in reason


# ── 6. 端到端：fetch_url 真链路（mock 网络）无拒绝文案 ──────────────────


def _text_reply(content):
    return {"choices": [{"message": {"content": content, "tool_calls": []}}]}


def _tool_reply(name, args, call_id="call_1"):
    return {"choices": [{"message": {
        "content": None,
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
        }],
    }}]}


def test_e2e_fetch_url_chain_no_denial_in_trace(tmp_path, monkeypatch):
    """受控配置 + mock 网络：fetch_url 工具链跑通，trace 无任何拒绝文案。"""
    html = "<html><head><style>p{}</style></head><body><h1>示例院校招生简章</h1><p>招生 30 人</p><script>x()</script></body></html>"
    fake_resp = _FakeResponse(html.encode("utf-8"))

    # LLM 请求走 loop.safe_urlopen；fetch_url 内部走 tools_impl.safe_urlopen
    llm_script = [
        _tool_reply("fetch_url", {"url": "https://example.com/zs"}),
        _text_reply("根据网页：招生 30 人"),
    ]
    llm_calls = []

    def fake_loop_urlopen(req, timeout=None):
        llm_calls.append(req)
        idx = min(len(llm_calls) - 1, len(llm_script) - 1)
        body = json.dumps(llm_script[idx], ensure_ascii=False).encode("utf-8")
        return _FakeResponse(body)

    monkeypatch.setattr(loop_module, "safe_urlopen", fake_loop_urlopen)
    monkeypatch.setattr(tools_impl_module, "safe_urlopen",
                        lambda req, timeout=None: fake_resp)
    # [P1] fetch_url 现走 tools/intelligence/fetcher.py 的两级采集入口，其
    # safe_urlopen 是另一个模块对象的全局名（双导入路径下各不相同），必须
    # 一并替换，否则该用例会穿透到真实网络（曾拿到 HTTP_404）。
    import importlib
    for _name in ("tools.intelligence.fetcher", "intelligence.fetcher"):
        try:
            _mod = importlib.import_module(_name)
        except ImportError:
            continue
        monkeypatch.setattr(_mod, "safe_urlopen",
                            lambda req, timeout=None, context=None: fake_resp)

    runner = _make_runner(tmp_path, CONTROLLED_AGENT_CFG)
    answer = runner.run("请查一下招生简章", interactive=False)
    assert answer == "根据网页：招生 30 人"

    # 事件流：tool_result 里是真网页内容（工具真的执行成功），无拒绝文案
    events = []
    for p in (tmp_path / ".memory" / "sessions").glob("*.jsonl"):
        events.extend(load_events(p))
    tool_results = [e for e in events if e["type"] == "tool_result"]
    assert len(tool_results) == 1, f"应有 1 条 tool_result，实际 {len(tool_results)}"
    content = tool_results[0]["payload"]["content"]
    assert "招生 30 人" in content, f"工具应返回网页文本，实际: {content[:200]}"
    for marker in DENIAL_MARKERS:
        assert marker not in content, f"trace 混入拒绝文案: {marker}"
    # 全事件流级扫描（含 llm_call / assistant）
    raw = json.dumps(events, ensure_ascii=False)
    assert "无法请求用户审批" not in raw
    assert "PermissionDenied" not in raw
