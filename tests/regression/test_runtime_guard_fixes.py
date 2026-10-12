# -*- coding: utf-8 -*-
"""# [Regress] 运行时守卫回归：院校解析长度守卫 / TUI 数字键 / ky init 注册 / LLM 回环放行。
修复来源：2026-10-11《第五轮深审报告》（针对远端 af42f05，见
`桌面/考研学习chain分析报告/kaoyan_chain_第五轮深审报告_2026-10-11.md`）。覆盖：

  P0-1 院校解析裸子串误命中：resolve("华")→兵器研究院、resolve("大学")→
       最长校名、2 万字符垃圾串命中/合成 —— 加锚定长度守卫（第 4/5/6 步）。
  P0-3 UI：ky init 注册 / TUI 数字键 10·11·12 两击序列 / "去报到"错别字。
  P1：llm_client 回环放行（本地 Ollama 与文档对齐）。

隔离：不联网、不写真实工作区（全部 tmp_path）；文本断言只针对本批新增行为。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ════════════════════════════════════════════════════════════════
# 1. 院校解析长度守卫（P0-1）
# ════════════════════════════════════════════════════════════════

class TestRegistryResolveGuards:
    """resolve() 的锚定/长度守卫：单汉字、通用词、超长串一律拒绝。"""

    @staticmethod
    def _registry():
        from tools.intelligence.registry import UniversityRegistry
        return UniversityRegistry()

    def test_single_hanzi_rejected(self):
        """[红线] 单汉字不得命中任何实体。

        修复前实测：resolve("华") → 中国兵器科学研究院(陕西青华机电研究所)，
        resolve("理") → 中国兵器科学研究院(陕西应用物理化学研究所) ——
        下游还会给误命中实体贴 [LOCAL_DB_VERIFIED] 信任标签。
        阴性对照：去掉 `len(q_lower) >= 3` 守卫 → 本用例红。
        """
        reg = self._registry()
        for q in ("华", "理", "清", "北", "h", "x"):
            assert reg.resolve(q) is None, f"{q!r} 不应命中实体"

    def test_generic_word_rejected(self):
        """[红线] 「大学」「学院」「研究院」等通用词不得命中/合成。

        修复前实测：resolve("大学") → 北京师范大学-香港浸会大学联合国际学院
        （名称子串方向取最长者）；加 2 字守卫后又被第 6 步启发式合成接住
        （合成出 name="大学" 的实体）→ 合成入口加 >=4 字守卫；3 字通用词
        （「研究院」「研究所」）同样会命中最长校名 → 名称子串方向守卫定为 4 字。
        """
        reg = self._registry()
        for q in ("大学", "学院", "学校", "研究院", "研究所"):
            ent = reg.resolve(q)
            assert ent is None, f"{q!r} 不应命中，实测 {getattr(ent, 'name', None)}"

    def test_long_garbage_rejected(self):
        """[红线] 2 万字符垃圾串（含校名/含「大学」）不得命中或合成。"""
        reg = self._registry()
        garbage_with_school = "x" * 20000 + "华中科技大学" + "y" * 20000
        garbage_with_word = "x" * 20000 + "大学" + "y" * 20000
        assert reg.resolve(garbage_with_school) is None
        assert reg.resolve(garbage_with_word) is None

    def test_legit_inputs_still_resolve(self):
        """正常路径不受守卫影响：全称/简称/别名/缩写/自然语言/未收录合成。"""
        reg = self._registry()
        assert reg.resolve("华中科技大学").name == "华中科技大学"
        assert reg.resolve("华中科技").name == "华中科技大学"
        assert reg.resolve("华科").name == "华中科技大学"
        assert reg.resolve("hust").name == "华中科技大学"
        assert reg.resolve("清华").name == "清华大学"
        assert reg.resolve("我想考华中科技大学").name == "华中科技大学"
        # 未收录院校（>=4 字含「大学」）仍走启发式合成
        ent = reg.resolve("石家庄铁道大学")
        assert ent is not None and ent.name == "石家庄铁道大学"


# ════════════════════════════════════════════════════════════════
# 2. TUI 数字键 10/11/12（P0-3）
# ════════════════════════════════════════════════════════════════

class TestTuiTwoStrokeDigits:
    """action_run_digit 的两击序列：1→0=10、1→1=11、1→2=12。"""

    @staticmethod
    def _app():
        from tools.tui.app import KaoyanTUI
        app = KaoyanTUI(workspace_root=ROOT)
        dispatched = []
        app._dispatch_menu_key = lambda key: dispatched.append(key)  # type: ignore
        app.set_timer = lambda *a, **k: None                         # type: ignore
        return app, dispatched

    def test_one_one_dispatches_11(self):
        """[红线] 按 1、1 → 派发 11（学科报到）。

        修复前：第二次 1 走「digit != 0 → 补跑动作 1」分支，且再挂起一次
        → 动作 1 被派发两次、「11」永远不可达。阴性对照：还原硬编码
        "10" 判据 → 本用例红。
        """
        app, dispatched = self._app()
        app.action_run_digit("1")
        assert app._pending_digit == "1"
        app.action_run_digit("1")
        assert dispatched == ["11"]
        assert app._pending_digit == ""

    def test_one_two_dispatches_12(self):
        """按 1、2 → 派发 12（交作业）。"""
        app, dispatched = self._app()
        app.action_run_digit("1")
        app.action_run_digit("2")
        assert dispatched == ["12"]

    def test_one_zero_still_dispatches_10(self):
        """按 1、0 → 派发 10（既有行为不变）。"""
        app, dispatched = self._app()
        app.action_run_digit("1")
        app.action_run_digit("0")
        assert dispatched == ["10"]

    def test_one_then_other_runs_both(self):
        """按 1、5 → 补跑 1 再派发 5（既有语义不变）。"""
        app, dispatched = self._app()
        app.action_run_digit("1")
        app.action_run_digit("5")
        assert dispatched == ["1", "5"]

    def test_plain_digit_still_direct(self):
        """非 1 开头的数字键仍单键直达。"""
        app, dispatched = self._app()
        app.action_run_digit("7")
        assert dispatched == ["7"]


# ════════════════════════════════════════════════════════════════
# 3. ky init 命令注册（P0-3）
# ════════════════════════════════════════════════════════════════

class TestKyInitRegistered:
    """`ky init` 已注册且指向初始化向导。"""

    def test_init_registered(self):
        """[红线] 命令注册表含 init（修复前 `ky init` 报「未知命令」）。"""
        from tools.cli import dispatch
        dispatch._init_all_commands()
        cmd = dispatch.get_command("init")
        assert cmd is not None, "ky init 未注册"
        assert "--init" in cmd.aliases

    def test_init_forwards_to_wizard(self, monkeypatch: pytest.MonkeyPatch):
        """init handler 转发 init_workspace.main()（参数透传 + argv 还原）。"""
        import tools.cli.commands.system as system_mod
        from tools import init_workspace

        called = {}

        def fake_main():
            called["argv"] = list(sys.argv)

        monkeypatch.setattr(init_workspace, "main", fake_main)
        _saved = list(sys.argv)
        system_mod._cmd_init(["init", "--quick"])
        assert called["argv"] == ["ky init", "--quick"]
        assert sys.argv == _saved, "argv 必须还原"


# ════════════════════════════════════════════════════════════════
# 4. llm_client 回环放行（P1 · 本地 Ollama 对齐）
# ════════════════════════════════════════════════════════════════

class TestLlmLoopbackBypass:
    """回环 base_url 经 safe_urlopen(allow_loopback=True) 放行；非回环 fail-closed。"""

    def test_loopback_allowed_via_allow_loopback(self, monkeypatch: pytest.MonkeyPatch):
        """[红线] http://localhost:11434/v1 → safe_urlopen(allow_loopback=True)。

        修复前：safe_urlopen 对回环 fail-closed，README 承诺的本地 Ollama
        一律不可用（文档与实现冲突）。[2026-10-11 适配·lint 门禁] 实现从
        「绕过通道直连 urllib.request.urlopen」改为「net_guard 既有的
        allow_loopback 参数」——回环请求同样受重定向复核 / 跨主机剥
        Authorization / IP pin 保护，且不再触发 lint 规则 8（禁止裸
        urlopen）。阴性对照：allow_loopback 恒 False（去掉回环分支）→ 红。
        """
        import urllib.request as ur

        import tools.llm_client as lc

        seen = []

        def fake_safe(req: object, timeout: float | None = None, **kwargs: object):
            seen.append(kwargs.get("allow_loopback"))
            return "OK"

        monkeypatch.setattr(lc, "safe_urlopen", fake_safe)

        for url in ("http://localhost:11434/v1/chat/completions",
                    "http://127.0.0.1:8080/v1/chat/completions",
                    "http://[::1]:11434/v1/models"):
            req = ur.Request(url)
            assert lc._llm_urlopen(req) == "OK"
        assert seen == [True, True, True]

    def test_non_loopback_uses_safe(self, monkeypatch: pytest.MonkeyPatch):
        """阴性对照：公网 base_url 走 safe_urlopen 且 allow_loopback=False。"""
        import urllib.request as ur

        import tools.llm_client as lc

        seen = []

        def fake_safe(req: object, timeout: float | None = None, **kwargs: object):
            seen.append(kwargs.get("allow_loopback"))
            return "SAFE"

        monkeypatch.setattr(lc, "safe_urlopen", fake_safe)
        req = ur.Request("https://api.deepseek.com/v1/chat/completions")
        assert lc._llm_urlopen(req) == "SAFE"
        assert seen == [False]

    def test_private_ip_still_blocked(self, monkeypatch: pytest.MonkeyPatch):
        """阴性对照：私网 IP（非回环）不放行——allow_loopback=False。"""
        import urllib.request as ur

        import tools.llm_client as lc

        seen = []

        def fake_safe(req: object, timeout: float | None = None, **kwargs: object):
            seen.append(kwargs.get("allow_loopback"))
            return "SAFE"

        monkeypatch.setattr(lc, "safe_urlopen", fake_safe)
        req = ur.Request("http://192.168.1.10:11434/v1/models")
        assert lc._llm_urlopen(req) == "SAFE"
        assert seen == [False]


# ════════════════════════════════════════════════════════════════
# 5. 杂项：错别字与文案
# ════════════════════════════════════════════════════════════════

class TestMiscTexts:
    def test_no_qubaodao_typo(self):
        """「去报道」错别字已修（应为「去报到」）。"""
        text = (ROOT / "tools" / "tui_navigator.py").read_text(encoding="utf-8")
        assert "去报道" not in text
        assert "去报到" in text

    def test_snapshot_hint_has_dir_context(self):
        """snapshot 提示的 python build.py 命令带目录语境（不再裸命令）。"""
        text = (ROOT / "05-考研看板" / "web" / "snapshot.py").read_text(encoding="utf-8")
        assert "cd 05-考研看板" in text
        assert "ky build" in text
