from pathlib import Path
from typing import Dict, Any, Set, List, Iterable
from itertools import chain


def _is_hidden(path: Path) -> bool:
    """Return True if *path* or any of its ancestors is a hidden directory.

    Hidden directories are those whose name starts with a dot (".").
    """ 
    return any(part.startswith(".") for part in path.parts)


def _load_patterns_from_file(file_path: Path) -> List[str]:
    """Load patterns from a single ignore file, filtering out empty lines and comments."""
    try:
        patterns = file_path.read_text(encoding="utf-8").splitlines()
        # Filter out empty lines and comments
        return [
            pattern.strip() for pattern in patterns 
            if pattern.strip() and not pattern.strip().startswith("#")
        ]
    except Exception:
        # If we can't read the file, proceed without its ignore patterns
        return []


def _load_ignore_patterns(root_path: Path) -> list[str]:
    """Load ignore patterns from .ayeignore and .gitignore files in the root directory and all parent directories."""
    ignore_patterns = []
    
    # Start from root_path and go up through all parent directories
    current_path = root_path.resolve()
    
    # Include .ayeignore and .gitignore from all parent directories
    while current_path != current_path.parent:  # Stop when we reach the root directory
        # Load .ayeignore file
        ayeignore_file = current_path / ".ayeignore"
        if ayeignore_file.exists():
            ignore_patterns.extend(_load_patterns_from_file(ayeignore_file))
        
        # Load .gitignore file
        gitignore_file = current_path / ".gitignore"
        if gitignore_file.exists():
            ignore_patterns.extend(_load_patterns_from_file(gitignore_file))
        
        current_path = current_path.parent
    
    return ignore_patterns


def _matches_ignore_pattern(path: Path, ignore_patterns: list[str], base_path: Path) -> bool:
    """Check if a path matches any of the ignore patterns."""
    try:
        relative_path = path.relative_to(base_path)
    except ValueError:
        # Path is not relative to base_path, shouldn't happen but be safe
        return False
    
    path_str = relative_path.as_posix()
    
    for pattern in ignore_patterns:
        # Handle directory patterns (ending with /)
        if pattern.endswith("/"):
            dir_pattern = pattern.rstrip("/")
            # Check if any part of the path matches the directory pattern
            if any(part == dir_pattern for part in relative_path.parts):
                return True
        else:
            # For file patterns, check if the file name or full path matches
            if relative_path.match(pattern):
                return True
            # Also check just the file name for simpler patterns
            if path.name == pattern or Path(path.name).match(pattern):
                return True
    
    return False


def collect_sources(
    root_dir: str = ".",
    file_mask: str = "*.py",
    recursive: bool = True,
) -> Dict[str, str]:
    sources: Dict[str, str] = {}
    base_path = Path(root_dir).expanduser().resolve()

    if not base_path.is_dir():
        raise NotADirectoryError(f"'{root_dir}' is not a valid directory")

    # Load ignore patterns from .ayeignore and .gitignore files
    ignore_patterns = _load_ignore_patterns(base_path)

    masks: List[str] = [m.strip() for m in file_mask.split(",") if m.strip()]  # e.g. ["*.py", "*.jsx"]

    def _iter_for(mask: str) -> Iterable[Path]:
        return base_path.rglob(mask) if recursive else base_path.glob(mask)

    # Chain all iterators; convert to a set to deduplicate paths
    all_matches: Set[Path] = set(chain.from_iterable(_iter_for(m) for m in masks))

    for py_file in all_matches:
        # Skip hidden subfolders (any part of the path starting with '.')
        if _is_hidden(py_file.relative_to(base_path)):
            continue
        
        # Skip files that match ignore patterns
        if _matches_ignore_pattern(py_file, ignore_patterns, base_path):
            continue
            
        if not py_file.is_file():
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
            rel_key = py_file.relative_to(base_path).as_posix()
            sources[rel_key] = content
        except UnicodeDecodeError:
            # Skip non-UTF8 files
            print(f"   Skipping non-UTF8 file: {py_file}")

    return sources


# ----------------------------------------------------------------------
# Example usage
def driver():
    py_dict = collect_sources()               # looks in ./aye
    # Or: py_dict = collect_py_sources("path/to/aye")

    # Show the keys (file names) that were collected
    print("Collected .py files:", list(py_dict.keys()))

    # Print the first 120 characters of each file (for demo)
    for name, txt in py_dict.items():
        print(f"\n--- {name} ---")
        print(txt[:120] + ("…" if len(txt) > 120 else ""))


if __name__ == "__main__":
    driver()
