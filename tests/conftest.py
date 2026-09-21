# -*- coding: utf-8 -*-
"""tests/conftest.py —— 保护开发者真实 ``ky_config.json`` / ``ky_history.json``。

背景（真实事故，非推测）：
    仓库根目录的 ``ky_config.json`` 是开发者本人的真实备考配置（被 ``.gitignore``
    保护、不在版本控制内），内含完整 ``study_plan``（目标院校 / 报考专业 / 各科
    目标分 / 每日时长）+ ``webhooks`` + API 配置。

    它曾被测试**反复覆盖**，最惨时只剩 3 个键::

        {"api_key":"sk-test-fake","model":"deepseek-chat","active_subject":"pol"}

    原因是 ``tools/cli/shared.py`` 把 ``ROOT`` 硬编码为仓库根、``CONFIG_FILE``
    指向真实配置，任何一次 ``save_config()`` 都会写盘真实文件。更隐蔽的是本项目
    存在 **双导入**：``cli.shared`` 与 ``tools.cli.shared`` 是两个独立模块对象，
    各有一份 ``CONFIG_FILE`` 全局；只补一个模块不足以重定向写盘目标。

本文件提供两道防线（两道都必须保留，不得放宽）：

    (a) 主动重定向（best-effort）：autouse fixture 在测试开始前，把**已加载**
        的相关模块的 ``CONFIG_FILE`` / ``HISTORY_FILE`` 指向 per-test 的
        ``tmp_path``；并在测试期间挂钩 import 机制，让**测试中途才导入**的
        模块也立刻被重定向。从源头避免误写真实文件。
    (b) 兜底绊线（tripwire，硬失败）：测试前后比对真实文件的字节。若被改动，
        **立刻还原原内容** 并 ``pytest.fail``，点名 nodeid；若原本不存在而测试
        新建了，则删除该文件。这样任何漏网的写入者都会被当场抓住，且不会造成
        永久数据损失。

历史教训（为什么 (a) 需要「挂钩 import」这一层）：
    早期实现靠一份手写的模块名清单（``_KNOWN_MODULE_NAMES``）去重定向，且只在
    fixture 开始前扫描一次 ``sys.modules``。但 ``tools.cli.shared`` / ``ky_cli``
    这些持有者往往是**测试体内惰性导入**的（例如
    ``tools/gui/workers/agent_worker.py`` 在 ``run()`` 里才
    ``from ky_cli import save_config``），fixture 开始扫描时它们还不存在于
    ``sys.modules``，于是漏网并在写盘时指向真实工作区。手写清单还会随新增别名
    继续漏。现改为：
        1. 不再维护模块名清单，直接扫描 ``sys.modules`` 里**所有**持有
           ``CONFIG_FILE`` / ``HISTORY_FILE`` 全局、且**值确实等于真实路径**的
           模块（按路径相等判断，不按名字猜）；
        2. 在测试期间包一层文件加载器的 ``exec_module``，任何模块导入完成后
           立刻补做同样的扫描与重定向。
"""

import importlib.machinery
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REAL_CONFIG = ROOT / "ky_config.json"
REAL_HISTORY = ROOT / "ky_history.json"

#: 守卫留证目录的**测试覆盖点**。``None`` 表示用 ``config_guard.BACKUP_DIR``
#: 解析出的仓库外位置（见 :func:`_evidence_dir`）；测试可把它指到 ``tmp_path``。
EVIDENCE_DIR_OVERRIDE = None

#: 留证目录不可用时的兜底（**仍在仓库之外** —— 留证同样是明文配置快照，
#: 绝不能因为解析失败就落回仓库根，那等于把已修好的泄漏重新引入）。
_FALLBACK_EVIDENCE_SUBDIR = ("kaoyan-study-chain", "config_backup")

#: 受保护的全局名 -> 对应的真实文件
_PROTECTED_NAMES = {
    "CONFIG_FILE": REAL_CONFIG,
    "HISTORY_FILE": REAL_HISTORY,
}

#: 受保护的全局名 -> 重定向后的文件名（放在 per-test tmp_path 下）
_TMP_FILENAMES = {
    "CONFIG_FILE": "ky_config.json",
    "HISTORY_FILE": "ky_history.json",
}

#: 需要挂钩 ``exec_module`` 的文件加载器类（.py / .pyc / 扩展模块）。
#: 挂钩它们即可覆盖所有「文件型模块」的导入，与 ``sys.meta_path`` 的顺序无关，
#: 因此不会抢在 pytest 的断言重写钩子前面。
_LOADER_CLASSES = (
    importlib.machinery.SourceFileLoader,
    importlib.machinery.SourcelessFileLoader,
    importlib.machinery.ExtensionFileLoader,
)


