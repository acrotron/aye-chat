import subprocess
import re
from pathlib import Path
from typing import Union
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

def _python_diff_content(content1: str, content2: str, label1: str, label2: str) -> None:
    """Show diff between two content strings using Python's difflib."""
    try:
        from difflib import unified_diff
        
        lines1 = content1.splitlines(keepends=True)
        lines2 = content2.splitlines(keepends=True)
        
        # Generate unified diff
        diff = unified_diff(
            lines2,  # from (snapshot)
            lines1,  # to (current)
            fromfile=label2,
            tofile=label1
        )
        
        # Convert diff to string and print
        diff_str = ''.join(diff)
        if diff_str.strip():
            _diff_console.print(diff_str)
        else:
            rprint("[green]No differences found.[/]")
    except Exception as e:
        rprint(f"[red]Error running Python diff:[/] {e}")

def show_diff(file1: Union[Path, str], file2: Union[Path, str], is_stash_ref: bool = False) -> None:
    """Show diff between two files or between a file and a stash reference.
    
    Args:
        file1: Current file path or content string
        file2: Snapshot file path, stash reference, or content string
        is_stash_ref: If True, file2 is a stash reference like 'stash@{0}:path/to/file'
    """
    # Handle stash references
    if is_stash_ref:
        # file2 is in format 'stash@{N}:path/to/file'
        # file1 is the current file path
        try:
            from aye.model.snapshot import get_backend
            from aye.model.snapshot.git_backend import GitStashBackend
            
            backend = get_backend()
            if not isinstance(backend, GitStashBackend):
                rprint("[red]Error: Stash references only work with git backend[/]")
                return
            
            # Parse stash reference
            stash_ref, file_path = str(file2).split(':', 1)
            
            # Get content from stash
            stash_content = backend.get_file_content_from_snapshot(file_path, stash_ref)
            if stash_content is None:
                rprint(f"[red]Error: Could not extract file from {stash_ref}[/]")
                return
            
            # Get current file content
            current_file = Path(file1)
            if not current_file.exists():
                rprint(f"[red]Error: Current file {file1} does not exist[/]")
                return
            
            current_content = current_file.read_text(encoding="utf-8")
            
            # Diff the contents
            _python_diff_content(
                current_content,
                stash_content,
                str(file1),
                f"{stash_ref}:{file_path}"
            )
            return
            
        except Exception as e:
            rprint(f"[red]Error processing stash diff:[/] {e}")
            return
    
    # Handle regular file paths
    file1_path = Path(file1) if not isinstance(file1, Path) else file1
    file2_path = Path(file2) if not isinstance(file2, Path) else file2
    
    try:
        result = subprocess.run(
            ["diff", "--color=always", "-u", str(file2_path), str(file1_path)],
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
        _python_diff_files(file1_path, file2_path)
    except Exception as e:
        rprint(f"[red]Error running diff:[/] {e}")
