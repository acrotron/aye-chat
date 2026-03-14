"""Snapshot module - provides file versioning with automatic backend selection.

When in a git repository, uses a private git-ref/commit backend for snapshots.
Otherwise, falls back to file-based snapshots in .aye/snapshots.

Note:
- Git stash snapshots (GitStashBackend) are intentionally NOT used.
"""

import json
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from .base import SnapshotBackend
from .file_backend import FileBasedBackend, SNAP_ROOT, LATEST_SNAP_DIR
from .git_ref_backend import GitRefBackend

__all__ = [
    # Classes
    "SnapshotBackend",
    "FileBasedBackend",
    "GitRefBackend",
    # Constants
    "SNAP_ROOT",
    "LATEST_SNAP_DIR",
    # Public API
    "create_snapshot",
    "list_snapshots",
    "restore_snapshot",
    "apply_updates",
    "list_all_snapshots",
    "delete_snapshot",
    "prune_snapshots",
    "cleanup_snapshots",
    "get_diff_base_for_file",
    # Utilities
    "get_backend",
    "reset_backend",
    # Legacy helpers (for backward compatibility)
    "_get_next_ordinal",
    "_get_latest_snapshot_dir",
    "_truncate_prompt",
    "_list_all_snapshots_with_metadata",
    "_is_git_repository",
]

# ------------------------------------------------------------------
# Backend Factory
# ------------------------------------------------------------------
_backend: Optional[SnapshotBackend] = None


