import json
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from types import SimpleNamespace

from rich import print as rprint

from aye.model import auth, config, snapshot, download_plugins, vector_db, onnx_manager
from aye.controller.plugin_manager import PluginManager
from aye.controller.util import find_project_root
from aye.model.index_manager.index_manager import IndexManager
from aye.model.auth import get_user_config
from aye.model.config import (
    DEFAULT_MODEL_ID,
    SMALL_PROJECT_FILE_LIMIT,
    SMALL_PROJECT_TOTAL_SIZE_LIMIT,
)
from aye.model.snapshot.git_ref_backend import GitRefBackend
from aye.model.source_collector import get_project_files_with_limit


# --- Auth Commands ---

def login_and_fetch_plugins() -> None:
    """Initiate login flow and fetch plugins on success."""
    auth.login_flow()
    token = auth.get_token()
    if token:
        download_plugins.fetch_plugins()


def logout() -> None:
    """Remove the stored aye credentials."""
    auth.delete_token()


def get_auth_status_token() -> Optional[str]:
    """Get the current auth token for status display."""
    return auth.get_token()


# --- Snapshot Commands ---

def get_snapshot_history(file: Optional[Path] = None) -> List[str]:
    """Get a list of formatted snapshot history strings."""
    return snapshot.list_snapshots(file)


def get_snapshot_content(file: Path, ts: str) -> Optional[str]:
    """Get the content of a specific snapshot as a string.

    Args:
        file: file to retrieve from snapshot
        ts: snapshot identifier. Typically an ordinal like "001".
            Also accepts a full batch id like "001_20231201T120000".
    """
    backend = snapshot.get_backend()

    normalized = ts.zfill(3) if ts and ts.isdigit() else ts

    for batch_id, snap_ref in snapshot.list_snapshots(file):
        # Match by full batch_id or by ordinal prefix
        if batch_id == ts or batch_id == normalized or batch_id.startswith(f"{normalized}_"):
            if isinstance(backend, GitRefBackend):
                # On Windows, Path.resolve() can normalize drive casing / prefixes.
                # relative_to() is strict, so normalize both sides.
                git_root = Path(backend.git_root).resolve()
                file_resolved = file.resolve()

                try:
                    rel_path = file_resolved.relative_to(git_root).as_posix()
                except ValueError:
                    # File is outside git root; GitRefBackend may store it under __aye__/external
                    # but that requires manifest lookup. For now, treat as unsupported.
                    return None

                return backend.get_file_content_from_snapshot(rel_path, snap_ref)

            # File-based backend: snap_ref is a file path on disk
            return Path(snap_ref).read_text(encoding="utf-8")

    return None


def restore_from_snapshot(ts: Optional[str], file_name: Optional[str] = None) -> None:
    """Restore files from a snapshot."""
    snapshot.restore_snapshot(ts, file_name)


def prune_snapshots(keep: int) -> int:
    """Delete all but the most recent N snapshots."""
    return snapshot.prune_snapshots(keep)


def cleanup_old_snapshots(days: int) -> int:
    """Delete snapshots older than N days."""
    return snapshot.cleanup_snapshots(days)


