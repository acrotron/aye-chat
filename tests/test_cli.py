import subprocess
import sys

def test_help_output():
    """Running `python -m aye --help` should exit 0 and contain “Usage”."""
    result = subprocess.run(
        [sys.executable, "-m", "aye", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "Usage:" in result.stdout

