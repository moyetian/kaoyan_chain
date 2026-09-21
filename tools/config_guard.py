# -*- coding: utf-8 -*-
"""ky_config.json 配置守卫 —— 快照 / 校验 / 还原。

背景（真实事故，2026-09-21）：
    仓库根的 ``ky_config.json`` 是开发者本人的真实备考配置（``.gitignore`` 保护、
    不在版本控制内），内含 39 字段 ``study_plan``（目标院校 / 报考专业 / 各科
    薄弱点与基线 / 时间分配 / 目标分）+ 51 字符真实 ``api_key`` + webhooks。

    它被**并发运行的临时验证脚本**覆写成了只剩 2 个键的测试夹具
    （``{"api_key": "sk-SHORT", "webhooks": {...}}``），且因为写入者绕过了
    ``tests/conftest.py`` 的绊线（那是 pytest 专用的），损坏**持续存在**并被
    反复覆盖，直到人工从历史副本恢复。

    ``tests/conftest.py`` 的绊线只覆盖 pytest 进程；本模块提供**与测试框架无关**
    的独立守卫，供任何脚本、会话或人工操作调用。

设计取向：
    * **可恢复优先**：不阻止写入（正常 ``ky config`` 也要写盘），但保证任何一次
      写入之前都有快照可回退。
    * **零依赖**：只用标准库，任何环境下都能跑。
    * **fail-closed 的 verify**：``verify()`` 返回 False 表示配置已偏离快照，
      调用方应视为错误而非警告。
    * **快照落位在仓库之外**（2026-09-21 CRITICAL 加固）：快照是 ``ky_config.json``
      的明文完整副本（含真实 ``api_key``）。它原先写在仓库根的
      ``.config_backup/``，只被 ``.gitignore`` 保护 —— 而导出副本走文件系统遍历，
      密钥快照会被镜像进公开仓库。现在默认落位见 :func:`default_backup_dir`，
      可用环境变量 ``KY_CONFIG_BACKUP_DIR`` 覆盖；旧的仓库内目录仍**只读兼容**。

用法::

    py tools/config_guard.py snapshot [--tag NAME]   # 记录快照
    py tools/config_guard.py verify                  # 校验当前配置是否偏离最近快照
    py tools/config_guard.py list                    # 列出全部快照
    py tools/config_guard.py restore [--tag NAME]    # 从快照还原
    py tools/config_guard.py auto-backup             # 写盘前自动留档（供代码调用）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "ky_config.json"
HISTORY = ROOT / "ky_history.json"

#: 备份目录的**环境变量覆盖点**（便于测试与用户自定义落位）。
BACKUP_DIR_ENV = "KY_CONFIG_BACKUP_DIR"

#: 旧的、**仓库内**的备份目录。
#:
#: 历史上快照写在这里，于是 ``ky_config.json`` 的**明文完整快照**（含 ``sk-``
#: 开头的真实 api_key）落在仓库根 —— 它只被 ``.gitignore`` 保护（git 层），而
#: 导出副本走的是**文件系统遍历**，只认 ``dir_should_exclude`` /
#: ``file_should_exclude``，密钥快照因此会被镜像进公开副本（CRITICAL）。
#:
#: 现在**新写入一律去 :func:`default_backup_dir`（仓库之外）**；本常量只作为
#: **只读兼容**来源，保证迁移之前的历史快照不失效。
LEGACY_BACKUP_DIR = ROOT / ".config_backup"


def _workspace_slug(root: Path) -> str:
    """工作区专属子目录名：可读目录名 + 路径 sha1 前 8 位。

    必须**按工作区隔离**：``tools/`` 会被整棵复制进沙箱后以子进程方式运行
    （例如 ``tests/test_fix_safe_mode_write_gate.py`` 的 ``sandbox_workspace``），
    此时 ``ROOT`` 指向沙箱根。若所有工作区共用一个全局备份目录，沙箱里的
    ``auto_backup()`` 就会把**测试夹具配置**写进用户真实的备份目录与账本 ——
    实测：跑一次全量测试后，真实备份目录里多出一份内容为 ``沙箱院校`` 的
    264 字节 ``ky_config.auto.*.json``。修复前它落在沙箱自己的
    ``.config_backup`` 里、随 ``tmp_path`` 一起销毁，不能因为「移出仓库根」
    而把这个隔离性质丢掉。
    """
    readable = re.sub(r"[^0-9A-Za-z_.-]+", "-", root.name).strip("-.")
    digest = hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:8]
    return f"{readable}-{digest}" if readable else digest


def default_backup_dir() -> Path:
    """备份目录的默认落位：**仓库之外**的稳定子目录（按工作区隔离）。

    取值优先级：
        1. 环境变量 ``KY_CONFIG_BACKUP_DIR``（测试 / 用户自定义落位）；
        2. Windows：``%LOCALAPPDATA%\\kaoyan-study-chain\\config_backup\\<工作区>``；
        3. 其它平台：``$XDG_DATA_HOME``（缺省 ``~/.local/share``）下的
           ``kaoyan-study-chain/config_backup/<工作区>`` —— 主目录下的隐藏目录。

    ``<工作区>`` 由 :func:`_workspace_slug` 从 ``ROOT`` 推导，保证「同一工作区
    稳定、不同工作区隔离」。

    放在仓库之外是**结构性**修复：无论将来谁再加一条导出路径、或忘了同步
    排除名单，明文密钥快照都不在任何文件系统遍历范围内。排除名单
    （``privacy_policy.DEV_SCRATCH_DIRS`` / ``sync_publish.EXCLUDE_DIRS``）
    仍然保留，作为第二层纵深防御。
    """
    override = os.environ.get(BACKUP_DIR_ENV)
    if override and override.strip():
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return (Path(base) / "kaoyan-study-chain" / "config_backup"
                    / _workspace_slug(ROOT))
    xdg = os.environ.get("XDG_DATA_HOME")
    home_base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return (home_base / "kaoyan-study-chain" / "config_backup"
            / _workspace_slug(ROOT))


#: 快照目录（仓库之外；内含真实 api_key，绝不入库、绝不进导出遍历范围）
BACKUP_DIR = default_backup_dir()
LEDGER = BACKUP_DIR / "snapshots.json"


def _active_backup_dir() -> Path:
    """本次调用实际使用的备份目录。

    [S7 修复·import 期冻结] 旧实现 ``BACKUP_DIR = default_backup_dir()``
    在 import 期求值：进程内后改 ``KY_CONFIG_BACKUP_DIR`` 不生效。
    此处每次调用重算环境变量覆盖（测试仍可 monkeypatch BACKUP_DIR 全局量，
    无环境变量时回落到它，行为兼容）。
    """
    override = os.environ.get(BACKUP_DIR_ENV)
    if override and override.strip():
        return Path(override).expanduser()
    return Path(BACKUP_DIR)


def _tighten_snapshot_perms(path: Path) -> None:
    """[S7 修复·明文快照权限] 快照是 ky_config.json 的明文完整副本（含真实
    api_key）。POSIX 下目录收紧 0700、文件 0600；Windows 无影响。失败只留痕。"""
    if os.name == "nt":
        return
    try:
        target = Path(path)
        if target.is_dir():
            os.chmod(target, 0o700)
        else:
            if target.parent.exists():
                os.chmod(target.parent, 0o700)
            os.chmod(target, 0o600)
    except OSError as e:
        import logging
        logging.getLogger(__name__).debug("快照权限收紧失败（已忽略）: %s: %s", path, e)

#: ``auto-backup`` 保留的自动档份数上限（按时间倒序）
AUTO_KEEP = 15


def _sha256(path: Path) -> Optional[str]:
    """文件内容的 sha256；文件不存在返回 None。"""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _shape(path: Path) -> str:
    """给人看的形状摘要：顶层字段数 / study_plan 字段数 / api_key 长度。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "（无法解析）"
    plan = data.get("study_plan") or {}
    key = str(data.get("api_key") or "")
    return (f"顶层 {len(data)} 字段 / study_plan {len(plan)} 字段 / "
            f"api_key {len(key)} 字符")


