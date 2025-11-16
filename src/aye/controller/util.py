from pathlib import Path
import os
from typing import Union, Optional

# The only marker we care about now is the index file inside the .aye directory
PROJECT_MARKER = ".aye/file_index.json"

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
