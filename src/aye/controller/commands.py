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
from aye.model.config import DEFAULT_MODEL_ID
from aye.model.snapshot.git_backend import GitStashBackend


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
    """Get the content of a specific snapshot as a string."""
    for snap_ts, snap_path in snapshot.list_snapshots(file):
        if snap_ts == ts:
            return Path(snap_path).read_text(encoding="utf-8")
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
        Tuple of (current_file_path, snapshot_reference, is_stash_ref)
        where snapshot_reference is either a file path or a stash reference like 'stash@{0}:path/to/file'
    """
    file_path = Path(file_name)
    if not file_path.exists():
        raise FileNotFoundError(f"File '{file_name}' does not exist.")

    snapshots = snapshot.list_snapshots(file_path)
    if not snapshots:
        raise ValueError(f"No snapshots found for file '{file_name}'.")

    # Check if we're using git backend
    backend = snapshot.get_backend()
    is_git_backend = isinstance(backend, GitStashBackend)

    if is_git_backend:
        # For git backend, snapshots are tuples of (batch_id, stash_ref)
        # Build a mapping from ordinal to stash reference for this file
        snapshot_refs = {}
        for batch_id, stash_ref in snapshots:
            ordinal = batch_id.split('_')[0]
            # Get the file path relative to git root for the stash reference
            try:
                rel_path = file_path.resolve().relative_to(backend.git_root)
                snapshot_refs[ordinal] = f"{stash_ref}:{rel_path.as_posix()}"
            except ValueError:
                # File is outside git root
                snapshot_refs[ordinal] = f"{stash_ref}:{file_path.name}"

        if snap_id1 and snap_id2:
            # Diff between two snapshots
            if snap_id1 not in snapshot_refs or snap_id2 not in snapshot_refs:
                raise ValueError(f"Snapshot not found")
            # For two stash refs, we need to extract both contents
            # Return first stash ref and indicate special handling needed
            return (file_path, f"{snapshot_refs[snap_id2]}|{snapshot_refs[snap_id1]}", True)
        elif snap_id1:
            # Diff between current file and one snapshot
            # Normalize the ordinal to 3 digits for comparison
            normalized_snap_id1 = snap_id1.zfill(3) if snap_id1.isdigit() else snap_id1
            
            # Also check if any ordinal matches when normalized
            matching_ordinal = None
            for ordinal in snapshot_refs.keys():
                if ordinal == normalized_snap_id1 or ordinal.lstrip('0') == snap_id1.lstrip('0'):
                    matching_ordinal = ordinal
                    break
            
            if not matching_ordinal:
                raise ValueError(f"Snapshot '{snap_id1}' not found.")
            
            return (file_path, snapshot_refs[matching_ordinal], True)
        else:
            # Diff between current file and latest snapshot
            latest_ordinal = snapshots[0][0].split('_')[0]
            return (file_path, snapshot_refs[latest_ordinal], True)
    else:
        # For file backend, snapshots are tuples of (batch_id, file_path)
        snapshot_paths = {}
        for snap_ts, snap_path_str in snapshots:
            ordinal = snap_ts.split('_')[0]
            snapshot_paths[ordinal] = Path(snap_path_str)

        if snap_id1 and snap_id2:
            # Diff between two snapshots
            if snap_id1 not in snapshot_paths or snap_id2 not in snapshot_paths:
                raise ValueError(f"Snapshot not found")
            return (snapshot_paths[snap_id1], str(snapshot_paths[snap_id2]), False)
        elif snap_id1:
            # Diff between current file and one snapshot
            # Normalize the ordinal to 3 digits for comparison
            normalized_snap_id1 = snap_id1.zfill(3) if snap_id1.isdigit() else snap_id1
            
            # Also check if any ordinal matches when normalized
            matching_ordinal = None
            for ordinal in snapshot_paths.keys():
                if ordinal == normalized_snap_id1 or ordinal.lstrip('0') == snap_id1.lstrip('0'):
                    matching_ordinal = ordinal
                    break
            
            if not matching_ordinal:
                raise ValueError(f"Snapshot '{snap_id1}' not found.")
            
            return (file_path, str(snapshot_paths[matching_ordinal]), False)
        else:
            # Diff between current file and latest snapshot
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

    # 1. Ensure the ONNX model is downloaded before proceeding. This is a blocking
    #    operation required for the RAG system to initialize correctly.
    onnx_manager.download_model_if_needed(background=False)

    # 2. Find and set the project root
    # If --root is explicitly provided, use it directly without searching for parent index
    if root:
        conf.root = root.resolve()
    else:
        # No explicit root provided, search for existing project root
        start_dir = Path.cwd()
        conf.root = find_project_root(start_dir)

    # 3. Initialize Plugin Manager and add to conf
    plugin_manager = PluginManager(verbose=conf.verbose)
    plugin_manager.discover()
    conf.plugin_manager = plugin_manager

    # 4. Auto-detect file mask if not provided
    if not file_mask:
        response = plugin_manager.handle_command(
            "auto_detect_mask", {"project_root": str(conf.root)}
        )
        conf.file_mask = response["mask"] if response and response.get("mask") else "*.py"
    else:
        conf.file_mask = file_mask

    # 5. Initialize the IndexManager, which handles vector DB and file scanning
    conf.index_manager = IndexManager(conf.root, conf.file_mask, verbose=conf.verbose)

    # 6. Perform initial file scan and prepare for background indexing
    if conf.verbose:
        rprint("[cyan]Scanning project for changes...[/]")
    try:
        # The prepare_sync method now handles the fast scan and prints changes
        conf.index_manager.prepare_sync(verbose=conf.verbose)
    except Exception as e:
        rprint(f"[red]Error during project scan: {e}[/]")
        rprint("[yellow]Proceeding without index updates.[/]")

    # 7. Load other configs
    conf.selected_model = get_user_config("selected_model", DEFAULT_MODEL_ID)

    return conf
