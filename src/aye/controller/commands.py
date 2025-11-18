import json
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from types import SimpleNamespace

from rich import print as rprint

from aye.model import auth, config, snapshot, download_plugins, vector_db, onnx_manager
from aye.controller.plugin_manager import PluginManager
from aye.controller.util import find_project_root
from aye.model.index_manager import IndexManager
from aye.model.auth import get_user_config
from aye.model.config import DEFAULT_MODEL_ID


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

def get_diff_paths(file_name: str, snap_id1: Optional[str] = None, snap_id2: Optional[str] = None) -> Tuple[Path, Path]:
    """Logic to determine which two files to diff."""
    file_path = Path(file_name)
    if not file_path.exists():
        raise FileNotFoundError(f"File '{file_name}' does not exist.")

    snapshots = snapshot.list_snapshots(file_path)
    if not snapshots:
        raise ValueError(f"No snapshots found for file '{file_name}'.")

    snapshot_paths = {}
    for snap_ts, snap_path_str in snapshots:
        ordinal = snap_ts.split('_')[0]
        snapshot_paths[ordinal] = Path(snap_path_str)

    if snap_id1 and snap_id2:
        # Diff between two snapshots
        if snap_id1 not in snapshot_paths:
            raise ValueError(f"Snapshot '{snap_id1}' not found.")
        if snap_id2 not in snapshot_paths:
            raise ValueError(f"Snapshot '{snap_id2}' not found.")
        return (snapshot_paths[snap_id1], snapshot_paths[snap_id2])
    elif snap_id1:
        # Diff between current file and one snapshot
        if snap_id1 not in snapshot_paths:
            raise ValueError(f"Snapshot '{snap_id1}' not found.")
        return (file_path, snapshot_paths[snap_id1])
    else:
        # Diff between current file and latest snapshot
        latest_snap_path = Path(snapshots[0][1])
        return (file_path, latest_snap_path)


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

def initialize_project_context(root: Optional[Path], file_mask: Optional[str]) -> Any:
    """
    Initializes the project context by finding the root, setting up plugins,
    and performing an initial file scan and index.
    """
    conf = SimpleNamespace()

    # Load verbose config first
    conf.verbose = get_user_config("verbose", "off").lower() == "on"

    # 1. Ensure the ONNX model is downloaded before proceeding. This is a blocking
    #    operation required for the RAG system to initialize correctly.
    onnx_manager.download_model_if_needed(background=False)

    # 2. Find and set the project root
    start_dir = root if root else Path.cwd()
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
