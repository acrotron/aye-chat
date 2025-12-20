import json
import os
import tempfile
import unittest
from datetime import datetime as real_datetime, timezone
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

from aye.model.snapshot.git_ref_backend import GitRefBackend, _SnapshotRef


def cp(args: List[str], returncode: int = 0, stdout: Any = "", stderr: Any = "") -> CompletedProcess:
    return CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr=stderr)


class TestGitRefBackendHelpers(unittest.TestCase):
    def test_parse_batch_id_no_timestamp(self):
        b = GitRefBackend(Path("."))
        self.assertEqual(b._parse_batch_id("001"), ("001", ""))

    def test_parse_batch_id_with_timestamp(self):
        b = GitRefBackend(Path("."))
        self.assertEqual(b._parse_batch_id("007_20250101T000000"), ("007", "20250101T000000"))

    def test_truncate_prompt_none_or_blank(self):
        b = GitRefBackend(Path("."))
        self.assertEqual(b._truncate_prompt(None, max_length=8), "no prompt")
        self.assertEqual(b._truncate_prompt("   ", max_length=8), "no prompt")

    def test_truncate_prompt_short_pads(self):
        b = GitRefBackend(Path("."))
        self.assertEqual(b._truncate_prompt("hi", max_length=6), "hi".ljust(6))

    def test_truncate_prompt_long_truncates(self):
        b = GitRefBackend(Path("."))
        s = "x" * 40
        self.assertEqual(b._truncate_prompt(s, max_length=10), ("x" * 10) + "...")


class TestGitRefBackendGitParsing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.backend = GitRefBackend(self.root)

    def test_get_all_snapshot_refs_git_failure_returns_empty(self):
        self.backend._run_git = MagicMock(return_value=cp(["git"], returncode=1, stdout="", stderr="err"))
        self.assertEqual(self.backend._get_all_snapshot_refs(), [])

    def test_get_all_snapshot_refs_parses_lines(self):
        out = (
            "refs/aye/snapshots/001_20250101T000001 abcdef\n"
            "refs/aye/snapshots/002_20250101T000002 012345\n"
            "malformed\n"
            "\n"
        )
        self.backend._run_git = MagicMock(return_value=cp(["git"], stdout=out))
        refs = self.backend._get_all_snapshot_refs()
        self.assertEqual(len(refs), 2)
        self.assertEqual(refs[0].batch_id, "001_20250101T000001")
        self.assertEqual(refs[0].ordinal, "001")
        self.assertEqual(refs[0].timestamp, "20250101T000001")
        self.assertEqual(refs[0].commit, "abcdef")

    def test_get_next_ordinal_ignores_non_int_ordinals(self):
        self.backend._get_all_snapshot_refs = MagicMock(
            return_value=[
                _SnapshotRef(
                    batch_id="aaa_20250101T000000",
                    refname="refs/aye/snapshots/aaa_20250101T000000",
                    commit="c",
                    ordinal="aaa",
                    timestamp="20250101T000000",
                ),
                _SnapshotRef(
                    batch_id="005_20250101T000005",
                    refname="refs/aye/snapshots/005_20250101T000005",
                    commit="c2",
                    ordinal="005",
                    timestamp="20250101T000005",
                ),
            ]
        )
        self.assertEqual(self.backend._get_next_ordinal(), 6)


class TestGitRefBackendManifestAndPaths(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.backend = GitRefBackend(self.root)

    def test_read_manifest_missing_returns_none(self):
        self.backend._run_git = MagicMock(return_value=cp(["git"], returncode=1, stdout=""))
        self.assertIsNone(self.backend._read_manifest("deadbeef"))

    def test_read_manifest_invalid_json_returns_none(self):
        self.backend._run_git = MagicMock(return_value=cp(["git"], returncode=0, stdout="not-json"))
        self.assertIsNone(self.backend._read_manifest("deadbeef"))

    def test_read_manifest_valid_json(self):
        manifest = {"version": 1, "files": []}
        self.backend._run_git = MagicMock(return_value=cp(["git"], returncode=0, stdout=json.dumps(manifest)))
        self.assertEqual(self.backend._read_manifest("deadbeef"), manifest)

    def test_path_to_repo_rel_posix_inside_and_outside(self):
        inside = self.root / "a" / "b.txt"
        inside.parent.mkdir(parents=True, exist_ok=True)
        inside.write_text("x", encoding="utf-8")
        outside_dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(outside_dir, ignore_errors=True))
        outside = outside_dir / "c.txt"
        outside.write_text("y", encoding="utf-8")

        self.assertEqual(self.backend._path_to_repo_rel_posix(inside), "a/b.txt")
        self.assertIsNone(self.backend._path_to_repo_rel_posix(outside))


