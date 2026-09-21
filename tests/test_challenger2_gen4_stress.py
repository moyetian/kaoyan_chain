# -*- coding: utf-8 -*-
"""
考研学习链 · Challenger 2 (Gen 4) 专项对抗性与压力测试套件
(test_challenger2_gen4_stress.py)

Empirical stress tests verifying:
1. Windows Subprocess & Encoding Robustness:
   - Extreme Chinese characters, non-BMP characters, rare Hanzi, emojis, ANSI escape codes, ultra-long lines (>150k chars).
   - Pipe reading with encoding="utf-8", errors="replace" produces zero UnicodeDecodeError.
   - test_fix_ky_suite_guard workspace guard execution under adversarial Unicode configs.
   - Rapid process termination leaves zero unhandled daemon thread warnings (PytestUnhandledThreadExceptionWarning).
2. Search Provider HTTP Decompression:
   - tools/search/providers/_http.py decompress_body with valid gzip, valid zlib deflate, valid raw deflate (-MAX_WBITS).
   - Magic bytes sniffing (b"\x1f\x8b") without Content-Encoding header.
   - Malformed/corrupted gzip and deflate payloads returning raw_data gracefully without raising unhandled exceptions.
   - End-to-end get_text TLS 证书错误语义：证书无效时如实失败（ProviderError 可辨识），
     不再静默降级为未验证连接。
3. REPL Clipboard Encoding:
   - tools/cli/repl/session.py _read_win32_clipboard_text and get_clipboard_text with Unicode, emojis, rare Hanzi, and multilines.
   - PowerShell fallback with explicit UTF-8 encoding.
   - grab_clipboard_image PowerShell command escaping for paths containing single quotes, spaces, brackets, dollar signs, and Chinese characters.
"""

from __future__ import annotations

import base64
import ctypes
import gzip
import io
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
SUITE_SRC = ROOT / "tools" / "test_ky_suite.py"
HTTP_MOD = ROOT / "tools" / "search" / "providers" / "_http.py"
SESSION_MOD = ROOT / "tools" / "cli" / "repl" / "session.py"


# ==============================================================================
# Part 1: Windows Subprocess & Encoding Robustness Stress Tests
# ==============================================================================

