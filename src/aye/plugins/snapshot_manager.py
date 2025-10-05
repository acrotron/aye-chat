import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from .base import Plugin
from rich import print as rprint

SNAP_ROOT = Path(".aye/snapshots").resolve()
LATEST_SNAP_DIR = SNAP_ROOT / "latest"


class SnapshotManagerPlugin(Plugin):
    name = "snapshot_manager"
    version = "1.0.0"
    premium = "free"

    def init(self, cfg: Dict[str, Any]) -> None:
        """Initialize the snapshot manager plugin."""
        pass

    def _get_next_ordinal(self) -> int:
        """Get the next ordinal number by checking existing snapshot directories."""
        if not SNAP_ROOT.is_dir():
            return 1
        
        ordinals = []
        for batch_dir in SNAP_ROOT.iterdir():
            if batch_dir.is_dir() and "_" in batch_dir.name and batch_dir.name != "latest":
                try:
                    ordinal = int(batch_dir.name.split("_")[0])
                    ordinals.append(ordinal)
                except ValueError:
                    continue
        
        return max(ordinals, default=0) + 1

    def _get_latest_snapshot_dir(self) -> Optional[Path]:
        """Get the latest snapshot directory by finding the one with the highest ordinal."""
        if not SNAP_ROOT.is_dir():
            return None
        
        snapshot_dirs = []
        for batch_dir in SNAP_ROOT.iterdir():
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

    def _ensure_batch_dir(self, ts: str) -> Path:
        """Create (or return) the batch directory for a given timestamp."""
        ordinal = self._get_next_ordinal()
        ordinal_str = f"{ordinal:03d}"
        batch_dir_name = f"{ordinal_str}_{ts}"
        batch_dir = SNAP_ROOT / batch_dir_name
        batch_dir.mkdir(parents=True, exist_ok=True)
        return batch_dir

    def _list_all_snapshots_with_metadata(self) -> List[str]:
        """List all snapshots in descending order with file names from metadata."""
        if not SNAP_ROOT.is_dir():
            return []

        timestamps = [p.name for p in SNAP_ROOT.iterdir() if p.is_dir() and p.name != "latest"]
        timestamps.sort(reverse=True)
        result = []
        for ts in timestamps:
            if "_" in ts:
                ordinal_part, timestamp_part = ts.split("_", 1)
                formatted_ts = f"{ordinal_part} ({timestamp_part})"
            else:
                formatted_ts = ts
                
            meta_path = SNAP_ROOT / ts / "metadata.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text())
                files = [Path(entry["original"]).name for entry in meta["files"]]
                files_str = ",".join(files)
                result.append(f"{formatted_ts}  {files_str}")
            else:
                result.append(f"{formatted_ts}  (metadata missing)")
        return result

    def create_snapshot(self, file_paths: List[Path]) -> str:
        """Create a snapshot of current files."""
        if not file_paths:
            raise ValueError("No files supplied for snapshot")

        changed_files = []
        latest_snap_dir = self._get_latest_snapshot_dir()
        
        for src_path in file_paths:
            src_path = src_path.resolve()
            if src_path.is_file():
                current_content = src_path.read_text()
                if latest_snap_dir is not None:
                    snapshot_content_path = latest_snap_dir / src_path.name
                    if snapshot_content_path.exists():
                        snapshot_content = snapshot_content_path.read_text()
                        if current_content == snapshot_content:
                            continue
                changed_files.append(src_path)
            else:
                changed_files.append(src_path)
        
        if not changed_files:
            return ""

        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        batch_dir = self._ensure_batch_dir(ts)

        meta_entries: List[Dict[str, Any]] = []

        for src_path in changed_files:
            dest_path = batch_dir / src_path.name

            if src_path.is_file():
                shutil.copy2(src_path, dest_path)
            else:
                dest_path.write_text("")

            meta_entries.append(
                {"original": str(src_path), "snapshot": str(dest_path)}
            )

        meta = {"timestamp": ts, "files": meta_entries}
        (batch_dir / "metadata.json").write_text(json.dumps(meta, indent=2))

        if LATEST_SNAP_DIR.exists():
            shutil.rmtree(LATEST_SNAP_DIR)
        
        LATEST_SNAP_DIR.mkdir(parents=True, exist_ok=True)
        for src_path in changed_files:
            dest_path = LATEST_SNAP_DIR / src_path.name
            if src_path.is_file():
                shutil.copy2(src_path, dest_path)
            else:
                dest_path.write_text("")

        return batch_dir.name

    def list_snapshots(self, file: Optional[Path] = None) -> List[str]:
        """List snapshots for a file or all snapshots."""
        if file is None:
            return self._list_all_snapshots_with_metadata()
        
        if not SNAP_ROOT.is_dir():
            return []

        snapshots = []
        for batch_dir in SNAP_ROOT.iterdir():
            if batch_dir.is_dir() and batch_dir.name != "latest":
                meta_path = batch_dir / "metadata.json"
                if meta_path.exists():
                    meta = json.loads(meta_path.read_text())
                    for entry in meta["files"]:
                        if Path(entry["original"]) == file.resolve():
                            snapshots.append((batch_dir.name, entry["snapshot"]))
        snapshots.sort(key=lambda x: x[0], reverse=True)
        return snapshots

    def restore_snapshot(self, ordinal: Optional[str] = None, file_name: Optional[str] = None) -> None:
        """Restore files from a snapshot."""
        if ordinal is None:
            timestamps = self.list_snapshots()
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
            filtered_entries = [
                entry for entry in meta["files"]
                if Path(entry["original"]).name == file_name
            ]
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

    def apply_updates(self, updated_files: List[Dict[str, str]]) -> str:
        """Apply updates and create snapshots."""
        file_paths: List[Path] = [
            Path(item["file_name"])
            for item in updated_files
            if "file_name" in item and "file_content" in item
        ]

        batch_ts = self.create_snapshot(file_paths)

        if not batch_ts:
            return ""

        for item in updated_files:
            fp = Path(item["file_name"])
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(item["file_content"])

        return batch_ts

    def prune_snapshots(self, keep_count: int = 10) -> int:
        """Delete all but the most recent N snapshots. Returns number of deleted snapshots."""
        if not SNAP_ROOT.is_dir():
            return 0

        snapshots = []
        for batch_dir in SNAP_ROOT.iterdir():
            if batch_dir.is_dir() and "_" in batch_dir.name and batch_dir.name != "latest":
                try:
                    ts_part = batch_dir.name.split("_", 1)[1]
                    snapshot_time = datetime.strptime(ts_part, "%Y%m%dT%H%M%S")
                    snapshots.append((snapshot_time, batch_dir))
                except (ValueError, IndexError):
                    continue

        # Sort by timestamp (oldest first)
        snapshots.sort(key=lambda x: x[0])
        
        if len(snapshots) <= keep_count:
            return 0
        
        # Delete the oldest snapshots
        to_delete = snapshots[:-keep_count]
        deleted_count = 0
        
        for _, snapshot_dir in to_delete:
            try:
                shutil.rmtree(snapshot_dir)
                deleted_count += 1
            except Exception:
                continue
        
        return deleted_count

    def _handle_history_command(self) -> None:
        """Handle the history command logic and output."""
        timestamps = self.list_snapshots()
        if not timestamps:
            rprint("[yellow]No snapshots found.[/]")
        else:
            rprint("[bold]Snapshot History:[/]")
            for ts in timestamps:
                rprint(f"  {ts}")

    def _handle_restore_command(self, tokens: List[str]) -> None:
        """Handle the restore command logic and output."""
        ordinal = tokens[0] if tokens else None
        file_name = tokens[1] if len(tokens) > 1 else None
        
        try:
            self.restore_snapshot(ordinal, file_name)
            if ordinal:
                if file_name:
                    rprint(f"[green]✅ File '{file_name}' restored to {ordinal}[/]")
                else:
                    rprint(f"[green]✅ All files restored to {ordinal}[/]")
            else:
                if file_name:
                    rprint(f"[green]✅ File '{file_name}' restored to latest snapshot.[/]")
                else:
                    rprint("[green]✅ All files restored to latest snapshot.[/]")
        except Exception as e:
            rprint(f"[red]Error restoring snapshot:[/] {e}")

    def _handle_keep_command(self, tokens: List[str]) -> None:
        """Handle the keep command logic and output."""
        try:
            keep_count = int(tokens[0]) if tokens and tokens[0].isdigit() else 10
            deleted_count = self.prune_snapshots(keep_count)
            rprint(f"✅ {deleted_count} snapshots pruned. {keep_count} most recent kept.")
        except Exception as e:
            rprint(f"Error pruning snapshots: {e}")

    def on_command(self, command_name: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Handle snapshot-related commands through plugin system."""
        try:
            if command_name in {"history", "/history"}:
                self._handle_history_command()
                return {"handled": True}
            
            elif command_name in {"restore", "revert", "/restore", "/revert"}:
                args = params.get("args", [])
                self._handle_restore_command(args)
                return {"handled": True}
            
            elif command_name in {"keep", "/keep"}:
                args = params.get("args", [])
                self._handle_keep_command(args)
                return {"handled": True}
            
            elif command_name == "apply_updates":
                updated_files = params.get("updated_files", [])
                batch_ts = self.apply_updates(updated_files)
                return {"batch_timestamp": batch_ts}
            
            elif command_name == "list_snapshots":
                file = params.get("file")
                if file:
                    file = Path(file)
                return {"snapshots": self.list_snapshots(file)}
            
            elif command_name == "restore_snapshot":
                ordinal = params.get("ordinal")
                file_name = params.get("file_name")
                self.restore_snapshot(ordinal, file_name)
                return {"success": True}
            
            elif command_name == "create_snapshot":
                file_paths = [Path(p) for p in params.get("file_paths", [])]
                batch_ts = self.create_snapshot(file_paths)
                return {"batch_timestamp": batch_ts}
            
        except Exception as e:
            return {"error": str(e)}
        
        return None