def _as_path(value):
    """把模块属性尽力规范成 Path；无法规范时返回 None。"""
    if isinstance(value, Path):
        return value
    if isinstance(value, str) and value:
        try:
            return Path(value)
        except Exception:
            return None
    return None


def _same_file(value, real_path: Path) -> bool:
    """判断某个模块属性是否指向受保护的真实文件。"""
    candidate = _as_path(value)
    if candidate is None:
        return False
    try:
        return os.path.normcase(str(candidate.resolve())) == os.path.normcase(
            str(real_path.resolve())
        )
    except Exception:
        return os.path.normcase(str(candidate)) == os.path.normcase(str(real_path))


def _iter_modules():
    """产出 ``sys.modules`` 中所有带 ``__dict__`` 的模块。

    不维护模块名清单：将来任何新增别名只要被导入，就会出现在这里。
    ``pathlib`` 之类的无关模块不含受保护全局名，会被下一步自然过滤掉。
    """
    for name, mod in list(sys.modules.items()):
        if mod is None or getattr(mod, "__dict__", None) is None:
            continue
        yield name, mod


def _redirect_module(mod, tmp_path: Path, monkeypatch) -> bool:
    """把单个模块里指向真实配置的受保护全局名改到 tmp_path。

    仅当属性**确实存在于模块全局**且**值等于真实路径**时才改（按路径相等判断，
    不按模块名猜）。返回是否发生了改动。
    """
    namespace = getattr(mod, "__dict__", None)
    if namespace is None:
        return False

    changed = False
    for attr, real_path in _PROTECTED_NAMES.items():
        if attr not in namespace:
            continue
        try:
            current = namespace[attr]
        except Exception:
            continue
        if not _same_file(current, real_path):
            continue
        try:
            monkeypatch.setattr(mod, attr, tmp_path / _TMP_FILENAMES[attr], raising=False)
        except Exception:
            # 只读 / 不可写属性：交给绊线兜底
            continue
        changed = True
    return changed


def _redirect_all(tmp_path: Path, monkeypatch) -> int:
    """扫描全部已加载模块并重定向；返回被改动的模块数。"""
    count = 0
    for _name, mod in _iter_modules():
        if _redirect_module(mod, tmp_path, monkeypatch):
            count += 1
    return count


def _blame_modules() -> str:
    """污染发生后，点名仍有模块把 CONFIG_FILE 指向真实路径（辅助定位）。"""
    culprits = []
    for name, mod in _iter_modules():
        namespace = getattr(mod, "__dict__", None) or {}
        for attr, real_path in _PROTECTED_NAMES.items():
            if attr not in namespace:
                continue
            try:
                if _same_file(namespace[attr], real_path):
                    culprits.append(f"{name}.{attr}")
            except Exception:
                continue
    return ", ".join(sorted(set(culprits))) or "（未找到；写入者可能已自行还原）"


def _install_import_hook(tmp_path: Path, monkeypatch) -> None:
    """挂钩文件加载器：模块导入完成后立刻补做一次重定向。

    这是本文件的关键修复点。``ky_cli`` / ``tools.cli.shared`` /
    ``skills.experience_dossier`` 等持有者常在**测试体内**才被惰性导入，
    fixture 开始时的 ``sys.modules`` 快照里并没有它们；只靠开始时扫描一次，
    它们就会带着真实 ``CONFIG_FILE`` 完成导入，随后 ``save_config()`` 直接
    写盘真实工作区。
    """
    for loader_cls in _LOADER_CLASSES:
        original = loader_cls.exec_module
        if getattr(original, "_ky_tripwire_patched", False):
            continue

        def make_patched(_original):
            def exec_module(self, module):
                _original(self, module)
                try:
                    _redirect_module(module, tmp_path, monkeypatch)
                except Exception:
                    # 重定向本身绝不允许影响被测代码的导入流程
                    pass

            exec_module._ky_tripwire_patched = True
            return exec_module

        monkeypatch.setattr(loader_cls, "exec_module", make_patched(original))


