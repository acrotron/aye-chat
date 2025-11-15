import subprocess
import re
from pathlib import Path
from rich import print as rprint
from rich.console import Console

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mK]")

# Create a global console instance for diff output
_diff_console = Console(force_terminal=True, markup=False, color_system="standard")

def _python_diff_files(file1: Path, file2: Path) -> None:
    """Show diff between two files using Python's difflib."""
    try:
        from difflib import unified_diff
        
        # Read file contents
        content1 = file1.read_text(encoding="utf-8").splitlines(keepends=True) if file1.exists() else []
        content2 = file2.read_text(encoding="utf-8").splitlines(keepends=True) if file2.exists() else []
        
        # Generate unified diff
        diff = unified_diff(
            content2,  # from file (snapshot)
            content1,  # to file (current)
            fromfile=str(file2),
            tofile=str(file1)
        )
        
        # Convert diff to string and print
        diff_str = ''.join(diff)
        if diff_str.strip():
            _diff_console.print(diff_str)
        else:
            rprint("[green]No differences found.[/]")
    except Exception as e:
        rprint(f"[red]Error running Python diff:[/] {e}")

def show_diff(file1: Path, file2: Path) -> None:
    """Show diff between two files using system diff command or Python fallback."""
    try:
        result = subprocess.run(
            ["diff", "--color=always", "-u", str(file2), str(file1)],
            capture_output=True,
            text=True
        )
        if result.stdout.strip():
            clean_output = ANSI_RE.sub("", result.stdout)
            _diff_console.print(clean_output)
        else:
            rprint("[green]No differences found.[/]")
    except FileNotFoundError:
        # Fallback to Python's difflib if system diff is not available
        _python_diff_files(file1, file2)
    except Exception as e:
        rprint(f"[red]Error running diff:[/] {e}")
