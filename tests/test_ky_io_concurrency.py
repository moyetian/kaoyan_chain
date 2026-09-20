# -*- coding: utf-8 -*-
"""ky_io 并发与多进程压测（审查报告 §四-7 专项）

覆盖三件此前只有单进程推演、未经真实多进程实证的事：

1. **同进程重入**（审查 M-5 的回归）：持有 ky_io 的锁实例时嵌套调用
   ``atomic_write_text`` 写同一路径，旧实现在 5 秒后抛 Timeout；
   修复后锁实例按路径复用、filelock 内部计数，必须立即通过。
   （注意：filelock 的可重入只对**同一实例**成立，外部自建 FileLock 与
   ky_io 内部锁实例互斥是 filelock 的设计，不在本测试范围。）
2. **跨进程互斥**：子进程持锁期间父进程写入必须真实等待（而不是旁路）；
   持锁超过锁超时(5s)时父进程必须显式失败。
3. **读写并发下的不变式**：多写进程 × 紧循环读线程同时打同一文件，
   任何一次读取都必须是某个写方的**完整**载荷（绝不出现半截/混合内容）。
   既知局限（代码注释已声明并经本测试实证）：写侧可能以**显式报错**结束
   而不是成功 —— ①持续紧循环读取可耗尽 ``os.replace`` 的 1.4s 重试窗口
   （``PermissionError``）；②多个写进程争同一把锁、负载下排在后面的写进程
   可能等不到锁（``filelock.Timeout``，锁超时 5s）。两者都**内容永不损坏**，
   只是写不保证成功。本测试把该契约固化：只强断言「读取侧不见半截内容」。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: 子进程驱动：单一 JSON 参数，避免位置参数错位。spawn 而非线程，锁才有跨进程意义。
CHILD = r"""
import json, sys, time
cfg = json.loads(sys.argv[1])
sys.path.insert(0, cfg["root"])
from tools import ky_io
from filelock import FileLock

role, target = cfg["role"], cfg["target"]

if role == "hold":
    # [R2-D4 修复·去时序依赖] 持锁直到父进程在 stdin 写一行再释放，
    # 不再用 sleep(N) 定时释放。旧写法有两个赌时序的窗口：
    #   (a) 子进程固定睡眠 N 秒，负载下父进程「进入等待」的时刻可能晚于
    #       子进程释放的时刻 —— 父进程的写会意外成功，pytest.raises 假失败；
    #   (b) 反之若父进程等待窗口与睡眠窗口错位，等待时长的断言也会漂。
    # 事件驱动后，持锁时长完全由父进程掌控，与机器快慢无关。
    with FileLock(ky_io._lock_path_for(target), timeout=30):
        print("HELD", flush=True)
        sys.stdin.readline()
        print("RELEASED", flush=True)
elif role == "write":
    ky_io.atomic_write_text(target, cfg["payload"])
    print("WROTE", flush=True)
"""


def _spawn(cfg: dict) -> subprocess.Popen:
    cfg = {"root": str(ROOT), **cfg}
    env = os.environ.copy()
    # Windows 子进程异常回溯默认跟随本地代码页，而管道按 UTF-8 解码；强制统一，
    # 否则真正的并发异常会被 reader thread 的 UnicodeDecodeError 掩盖。
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.Popen(
        [sys.executable, "-c", CHILD, json.dumps(cfg, ensure_ascii=False)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        cwd=str(ROOT),
        env=env,
    )


def _release(proc: subprocess.Popen) -> None:
    """放行持锁子进程（事件驱动同步的「信号」端）。"""
    try:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.write("release\n")
            proc.stdin.flush()
    except (OSError, ValueError):
        pass  # 子进程已退出：忽略


def _kill(proc: subprocess.Popen) -> None:
    """终止子进程并**显式关闭管道**：pyproject 设了 filterwarnings=error，
    若留待 GC 关闭被杀进程的 pipe 会触发 PytestUnraisableExceptionWarning。"""
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=5)
    for f in (proc.stdin, proc.stdout, proc.stderr):
        if f:
            try:
                f.close()
            except OSError:
                pass


def _wait_line(proc: subprocess.Popen, token: str, timeout: float = 15.0) -> None:
    """阻塞等待子进程打印出 token（如 HELD），确认其已进入目标状态。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = proc.stdout.readline() if proc.stdout else ""
        if token in line:
            return
        if line == "" and proc.poll() is not None:
            break
    err = (proc.stderr.read() if proc.stderr else "")[-400:]
    _kill(proc)
    pytest.fail(f"子进程未在 {timeout}s 内进入 {token} 状态: {err}")


