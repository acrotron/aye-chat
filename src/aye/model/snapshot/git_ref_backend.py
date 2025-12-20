"""Git ref/commit-backed snapshot backend (Option B: partial snapshots + manifest).

This backend creates snapshot commits without touching the user's working tree or index.
Snapshots are stored as commits referenced by private refs:

    refs/aye/snapshots/<batch_id>

Each snapshot commit contains:
- __aye__/manifest.json
- blobs for each snapshotted file that existed at snapshot time

Restores are performed via direct file I/O (write/delete) based on the manifest.

Important implementation detail:
- We use a temporary git index file (GIT_INDEX_FILE) to build a tree.
- The index path must NOT be a pre-created empty file. If it exists but is empty/truncated,
  git can error with: "index file smaller than expected".
- Therefore we point GIT_INDEX_FILE at a non-existent path inside a temp directory and
  initialize it with `git read-tree --empty`.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from rich import print as rprint

from .base import SnapshotBackend


@dataclass(frozen=True)
class _SnapshotRef:
    batch_id: str
    refname: str  # full refname, e.g. refs/aye/snapshots/001_...
    commit: str
    ordinal: str
    timestamp: str


class GitRefBackend(SnapshotBackend):
    """Git commit snapshot backend backed by private refs and a manifest."""

    REF_NAMESPACE = "refs/aye/snapshots"
    MANIFEST_PATH = "__aye__/manifest.json"
    EXTERNAL_PREFIX = "__aye__/external"

    def __init__(self, git_root: Path):
        self.git_root = git_root.resolve()

    # ------------------------------------------------------------
    # Git helpers
    # ------------------------------------------------------------
    def _run_git(
        self,
        args: List[str],
        *,
        check: bool = True,
        capture_output: bool = True,
        text: bool = True,
        env: Optional[Dict[str, str]] = None,
        input_data: Optional[Union[str, bytes]] = None,
    ) -> subprocess.CompletedProcess:
        cmd = ["git"] + args
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)

        result = subprocess.run(
            cmd,
            cwd=self.git_root,
            capture_output=capture_output,
            text=text,
            env=merged_env,
            input=input_data,
        )

        if check and result.returncode != 0:
            stderr = result.stderr if result.stderr is not None else ""
            raise RuntimeError(f"Git command failed: {' '.join(cmd)}\n{stderr}")
        return result

    def _head_commit(self) -> Optional[str]:
        """Return HEAD commit sha, or None if repository has no commits."""
        res = self._run_git(["rev-parse", "HEAD"], check=False)
        if res.returncode != 0:
            return None
        sha = (res.stdout or "").strip()
        return sha or None

    def _parse_batch_id(self, batch_id: str) -> Tuple[str, str]:
        """Return (ordinal, timestamp) where timestamp is the string after '_'."""
        if "_" not in batch_id:
            return (batch_id, "")
        ordinal, timestamp = batch_id.split("_", 1)
        return (ordinal, timestamp)

    def _truncate_prompt(self, prompt: Optional[str], max_length: int = 32) -> str:
        if not prompt:
            return "no prompt".ljust(max_length)
        prompt = prompt.strip()
        if not prompt:
            return "no prompt".ljust(max_length)
        if len(prompt) <= max_length:
            return prompt.ljust(max_length)
        return prompt[:max_length] + "..."

    def _get_all_snapshot_refs(self) -> List[_SnapshotRef]:
        res = self._run_git(
            [
                "for-each-ref",
                "--format=%(refname) %(objectname)",
                self.REF_NAMESPACE,
            ],
            check=False,
        )
        if res.returncode != 0:
            return []

        refs: List[_SnapshotRef] = []
        for line in (res.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                refname, commit = line.split(" ", 1)
            except ValueError:
                continue
            batch_id = refname.rsplit("/", 1)[-1]
            ordinal, timestamp = self._parse_batch_id(batch_id)
            refs.append(
                _SnapshotRef(
                    batch_id=batch_id,
                    refname=refname,
                    commit=commit.strip(),
                    ordinal=ordinal,
                    timestamp=timestamp,
                )
            )
        return refs

    def _get_next_ordinal(self) -> int:
        refs = self._get_all_snapshot_refs()
        if not refs:
            return 1
        ords: List[int] = []
        for r in refs:
            try:
                ords.append(int(r.ordinal))
            except ValueError:
                continue
        return (max(ords) + 1) if ords else 1

    def _read_manifest(self, commit_or_ref: str) -> Optional[Dict[str, Any]]:
        res = self._run_git(["show", f"{commit_or_ref}:{self.MANIFEST_PATH}"], check=False)
        if res.returncode != 0:
            return None
        try:
            return json.loads(res.stdout)
        except Exception:
            return None

    def _path_to_repo_rel_posix(self, p: Path) -> Optional[str]:
        """Return repo-relative POSIX path for p, or None if outside git_root."""
        try:
            rel = p.resolve().relative_to(self.git_root)
        except ValueError:
            return None
        return rel.as_posix()

    def _file_mode_str(self, p: Path) -> Optional[str]:
        try:
            st = p.lstat()  # preserve symlink
        except OSError:
            return None

        # symlink
        if p.is_symlink():
            return "120000"

        # executable bit
        if st.st_mode & 0o111:
            return "100755"

        return "100644"

    def _read_file_bytes_for_blob(self, p: Path) -> bytes:
        if p.is_symlink():
            # git stores symlink target path as blob content
            target = os.readlink(p)
            return target.encode("utf-8")
        return p.read_bytes()

    def _hash_object(self, data: bytes, *, env: Dict[str, str]) -> str:
        res = self._run_git(
            ["hash-object", "-w", "--stdin"],
            env=env,
            text=False,
            input_data=data,
        )
        # In binary mode, stdout is bytes
        sha = (
            res.stdout.decode("utf-8", errors="replace").strip()
            if isinstance(res.stdout, (bytes, bytearray))
            else str(res.stdout).strip()
        )
        return sha

    def _update_index_cacheinfo(self, *, mode: str, sha: str, path: str, env: Dict[str, str]) -> None:
        self._run_git(
            ["update-index", "--add", "--cacheinfo", mode, sha, path],
            env=env,
        )

    def _write_tree(self, *, env: Dict[str, str]) -> str:
        res = self._run_git(["write-tree"], env=env)
        return (res.stdout or "").strip()

    def _commit_tree(self, tree_sha: str, *, parent: Optional[str], message: str, env: Dict[str, str]) -> str:
        args = ["commit-tree", tree_sha]
        if parent:
            args.extend(["-p", parent])
        args.extend(["-m", message])
        res = self._run_git(args, env=env)
        return (res.stdout or "").strip()

    # ------------------------------------------------------------
    # SnapshotBackend API
    # ------------------------------------------------------------
    def create_snapshot(self, file_paths: List[Path], prompt: Optional[str] = None) -> str:
        if not file_paths:
            raise ValueError("No files supplied for snapshot")

        ordinal = self._get_next_ordinal()
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        batch_id = f"{ordinal:03d}_{ts}"

        # Build commit message (safe-ish for display; avoid newlines)
        msg_prompt = (prompt or "no prompt").replace("\n", " ")
        message = f"aye: {batch_id} | {msg_prompt}"

        files_manifest: List[Dict[str, Any]] = []

        # IMPORTANT: Use a temp directory and point GIT_INDEX_FILE at a path
        # that does not exist yet, then initialize it via read-tree.
        with tempfile.TemporaryDirectory(prefix="aye-git-index-") as tmpdir:
            tmp_index_path = Path(tmpdir) / "index"
            env = {"GIT_INDEX_FILE": str(tmp_index_path)}

            # Initialize an empty index. This ensures the index file is valid.
            # Without this, a pre-created 0-byte file can cause:
            #   fatal: <path>: index file smaller than expected
            self._run_git(["read-tree", "--empty"], env=env)

            external_counter = 0
            for p in file_paths:
                abs_path = p.resolve()
                repo_rel = self._path_to_repo_rel_posix(abs_path)
                existed = abs_path.exists()

                entry: Dict[str, Any] = {
                    "path": repo_rel,
                    "original": str(abs_path),
                    "existed": bool(existed),
                    "captured": False,
                    "mode": None,
                }

                if existed and (abs_path.is_file() or abs_path.is_symlink()):
                    mode = self._file_mode_str(abs_path)
                    entry["mode"] = mode

                    data = self._read_file_bytes_for_blob(abs_path)
                    blob_sha = self._hash_object(data, env=env)

                    if repo_rel is not None:
                        tree_path = repo_rel
                    else:
                        # "Hybrid-ish" support: store out-of-repo files under __aye__/external/
                        tree_path = f"{self.EXTERNAL_PREFIX}/{external_counter}"
                        entry["snapshot_path"] = tree_path
                        external_counter += 1

                    self._update_index_cacheinfo(mode=mode or "100644", sha=blob_sha, path=tree_path, env=env)
                    entry["captured"] = True

                files_manifest.append(entry)

            manifest: Dict[str, Any] = {
                "version": 1,
                "batch_id": batch_id,
                "ordinal": f"{ordinal:03d}",
                "timestamp": ts,
                "prompt": prompt or None,
                "git_root": str(self.git_root),
                "files": files_manifest,
            }

            manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
            manifest_sha = self._hash_object(manifest_bytes, env=env)
            self._update_index_cacheinfo(mode="100644", sha=manifest_sha, path=self.MANIFEST_PATH, env=env)

            tree_sha = self._write_tree(env=env)
            parent = self._head_commit()
            commit_sha = self._commit_tree(tree_sha, parent=parent, message=message, env=env)

            # Transactional-ish: only create/update the ref as the last step.
            refname = f"{self.REF_NAMESPACE}/{batch_id}"
            self._run_git(["update-ref", refname, commit_sha])

            return batch_id

    def list_snapshots(self, file: Optional[Path] = None) -> Union[List[str], List[Tuple[str, str]]]:
        refs = self._get_all_snapshot_refs()

        # Sort newest first (timestamp desc). Fall back to batch_id.
        refs.sort(key=lambda r: (r.timestamp, r.batch_id), reverse=True)

        if file is None:
            out: List[str] = []
            for r in refs:
                manifest = self._read_manifest(r.commit) or {}
                prompt = manifest.get("prompt")
                prompt_str = self._truncate_prompt(prompt)

                file_entries = manifest.get("files", []) if isinstance(manifest.get("files"), list) else []
                display_files: List[str] = []
                for fe in file_entries[:5]:
                    # Prefer repo-relative path if present, else basename of original.
                    pth = fe.get("path")
                    if pth:
                        display_files.append(str(pth))
                    else:
                        orig = fe.get("original")
                        display_files.append(Path(orig).name if orig else "(unknown)")
                if len(file_entries) > 5:
                    display_files.append(f"...+{len(file_entries) - 5}")

                files_str = ",".join(display_files)
                out.append(f"{r.ordinal}  ({prompt_str})  {files_str}")
            return out

        # File-specific listing: return tuples (batch_id, snapshot_reference)
        file_abs = file.resolve()
        file_repo_rel = self._path_to_repo_rel_posix(file_abs)
        matched: List[Tuple[str, str]] = []

        for r in refs:
            manifest = self._read_manifest(r.commit)
            if not manifest:
                continue
            entries = manifest.get("files")
            if not isinstance(entries, list):
                continue

            for fe in entries:
                # match either by repo path or by absolute "original"
                if file_repo_rel and fe.get("path") == file_repo_rel:
                    matched.append((r.batch_id, r.refname))
                    break
                if str(file_abs) == fe.get("original"):
                    matched.append((r.batch_id, r.refname))
                    break

        # Ensure newest first to match FileBasedBackend behavior.
        matched.sort(key=lambda t: t[0], reverse=True)
        return matched

    def get_file_content_from_snapshot(self, file_path: str, ref_or_commit: str) -> Optional[str]:
        """Return file contents from snapshot commit/ref for an in-repo file path.

        Args:
            file_path: repo-relative POSIX path
            ref_or_commit: commit sha or refname

        Returns:
            file content as text, or None if missing
        """
        try:
            res = self._run_git(["show", f"{ref_or_commit}:{file_path}"], check=False)
            if res.returncode != 0:
                return None
            return res.stdout
        except Exception:
            return None

    def restore_snapshot(self, ordinal: Optional[str] = None, file_name: Optional[str] = None) -> None:
        refs = self._get_all_snapshot_refs()
        if not refs:
            raise ValueError("No snapshots found")

        # Sort newest first.
        refs.sort(key=lambda r: (r.timestamp, r.batch_id), reverse=True)

        # Resolve target snapshot
        target: Optional[_SnapshotRef] = None

        if ordinal is None and file_name is not None:
            # Latest snapshot that contains file
            matches = self.list_snapshots(Path(file_name))
            if not matches:
                raise ValueError(f"No snapshots found for file '{file_name}'")
            batch_id, refname = matches[0]
            target = next((r for r in refs if r.batch_id == batch_id and r.refname == refname), None)
            if target is None:
                # Fallback: match by batch id
                target = next((r for r in refs if r.batch_id == batch_id), None)
        elif ordinal is None:
            target = refs[0]
        else:
            # Normalize numeric ordinals to 3 digits
            normalized = ordinal.zfill(3) if ordinal.isdigit() else ordinal
            target = next((r for r in refs if r.ordinal == normalized), None)

        if target is None:
            raise ValueError(f"Snapshot with Id {ordinal} not found")

        manifest = self._read_manifest(target.commit)
        if not manifest:
            raise ValueError(f"Metadata missing for snapshot {target.ordinal}")

        entries = manifest.get("files")
        if not isinstance(entries, list):
            raise ValueError(f"Invalid metadata for snapshot {target.ordinal}: missing files")

        if file_name is not None:
            target_abs = Path(file_name).resolve()
            target_repo_rel = self._path_to_repo_rel_posix(target_abs)

            filtered = []
            for fe in entries:
                if target_repo_rel and fe.get("path") == target_repo_rel:
                    filtered.append(fe)
                    break
                if str(target_abs) == fe.get("original"):
                    filtered.append(fe)
                    break

            if not filtered:
                # Match FileBasedBackend error semantics
                raise ValueError(f"File '{file_name}' not found in snapshot {target.ordinal}")
            entries = filtered

        for fe in entries:
            original_str = fe.get("original")
            if not original_str:
                continue

            original_path = Path(original_str)
            existed = bool(fe.get("existed"))
            captured = bool(fe.get("captured"))

            if not existed:
                # File did not exist at snapshot time -> delete if it exists now
                try:
                    if original_path.exists() or original_path.is_symlink():
                        # On Windows, attempting to unlink a directory raises PermissionError
                        # (not IsADirectoryError). Detect directory upfront so we can emit the
                        # intended warning consistently across platforms.
                        if original_path.is_dir() and not original_path.is_symlink():
                            print(f"Warning: expected file but found directory  {original_path}")
                        else:
                            original_path.unlink()
                except IsADirectoryError:
                    # If it's a directory now, skip with warning
                    print(f"Warning: expected file but found directory  {original_path}")
                except Exception as e:
                    print(f"Warning: failed to delete {original_path}: {e}")
                continue

            if not captured:
                print(f"Warning: snapshot missing content for {original_path}")
                continue

            repo_rel = fe.get("path")
            snapshot_tree_path = repo_rel or fe.get("snapshot_path")
            if not snapshot_tree_path:
                print(f"Warning: snapshot missing tree path for {original_path}")
                continue

            # Extract bytes from git object
            res = self._run_git(["show", f"{target.commit}:{snapshot_tree_path}"], check=False, text=False)
            if res.returncode != 0:
                print(f"Warning: snapshot blob missing for {original_path}")
                continue

            data = res.stdout if isinstance(res.stdout, (bytes, bytearray)) else bytes(str(res.stdout), "utf-8")

            # Write atomically
            try:
                original_path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    prefix=f".aye-restore-",
                    dir=str(original_path.parent),
                    delete=False,
                ) as tmp:
                    tmp_path = Path(tmp.name)
                    tmp.write(data)
                tmp_path.replace(original_path)
            except Exception as e:
                print(f"Warning: failed to restore {original_path}: {e}")

    def list_all_snapshots(self) -> List[Path]:
        refs = self._get_all_snapshot_refs()
        # Oldest first
        refs.sort(key=lambda r: (r.timestamp, r.batch_id))
        return [Path(r.batch_id) for r in refs]

    def delete_snapshot(self, snapshot_id: Any) -> None:
        batch_id = str(snapshot_id.name if isinstance(snapshot_id, Path) else snapshot_id)
        refname = f"{self.REF_NAMESPACE}/{batch_id}"

        # Validate existence
        refs = {r.refname for r in self._get_all_snapshot_refs()}
        if refname not in refs:
            print(f"Warning: Snapshot {batch_id} not found")
            return

        self._run_git(["update-ref", "-d", refname])
        print(f"Deleted snapshot: {batch_id}")

    def prune_snapshots(self, keep_count: int = 10) -> int:
        refs = self._get_all_snapshot_refs()
        if len(refs) <= keep_count:
            return 0

        # Keep newest
        refs.sort(key=lambda r: (r.timestamp, r.batch_id), reverse=True)
        to_delete = refs[keep_count:] if keep_count > 0 else refs

        deleted = 0
        for r in to_delete:
            self._run_git(["update-ref", "-d", r.refname])
            print(f"Deleted snapshot: {r.batch_id}")
            deleted += 1
        return deleted

    def cleanup_snapshots(self, older_than_days: int = 30) -> int:
        refs = self._get_all_snapshot_refs()
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=older_than_days)

        deleted = 0
        for r in refs:
            try:
                snap_time = datetime.strptime(r.timestamp, "%Y%m%dT%H%M%S")
            except Exception:
                print(f"Warning: Could not parse timestamp from {r.batch_id}")
                continue

            if snap_time < cutoff:
                self._run_git(["update-ref", "-d", r.refname])
                print(f"Deleted snapshot: {r.batch_id}")
                deleted += 1

        return deleted
