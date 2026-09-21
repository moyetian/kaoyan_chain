# -*- coding: utf-8 -*-
"""
考研学习链 (Kaoyan AI Study Chain) · 公共 IO 与路径安全工具
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
本模块存在的唯一目的：把「防御只做了一半」变成「只做一次」。

此前项目里同一个防御在不同文件里各写一遍，于是出现了
「material_scanner.py 用了 tmp+replace 原子写，而 ky_config.json 却是裸 write_text」
这类不一致 —— 一旦写入中途被打断，ky_config.json 会被截成半截 JSON，
而它不在版本控制内，无法恢复，整个系统随即启动即崩。

提供三类能力：
  1. atomic_write_text   —— 原子写文本（同目录临时文件 + fsync + os.replace）
  2. safe_filename       —— 把任意字符串变成安全的单层文件名（防路径穿越）
  3. is_within           —— 路径包含关系判定（供写入前的边界断言）

依赖说明
--------
* **必需**：仅 Python 标准库。
* **可选**：跨进程写互斥使用 ``filelock``；未安装时自动降级为"无跨进程锁"
  （同进程内的可重入保护仍然生效，见 :func:`_get_file_lock`）。
  这是本项目唯一引入的第三方 IO 依赖，且**不影响功能正确性**，仅影响多进程并发写同一文件时的互斥强度。
"""

import hashlib
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Union

PathLike = Union[str, "os.PathLike[str]", Path]

#: Windows 文件名非法字符 + 各类换行/制表符（换行会让文件名被截断或产生诡异文件）
_ILLEGAL_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t\x00-\x1f]')


# ── 严格只读模式（--permission=safe）全局闸门 ──────────────────────────────
# [D2 修复] 此前 safe 模式只在 PermissionManager.check_permission 里判定，而
# memory prune / rollback / 各类落盘命令**根本不经过该函数**，于是"只读模式"
# 仍能修剪记忆库、回滚快照 —— 安全承诺在真实入口上失效。
# 现把闸门下沉到统一写入口（本模块 atomic_write_text），由 ky_cli 启动时置位，
# 保证任何写入路径（含未接入 PermissionManager 的旧命令）都被拦住。
_READ_ONLY_MODE = False

#: 同一份 ky_io.py 在本项目里会被加载成多个模块别名（``tools.ky_io`` 与 ``ky_io``，
#: 取决于调用方是 ``from tools import ky_io`` 还是 ``from ky_io import ...``）。
#: 它们是**互相独立的模块对象**，各自持有一份 ``_READ_ONLY_MODE``。
_KY_IO_ALIASES = ("tools.ky_io", "ky_io")


class PermissionDeniedError(RuntimeError):
    """只读模式下尝试写盘时抛出。"""


def _sibling_modules() -> List[object]:
    """返回当前进程中指向**同一份 ky_io.py** 的所有其它已加载模块别名。

    以 ``__file__`` 而非模块名判定身份：只有真·同一份源码的别名才共享标志，
    避免把同名但来自其它安装目录的模块误判成兄弟。
    """
    here = os.path.abspath(__file__)
    self_mod = sys.modules.get(__name__)
    out: List[object] = []
    for name in _KY_IO_ALIASES:
        mod = sys.modules.get(name)
        if mod is None or mod is self_mod:
            continue
        f = getattr(mod, "__file__", None)
        if f and os.path.abspath(f) == here:
            out.append(mod)
    return out


def set_read_only_mode(enabled: bool) -> None:
    """置位/取消全局严格只读模式（由 CLI 解析 --permission=safe 后调用）。

    [P0 修复·双导入] 必须**同步到同一份源码的所有模块别名**。此前只置自己那一份，
    写入方若走另一个别名，``atomic_write_text`` 里的 ``guard_write`` 看到的仍是
    ``False`` —— 闸门形同虚设（实测 ``ky mount`` 在 ``--permission=safe`` 下照样
    改写 ``ky_config.json`` 与 ``AGENTS.md`` 并 exit=0）。
    """
    global _READ_ONLY_MODE
    _READ_ONLY_MODE = bool(enabled)
    for mod in _sibling_modules():
        mod._READ_ONLY_MODE = bool(enabled)


