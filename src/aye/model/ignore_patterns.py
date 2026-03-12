"""Shared utility for loading .gitignore and .ayeignore patterns.

This module provides a centralized function for loading ignore patterns
from .gitignore and .ayeignore files. This ensures consistent behavior
across all parts of the codebase that need to respect these patterns.
"""

import platform
from pathlib import Path

import pathspec

from aye.model.config import DEFAULT_IGNORE_SET


def load_ignore_patterns(root: Path) -> pathspec.PathSpec:
    """
    Load ignore patterns from .gitignore and .ayeignore files.
    
    Walks up the directory tree from `root` to the filesystem root,
    collecting patterns from all .gitignore and .ayeignore files found.
    Also includes DEFAULT_IGNORE_SET patterns.
    
    Args:
        root: The root directory to start searching from
        
    Returns:
        A PathSpec object that can be used to match files against the patterns
        
    Example:
        >>> from pathlib import Path
        >>> ignore_spec = load_ignore_patterns(Path.cwd())
        >>> if ignore_spec.match_file("node_modules/package.json"):
        ...     print("File is ignored")
    """
    patterns = list(DEFAULT_IGNORE_SET)

    # If running on Windows and the root is the home directory, add common
    # problematic directory names to the ignore list to prevent hangs when
    # scanning network-mapped folders (e.g., OneDrive).
    try:
        if platform.system() == "Windows" and root.resolve() == Path.home().resolve():
            windows_home_ignores = [
                "OneDrive",
                "Documents",
                "Pictures",
                "Videos",
                "Music",
                "Downloads",
                "AppData",
            ]
            patterns.extend(windows_home_ignores)
    except Exception:
        # Path.home() can fail; proceed without special ignores.
        pass

    current_path = root.resolve()

    # Walk up the directory tree to the filesystem root
    while True:
        for ignore_name in (".gitignore", ".ayeignore"):
            ignore_file = current_path / ignore_name
            if ignore_file.is_file():
                try:
                    with ignore_file.open("r", encoding="utf-8") as f:
                        patterns.extend(
                            line.rstrip() for line in f
                            if line.strip() and not line.strip().startswith("#")
                        )
                except Exception:
                    # Ignore files we can't read
                    pass
        
        # Check if we've reached the filesystem root
        if current_path.parent == current_path:
            break
        
        current_path = current_path.parent

    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)