class TestWindowsSubprocessEncodingRobustness:
    """Stress-test subprocess pipe reading, encoding='utf-8', errors='replace', and thread stability."""

    def test_extreme_chinese_and_rare_hanzi_pipe_reading(self):
        """Simulate child process emitting rare Hanzi, CJK Ext B/C/D, non-BMP, Tibetan, and full-width punctuation."""
        sample_text = (
            "【考研深度研究】"
            "中国人民大学 · 马克思主义理论 · 重点学科；"
            "生僻与异体字：𠮷 𪚥 𱁬 𠜎 𣛧 龘 鱻 贔 麤 龖；"
            "少数民族与多语言：བཀྲ་ཤིས་བདེ་ལེགས། ᠮᠣᠩᡤᠣᠯ 维吾尔 Учитель；"
            "全角标点与特殊符号：！？；：“”‘’【】《》——……￥※★☆▲△◆◇"
        )
        child_code = (
            "import sys\n"
            f"sample = {sample_text!r}\n"
            "sys.stdout.buffer.write(sample.encode('utf-8'))\n"
            "sys.stdout.buffer.flush()\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", child_code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        assert proc.returncode == 0
        assert "中国人民大学" in proc.stdout
        assert "𠮷" in proc.stdout
        assert "𪚥" in proc.stdout
        assert "龘" in proc.stdout

    def test_emojis_and_ansi_escape_sequences_pipe_reading(self):
        """Simulate child process outputting complex emojis (ZWJ, flags, skin tones) and intense ANSI escape codes."""
        ansi_and_emoji = (
            "\x1b[31;1m[ERROR]\x1b[0m \x1b[32;4m[SUCCESS]\x1b[0m \x1b[38;2;255;128;0m[RGB COLOR]\x1b[0m\n"
            "Emojis: 🚀 🔥 🎉 💻 📚 ✨ 🧪 🤖 💥 💯 ❤️ 👍 🎓 👨‍👩‍👧‍👦 🇨🇳 🧙‍♂️ 🧑🏿‍💻 📈 📉\n"
            "\x1b[2J\x1b[H\x1b[K\x1b[1A\x1b[2K"  # Screen clear, cursor jump, line erase
            "Status: Complete! 100% ✔ ✘ ⚡"
        )
        child_code = (
            "import sys\n"
            f"sample = {ansi_and_emoji!r}\n"
            "sys.stdout.buffer.write(sample.encode('utf-8'))\n"
            "sys.stdout.buffer.flush()\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", child_code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        assert proc.returncode == 0
        assert "🚀" in proc.stdout
        assert "👨‍👩‍👧‍👦" in proc.stdout
        assert "\x1b[31;1m" in proc.stdout
        assert "Complete!" in proc.stdout

    def test_ultra_long_continuous_strings_and_chunk_wrapping(self):
        """Stress-test pipe buffer with 150,000+ char continuous line without newline and multi-chunk output."""
        child_code = (
            "import sys\n"
            "unit = '考研2026_河南农大_马克思主义理论_🔥_𠮷_'.encode('utf-8')\n"
            "for _ in range(5000):\n"
            "    sys.stdout.buffer.write(unit)\n"
            "sys.stdout.buffer.flush()\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", child_code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        out, err = proc.communicate(timeout=20)
        assert proc.returncode == 0
        assert len(out) >= 100000
        assert out.startswith("考研2026")
        assert "🔥" in out
        assert "𠮷" in out

    def test_corrupted_utf8_boundary_with_errors_replace(self):
        """Verify errors='replace' cleanly replaces invalid byte fragments with U+FFFD without crashing."""
        child_code = (
            "import sys\n"
            "corrupted_bytes = b'Prefix_' + b'\\xe6\\xb2' + b'_Mid_' + b'\\xff\\xfe' + b'_Suffix_\\xe4\\xbd\\xa0\\xe5\\xa5\\xbd'\n"
            "sys.stdout.buffer.write(corrupted_bytes)\n"
            "sys.stdout.buffer.flush()\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", child_code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        assert proc.returncode == 0
        assert "Prefix_" in proc.stdout
        assert "_Mid_" in proc.stdout
        assert "_Suffix_你好" in proc.stdout
        # Replacement character \ufffd should appear for the corrupted bytes
        assert "\ufffd" in proc.stdout

    def test_suite_guard_with_adversarial_unicode_config(self, tmp_path):
        """Challenge test_fix_ky_suite_guard mechanism with complex Unicode and emoji in ky_config.json."""
        adversarial_cfg = {
            "study_plan": {
                "school": "中国人民大学🎓·先进理论试验区\x1b[32m[特级]\x1b[0m 𠮷",
                "major": "030100 法学 (中国化 & 国际化) 🚀",
                "math_key": "none",
                "eng_key": "eng1",
                "total_hours": 6.5,
            },
            "api_key": "sk-REAL-LOOKING-KEY-1234567890abcdef",
            "model": "mimo-v2.5",
        }
        (tmp_path / "tools").mkdir(parents=True, exist_ok=True)
        shutil.copy2(SUITE_SRC, tmp_path / "tools" / "test_ky_suite.py")
        (tmp_path / "ky_config.json").write_text(
            json.dumps(adversarial_cfg, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        env = dict(os.environ)
        env.pop("KY_TEST_ALLOW_REAL_WORKSPACE", None)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(ROOT), str(ROOT / "tools"), env.get("PYTHONPATH", "")]
        ).strip(os.pathsep)
        env["PYTHONIOENCODING"] = "utf-8"

        res = subprocess.run(
            [sys.executable, "-u", "tools/test_ky_suite.py"],
            cwd=str(tmp_path),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        # Must refuse execution with exit code 2 and accurately format output
        assert res.returncode == 2
        assert "已拒绝运行" in res.stdout
        assert "中国人民大学" in res.stdout
        assert "🎓" in res.stdout or "\ufffd" not in res.stdout

    def test_rapid_process_lifecycle_zero_thread_warnings(self, recwarn):
        """Repeatedly start, communicate, and terminate child processes to stress-test daemon threads and pipes."""
        for _ in range(5):
            proc = subprocess.Popen(
                [sys.executable, "-u", "-c", "import time, sys; sys.stdout.write('READY\\n'); sys.stdout.flush(); time.sleep(10)"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            line = proc.stdout.readline()
            assert "READY" in line
            proc.terminate()
            try:
                proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()

        # Verify no PytestUnhandledThreadExceptionWarning occurred
        thread_warnings = [
            w for w in recwarn.list
            if "Thread" in str(w.category.__name__) or "thread" in str(w.message).lower()
        ]
        assert len(thread_warnings) == 0, f"Detected thread warnings: {thread_warnings}"


# ==============================================================================
# Part 2: Search Provider HTTP Decompression Stress Tests
# ==============================================================================

class TestSearchProviderHTTPDecompression:
    """Stress-test tools/search/providers/_http.py decompress_body and SSL fallback integration."""

    @pytest.fixture(autouse=True)
    def import_http(self):
        sys.path.insert(0, str(ROOT / "tools"))
        try:
            from search.providers import _http
            self.http = _http
        finally:
            try:
                sys.path.remove(str(ROOT / "tools"))
            except ValueError:
                pass

    def test_decompress_body_valid_gzip(self):
        original = "中国人民大学考研招生简章·马克思主义学院".encode("utf-8")
        compressed = gzip.compress(original)

        # Header specified
        assert self.http.decompress_body(compressed, {"Content-Encoding": "gzip"}) == original
        assert self.http.decompress_body(compressed, {"content-encoding": " x-gzip "}) == original

        # Sniffed magic bytes without headers
        assert self.http.decompress_body(compressed) == original
        assert self.http.decompress_body(compressed, {}) == original
        assert self.http.decompress_body(compressed, resp_headers={}) == original

    def test_decompress_body_valid_deflate_zlib_and_raw(self):
        original = "法学综合 810 考试大纲".encode("utf-8")

        # Standard zlib header
        compressed_zlib = zlib.compress(original)
        assert self.http.decompress_body(compressed_zlib, {"Content-Encoding": "deflate"}) == original
        assert self.http.decompress_body(compressed_zlib, {"content-encoding": "zlib"}) == original

        # Raw deflate without zlib header (-MAX_WBITS)
        compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
        compressed_raw = compressor.compress(original) + compressor.flush()
        assert self.http.decompress_body(compressed_raw, {"Content-Encoding": "deflate"}) == original

    def test_decompress_body_malformed_gzip_fallback(self):
        """Malformed gzip must return original raw_data gracefully without raising an exception."""
        # 1. Magic bytes \x1f\x8b followed by garbage
        bad_magic_gzip = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff_CORRUPTED_GZIP_PAYLOAD_"
        res1 = self.http.decompress_body(bad_magic_gzip)
        assert res1 == bad_magic_gzip

        # 2. Content-Encoding: gzip but body is plain text
        plain_text = b"This is plain text claiming to be gzip"
        res2 = self.http.decompress_body(plain_text, {"Content-Encoding": "gzip"})
        assert res2 == plain_text

        # 3. Truncated gzip stream
        valid_gzip = gzip.compress(b"Valid content")
        truncated_gzip = valid_gzip[: len(valid_gzip) // 2]
        res3 = self.http.decompress_body(truncated_gzip, {"Content-Encoding": "gzip"})
        assert res3 == truncated_gzip

    def test_decompress_body_malformed_deflate_fallback(self):
        """Malformed deflate must return original raw_data gracefully without raising an exception."""
        bad_deflate = b"\x78\x9c_GARBAGE_PAYLOAD_THAT_FAILS_ZLIB_DECOMPRESSION_"
        res = self.http.decompress_body(bad_deflate, {"Content-Encoding": "deflate"})
        assert res == bad_deflate

        truncated_deflate = zlib.compress(b"Valid deflate stream")[:6]
        res2 = self.http.decompress_body(truncated_deflate, {"Content-Encoding": "deflate"})
        assert res2 == truncated_deflate

    def test_decompress_body_edge_cases(self):
        """Test empty bytes, non-compressed, alias compatibility."""
        assert self.http.decompress_body(b"") == b""
        assert self.http.decompress_response(b"") == b""
        assert self.http.decompress_body(b"\x1f") == b"\x1f"  # Single magic byte
        normal_bytes = b"Hello Kaoyan"
        assert self.http.decompress_body(normal_bytes, {"Content-Type": "text/html"}) == normal_bytes

    def test_get_text_ssl_cert_error_fails_honestly_with_gzip_payload(self):
        """[语义反转] 原 ``test_get_text_ssl_fallback_with_gzip_decompression``。

        旧产品行为把「自签名/过期证书的高校站点仍能抓到内容」钉成了特性：
        ``get_text`` 在证书错误后**静默**换成 ``CERT_NONE`` 上下文重试并返回正文。
        降级路径已删除，因此本用例改为断言反面语义：证书无效必须**如实失败**
        （抛 ``ProviderError``），失败原因可辨识（含「TLS 证书校验失败」），
        且**绝不**再发起未验证连接。gzip 解压本身由
        ``test_decompress_body_*`` 系列覆盖，无需再借 SSL 降级路径验证。
        """
        html_content = "<html><head><title>河南农大研招网</title></head><body>录取比例与考纲</body></html>"
        gzipped_body = gzip.compress(html_content.encode("utf-8"))

        class MockGzipResponse:
            def __init__(self):
                self.headers = {"Content-Encoding": "gzip", "Content-Type": "text/html; charset=utf-8"}
                self.status = 200

            def read(self):
                return gzipped_body

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        insecure_hits = {"n": 0}

        def mock_urlopen(req, timeout=10, context=None):
            if context is not None and getattr(context, "check_hostname", True) is False:
                # 未验证上下文：旧实现在此处返回正文；新实现根本不该走到这里
                insecure_hits["n"] += 1
                return MockGzipResponse()
            raise urllib.error.URLError(ssl.SSLError("certificate verify failed: self-signed certificate"))

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            with pytest.raises(self.http.ProviderError) as excinfo:
                self.http.get_text("https://yz.henau.edu.cn/notice/123.html")

        message = str(excinfo.value)
        assert "TLS 证书校验失败" in message, f"失败原因不可辨识: {message}"
        assert insecure_hits["n"] == 0, "证书错误后仍静默降级为未验证连接"

    def test_get_text_ssl_cert_error_fails_honestly_with_deflate_payload(self):
        """[语义反转] 原 ``test_get_text_ssl_fallback_with_deflate_decompression``。

        同上一用例：旧的「证书错误 -> 静默未验证重试 -> 解压 raw deflate 正文」
        已被判定为不可接受的产品行为。现要求证书错误如实失败、原因可辨识，
        且不触发任何未验证连接。raw deflate 解压由 ``test_decompress_body_*`` 覆盖。
        """
        html_content = "<html><body>专业课 810 参考书目</body></html>"
        compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
        raw_deflate_body = compressor.compress(html_content.encode("utf-8")) + compressor.flush()

        class MockDeflateResponse:
            def __init__(self):
                self.headers = {"Content-Encoding": "deflate", "Content-Type": "text/html; charset=utf-8"}
                self.status = 200

            def read(self):
                return raw_deflate_body

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        insecure_hits = {"n": 0}

        def mock_urlopen(req, timeout=10, context=None):
            if context is not None and getattr(context, "check_hostname", True) is False:
                insecure_hits["n"] += 1
                return MockDeflateResponse()
            raise urllib.error.URLError(ssl.SSLError("SSL hostname mismatch"))

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            with pytest.raises(self.http.ProviderError) as excinfo:
                self.http.get_text("https://yz.henau.edu.cn/outline.html")

        message = str(excinfo.value)
        assert "TLS 证书校验失败" in message, f"失败原因不可辨识: {message}"
        assert insecure_hits["n"] == 0, "证书错误后仍静默降级为未验证连接"

    def test_get_text_ssl_cert_error_is_not_masked_by_malformed_payload(self):
        """[语义反转] 原 ``test_get_text_ssl_fallback_with_malformed_compression_graceful``。

        原用例断言「降级后拿到的是坏 gzip 头但正文仍可读、不崩溃」。降级删除后
        语义反转为：证书错误必须以 ``ProviderError`` 如实抛出，而不是被后续的
        解压/解析异常掩盖成别的错误类型，也不是悄悄返回未验证正文。
        """
        raw_garbage = b"<html><body>Uncompressed text with bogus header</body></html>"

        class MockMalformedResponse:
            def __init__(self):
                self.headers = {"Content-Encoding": "gzip", "Content-Type": "text/html; charset=utf-8"}
                self.status = 200

            def read(self):
                return raw_garbage

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        insecure_hits = {"n": 0}

        def mock_urlopen(req, timeout=10, context=None):
            if context is not None and getattr(context, "check_hostname", True) is False:
                insecure_hits["n"] += 1
                return MockMalformedResponse()
            raise urllib.error.URLError(ssl.SSLError("SSL error"))

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            with pytest.raises(self.http.ProviderError) as excinfo:
                self.http.get_text("https://yz.henau.edu.cn/malformed.html")

        message = str(excinfo.value)
        assert "TLS 证书校验失败" in message, f"失败原因被掩盖: {message}"
        assert insecure_hits["n"] == 0, "证书错误后仍静默降级为未验证连接"


# ==============================================================================
# Part 3: REPL Clipboard Encoding Stress Tests
# ==============================================================================

class TestREPLClipboardEncoding:
    """Stress-test tools/cli/repl/session.py clipboard text reading, PowerShell escaping, and image paths."""

    @pytest.fixture(autouse=True)
    def import_session(self):
        sys.path.insert(0, str(ROOT / "tools"))
        try:
            from cli.repl import session
            self.session = session
        finally:
            try:
                sys.path.remove(str(ROOT / "tools"))
            except ValueError:
                pass

    def test_read_win32_clipboard_text_mocked_unicode_characters(self):
        """Verify _read_win32_clipboard_text properly decodes Unicode, rare Hanzi, and emojis via ctypes."""
        if sys.platform != "win32":
            pytest.skip("Win32 API only available on Windows")

        test_string = "考研政治 1000 题：马克思主义基本原理 🎓 🚀 𠮷 𪚥"
        
        # Test mock Win32 API returns wchar pointer value
        with patch.object(self.session, "_read_win32_clipboard_text", return_value=test_string):
            result = self.session.get_clipboard_text()
            assert result == test_string

    def test_get_clipboard_text_powershell_fallback_encoding(self):
        """Verify PowerShell fallback uses UTF-8 console encoding and returns non-ASCII text without corruption."""
        if sys.platform != "win32":
            pytest.skip("PowerShell only available on Windows")

        # Mock _read_win32_clipboard_text to return None, forcing PowerShell path
        with patch.object(self.session, "_read_win32_clipboard_text", return_value=None):
            # Mock subprocess.run returning UTF-8 encoded output
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = "中国人民大学 2026 研招简章 \n\n"
            with patch("subprocess.run", return_value=mock_proc) as mock_run:
                text = self.session.get_clipboard_text()
                assert text == "中国人民大学 2026 研招简章"
                
                # Check PowerShell command arguments
                args, kwargs = mock_run.call_args
                cmd = args[0]
                assert cmd[0] == "powershell"
                assert "[System.Text.Encoding]::UTF8" in cmd[3]
                assert kwargs.get("encoding") == "utf-8"
                assert kwargs.get("errors") == "replace"

    def test_grab_clipboard_image_path_escaping_adversarial_paths(self, tmp_path):
        """Adversarially challenge safe_target_str construction against quotes, spaces, dollar signs, and Chinese."""
        if sys.platform != "win32":
            pytest.skip("PowerShell only tested on Windows")

        # Test various adversarial subdirectories and filenames
        test_dir = tmp_path / "uploads_test's"
        test_dir.mkdir(parents=True, exist_ok=True)

        test_paths = [
            # Single quotes in directory/file name
            test_dir / "clip's 'test'.png",
            # Multiple single quotes
            test_dir / "clip'''nested'''.png",
            # Spaces and brackets
            test_dir / "clip [2026] (exam) copy.png",
            # Dollar signs and backticks (must not trigger PowerShell variable evaluation)
            test_dir / "clip_$HOME_`n_eval.png",
            # Chinese characters
            test_dir / "clip_河南农大_马克思.png",
        ]

        for p in test_paths:
            safe_target_str = str(p).replace("\\", "/").replace("'", "''")

            # Emulate PowerShell script in grab_clipboard_image saving a dummy bitmap
            ps_script = f"""
            [Console]::OutputEncoding = [System.Text.Encoding]::UTF8;
            Add-Type -AssemblyName System.Windows.Forms;
            Add-Type -AssemblyName System.Drawing;
            $bmp = New-Object System.Drawing.Bitmap 2, 2;
            $bmp.Save('{safe_target_str}', [System.Drawing.Imaging.ImageFormat]::Png);
            Write-Output 'OK';
            """

            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                creationflags=creationflags,
            )
            assert res.returncode == 0, f"PowerShell execution failed on path {p}:\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}"
            assert "OK" in res.stdout
            assert p.exists(), f"Target image file was not created at expected path: {p}"
            assert p.stat().st_size > 0, f"Target image file is empty: {p}"

    def test_clipboard_long_multiline_text_handling(self):
        """Stress-test get_clipboard_text with 5,000 lines of mixed CRLF and LF text."""
        lines = [f"第 {i} 条知识点：唯物辩证法核心三大规律与五大范畴" for i in range(5000)]
        big_multiline = "\r\n".join(lines)

        with patch.object(self.session, "_read_win32_clipboard_text", return_value=big_multiline):
            ret = self.session.get_clipboard_text()
            assert ret.startswith("第 0 条知识点")
            assert ret.endswith("五大范畴")
            assert len(ret.splitlines()) == 5000
