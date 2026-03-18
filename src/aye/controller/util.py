from pathlib import Path
import os
from typing import Union, Optional, Tuple

# The only marker we care about now is the index file inside the .aye directory
PROJECT_MARKER = ".aye/file_index.json"

AGENTS_FILENAME = "AGENTS.md"


def find_project_root(start_path: Optional[Union[str, Path]] = None) -> Path:
    """
    Find the project root by searching upwards for a '.aye/file_index.json' file.
    If no start_path is given, it uses the current working directory.
    If no marker is found, it defaults to the current working directory.

    Args:
        start_path: The path to start searching from (can be a file or directory).

    Returns:
        The path to the project root directory (the one containing .aye/file_index.json),
        or the current working directory if no project root is found.
    """
    # Capture the current working directory at the beginning for the fallback case.
    cwd = Path.cwd().resolve()

    if start_path:
        search_dir = Path(start_path).resolve()
    else:
        search_dir = cwd

    # If the path is a file or does not exist, start from its parent.
    if not search_dir.is_dir():
        search_dir = search_dir.parent
    
    # If the search directory is not valid, return the captured CWD immediately.
    if not search_dir.is_dir():
        return cwd

    # Walk up the directory tree.
    while True:
        # Check for the specific project marker.
        if (search_dir / PROJECT_MARKER).is_file():
            return search_dir

        # Move to the parent directory.
        parent_dir = search_dir.parent

        # If the parent is the same as the current directory, we've reached the filesystem root.
        if parent_dir == search_dir:
            # Marker not found, return the captured current working directory.
            return cwd
        
        search_dir = parent_dir


def discover_agents_file(
    cwd: Path, repo_root: Path, verbose: bool = False
) -> Optional[Tuple[Path, str]]:
    """
    Discover an AGENTS.md file for the current project context.

    Discovery order (first match wins, no merging):
      1. cwd/.aye/AGENTS.md   (highest precedence)
      2. Walk upward from cwd checking AGENTS.md at each directory,
         stopping at repo_root or filesystem root.

    Args:
        cwd:        Resolved current working directory.
        repo_root:  Resolved repository/project root (upper boundary for search).
        verbose:    If True, warnings for unreadable files are printed.

    Returns:
        A tuple (path, contents) if found and readable, otherwise None.
    """
    cwd = cwd.resolve()
    repo_root = repo_root.resolve()

    # --- 1) Highest precedence: cwd/.aye/AGENTS.md ---
    aye_agents = cwd / ".aye" / AGENTS_FILENAME
    result = _try_read_agents(aye_agents, verbose)
    if result is not None:
        return result

    # --- 2) Walk upward from cwd, checking AGENTS.md at each level ---
    search_dir = cwd
    while True:
        candidate = search_dir / ".aye" / AGENTS_FILENAME
        result = _try_read_agents(candidate, verbose)
        if result is not None:
            return result

        candidate = search_dir / AGENTS_FILENAME
        result = _try_read_agents(candidate, verbose)
        if result is not None:
            return result

        # Stop conditions
        parent = search_dir.parent
        if parent == search_dir:
            # Filesystem root reached
            break
        search_dir = parent

    return None


def _try_read_agents(path: Path, verbose: bool) -> Optional[Tuple[Path, str]]:
    """
    Attempt to read an AGENTS.md candidate file.

    Returns (path, contents) on success, or None if the file does not exist
    or cannot be read. On read failure, a warning is printed when verbose is True.
    """
    if not path.is_file():
        return None
    try:
        contents = path.read_text(encoding="utf-8")
        return (path, contents)
    except Exception as e:
        if verbose:
            from rich import print as rprint
            rprint(f"[yellow]Warning: found {path} but could not read it: {e}[/]")
        return None


def is_truncated_json(raw_text: str) -> bool:
    """
    Detect if a JSON string appears to be truncated.
    
    Simple and robust approach: checks if the response has matching outer delimiters.
    A valid JSON response must start with { or [ and end with the corresponding } or ].
    
    Args:
        raw_text: The raw response string that failed to parse as JSON
        
    Returns:
        True if the response appears to be truncated, False otherwise
    """
    if not raw_text:
        return False
    
    text = raw_text.strip()
    if not text:
        return False
    
    # Check for matching outer delimiters
    if text.startswith('{') and text.endswith('}'):
        return False
    
    if text.startswith('[') and text.endswith(']'):
        return False
    
    # If it starts with { or [ but doesn't have matching closing delimiter, it's truncated
    if text.startswith('{') or text.startswith('['):
        return True
    
    # Doesn't look like JSON at all
    return False
