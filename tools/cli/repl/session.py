# -*- coding: utf-8 -*-
"""
REPL 会话状态与系统剪贴板管理 (session.py)
跨平台截图获取、剪贴板纯文本读取、会话状态上下文
"""

import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOG = logging.getLogger(__name__)

try:
    from tools.cli.shared import ROOT
except ImportError:
    try:
        from cli.shared import ROOT
    except ImportError:
        ROOT = Path(__file__).resolve().parent.parent.parent

@dataclass
class ReplSession:
    """REPL 运行期会话状态上下文"""
    permission_mode: str = "ask"
    gateway_host: str = "127.0.0.1"
    gateway_token: str = ""
    active_subject: str = "math"
    history: List[Dict[str, Any]] = field(default_factory=list)
    live_port: int = 8088

def _read_win32_clipboard_text() -> Optional[str]:
    """通过 Win32 API 原生极速读取 Unicode 剪贴板文本 (<0.1ms, 免子进程, 纯 UTF-16)。"""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        CF_UNICODETEXT = 13

        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL

        opened = False
        for _ in range(3):
            if user32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.01)
        if not opened:
            return None

        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return None
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                return None
            try:
                val = ctypes.c_wchar_p(pointer).value
                return str(val) if val is not None else ""
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()
    except Exception as e:
        _LOG.debug("Win32 clipboard read failed: %s", e)
        return None


def grab_clipboard_image() -> Optional[Path]:
    """尝试从系统剪贴板读取图片，保存为 tools/scratch/uploads/clip_*.png 并返回路径。"""
    upload_dir = ROOT / "tools" / "scratch" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    target_path = upload_dir / f"clip_{int(time.time() * 1000)}.png"

    # 1. Pillow 读取 (支持 Mac/Linux/Win)
    try:
        from PIL import ImageGrab
        im = ImageGrab.grabclipboard()
        if im is not None:
            if hasattr(im, "save"):
                im.save(str(target_path), "PNG")
                return target_path
            elif isinstance(im, list):
                for item in im:
                    p = Path(item)
                    if p.is_file() and p.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp', '.bmp'):
                        return p
    except Exception as e:
        _LOG.debug("Pillow grabclipboard error: %s", e)

    # 2. Windows PowerShell 底层读取剪贴板位图
    if sys.platform == "win32":
        try:
            import subprocess
            safe_target_str = str(target_path).replace("\\", "/").replace("'", "''")
            ps_cmd = f"""
            Add-Type -AssemblyName System.Windows.Forms;
            $img = [System.Windows.Forms.Clipboard]::GetImage();
            if ($img -ne $null) {{
                $img.Save('{safe_target_str}', [System.Drawing.Imaging.ImageFormat]::Png);
                Write-Output 'OK';
            }}
            """
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=4,
                creationflags=creationflags,
            )
            if "OK" in (res.stdout or "") and target_path.exists():
                return target_path
        except Exception as e:
            _LOG.debug("PowerShell grab_clipboard_image error: %s", e)

    return None


def get_clipboard_text() -> str:
    """读取系统剪贴板中的纯文本 (跨平台)"""
    if sys.platform == "win32":
        # 1. 原生 Win32 API 极速路径 (<0.1ms, 免子进程, 纯 UTF-16)
        text = _read_win32_clipboard_text()
        if text is not None:
            return text.strip()

        # 2. PowerShell 容错降级路径 (显式 UTF-8 编码与无窗口标志)
        try:
            import subprocess
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            ps_cmd = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; Get-Clipboard"
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=2,
                creationflags=creationflags,
            )
            if res.returncode == 0 and res.stdout:
                return res.stdout.strip()
        except Exception as e:
            _LOG.debug("PowerShell get_clipboard_text error: %s", e)
    elif sys.platform == "darwin":
        try:
            import subprocess
            res = subprocess.run(
                ["pbpaste"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=2,
            )
            if res.returncode == 0 and res.stdout:
                return res.stdout.strip()
        except Exception as e:
            _LOG.debug("Darwin pbpaste error: %s", e)
    elif sys.platform.startswith("linux"):
        try:
            import subprocess
            res = subprocess.run(
                ["xclip", "-selection", "clipboard", "-o"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=2,
            )
            if res.returncode == 0 and res.stdout:
                return res.stdout.strip()
        except Exception as e:
            _LOG.debug("Linux xclip error: %s", e)
    return ""
