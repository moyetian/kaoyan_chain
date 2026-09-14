import subprocess
import sys
import pytest
from pathlib import Path
from unittest.mock import patch

# -- L1 单元层 --
def test_protocol_loader():
    from tools.protocol_loader import load_protocol
    content = load_protocol("AGENTS")
    assert "总控系统协议" in content, "AGENTS.md loading failed"

def test_note_lock():
    from tools.note_lock import note_is_locked
    locked_md = "---\nlocked: true\n---\n# Content"
    unlocked_md = "---\nlocked: false\n---\n# Content"
    no_frontmatter = "# Content"
    
    assert note_is_locked(locked_md) is True
    assert note_is_locked(unlocked_md) is False
    assert note_is_locked(no_frontmatter) is False

# -- L2 CLI 层 --
# Since tools.ky_cli is not based on typer or click in a standard way,
# we will mock sys.argv and call main() if possible, or test its subcomponents.
# If ky_cli parses args manually, we test the argument parsing.

# -- L3 进程层 --
def test_cli_entrypoint_installed():
    # Runs the entrypoint as a subprocess to verify the CLI executes
    r = subprocess.run([sys.executable, "-m", "tools.ky_cli", "--help"],
                       capture_output=True, encoding="utf-8", timeout=60)
    
    # We expect return code 0 and some help text
    assert r.returncode == 0, f"CLI execution failed: {r.stderr}"
    assert "ky-cli" in (r.stdout or "").lower() or "agent" in (r.stdout or "").lower()
