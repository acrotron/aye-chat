"""File-based snapshot backend using .aye/snapshots directory."""

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .base import SnapshotBackend

# Module-level constants
SNAP_ROOT = Path(".aye/snapshots").resolve()
LATEST_SNAP_DIR = SNAP_ROOT / "latest"


class FileBasedBackend(SnapshotBackend):
    """File-based snapshot backend using .aye/snapshots directory."""

    def __init__(self):
        self.snap_root = SNAP_ROOT
        self.latest_dir = LATEST_SNAP_DIR

    def _get_next_ordinal(self) -> int:
        """Get the next ordinal number by checking existing snapshot directories."""
        if not self.snap_root.is_dir():
            return 1

        ordinals = []
        for batch_dir in self.snap_root.iterdir():
            if batch_dir.is_dir() and "_" in batch_dir.name and batch_dir.name != "latest":
                try:
                    ordinal = int(batch_dir.name.split("_")[0])
                    ordinals.append(ordinal)
                except ValueError:
                    continue

        return max(ordinals, default=0) + 1

    def _ensure_batch_dir(self, ts: str) -> Path:
        """Create (or return) the batch directory for a given timestamp."""
        ordinal = self._get_next_ordinal()
        ordinal_str = f"{ordinal:03d}"
        batch_dir_name = f"{ordinal_str}_{ts}"
        batch_dir = self.snap_root / batch_dir_name
        batch_dir.mkdir(parents=True, exist_ok=True)
        return batch_dir

    def _truncate_prompt(self, prompt: Optional[str], max_length: int = 32) -> str:
        """Truncate a prompt to max_length characters, adding ellipsis if needed."""
        if not prompt:
            return "no prompt".ljust(max_length)
        prompt = prompt.strip()
        if not prompt:
            return "no prompt".ljust(max_length)
        if len(prompt) <= max_length:
            return prompt.ljust(max_length)
        return prompt[:max_length] + "..."

    def _list_all_snapshots_with_metadata(self) -> List[str]:
        """List all snapshots in descending order with file names from metadata."""
        if not self.snap_root.is_dir():
            return []

        batch_dirs = [p for p in self.snap_root.iterdir() if p.is_dir() and p.name != "latest"]
        batch_dirs.sort(key=lambda p: p.name.split("_", 1)[1] if "_" in p.name else p.name, reverse=True)

        result = []
        for batch_dir in batch_dirs:
            ts = batch_dir.name
            if "_" in ts:
                ordinal_part, _ = ts.split("_", 1)
            else:
                ordinal_part = ts

            meta_path = batch_dir / "metadata.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                prompt_str = self._truncate_prompt(meta.get("prompt"))

                files = []
                cwd = Path.cwd()
                for entry in meta["files"]:
                    original_path_str = entry["original"]
                    try:
                        files.append(str(Path(original_path_str).relative_to(cwd)))
                    except ValueError:
                        files.append(original_path_str)

                files_str = ", ".join(files)
                result.append(f"{ordinal_part}  ({prompt_str})  {files_str}")
            else:
                result.append(f"{ordinal_part}  (metadata missing)")
        return result

    def create_snapshot(self, file_paths: List[Path], prompt: Optional[str] = None) -> str:
        """Snapshot the current contents of the given files."""
        if not file_paths:
            raise ValueError("No files supplied for snapshot")

        changed_files: List[Path] = []
        for src_path in file_paths:
            src_path = src_path.resolve()
            changed_files.append(src_path)

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        batch_dir = self._ensure_batch_dir(ts)

        meta_entries: List[Dict[str, Any]] = []
        for src_path in changed_files:
            dest_path = batch_dir / src_path.name
            if src_path.is_file():
                shutil.copy2(src_path, dest_path)
            else:
                dest_path.write_text("", encoding="utf-8")
            meta_entries.append({"original": str(src_path), "snapshot": str(dest_path)})

        meta = {"timestamp": ts, "files": meta_entries}
        if prompt:
            meta["prompt"] = prompt
        (batch_dir / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

        if self.latest_dir.exists():
            shutil.rmtree(self.latest_dir)
        self.latest_dir.mkdir(parents=True, exist_ok=True)
        for src_path in changed_files:
            dest_path = self.latest_dir / src_path.name
            if src_path.is_file():
                shutil.copy2(src_path, dest_path)
            else:
                dest_path.write_text("", encoding="utf-8")

        return batch_dir.name

    def list_snapshots(self, file: Optional[Path] = None) -> Union[List[str], List[Tuple[str, str]]]:
        """Return all batch-snapshot timestamps, newest first, or snapshots for a specific file."""
        if file is None:
            return self._list_all_snapshots_with_metadata()

        if not self.snap_root.is_dir():
            return []

        snapshots = []
        for batch_dir in self.snap_root.iterdir():
            if batch_dir.is_dir() and batch_dir.name != "latest":
                meta_path = batch_dir / "metadata.json"
                if meta_path.exists():
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    for entry in meta["files"]:
                        if Path(entry["original"]).resolve() == file.resolve():
                            snapshots.append((batch_dir.name, entry["snapshot"]))
        snapshots.sort(key=lambda x: x[0], reverse=True)
        return snapshots

    def restore_snapshot(self, ordinal: Optional[str] = None, file_name: Optional[str] = None) -> None:
        """Restore files from a batch snapshot."""
        if ordinal is None and file_name is not None:
            snapshots = self.list_snapshots(Path(file_name))
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
            timestamps = self.list_snapshots()
            if not timestamps:
                raise ValueError("No snapshots found")
            ordinal = timestamps[0].split()[0].split("(")[0] if timestamps else None
            if not ordinal:
                raise ValueError("No snapshots found")

        batch_dir = None
        if ordinal.isdigit() and len(ordinal) == 3:
            for dir_path in self.snap_root.iterdir():
                if dir_path.is_dir() and dir_path.name.startswith(f"{ordinal}_"):
                    batch_dir = dir_path
                    break
        if batch_dir is None:
            raise ValueError(f"Snapshot with Id {ordinal} not found")

        meta_file = batch_dir / "metadata.json"
        if not meta_file.is_file():
            raise ValueError(f"Metadata missing for snapshot {ordinal}")

        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid metadata for snapshot {ordinal}: {e}")

        if file_name is not None:
            target_path = Path(file_name).resolve()
            filtered_entries = [
                entry for entry in meta["files"]
                if Path(entry["original"]).resolve() == target_path
            ]
            if not filtered_entries:
                raise ValueError(f"File '{file_name}' not found in snapshot {ordinal}")
            meta["files"] = filtered_entries

        for entry in meta["files"]:
            original = Path(entry["original"])
            snapshot_path = Path(entry["snapshot"])
            try:
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(snapshot_path, original)
            except FileNotFoundError:
                print(f"Warning: snapshot file missing – {snapshot_path}")
            except Exception as e:
                print(f"Warning: failed to restore {original}: {e}")
                continue

    def list_all_snapshots(self) -> List[Path]:
        """List all snapshot directories in chronological order (oldest first)."""
        if not self.snap_root.is_dir():
            return []
        snapshots = [p for p in self.snap_root.iterdir() if p.is_dir() and "_" in p.name and p.name != "latest"]
        snapshots.sort(key=lambda p: p.name.split("_", 1)[1])
        return snapshots

    def delete_snapshot(self, snapshot_dir: Path) -> None:
        """Delete a snapshot directory and all its contents."""
        if snapshot_dir.is_dir():
            shutil.rmtree(snapshot_dir)
            print(f"Deleted snapshot: {snapshot_dir.name}")

    def prune_snapshots(self, keep_count: int = 10) -> int:
        """Delete all but the most recent N snapshots."""
        snapshots = self.list_all_snapshots()
        if len(snapshots) <= keep_count:
            return 0
        to_delete = snapshots[:-keep_count] if keep_count > 0 else snapshots
        deleted_count = 0
        for snapshot_dir in to_delete:
            self.delete_snapshot(snapshot_dir)
            deleted_count += 1
        return deleted_count

    def cleanup_snapshots(self, older_than_days: int = 30) -> int:
        """Delete snapshots older than N days."""
        snapshots = self.list_all_snapshots()
        cutoff_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=older_than_days)
        deleted_count = 0
        for snapshot_dir in snapshots:
            try:
                ts_part = snapshot_dir.name.split("_", 1)[1]
                snapshot_time = datetime.strptime(ts_part, "%Y%m%dT%H%M%S")
                if snapshot_time < cutoff_time:
                    self.delete_snapshot(snapshot_dir)
                    deleted_count += 1
            except (ValueError, IndexError):
                print(f"Warning: Could not parse timestamp from {snapshot_dir.name}")
                continue
        return deleted_count