def get_diff_paths(file_name: str, snap_id1: Optional[str] = None, snap_id2: Optional[str] = None) -> Tuple[Path, str, bool]:
    """Logic to determine which two files to diff.

    Returns:
        Tuple of (current_file_path, snapshot_reference, is_git_ref)

        - If is_git_ref is False:
            snapshot_reference is a filesystem path (string) to the snapshot file.
        - If is_git_ref is True:
            snapshot_reference is either:
              - "<refname>:<repo_rel_path>" for current-vs-snapshot
              - "<ref1>:<repo_rel_path>|<ref2>:<repo_rel_path>" for snapshot-vs-snapshot
    """
    file_path = Path(file_name)
    if not file_path.exists():
        raise FileNotFoundError(f"File '{file_name}' does not exist.")

    snapshots = snapshot.list_snapshots(file_path)
    if not snapshots:
        raise ValueError(f"No snapshots found for file '{file_name}'.")

    backend = snapshot.get_backend()

    # Git ref backend
    if isinstance(backend, GitRefBackend):
        # On Windows, Path.resolve() can normalize drive casing / prefixes.
        # relative_to() is strict, so normalize both sides.
        git_root = Path(backend.git_root).resolve()
        file_resolved = file_path.resolve()

        try:
            rel_path = file_resolved.relative_to(git_root).as_posix()
        except ValueError:
            raise ValueError("Diff is not supported for files outside the git repository when using GitRefBackend")

        # snapshots are tuples of (batch_id, refname)
        snapshot_refs: Dict[str, str] = {}
        for batch_id, refname in snapshots:
            ordinal = batch_id.split("_", 1)[0]
            snapshot_refs[ordinal] = f"{refname}:{rel_path}"

        def _find_matching_ordinal(user_id: str) -> Optional[str]:
            if not user_id:
                return None
            normalized = user_id.zfill(3) if user_id.isdigit() else user_id
            for ordinal in snapshot_refs.keys():
                if ordinal == normalized or ordinal.lstrip("0") == user_id.lstrip("0"):
                    return ordinal
            return None

        if snap_id1 and snap_id2:
            o1 = _find_matching_ordinal(snap_id1)
            o2 = _find_matching_ordinal(snap_id2)
            if not o1 or not o2:
                raise ValueError("Snapshot not found")
            return (file_path, f"{snapshot_refs[o1]}|{snapshot_refs[o2]}", True)

        if snap_id1:
            o1 = _find_matching_ordinal(snap_id1)
            if not o1:
                raise ValueError(f"Snapshot '{snap_id1}' not found.")
            return (file_path, snapshot_refs[o1], True)

        # Latest snapshot
        latest_ordinal = snapshots[0][0].split("_", 1)[0]
        return (file_path, snapshot_refs[latest_ordinal], True)

    # File backend
    snapshot_paths: Dict[str, Path] = {}
    for snap_ts, snap_path_str in snapshots:
        ordinal = snap_ts.split("_", 1)[0]
        snapshot_paths[ordinal] = Path(snap_path_str)

    def _find_matching_ordinal_file(user_id: str) -> Optional[str]:
        if not user_id:
            return None
        normalized = user_id.zfill(3) if user_id.isdigit() else user_id
        for ordinal in snapshot_paths.keys():
            if ordinal == normalized or ordinal.lstrip("0") == user_id.lstrip("0"):
                return ordinal
        return None

    if snap_id1 and snap_id2:
        o1 = _find_matching_ordinal_file(snap_id1)
        o2 = _find_matching_ordinal_file(snap_id2)
        if not o1 or not o2:
            raise ValueError("Snapshot not found")
        return (snapshot_paths[o1], str(snapshot_paths[o2]), False)

    if snap_id1:
        o1 = _find_matching_ordinal_file(snap_id1)
        if not o1:
            raise ValueError(f"Snapshot '{snap_id1}' not found.")
        return (file_path, str(snapshot_paths[o1]), False)

    latest_snap_path = Path(snapshots[0][1])
    return (file_path, str(latest_snap_path), False)


# --- Config Commands ---

def get_all_config() -> Dict[str, Any]:
    """Get all configuration values."""
    return config.list_config()


def set_config_value(key: str, value: str) -> None:
    """Set a configuration value."""
    try:
        parsed_value = json.loads(value)
    except json.JSONDecodeError:
        parsed_value = value
    config.set_value(key, parsed_value)


def get_config_value(key: str) -> Any:
    """Get a specific configuration value."""
    return config.get_value(key)


def delete_config_value(key: str) -> bool:
    """Delete a configuration value."""
    return config.delete_value(key)


# --- Context and Indexing Commands ---

def _calculate_total_file_size(files: List[Path]) -> int:
    """Calculate total size of files in bytes."""
    total_size = 0
    for file_path in files:
        try:
            total_size += file_path.stat().st_size
        except (OSError, IOError):
            # Skip files we can't stat
            continue
    return total_size



