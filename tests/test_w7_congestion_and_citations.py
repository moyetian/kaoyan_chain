# -*- coding: utf-8 -*-
"""[W7b] 拥塞避让与本地引用 —— 回归测试。

背景（KaoYanBench core50 v3.1.0-w4w6 实测）
------------------------------------------
- 12 题空答案全部是「网络死亡螺旋」：30 条 network 失败全部 lat≈184s
  （3 次尝试 × 61.3s）；442 次单次成功请求 max=59.9s、无一次 >=60s
  → 服务端 60s 网关硬超时。失败呈波动性拥塞（10 分钟桶失败率 0-41%），
  短退避（0.5/1.5s）的重试仍落在同一拥塞窗口内 → 三连败。
- PDF 类任务的引用是「文件名 + 页码 + 原文」型（非 URL），只认 URL 的
  提取器让 citation 维度成建制 0 分。
- ``python -c`` 拦截文案引导「写脚本执行」，但受控目录闸门会再拒绝
  工作区脚本 → 模型被引导进「写脚本 → 再被拦」的循环。

修复
----
1. 网络/5xx 退避 0.5/1.5s → 2/6s（错过拥塞窗口）；llm_call 埋点补
   prompt_chars / message_count（区分「生成量」与「服务端拥塞」假设）；
2. ``_emit_citations`` 扩展 {page, quote} 型引用（含中文键与代码块剥壳，
   引文前 40 字符在工具结果中核验 supported）；
3. 契约段增补：输出精简 / 长文档续读 / schema 严格遵循 / 产物落盘前置自检；
4. ``python -c`` 拦截文案直接引导内置工具（一步到位，不再指向脚本执行）。
"""

import json
import sys
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import tools.agent.loop as _loop_mod  # noqa: E402
from tools.agent.loop import AgentRunner  # noqa: E402


def _make_runner(tmp_path, **kw):
    cfg = {"api_key": "sk-test-fake", "model": "样本模型", "active_subject": "pol"}
    return AgentRunner(config=cfg, workspace_root=tmp_path,
                       permission_mode="auto", quiet=True, max_steps=1, **kw)


# ── 1. 契约段增补 ───────────────────────────────────────────────────────


def test_contract_section_has_w7b_additions(tmp_path):
    """契约段含输出精简 / 长文档续读 / schema 严格遵循 / 本地引用规范。"""
    runner = _make_runner(tmp_path)
    prompt = runner.context_engine.build_system_prompt()
    assert "作答契约与引用规范" in prompt
    assert "输出精简" in prompt
    assert "长文档续读" in prompt
    assert "要求数组就输出数组" in prompt
    assert "文件名 + 页码" in prompt
    print("  [√] 契约段 W7b 增补齐备")


# ── 2. {page, quote} 引用提取 ──────────────────────────────────────────


def test_extract_page_quote_refs_english_keys():
    text = json.dumps({
        "source_file": "references/doc.pdf",
        "fields": [
            {"name": "学制", "value": None, "page": None, "quote": None},
            {"name": "复试方式", "value": "面试", "page": 7,
             "quote": "Retest method: on-site interview."},
        ],
    }, ensure_ascii=False)
    refs = AgentRunner._extract_page_quote_refs(text)
    assert len(refs) == 1
    assert refs[0]["page"] == 7
    assert refs[0]["source_file"] == "references/doc.pdf"
    print("  [√] 英文键 page/quote 提取")


def test_extract_page_quote_refs_chinese_keys_and_fence():
    """中文键（页码/原文）与 ```json 代码块包裹均可解析。"""
    text = "```json\n" + json.dumps({
        "来源文件": "references/x.pdf",
        "条目": [{"页码": 3, "原文": "复试占 40%"}],
    }, ensure_ascii=False) + "\n```"
    refs = AgentRunner._extract_page_quote_refs(text)
    assert len(refs) == 1
    assert refs[0]["page"] == 3
    assert refs[0]["source_file"] == "references/x.pdf"
    print("  [√] 中文键 + 代码块剥壳")


def test_extract_page_quote_refs_non_json():
    assert AgentRunner._extract_page_quote_refs("这是散文，不是 JSON") == []
    assert AgentRunner._extract_page_quote_refs("") == []
    print("  [√] 非 JSON 安全返回空")


# ── 3. _emit_citations 的 page/quote 路径 ──────────────────────────────


def _tool_messages(*contents):
    return [{"role": "tool", "content": c} for c in contents]


def test_emit_citations_page_quote_supported(tmp_path):
    """引文出现在工具结果中 → supported=True、source_ref 含文件名与页码。"""
    runner = _make_runner(tmp_path)
    answer = json.dumps({
        "source_file": "references/doc.pdf",
        "fields": [
            {"name": "复试权重", "page": 7, "quote": "Weight: retest 40%"},
            {"name": "学制", "page": None, "quote": None},
        ],
    }, ensure_ascii=False)
    messages = _tool_messages("第 7 页：Weight: retest 40%, preliminary 60%.")
    runner._emit_citations(answer, messages)
    data = json.loads((tmp_path / ".memory" / "last_citations.json")
                      .read_text(encoding="utf-8"))
    assert len(data) == 1
    c = data[0]
    assert c["supported"] is True
    assert c["source_ref"] == "references/doc.pdf 第 7 页"
    assert c["unsupported_reason"] is None
    print("  [√] page/quote 引用 supported=True")


