from pathlib import Path
from typing import Dict, Any, Set, List, Iterable
from itertools import chain
import pathspec


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
    ignore_patterns: List[str] = []
    
    # Start from root_path and go up through all parent directories
    current_path = root_path.resolve()
    
    # Include .ayeignore and .gitignore from all parent directories
    while current_path != current_path.parent:  # Stop when we reach the filesystem root
        for ignore_name in (".ayeignore", ".gitignore"):
            ignore_file = current_path / ignore_name
            if ignore_file.exists():
                ignore_patterns.extend(_load_patterns_from_file(ignore_file))
        current_path = current_path.parent
    
    return ignore_patterns


def get_project_files(
    root_dir: str = ".",
    file_mask: str = "*.py",
    recursive: bool = True,
) -> List[Path]:
    """
    Collects a list of source file paths based on include masks and ignore patterns,
    without reading their content. This is an I/O-optimized way to get a file list.

    Returns:
        A list of Path objects for all matching files.
    """
    base_path = Path(root_dir).expanduser().resolve()

    if not base_path.is_dir():
        raise NotADirectoryError(f"'{root_dir}' is not a valid directory")

    ignore_patterns = _load_ignore_patterns(base_path)
    spec = pathspec.PathSpec.from_lines("gitwildmatch", ignore_patterns)

    masks: List[str] = [m.strip() for m in file_mask.split(",") if m.strip()]

    def _iter_for(mask: str) -> Iterable[Path]:
        return base_path.rglob(mask) if recursive else base_path.glob(mask)

    all_matches: Set[Path] = set(chain.from_iterable(_iter_for(m) for m in masks))

    project_files = []
    for py_file in all_matches:
        if _is_hidden(py_file.relative_to(base_path)):
            continue
        
        rel_path = py_file.relative_to(base_path).as_posix()
        if spec.match_file(rel_path):
            continue
        
        if not py_file.is_file():
            continue
        
        project_files.append(py_file)
    
    return project_files


def collect_sources(
    root_dir: str = ".",
    file_mask: str = "*.py",
    recursive: bool = True,
) -> Dict[str, str]:
    """
    Collects source files and their content into a dictionary.
    Uses get_project_files to find relevant files and then reads them.
    """
    sources: Dict[str, str] = {}
    base_path = Path(root_dir).expanduser().resolve()
    
    project_files = get_project_files(root_dir, file_mask, recursive)

    for py_file in project_files:
        try:
            content = py_file.read_text(encoding="utf-8")
            rel_key = py_file.relative_to(base_path).as_posix()
            sources[rel_key] = content
        except UnicodeDecodeError:
            # Skip non‑UTF8 files
            print(f"   Skipping non‑UTF8 file: {py_file}")

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
