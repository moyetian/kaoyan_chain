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
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REAL_CONFIG = ROOT / "ky_config.json"
REAL_HISTORY = ROOT / "ky_history.json"

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
