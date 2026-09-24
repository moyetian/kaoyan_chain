# -*- coding: utf-8 -*-
"""B2b 回归 · 「读取工作区外文件」授权闸门（默认拒绝 + 交互授权 + 目录级记忆）。

缺陷现场
--------
``Sandbox.resolve_safe_path`` 此前对「工作区外 + 只读 + 白名单扩展名（.pdf/.txt/
.md/...）」的文件**静默放行**（只留一条 WARNING 审计）：agent 的 ``read_file`` /
``read_exam_paper`` 因此可以不经任何用户同意，读取工作区外任意白名单后缀文件
（桌面真题 PDF、他人目录里的 .md/.txt）。豁免的初衷（/img 拍照批改）合理，但
"静默" 二字与审批模型冲突 —— 用户既看不到、也无法拒绝。

修复（默认拒绝 + 交互授权 + 进程级目录记忆）
--------------------------------------------
① **默认拒绝**：白名单后缀只是「可授权候选」，未授权一律抛 ``SecurityException``
   （带 ``external_read_candidate`` 标记，供上层识别）；敏感路径 / 凭据目录 /
   相对穿越 / 非白名单后缀（如 .json）**永不可授权**（前置拒绝不带标记）。
② **交互授权**：``read_file`` / ``read_exam_paper`` 读取入口先过
   ``PermissionManager.check_external_read`` —— headless（管道 / CI / GUI 无人
   应答）一律拒绝并给两条出路（配置白名单 / 交互终端重试）；交互通道弹一次卡，
   批准后按**目录**记忆（同目录后续文件免再问），拒绝则不记忆。
③ **授权记忆不落盘**：只存在于进程内存（``_SESSION_AUTHORIZED_READ_DIRS``），
   与 B2a 的 ``_SESSION_WRITTEN_FILES`` 同款约定；且是**进程级**（GUI 每条消息
   新建 AgentRunner/Sandbox，实例级集合会被清空）。
④ **配置白名单**：``ky_config.json`` 的 ``agent.allowed_extra_paths`` 目录直接
   放行、不弹卡（``AgentRunner._resolve_extra_paths`` 解析，fail-safe 回 []）。
⑤ **会话汇总**：放行过的外部读取记入 ``session_external_reads``（去重保序），
   REPL 退出时打印 —— 含白名单目录的读取（它不弹卡，但同样要可见）。

测试策略
--------
* 全部用 ``tmp_path`` 造真实文件，不写真实仓库；夹具词中性化。
* 审批通道用 ``_FakeApprovalChannel``（A3a 协议测试替身，与 B2a 测试同款写法），
  记录每次请求并按预设答复返回；[a] 信任选项由 ``remember=True`` 模拟。
* 每条守卫配**阴性对照**：摘掉授权检查后同一读取必须真的放行，证明断言确实
  指向该守卫（而不是路径写错 / 权限层先拒等旁因）。
"""

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(REPO_ROOT), str(REPO_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools.agent import sandbox as sandbox_mod  # noqa: E402
from tools.agent.approval import session_remember_key  # noqa: E402
from tools.agent.permissions import (  # noqa: E402
    EXTERNAL_READ_TOOL_NAME,
    PermissionLevel,
    PermissionManager,
)
from tools.agent.sandbox import Sandbox, SecurityException  # noqa: E402
from tools.agent.tools_impl import ToolRegistry  # noqa: E402

#: 工作区外文件的内容哨兵：出现在任何"被拒绝"的返回值里即为泄漏。
OUTSIDE_CONTENT = "B2B_OUTSIDE_CONTENT_7d2e"
#: 同目录第二个文件的内容哨兵。
SECOND_CONTENT = "B2B_SECOND_FILE_31ab"


# ───────────────────────── 公共工具 ─────────────────────────

@pytest.fixture(autouse=True)
def _isolate_process_wide_state(monkeypatch):
    """授权目录集合与读取记录都是**进程级**全局（GUI 每条消息新建 runner，
    见 sandbox.py 模块级说明）：逐条用例换成全新对象，既不依赖也不污染同进程
    里的其他测试。"""
    monkeypatch.setattr(sandbox_mod, "_SESSION_AUTHORIZED_READ_DIRS", set())
    monkeypatch.setattr(sandbox_mod, "_SESSION_EXTERNAL_READS", [])


@pytest.fixture
def ws(tmp_path):
    """独立工作区（与真实仓库完全隔离）。"""
    root = tmp_path / "ws"
    root.mkdir()
    return root


@pytest.fixture
def outside(tmp_path):
    """沙箱根之外的资料目录：两个白名单后缀文件 + 一个 .json。"""
    d = tmp_path / "outside"
    d.mkdir()
    (d / "notes.md").write_text(OUTSIDE_CONTENT + "\n", encoding="utf-8")
    (d / "photo.txt").write_text(SECOND_CONTENT + "\n", encoding="utf-8")
    (d / "creds.json").write_text('{"k":"B2B_JSON_SENTINEL"}', encoding="utf-8")
    return d


class _FakeApprovalChannel:
    """假审批通道（A3a 通道协议测试替身）：记录请求，按预设答复返回。

    ``remember=True`` 模拟用户在卡片上选 [a]「本会话记住并信任此类操作」：
    把 ``session_remember_key(tool_name)`` 写进**共享的** session_allowed_tools
    （与 TtyApproval 的 [a] 分支同款），此后同类操作不再弹卡。
    """

    def __init__(self, approve=True, remember=False, reason="用户批准单次执行"):
        self.is_interactive = True
        self.session_allowed_tools = set()
        self.requests = []
        self._approve = approve
        self._remember = remember
        self._reason = reason

    def request(self, tool_name, level, tool_args):
        self.requests.append((tool_name, level, dict(tool_args)))
        if self._approve and self._remember:
            self.session_allowed_tools.add(session_remember_key(tool_name))
        return (self._approve, self._reason)

    def request_plan(self, tool_name, level, tool_args):
        return self.request(tool_name, level, tool_args)


class _FakeStdin:
    """假 stdin：只回答 ``isatty()``（通道选择用）。"""

    def __init__(self, tty):
        self._tty = tty

    def isatty(self):
        return self._tty


def _feed_input(monkeypatch, answers):
    """按序喂给 ``input()``；用尽后抛断言错误（防实现多问一次）。"""
    it = iter(answers)

    def _fake_input(*_a, **_k):
        try:
            return next(it)
        except StopIteration:  # pragma: no cover - 只有实现回归才会走到
            raise AssertionError("实现调用了比预期更多的 input()")

    monkeypatch.setattr("builtins.input", _fake_input)


def _registry(ws, *, mode="auto", approval_channel=None, extra_paths=None):
    perm = PermissionManager(mode=mode, workspace_root=ws,
                             approval_channel=approval_channel)
    return ToolRegistry(
        Sandbox(workspace_root=ws, allowed_extra_paths=extra_paths), perm)


def _build_pdf(marker: str) -> bytes:
    """构造一份 pypdf 可解析的最小单页 PDF（页面文本为 marker）。

    手写 xref 偏移，避免依赖 pypdf 的写能力；``read_exam_paper`` 的 PDF 提取
    才能真的返回内容，让"批准后可读"的断言有实据。
    """
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
         b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"),
        ("<< /Length %d >>\nstream\nBT /F1 12 Tf 72 720 Td (%s) Tj ET\nendstream"
         % (len(marker) + 37, marker)).encode("ascii"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref_off = len(out)
    out += b"xref\n0 %d\n" % (len(objs) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, xref_off))
    return bytes(out)


def _snapshot(root: Path) -> dict:
    """工作区字节指纹（文件 → 内容）。"""
    out = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            p = Path(dirpath) / fn
            out[str(p.relative_to(root)).replace("\\", "/")] = p.read_bytes()
    return out


# ═══════════ ① headless：默认拒绝 + 两条出路引导 ═══════════

def test_headless_read_file_external_is_denied_with_guidance(ws, outside):
    """核心现场：headless 下读取工作区外 .md 必须被拒，文案给出两条出路。

    auto 模式对 Level 0 只读是自动放行的 —— 这条断言正是"auto 不得覆盖读取闸门"。
    """
    reg = _registry(ws, mode="auto")

    out = reg.execute_tool("read_file", {"path": str(outside / "notes.md")},
                           interactive=False)

    assert ("SecurityError" in out or "PermissionDenied" in out), out
    assert "拒绝读取工作区外文件" in out, f"缺少可辨识关键词: {out!r}"
    assert "allowed_extra_paths" in out, f"缺少长期授权引导: {out!r}"
    assert "交互终端" in out, f"缺少交互授权引导: {out!r}"
    assert OUTSIDE_CONTENT not in out, f"被拒的读取泄漏了文件内容: {out!r}"


def test_check_external_read_headless_unit_guidance(ws):
    """策略层单测：headless 返回 False，理由同时含配置与交互两条出路。"""
    perm = PermissionManager(mode="auto", workspace_root=ws)

    allowed, reason = perm.check_external_read(
        "D:/somewhere/notes.md", {"path": "D:/somewhere/notes.md"}, interactive=False)

    assert allowed is False, reason
    assert "allowed_extra_paths" in reason, reason
    assert "交互终端" in reason, reason


# ═══════════ ② classify：可授权候选 vs 永不可授权 ═══════════

def test_classify_external_read_marks_whitelisted_candidate(ws, outside):
    """白名单后缀 + 已存在 + 文件 + 只读 = 可授权候选（返回解析后路径）。"""
    sb = Sandbox(workspace_root=ws)

    cand = sb.classify_external_read(str(outside / "notes.md"))

    assert cand is not None, "白名单后缀的外部文件应被判为可授权候选"
    assert Path(cand) == (outside / "notes.md").resolve()


def test_classify_external_read_none_inside_workspace(ws):
    """工作区内文件不是"外部读取"：classify 返回 None（直接可读，无需授权）。"""
    sb = Sandbox(workspace_root=ws)
    (ws / "note.md").write_text("inside\n", encoding="utf-8")

    assert sb.classify_external_read(str(ws / "note.md")) is None


def test_classify_external_read_none_for_nonexistent_external(ws, tmp_path):
    """不存在的路径不是可授权候选（避免"先批准后探测"式的路径探测）。"""
    sb = Sandbox(workspace_root=ws)

    assert sb.classify_external_read(str(tmp_path / "outside" / "ghost.md")) is None


# ═══════════ ③ 交互授权：批准 / 目录记忆 / 不落盘 ═══════════

def test_fake_channel_approval_reads_content(ws, outside):
    """批准 → 读到内容；审批请求用统一键、Level 4，且带 scope 说明。"""
    fake = _FakeApprovalChannel(approve=True)
    reg = _registry(ws, mode="auto", approval_channel=fake)

    out = reg.execute_tool("read_file", {"path": str(outside / "notes.md")},
                           interactive=False)

    assert OUTSIDE_CONTENT in out, out
    assert len(fake.requests) == 1, fake.requests
    tool_name, level, tool_args = fake.requests[0]
    assert tool_name == EXTERNAL_READ_TOOL_NAME, tool_name
    assert level == PermissionLevel.NETWORK, level
    assert "notes.md" in str(tool_args.get("path", "")), tool_args
    assert "scope" in tool_args, tool_args


def test_same_dir_second_file_does_not_ask_again(ws, outside):
    """批准一次 = 本会话内该目录免再弹卡（目录级记忆）。"""
    fake = _FakeApprovalChannel(approve=True)
    reg = _registry(ws, mode="auto", approval_channel=fake)

    out1 = reg.execute_tool("read_file", {"path": str(outside / "notes.md")},
                            interactive=False)
    assert OUTSIDE_CONTENT in out1, out1

    out2 = reg.execute_tool("read_file", {"path": str(outside / "photo.txt")},
                            interactive=False)
    assert SECOND_CONTENT in out2, out2
    assert len(fake.requests) == 1, f"同目录第二个文件不应再弹卡: {fake.requests}"


def test_approval_leaves_no_auth_state_file_in_workspace(ws, outside):
    """批准后工作区不得多出任何文件（授权状态只存在于进程内存，不落盘）。"""
    fake = _FakeApprovalChannel(approve=True)
    reg = _registry(ws, mode="auto", approval_channel=fake)
    before = _snapshot(ws)

    out = reg.execute_tool("read_file", {"path": str(outside / "notes.md")},
                           interactive=False)
    assert OUTSIDE_CONTENT in out, out

    new_files = set(_snapshot(ws)) - set(before)
    assert new_files == set(), (
        f"批准流程在工作区写出了文件（授权状态疑似落盘）: {sorted(new_files)}")


# ═══════════ ④ 拒绝：无内容泄漏、不产生记忆 ═══════════

def test_fake_channel_denial_blocks_and_no_leak(ws, outside):
    """拒绝 → 不读、不泄漏内容、不留下授权记忆（再次读取仍要弹卡）。"""
    fake = _FakeApprovalChannel(approve=False, reason="用户拒绝执行该操作")
    reg = _registry(ws, mode="auto", approval_channel=fake)

    out = reg.execute_tool("read_file", {"path": str(outside / "notes.md")},
                           interactive=False)
    assert "拒绝读取工作区外文件" in out, out
    assert "用户拒绝执行该操作" in out, out
    assert OUTSIDE_CONTENT not in out, out
    assert len(fake.requests) == 1, fake.requests

    out2 = reg.execute_tool("read_file", {"path": str(outside / "notes.md")},
                            interactive=False)
    assert len(fake.requests) == 2, "被拒的读取不得产生授权记忆"
    assert OUTSIDE_CONTENT not in out2, out2


# ═══════════ ⑤ [a] 信任：一次批准，全部外部读取免卡 ═══════════

def test_remember_option_trusts_all_external_reads(ws, outside, tmp_path):
    """用户选 [a] 后，连"从未授权过的新目录"也直接放行、不再弹卡。"""
    fake = _FakeApprovalChannel(approve=True, remember=True)
    reg = _registry(ws, mode="auto", approval_channel=fake)

    out1 = reg.execute_tool("read_file", {"path": str(outside / "notes.md")},
                            interactive=False)
    assert OUTSIDE_CONTENT in out1, out1
    assert len(fake.requests) == 1, fake.requests

    other = tmp_path / "other"
    other.mkdir()
    (other / "memo.md").write_text("B2B_OTHER_DIR_MEMO_55c3\n", encoding="utf-8")
    out2 = reg.execute_tool("read_file", {"path": str(other / "memo.md")},
                            interactive=False)
    assert "B2B_OTHER_DIR_MEMO_55c3" in out2, out2
    assert len(fake.requests) == 1, f"[a] 信任后不应再弹卡: {fake.requests}"


def test_check_external_read_trust_set_short_circuits_headless(ws):
    """策略层：信任集命中时先于 headless 判定返回放行（[a] 的语义）。"""
    perm = PermissionManager(mode="ask", workspace_root=ws)
    perm.session_allowed_tools.add(EXTERNAL_READ_TOOL_NAME)

    allowed, _reason = perm.check_external_read(
        "D:/somewhere/notes.md", {"path": "D:/somewhere/notes.md"}, interactive=False)

    assert allowed is True


# ═══════════ ⑥ 配置白名单：allowed_extra_paths 直接放行不弹卡 ═══════════

def test_allowed_extra_paths_dir_allows_without_card(ws, outside):
    """配置内目录直接放行，审批通道一次都不该被问到。"""
    fake = _FakeApprovalChannel(approve=False)   # 通道会拒绝：因此必须不经过它
    reg = _registry(ws, mode="auto", approval_channel=fake,
                    extra_paths=[str(outside)])

    out = reg.execute_tool("read_file", {"path": str(outside / "notes.md")},
                           interactive=False)

    assert OUTSIDE_CONTENT in out, out
    assert fake.requests == [], f"配置白名单目录不应弹卡: {fake.requests}"


def test_allowed_extra_paths_dir_allows_headless(ws, outside):
    """headless（无审批通道）下，配置内目录同样直接放行。"""
    reg = _registry(ws, mode="auto", extra_paths=[str(outside)])

    out = reg.execute_tool("read_file", {"path": str(outside / "notes.md")},
                           interactive=False)

    assert OUTSIDE_CONTENT in out, out


def test_agent_runner_resolves_allowed_extra_paths_failsafe():
    """``AgentRunner._resolve_extra_paths``：只收非空字符串，异常/缺失一律回 []。"""
    from tools.agent.loop import AgentRunner

    assert AgentRunner._resolve_extra_paths(
        {"agent": {"allowed_extra_paths": ["D:/资料", "", 3, None]}}) == ["D:/资料"]
    assert AgentRunner._resolve_extra_paths({}) == []
    assert AgentRunner._resolve_extra_paths(None) == []
    assert AgentRunner._resolve_extra_paths({"agent": "not-a-dict"}) == []
    assert AgentRunner._resolve_extra_paths(
        {"agent": {"allowed_extra_paths": "D:/not-a-list"}}) == []


# ═══════════ ⑦ safe 模式：拒绝且不弹卡 ═══════════

def test_safe_mode_denies_external_read_without_card(ws, outside):
    """safe 模式（严格只读）下外部读取直接拒绝，不弹卡、不泄漏。"""
    fake = _FakeApprovalChannel(approve=True)
    reg = _registry(ws, mode="safe", approval_channel=fake)

    out = reg.execute_tool("read_file", {"path": str(outside / "notes.md")},
                           interactive=False)

    assert "拒绝读取工作区外文件" in out, out
    assert ("严格安全模式" in out or "safe" in out), out
    assert fake.requests == [], f"safe 模式不应弹卡: {fake.requests}"
    assert OUTSIDE_CONTENT not in out, out


def test_check_external_read_safe_mode_unit(ws):
    """策略层：safe 模式即便声明可交互也直接拒绝（不弹卡）。"""
    perm = PermissionManager(mode="safe", workspace_root=ws)

    allowed, reason = perm.check_external_read(
        "D:/somewhere/notes.md", {"path": "D:/somewhere/notes.md"}, interactive=True)

    assert allowed is False, reason
    assert ("严格安全模式" in reason or "safe" in reason), reason


# ═══════════ ⑧ 永不可授权：凭据 / 穿越 / 非白名单后缀 ═══════════

def test_ssh_credential_file_cannot_be_authorized(ws, tmp_path):
    """白名单后缀但位于 .ssh 凭据目录：不可成为候选，登记授权也拒。"""
    ssh_dir = tmp_path / "home" / ".ssh"
    ssh_dir.mkdir(parents=True)
    secret = ssh_dir / "notes.md"
    secret.write_text("B2B_SSH_SENTINEL_88f1\n", encoding="utf-8")

    sb = Sandbox(workspace_root=ws)
    assert sb.classify_external_read(str(secret)) is None, \
        "凭据目录不得成为可授权候选"

    sb.register_authorized_read_dir(ssh_dir)   # 即便强行登记授权目录
    with pytest.raises(SecurityException):
        sb.resolve_safe_path(str(secret), read_only=True)


def test_relative_traversal_cannot_be_authorized(ws, outside):
    """相对穿越（../outside/notes.md）不可成为候选，登记授权也拒。"""
    sb = Sandbox(workspace_root=ws)
    rel = os.path.relpath(outside / "notes.md", ws)

    assert sb.classify_external_read(rel) is None, "相对穿越不得成为可授权候选"

    sb.register_authorized_read_dir(outside)   # 即便登记了目标目录
    with pytest.raises(SecurityException):
        sb.resolve_safe_path(rel, read_only=True)


def test_json_file_cannot_be_authorized(ws, outside):
    """.json 被刻意排除（防凭据外泄）：不可成为候选，登记授权也拒。"""
    sb = Sandbox(workspace_root=ws)

    assert sb.classify_external_read(str(outside / "creds.json")) is None

    sb.register_authorized_read_dir(outside)
    with pytest.raises(SecurityException):
        sb.resolve_safe_path(str(outside / "creds.json"), read_only=True)


def test_json_external_read_is_denied_without_card(ws, outside):
    """端到端：外部 .json 直接拒绝、不弹卡（永不可授权，不给批准口子）。"""
    fake = _FakeApprovalChannel(approve=True)
    reg = _registry(ws, mode="auto", approval_channel=fake)

    out = reg.execute_tool("read_file", {"path": str(outside / "creds.json")},
                           interactive=False)

    assert "SecurityError" in out, out
    assert fake.requests == [], f"永不可授权路径不得弹卡: {fake.requests}"
    assert "B2B_JSON_SENTINEL" not in out, out


# ═══════════ ⑨ 阴性对照：摘掉授权检查后必须真的放行 ═══════════

def test_negative_control_without_authorization_check_read_succeeds(
        ws, outside, monkeypatch):
    """阴性对照：同一个 registry、同一个文件，只摘掉授权检查这一个变量。

    先证"未摘除时确实被拦"，再证"摘除后确实读到" —— 排除断言因路径写错、
    权限层先拒等旁因而假通过。
    """
    reg = _registry(ws, mode="auto")

    denied = reg.execute_tool("read_file", {"path": str(outside / "notes.md")},
                              interactive=False)
    assert "拒绝读取工作区外文件" in denied, f"对照前提不成立（闸门未生效）: {denied!r}"
    assert OUTSIDE_CONTENT not in denied, denied

    monkeypatch.setattr(Sandbox, "is_authorized_read_dir", lambda self, p: True)

    allowed = reg.execute_tool("read_file", {"path": str(outside / "notes.md")},
                               interactive=False)
    assert OUTSIDE_CONTENT in allowed, (
        f"摘掉授权检查后仍未读到，说明断言没指向该守卫: {allowed!r}")


# ═══════════ ⑩ 进程级共享 + 目录边界匹配 ═══════════

def test_authorized_dirs_shared_across_sandboxes_in_process(ws, outside):
    """GUI 场景：每条消息新建 AgentRunner/Sandbox，授权目录必须跨实例共享。

    否则「第 1 条消息批准、第 2 条消息再读同目录」就要反复弹卡（B2a 同款约定）。
    """
    sb1 = Sandbox(workspace_root=ws)
    sb1.register_authorized_read_dir(outside)

    sb2 = Sandbox(workspace_root=ws)   # 模拟 GUI 的下一条消息：全新 runner/sandbox
    assert sb2.is_authorized_read_dir(outside / "notes.md") is True
    assert sb2.resolve_safe_path(str(outside / "notes.md"), read_only=True) \
        == (outside / "notes.md").resolve()


def test_authorized_dir_matching_requires_separator_boundary(ws, tmp_path):
    """前缀相同的兄弟目录不得被放行（匹配必须带分隔符边界）。"""
    base = tmp_path / "资料"
    sibling = tmp_path / "资料私密"
    base.mkdir()
    sibling.mkdir()
    (sibling / "x.md").write_text("B2B_SIBLING_SENTINEL_2f7a\n", encoding="utf-8")

    sb = Sandbox(workspace_root=ws)
    sb.register_authorized_read_dir(base)

    assert sb.is_authorized_read_dir(base / "ok.md") is True
    assert sb.is_authorized_read_dir(sibling / "x.md") is False, \
        "前缀相同的兄弟目录不得被误放行"
    with pytest.raises(SecurityException):
        sb.resolve_safe_path(str(sibling / "x.md"), read_only=True)


# ═══════════ ⑪ 会话汇总：session_external_reads ═══════════

def test_session_external_reads_records_approved_reads(ws, outside):
    """批准读取后记入会话汇总；同文件重复读取去重。"""
    fake = _FakeApprovalChannel(approve=True)
    reg = _registry(ws, mode="auto", approval_channel=fake)
    assert reg.sandbox.session_external_reads == []

    reg.execute_tool("read_file", {"path": str(outside / "notes.md")},
                     interactive=False)
    reads = reg.sandbox.session_external_reads
    assert str((outside / "notes.md").resolve()) in reads, reads

    reg.execute_tool("read_file", {"path": str(outside / "notes.md")},
                     interactive=False)
    assert len(reg.sandbox.session_external_reads) == 1, "同一文件应去重保序"


def test_session_external_reads_ignores_denied_reads(ws, outside):
    """被拒的读取不得进入会话汇总（记录只发生在实际放行之后）。"""
    fake = _FakeApprovalChannel(approve=False)
    reg = _registry(ws, mode="auto", approval_channel=fake)

    reg.execute_tool("read_file", {"path": str(outside / "notes.md")},
                     interactive=False)

    assert reg.sandbox.session_external_reads == []


def test_session_external_reads_includes_extra_paths_reads(ws, outside):
    """白名单目录读取不弹卡，但同样计入会话汇总（清单必须完整）。"""
    reg = _registry(ws, mode="auto", extra_paths=[str(outside)])

    reg.execute_tool("read_file", {"path": str(outside / "notes.md")},
                     interactive=False)

    assert str((outside / "notes.md").resolve()) in reg.sandbox.session_external_reads


# ═══════════ ⑫ read_exam_paper：同样接入闸门 ═══════════

def test_read_exam_paper_denies_external_pdf_headless(ws, outside):
    """headless 下读工作区外真题 PDF 被拒，且不泄漏 PDF 内容。"""
    pdf = outside / "paper.pdf"
    pdf.write_bytes(_build_pdf("B2B_PAPER_MARKER_41c9"))
    reg = _registry(ws, mode="auto")

    out = reg.execute_tool("read_exam_paper", {"pdf_path": str(pdf)},
                           interactive=False)

    assert ("SecurityError" in out or "PermissionDenied" in out), out
    assert "拒绝读取工作区外文件" in out, out
    assert "B2B_PAPER_MARKER_41c9" not in out, f"被拒的读取泄漏了 PDF 内容: {out!r}"


def test_read_exam_paper_approved_passes_gate_and_extracts(ws, outside):
    """批准后真题 PDF 正常提取（原「读工作区外 PDF」场景在有授权时回归通过）。"""
    pdf = outside / "paper.pdf"
    pdf.write_bytes(_build_pdf("B2B_PAPER_MARKER_41c9"))
    fake = _FakeApprovalChannel(approve=True)
    reg = _registry(ws, mode="auto", approval_channel=fake)

    out = reg.execute_tool("read_exam_paper", {"pdf_path": str(pdf)},
                           interactive=False)

    assert "拒绝读取工作区外文件" not in out, out
    assert "PermissionDenied" not in out, out
    assert len(fake.requests) == 1, fake.requests
    assert fake.requests[0][0] == EXTERNAL_READ_TOOL_NAME, fake.requests
    assert "B2B_PAPER_MARKER_41c9" in out, f"批准后应提取到 PDF 内容: {out!r}"
    assert str(pdf.resolve()) in reg.sandbox.session_external_reads


# ═══════════ ⑬ 防误伤：常量与工作区内读取 ═══════════

def test_external_read_tool_name_constant():
    """审批键必须是模块级常量（便于测试引用与审计）。"""
    assert EXTERNAL_READ_TOOL_NAME == "read_file@external"


def test_tty_card_renders_and_approves(ws, outside, monkeypatch, capsys):
    """真实 TTY 通道：渲染 Level 4 审批卡（带 [a] 选项），输入 y 后读取放行。"""
    monkeypatch.setattr(sys, "stdin", _FakeStdin(True))
    _feed_input(monkeypatch, ["y"])
    reg = _registry(ws, mode="auto")

    out = reg.execute_tool("read_file", {"path": str(outside / "notes.md")},
                           interactive=True)

    card = capsys.readouterr().out
    assert "🛡️ [权限审批] 智能私教请求调用外部工具:" in card, card
    assert EXTERNAL_READ_TOOL_NAME in card, card
    assert "[a] 本会话记住并信任此类操作" in card, "Level 4 卡片应带 [a] 信任选项"
    assert "极其谨慎核对" not in card, "外部只读不应按 Level 5 高危卡片渲染"
    assert OUTSIDE_CONTENT in out, out


def test_tty_card_remember_option_skips_second_card(ws, outside, tmp_path,
                                                    monkeypatch, capsys):
    """真实 TTY 通道选 [a]：连从未授权过的新目录也不再弹卡。

    ``_feed_input`` 只提供一次输入 —— 若实现再次弹卡，假 input 会抛
    AssertionError（比"计数为 1"更强：连多问一次都不允许）。
    """
    monkeypatch.setattr(sys, "stdin", _FakeStdin(True))
    _feed_input(monkeypatch, ["a"])
    reg = _registry(ws, mode="auto")

    out1 = reg.execute_tool("read_file", {"path": str(outside / "notes.md")},
                            interactive=True)
    capsys.readouterr()
    assert OUTSIDE_CONTENT in out1, out1

    other = tmp_path / "other"
    other.mkdir()
    (other / "memo.md").write_text("B2B_TTY_REMEMBER_9c4d\n", encoding="utf-8")
    out2 = reg.execute_tool("read_file", {"path": str(other / "memo.md")},
                            interactive=True)
    assert "B2B_TTY_REMEMBER_9c4d" in out2, out2


def test_workspace_internal_read_unaffected(ws):
    """正常用法行为不变：工作区内读取照常，不弹卡。"""
    fake = _FakeApprovalChannel(approve=True)
    reg = _registry(ws, mode="auto", approval_channel=fake)
    (ws / "inside.md").write_text("B2B_INSIDE_OK_91de\n", encoding="utf-8")

    out = reg.execute_tool("read_file", {"path": "inside.md"}, interactive=False)

    assert "B2B_INSIDE_OK_91de" in out, out
    assert fake.requests == [], fake.requests