def test_emit_citations_page_quote_unsupported(tmp_path):
    """引文不在工具结果中 → supported=False 且写明原因（不伪装通过）。"""
    runner = _make_runner(tmp_path)
    answer = json.dumps({
        "source_file": "references/doc.pdf",
        "fields": [{"page": 9, "quote": "编造的引文内容不存在"}],
    }, ensure_ascii=False)
    messages = _tool_messages("第 7 页：完全不同的内容。")
    runner._emit_citations(answer, messages)
    data = json.loads((tmp_path / ".memory" / "last_citations.json")
                      .read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["supported"] is False
    assert "未在本轮工具结果中出现" in data[0]["unsupported_reason"]
    print("  [√] page/quote 引用 supported=False（未核验）")


def test_emit_citations_mixed_url_and_page_quote(tmp_path):
    """URL 与 page/quote 混合时都产出，编号连续。"""
    runner = _make_runner(tmp_path)
    answer = json.dumps({
        "source_file": "references/doc.pdf",
        "fields": [{"page": 7, "quote": "Retest method: interview."}],
        "url": "https://example.edu.cn/notice",
    }, ensure_ascii=False)
    messages = _tool_messages(
        "Retest method: interview.", "来源 https://example.edu.cn/notice")
    runner._emit_citations(answer, messages)
    data = json.loads((tmp_path / ".memory" / "last_citations.json")
                      .read_text(encoding="utf-8"))
    assert len(data) == 2
    assert [c["citation_id"] for c in data] == ["cit1", "cit2"]
    assert all(c["supported"] is True for c in data)
    print("  [√] URL + page/quote 混合产出")


# ── 4. python -c 拦截文案（不再指向脚本执行死路）──────────────────────


def test_python_c_denial_points_to_builtin_tools(tmp_path):
    runner = _make_runner(tmp_path)
    out = runner.tool_registry.execute_tool(
        "run_command", {"command": 'python -c "print(1)"'}, interactive=False)
    assert "安全拦截" in out
    assert "替代路径" in out
    assert "read_file" in out
    assert "请将逻辑写入工作区内的 .py 脚本" not in out, "旧文案死路不应保留"
    print("  [√] python -c 文案引导内置工具")


# ── 5. 拥塞避让：退避拉长 + 埋点补字段 ─────────────────────────────────


def _text_reply(content):
    return {"choices": [{"message": {"content": content, "tool_calls": []}}]}


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.headers = {}

    def read(self, n=-1):
        return self._body if (n is None or n < 0) else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_network_backoff_values_are_2s_6s(tmp_path, monkeypatch):
    """网络类退避实测为 2s/6s（含 jitter 上限 0.5s）。"""
    sleeps = []

    def fake_sleep(sec):
        sleeps.append(round(float(sec), 2))

    err = urllib.error.URLError("boom")
    def fake_urlopen(req, timeout=None):
        raise err

    monkeypatch.setattr(_loop_mod, "safe_urlopen", fake_urlopen)
    monkeypatch.setattr(_loop_mod.time, "sleep", fake_sleep)

    runner = _make_runner(tmp_path)
    assert runner.run("你好", interactive=False) == ""
    # 主对话 + 收尾两档，各 2 次退避（attempt 0/1），共 6 次
    backoff = [s for s in sleeps if s >= 2.0]
    assert len(backoff) == 6, f"应为 3 次调用 × 2 次退避，实际 {sleeps}"
    # 每次退避 = 基准（2.0 或 6.0）+ jitter(<0.5)
    bases = sorted({2.0 if s < 4 else 6.0 for s in backoff})
    assert bases == [2.0, 6.0]
    assert all(2.0 <= s <= 2.5 or 6.0 <= s <= 6.5 for s in backoff)
    print("  [√] 网络退避 2s/6s + jitter")


def _sse_response(content: str, chunk_size: int = 19):
    """[W8] 生产代码默认流式：成功路径 mock 改为逐片吐 SSE。"""
    events = [
        {"choices": [{"index": 0, "delta": {"content": content},
                      "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    lines = []
    for ev in events:
        lines.append("data: " + json.dumps(ev, ensure_ascii=False))
        lines.append("")
    lines.append("data: [DONE]")
    lines.append("")
    body = ("\n".join(lines) + "\n").encode("utf-8")

    class _SSEResp:
        headers = {"Content-Type": "text/event-stream"}

        def __init__(self):
            self._b, self._p = body, 0

        def read(self, n=-1):
            if n is None or n < 0:
                piece, self._p = self._b[self._p:], len(self._b)
                return piece
            size = min(n, chunk_size)
            piece = self._b[self._p:self._p + size]
            self._p += len(piece)
            return piece

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return _SSEResp()


def test_llm_call_event_has_prompt_chars(tmp_path, monkeypatch):
    """llm_call 埋点含 prompt_chars / message_count（W7b 诊断字段）。"""
    fake = lambda req, timeout=None: _sse_response("答复")
    monkeypatch.setattr(_loop_mod, "safe_urlopen", fake)

    runner = _make_runner(tmp_path)
    assert runner.run("你好", interactive=False) == "答复"
    sess = list((tmp_path / ".memory" / "sessions").glob("*.jsonl"))
    events = []
    for p in sess:
        for line in p.read_text(encoding="utf-8").splitlines():
            ev = json.loads(line)
            if ev.get("type") == "llm_call":
                events.append(ev)
    assert events
    payload = events[0]["payload"]
    assert payload["prompt_chars"] > 0
    assert payload["message_count"] >= 1
    print("  [√] llm_call 埋点含 prompt_chars / message_count")
