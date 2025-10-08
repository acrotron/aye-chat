import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

SNAP_ROOT = Path(".aye/snapshots").resolve()
LATEST_SNAP_DIR = SNAP_ROOT / "latest"


def _get_next_ordinal() -> int:
    """Get the next ordinal number by checking existing snapshot directories."""
    batches_root = SNAP_ROOT
    if not batches_root.is_dir():
        return 1
    
    ordinals = []
    for batch_dir in batches_root.iterdir():
        if batch_dir.is_dir() and "_" in batch_dir.name and batch_dir.name != "latest":
            try:
                ordinal = int(batch_dir.name.split("_")[0])
                ordinals.append(ordinal)
            except ValueError:
                continue
    
    return max(ordinals, default=0) + 1


def _get_latest_snapshot_dir() -> Path | None:
    """Get the latest snapshot directory by finding the one with the highest ordinal."""
    batches_root = SNAP_ROOT
    if not batches_root.is_dir():
        return None
    
    snapshot_dirs = []
    for batch_dir in batches_root.iterdir():
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

# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------
def _ensure_batch_dir(ts: str) -> Path:
    """Create (or return) the batch directory for a given timestamp."""
    ordinal = _get_next_ordinal()
    ordinal_str = f"{ordinal:03d}"
    batch_dir_name = f"{ordinal_str}_{ts}"
    batch_dir = SNAP_ROOT / batch_dir_name
    batch_dir.mkdir(parents=True, exist_ok=True)
    return batch_dir


def _list_all_snapshots_with_metadata():
    """List all snapshots in descending order with file names from metadata."""
    batches_root = SNAP_ROOT
    if not batches_root.is_dir():
        return []

    timestamps = [p.name for p in batches_root.iterdir() if p.is_dir() and p.name != "latest"]
    timestamps.sort(reverse=True)
    result = []
    for ts in timestamps:
        if "_" in ts:
            ordinal_part, timestamp_part = ts.split("_", 1)
            formatted_ts = f"{ordinal_part} ({timestamp_part})"
        else:
            formatted_ts = ts
        
        meta_path = batches_root / ts / "metadata.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            files = [Path(entry["original"]).name for entry in meta["files"]]
            files_str = ",".join(files)
            result.append(f"{formatted_ts}  {files_str}")
        else:
            result.append(f"{formatted_ts}  (metadata missing)")
    return result

# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------
def create_snapshot(file_paths: List[Path]) -> str:
    """Snapshot the **current** contents of the given files.

    The function now always creates a snapshot for the supplied files, even if a
    file's content matches the most recent snapshot. This ensures that subsequent
    update logic (e.g., ``apply_updates``) always has a snapshot to compare
    against and that files are written to disk when the LLM requests changes.
    """
    if not file_paths:
        raise ValueError("No files supplied for snapshot")

    # Previously the code filtered out files whose content matched the latest
    # snapshot, which caused ``apply_updates`` to skip writing updates when the
    # LLM suggested a change for a file that hadn't been modified since the
    # previous snapshot. The filtering has been removed – every provided path is
    # considered a changed file for snapshot purposes.
    changed_files: List[Path] = []
    for src_path in file_paths:
        src_path = src_path.resolve()
        if src_path.is_file():
            changed_files.append(src_path)
        else:
            # Include non‑existent (new) files as well
            changed_files.append(src_path)
    
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    batch_dir = _ensure_batch_dir(ts)

    meta_entries: List[Dict[str, Any]] = []
    for src_path in changed_files:
        dest_path = batch_dir / src_path.name
        if src_path.is_file():
            shutil.copy2(src_path, dest_path)
        else:
            dest_path.write_text("", encoding="utf-8")
        meta_entries.append({"original": str(src_path), "snapshot": str(dest_path)})

    meta = {"timestamp": ts, "files": meta_entries}
    (batch_dir / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # Update the latest snapshot directory
    if LATEST_SNAP_DIR.exists():
        shutil.rmtree(LATEST_SNAP_DIR)
    LATEST_SNAP_DIR.mkdir(parents=True, exist_ok=True)
    for src_path in changed_files:
        dest_path = LATEST_SNAP_DIR / src_path.name
        if src_path.is_file():
            shutil.copy2(src_path, dest_path)
        else:
            dest_path.write_text("", encoding="utf-8")

    return batch_dir.name

def list_snapshots(file: Path | None = None) -> List[str] | List[tuple[str, str]]:
    """Return all batch-snapshot timestamps, newest first, or snapshots for a specific file."""
    if file is None:
        return _list_all_snapshots_with_metadata()
    
    batches_root = SNAP_ROOT
    if not batches_root.is_dir():
        return []

    snapshots = []
    for batch_dir in batches_root.iterdir():
        if batch_dir.is_dir() and batch_dir.name != "latest":
            meta_path = batch_dir / "metadata.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text())
                for entry in meta["files"]:
                    if Path(entry["original"]).resolve() == file.resolve():
                        snapshots.append((batch_dir.name, entry["snapshot"]))
    snapshots.sort(key=lambda x: x[0], reverse=True)
    return snapshots

def restore_snapshot(ordinal: str | None = None, file_name: str | None = None) -> None:
    """
    Restore *all* files from a batch snapshot identified by ordinal number.
    If ``ordinal`` is omitted the most recent snapshot is used.
    If ``file_name`` is provided, only that file is restored.
    New behavior: when ``ordinal`` is ``None`` and ``file_name`` is provided,
    the function restores the most recent snapshot *for that file*.
    """
    if ordinal is None and file_name is not None:
        snapshots = list_snapshots(Path(file_name))
        if not snapshots:
            raise ValueError(f"No snapshots found for file '{file_name}'")
        _, snapshot_path_str = snapshots[0]
        snapshot_path = Path(snapshot_path_str)
        original_path = Path(file_name).resolve()
        original_path.parent.mkdir(parents=True, exist_ok=True)
        if not snapshot_path.exists():
            raise FileNotFoundError(f"Snapshot file missing: {snapshot_path}")
        shutil.copy2(snapshot_path, original_path)
        return

    if ordinal is None:
        timestamps = list_snapshots()
        if not timestamps:
            raise ValueError("No snapshots found")
        ordinal = timestamps[0].split()[0].split("(")[0] if timestamps else None
        if not ordinal:
            raise ValueError("No snapshots found")

    batch_dir = None
    if ordinal.isdigit() and len(ordinal) == 3:
        for dir_path in SNAP_ROOT.iterdir():
            if dir_path.is_dir() and dir_path.name.startswith(f"{ordinal}_"):
                batch_dir = dir_path
                break
    if batch_dir is None:
        raise ValueError(f"Snapshot with ordinal {ordinal} not found")

    meta_file = batch_dir / "metadata.json"
    if not meta_file.is_file():
        raise ValueError(f"Metadata missing for snapshot {ordinal}")

    try:
        meta = json.loads(meta_file.read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid metadata for snapshot {ordinal}: {e}")

    if file_name is not None:
        filtered_entries = [entry for entry in meta["files"] if Path(entry["original"]).name == file_name]
        if not filtered_entries:
            raise ValueError(f"File '{file_name}' not found in snapshot {ordinal}")
        meta["files"] = filtered_entries

    for entry in meta["files"]:
        original = Path(entry["original"])
        snapshot_path = Path(entry["snapshot"])
        if not snapshot_path.exists():
            print(f"Warning: snapshot file missing – {snapshot_path}")
            continue
        try:
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(snapshot_path, original)
        except Exception as e:
            print(f"Warning: failed to restore {original}: {e}")
            continue

def apply_updates(updated_files: List[Dict[str, str]]) -> str:
    """
    1. Take a snapshot of the *current* files.
    2. Write the new contents supplied by the LLM.
    Returns the batch timestamp (useful for UI feedback).
    """
    file_paths: List[Path] = [Path(item["file_name"]) for item in updated_files if "file_name" in item and "file_content" in item]
    batch_ts = create_snapshot(file_paths)
    # Even if ``create_snapshot`` returns an empty string (which it no longer does), we still write the updates.
    for item in updated_files:
        fp = Path(item["file_name"])
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(item["file_content"], encoding="utf-8")
    return batch_ts

# ------------------------------------------------------------------
# Snapshot cleanup/pruning functions
# ------------------------------------------------------------------
def list_all_snapshots() -> List[Path]:
    """List all snapshot directories in chronological order (oldest first)."""
    batches_root = SNAP_ROOT
    if not batches_root.is_dir():
        return []
    snapshots = [p for p in batches_root.iterdir() if p.is_dir() and "_" in p.name and p.name != "latest"]
    snapshots.sort(key=lambda p: p.name.split("_", 1)[1])
    return snapshots

def delete_snapshot(snapshot_dir: Path) -> None:
    """Delete a snapshot directory and all its contents."""
    if snapshot_dir.is_dir():
        shutil.rmtree(snapshot_dir)
        print(f"Deleted snapshot: {snapshot_dir.name}")

def prune_snapshots(keep_count: int = 10) -> int:
    """Delete all but the most recent N snapshots. Returns number of deleted snapshots."""
    snapshots = list_all_snapshots()
    if len(snapshots) <= keep_count:
        return 0
    to_delete = snapshots[:-keep_count]
    deleted_count = 0
    for snapshot_dir in to_delete:
        delete_snapshot(snapshot_dir)
        deleted_count += 1
    return deleted_count

def cleanup_snapshots(older_than_days: int = 30) -> int:
    """Delete snapshots older than N days. Returns number of deleted snapshots."""
    from datetime import timedelta
    snapshots = list_all_snapshots()
    cutoff_time = datetime.utcnow() - timedelta(days=older_than_days)
    deleted_count = 0
    for snapshot_dir in snapshots:
        try:
            ts_part = snapshot_dir.name.split("_", 1)[1]
            snapshot_time = datetime.strptime(ts_part, "%Y%m%dT%H%M%S")
            if snapshot_time < cutoff_time:
                delete_snapshot(snapshot_dir)
                deleted_count += 1
        except (ValueError, IndexError):
            print(f"Warning: Could not parse timestamp from {snapshot_dir.name}")
            continue
    return deleted_count

def driver():
    list_snapshots()

if __name__ == "__main__":
    driver()