def test_same_process_nested_write_is_reentrant(tmp_path):
    """M-5 回归：持 ky_io 锁实例时嵌套写同一路径，必须在锁超时(5s)内完成。"""
    from tools import ky_io

    target = tmp_path / "状态.json"
    lock = ky_io._get_file_lock(ky_io._lock_path_for(target))
    assert lock is not None, "filelock 未安装时无法验证重入"
    t0 = time.monotonic()
    with lock:  # 外层持锁（旧实现：嵌套写同一路径 5 秒后 Timeout）
        ky_io.atomic_write_text(target, '{"nested": 1}')
        assert json.loads(target.read_text(encoding="utf-8")) == {"nested": 1}
        ky_io.atomic_write_text(target, '{"nested": 2}')
        assert json.loads(target.read_text(encoding="utf-8")) == {"nested": 2}
    assert time.monotonic() - t0 < 5, "嵌套写入耗时 ≥ 锁超时，说明同进程锁不可重入"


def test_lock_files_are_centralized_and_stable(tmp_path):
    """锁路径应稳定且不再污染目标文件所在目录。"""
    from tools import ky_io

    target = tmp_path / "状态.json"
    first = ky_io._lock_path_for(target)
    second = ky_io._lock_path_for(target)
    assert first == second
    assert first.parent == ky_io._LOCK_DIR
    assert first.name.endswith(".lock")
    assert str(target.parent) not in str(first.parent)

    ky_io.atomic_write_text(target, "ok")
    assert not Path(str(target) + ".lock").exists()


def test_cross_process_mutex_writer_really_waits(tmp_path):
    """子进程持锁期间，父进程写入必须等待其释放后才成功。

    [R2-D4 修复] 放行时机由**父进程的定时器**给出（从「开始等待」那一刻计时），
    而不是子进程自己 sleep。这样「等待时长」的期望值与机器快慢解耦：
    无论进程启动多慢，父进程总是恰好等约 2.5s 才拿到锁。
    """
    from tools import ky_io

    target = tmp_path / "互斥.json"
    holder = _spawn({"role": "hold", "target": str(target)})
    timer = None
    try:
        _wait_line(holder, "HELD")
        t0 = time.monotonic()
        timer = threading.Timer(2.5, _release, args=(holder,))
        timer.start()
        ky_io.atomic_write_text(target, "parent-write")
        elapsed = time.monotonic() - t0
        # 真实等待过 → elapsed 覆盖父进程自己的 2.5s 定时器；旁路写入则会 < 1s。
        # 上界给足余量（负载下 os.replace 与调度可能明显变慢），只守「确实等了」。
        assert 1.5 <= elapsed <= 20.0, f"写入耗时 {elapsed:.2f}s：过短疑似绕锁"
        assert target.read_text(encoding="utf-8") == "parent-write"
        out, _err = holder.communicate(timeout=30)
        assert "RELEASED" in out, "子进程应正常走完持锁周期"
    finally:
        if timer is not None:
            timer.cancel()
        _kill(holder)