def _is_small_project(root: Path, file_mask: str, verbose: bool) -> Tuple[bool, List[Path]]:
    """
    Determine if this is a small project that doesn't need RAG.

    A project is considered "small" if:
    - File count < SMALL_PROJECT_FILE_LIMIT (200)
    - Total file size < SMALL_PROJECT_TOTAL_SIZE_LIMIT (170KB)

    Returns:
        Tuple of (is_small, files_list)
    """
    # Quick scan with limit
    files, limit_hit = get_project_files_with_limit(root_dir=str(root), file_mask=file_mask, limit=SMALL_PROJECT_FILE_LIMIT)

    # If we hit the file count limit, it's a large project
    if limit_hit:
        if verbose:
            rprint(f"[cyan]Large project detected: {SMALL_PROJECT_FILE_LIMIT}+ files[/]")
        return False, files

    # Check total size of discovered files
    total_size = _calculate_total_file_size(files)

    if total_size >= SMALL_PROJECT_TOTAL_SIZE_LIMIT:
        if verbose:
            rprint(f"[cyan]Large project detected: {total_size / 1024:.1f}KB total size[/]")
        return False, files

    if verbose:
        rprint(f"[cyan]Small project: {len(files)} files, {total_size / 1024:.1f}KB total[/]")

    return True, files



def initialize_project_context(root: Optional[Path], file_mask: Optional[str], ground_truth_file: Optional[str] = None) -> Any:
    """
    Initializes the project context by finding the root, setting up plugins,
    and performing an initial file scan and index.
    """
    conf = SimpleNamespace()

    # Load verbose config first
    conf.verbose = get_user_config("verbose", "off").lower() == "on"

    # Load custom system prompt from file if provided
    conf.ground_truth = None
    if ground_truth_file:
        try:
            prompt_file = Path(ground_truth_file)
            if not prompt_file.exists():
                rprint(f"[red]Error: Ground truth file not found: {ground_truth_file}[/]")
                raise SystemExit(1)
            conf.ground_truth = prompt_file.read_text(encoding="utf-8")
            if conf.verbose:
                rprint(f"[cyan]Using custom system prompt from: {ground_truth_file}[/]")
        except Exception as e:
            rprint(f"[red]Error reading ground truth file: {e}[/]")
            raise SystemExit(1)

    # 0. Ensure the ONNX model is downloaded on first launch.
    #    This is a blocking operation but only happens once per install.
    #    We do this early so the user doesn't have to wait later.
    onnx_manager.download_model_if_needed(background=False)

    # 1. Find and set the project root
    # If --root is explicitly provided, use it directly without searching for parent index
    if root:
        conf.root = root.resolve()
    else:
        # No explicit root provided, search for existing project root
        start_dir = Path.cwd()
        conf.root = find_project_root(start_dir)

    rprint(f"[bold cyan]Project root: {conf.root}[/]")

    # 2. Initialize Plugin Manager and add to conf
    plugin_manager = PluginManager(verbose=conf.verbose)
    plugin_manager.discover()
    conf.plugin_manager = plugin_manager

    # 3. Auto-detect file mask if not provided
    if not file_mask:
        response = plugin_manager.handle_command("auto_detect_mask", {"project_root": str(conf.root)})
        conf.file_mask = response["mask"] if response and response.get("mask") else "*.py"
    else:
        conf.file_mask = file_mask

    # 4. Fast project size check to determine if we need RAG
    is_small, discovered_files = _is_small_project(conf.root, conf.file_mask, conf.verbose)

    if is_small:
        # 5. Small project: Skip IndexManager entirely, use all files directly
        conf.use_rag = False
        conf.index_manager = None
        if conf.verbose:
            rprint("[cyan]Small project mode: including all files without RAG indexing.[/]")
    else:
        # 6. Large project: Initialize IndexManager for RAG-based context retrieval
        conf.use_rag = True
        conf.index_manager = IndexManager(conf.root, conf.file_mask, verbose=conf.verbose)

        if conf.verbose:
            rprint("[cyan]Scanning project for changes...[/]")
        try:
            # The prepare_sync method handles the fast scan and prints changes
            conf.index_manager.prepare_sync(verbose=conf.verbose)
        except Exception as e:
            rprint(f"[red]Error during project scan: {e}[/]")
            rprint("[yellow]Proceeding without index updates.[/]")

    # 7. Load other configs
    conf.selected_model = get_user_config("selected_model", DEFAULT_MODEL_ID)

    return conf
