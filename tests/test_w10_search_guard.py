# -*- coding: utf-8 -*-
"""[W10 检索行为引导] fetch_url 拦截「搜索引擎直抓」——回归测试。

背景（KaoYanBench core50 w9/w10 实测）
--------------------------------------
50 题中 web_search 调用为 **0**、fetch_url 560 次且大量直接打在搜索引擎上
（Bing/百度/DuckDuckGo/Sogou/Mojeek/Ecosia/Brave/Searx… 普遍反爬，返回验证码
或无关页）——既浪费步数又拿不到有效结果。系统提示词级引导（「优先使用
web_search」）压不住 flash 级模型「搜索=手动拼接搜索引擎 URL」的强惯性，
故在工具层拦截：命中「搜索引擎域名 + 搜索行为」时拒绝执行并明确引导到
web_search；打开引擎的静态页面（首页/已选定结果）不受影响。

工具层拦截 + 提示词强化仍压不住「被拦后换引擎重试」时，loop 层在拦截回包
之后追加一条直白的用户消息（nudge）再次要求调用 web_search（第 3 组用例）。

全程离线：工具层用例只触发拦截分支，不发起任何网络请求；loop 层用例
mock ``_call_llm`` 与 ``execute_tool``。
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from tools.agent.tools_impl import _search_engine_host  # noqa: E402


# ── 1. 判定函数：命中「搜索引擎域名 + 搜索行为」 ────────────────────────


@pytest.mark.parametrize("url,expected", [
    # 实测中模型真实使用过的搜索引擎（w9 SEARCH-005 轨迹）
    ("https://www.bing.com/search?q=%E8%80%83%E7%A0%94%E6%95%B0%E5%AD%A6", "bing.com"),
    ("https://cn.bing.com/search?q=x&format=rss&mkt=zh-CN", "bing.com"),
    ("https://www.baidu.com/s?wd=kaoyan", "baidu.com"),
    ("https://duckduckgo.com/html/?q=x", "duckduckgo.com"),
    ("https://lite.duckduckgo.com/lite/?q=x", "duckduckgo.com"),
    ("https://www.sogou.com/web?query=x", "sogou.com"),
    ("https://www.so.com/s?q=x", "so.com"),
    ("https://search.marginalia.nu/search?query=x", "marginalia.nu"),
    ("https://searx.be/search?q=x&format=json", "searx.be"),
    ("https://search.brave.com/search?q=x", "brave.com"),
    ("https://www.ecosia.org/search?q=x", "ecosia.org"),
    ("https://www.mojeek.com/search?q=x", "mojeek.com"),
    ("https://www.google.com/search?q=x", "google.com"),
    # 仅搜索路径、无查询参数（Bing /search 空参数页同样是搜索）
    ("https://www.bing.com/search", "bing.com"),
])
def test_search_engine_fetch_detected(url, expected):
    assert _search_engine_host(url) == expected


@pytest.mark.parametrize("url", [
    # 非搜索引擎域名：正常网页不受影响
    "https://yz.chsi.com.cn/kyzx/zsjz/",
    # w9 轨迹中模型猜过的院校研招页形态（域名已中性化，真实校名不进 tests/）
    "https://gra.example.edu.cn/plus/list.php?tid=12",
    "https://yankao.neea.edu.cn/html1/category/1509/6235-1.htm",
    "https://www.neea.edu.cn/",
    # 搜索引擎域名但非搜索行为（首页 / 地图等静态页）→ 不拦
    "https://www.bing.com/",
    "https://www.baidu.com/",
    "https://www.google.com/maps/place/x",
    # 参数名精确匹配：password 不因含 "word" 子串而误伤（且域名也非引擎）
    "https://accounts.example.com/login?password=x",
])
def test_non_search_fetch_not_detected(url):
    assert _search_engine_host(url) is None


# ── 2. 工具层：拦截并引导 web_search（不发起网络请求） ──────────────────


def _tool(name, tmp_path):
    from tools.agent.permissions import PermissionManager
    from tools.agent.sandbox import Sandbox
    from tools.agent.tools_impl import ToolRegistry
    reg = ToolRegistry(Sandbox(workspace_root=tmp_path),
                       PermissionManager(mode="auto", workspace_root=tmp_path))
    return reg.tools[name].func


def _patch_both_fetchers(monkeypatch, attr, value):
    """工具层实际跑的是 ``intelligence.fetcher``，而本文件导入的是
    ``tools.intelligence.fetcher`` —— 双导入路径下这是两个模块对象，
    两处都打补丁（同 test_p1_two_tier_fetch 的既有约定）。"""
    import importlib
    names = []
    for name in ("tools.intelligence.fetcher", "intelligence.fetcher"):
        try:
            mod = importlib.import_module(name)
        except ImportError:
            continue
        monkeypatch.setattr(mod, attr, value)
        names.append(name)
    assert names, f"未能 patch 任何 fetcher 模块的 {attr}"


def test_tool_fetch_url_blocks_search_engine(tmp_path):
    """搜索引擎直抓 → 拦截文案含原因与 web_search 引导。"""
    fn = _tool("fetch_url", tmp_path)
    out = fn("https://www.bing.com/search?q=kaoyan")
    assert out.startswith("Error: 已拦截搜索引擎直抓")
    assert "web_search" in out
    assert "反爬" in out


def test_tool_fetch_url_normal_site_passes_guard(tmp_path, monkeypatch):
    """正常网页 URL 不触发拦截（mock 掉抓取层，只验证守卫放行）。"""
    calls = []

    class _Res:
        is_valid = True
        access_status = "OK"
        status_code = 200
        tier = "http"
        escalation = None
        content = "<html><body>ok</body></html>"
        resources = None

    def fake_fetch(url, **kwargs):
        calls.append(url)
        return _Res()

    _patch_both_fetchers(monkeypatch, "fetch_with_fallback", fake_fetch)
    fn = _tool("fetch_url", tmp_path)
    out = fn("https://accounts.example.com/login?password=x")
    assert not out.startswith("Error: 已拦截搜索引擎直抓")
    assert calls == ["https://accounts.example.com/login?password=x"]


# ── 3. loop 层：拦截后向对话流追加 web_search 引导（nudge） ──────────────
#
# 实测（w10-s005b/c）：工具层拦截生效后，模型被拦 5 次仍换引擎重试、猜站内
# URL，就是不调用 web_search。故在工具回包后追加一条直白的用户消息——这是
# 提示词/描述/置顶都压不住时的最后一道引导（tools/agent/loop.py [W10 检索
# 行为引导]）。本组用例走 run() 全链路，mock _call_llm 与 execute_tool。

#: 复刻工具层拦截文案的关键前缀（真实文案见 tools_impl.py fetch_url 拦截分支）。
_BLOCKED_RESULT = (
    "Error: 已拦截搜索引擎直抓（bing.com）。本环境已禁用搜索引擎直接抓取——"
    "更换其他搜索引擎重试同样会被拦截。检索信息的正确方式是 **web_search 工具**，"
    "请立即改用。"
)


def _text_reply(content):
    """无工具调用的普通回复（循环应在此终止）。"""
    return {"choices": [{"message": {"content": content, "tool_calls": []}}]}


def _tool_reply(name, args, call_id="call_f1"):
    """带工具调用的回复（模型继续要工具 → 循环继续）。"""
    return {"choices": [{"message": {
        "content": "",
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(args, ensure_ascii=False)},
        }],
    }}]}


def _make_loop_runner(tmp_path, exec_result):
    """构造离线 AgentRunner；工具执行固定返回 exec_result。"""
    from tools.agent.loop import AgentRunner
    cfg = {"api_key": "sk-test-fake", "model": "样本模型", "active_subject": "pol"}
    runner = AgentRunner(config=cfg, workspace_root=tmp_path,
                         permission_mode="auto", quiet=True, max_steps=3)
    runner.tool_registry.execute_tool = (
        lambda name, args, interactive=True, call_id="": exec_result)
    return runner


def _install_scripted_llm(monkeypatch, scripted):
    """替换 ``_call_llm`` 为脚本化假实现；返回调用记录（messages, allow_tools）。"""
    from tools.agent.loop import AgentRunner
    calls = []

    def fake_call_llm(self, messages, allow_tools=True, tools_subset=None):
        calls.append({"messages": list(messages), "allow_tools": allow_tools})
        idx = min(len(calls) - 1, len(scripted) - 1)
        return scripted[idx]

    monkeypatch.setattr(AgentRunner, "_call_llm", fake_call_llm)
    return calls


def _load_events(tmp_path):
    from tools.agent.session_log import load_events
    events = []
    for p in (tmp_path / ".memory" / "sessions").glob("*.jsonl"):
        events.extend(load_events(p))
    return events


def _nudge_events(tmp_path):
    return [e for e in _load_events(tmp_path)
            if e.get("payload", {}).get("kind") == "search_guard_nudge"]


def test_blocked_fetch_appends_search_guard_nudge(tmp_path, monkeypatch):
    """fetch_url 被拦截 → 下一轮对话流含直白的 web_search 指令 + 事件记录。"""
    calls = _install_scripted_llm(monkeypatch, [
        _tool_reply("fetch_url", {"url": "https://www.bing.com/search?q=x"}),
        _text_reply("最终答复"),
    ])
    runner = _make_loop_runner(tmp_path, exec_result=_BLOCKED_RESULT)

    out = runner.run("检索任务", interactive=False)

    assert out == "最终答复"
    assert len(calls) == 2, "应为 1 步工具 + 1 次终答"
    # 拦截后的下一轮调用能看到 nudge（role=user，含 web_search 与禁止重试约束）
    nudge_msgs = [m for m in calls[1]["messages"]
                  if m.get("role") == "user" and "web_search" in str(m.get("content"))]
    assert nudge_msgs, "拦截后必须追加 web_search 引导"
    assert "不要再尝试其他搜索引擎" in nudge_msgs[0]["content"]
    # 事件流可审计
    assert len(_nudge_events(tmp_path)) == 1


def test_normal_fetch_gets_no_nudge(tmp_path, monkeypatch):
    """正常 URL 抓取成功 → 不追加 nudge、不记事件。"""
    calls = _install_scripted_llm(monkeypatch, [
        _tool_reply("fetch_url", {"url": "https://yz.chsi.com.cn/kyzx/zsjz/"}),
        _text_reply("最终答复"),
    ])
    runner = _make_loop_runner(tmp_path, exec_result="页面正文：招生简章……")

    out = runner.run("检索任务", interactive=False)

    assert out == "最终答复"
    assert not [m for m in calls[1]["messages"]
                if m.get("role") == "user" and "已被拦截" in str(m.get("content"))]
    assert _nudge_events(tmp_path) == []


def test_nudge_not_triggered_for_other_tools(tmp_path, monkeypatch):
    """其他工具的结果即便含拦截字样（如读日志）也不触发 nudge（仅限 fetch_url）。"""
    _install_scripted_llm(monkeypatch, [
        _tool_reply("read_file", {"path": "样本日志.txt"}),
        _text_reply("最终答复"),
    ])
    runner = _make_loop_runner(tmp_path, exec_result=_BLOCKED_RESULT)

    out = runner.run("读日志", interactive=False)

    assert out == "最终答复"
    assert _nudge_events(tmp_path) == []


def test_nudge_repeats_on_each_blocked_fetch(tmp_path, monkeypatch):
    """连续两次被拦 → 每次都有引导（模型换引擎重试时仍被拉回）。"""
    calls = _install_scripted_llm(monkeypatch, [
        _tool_reply("fetch_url", {"url": "https://www.bing.com/search?q=x"}, "c1"),
        _tool_reply("fetch_url", {"url": "https://www.baidu.com/s?wd=x"}, "c2"),
        _text_reply("最终答复"),
    ])
    runner = _make_loop_runner(tmp_path, exec_result=_BLOCKED_RESULT)

    out = runner.run("检索任务", interactive=False)

    assert out == "最终答复"
    assert len(calls) == 3
    assert len(_nudge_events(tmp_path)) == 2