class TestGitRefBackendCreateSnapshot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.backend = GitRefBackend(self.root)

    def test_create_snapshot_requires_files(self):
        with self.assertRaises(ValueError):
            self.backend.create_snapshot([])

    def test_create_snapshot_builds_manifest_and_updates_ref(self):
        # Arrange a mix: in-repo existing file, out-of-repo existing file, in-repo missing file.
        in_repo = self.root / "in.txt"
        in_repo.write_text("in", encoding="utf-8")

        out_dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(out_dir, ignore_errors=True))
        out_repo = out_dir / "out.txt"
        out_repo.write_text("out", encoding="utf-8")

        missing = self.root / "missing.txt"  # does not exist

        self.backend._get_next_ordinal = MagicMock(return_value=1)
        self.backend._head_commit = MagicMock(return_value=None)
        self.backend._write_tree = MagicMock(return_value="tree123")
        self.backend._commit_tree = MagicMock(return_value="commit123")

        update_index_calls: List[Dict[str, Any]] = []

        def _update_index_cacheinfo(*, mode: str, sha: str, path: str, env: Dict[str, str]) -> None:
            update_index_calls.append({"mode": mode, "sha": sha, "path": path, "env": dict(env)})

        self.backend._update_index_cacheinfo = MagicMock(side_effect=_update_index_cacheinfo)

        # Capture manifest JSON passed to hash_object
        seen_manifest: Dict[str, Any] = {}
        sha_counter = {"n": 0}

        def _hash_object(data: bytes, *, env: Dict[str, str]) -> str:
            sha_counter["n"] += 1
            # Detect the manifest by parsing JSON
            try:
                maybe = json.loads(data.decode("utf-8"))
                if isinstance(maybe, dict) and maybe.get("version") == 1 and "files" in maybe:
                    seen_manifest.clear()
                    seen_manifest.update(maybe)
            except Exception:
                pass
            return f"sha{sha_counter['n']:03d}"

        self.backend._hash_object = MagicMock(side_effect=_hash_object)

        # Track _run_git calls (create_snapshot now calls read-tree --empty AND update-ref)
        run_git_calls: List[List[str]] = []

        def _run_git(args: List[str], **kwargs):
            run_git_calls.append(args)
            return cp(["git"] + args, returncode=0, stdout="")

        self.backend._run_git = MagicMock(side_effect=_run_git)

        # Act
        batch_id = self.backend.create_snapshot([in_repo, out_repo, missing], prompt="hello")

        # Assert: returned batch id starts with ordinal
        self.assertTrue(batch_id.startswith("001_"))

        # create_snapshot initializes the temp index with read-tree --empty
        self.assertGreaterEqual(len(run_git_calls), 2)
        self.assertEqual(run_git_calls[0], ["read-tree", "--empty"])

        # update-ref should be invoked last with correct namespace + commit
        self.assertEqual(run_git_calls[-1][0:2], ["update-ref", f"refs/aye/snapshots/{batch_id}"])
        self.assertEqual(run_git_calls[-1][2], "commit123")

        # Manifest checks
        self.assertEqual(seen_manifest.get("version"), 1)
        self.assertEqual(seen_manifest.get("batch_id"), batch_id)
        self.assertEqual(seen_manifest.get("ordinal"), "001")
        self.assertEqual(seen_manifest.get("prompt"), "hello")
        files = seen_manifest.get("files")
        self.assertIsInstance(files, list)
        self.assertEqual(len(files), 3)

        # in-repo file entry
        in_entry = next(e for e in files if e.get("original") == str(in_repo.resolve()))
        self.assertEqual(in_entry.get("path"), "in.txt")
        self.assertTrue(in_entry.get("existed"))
        self.assertTrue(in_entry.get("captured"))

        # out-of-repo file entry uses snapshot_path
        out_entry = next(e for e in files if e.get("original") == str(out_repo.resolve()))
        self.assertIsNone(out_entry.get("path"))
        self.assertTrue(out_entry.get("existed"))
        self.assertTrue(out_entry.get("captured"))
        self.assertTrue(str(out_entry.get("snapshot_path")).startswith("__aye__/external/"))

        # missing file entry
        miss_entry = next(e for e in files if e.get("original") == str(missing.resolve()))
        self.assertEqual(miss_entry.get("path"), "missing.txt")
        self.assertFalse(miss_entry.get("existed"))
        self.assertFalse(miss_entry.get("captured"))

        # update-index should be called for each captured file + manifest
        # captured: in_repo + out_repo + manifest = 3
        self.assertEqual(len(update_index_calls), 3)
        index_paths = {c["path"] for c in update_index_calls}
        self.assertIn("in.txt", index_paths)
        self.assertIn("__aye__/manifest.json", index_paths)
        self.assertTrue(any(p.startswith("__aye__/external/") for p in index_paths))


