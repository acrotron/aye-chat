from pathlib import Path
from typing import List, Tuple
import pathspec
import platform

from aye.model.config import DEFAULT_IGNORE_SET, SMALL_PROJECT_FILE_LIMIT


def _load_ignore_patterns(root_dir: Path) -> pathspec.PathSpec:
    """
    Load ignore patterns from .gitignore and .ayeignore files in the root
    directory and all parent directories up to the filesystem root.
    """
    patterns = list(DEFAULT_IGNORE_SET)

    # If running on Windows and the root is the home directory, add common
    # problematic directory names to the ignore list to prevent hangs when
    # scanning network-mapped folders (e.g., OneDrive).
    try:
        if platform.system() == "Windows" and root_dir.resolve() == Path.home().resolve():
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

    current_path = root_dir.resolve()

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
                    pass
        
        if current_path.parent == current_path:
            break
        
        current_path = current_path.parent

    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


def get_project_files_with_limit(root_dir: str, file_mask: str, limit: int = SMALL_PROJECT_FILE_LIMIT) -> Tuple[List[Path], bool]:
    """
    Enumerate project files up to a limit.
    
    Args:
        root_dir: Root directory to scan
        file_mask: Comma-separated glob patterns for files to include
        limit: Maximum number of files to enumerate before stopping
        
    Returns:
        A tuple of (file_list, limit_hit) where:
        - file_list: List of Path objects for files found (up to limit)
        - limit_hit: True if we stopped because we hit the limit, False otherwise
    """
    root_path = Path(root_dir).resolve()
    if not root_path.is_dir():
        return [], False

    ignore_spec = _load_ignore_patterns(root_path)
    patterns = [p.strip() for p in file_mask.split(",") if p.strip()]
    
    collected_files: List[Path] = []
    
    for pattern in patterns:
        for file_path in root_path.rglob(pattern):
            if len(collected_files) >= limit:
                return collected_files, True
                
            if not file_path.is_file():
                continue
            
            try:
                rel_path = file_path.relative_to(root_path)
                rel_path_str = rel_path.as_posix()
                
                if ignore_spec.match_file(rel_path_str):
                    continue
                    
                # all directories starting with a dot are ignored
                # including common directories such as .github, but this is by design
                if any(part.startswith('.') for part in rel_path.parts):
                    continue
                    
                collected_files.append(file_path)
                
            except (ValueError, OSError):
                continue
    
    return collected_files, False


def get_project_files(root_dir: str, file_mask: str) -> List[Path]:
    """
    Recursively collect all project files matching the file mask,
    respecting .gitignore and .ayeignore patterns.
    
    Args:
        root_dir: Root directory to scan
        file_mask: Comma-separated glob patterns for files to include
        
    Returns:
        List of Path objects for all matching files
    """
    root_path = Path(root_dir).resolve()
    if not root_path.is_dir():
        return []

    ignore_spec = _load_ignore_patterns(root_path)
    patterns = [p.strip() for p in file_mask.split(",") if p.strip()]
    
    collected_files: List[Path] = []
    
    for pattern in patterns:
        for file_path in root_path.rglob(pattern):
            if not file_path.is_file():
                continue
            
            try:
                rel_path = file_path.relative_to(root_path)
                rel_path_str = rel_path.as_posix()
                
                if ignore_spec.match_file(rel_path_str):
                    continue
                    
                if any(part.startswith('.') for part in rel_path.parts):
                    continue
                    
                collected_files.append(file_path)
                
            except (ValueError, OSError):
                continue
    
    return collected_files


def collect_sources(root_dir: str, file_mask: str) -> dict[str, str]:
    """
    Collect source files and return a dictionary mapping relative paths to content.
    
    Args:
        root_dir: Root directory to scan
        file_mask: Comma-separated glob patterns for files to include
        
    Returns:
        Dictionary mapping relative file paths to their content
    """
    root_path = Path(root_dir).resolve()
    files = get_project_files(root_dir, file_mask)
    
    result = {}
    for file_path in files:
        try:
            rel_path = file_path.relative_to(root_path).as_posix()
            content = file_path.read_text(encoding="utf-8")
            result[rel_path] = content
        except Exception:
            continue
    
    return result