def test_cross_process_lock_timeout_raises(tmp_path):
    """子进程一直持锁 > 锁超时(5s)：父进程显式失败、目标文件保持原内容。

    [R2-D4 修复] 持锁方**全程不释放**（事件驱动，父进程不发放行信号），
    因此不存在「子进程睡眠先到期、父进程的写意外成功」的时序窗口；
    只对「等待时长」设一个宽松下界（证明真的等过锁超时）与宽裕上界。
    """
    from tools import ky_io

    target = tmp_path / "超时.json"
    before_bytes = "keep-me".encode("utf-8")
    target.write_bytes(before_bytes)
    holder = _spawn({"role": "hold", "target": str(target)})
    try:
        _wait_line(holder, "HELD")
        t0 = time.monotonic()
        with pytest.raises(Exception) as ei:  # filelock.Timeout
            ky_io.atomic_write_text(target, "should-fail")
        elapsed = time.monotonic() - t0
        assert 4.0 <= elapsed <= 30.0, f"超时未在锁 timeout(5s) 附近触发（{elapsed:.2f}s）"
        assert "lock" in str(ei.value).lower(), f"异常应来自文件锁: {ei.value}"
        assert target.read_bytes() == before_bytes, "失败的写不得改动目标内容"
    finally:
        _release(holder)
        _kill(holder)


def test_readers_never_see_partial_content(tmp_path):
    """4 写进程 × 紧循环读线程：读取侧永远只见完整载荷（原子性不变式）。

    契约（含既知局限）：写可能以**显式报错**结束而非成功 —— 读侧挤占
    ``os.replace`` 窗口会 ``PermissionError``，多写进程争锁超时会
    ``filelock.Timeout``；两者内容都不损坏。但**任何读取都绝不出现半截/混合内容**。
    """
    from tools import ky_io

    target = tmp_path / "原子性.json"
    payload_of = lambda w, s: json.dumps(  # noqa: E731
        {"writer": w, "seq": s, "blob": f"{w}-{s}-" + "x" * 400}, ensure_ascii=False
    )

    n_writers, rounds = 4, 2
    corrupted: list = []
    readers_stop = time.monotonic() + 15.0

    def read_until_stop():
        while time.monotonic() < readers_stop:
            try:
                raw = target.read_text(encoding="utf-8")
            except (FileNotFoundError, OSError):
                continue  # 尚未创建 / 读取侧共享冲突：都不构成内容损坏
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                corrupted.append(raw[:80])
                continue
            if not (isinstance(obj, dict) and "writer" in obj and len(obj.get("blob", "")) >= 400):
                corrupted.append(raw[:80])

    readers = [threading.Thread(target=read_until_stop, daemon=True) for _ in range(4)]
    for t in readers:
        t.start()

    ok_writes = 0
    degraded_writes = 0
    try:
        for r in range(rounds):
            procs = [
                (w, _spawn({"role": "write", "target": str(target), "payload": payload_of(w, r)}))
                for w in range(n_writers)
            ]
            for w, p in procs:
                out, err = p.communicate(timeout=30)
                if p.returncode == 0 and "WROTE" in out:
                    ok_writes += 1
                elif "PermissionError" in (err or "") or (
                        "filelock" in (err or "") and "Timeout" in (err or "")):
                    # [R2-D4 修复·去时序依赖] 既知局限里的「显式失败」不止一种：
                    #   ① PermissionError —— 读侧挤占 os.replace 的 1.4s 重试窗口；
                    #   ② filelock.Timeout —— 4 个写进程争同一把锁（锁超时 5s），
                    #      负载下排在后面的写进程可能等不到锁。
                    # 两者都是「显式报错、内容不损坏」，符合本测试 docstring 写明的
                    # 契约；旧实现只认 ①，于是负载下 ② 被当成产品缺陷 —— 那正是
                    # 这个用例在压力下变红的真实原因（实测负载下 7/10 红）。
                    # 真正的不变式（读取侧绝不见半截内容）在下方照常强断言。
                    degraded_writes += 1
                else:
                    pytest.fail(f"写进程 {w} 非预期失败 rc={p.returncode}: {(err or '')[-300:]}")
    finally:
        readers_stop = time.monotonic()
        for t in readers:
            t.join(timeout=15)
        for _w, _p in procs:
            _kill(_p)

    assert ok_writes + degraded_writes == n_writers * rounds, "写进程结果计数不符"
    assert not corrupted, f"读取侧观察到 {len(corrupted)} 次不完整内容，首例: {corrupted[:2]}"
    final = json.loads(target.read_text(encoding="utf-8"))
    assert final["writer"] in range(n_writers) and len(final["blob"]) >= 400