@pytest.fixture(autouse=True)
def _protect_real_ky_config(tmp_path, monkeypatch, request):
    """(a) 主动重定向 + (b) 兜底绊线。绊线为硬失败，不得改为 warning。"""
    snapshots = {}
    for path in (REAL_CONFIG, REAL_HISTORY):
        snapshots[path] = path.read_bytes() if path.exists() else None

    _redirect_all(tmp_path, monkeypatch)
    _install_import_hook(tmp_path, monkeypatch)

    yield

    polluted = []
    for path, before in snapshots.items():
        after = path.read_bytes() if path.exists() else None
        if after == before:
            continue

        # 先还原，保证不造成永久数据损失
        restore_note = ""
        try:
            if before is None:
                if path.exists():
                    path.unlink()
                restore_note = "已删除测试新建的文件"
            else:
                path.write_bytes(before)
                restore_note = "已还原为原始内容"
        except Exception as exc:  # pragma: no cover - 极端情况
            restore_note = f"还原失败({exc!r})，请立即手动检查！"

        polluted.append((path, restore_note))

    if polluted:
        details = "；".join(f"{p.name}: {note}" for p, note in polluted)
        pytest.fail(
            f"[tripwire] 测试 {request.node.nodeid} 污染了真实 {details}。"
            f" 必须把 CONFIG_FILE/HISTORY_FILE 重定向到 tmp_path"
            f"（本文件已扫描 sys.modules 全量模块并挂钩 import，若仍被写说明"
            f"写入者绕开了模块全局或使用了硬编码路径）。"
            f" 仍指向真实路径的模块：{_blame_modules()}",
            pytrace=False,
        )


# ─────────────────────────────────────────────────────────────────────────────
# (c) 会话级守卫（2026-09-21 事故后新增）
#
# 上面 (b) 的绊线是 **per-test** 的：只在每个用例前后比对。若写入发生在
# pytest 的**收集阶段**、conftest 加载之前、或某个后台线程/子进程里，它就抓不到。
# 2026-09-21 的真实事故正是这一类：`ky_config.json` 被并发运行的**临时验证脚本**
# 覆写成只剩 2 个键的测试夹具（39 字段 study_plan + 51 字符真实 api_key 丢失），
# 且因为写入者根本不在 pytest 进程内，绊线一次都没响，损坏持续存在并被反复覆盖。
#
# 这一层在整个会话的首尾各取一次指纹：
#   * 结束时若发现偏离 —— **先还原**（保证不丢数据），再让整次运行失败；
#   * 开始时把原始字节留档为 ``<name>.session_before``，偏离时把污染现场另存
#     为 ``<name>.session_after``（名字与内容必须相符，否则据名恢复会二次污染）；
#   * 同时把现场写进留证目录的 ``session_guard.log``，便于事后定位写入者。
#     留证目录 = ``config_guard.BACKUP_DIR``（**仓库之外**）—— 见 `_evidence_dir`。
# ─────────────────────────────────────────────────────────────────────────────

def _digest(path: Path):
    """文件内容的 sha256；不存在返回 None。"""
    try:
        import hashlib
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _evidence_dir() -> Path:
    """守卫留证目录 —— 与 ``config_guard.BACKUP_DIR`` **同源，且必在仓库之外**。

    留证文件（``<name>.session_before`` / ``<name>.session_after``）就是
    ``ky_config.json`` 的**明文完整副本**，因此它们和快照一样绝不能落在仓库根：
    导出副本走文件系统遍历，明文密钥会随公开副本出门。此前它们被写进
    ``ROOT/.config_backup``，正是同一个 CRITICAL 的另一半。

    取值顺序：``EVIDENCE_DIR_OVERRIDE``（测试）→ ``config_guard.BACKUP_DIR``
    （仓库之外）→ 系统临时目录下的同名子目录（兜底，仍不在仓库内）。
    """
    if EVIDENCE_DIR_OVERRIDE is not None:
        return Path(EVIDENCE_DIR_OVERRIDE)
    try:
        sys.path.insert(0, str(ROOT))
        from tools import config_guard  # noqa: WPS433
        return Path(config_guard.BACKUP_DIR)
    except Exception:
        import tempfile
        return Path(tempfile.gettempdir()).joinpath(*_FALLBACK_EVIDENCE_SUBDIR)


