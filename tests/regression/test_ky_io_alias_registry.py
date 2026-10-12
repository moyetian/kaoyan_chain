# -*- coding: utf-8 -*-
"""# [Regress] ky_io 进程级别名注册表回归（子进程隔离运行）。

加固来源：2026-10-12《考研学习链-项目审查与升级改造方案》逐项核实批次。
背景：``ky_io`` 的跨别名同步（``set_read_only_mode`` / ``is_read_only_mode``
fail-closed）依赖 ``_sibling_modules`` 找出同一份源码的其它模块对象。旧实现只
按硬编码名称 ``("tools.ky_io", "ky_io")`` 枚举 —— 同一份源码若以**其它名称**
被加载（外部工具注入 sys.path 前缀、自定义 loader、测试的 spec_from_file_location），
跨别名判定静默失效（fail-open）。修复后主路径为进程级注册表（导入期按
``__file__`` 身份自登记，与模块名无关），名称枚举降级为兜底。

本文件守护两个方向（修复前下列用例会红）：
  1. [Regress] 第三名称别名必须被识别为兄弟（注册表主路径）；
  2. [Regress] 注册表异常缺失时，标准名称的别名仍须被找到（兜底路径）。

本批（2026-10-12 OCR 代码审查 L4 修复）新增守护：
  3. [Regress] 注册表建表必须原子（``sys.__dict__.setdefault``）：旧实现
     getattr→None→建表→setattr 两步非原子，两线程并发首次调用可能各建一表，
     败者的登记落入孤儿表（注册表主路径静默漏掉该别名）。

与 ``tests/test_fix_safe_mode_write_gate.py`` 的分工：该文件的别名用例守护
「双导入的常规两个别名」；本文件守护「名称不可枚举 / 注册表缺失 / 并发建表」
三个边界。
"""

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_alias_registry_recognizes_arbitrary_third_name():
    """同一份 ky_io.py 以**第三种名称**加载时，仍须被识别为兄弟别名。

    两个方向都断言：
      方向一：标准别名置位只读 → 第三名称别名必须同步看到；
      方向二：第三名称别名的原始标志置位 → 标准别名必须 fail-closed 视为只读。

    阴性对照：把 ``_sibling_modules`` 退回按名称枚举（去掉注册表主路径），
    本用例在 ``'ky_io_via_file' in sib`` 处变红（已在 2026-10-12 批次实测）。
    """
    snippet = (
        "import sys\n"
        "sys.path.insert(0, r'{root}')\n"
        "sys.path.insert(0, r'{tools}')\n"
        "import importlib, importlib.util\n"
        "a = importlib.import_module('tools.ky_io')\n"
        "spec = importlib.util.spec_from_file_location('ky_io_via_file', r'{ky_io_path}')\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "sys.modules['ky_io_via_file'] = m\n"
        "spec.loader.exec_module(m)\n"
        "assert m is not a\n"
        "sib = [x.__name__ for x in a._sibling_modules()]\n"
        "assert 'ky_io_via_file' in sib, '第三名称别名未被识别为兄弟: ' + repr(sib)\n"
        "a.set_read_only_mode(True)\n"
        "assert m.is_read_only_mode() is True, '第三名称别名未同步只读标志'\n"
        "a.set_read_only_mode(False)\n"
        "m._READ_ONLY_MODE = True\n"
        "assert a.is_read_only_mode() is True, '第三名称别名的只读标志未 fail-closed 传播'\n"
        "print('CASE_THIRD_NAME_OK')\n"
    ).format(root=REPO_ROOT, tools=REPO_ROOT / "tools",
             ky_io_path=REPO_ROOT / "tools" / "ky_io.py")

    r = subprocess.run([sys.executable, "-c", snippet], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, f"子进程失败: {r.stdout}\n{r.stderr}"
    assert "CASE_THIRD_NAME_OK" in r.stdout


def test_alias_fallback_by_name_when_registry_cleared():
    """注册表异常缺失时，按名称枚举的兜底路径必须仍能识别标准别名。

    场景：外部代码（或极端时序）清理了 ``sys`` 上的注册表属性/内容。此时
    ``_sibling_modules`` 不得静默返回空列表 —— 标准名称的别名仍须被找到，
    保证双导入场景（本项目的常态）下的 fail-closed 判定不失效。

    阴性对照：去掉 ``_sibling_modules`` 里的名称枚举兜底（只留注册表路径），
    本用例在 ``sib == ['ky_io']`` 处变红（已在 2026-10-12 批次实测）。
    """
    snippet = (
        "import sys\n"
        "sys.path.insert(0, r'{root}')\n"
        "sys.path.insert(0, r'{tools}')\n"
        "import importlib\n"
        "a = importlib.import_module('tools.ky_io')\n"
        "b = importlib.import_module('ky_io')\n"
        "assert a is not b\n"
        "assert hasattr(sys, '_ky_io_alias_registry'), '注册表未建立'\n"
        "sys._ky_io_alias_registry.clear()\n"
        "sib = [x.__name__ for x in a._sibling_modules()]\n"
        "assert sib == ['ky_io'], '注册表清空后名称兜底失效: ' + repr(sib)\n"
        "a.set_read_only_mode(True)\n"
        "assert b.is_read_only_mode() is True, '兜底路径下只读标志未同步'\n"
        "print('CASE_FALLBACK_OK')\n"
    ).format(root=REPO_ROOT, tools=REPO_ROOT / "tools")

    r = subprocess.run([sys.executable, "-c", snippet], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, f"子进程失败: {r.stdout}\n{r.stderr}"
    assert "CASE_FALLBACK_OK" in r.stdout


def test_alias_registry_atomic_first_build_under_concurrency():
    """并发首次建表必须唯一：败者不得落入孤儿表（setdefault 原子性）。

    旧实现 getattr→None→建表→setattr 两步非原子。竞态窗口极窄（纯
    setswitchinterval 调小实测无法稳定复现），故用行级 trace 放大：当
    执行到 ``reg = {}`` 行前，tracer 强制 sleep 让出 GIL —— 8 线程将
    全部通过 getattr 看到 None 后才有人 setattr，各建各表、各自返回自建
    表（实测 8 个不同 id），而 sys 上只留下最后写入的一张。
    修复后 ``sys.__dict__.setdefault`` 单次原子调用（无 ``reg = {}`` 行，
    tracer 不介入），所有线程拿到同一张表。

    阴性对照：把 ``_alias_registry`` 还原为 getattr/setattr 两步写法，
    本用例在 ``len(set(results)) == 1`` 处变红（已在 2026-10-12 批次实测）。
    """
    snippet = (
        "import sys, threading, time, linecache\n"
        "sys.path.insert(0, r'{tools}')\n"
        "import ky_io\n"
        "def tracer(frame, event, arg):\n"
        "    if event == 'line' and frame.f_code.co_name == '_alias_registry':\n"
        "        text = linecache.getline(frame.f_code.co_filename, frame.f_lineno)\n"
        "        if text.strip().startswith('reg = {{}}'):\n"
        "            time.sleep(0.005)\n"
        "    return tracer\n"
        "for round_ in range(3):\n"
        "    if hasattr(sys, '_ky_io_alias_registry'):\n"
        "        del sys._ky_io_alias_registry\n"
        "    results = []\n"
        "    barrier = threading.Barrier(8)\n"
        "    def worker():\n"
        "        sys.settrace(tracer)\n"
        "        barrier.wait()\n"
        "        results.append(id(ky_io._alias_registry()))\n"
        "        sys.settrace(None)\n"
        "    ts = [threading.Thread(target=worker) for _ in range(8)]\n"
        "    for t in ts:\n"
        "        t.start()\n"
        "    for t in ts:\n"
        "        t.join()\n"
        "    assert len(set(results)) == 1, (\n"
        "        '第%d 轮并发建表不唯一（孤儿表）: %r' % (round_, results))\n"
        "    assert id(sys._ky_io_alias_registry) == results[0], (\n"
        "        'sys 上的表与线程返回值不一致')\n"
        "print('CASE_ATOMIC_OK')\n"
    ).format(tools=REPO_ROOT / "tools")

    r = subprocess.run([sys.executable, "-c", snippet], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, f"子进程失败: {r.stdout}\n{r.stderr}"
    assert "CASE_ATOMIC_OK" in r.stdout
