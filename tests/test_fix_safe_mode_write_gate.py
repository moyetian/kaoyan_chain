# -*- coding: utf-8 -*-
"""P0 回归 · ``--permission=safe`` 必须真正拦住写命令。

缺陷现场（实测复现）
--------------------
``tools/cli/shared.py`` 的 ``detect_safe_mode_violation()`` 原是一份**手写的写命令
黑名单**，末尾 ``return None`` ——「未列举即放行」(fail-open)。命令注册表里其实早
就有权威元数据 ``Command.write``（``tools/cli/commands/material.py`` 给 ``mount``
写的是 ``write=True``），但从未被查询过。

后果：``py tools/ky_cli.py --permission=safe mount`` 在声称「严格只读」的模式下
改写了 ``ky_config.json``（``study_plan.pro_books``）与 ``AGENTS.md``，生成备份
文件，并 ``exit=0`` 报告成功。

修复（两处根因）
----------------
1. **命令级门禁**（``tools/cli/shared.py``）：以注册表 ``Command.write`` 为唯一
   事实源；未注册命令 fail-closed 拒绝。「默认只读、加 ``--save`` 才落盘」
   （map/compare/diagnose/fetch/scout/admission/wechat）、「写命令里的只读子操作」
   （``key list`` / ``memory status``）、「默认只读、特定子命令才落盘」（``watch``）
   三张细分表只是对 ``write`` 元数据在子命令粒度的补充，不是第二套命令名单。
2. **双导入**（``tools/ky_io.py``）：``ky_io`` 与 ``tools.ky_io`` 是两个独立模块
   对象、各持一份 ``_READ_ONLY_MODE``。现 ``set_read_only_mode`` 同步所有别名、
   ``is_read_only_mode`` 跨别名 fail-closed 判定、新别名在导入期继承标志。

本文件是**枚举式**回归：遍历注册表里所有 ``write=True`` 的命令逐条断言。将来新增
写命令若忘了补样例句，``test_write_command_samples_are_complete`` 会立刻失败。
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(REPO_ROOT), str(REPO_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools.cli import dispatch  # noqa: E402
from tools.cli import shared  # noqa: E402

#: 每个「写命令」的一条**真实写形式**样例命令（用于枚举式拦截测试）。
#: 新增 write=True 命令时必须在此补一条，否则 test_write_command_samples_are_complete 失败。
WRITE_COMMAND_SAMPLES = {
    "build":       ["build"],
    "clawbot":     ["clawbot"],
    "config":      ["config"],
    "done":        ["done", "背单词"],
    "exam":        ["exam"],
    "exam-submit": ["exam-submit", "paper.md", "A B C"],
    "gui":         ["gui"],
    "ingest":      ["ingest", "paper.md"],
    "key":         ["key", "set", "PAPER-1", "1", "答案：B"],
    "memory":      ["memory", "prune"],
    "menu":        ["menu"],
    "mount":       ["mount"],
    "notify":      ["notify", "今日任务已推送"],
    "plan":        ["plan"],
    "relieve":     ["relieve"],
    "rollback":    ["rollback"],
    "session":     ["session", "fork", "abc"],
    "style":       ["style", "2"],
    "subject":     ["subject"],
    "variant":     ["variant", "导数定义"],
}


# ───────────────────────── 公共工具 ─────────────────────────

def _ky_io_aliases():
    """返回已加载的 ky_io 模块别名（双导入下通常有两个对象）。"""
    import importlib
    mods = []
    for name in ("tools.ky_io", "ky_io"):
        try:
            mods.append(importlib.import_module(name))
        except ImportError:
            continue
    return mods


def _set_read_only(enabled: bool) -> None:
    for mod in _ky_io_aliases():
        mod.set_read_only_mode(enabled)


def _write_command_names():
    dispatch._init_all_commands()
    return sorted(c.name for c in dispatch.list_commands() if c.write)


@pytest.fixture(autouse=True)
def _clean_read_only_flag():
    """dispatch.main / 测试体都可能置位全局只读标志，逐条复位，避免互相污染。"""
    _set_read_only(False)
    yield
    _set_read_only(False)


@pytest.fixture
def probe_write_handlers(monkeypatch, tmp_path):
    """把所有写命令的 handler 换成「记一笔 + 写哨兵文件」的探针。

    这样即使门禁失效、handler 被调用，也不会真的改写仓库（探针只写 tmp_path），
    同时留下可断言的证据。
    """
    dispatch._init_all_commands()
    calls = []

    def _make_probe(name):
        def _probe(args):
            calls.append(name)
            (tmp_path / f"__wrote_{name}.txt").write_text("x", encoding="utf-8")
            return 0
        return _probe

    for cmd in dispatch.list_commands():
        if cmd.write:
            monkeypatch.setattr(cmd, "handler", _make_probe(cmd.name))
    return calls


def _snapshot(root: Path) -> dict:
    """工作区字节指纹（忽略解释器自动生成的 __pycache__ / .pyc）。"""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if fn.endswith(".pyc"):
                continue
            p = Path(dirpath) / fn
            out[str(p.relative_to(root))] = p.read_bytes()
    return out


# ───────────────────────── 元数据完整性 ─────────────────────────

def test_write_command_samples_are_complete():
    """注册表里每个 write=True 命令都必须有样例；样例不得指向已不存在的命令。"""
    names = set(_write_command_names())
    samples = set(WRITE_COMMAND_SAMPLES)

    missing = sorted(names - samples)
    stale = sorted(samples - names)
    assert not missing, (
        f"新增了写命令却忘了补样例，枚举式拦截测试会漏测: {missing}。"
        f"请在 WRITE_COMMAND_SAMPLES 中补上一条真实写形式的命令。")
    assert not stale, f"WRITE_COMMAND_SAMPLES 里有已不存在的写命令: {stale}"
    assert names, "注册表里一个写命令都没有，测试失去意义（检查命令加载）"


# ───────────────────────── 门禁：枚举式拦截 ─────────────────────────

@pytest.mark.parametrize("name", sorted(WRITE_COMMAND_SAMPLES))
def test_gate_rejects_every_write_command(name):
    """每一个写命令在只读模式下都必须被判为违规。"""
    argv = WRITE_COMMAND_SAMPLES[name]
    violation = shared.detect_safe_mode_violation(argv)
    assert violation, f"只读模式下未拦住写命令: ky {' '.join(argv)}"


@pytest.mark.parametrize("name", sorted(WRITE_COMMAND_SAMPLES))
def test_dispatch_never_reaches_write_handler(name, probe_write_handlers, tmp_path):
    """端到端（in-process）：safe 模式下写命令必须以 exit=3 拒绝，handler 不得被调用。"""
    argv = WRITE_COMMAND_SAMPLES[name]
    with pytest.raises(SystemExit) as ei:
        dispatch.main(["--permission=safe"] + argv)
    assert ei.value.code == 3, f"ky {' '.join(argv)} 未以退出码 3 拒绝"
    assert name not in probe_write_handlers, (
        f"写命令 ky {' '.join(argv)} 的 handler 被调用了 —— 只读闸门失效")
    assert list(tmp_path.glob("__wrote_*.txt")) == [], "写命令的 handler 实际执行了"


def test_dispatch_rejects_all_write_commands_without_touching_workspace(
        probe_write_handlers, tmp_path):
    """一次性跑完全部写命令：全拒 + 工作区（tmp_path）字节不变。"""
    before = _snapshot(tmp_path)
    for name in sorted(WRITE_COMMAND_SAMPLES):
        with pytest.raises(SystemExit) as ei:
            dispatch.main(["--permission=safe"] + WRITE_COMMAND_SAMPLES[name])
        assert ei.value.code == 3
    assert probe_write_handlers == []
    assert _snapshot(tmp_path) == before


# ───────────────────────── 只读命令仍须放行 ─────────────────────────

@pytest.mark.parametrize("argv", [
    ["status"], ["today"], ["today", "--json"], ["version"], ["help"], ["commands"],
    ["doctor"], ["fatigue"], ["review"], ["calc", "1+1"], ["map"],
    ["key", "list"], ["key", "list", "PAPER-1"], ["memory", "status"],
    ["memory"], ["watch"], ["watch", "--list"],
    ["fetch", "watch", "--list"], ["fetch", "diff"],
    ["bridge"], ["admission", "--help"], ["mount", "--help"], ["key", "--help"],
    ["-v"], ["--version"],
])
def test_gate_allows_readonly_invocations(argv):
    assert shared.detect_safe_mode_violation(argv) is None, (
        f"只读调用被误拦: ky {' '.join(argv)}")


@pytest.mark.parametrize("argv", [
    ["map", "--save"], ["map", "-s"],
    ["compare", "--save"], ["compare", "A", "B", "--save"],
    ["diagnose", "答题卡", "--save"],
    ["fetch", "diff", "--save"], ["fetch", "info", "某大学", "--save"],
    ["scout", "某大学", "--save"], ["scout", "某大学", "--apply"],
    ["admission", "某大学", "--save"],
    ["watch", "某大学"], ["watch", "--remove", "某大学"],
    # check_updates() 末尾无条件 self._save()（watcher.py:317），故 --check 是写操作
    ["watch", "--check"], ["fetch", "watch", "--check"],
    ["fetch", "watch", "某大学"],
    ["memory", "prune"], ["key", "set", "P1", "1", "A"],
])
def test_gate_blocks_write_escalations(argv):
    """「默认只读、显式开关/子命令才落盘」的命令必须被识别为写操作。"""
    assert shared.detect_safe_mode_violation(argv), (
        f"写触发未被拦截: ky {' '.join(argv)}")


def test_unknown_command_is_fail_closed():
    """未注册命令不得再 fail-open 放行。"""
    for argv in (["totally-unknown-cmd"], ["pdf"], ["skills"], ["history"],
                 ["log"], ["clear"], ["watchdog"], ["verify-health"]):
        violation = shared.detect_safe_mode_violation(argv)
        assert violation, f"未注册命令被放行: ky {' '.join(argv)}"
        assert "未注册" in violation


def test_registry_unavailable_does_not_false_reject_read_commands(monkeypatch):
    """防御：注册表为空（命令模块整体导入失败）时不得把所有查询命令误判成未注册。

    这种状态下 ``dispatch`` 自身也拿不到任何 handler（``get_command`` 返回 None →
    「未知参数」exit 1），所以不会有写操作漏网 —— 此处只验证不误报。
    """
    monkeypatch.setattr(dispatch, "list_commands", lambda: [])
    assert shared.detect_safe_mode_violation(["status"]) is None
    assert shared.detect_safe_mode_violation(["today"]) is None

    monkeypatch.setattr(dispatch, "_REGISTRY", {})
    assert dispatch.get_command("mount") is None, (
        "注册表为空时任何命令都取不到 handler，这正是「不会漏网」的依据")


# ───────────────────────── 阴性验证 ─────────────────────────

def test_negative_control_gate_is_what_blocks(probe_write_handlers, monkeypatch, tmp_path):
    """阴性对照：把门禁摘掉后 handler 必须真的被执行。

    证明上面那些「handler 未被调用」的断言确实由本次修复支撑，而不是测试本身
    根本没走到 handler（例如命令名写错、注册表没加载）。
    """
    monkeypatch.setattr(dispatch, "detect_safe_mode_violation", lambda args: None)

    rc = dispatch.main(["--permission=safe", "mount"])

    assert rc == 0, "摘掉门禁后 mount 应正常执行"
    assert "mount" in probe_write_handlers, "阴性对照失效：即使没有门禁 handler 也没被调用"
    assert (tmp_path / "__wrote_mount.txt").exists()


def test_negative_control_save_flag_escalation_is_what_blocks(monkeypatch):
    """阴性对照（子命令粒度）：把细分表清空后 ``map --save`` 会漏网。"""
    monkeypatch.setattr(shared, "_SAFE_MODE_WRITE_FLAGS", {})
    assert shared.detect_safe_mode_violation(["map", "--save"]) is None, (
        "细分表清空后仍未放行，说明断言没有真正指向该表")


# ───────────────── 低层写盘点：ROOT 拼路径 / 裸写收口 ─────────────────
# 命令级门禁是第一道防线；这些测试覆盖「绕开 dispatch 直接调函数」的写盘点，
# 确保闸门下沉到统一写入口后，同一病根不会从另一条路径漏出去。

def test_watcher_save_is_gated_by_read_only_mode(tmp_path, monkeypatch):
    """``ky watch --check`` 只是比对指纹，但 ``check_updates()`` 末尾无条件
    ``self._save()``（watcher.py:317）。``_save`` 此前是裸 ``open(...,"w")``，
    绕开 ky_io 闸门 —— 只读模式下仍会落盘监控库。
    """
    from tools.intelligence import watcher as watcher_mod

    target = tmp_path / "admission_watch.json"
    monkeypatch.setattr(watcher_mod, "WATCH_FILE", target)

    w = watcher_mod.AdmissionWatcher.__new__(watcher_mod.AdmissionWatcher)
    w.watch_data = {"x": {"name": "测试大学"}}

    _set_read_only(True)
    try:
        with pytest.raises(Exception) as ei:
            w._save()
        assert type(ei.value).__name__ == "PermissionDeniedError", (
            f"只读模式下 watcher._save 应被闸门拒绝，实际: {type(ei.value).__name__}")
    finally:
        _set_read_only(False)

    assert not target.exists(), "只读模式下不得落盘监控库"
    assert list(tmp_path.glob("*.tmp")) == [], "被拒绝的写入不得留下临时文件"


def test_watcher_save_still_writes_by_default(tmp_path, monkeypatch):
    """阴性对照：非只读时 ``_save`` 必须照常落盘（证明上面不是「根本没跑」）。"""
    from tools.intelligence import watcher as watcher_mod

    target = tmp_path / "admission_watch.json"
    monkeypatch.setattr(watcher_mod, "WATCH_FILE", target)

    w = watcher_mod.AdmissionWatcher.__new__(watcher_mod.AdmissionWatcher)
    w.watch_data = {"x": {"name": "测试大学"}}
    w._save()

    assert target.exists()
    assert "测试大学" in target.read_text(encoding="utf-8")


def test_material_scanner_agents_backup_is_gated(tmp_path):
    """AGENTS.md 备份曾是 AGENTS 家族里唯一一处裸 ``write_text``，现须过闸门。"""
    from tools.skills import material_scanner as ms

    agents = tmp_path / "AGENTS.md"
    agents.write_text("# AGENTS\n", encoding="utf-8")

    _set_read_only(True)
    try:
        with pytest.raises(Exception) as ei:
            ms._backup_agents(agents, "# 改写前内容\n")
        assert type(ei.value).__name__ == "PermissionDeniedError", (
            f"只读模式下 AGENTS 备份应被闸门拒绝，实际: {type(ei.value).__name__}")
    finally:
        _set_read_only(False)

    assert list(tmp_path.rglob("AGENTS_backup_*.md")) == [], (
        "只读模式下不得凭空落一份 AGENTS 备份")


def test_material_scanner_agents_backup_still_writes_by_default(tmp_path):
    """阴性对照：非只读时备份照常落盘。"""
    from tools.skills import material_scanner as ms

    agents = tmp_path / "AGENTS.md"
    agents.write_text("# AGENTS\n", encoding="utf-8")

    backup = ms._backup_agents(agents, "# 改写前内容\n")

    assert backup is not None and backup.exists()
    assert backup.read_text(encoding="utf-8") == "# 改写前内容\n"


def test_search_cache_save_is_gated(tmp_path):
    """``ky scout`` 不带 ``--save`` 也会写检索缓存；``SearchCache._save`` 此前是
    裸 ``write_text``，绕开闸门 —— 只读模式下不得落盘。"""
    from tools.search.cache import SearchCache

    target = tmp_path / "search_cache.json"
    cache = SearchCache(path=target)

    _set_read_only(True)
    try:
        cache._save()
    finally:
        _set_read_only(False)

    assert not target.exists(), "只读模式下不得落盘检索缓存"


def test_search_cache_save_still_writes_by_default(tmp_path):
    """阴性对照：非只读时缓存照常落盘（证明上一条不是因为路径写错）。"""
    from tools.search.cache import SearchCache

    target = tmp_path / "search_cache.json"
    cache = SearchCache(path=target)
    cache._save()

    assert target.exists(), "非只读模式下检索缓存应正常落盘"


def test_error_logger_review_event_is_gated(tmp_path, monkeypatch):
    """``record_review_event`` 此前用裸 ``open(..., "a")`` 追加复测事件日志，
    绕开闸门 —— 只读模式下不得产生日志文件。"""
    from tools.skills import error_logger

    target = tmp_path / "review_log.jsonl"
    monkeypatch.setattr(error_logger, "REVIEW_LOG_FILE", target)

    _set_read_only(True)
    try:
        ok = error_logger.record_review_event(
            subject="eng", title="长难句", stage_before=1, rating="good")
    finally:
        _set_read_only(False)

    assert ok is False, "只读模式下追加复测日志应返回失败而不是静默成功"
    assert not target.exists(), "只读模式下不得落盘复测事件日志"


def test_error_logger_review_event_still_appends_by_default(tmp_path, monkeypatch):
    """阴性对照：非只读时日志照常追加。"""
    from tools.skills import error_logger

    target = tmp_path / "review_log.jsonl"
    monkeypatch.setattr(error_logger, "REVIEW_LOG_FILE", target)

    ok = error_logger.record_review_event(
        subject="eng", title="长难句", stage_before=1, rating="good")

    assert ok is True
    assert target.exists() and target.read_text(encoding="utf-8").strip()


def test_config_and_agents_writers_all_go_through_guarded_primitive():
    """静态盘点：ky_config.json / AGENTS.md 的写入必须走 ``atomic_write_text``。

    ``atomic_write_text`` 内部调 ``guard_write``，是唯一接上只读闸门的写原语。
    这条测试把「裸 ``Path.write_text`` 写这两类文件」钉死 —— 今天两次真实数据
    事故的受害者就是它们。
    """
    import re as _re

    offenders = []
    for py in sorted((REPO_ROOT / "tools").rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        # tools/ 下自带的自测脚本（test_ky_suite.py / test_new_features.py 等）是
        # 测试脚手架，它们对配置的裸读写本身就是被测对象，且各自另有守卫，排除。
        if py.name.startswith("test_") or py.name.endswith("_test.py"):
            continue
        text = py.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "atomic_write_text" in line or "guard_write" in line:
                continue
            # 只盯「写这两类文件」的裸写：变量名/字面量里出现 AGENTS 或 ky_config
            if not _re.search(r"AGENTS|agents_file|agents_path|agents_root|ky_config|cfg_file|cfg_path",
                              line):
                continue
            if _re.search(r"\.write_text\(|open\([^)]*[\"']w", line):
                offenders.append(f"{py.relative_to(REPO_ROOT)}:{i}: {stripped}")

    assert not offenders, (
        "以下位置用裸写落盘 ky_config.json / AGENTS.md，绕开了 ky_io 写闸门：\n  "
        + "\n  ".join(offenders))


# ───────────────────────── 根因二：双导入别名 ─────────────────────────

def test_read_only_flag_is_shared_across_ky_io_aliases():
    """只置一个别名，另一个别名（另一条写入路径）必须同样看到只读。"""
    mods = _ky_io_aliases()
    assert len(mods) >= 2, f"未观察到双导入别名，测试前提不成立: {[m.__name__ for m in mods]}"

    mods[0].set_read_only_mode(True)
    try:
        for m in mods:
            assert m.is_read_only_mode(), f"{m.__name__} 未同步到只读标志"
        with pytest.raises(Exception) as ei:
            mods[-1].atomic_write_text(Path(__file__).parent / "__should_not_exist__.txt", "x")
        assert type(ei.value).__name__ == "PermissionDeniedError"
        assert not (Path(__file__).parent / "__should_not_exist__.txt").exists()
    finally:
        _set_read_only(False)


def test_read_only_flag_is_fail_closed_across_aliases(monkeypatch):
    """只动另一个别名的原始属性时，本别名也必须 fail-closed 拒绝写入。"""
    mods = _ky_io_aliases()
    assert len(mods) >= 2
    monkeypatch.setattr(mods[-1], "_READ_ONLY_MODE", True, raising=False)

    assert mods[0].is_read_only_mode(), "任一别名只读即应视为只读（fail-closed）"
    with pytest.raises(Exception) as ei:
        mods[0].guard_write("probe")
    assert type(ei.value).__name__ == "PermissionDeniedError"


def test_late_imported_alias_inherits_read_only_flag():
    """时序：标志置位之后才首次 import 另一个别名，新别名不得以 False 起跑。"""
    snippet = (
        "import sys; sys.path.insert(0, r'{root}'); sys.path.insert(0, r'{tools}')\n"
        "import importlib\n"
        "a = importlib.import_module('tools.ky_io')\n"
        "a.set_read_only_mode(True)\n"
        "b = importlib.import_module('ky_io')\n"
        "assert a is not b\n"
        "assert b.is_read_only_mode() is True, '后导入的别名未继承只读标志'\n"
        "assert a.is_read_only_mode() is True\n"
        "print('OK')\n"
    ).format(root=REPO_ROOT, tools=REPO_ROOT / "tools")

    r = subprocess.run([sys.executable, "-c", snippet], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, f"子进程失败: {r.stdout}\n{r.stderr}"
    assert "OK" in r.stdout


def test_alias_recognized_across_drive_case_variants():
    """同一份 ky_io.py 经不同盘符大小写的 sys.path 条目加载时，别名必须仍判为兄弟。

    [实测根因] ``_sibling_modules`` 原用 ``os.path.abspath(f) == here`` 做
    **大小写敏感**的字符串比较。Windows 文件系统不区分大小写：pytest 的
    ``pythonpath=["."]``（根目录盘符大小写随 shell cwd）与测试文件自插入的
    ``tools`` 目录（盘符大小写随 ``__file__``）可能给出仅差盘符大小写的
    ``__file__`` —— 比较失败 → 兄弟识别为空 → 跨别名 fail-closed 判定静默
    失效（本机全量 pytest 实测：``test_read_only_flag_is_shared_across_ky_io_aliases``
    与 ``test_read_only_flag_is_fail_closed_across_aliases`` 间歇变红，是否复现
    取决于调用方 shell 设置的 cwd 盘符大小写）。

    阴性对照：把 ``_sibling_modules`` 里的 ``os.path.normcase`` 去掉，本用例变红
    （子进程里 ``a._sibling_modules()`` 返回空列表）。
    """
    tools_dir = str(REPO_ROOT / "tools")
    tools_alt = tools_dir[0].swapcase() + tools_dir[1:]
    if tools_alt == tools_dir:
        pytest.skip("当前平台路径大小写不敏感变体不适用（POSIX 大小写敏感）")

    snippet = (
        "import sys\n"
        "sys.path.insert(0, r'{root}')\n"       # 原盘符（如 C:）→ 解析 tools 包
        "sys.path.insert(0, r'{tools_alt}')\n"  # 变体盘符（如 c:）→ 解析顶层 ky_io
        "import importlib\n"
        "a = importlib.import_module('tools.ky_io')\n"
        "b = importlib.import_module('ky_io')\n"
        "assert a is not b\n"
        "sib = [m.__name__ for m in a._sibling_modules()]\n"
        "assert sib == ['ky_io'], (\n"
        "    '大小写变体路径下兄弟别名识别失败: ' + repr(sib)\n"
        "    + ' | a.__file__=' + repr(a.__file__) + ' b.__file__=' + repr(b.__file__))\n"
        "a.set_read_only_mode(True)\n"
        "assert b.is_read_only_mode() is True, '只读标志未跨大小写变体别名同步'\n"
        "print('CASE_ALIAS_OK')\n"
    ).format(root=REPO_ROOT, tools_alt=tools_alt)

    r = subprocess.run([sys.executable, "-c", snippet], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, f"子进程失败: {r.stdout}\n{r.stderr}"
    assert "CASE_ALIAS_OK" in r.stdout


# ───────────────────────── 真实 CLI 端到端（沙箱） ─────────────────────────

@pytest.fixture
def sandbox_workspace(tmp_path):
    """把 tools/ 复制到独立沙箱：``shared.ROOT`` 由 ``__file__`` 推导，指向沙箱根，
    因此真实仓库不会被任何写操作波及。"""
    root = tmp_path / "ws"
    root.mkdir()
    shutil.copytree(REPO_ROOT / "tools", root / "tools",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (root / "ky_config.json").write_text(json.dumps({
        "api_provider": "deepseek",
        "active_subject": "pro",
        "study_plan": {
            "school": "沙箱院校", "major": "030100 法学",
            "math_key": "none", "total_hours": 6.5, "pro_books": "沙箱原始值",
        },
        "completion_history": {},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "AGENTS.md").write_text(
        "# 顶层总控协议\n" + "沙箱占位正文。\n" * 40, encoding="utf-8")
    return root


def _run_cli_in_sandbox(root: Path, argv):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONNOUSERSITE"] = "1"
    return subprocess.run(
        [sys.executable, "tools/ky_cli.py", "--permission=safe"] + argv,
        cwd=str(root), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=180)


@pytest.mark.parametrize("argv", [
    ["mount"], ["done", "背单词"], ["plan"], ["relieve"], ["style", "2"],
])
def test_real_cli_blocks_write_commands_in_sandbox(argv, sandbox_workspace):
    """真实 CLI 子进程端到端：被拒绝（exit=3）且沙箱工作区字节不变。"""
    before = _snapshot(sandbox_workspace)

    r = _run_cli_in_sandbox(sandbox_workspace, argv)

    assert r.returncode == 3, (
        f"ky {' '.join(argv)} 在只读模式下未被拒绝 (exit={r.returncode})\n"
        f"stdout={r.stdout[-600:]}\nstderr={r.stderr[-600:]}")
    assert "已拒绝" in r.stdout
    assert _snapshot(sandbox_workspace) == before, (
        f"ky {' '.join(argv)} 在只读模式下改动了沙箱工作区文件")


def test_real_cli_sandbox_control_writes_without_safe_flag(sandbox_workspace):
    """阴性对照：去掉 --permission=safe 后写命令必须真的落盘。

    证明上面的「字节不变」不是因为命令根本没跑（例如沙箱缺依赖而提前失败）。
    这里用 ``style 2`` 而非 ``mount``：mount 会做联网扫描，作为对照太慢。
    """
    before = _snapshot(sandbox_workspace)

    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONNOUSERSITE"] = "1"
    r = subprocess.run(
        [sys.executable, "tools/ky_cli.py", "style", "2"],
        cwd=str(sandbox_workspace), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=180)

    assert r.returncode == 0, f"对照命令失败: {r.stdout[-600:]}\n{r.stderr[-600:]}"
    after = _snapshot(sandbox_workspace)
    changed = {k for k in after if after[k] != before.get(k)}
    assert changed, "对照组未产生任何写入，说明沙箱里的写命令根本没跑起来"
    assert {"ky_config.json", "AGENTS.md"} & changed, (
        f"对照组应改写 ky_config.json / AGENTS.md，实际变化: {sorted(changed)}")
