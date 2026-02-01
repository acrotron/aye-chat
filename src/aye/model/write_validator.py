"""Validates file writes against ignore patterns.

This module provides functionality to check if files being written
match .gitignore or .ayeignore patterns, and optionally block such writes.

See: https://github.com/acrotron/aye-chat/issues/50
"""

from pathlib import Path
from typing import List, Dict, Tuple

from aye.model.ignore_patterns import load_ignore_patterns
from aye.model.auth import get_user_config


# Config key for strict mode (block writes to ignored files)
BLOCK_IGNORED_WRITES_KEY = "block_ignored_file_writes"


def is_strict_mode_enabled() -> bool:
    """Check if strict mode is enabled (block writes to ignored files).

    Can be set via:
    - Environment variable: AYE_BLOCK_IGNORED_FILE_WRITES=on
    - Config file (~/.ayecfg): block_ignored_file_writes=on

    Returns:
        True if strict mode is enabled, False otherwise (default)
    """
    value = get_user_config(BLOCK_IGNORED_WRITES_KEY, "off")
    return str(value).lower() in ("on", "true", "1", "yes")


def check_files_against_ignore_patterns(
    files: List[Dict[str, str]],
    root_path: Path
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """Check which files match ignore patterns.

    Args:
        files: List of file dicts with 'file_name' and 'file_content' keys
        root_path: Project root path for loading ignore patterns

    Returns:
        Tuple of (allowed_files, ignored_files)
        - allowed_files: Files that don't match any ignore pattern
        - ignored_files: Files that match ignore patterns
    """
    if not files:
        return [], []

    ignore_spec = load_ignore_patterns(root_path)

    allowed_files = []
    ignored_files = []

    for file_dict in files:
        file_name = file_dict.get("file_name", "")
        if not file_name:
            continue

        # Normalize path for matching
        # The pathspec library expects forward slashes
        rel_path = file_name.replace("\\", "/")

        if ignore_spec.match_file(rel_path):
            ignored_files.append(file_dict)
        else:
            allowed_files.append(file_dict)

    return allowed_files, ignored_files


def format_ignored_files_warning(
    ignored_files: List[Dict[str, str]],
    strict_mode: bool
) -> str:
    """Format a warning message about ignored files.

    Args:
        ignored_files: List of file dicts that match ignore patterns
        strict_mode: Whether strict mode is enabled

    Returns:
        Formatted warning message
    """
    file_names = [f.get("file_name", "unknown") for f in ignored_files]
    file_list = ", ".join(file_names)

    if strict_mode:
        msg = (
            f"[yellow]Blocked write to ignored file(s): {file_list}[/]\n"
            "[dim]These files match patterns in .gitignore or .ayeignore and were "
            "not written.[/]"
        )
    else:
        msg = (
            f"[yellow]Warning: Writing to ignored file(s): {file_list}[/]\n"
            "[dim]These files match patterns in .gitignore or .ayeignore. "
            "Since they weren't read into context, their original content will be overwritten.[/]\n"
            "[dim]To block writes to ignored files, set [bold]block_ignored_file_writes=on[/bold] "
            "in ~/.ayecfg or use [bold]AYE_BLOCK_IGNORED_FILE_WRITES=on[/bold] environment variable.[/]"
        )

    return msg
