"""Git stash-based snapshot backend."""

import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from rich import print as rprint

from .base import SnapshotBackend


class GitStashBackend(SnapshotBackend):
    """Git stash-based snapshot backend."""

    AYE_STASH_PREFIX = "aye:"

    def __init__(self, git_root: Path):
        self.git_root = git_root

    def _run_git(self, args: List[str], check: bool = True, capture_output: bool = True) -> subprocess.CompletedProcess:
        """Execute a git command and return the result."""
        cmd = ["git"] + args
        result = subprocess.run(
            cmd,
            cwd=self.git_root,
            capture_output=capture_output,
            text=True
        )
        if check and result.returncode != 0:
            raise RuntimeError(f"Git command failed: {' '.join(cmd)}\n{result.stderr}")
        return result

    def _get_stash_list(self) -> List[Dict[str, Any]]:
        """Parse git stash list and return aye-prefixed stashes with metadata."""
        result = self._run_git(["stash", "list"], check=False)
        if result.returncode != 0:
            return []

        stashes = []
        # Parse: stash@{0}: On branch: aye: 001_20231201T120000 | prompt | files
        pattern = re.compile(r'stash@\{(\d+)\}.*?' + re.escape(self.AYE_STASH_PREFIX) + r'\s*(\d{3}_\d{8}T\d{6})\s*\|\s*([^|]*)\|\s*(.*)')

        for line in result.stdout.splitlines():
            if self.AYE_STASH_PREFIX not in line:
                continue

            match = pattern.match(line)
            if match:
                stash_idx = int(match.group(1))
                batch_id = match.group(2)
                prompt = match.group(3).strip()
                files = match.group(4).strip()

                stashes.append({
                    'index': stash_idx,
                    'batch_id': batch_id,
                    'ordinal': batch_id.split('_')[0],
                    'timestamp': batch_id.split('_')[1] if '_' in batch_id else '',
                    'prompt': prompt,
                    'files': files
                })

        return stashes

    def _get_next_ordinal(self) -> int:
        """Get the next ordinal number from existing aye stashes."""
        stashes = self._get_stash_list()
        if not stashes:
            return 1
        ordinals = [int(s['ordinal']) for s in stashes]
        return max(ordinals, default=0) + 1

    def _truncate_prompt(self, prompt: Optional[str], max_length: int = 32) -> str:
        """Truncate a prompt to max_length characters."""
        if not prompt:
            return "no prompt".ljust(max_length)
        prompt = prompt.strip()
        if not prompt:
            return "no prompt".ljust(max_length)
        if len(prompt) <= max_length:
            return prompt.ljust(max_length)
        return prompt[:max_length] + "..."

    def _check_other_uncommitted_changes(self, target_files: List[Path]) -> List[Path]:
        """Check for uncommitted changes to files NOT in target_files."""
        result = self._run_git(["status", "--porcelain"], check=False)
        if result.returncode != 0:
            return []

        target_set = {f.resolve() for f in target_files}
        changed_files = []

        for line in result.stdout.splitlines():
            if len(line) < 3:
                continue
            status = line[:2]
            filepath_str = line[3:].strip()
            # Handle quoted paths (git uses quotes for special chars)
            if filepath_str.startswith('"') and filepath_str.endswith('"'):
                filepath_str = filepath_str[1:-1]
            # Git status returns paths relative to git root, so resolve from git_root
            filepath = (self.git_root / filepath_str).resolve()

            if filepath not in target_set and status.strip():
                changed_files.append(filepath)

        return changed_files

    def _warn_uncommitted_changes(self, files: List[Path]) -> None:
        """Print a warning about uncommitted changes to other files."""
        rprint(f"[yellow]Warning: You have uncommitted changes to {len(files)} other file(s):[/]")
        for f in files[:5]:
            try:
                rel_path = f.relative_to(self.git_root)
            except ValueError:
                rel_path = f
            rprint(f"[yellow]  - {rel_path}[/]")
        if len(files) > 5:
            rprint(f"[yellow]  ... and {len(files) - 5} more[/]")

    def _get_untracked_files(self, file_paths: List[Path]) -> List[Path]:
        """Get list of untracked files from the given paths."""
        result = self._run_git(["ls-files", "--others", "--exclude-standard"], check=False)
        if result.returncode != 0:
            return []

        untracked_set = set()
        for line in result.stdout.splitlines():
            untracked_set.add((self.git_root / line.strip()).resolve())

        return [f for f in file_paths if f.resolve() in untracked_set]

    def get_file_content_from_snapshot(self, file_path: str, stash_ref: str) -> Optional[str]:
        """Extract file content from a git stash.
        
        Args:
            file_path: Relative path to the file (from git root)
            stash_ref: Stash reference like 'stash@{0}'
            
        Returns:
            File content as string, or None if file not found in stash
        """
        try:
            # Use git show to extract file content from stash
            result = self._run_git(["show", f"{stash_ref}:{file_path}"], check=False)
            if result.returncode == 0:
                return result.stdout
            return None
        except Exception:
            return None

    def create_snapshot(self, file_paths: List[Path], prompt: Optional[str] = None) -> str:
        """Create a git stash for the specified files."""
        if not file_paths:
            raise ValueError("No files supplied for snapshot")

        # Resolve all paths
        resolved_files = [f.resolve() for f in file_paths]

        # Check for uncommitted changes to OTHER files
        other_changes = self._check_other_uncommitted_changes(resolved_files)
        if other_changes:
            self._warn_uncommitted_changes(other_changes)

        # Handle untracked files - they need to be staged first
        untracked = self._get_untracked_files(resolved_files)
        if untracked:
            self._run_git(["add", "--intent-to-add", "--"] + [str(f) for f in untracked])

        # Generate ordinal and timestamp
        ordinal = self._get_next_ordinal()
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        batch_id = f"{ordinal:03d}_{ts}"

        # Build stash message with metadata - use relative paths from current directory
        prompt_part = (prompt[:50].replace("|", "-") if prompt else "no prompt")
        files_list = []
        cwd = Path.cwd()
        for f in resolved_files[:5]:
            try:
                # Try to make path relative to current directory first
                rel_path = f.relative_to(cwd)
                files_list.append(str(rel_path))
            except ValueError:
                # If file is outside CWD, try relative to git root
                try:
                    rel_path = f.relative_to(self.git_root)
                    files_list.append(str(rel_path))
                except ValueError:
                    # If we can't make it relative to either, use the name as fallback
                    files_list.append(f.name)
        if len(resolved_files) > 5:
            files_list.append(f"...+{len(resolved_files) - 5}")
        files_str = ",".join(files_list)
        stash_message = f"{self.AYE_STASH_PREFIX} {batch_id} | {prompt_part} | {files_str}"

        # Check if files have any changes to stash
        status_result = self._run_git(["status", "--porcelain", "--"] + [str(f) for f in resolved_files], check=False)
        if not status_result.stdout.strip():
            # No changes to stash - create a placeholder commit-like entry
            # For now, we'll just return the batch_id without creating a stash
            rprint(f"[yellow]Note: No changes to snapshot for the specified files.[/]")
            return batch_id

        # Create the stash with only the specified files
        file_args = ["--"] + [str(f) for f in resolved_files]
        stash_result = self._run_git(["stash", "push", "-m", stash_message] + file_args, check=False)

        if stash_result.returncode != 0:
            # Stash failed - might be no changes or other issue
            if "No local changes" in stash_result.stdout or "No local changes" in stash_result.stderr:
                rprint(f"[yellow]Note: No changes to snapshot.[/]")
                return batch_id
            raise RuntimeError(f"Failed to create stash: {stash_result.stderr}")

        # Immediately apply the stash to keep files modified (mimics file-based behavior)
        self._run_git(["stash", "apply", "stash@{0}"], check=False)

        return batch_id

    def list_snapshots(self, file: Optional[Path] = None) -> Union[List[str], List[Tuple[str, str]]]:
        """List aye-created stashes."""
        stashes = self._get_stash_list()

        if file is None:
            # Return formatted strings
            return [
                f"{s['ordinal']}  ({self._truncate_prompt(s['prompt'])})  {s['files']}"
                for s in stashes
            ]
        else:
            # Return tuples for specific file
            file_resolved = file.resolve()
            result = []
            for s in stashes:
                # Check if this file is in the stash
                show_result = self._run_git(["stash", "show", f"stash@{{{s['index']}}}"], check=False)
                if show_result.returncode == 0:
                    for line in show_result.stdout.splitlines():
                        stashed_file = line.split("|")[0].strip() if "|" in line else line.strip()
                        stashed_path = (self.git_root / stashed_file).resolve()
                        if stashed_path == file_resolved:
                            result.append((s['batch_id'], f"stash@{{{s['index']}}}"))
                            break
            return result

    def restore_snapshot(self, ordinal: Optional[str] = None, file_name: Optional[str] = None) -> None:
        """Restore from a git stash (uses pop to remove stash after restore)."""
        stashes = self._get_stash_list()
        if not stashes:
            raise ValueError("No snapshots found")

        if ordinal is None and file_name is not None:
            # Find most recent stash containing this file
            file_resolved = Path(file_name).resolve()
            for s in stashes:
                show_result = self._run_git(["stash", "show", f"stash@{{{s['index']}}}"], check=False)
                if show_result.returncode == 0:
                    for line in show_result.stdout.splitlines():
                        stashed_file = line.split("|")[0].strip() if "|" in line else line.strip()
                        stashed_path = (self.git_root / stashed_file).resolve()
                        if stashed_path == file_resolved:
                            # Restore this specific file
                            stash_ref = f"stash@{{{s['index']}}}"
                            self._run_git(["checkout", stash_ref, "--", file_name])
                            self._run_git(["stash", "drop", stash_ref])
                            return
            raise ValueError(f"No snapshots found for file '{file_name}'")

        if ordinal is None:
            # Use most recent
            target_stash = stashes[0]
        else:
            # Find by ordinal
            target_stash = next((s for s in stashes if s['ordinal'] == ordinal), None)
            if not target_stash:
                raise ValueError(f"Snapshot with Id {ordinal} not found")

        stash_ref = f"stash@{{{target_stash['index']}}}"

        if file_name:
            # Restore specific file from stash then drop the stash
            self._run_git(["checkout", stash_ref, "--", file_name])
            self._run_git(["stash", "drop", stash_ref])
        else:
            # Restore all files using pop (restores and removes stash)
            result = self._run_git(["stash", "pop", stash_ref], check=False)
            if result.returncode != 0:
                if "CONFLICT" in result.stdout or "CONFLICT" in result.stderr:
                    rprint("[yellow]Warning: Merge conflicts occurred. Please resolve manually.[/]")
                    rprint("[yellow]Run 'git status' to see conflicting files.[/]")
                else:
                    raise RuntimeError(f"Failed to restore stash: {result.stderr}")

    def list_all_snapshots(self) -> List[Path]:
        """List all aye stash identifiers (as pseudo-paths for API compatibility)."""
        stashes = self._get_stash_list()
        # Sort by timestamp (oldest first)
        stashes.sort(key=lambda s: s['timestamp'])
        # Return batch_ids as Path objects for API compatibility
        return [Path(s['batch_id']) for s in stashes]

    def delete_snapshot(self, snapshot_id: Any) -> None:
        """Delete a specific stash by batch_id or Path."""
        batch_id = str(snapshot_id.name if isinstance(snapshot_id, Path) else snapshot_id)
        stashes = self._get_stash_list()
        for s in stashes:
            if s['batch_id'] == batch_id:
                self._run_git(["stash", "drop", f"stash@{{{s['index']}}}"])
                print(f"Deleted snapshot: {batch_id}")
                return
        print(f"Warning: Snapshot {batch_id} not found")

    def prune_snapshots(self, keep_count: int = 10) -> int:
        """Delete all but the most recent N aye stashes."""
        stashes = self._get_stash_list()
        if len(stashes) <= keep_count:
            return 0

        # Sort by ordinal descending to keep newest
        stashes.sort(key=lambda s: int(s['ordinal']), reverse=True)
        to_delete = stashes[keep_count:]

        deleted = 0
        # Delete from highest stash index first to avoid index shifting issues
        for stash in sorted(to_delete, key=lambda s: s['index'], reverse=True):
            self._run_git(["stash", "drop", f"stash@{{{stash['index']}}}"])
            print(f"Deleted snapshot: {stash['batch_id']}")
            deleted += 1

        return deleted

    def cleanup_snapshots(self, older_than_days: int = 30) -> int:
        """Delete stashes older than N days."""
        stashes = self._get_stash_list()
        cutoff_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=older_than_days)
        deleted_count = 0

        # Process from highest index first to avoid shifting
        for stash in sorted(stashes, key=lambda s: s['index'], reverse=True):
            try:
                snapshot_time = datetime.strptime(stash['timestamp'], "%Y%m%dT%H%M%S")
                if snapshot_time < cutoff_time:
                    self._run_git(["stash", "drop", f"stash@{{{stash['index']}}}"])
                    print(f"Deleted snapshot: {stash['batch_id']}")
                    deleted_count += 1
            except ValueError:
                print(f"Warning: Could not parse timestamp from {stash['batch_id']}")
                continue

        return deleted_count