def _guard_log(message: str) -> None:
    """把守卫事件追加到留证目录下的 ``session_guard.log``（尽力而为）。"""
    try:
        backup_dir = _evidence_dir()
        backup_dir.mkdir(parents=True, exist_ok=True)
        import time
        with (backup_dir / "session_guard.log").open("a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except OSError:
        pass


def _snapshot_file(path: Path, suffix: str) -> None:
    """把 ``path`` 的**当前原始字节**拷成 ``<留证目录>/<name>.<suffix>``。

    名字必须与内容相符：``session_before`` 只在会话**开始**时写（真·原始），
    ``session_after`` 只在发现偏离后写（真·污染现场）。此前唯一的拷贝发生在
    会话结束时却命名为 ``session_before``，事后据名恢复会把污染内容写回。
    """
    dest = _evidence_dir() / f"{path.name}.{suffix}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, dest)


def _session_guard_prepare(paths) -> None:
    """会话**开始**时：把各真实文件的原始字节留档为 ``<name>.session_before``。

    这是「名字与内容相符」的关键 —— 原始字节只能在会话开始前取到。
    """
    for path in paths:
        try:
            if path.exists():
                _snapshot_file(path, "session_before")
        except OSError:  # pragma: no cover - 留证失败不得阻断测试
            pass


def _restore_from_guard(path: Path) -> bool:
    """用 ``config_guard`` 的期望状态快照还原 ``path``（仅当它确实是守卫目标）。

    **[安全闸]** ``config_guard.CONFIG`` 是**模块级全局**，可能指向与本次守卫
    完全无关的文件。实测缺陷（2026-09-21 复现）：某测试把守卫的 ``ROOT`` /
    ``REAL_CONFIG`` 重定向到 ``tmp_path``、并试图用
    ``sys.modules["tools.config_guard"] = None`` 拦住导入 —— 但 ``from tools
    import config_guard`` 会命中**已加载包属性**从而绕过该拦截，于是
    ``restore()`` 把**仓库根的真实 ``ky_config.json``** 覆盖成了历史快照：
    跑一次全量测试后真实配置的 ``mtime`` 被刷新（该次内容恰好相同，``md5`` 未变）。
    若真实配置在此期间被合法修改过，就会被**静默回滚** —— 不可接受。

    因此这里先确认 ``config_guard.CONFIG`` **正是**被守卫的那个文件，否则放弃
    还原（交回给调用方按「无法从快照还原」处理），绝不代它去写别的文件。
    """
    try:
        sys.path.insert(0, str(ROOT))
        from tools import config_guard  # noqa: WPS433
        try:
            if Path(config_guard.CONFIG).resolve() != Path(path).resolve():
                return False
        except Exception:
            return False
        return bool(config_guard.restore(None, force=False))
    except Exception:
        return False


def _session_guard_finalize(before: dict) -> list:
    """会话**结束**时比对指纹；偏离则留证（``session_after``）+ 还原。

    Returns:
        ``[(文件名, 处置说明)]``；为空表示未偏离。
    """
    polluted = []
    for path, digest in before.items():
        if _digest(path) == digest:
            continue
        # 先还原，再报错 —— 顺序不能反，否则报错中断会留下损坏文件
        note = "还原失败，请立即手动检查！"
        try:
            if digest is None:
                if path.exists():
                    path.unlink()
                note = "已删除测试新建的文件"
            else:
                _snapshot_file(path, "session_after")   # 污染现场（名字即内容）
                # 优先用 config_guard 的期望状态快照还原（更可靠）
                restored = _restore_from_guard(path)
                if not restored:
                    note = ("无法从快照还原（留证目录无可用期望状态快照），"
                            "污染现场已留档为 "
                            f"{_evidence_dir()}/{path.name}.session_after")
        except OSError as exc:  # pragma: no cover
            note = f"还原异常({exc!r})，请立即手动检查！"
        polluted.append((path.name, note))
    return polluted


def _session_guard_run():
    """会话级守卫的完整生命周期（生成器），供 fixture 与测试共用。

    第一次 ``next()``  = 会话开始：把原始字节留档为 ``<name>.session_before``。
    第二次 ``next()``  = 会话结束：比对指纹，偏离则留 ``<name>.session_after``
    并还原；返回值（``StopIteration.value``）是 ``[(文件名, 处置说明)]``。
    """
    before = {p: _digest(p) for p in (REAL_CONFIG, REAL_HISTORY)}

    # [缺陷修复·留证快照名实相符] 会话**开始**时就拷一份原始字节为
    # ``<name>.session_before``；结束时若发现变更，再把污染现场另存为
    # ``<name>.session_after``。此前只在结束时拷贝、却命名为 session_before，
    # 语义与名字相反 —— 运维据名"恢复"会把污染内容写回，造成二次污染。
    _session_guard_prepare(before)

    yield

    return _session_guard_finalize(before)


@pytest.fixture(scope="session", autouse=True)
def _session_config_guard():
    """会话级配置完整性守卫：首尾指纹比对，偏离则还原并让运行失败。"""
    lifecycle = _session_guard_run()
    next(lifecycle)                  # 会话开始：留 session_before
    yield
    try:                             # 会话结束：留 session_after + 还原
        next(lifecycle)
        polluted = []
    except StopIteration as stop:
        polluted = stop.value or []

    if polluted:
        details = "；".join(f"{name}: {note}" for name, note in polluted)
        _guard_log(f"会话级守卫发现偏离 -> {details}")
        raise RuntimeError(
            f"[session-guard] 本次 pytest 会话期间，真实配置发生偏离（{details}）。"
            f" 这通常意味着有**测试进程之外**的写入者（后台线程 / 子进程 / 并发脚本）"
            f"改动了工作区根目录的 ky_config.json。"
            f" 请检查 {_evidence_dir()}/session_guard.log 与该目录下的留证快照。"
        )
