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

零第三方依赖，仅标准库。
"""

import os
import re
import tempfile
from pathlib import Path
from typing import Union

PathLike = Union[str, "os.PathLike[str]", Path]

#: Windows 文件名非法字符 + 各类换行/制表符（换行会让文件名被截断或产生诡异文件）
_ILLEGAL_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t\x00-\x1f]')

#: Windows 保留设备名（不区分大小写，且带扩展名也仍然保留）
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _align_to_umask(path: Path, mode: int = 0o666) -> None:
    """把临时文件权限对齐到「umask 默认」，与 `Path.write_text` 行为保持一致。

    `tempfile.mkstemp` 固定以 0600 创建文件；若直接 replace 到目标，POSIX 下会把
    项目文件悄悄变成「仅属主可读写」。Windows 不使用这些权限位，直接跳过。
    """
    if os.name == "nt":
        return
    try:
        current = os.umask(0)
        os.umask(current)
        os.chmod(path, mode & ~current)
    except OSError:
        pass


def atomic_write_text(path: PathLike, text: str, *, encoding: str = "utf-8",
                      newline: str = None, fsync: bool = True) -> Path:
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

    Returns:
        目标路径 Path
    """
    target = Path(path)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)

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
            _align_to_umask(tmp_path)
        os.replace(tmp_path, target)
        tmp_path = None        # 已改名成功，无需清理
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
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
    last_err: Exception = None
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
