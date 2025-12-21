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
            tofile=str(file1),
        )

        # Convert diff to string and print
        diff_str = "".join(diff)
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
            tofile=label1,
        )

        # Convert diff to string and print
        diff_str = "".join(diff)
        if diff_str.strip():
            _diff_console.print(diff_str)
        else:
            rprint("[green]No differences found.[/]")
    except Exception as e:
        rprint(f"[red]Error running Python diff:[/] {e}")


def show_diff(file1: Union[Path, str], file2: Union[Path, str], is_stash_ref: bool = False) -> None:
    """Show diff between two files or between a file and a git snapshot reference.

    Args:
        file1: Current file path
        file2:
          - For file backend: snapshot file path
          - For GitRefBackend: "<refname>:<repo_rel_path>" or "<ref1>:<path>|<ref2>:<path>"
        is_stash_ref: historical name; when True, treat file2 as a git snapshot reference.
    """
    # Handle git snapshot references (GitRefBackend)
    if is_stash_ref:
        try:
            from aye.model.snapshot import get_backend
            from aye.model.snapshot.git_ref_backend import GitRefBackend

            backend = get_backend()
            if not isinstance(backend, GitRefBackend):
                rprint("[red]Error: Git snapshot references only work with GitRefBackend[/]")
                return

            def _extract(ref_with_path: str) -> tuple[str, str]:
                refname, repo_rel_path = ref_with_path.split(":", 1)
                return refname, repo_rel_path

            # Two-snapshot diff: "ref1:path|ref2:path"
            file2_str = str(file2)
            if "|" in file2_str:
                left, right = file2_str.split("|", 1)
                ref_l, path_l = _extract(left)
                ref_r, path_r = _extract(right)

                content_l = backend.get_file_content_from_snapshot(path_l, ref_l)
                content_r = backend.get_file_content_from_snapshot(path_r, ref_r)

                if content_l is None:
                    rprint(f"[red]Error: Could not extract file from {ref_l}[/]")
                    return
                if content_r is None:
                    rprint(f"[red]Error: Could not extract file from {ref_r}[/]")
                    return

                _python_diff_content(
                    content_l,
                    content_r,
                    f"{ref_l}:{path_l}",
                    f"{ref_r}:{path_r}",
                )
                return

            # Current-vs-snapshot diff
            refname, repo_rel_path = _extract(file2_str)

            snap_content = backend.get_file_content_from_snapshot(repo_rel_path, refname)
            if snap_content is None:
                rprint(f"[red]Error: Could not extract file from {refname}[/]")
                return

            current_file = Path(file1)
            if not current_file.exists():
                rprint(f"[red]Error: Current file {file1} does not exist[/]")
                return

            current_content = current_file.read_text(encoding="utf-8")

            _python_diff_content(
                current_content,
                snap_content,
                str(file1),
                f"{refname}:{repo_rel_path}",
            )
            return

        except Exception as e:
            rprint(f"[red]Error processing git snapshot diff:[/] {e}")
            return

    # Handle regular file paths
    file1_path = Path(file1) if not isinstance(file1, Path) else file1
    file2_path = Path(file2) if not isinstance(file2, Path) else file2

    try:
        result = subprocess.run(
            ["diff", "--color=always", "-u", str(file2_path), str(file1_path)],
            capture_output=True,
            text=True,
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