def is_read_only_mode() -> bool:
    """当前是否处于严格只读模式。

    **fail-closed**：任一别名报告只读即视为只读。仅靠 ``set_read_only_mode``
    同步一次不够 —— 若某个别名是在标志置位**之后**才首次被 import，它的
    ``_READ_ONLY_MODE`` 会以模块初始值 ``False`` 起跑；此处跨别名判定 + 导入期
    继承（见模块末尾的 ``_adopt_read_only_mode_from_aliases``）双重兜住。
    """
    if _READ_ONLY_MODE:
        return True
    for mod in _sibling_modules():
        if getattr(mod, "_READ_ONLY_MODE", False):
            return True
    return False


def guard_write(op: str, target: PathLike = "") -> None:
    """写入前统一校验；只读模式下直接拒绝。

    op 用于在报错信息里说明被拦截的动作（如 "memory prune"、"写入报告"）。
    """
    if is_read_only_mode():
        where = f" -> {target}" if target else ""
        raise PermissionDeniedError(
            f"当前处于严格只读模式 (--permission=safe)，已拒绝非只读操作 [{op}]{where}")


#: Windows 保留设备名（不区分大小写，且带扩展名也仍然保留）
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


# ── 进程内文件锁注册表（保证同一路径的锁**实例唯一**，从而可重入）──────────
# [缺陷修复] 旧实现每次调用都新建 FileLock 实例。filelock 的可重入性只对
# **同一个实例**成立；同进程内两个实例争抢同一路径会互斥，实测表现为
# "写 A 的过程中又写 A" 在 5 秒后抛 Timeout（潜在死锁）。
# 现按路径缓存实例，使同进程嵌套写入安全通过（filelock 内部计数）。
_LOCKS: Dict[str, object] = {}
_LOCKS_GUARD = threading.Lock()

# 锁文件必须在各进程间保持稳定，且不能在每次释放后删除：等待中的进程仍可能
# 持有该路径的句柄。把锁集中到系统临时目录可避免在每个学习文件旁留下
# ``*.lock``，同时不要求已安装的包目录可写。
_LOCK_DIR = Path(tempfile.gettempdir()) / "kaoyan-study-chain" / "locks"


def _lock_path_for(target: PathLike) -> Path:
    """返回目标文件的稳定集中式锁路径。

    Windows 路径大小写不敏感，统一 ``casefold``，确保不同写法仍争用同一把锁；
    只把路径摘要写入临时目录，避免泄露用户目录或产生非法文件名。
    """
    resolved = str(Path(target).resolve(strict=False))
    identity = resolved.casefold() if os.name == "nt" else resolved
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    _LOCK_DIR.mkdir(parents=True, exist_ok=True)
    return _LOCK_DIR / f"{digest}.lock"


def _get_file_lock(lock_path: Path):
    """取得该路径对应的**单例**文件锁；filelock 不可用时返回 None。"""
    key = str(lock_path)
    with _LOCKS_GUARD:
        if key not in _LOCKS:
            lock = None
            try:
                from filelock import FileLock
                lock = FileLock(lock_path, timeout=5)
            except ImportError:
                lock = None
            _LOCKS[key] = lock
        return _LOCKS[key]


#: 进程 umask 只探测一次并缓存。
# [缺陷修复] 旧实现每次写入都 `os.umask(0)` 再还原，两次调用之间存在窗口：
# 此刻其他线程创建的文件会拿到 0 权限（TOCTOU 竞态）。改为进程启动后首次
# 需要时探测一次并缓存（探测本身在锁内完成）。
_UMASK: Optional[int] = None
_UMASK_GUARD = threading.Lock()


def _current_umask() -> int:
    if os.name == "nt":
        return 0
    global _UMASK
    with _UMASK_GUARD:
        if _UMASK is None:
            current = os.umask(0)
            os.umask(current)
            _UMASK = current
        return _UMASK


#: os.replace 的瞬时失败重试次数与退避基数（秒）。
# 8 次 × 线性退避 ≈ 最长 1.4 秒重试窗口，足以覆盖真实场景的瞬时读占用；
# 极端压测（1 写 × 4 读 无间隔循环）下仍可能偶发失败，但**文件内容永不损坏**。
_REPLACE_RETRIES = 8
_REPLACE_BACKOFF = 0.04