def _backup_dirs() -> List[Path]:
    """按「新位置优先」顺序返回所有快照目录（含只读兼容的旧仓库内目录）。"""
    dirs = [_active_backup_dir()]
    legacy = Path(LEGACY_BACKUP_DIR)
    if legacy != dirs[0]:
        dirs.append(legacy)
    return dirs


def _resolve_snapshot(name: str) -> Optional[Path]:
    """在「新位置 → 旧仓库内目录」中按序查找快照文件；都不存在返回 None。"""
    for base in _backup_dirs():
        candidate = base / name
        try:
            if candidate.exists():
                return candidate
        except OSError:  # pragma: no cover - 目录不可访问时视为不存在
            continue
    return None


def _load_ledger_file(path: Path) -> List[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _load_ledger() -> List[dict]:
    """读取全部快照记录：**旧仓库内目录在前、新目录在后**。

    ``verify`` 与裸 ``restore`` 都取「最后一条」，所以这个顺序就是
    「**新位置优先**」。旧目录里的历史快照仍然可见（向后兼容），迁移动作
    不会让它们失效。重复条目（同 tag/path/sha256）只保留先出现的那份。
    """
    merged: List[dict] = []
    seen = set()
    for base in reversed(_backup_dirs()):        # 旧位置先入列 → 新位置在后
        for entry in _load_ledger_file(base / "snapshots.json"):
            if not isinstance(entry, dict) or not entry.get("path"):
                continue
            key = (entry.get("tag"), entry.get("path"), entry.get("sha256"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(entry)
    return merged


def _save_ledger(entries: List[dict]) -> None:
    active = _active_backup_dir()
    active.mkdir(parents=True, exist_ok=True)
    _tighten_snapshot_perms(active)
    ledger = active / "snapshots.json"
    tmp = ledger.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(ledger)


#: 同进程账本线程锁注册表（按备份目录实例唯一，保证可重入 exclusivity）。
#: msvcrt/fcntl 只防跨进程；同进程线程必须先过这一层（见 _hold_ledger_lock）。
_LEDGER_THREAD_LOCKS: dict = {}
_LEDGER_THREAD_LOCKS_GUARD = threading.Lock()


def _hold_ledger_lock() -> "contextlib.AbstractContextManager[bool]":
    """账本互斥（[G6 修复·并发丢条目]）。

    旧实现 ``_load_ledger → append → _save_ledger`` 非原子：两进程并发
    auto_backup 会丢一条记录。``tmp.replace`` 只保证单次写原子，不保 RMW。
    此处用标准库文件锁（Windows msvcrt / POSIX fcntl）把"读-改-写"包成临界区；
    加锁失败时降级为无锁（返回 False，调用方仍执行，避免单点卡死）。
    """
    import contextlib

    @contextlib.contextmanager
    def _locked():
        # [G6-2 修复] msvcrt 锁是 per-handle 的：同进程多线程各 open 一次，
        # 重叠区域锁互不排斥（实测 12 线程并发仍丢条目 + tmp.replace 撞车）。
        # 先拿同进程 threading.Lock（按目录实例唯一），再拿跨进程文件锁。
        key = str(_active_backup_dir())
        with _LEDGER_THREAD_LOCKS_GUARD:
            tlock = _LEDGER_THREAD_LOCKS.get(key)
            if tlock is None:
                tlock = threading.Lock()
                _LEDGER_THREAD_LOCKS[key] = tlock
        tlock.acquire()
        try:
            _active_backup_dir().mkdir(parents=True, exist_ok=True)
            lock_path = _active_backup_dir() / "snapshots.lock"
            fh = None
            locked = False
            try:
                fh = open(lock_path, "a+b")
                try:
                    if os.name == "nt":
                        import msvcrt
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                    locked = True
                except Exception:
                    locked = False
                yield locked
            finally:
                if fh is not None:
                    try:
                        if locked:
                            if os.name == "nt":
                                import msvcrt
                                fh.seek(0)
                                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                            else:
                                import fcntl
                                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                    except Exception:
                        pass
                    try:
                        fh.close()
                    except Exception:
                        pass
        finally:
            tlock.release()

    return _locked()


def snapshot(tag: str = "manual", expected: bool = True) -> Optional[Path]:
    """把当前 ``ky_config.json`` 存成一份具名快照，返回快照路径。

    文件名形如 ``ky_config.<tag>.<timestamp>.json``；同一 tag 重复调用会各留一份
    （快照只增不删，避免"最新一份恰好是坏的"）。

    Args:
        tag: 快照标签。
        expected: 该快照是否代表**期望状态**。只有期望状态快照才会被
            :func:`verify` 当作比对基准。``auto``（写盘前自动留档）与
            ``before_restore``（还原前的现场留证）都必须传 ``False`` ——
            它们记录的是「上一次的内容」或「损坏现场」，拿它们当基准会让
            ``verify`` 把正确的配置报成偏离。
    """
    if not CONFIG.exists():
        print(f"[guard] 未找到 {CONFIG}，跳过快照")
        return None
    active = _active_backup_dir()
    active.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    dest = active / f"ky_config.{tag}.{ts}.json"
    shutil.copyfile(CONFIG, dest)
    _tighten_snapshot_perms(dest)
    digest = _sha256(dest)
    # [G6] 读-改-写在锁内完成，避免并发 auto_backup 丢条目。
    with _hold_ledger_lock():
        entries = _load_ledger()
        entries.append({
            "tag": tag,
            "path": dest.name,
            "sha256": digest,
            "size": dest.stat().st_size,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "shape": _shape(dest),
            "expected": bool(expected),
        })
        _save_ledger(entries)
    kind = "期望状态快照" if expected else "留证快照（不作 verify 基准）"
    print(f"[guard] {kind}已保存：{dest.name}（{_shape(dest)}）")
    return dest


def auto_backup() -> Optional[Path]:
    """写盘前自动留档；由 ``save_config()`` 等写入方调用，并自动裁剪旧档。

    与 :func:`snapshot` 的区别：只保留最近 ``AUTO_KEEP`` 份，避免长期使用后
    无限膨胀。
    """
    dest = snapshot(tag="auto", expected=False)
    if dest is None:
        return None
    # [G6] 裁剪同样在锁内：glob+unlink 与并发 snapshot 交错可能删掉刚写入的档。
    with _hold_ledger_lock():
        autos = sorted(_active_backup_dir().glob("ky_config.auto.*.json"))
        for stale in autos[:-AUTO_KEEP]:
            try:
                stale.unlink()
            except OSError:
                pass
    return dest


def list_snapshots() -> List[dict]:
    """返回全部快照记录（按记录顺序）。"""
    return _load_ledger()


def verify(quiet: bool = False) -> bool:
    """校验当前配置是否与**最近一份快照**一致。

    [B2 修复·fail-closed] 以下两种"无从判断"的情况此前返回 True
    （fail-open）：删掉快照目录即静默解除守卫。现一律返回 False，
    调用方（CLI exit 码）将其视为错误。

    Returns:
        True  —— 未偏离。
        False —— 已偏离，或无快照可比对，或快照文件缺失（调用方应视为错误）。
    """
    entries = [e for e in _load_ledger() if e.get("expected", True)]
    if not entries:
        if not quiet:
            print("[guard] ✗ 尚无「期望状态」快照可比对（fail-closed：视为未通过）；"
                  "请先运行 `snapshot` 记录基准")
        return False

    last = entries[-1]
    snap_path = _resolve_snapshot(last["path"])
    cur_digest = _sha256(CONFIG)
    snap_digest = _sha256(snap_path) if snap_path is not None else None

    if snap_digest is None:
        if not quiet:
            print(f"[guard] ✗ 快照文件缺失：{last['path']}（fail-closed：视为未通过）")
        return False
    if cur_digest is None:
        if not quiet:
            print(f"[guard] ✗ 当前配置不存在（快照 {last['tag']} 时为 {last['shape']}）")
        return False
    if cur_digest == snap_digest:
        if not quiet:
            print(f"[guard] ✓ 配置未偏离最近快照（{last['tag']} @ {last['time']}）")
        return True

    if not quiet:
        print(f"[guard] ✗ 配置已偏离最近快照！")
        print(f"        快照 {last['tag']} @ {last['time']}：{last['shape']}")
        print(f"        当前：{_shape(CONFIG)}")
        print(f"        快照 sha256={snap_digest[:16]}  当前 sha256={cur_digest[:16]}")
        print(f"        还原：py tools/config_guard.py restore --tag {last['tag']}")
    return False


def restore(tag: Optional[str] = None, force: bool = False) -> bool:
    """从快照还原 ``ky_config.json``。

    Args:
        tag: 快照 tag；None 表示最近一份**期望状态**快照（与 :func:`verify`
            的基准定义一致）。显式指定 tag 时按 tag 取，不受 ``expected`` 限制。
        force: 还原前是否把当前（可能已损坏的）内容也留一份档。

    Returns:
        是否成功还原。
    """
    entries = _load_ledger()
    if not entries:
        print("[guard] 没有任何快照可还原")
        return False

    if tag is None:
        # [缺陷修复] 裸 restore 必须与 verify() 用**同一个**「基准快照」定义
        # （只认 expected=True）。否则 `restore --force` 先写下的
        # before_restore（expected=False，内容是**损坏现场**）会成为「最近
        # 快照」，此后一次裸 restore 会把刚修好的配置重新写回损坏版本 ——
        # 恰好是「还原之后又被污染」的成因。
        baselines = [e for e in entries if e.get("expected", True)]
        if not baselines:
            print("[guard] 没有可作为还原基准的「期望状态」快照；"
                  "请先用 `snapshot` 记录一份，或显式 `restore --tag <名称>` "
                  "指定留证快照")
            return False
        chosen = baselines[-1]
    else:
        matches = [e for e in entries if e["tag"] == tag]
        if not matches:
            print(f"[guard] 未找到 tag={tag} 的快照；可用："
                  f"{sorted({e['tag'] for e in entries})}")
            return False
        chosen = matches[-1]

    src = _resolve_snapshot(chosen["path"])
    if src is None:
        print(f"[guard] 快照文件不存在：{chosen['path']}"
              f"（已查找：{', '.join(str(d) for d in _backup_dirs())}）")
        return False

    if force and CONFIG.exists():
        snapshot(tag="before_restore", expected=False)
    # [S7 修复·非原子覆盖] 旧 shutil.copyfile 直接覆盖，并发读取者可见半文件。
    # 先写同目录临时文件再 os.replace（与 ky_io.atomic_write_text 同语义，
    # 此处保持标准库零依赖，不跨模块导入）。
    tmp_restore = CONFIG.with_name(CONFIG.name + ".restore.tmp")
    try:
        shutil.copyfile(src, tmp_restore)
        os.replace(tmp_restore, CONFIG)
    finally:
        try:
            if tmp_restore.exists():
                tmp_restore.unlink()
        except OSError:
            pass
    print(f"[guard] 已从快照还原：{chosen['path']}（{chosen['shape']}）")
    return True


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="config_guard",
        description="ky_config.json 快照 / 校验 / 还原守卫",
    )
    sub = parser.add_subparsers(dest="cmd")

    p_snap = sub.add_parser("snapshot", help="记录一份快照")
    p_snap.add_argument("--tag", default="manual")

    sub.add_parser("verify", help="校验当前配置是否偏离最近快照")
    sub.add_parser("list", help="列出全部快照")
    sub.add_parser("auto-backup", help="写盘前自动留档并裁剪旧档")

    p_restore = sub.add_parser("restore", help="从快照还原")
    p_restore.add_argument("--tag", default=None)
    p_restore.add_argument("--force", action="store_true",
                           help="还原前先把当前内容留一份档")

    args = parser.parse_args(argv)

    if args.cmd == "snapshot":
        return 0 if snapshot(args.tag) else 1
    if args.cmd == "verify":
        return 0 if verify() else 1
    if args.cmd == "list":
        entries = list_snapshots()
        if not entries:
            print("[guard] 暂无快照")
            return 0
        for i, e in enumerate(entries):
            mark = "★" if e.get("expected", True) else " "
            print(f"  [{i}]{mark} {e['tag']:<16} {e['time']}  {e['shape']}  {e['path']}")
        print("  （★ = 期望状态快照，verify 以此为基准）")
        return 0
    if args.cmd == "auto-backup":
        return 0 if auto_backup() else 1
    if args.cmd == "restore":
        return 0 if restore(args.tag, force=args.force) else 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    raise SystemExit(main())