def _is_git_repository() -> Optional[Path]:
    """Check if current directory is inside a git repository.

    Returns:
        Path to git root if in a repo, None otherwise
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def get_backend() -> SnapshotBackend:
    """Get the appropriate snapshot backend (singleton pattern)."""
    global _backend

    if _backend is None:
        #git_root = _is_git_repository()
        #if git_root:
        #    _backend = GitRefBackend(git_root)
        #else:
        #    _backend = FileBasedBackend()

        # disabling GitRefBackend for now: it's not finished yet.
        _backend = FileBasedBackend()

    return _backend


def reset_backend() -> None:
    """Reset the backend singleton (useful for testing)."""
    global _backend
    _backend = None


# ------------------------------------------------------------------
# Public API (delegates to backend)
# ------------------------------------------------------------------
def create_snapshot(file_paths: List[Path], prompt: Optional[str] = None) -> str:
    """Snapshot the current contents of the given files."""
    return get_backend().create_snapshot(file_paths, prompt)


def list_snapshots(file: Optional[Path] = None) -> Union[List[str], List[Tuple[str, str]]]:
    """Return all batch-snapshot timestamps, newest first, or snapshots for a specific file."""
    return get_backend().list_snapshots(file)


def restore_snapshot(ordinal: Optional[str] = None, file_name: Optional[str] = None) -> None:
    """Restore files from a batch snapshot."""
    return get_backend().restore_snapshot(ordinal, file_name)


def apply_updates(
    updated_files: List[Dict[str, str]], 
    prompt: Optional[str] = None,
    root: Optional[Path] = None
) -> str:
    """
    1. Take a snapshot of the *current* files.
    2. Write the new contents supplied by the LLM.
    
    Args:
        updated_files: List of dicts with 'file_name' and 'file_content' keys
        prompt: Optional prompt text for the snapshot metadata
        root: Optional project root path. If provided, relative paths are 
              resolved against it. If None, paths are used as-is (relative 
              to current working directory).
    
    Returns:
        The batch timestamp (useful for UI feedback).
    """
    # Build list of file paths, resolving against root if provided
    file_paths: List[Path] = []
    for item in updated_files:
        if "file_name" not in item or "file_content" not in item:
            continue
        
        file_name = item["file_name"]
        if root is not None and not Path(file_name).is_absolute():
            file_paths.append(root / file_name)
        else:
            file_paths.append(Path(file_name))
    
    # Create snapshot of current state
    batch_ts = create_snapshot(file_paths, prompt)
    
    # Write new contents
    for item in updated_files:
        if "file_name" not in item or "file_content" not in item:
            continue
        
        file_name = item["file_name"]
        if root is not None and not Path(file_name).is_absolute():
            fp = root / file_name
        else:
            fp = Path(file_name)
        
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(item["file_content"], encoding="utf-8")
    
    return batch_ts


def list_all_snapshots() -> List[Path]:
    """List all snapshot directories in chronological order (oldest first)."""
    return get_backend().list_all_snapshots()


def delete_snapshot(snapshot_dir: Path) -> None:
    """Delete a snapshot directory/entry."""
    return get_backend().delete_snapshot(snapshot_dir)


def prune_snapshots(keep_count: int = 10) -> int:
    """Delete all but the most recent N snapshots. Returns number of deleted snapshots."""
    return get_backend().prune_snapshots(keep_count)


def cleanup_snapshots(older_than_days: int = 30) -> int:
    """Delete snapshots older than N days. Returns number of deleted snapshots."""
    return get_backend().cleanup_snapshots(older_than_days)


def get_diff_base_for_file(batch_id: str, file_path: Path) -> Optional[Tuple[str, bool]]:
    """Return the snapshot reference for a file in a given batch.

    This provides a backend-agnostic way to get a reference suitable for
    diff_presenter.show_diff().

    Args:
        batch_id: The batch identifier returned by apply_updates()
        file_path: The file path to get the snapshot reference for

    Returns:
        Tuple of (snapshot_ref, is_git_ref) where:
        - snapshot_ref: For FileBasedBackend, a filesystem path to the snapshot file.
                        For GitRefBackend, a 'refname:repo_rel_path' string.
        - is_git_ref: True if snapshot_ref is a git ref format, False for filesystem path.
        Returns None if the file is not found in the snapshot.
    """
    backend = get_backend()

    if isinstance(backend, FileBasedBackend):
        return _get_diff_base_file_backend(backend, batch_id, file_path)
    elif isinstance(backend, GitRefBackend):
        return _get_diff_base_git_backend(backend, batch_id, file_path)

    return None


def _get_diff_base_file_backend(
    backend: FileBasedBackend, batch_id: str, file_path: Path
) -> Optional[Tuple[str, bool]]:
    """Get diff base for FileBasedBackend.

    Reads the metadata.json from the snapshot batch directory to find
    the snapshot path for the given file.
    """
    # Find the batch directory matching the batch_id
    # batch_id format is like "001_20231201T120000"
    batch_dir = None
    if backend.snap_root.is_dir():
        for dir_path in backend.snap_root.iterdir():
            if dir_path.is_dir() and dir_path.name == batch_id:
                batch_dir = dir_path
                break

    if batch_dir is None:
        return None

    meta_file = batch_dir / "metadata.json"
    if not meta_file.is_file():
        return None

    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    # Resolve the target file path for comparison
    file_resolved = file_path.resolve()

    # Find the matching entry in metadata
    for entry in meta.get("files", []):
        original_path = entry.get("original")
        snapshot_path = entry.get("snapshot")

        if not original_path or not snapshot_path:
            continue

        if Path(original_path).resolve() == file_resolved:
            # Return the snapshot path as a string, not a git ref
            return (snapshot_path, False)

    return None


def _get_diff_base_git_backend(
    backend: GitRefBackend, batch_id: str, file_path: Path
) -> Optional[Tuple[str, bool]]:
    """Get diff base for GitRefBackend.

    Returns a ref:path format suitable for git show.
    """
    # Construct the refname from batch_id
    refname = f"{backend.REF_NAMESPACE}/{batch_id}"

    # Get repo-relative path
    try:
        file_resolved = file_path.resolve()
        rel_path = file_resolved.relative_to(backend.git_root).as_posix()
    except ValueError:
        # File is outside git root
        return None

    # Return the git ref format
    return (f"{refname}:{rel_path}", True)


# ------------------------------------------------------------------
# Legacy helper functions (for backward compatibility with tests)
# ------------------------------------------------------------------
def _get_next_ordinal() -> int:
    """Get the next ordinal number - delegates to the active backend."""
    backend = get_backend()
    if isinstance(backend, FileBasedBackend):
        return backend._get_next_ordinal()
    if isinstance(backend, GitRefBackend):
        return backend._get_next_ordinal()
    return 1


def _get_latest_snapshot_dir() -> Optional[Path]:
    """Get the latest snapshot directory by finding the one with the highest ordinal."""
    backend = get_backend()
    if isinstance(backend, FileBasedBackend):
        if not backend.snap_root.is_dir():
            return None

        snapshot_dirs = []
        for batch_dir in backend.snap_root.iterdir():
            if batch_dir.is_dir() and "_" in batch_dir.name and batch_dir.name != "latest":
                try:
                    ordinal = int(batch_dir.name.split("_")[0])
                    snapshot_dirs.append((ordinal, batch_dir))
                except ValueError:
                    continue

        if not snapshot_dirs:
            return None

        snapshot_dirs.sort(key=lambda x: x[0])
        return snapshot_dirs[-1][1]
    return None


def _truncate_prompt(prompt: Optional[str], max_length: int = 32) -> str:
    """Truncate a prompt to max_length characters."""
    backend = get_backend()
    if hasattr(backend, "_truncate_prompt"):
        return backend._truncate_prompt(prompt, max_length)
    if not prompt:
        return "no prompt".ljust(max_length)
    prompt = prompt.strip()
    if not prompt:
        return "no prompt".ljust(max_length)
    if len(prompt) <= max_length:
        return prompt.ljust(max_length)
    return prompt[:max_length] + "..."


def _list_all_snapshots_with_metadata() -> List[str]:
    """List all snapshots in descending order with file names from metadata."""
    backend = get_backend()
    if isinstance(backend, FileBasedBackend):
        return backend._list_all_snapshots_with_metadata()
    # For other backends, delegate to list_snapshots
    return backend.list_snapshots()


def driver():
    list_snapshots()


if __name__ == "__main__":
    driver()