def _atomic_replace(tmp_path: Path, target: Path) -> None:
    """`os.replace` 的健壮封装：**仅针对 Windows 的瞬时占用冲突**做有限重试。

    [Windows 缺陷修复] 在 Windows 上，若目标文件此刻正被其他线程/进程以
    ``Path.read_text()`` 之类"不共享删除权限"的方式打开，``os.replace`` 会抛
    ``PermissionError: [WinError 5] 拒绝访问``。实测 20 写 × 4 读并发下必然触发，
    表现为"只是读了一下状态文件，写入就崩了"。
    文件内容本身不会损坏（读取方从未观察到半截内容），但写入会失败，
    故此处按 50ms 线性退避重试若干次；仍失败则原样抛出，交由调用方处理。
    """
    last_err: Optional[BaseException] = None
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(tmp_path, target)
            return
        except PermissionError as e:  # 仅 Windows 的共享冲突属可重试情形
            last_err = e
            if os.name != "nt":
                raise
            time.sleep(_REPLACE_BACKOFF * (attempt + 1))
    if last_err is not None:
        raise last_err


def _align_to_umask(path: Path, mode: int = 0o666, sensitive: bool = False) -> None:
    """把临时文件权限对齐到「umask 默认」，与 `Path.write_text` 行为保持一致。

    `tempfile.mkstemp` 固定以 0600 创建文件；若直接 replace 到目标，POSIX 下会把
    项目文件悄悄变成「仅属主可读写」。Windows 不使用这些权限位，直接跳过。

    ``sensitive=True`` 时收紧为 ``0600``（仅属主可读写），用于含 API Key /
    Webhook 等凭证的配置文件 —— 此时**不再**按 umask 放宽为 0644，避免同机
    其他用户可读。
    """
    if os.name == "nt":
        return
    target_mode = 0o600 if sensitive else (mode & ~_current_umask())
    try:
        os.chmod(path, target_mode)
    except OSError as e:
        # 权限对齐是尽力而为：失败不影响写入内容，但留痕以便排查权限异常
        import logging
        logging.getLogger(__name__).debug("chmod 权限对齐失败（已忽略）: %s -> %s", path, e)


def atomic_write_text(path: PathLike, text: str, *, encoding: str = "utf-8",
                      newline: str = None, fsync: bool = True,
                      sensitive: bool = False) -> Path:
    """原子写文本：先写同目录临时文件，再 `os.replace` 覆盖目标。

    为什么必须同目录建临时文件：`os.replace` 只有在同一文件系统内才是原子的。
    写到系统临时目录再 replace 可能退化为「复制 + 删除」，不再原子。

    失败语义：任何异常都会清理临时文件后原样抛出（绝不留下半截目标文件，
    也绝不静默吞掉错误）。调用方可据此判断写入是否成功。

    Args:
        path: 目标路径（父目录不存在时会自动创建）
        text: 要写入的文本
        encoding: 编码，默认 utf-8
        newline: 换行控制；默认 None 表示不做转换（保持文本原样）
        fsync: 是否 fsync 落盘。默认 True（防断电丢数据）；
               高频小文件写入可传 False 换取一点性能。
        sensitive: 目标文件是否含凭证。POSIX 下为 True 时权限收紧为 0600，
                   避免 API Key / Webhook 被同机其他用户读取（Windows 无影响）。

    Returns:
        目标路径 Path
    """
    target = Path(path)
    guard_write("原子写文本", target)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)

    # 引入跨进程咨询锁（同进程同路径复用同一实例，保证可重入）。
    # FileLock 的锁文件是持久同步标识，不能在释放后主动 unlink；否则等待中的
    # 进程可能绕过互斥。集中式摘要路径既保留同步语义，也不污染学习资料目录。
    lock_path = _lock_path_for(target)
    lock = _get_file_lock(lock_path)
    if lock is None:
        import contextlib
        lock = contextlib.nullcontext()

    with lock:
        # 写前比对，避免无意义的 mtime 抖动
        try:
            if target.exists() and target.read_text(encoding=encoding) == text:
                # [缺陷修复] 内容未变也要补一次权限对齐再短路返回。
                # 否则：旧版本以 umask 0644 创建的 ky_config.json，若本次写入
                # 内容与磁盘完全一致，就会在 _align_to_umask 之前 return，
                # sensitive=True 的 0600 收紧被永久跳过 —— 恰好在「升级加固」
                # 场景下失效（加固动作写的内容与旧内容相同）。
                # Windows 上 _align_to_umask 直接返回，不影响跨平台一致性。
                _align_to_umask(target, sensitive=sensitive)
                return target
        except Exception as e:
            # 读旧内容只为「内容未变则跳过写入」的优化；读失败只说明无法短路，
            # 照常落盘即可，但需留痕以免掩盖真实的编码/权限问题
            import logging
            logging.getLogger(__name__).debug("写前内容比对失败（照常写入）: %s -> %s", target, e)

        fd = None
        tmp_path = None
        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".tmp", dir=str(parent))
            tmp_path = Path(tmp_name)
            with os.fdopen(fd, "w", encoding=encoding, newline=newline) as f:
                fd = None          # 已交给文件对象，避免重复 close
                f.write(text)
                f.flush()
                if fsync:
                    os.fsync(f.fileno())
                # mkstemp 建出来的文件权限是 0600（仅属主可读写），而 Path.write_text
                # 走的是 umask 默认（通常 0644）。若不做对齐，本函数在 POSIX 上会
                # 悄悄把项目文件变成「只有自己能读」，破坏跨平台一致性。
                # sensitive=True（凭证类文件）则保持 0600 不放宽。
                _align_to_umask(tmp_path, sensitive=sensitive)
            _atomic_replace(tmp_path, target)
            tmp_path = None        # 已改名成功，无需清理
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError as e:
                    import logging
                    logging.getLogger(__name__).debug("关闭临时文件句柄失败（进程退出时会回收）: %s", e)
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError as e:
                    # 清理失败只会留下一个 .tmp 残留，目标文件内容不受影响
                    import logging
                    logging.getLogger(__name__).debug("清理临时文件失败: %s -> %s", tmp_path, e)

    return target