class TestGitRefBackendListSnapshots(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.backend = GitRefBackend(self.root)

    def test_list_snapshots_formats_output(self):
        refs = [
            _SnapshotRef(
                batch_id="001_20250101T000001",
                refname="refs/aye/snapshots/001_20250101T000001",
                commit="c1",
                ordinal="001",
                timestamp="20250101T000001",
            )
        ]
        self.backend._get_all_snapshot_refs = MagicMock(return_value=refs)
        self.backend._read_manifest = MagicMock(
            return_value={
                "prompt": "this is a very long prompt that should be truncated beyond 32 chars",
                "files": [
                    {"path": "a.txt"},
                    {"path": "b.txt"},
                    {"path": "c.txt"},
                    {"path": "d.txt"},
                    {"path": "e.txt"},
                    {"path": "f.txt"},
                ],
            }
        )

        out = self.backend.list_snapshots()
        self.assertEqual(len(out), 1)
        # Ordinal at start
        self.assertTrue(out[0].startswith("001"))
        # Shows first 5 files plus ...+1
        self.assertIn("a.txt,b.txt,c.txt,d.txt,e.txt,...+1", out[0])
        # Prompt truncated ends with ...
        self.assertIn("...", out[0])

    def test_list_snapshots_handles_invalid_files_field(self):
        refs = [
            _SnapshotRef(
                batch_id="010_20250101T000001",
                refname="refs/aye/snapshots/010_20250101T000001",
                commit="c1",
                ordinal="010",
                timestamp="20250101T000001",
            )
        ]
        self.backend._get_all_snapshot_refs = MagicMock(return_value=refs)
        self.backend._read_manifest = MagicMock(return_value={"prompt": "p", "files": "not-a-list"})

        out = self.backend.list_snapshots()
        self.assertEqual(len(out), 1)
        self.assertIn("010", out[0])

    def test_list_snapshots_file_specific_matches_by_repo_path_and_is_newest_first(self):
        refs = [
            _SnapshotRef(
                batch_id="001_20250101T000001",
                refname="refs/aye/snapshots/001_20250101T000001",
                commit="c1",
                ordinal="001",
                timestamp="20250101T000001",
            ),
            _SnapshotRef(
                batch_id="002_20250101T000002",
                refname="refs/aye/snapshots/002_20250101T000002",
                commit="c2",
                ordinal="002",
                timestamp="20250101T000002",
            ),
        ]
        self.backend._get_all_snapshot_refs = MagicMock(return_value=refs)

        def _read_manifest(commit: str) -> Optional[Dict[str, Any]]:
            if commit == "c1":
                return {"files": [{"path": "dir/x.txt", "original": str(self.root / "dir" / "x.txt")}]}  # noqa: E501
            if commit == "c2":
                return {"files": [{"path": "dir/x.txt", "original": str(self.root / "dir" / "x.txt")}]}  # noqa: E501
            return None

        self.backend._read_manifest = MagicMock(side_effect=_read_manifest)

        # Create a real file path inside repo for matching
        f = self.root / "dir" / "x.txt"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x", encoding="utf-8")

        matched = self.backend.list_snapshots(file=f)
        self.assertEqual(
            matched,
            [
                ("002_20250101T000002", "refs/aye/snapshots/002_20250101T000002"),
                ("001_20250101T000001", "refs/aye/snapshots/001_20250101T000001"),
            ],
        )

    def test_list_snapshots_file_specific_skips_invalid_manifest(self):
        refs = [
            _SnapshotRef(
                batch_id="001_20250101T000001",
                refname="refs/aye/snapshots/001_20250101T000001",
                commit="c1",
                ordinal="001",
                timestamp="20250101T000001",
            )
        ]
        self.backend._get_all_snapshot_refs = MagicMock(return_value=refs)
        self.backend._read_manifest = MagicMock(return_value={"files": "bad"})

        f = self.root / "x.txt"
        f.write_text("x", encoding="utf-8")

        self.assertEqual(self.backend.list_snapshots(file=f), [])


class TestGitRefBackendRestoreSnapshot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.backend = GitRefBackend(self.root)

    def test_restore_snapshot_no_snapshots(self):
        self.backend._get_all_snapshot_refs = MagicMock(return_value=[])
        with self.assertRaises(ValueError):
            self.backend.restore_snapshot()

    def test_restore_snapshot_ordinal_normalizes_numeric(self):
        target_file = self.root / "a.txt"

        refs = [
            _SnapshotRef(
                batch_id="001_20250101T000001",
                refname="refs/aye/snapshots/001_20250101T000001",
                commit="c1",
                ordinal="001",
                timestamp="20250101T000001",
            )
        ]
        self.backend._get_all_snapshot_refs = MagicMock(return_value=refs)
        self.backend._read_manifest = MagicMock(
            return_value={
                "files": [
                    {
                        "path": "a.txt",
                        "original": str(target_file),
                        "existed": True,
                        "captured": True,
                    }
                ]
            }
        )

        def _run_git(args: List[str], **kwargs):
            if args[:1] == ["show"]:
                return cp(["git"] + args, returncode=0, stdout=b"hello")
            return cp(["git"] + args, returncode=0, stdout="")

        self.backend._run_git = MagicMock(side_effect=_run_git)

        self.backend.restore_snapshot("1")
        self.assertTrue(target_file.exists())
        self.assertEqual(target_file.read_bytes(), b"hello")

    def test_restore_snapshot_missing_manifest_raises(self):
        refs = [
            _SnapshotRef(
                batch_id="001_20250101T000001",
                refname="refs/aye/snapshots/001_20250101T000001",
                commit="c1",
                ordinal="001",
                timestamp="20250101T000001",
            )
        ]
        self.backend._get_all_snapshot_refs = MagicMock(return_value=refs)
        self.backend._read_manifest = MagicMock(return_value=None)

        with self.assertRaises(ValueError):
            self.backend.restore_snapshot("001")

    def test_restore_snapshot_invalid_manifest_files_raises(self):
        refs = [
            _SnapshotRef(
                batch_id="001_20250101T000001",
                refname="refs/aye/snapshots/001_20250101T000001",
                commit="c1",
                ordinal="001",
                timestamp="20250101T000001",
            )
        ]
        self.backend._get_all_snapshot_refs = MagicMock(return_value=refs)
        self.backend._read_manifest = MagicMock(return_value={"files": "nope"})

        with self.assertRaises(ValueError):
            self.backend.restore_snapshot("001")

    def test_restore_snapshot_writes_file_bytes(self):
        target_file = self.root / "a.txt"

        refs = [
            _SnapshotRef(
                batch_id="001_20250101T000001",
                refname="refs/aye/snapshots/001_20250101T000001",
                commit="c1",
                ordinal="001",
                timestamp="20250101T000001",
            )
        ]
        self.backend._get_all_snapshot_refs = MagicMock(return_value=refs)
        self.backend._read_manifest = MagicMock(
            return_value={
                "files": [
                    {
                        "path": "a.txt",
                        "original": str(target_file),
                        "existed": True,
                        "captured": True,
                    }
                ]
            }
        )

        def _run_git(args: List[str], **kwargs):
            # show <commit>:<path> returns bytes
            if args[:1] == ["show"]:
                return cp(["git"] + args, returncode=0, stdout=b"hello")
            return cp(["git"] + args, returncode=0, stdout="")

        self.backend._run_git = MagicMock(side_effect=_run_git)

        self.backend.restore_snapshot("001")
        self.assertTrue(target_file.exists())
        self.assertEqual(target_file.read_bytes(), b"hello")

    def test_restore_snapshot_warns_and_skips_when_captured_false(self):
        target_file = self.root / "a.txt"
        target_file.write_text("existing", encoding="utf-8")

        refs = [
            _SnapshotRef(
                batch_id="001_20250101T000001",
                refname="refs/aye/snapshots/001_20250101T000001",
                commit="c1",
                ordinal="001",
                timestamp="20250101T000001",
            )
        ]
        self.backend._get_all_snapshot_refs = MagicMock(return_value=refs)
        self.backend._read_manifest = MagicMock(
            return_value={
                "files": [
                    {
                        "path": "a.txt",
                        "original": str(target_file),
                        "existed": True,
                        "captured": False,
                    }
                ]
            }
        )
        self.backend._run_git = MagicMock()

        with patch("builtins.print") as p:
            self.backend.restore_snapshot("001")

        self.backend._run_git.assert_not_called()
        self.assertTrue(any("missing content" in str(c.args[0]) for c in p.mock_calls if c.args))

    def test_restore_snapshot_warns_and_skips_when_tree_path_missing(self):
        target_file = self.root / "a.txt"

        refs = [
            _SnapshotRef(
                batch_id="001_20250101T000001",
                refname="refs/aye/snapshots/001_20250101T000001",
                commit="c1",
                ordinal="001",
                timestamp="20250101T000001",
            )
        ]
        self.backend._get_all_snapshot_refs = MagicMock(return_value=refs)
        self.backend._read_manifest = MagicMock(
            return_value={
                "files": [
                    {
                        "path": None,
                        "snapshot_path": None,
                        "original": str(target_file),
                        "existed": True,
                        "captured": True,
                    }
                ]
            }
        )
        self.backend._run_git = MagicMock()

        with patch("builtins.print") as p:
            self.backend.restore_snapshot("001")

        self.backend._run_git.assert_not_called()
        self.assertTrue(any("missing tree path" in str(c.args[0]) for c in p.mock_calls if c.args))

    def test_restore_snapshot_warns_when_blob_missing(self):
        target_file = self.root / "a.txt"

        refs = [
            _SnapshotRef(
                batch_id="001_20250101T000001",
                refname="refs/aye/snapshots/001_20250101T000001",
                commit="c1",
                ordinal="001",
                timestamp="20250101T000001",
            )
        ]
        self.backend._get_all_snapshot_refs = MagicMock(return_value=refs)
        self.backend._read_manifest = MagicMock(
            return_value={
                "files": [
                    {
                        "path": "a.txt",
                        "original": str(target_file),
                        "existed": True,
                        "captured": True,
                    }
                ]
            }
        )

        self.backend._run_git = MagicMock(return_value=cp(["git", "show"], returncode=1, stdout=b"", stderr=b"no"))

        with patch("builtins.print") as p:
            self.backend.restore_snapshot("001")

        self.assertTrue(any("blob missing" in str(c.args[0]) for c in p.mock_calls if c.args))

    def test_restore_snapshot_deletes_file_that_did_not_exist(self):
        target_file = self.root / "gone.txt"
        target_file.write_text("present", encoding="utf-8")
        self.assertTrue(target_file.exists())

        refs = [
            _SnapshotRef(
                batch_id="001_20250101T000001",
                refname="refs/aye/snapshots/001_20250101T000001",
                commit="c1",
                ordinal="001",
                timestamp="20250101T000001",
            )
        ]
        self.backend._get_all_snapshot_refs = MagicMock(return_value=refs)
        self.backend._read_manifest = MagicMock(
            return_value={
                "files": [
                    {
                        "path": "gone.txt",
                        "original": str(target_file),
                        "existed": False,
                        "captured": False,
                    }
                ]
            }
        )
        self.backend._run_git = MagicMock(return_value=cp(["git"], returncode=0, stdout=b""))

        self.backend.restore_snapshot("001")
        self.assertFalse(target_file.exists())

    def test_restore_snapshot_delete_branch_handles_directory(self):
        dir_path = self.root / "now_a_dir"
        dir_path.mkdir(parents=True, exist_ok=True)

        refs = [
            _SnapshotRef(
                batch_id="001_20250101T000001",
                refname="refs/aye/snapshots/001_20250101T000001",
                commit="c1",
                ordinal="001",
                timestamp="20250101T000001",
            )
        ]
        self.backend._get_all_snapshot_refs = MagicMock(return_value=refs)
        self.backend._read_manifest = MagicMock(
            return_value={
                "files": [
                    {
                        "path": "now_a_dir",
                        "original": str(dir_path),
                        "existed": False,
                        "captured": False,
                    }
                ]
            }
        )

        with patch("builtins.print") as p:
            self.backend.restore_snapshot("001")

        self.assertTrue(dir_path.exists())
        self.assertTrue(any("found directory" in str(c.args[0]) for c in p.mock_calls if c.args))

    def test_restore_snapshot_file_filter_not_found_raises(self):
        refs = [
            _SnapshotRef(
                batch_id="001_20250101T000001",
                refname="refs/aye/snapshots/001_20250101T000001",
                commit="c1",
                ordinal="001",
                timestamp="20250101T000001",
            )
        ]
        self.backend._get_all_snapshot_refs = MagicMock(return_value=refs)
        self.backend._read_manifest = MagicMock(return_value={"files": []})

        with self.assertRaises(ValueError):
            self.backend.restore_snapshot("001", file_name=str(self.root / "nope.txt"))

    def test_restore_snapshot_latest_by_file_uses_list_snapshots_and_errors_when_none(self):
        refs = [
            _SnapshotRef(
                batch_id="002_20250101T000002",
                refname="refs/aye/snapshots/002_20250101T000002",
                commit="c2",
                ordinal="002",
                timestamp="20250101T000002",
            )
        ]
        self.backend._get_all_snapshot_refs = MagicMock(return_value=refs)
        self.backend.list_snapshots = MagicMock(return_value=[])

        with self.assertRaises(ValueError):
            self.backend.restore_snapshot(ordinal=None, file_name=str(self.root / "x.txt"))

    def test_restore_snapshot_latest_by_file_falls_back_to_batch_id_match(self):
        target_file = self.root / "a.txt"

        refs = [
            _SnapshotRef(
                batch_id="002_20250101T000002",
                refname="refs/aye/snapshots/002_20250101T000002",
                commit="c2",
                ordinal="002",
                timestamp="20250101T000002",
            )
        ]
        self.backend._get_all_snapshot_refs = MagicMock(return_value=refs)
        # refname mismatch triggers fallback-by-batch_id
        self.backend.list_snapshots = MagicMock(return_value=[("002_20250101T000002", "refs/aye/snapshots/DIFFERENT")])
        self.backend._read_manifest = MagicMock(
            return_value={
                "files": [
                    {
                        "path": "a.txt",
                        "original": str(target_file),
                        "existed": True,
                        "captured": True,
                    }
                ]
            }
        )

        def _run_git(args: List[str], **kwargs):
            if args[:1] == ["show"]:
                return cp(["git"] + args, returncode=0, stdout=b"hello")
            return cp(["git"] + args, returncode=0, stdout="")

        self.backend._run_git = MagicMock(side_effect=_run_git)

        self.backend.restore_snapshot(ordinal=None, file_name=str(target_file))
        self.assertEqual(target_file.read_bytes(), b"hello")


class TestGitRefBackendDeletePruneCleanup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.backend = GitRefBackend(self.root)

    def test_delete_snapshot_not_found_prints_warning(self):
        self.backend._get_all_snapshot_refs = MagicMock(return_value=[])
        self.backend._run_git = MagicMock()
        with patch("builtins.print") as p:
            self.backend.delete_snapshot("001_20250101T000001")
            self.backend._run_git.assert_not_called()
            self.assertTrue(any("not found" in str(call.args[0]) for call in p.mock_calls if call.args))

    def test_delete_snapshot_deletes_ref(self):
        ref = _SnapshotRef(
            batch_id="001_20250101T000001",
            refname="refs/aye/snapshots/001_20250101T000001",
            commit="c1",
            ordinal="001",
            timestamp="20250101T000001",
        )
        self.backend._get_all_snapshot_refs = MagicMock(return_value=[ref])
        self.backend._run_git = MagicMock(return_value=cp(["git"], stdout=""))
        with patch("builtins.print") as p:
            self.backend.delete_snapshot("001_20250101T000001")
            self.backend._run_git.assert_called_once_with(["update-ref", "-d", ref.refname])
            self.assertTrue(any("Deleted snapshot" in str(call.args[0]) for call in p.mock_calls if call.args))

    def test_delete_snapshot_accepts_path_object(self):
        ref = _SnapshotRef(
            batch_id="001_20250101T000001",
            refname="refs/aye/snapshots/001_20250101T000001",
            commit="c1",
            ordinal="001",
            timestamp="20250101T000001",
        )
        self.backend._get_all_snapshot_refs = MagicMock(return_value=[ref])
        self.backend._run_git = MagicMock(return_value=cp(["git"], stdout=""))

        with patch("builtins.print"):
            self.backend.delete_snapshot(Path("001_20250101T000001"))

        self.backend._run_git.assert_called_once_with(["update-ref", "-d", ref.refname])

    def test_prune_snapshots_deletes_older(self):
        refs = [
            _SnapshotRef(
                batch_id="001_20240101T000001",
                refname="refs/aye/snapshots/001_20240101T000001",
                commit="c1",
                ordinal="001",
                timestamp="20240101T000001",
            ),
            _SnapshotRef(
                batch_id="002_20240101T000002",
                refname="refs/aye/snapshots/002_20240101T000002",
                commit="c2",
                ordinal="002",
                timestamp="20240101T000002",
            ),
            _SnapshotRef(
                batch_id="003_20240101T000003",
                refname="refs/aye/snapshots/003_20240101T000003",
                commit="c3",
                ordinal="003",
                timestamp="20240101T000003",
            ),
        ]
        self.backend._get_all_snapshot_refs = MagicMock(return_value=refs)
        self.backend._run_git = MagicMock(return_value=cp(["git"], stdout=""))
        with patch("builtins.print"):
            deleted = self.backend.prune_snapshots(keep_count=1)

        self.assertEqual(deleted, 2)
        calls = [c.args[0] for c in self.backend._run_git.mock_calls]
        self.assertIn(["update-ref", "-d", "refs/aye/snapshots/002_20240101T000002"], calls)
        self.assertIn(["update-ref", "-d", "refs/aye/snapshots/001_20240101T000001"], calls)

    def test_prune_snapshots_keep_count_ge_len_returns_zero(self):
        refs = [
            _SnapshotRef(
                batch_id="001_20240101T000001",
                refname="refs/aye/snapshots/001_20240101T000001",
                commit="c1",
                ordinal="001",
                timestamp="20240101T000001",
            )
        ]
        self.backend._get_all_snapshot_refs = MagicMock(return_value=refs)
        self.backend._run_git = MagicMock()
        self.assertEqual(self.backend.prune_snapshots(keep_count=10), 0)
        self.backend._run_git.assert_not_called()

    def test_prune_snapshots_keep_count_zero_deletes_all(self):
        refs = [
            _SnapshotRef(
                batch_id="001_20240101T000001",
                refname="refs/aye/snapshots/001_20240101T000001",
                commit="c1",
                ordinal="001",
                timestamp="20240101T000001",
            ),
            _SnapshotRef(
                batch_id="002_20240101T000002",
                refname="refs/aye/snapshots/002_20240101T000002",
                commit="c2",
                ordinal="002",
                timestamp="20240101T000002",
            ),
        ]
        self.backend._get_all_snapshot_refs = MagicMock(return_value=refs)
        self.backend._run_git = MagicMock(return_value=cp(["git"], stdout=""))

        with patch("builtins.print"):
            deleted = self.backend.prune_snapshots(keep_count=0)

        self.assertEqual(deleted, 2)

    def test_cleanup_snapshots_deletes_older_than_cutoff(self):
        refs = [
            _SnapshotRef(
                batch_id="001_20200101T000001",
                refname="refs/aye/snapshots/001_20200101T000001",
                commit="c1",
                ordinal="001",
                timestamp="20200101T000001",
            ),
            _SnapshotRef(
                batch_id="002_20250115T000001",
                refname="refs/aye/snapshots/002_20250115T000001",
                commit="c2",
                ordinal="002",
                timestamp="20250115T000001",
            ),
        ]
        self.backend._get_all_snapshot_refs = MagicMock(return_value=refs)
        self.backend._run_git = MagicMock(return_value=cp(["git"], stdout=""))

        fixed_now = real_datetime(2025, 2, 1, 0, 0, 0, tzinfo=timezone.utc)

        class FakeDateTime:
            @staticmethod
            def now(tz=None):
                return fixed_now

            @staticmethod
            def strptime(s: str, fmt: str):
                return real_datetime.strptime(s, fmt)

        with patch("aye.model.snapshot.git_ref_backend.datetime", FakeDateTime), patch("builtins.print"):
            deleted = self.backend.cleanup_snapshots(older_than_days=30)

        self.assertEqual(deleted, 1)
        self.backend._run_git.assert_any_call(["update-ref", "-d", "refs/aye/snapshots/001_20200101T000001"])
        deleted_calls = [c.args[0] for c in self.backend._run_git.mock_calls]
        self.assertNotIn(["update-ref", "-d", "refs/aye/snapshots/002_20250115T000001"], deleted_calls)

    def test_cleanup_snapshots_handles_unparseable_timestamp(self):
        refs = [
            _SnapshotRef(
                batch_id="001_BAD",
                refname="refs/aye/snapshots/001_BAD",
                commit="c1",
                ordinal="001",
                timestamp="BAD",
            )
        ]
        self.backend._get_all_snapshot_refs = MagicMock(return_value=refs)
        self.backend._run_git = MagicMock()

        with patch("builtins.print") as p:
            deleted = self.backend.cleanup_snapshots(older_than_days=30)

        self.assertEqual(deleted, 0)
        self.backend._run_git.assert_not_called()
        self.assertTrue(any("Could not parse timestamp" in str(c.args[0]) for c in p.mock_calls if c.args))


class TestGitRefBackendAdditionalCoverage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.backend = GitRefBackend(self.root)

    def test_run_git_raises_runtimeerror_when_check_true(self):
        with patch("aye.model.snapshot.git_ref_backend.subprocess.run") as run:
            run.return_value = cp(["git", "status"], returncode=1, stdout="", stderr="oops")
            with self.assertRaises(RuntimeError) as ctx:
                self.backend._run_git(["status"], check=True)
            self.assertIn("Git command failed", str(ctx.exception))
            self.assertIn("git status", str(ctx.exception))

    def test_head_commit_returns_none_when_rev_parse_fails_or_empty(self):
        self.backend._run_git = MagicMock(return_value=cp(["git", "rev-parse"], returncode=1, stdout="", stderr=""))
        self.assertIsNone(self.backend._head_commit())

        self.backend._run_git = MagicMock(return_value=cp(["git", "rev-parse"], returncode=0, stdout="\n", stderr=""))
        self.assertIsNone(self.backend._head_commit())

    def test_file_mode_str_regular_and_executable(self):
        f = self.root / "a.sh"
        f.write_text("echo hi\n", encoding="utf-8")

        self.assertEqual(self.backend._file_mode_str(f), "100644")

        os.chmod(f, 0o755)

        # Windows doesn't reliably expose POSIX executable bits via chmod/stat.
        # On POSIX, this must become 100755.
        if os.name == "nt":
            self.assertEqual(self.backend._file_mode_str(f), "100644")
        else:
            self.assertEqual(self.backend._file_mode_str(f), "100755")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink not supported")
    def test_file_mode_str_and_blob_bytes_for_symlink(self):
        target = self.root / "target.txt"
        target.write_text("content", encoding="utf-8")
        link = self.root / "link.txt"
        try:
            os.symlink(str(target), str(link))
        except OSError:
            self.skipTest("symlink creation not permitted")

        self.assertTrue(link.is_symlink())
        self.assertEqual(self.backend._file_mode_str(link), "120000")

        b = self.backend._read_file_bytes_for_blob(link)
        self.assertEqual(b, os.readlink(link).encode("utf-8"))

    def test_file_mode_str_missing_returns_none(self):
        missing = self.root / "missing.txt"
        self.assertIsNone(self.backend._file_mode_str(missing))

    def test_hash_object_decodes_bytes_stdout(self):
        self.backend._run_git = MagicMock(return_value=cp(["git", "hash-object"], returncode=0, stdout=b"abc123\n"))
        sha = self.backend._hash_object(b"x", env={"GIT_INDEX_FILE": "x"})
        self.assertEqual(sha, "abc123")

    def test_get_file_content_from_snapshot_success_missing_and_exception(self):
        self.backend._run_git = MagicMock(return_value=cp(["git", "show"], returncode=0, stdout="hi"))
        self.assertEqual(self.backend.get_file_content_from_snapshot("a.txt", "c"), "hi")

        self.backend._run_git = MagicMock(return_value=cp(["git", "show"], returncode=1, stdout="", stderr="no"))
        self.assertIsNone(self.backend.get_file_content_from_snapshot("a.txt", "c"))

        self.backend._run_git = MagicMock(side_effect=RuntimeError("boom"))
        self.assertIsNone(self.backend.get_file_content_from_snapshot("a.txt", "c"))

    def test_list_all_snapshots_oldest_first(self):
        refs = [
            _SnapshotRef(
                batch_id="002_20250101T000002",
                refname="refs/aye/snapshots/002_20250101T000002",
                commit="c2",
                ordinal="002",
                timestamp="20250101T000002",
            ),
            _SnapshotRef(
                batch_id="001_20250101T000001",
                refname="refs/aye/snapshots/001_20250101T000001",
                commit="c1",
                ordinal="001",
                timestamp="20250101T000001",
            ),
        ]
        self.backend._get_all_snapshot_refs = MagicMock(return_value=refs)

        out = self.backend.list_all_snapshots()
        self.assertEqual(out, [Path("001_20250101T000001"), Path("002_20250101T000002")])
