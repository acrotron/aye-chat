import subprocess
import re
import difflib
from pathlib import Path
from typing import Union, Iterator, Any

from rich.console import Console
from rich.syntax import Syntax
from rich.theme import Theme
from rich.style import Style
from rich.table import Table
from rich.text import Text
from pygments.lexers import get_lexer_for_filename
from pygments.util import ClassNotFound

# Exposed at module level so unit tests can patch:
#   @patch('aye.presenter.diff_presenter.get_backend')
#
# Keep these imports best-effort to avoid import-time failures in environments
# where snapshot backends are unavailable.
try:
    from aye.model.snapshot import get_backend  # type: ignore
    from aye.model.snapshot.git_ref_backend import GitRefBackend  # type: ignore
except Exception:  # pragma: no cover
    get_backend = None  # type: ignore
    GitRefBackend = None  # type: ignore


# Rich theme used for the diff presenter.
#
# We use these named styles when printing structural lines from a unified diff:
# - diff.header: file headers (--- / +++)
# - diff.chunk : hunk headers (@@ ... @@)
# - diff.text  : generic/fallback text
# - diff.warning / diff.error: status messages
#
# Note: added/removed *content line* backgrounds are handled via STYLE_ADDED /
# STYLE_REMOVED below so the entire line gets a consistent background.
diff_theme = Theme({
    "diff.header": "bold cornflower_blue",
    "diff.chunk": "dim slate_blue3",
    "diff.text": "steel_blue",
    "diff.warning": "bold khaki1",
    "diff.error": "bold indian_red1",
    "diff.added": "bold sea_green2",
    "diff.removed": "bold indian_red1",
})

# Create a global console instance for diff output.
#
# force_terminal=True forces Rich to render with terminal capabilities even when
# stdout isn't detected as a TTY (e.g. captured output / some CI environments).
_diff_console = Console(force_terminal=True, theme=diff_theme)

# Background colors for added/removed lines.
#
# These are applied as full-line background colors so additions/removals are
# visible even when syntax highlighting colors vary.
GREEN = "#2e5842"
RED = "#5c3030"
STYLE_ADDED = Style(bgcolor=GREEN)
STYLE_REMOVED = Style(bgcolor=RED)
STYLE_ADD_SIGN = Style(color="#a8cc8c", bold=True)
STYLE_DEL_SIGN = Style(color="#f08080", bold=True)


def _is_git_ref_backend(backend: Any) -> bool:
    """Return True if backend is (or mocks) GitRefBackend.

    Tests often use MagicMock(spec=GitRefBackend). Those mocks are not instances
    of GitRefBackend, but they store the spec in `_spec_class`.
    """
    if GitRefBackend is None:
        return False

    if isinstance(backend, GitRefBackend):
        return True

    spec_cls = getattr(backend, "_spec_class", None)
    try:
        return spec_cls is not None and issubclass(spec_cls, GitRefBackend)
    except TypeError:
        return False


def _print_line(prefix: str, code: str, style: Style, lexer_name: str) -> None:
    """Print a single unified-diff *content* line using a prefix + code layout.

    Unified diff content lines look like:
    - "+<code>" for additions
    - "-<code>" for removals
    - " <code>" for context/unchanged

    Implementation details:
    - We use a 2-column `Table.grid` so the diff prefix stays aligned and visible
      even when the code wraps.
    - Syntax highlighting is provided by Rich `Syntax` using the given lexer.

    Important:
    - We fix the prefix column width and disable table edge padding so every line
      starts at the same terminal column. Without this, Rich can choose slightly
      different layouts per-line (each line is a separate grid), which looks like
      chaotic indentation.
    """

    # Deterministic grid layout for every line.
    grid = Table.grid(expand=True, padding=(0, 0))
    # Ensure no implicit left/right edge padding.
    try:
        grid.pad_edge = False
    except Exception:
        # Older/newer Rich versions may not expose pad_edge; safe to ignore.
        pass

    # Prefix column: fixed width so +/ -/ context always align.
    grid.add_column(width=2, no_wrap=True)
    # Code column: takes remaining space.
    grid.add_column(ratio=1)

    # Rich's `Syntax(background_color=...)` expects a string name/hex.
    bgcolor = style.bgcolor.name if style.bgcolor else "default"

    syntax = Syntax(
        code,
        lexer_name,
        theme="monokai",
        background_color=bgcolor,
        word_wrap=True,
        code_width=None,
        tab_size=4,
    )

    if prefix == "+ ":
        prefix_text = Text("+ ", style=STYLE_ADD_SIGN)
    elif prefix == "- ":
        prefix_text = Text("- ", style=STYLE_DEL_SIGN)
    else:
        prefix_text = Text(prefix)

    grid.add_row(prefix_text, syntax)
    _diff_console.print(grid, style=style)