def safe_filename(name: str, fallback: str = "未命名", max_length: int = 120) -> str:
    """把任意字符串规范化为**安全的单层文件名**。

    防御的是「用户/外部数据被直接拼进文件路径」导致的目录穿越与写出越界，
    例如 `file_name = "../../04-专业课/考试大纲.md"` 这类载荷。

    处理内容：
      * 路径分隔符与 Windows 非法字符 → `_`（因此结果永远不含路径层级）
      * 开头的 `.` 与结尾的空格/点（Windows 会静默丢弃结尾点，造成名实不符）
      * Windows 保留设备名（CON/PRN/AUX/NUL/COM1-9/LPT1-9）
      * 超长截断、空串回退 fallback
    """
    s = _ILLEGAL_FILENAME_CHARS.sub("_", str(name or "")).strip()
    s = s.strip().strip(".")
    if not s:
        s = fallback
    # 保留名判定要去掉扩展名，因为 Windows 下 "CON.txt" 同样非法
    if s.split(".")[0].upper() in _WINDOWS_RESERVED_NAMES:
        s = f"_{s}"
    if len(s) > max_length:
        # 尽量保留扩展名，便于下游按后缀判断类型
        stem, dot, ext = s.rpartition(".")
        if dot and len(ext) <= 10:
            s = stem[:max_length - len(ext) - 1] + "." + ext
        else:
            s = s[:max_length]
    return s or fallback


def is_within(child: PathLike, parent: PathLike) -> bool:
    """判断 child 是否位于 parent 目录之内（含自身）。

    用于写入前的边界断言：`is_within(target_file, expected_dir)`。
    路径不存在也可判定（基于 resolve 后的字符串比较，不要求真实存在）。
    """
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except (ValueError, OSError):
        return False


def read_text_fallback(path: PathLike, encodings=("utf-8-sig", "utf-8", "gbk")) -> str:
    """按候选编码依次尝试读取文本，全部失败则抛出最后一个异常。

    替代 `read_text(encoding="utf-8", errors="ignore")` —— 后者会把无法解码的
    字节**静默丢弃**，GBK 编码的中文资料会被整片吞掉，而调用方仍以为读取成功
    （「成功入库 N 道题」但内容残缺）。此处宁可显式失败，也不静默丢数据。
    """
    p = Path(path)
    last_err: Optional[Exception] = None
    for enc in encodings:
        try:
            return p.read_text(encoding=enc)
        except UnicodeDecodeError as e:
            last_err = e
            continue
        except (OSError, LookupError) as e:
            last_err = e
            break
    if last_err is not None:
        raise last_err
    return p.read_text(encoding=encodings[0] if encodings else "utf-8")


def _adopt_read_only_mode_from_aliases() -> None:
    """模块导入时从已加载的兄弟别名继承只读标志。

    覆盖的时序：``dispatch`` 先 ``set_read_only_mode(True)``，之后某个模块才
    首次 ``import`` 另一个别名。新别名若以 ``False`` 起跑，它那条写入路径就会
    绕过闸门；导入时主动继承即可闭合。
    """
    global _READ_ONLY_MODE
    if _READ_ONLY_MODE:
        return
    for mod in _sibling_modules():
        if getattr(mod, "_READ_ONLY_MODE", False):
            _READ_ONLY_MODE = True
            return


_adopt_read_only_mode_from_aliases()
