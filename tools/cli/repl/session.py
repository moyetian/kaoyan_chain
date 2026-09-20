# -*- coding: utf-8 -*-
"""
REPL 会话状态与系统剪贴板管理 (session.py)
跨平台截图获取、剪贴板纯文本读取、会话状态上下文
"""

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    except Exception:
        pass

    # 2. Windows PowerShell 底层读取剪贴板位图
    if sys.platform == "win32":
        try:
            import subprocess
            safe_target_str = str(target_path).replace("\\", "/")
            ps_cmd = f"""
            Add-Type -AssemblyName System.Windows.Forms;
            $img = [System.Windows.Forms.Clipboard]::GetImage();
            if ($img -ne $null) {{
                $img.Save('{safe_target_str}', [System.Drawing.Imaging.ImageFormat]::Png);
                Write-Output 'OK';
            }}
            """
            res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True, timeout=4)
            if "OK" in (res.stdout or "") and target_path.exists():
                return target_path
        except Exception:
            pass

    return None

def get_clipboard_text() -> str:
    """读取系统剪贴板中的纯文本 (跨平台)"""
    if sys.platform == "win32":
        try:
            import subprocess
            res = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"], capture_output=True, text=True, timeout=2)
            if res.returncode == 0 and res.stdout:
                return res.stdout.strip()
        except Exception:
            pass
    elif sys.platform == "darwin":
        try:
            import subprocess
            res = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=2)
            if res.returncode == 0 and res.stdout:
                return res.stdout.strip()
        except Exception:
            pass
    elif sys.platform.startswith("linux"):
        try:
            import subprocess
            res = subprocess.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True, text=True, timeout=2)
            if res.returncode == 0 and res.stdout:
                return res.stdout.strip()
        except Exception:
            pass
    return ""