def _print_diff_with_syntax(
    diff_lines: Iterator[str],
    file_path: str = ""
) -> None:
    """Print unified diff lines with styling and (optional) syntax highlighting."""

    # If difflib yields no lines, there are no differences.
    has_diff = False

    # Best-effort lexer inference based on filename.
    try:
        lexer = get_lexer_for_filename(file_path)
        lexer_name = lexer.aliases[0]
    except (ClassNotFound, Exception):
        lexer_name = "text"

    for line in diff_lines:
        has_diff = True

        # difflib includes trailing newlines in the unified diff output.
        # Strip both \n and \r to avoid CR messing with terminal rendering.
        line_content = line.rstrip("\r\n")

        # File header lines. Must be checked before +/- since they also begin
        # with '-'/'+' characters.
        if line.startswith("---") or line.startswith("+++"):
            _diff_console.print(line_content, style="diff.header")

        # Added lines (content additions).
        elif line.startswith("+"):
            code = line_content[1:]
            _print_line("+ ", code, STYLE_ADDED, lexer_name)

        # Removed lines (content deletions).
        elif line.startswith("-"):
            code = line_content[1:]
            _print_line("- ", code, STYLE_REMOVED, lexer_name)

        # Hunk header lines (range metadata for a block of changes).
        elif line.startswith("@@"):
            _diff_console.print(line_content, style="diff.chunk")

        # Context lines: unchanged lines. Unified diff prefixes these with a
        # leading space.
        elif line.startswith(" "):
            code = line_content[1:]
            _print_line("  ", code, Style(), lexer_name)

        # Fallback for any unexpected lines.
        else:
            _print_line("  ", line_content, Style(), lexer_name)

    if not has_diff:
        _diff_console.print("No differences found.", style="diff.warning")


def _python_diff_files(file1: Path, file2: Path) -> None:
    """Show diff between two files using Python's difflib."""
    try:
        # Read file contents.
        content1 = file1.read_text(encoding="utf-8").splitlines(keepends=True) if file1.exists() else []
        content2 = file2.read_text(encoding="utf-8").splitlines(keepends=True) if file2.exists() else []

        # Generate unified diff.
        diff = difflib.unified_diff(
            content2,  # from file (snapshot)
            content1,  # to file (current)
            fromfile=str(file2),
            tofile=str(file1),
        )

        # Pass the current file path as the lexer hint.
        _print_diff_with_syntax(diff, str(file1))
    except Exception as e:
        # Presenter behavior: print errors rather than raising.
        _diff_console.print(f"Error running Python diff: {e}", style="diff.error")


def _python_diff_content(content1: str, content2: str, label1: str, label2: str) -> None:
    """Show diff between two in-memory strings using Python's difflib."""
    try:
        lines1 = content1.splitlines(keepends=True)
        lines2 = content2.splitlines(keepends=True)

        # Generate unified diff.
        diff = difflib.unified_diff(
            lines2,  # from (snapshot)
            lines1,  # to (current)
            fromfile=label2,
            tofile=label1,
        )

        _print_diff_with_syntax(diff, label1)
    except Exception as e:
        _diff_console.print(f"Error running Python diff: {e}", style="diff.error")


def show_diff(file1: Union[Path, str], file2: Union[Path, str], is_stash_ref: bool = False) -> None:
    """Show diff between two files or between a file and a stash reference."""

    # Handle stash references
    if is_stash_ref:
        try:
            # Ensure module-level symbols exist even if earlier best-effort import failed.
            global get_backend, GitRefBackend
            if get_backend is None or GitRefBackend is None:  # pragma: no cover
                from aye.model.snapshot import get_backend as _get_backend
                from aye.model.snapshot.git_ref_backend import GitRefBackend as _GitRefBackend
                get_backend = _get_backend  # type: ignore
                GitRefBackend = _GitRefBackend  # type: ignore

            backend = get_backend()  # type: ignore[misc]
            if not _is_git_ref_backend(backend):
                _diff_console.print(
                    "Error: Git snapshot references only work with GitRefBackend",
                    style="diff.error",
                )
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
                    _diff_console.print(f"Error: Could not extract file from {ref_l}", style="diff.error")
                    return
                if content_r is None:
                    _diff_console.print(f"Error: Could not extract file from {ref_r}", style="diff.error")
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
                _diff_console.print(f"Error: Could not extract file from {refname}", style="diff.error")
                return

            # Read current file content from disk.
            current_file = Path(file1)
            if not current_file.exists():
                _diff_console.print(f"Error: Current file {file1} does not exist", style="diff.error")
                return

            current_content = current_file.read_text(encoding="utf-8")

            # Diff the contents.
            _python_diff_content(
                current_content,
                snap_content,
                str(file1),
                f"{refname}:{repo_rel_path}",
            )
            return

        except Exception as e:
            _diff_console.print(f"Error processing stash diff: {e}", style="diff.error")
            return

    # Handle regular file paths.
    # Normalize to Path objects so downstream helpers can rely on Path APIs.
    file1_path = Path(file1) if not isinstance(file1, Path) else file1
    file2_path = Path(file2) if not isinstance(file2, Path) else file2
    _python_diff_files(file1_path, file2_path)

#    try:
#        result = subprocess.run(
#            ["diff", "--color=always", "-u", str(file2_path), str(file1_path)],
#            capture_output=True,
#            text=True,
#        )
#        if result.stdout.strip():
#            clean_output = ANSI_RE.sub("", result.stdout)
#            _diff_console.print(clean_output)
#        else:
#            _diff_console.print("No differences found.")
#    except FileNotFoundError:
#        # Fallback to Python's difflib if system diff is not available
#        _python_diff_files(file1_path, file2_path)
#    except Exception as e:
#        _diff_console.print(f"Error running diff: {e}", style="diff.error")
