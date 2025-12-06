import os
import json
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch, MagicMock, call
import pytest
import tempfile
import re

import aye.model.snapshot as snapshot


class TestSnapshot(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.snap_root_val = Path(self.tmpdir.name) / "snapshots"
        self.snap_root_val.mkdir(parents=True, exist_ok=True)  # Ensure parent exists
        self.latest_dir_val = self.snap_root_val / "latest"
        self.test_dir = Path(self.tmpdir.name) / "src"
        self.test_dir.mkdir()

        # Patch the constants in the snapshot module (now in file_backend.py)
        self.snap_root_patcher = patch('aye.model.snapshot.file_backend.SNAP_ROOT', self.snap_root_val)
        self.latest_dir_patcher = patch('aye.model.snapshot.file_backend.LATEST_SNAP_DIR', self.latest_dir_val)
        # Force FileBasedBackend by making git detection return None
        self.git_repo_patcher = patch('aye.model.snapshot._is_git_repository', return_value=None)
        self.snap_root_patcher.start()
        self.latest_dir_patcher.start()
        self.git_repo_patcher.start()

        # Reset the backend singleton to pick up the patched values
        snapshot.reset_backend()

        self.test_files = [
            self.test_dir / "test1.py",
            self.test_dir / "test2.py"
        ]

        # Create test files
        for f in self.test_files:
            f.write_text("test content")

    def tearDown(self):
        self.snap_root_patcher.stop()
        self.latest_dir_patcher.stop()
        self.git_repo_patcher.stop()
        # Reset the backend singleton after tests
        snapshot.reset_backend()
        self.tmpdir.cleanup()

    def test_truncate_prompt(self):
        self.assertEqual(snapshot._truncate_prompt("short prompt"), "short prompt".ljust(32))
        self.assertEqual(snapshot._truncate_prompt("a" * 40), "a" * 32 + "...")
        self.assertEqual(snapshot._truncate_prompt(None), "no prompt".ljust(32))
        self.assertEqual(snapshot._truncate_prompt("  "), "no prompt".ljust(32))

    def test_create_snapshot(self):
        with patch('aye.model.snapshot._get_next_ordinal', return_value=1):
            batch_name = snapshot.create_snapshot(self.test_files, prompt="test prompt")

        self.assertTrue(batch_name.startswith("001_"))
        self.assertTrue(self.snap_root_val.exists())
        batch_dir = self.snap_root_val / batch_name
        self.assertTrue(batch_dir.is_dir())
        
        # Check if files were copied
        self.assertTrue((batch_dir / "test1.py").exists())
        self.assertTrue((batch_dir / "test2.py").exists())
        
        # Check metadata
        meta_path = batch_dir / "metadata.json"
        self.assertTrue(meta_path.exists())
        meta = json.loads(meta_path.read_text())
        self.assertEqual(meta['prompt'], "test prompt")
        self.assertEqual(len(meta['files']), 2)

    def test_create_snapshot_no_files(self):
        with self.assertRaisesRegex(ValueError, "No files supplied for snapshot"):
            snapshot.create_snapshot([])

    def test_create_snapshot_with_nonexistent_file(self):
        non_existent_file = self.test_dir / "non_existent.py"
        self.assertFalse(non_existent_file.exists())
        
        with patch('aye.model.snapshot._get_next_ordinal', return_value=1):
            batch_name = snapshot.create_snapshot([non_existent_file])
        
        batch_dir = self.snap_root_val / batch_name
        snapshot_file = batch_dir / "non_existent.py"
        self.assertTrue(snapshot_file.exists())
        self.assertEqual(snapshot_file.read_text(), "")

    def test_get_next_ordinal_returns_one_when_root_missing(self):
        shutil.rmtree(self.snap_root_val)
        self.assertEqual(snapshot._get_next_ordinal(), 1)

    def test_get_next_ordinal_ignores_malformed_directories(self):
        (self.snap_root_val / "badname").mkdir()
        (self.snap_root_val / "00x_broken").mkdir()
        (self.snap_root_val / "004_valid").mkdir()
        self.assertEqual(snapshot._get_next_ordinal(), 5)

    def test_get_latest_snapshot_dir_returns_most_recent(self):
        ts_old = (datetime.now(timezone.utc) - timedelta(minutes=2)).strftime("%Y%m%dT%H%M%S")
        ts_new = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        (self.snap_root_val / f"001_{ts_old}").mkdir()
        (self.snap_root_val / f"002_{ts_new}").mkdir()

        latest = snapshot._get_latest_snapshot_dir()
        self.assertIsNotNone(latest)
        self.assertEqual(latest.name, f"002_{ts_new}")

    def test_get_latest_snapshot_dir_returns_none_when_root_missing(self):
        shutil.rmtree(self.snap_root_val)
        self.assertIsNone(snapshot._get_latest_snapshot_dir())

    def test_get_latest_snapshot_dir_skips_invalid_batches(self):
        (self.snap_root_val / "invalid_dir").mkdir()
        (self.snap_root_val / "abc_bad").mkdir()
        self.assertIsNone(snapshot._get_latest_snapshot_dir())

    def test_list_snapshots_without_snapshot_root(self):
        shutil.rmtree(self.snap_root_val)
        self.assertEqual(snapshot.list_snapshots(), [])
        self.assertEqual(snapshot.list_snapshots(self.test_files[0]), [])

    def test_list_snapshots_reports_missing_metadata(self):
        ts = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y%m%dT%H%M%S")
        (self.snap_root_val / f"001_{ts}").mkdir()

        entries = snapshot.list_snapshots()
        self.assertEqual(len(entries), 1)
        self.assertIn("metadata missing", entries[0])

    def test_list_all_snapshots_with_metadata_handles_relative_and_external_paths(self):
        ts_new = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        ts_old = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y%m%dT%H%M%S")
        meta_dir = self.snap_root_val / f"002_{ts_new}"
        meta_dir.mkdir(parents=True)
        missing_dir = self.snap_root_val / f"001_{ts_old}"
        missing_dir.mkdir()

        relative_tmp = tempfile.NamedTemporaryFile(dir=Path.cwd(), delete=False)
        relative_path = Path(relative_tmp.name)
        relative_tmp.close()
        external_tmp = tempfile.NamedTemporaryFile(dir=self.tmpdir.name, delete=False)
        external_path = Path(external_tmp.name)
        external_tmp.close()

        meta = {
            "timestamp": ts_new,
            "prompt": "list metadata",
            "files": [
                {"original": str(relative_path), "snapshot": str(meta_dir / relative_path.name)},
                {"original": str(external_path), "snapshot": str(meta_dir / external_path.name)}
            ]
        }
        (meta_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")

        try:
            results = snapshot._list_all_snapshots_with_metadata()
            self.assertEqual(len(results), 2)
            relative_segment = str(relative_path.relative_to(Path.cwd()))
            self.assertIn(relative_segment, results[0])
            self.assertIn(str(external_path), results[0])
            self.assertIn("metadata missing", results[1])
        finally:
            for cleanup_path in (relative_path, external_path):
                try:
                    cleanup_path.unlink()
                except FileNotFoundError:
                    pass

    def test_list_snapshots(self):
        # Create mock snapshot dirs
        ts1 = (datetime.now(timezone.utc) - timedelta(minutes=2)).strftime("%Y%m%dT%H%M%S")
        ts2 = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y%m%dT%H%M%S")
        snap_dir1 = self.snap_root_val / f"001_{ts1}"
        snap_dir2 = self.snap_root_val / f"002_{ts2}"
        snap_dir1.mkdir(parents=True)
        snap_dir2.mkdir(parents=True)
        
        # Mock metadata files
        (snap_dir1 / "metadata.json").write_text(json.dumps({
            "timestamp": ts1, "prompt": "prompt1",
            "files": [{"original": str(self.test_files[0]), "snapshot": "path1"}]
        }))
        (snap_dir2 / "metadata.json").write_text(json.dumps({
            "timestamp": ts2, "prompt": "prompt2",
            "files": [{"original": str(self.test_files[0]), "snapshot": "path2"}]
        }))

        # Test listing all snapshots (returns formatted strings)
        snaps = snapshot.list_snapshots()
        self.assertEqual(len(snaps), 2)
        self.assertTrue(snaps[0].startswith("002")) # Newest first
        self.assertTrue(snaps[1].startswith("001"))

        # Test listing snapshots for specific file (returns tuples)
        file_snaps = snapshot.list_snapshots(self.test_files[0])
        self.assertEqual(len(file_snaps), 2)
        self.assertIsInstance(file_snaps[0], tuple)
        self.assertTrue(file_snaps[0][0].startswith("002_")) # Newest first

    def test_restore_snapshot(self):
        # Create a snapshot to restore from
        with patch('aye.model.snapshot._get_next_ordinal', return_value=1):
            snapshot.create_snapshot([self.test_files[0]])
        
        # Modify the original file
        self.test_files[0].write_text("modified content")
        self.assertNotEqual(self.test_files[0].read_text(), "test content")

        # Restore
        snapshot.restore_snapshot(ordinal="001", file_name=str(self.test_files[0]))
        
        # Verify content is restored
        self.assertEqual(self.test_files[0].read_text(), "test content")

    def test_restore_snapshot_full_batch(self):
        with patch('aye.model.snapshot._get_next_ordinal', return_value=1):
            snapshot.create_snapshot(self.test_files)
        
        self.test_files[0].write_text("mod1")
        self.test_files[1].write_text("mod2")

        snapshot.restore_snapshot(ordinal="001")

        self.assertEqual(self.test_files[0].read_text(), "test content")
        self.assertEqual(self.test_files[1].read_text(), "test content")

    def test_restore_snapshot_latest_no_ordinal(self):
        with patch('aye.model.snapshot._get_next_ordinal', return_value=1):
            snapshot.create_snapshot(self.test_files)
        self.test_files[0].write_text("mod1")
        snapshot.restore_snapshot()
        self.assertEqual(self.test_files[0].read_text(), "test content")

    def test_restore_snapshot_no_snapshots(self):
        with self.assertRaisesRegex(ValueError, "No snapshots found"):
            snapshot.restore_snapshot()

    def test_restore_snapshot_ordinal_not_found(self):
        with self.assertRaisesRegex(ValueError, "Snapshot with Id 007 not found"):
            snapshot.restore_snapshot(ordinal="007")

    def test_restore_snapshot_metadata_missing(self):
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        (self.snap_root_val / f"001_{ts}").mkdir()
        with self.assertRaisesRegex(ValueError, "Metadata missing for snapshot 001"):
            snapshot.restore_snapshot(ordinal="001")

    def test_restore_snapshot_metadata_invalid_json(self):
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        snap_dir = self.snap_root_val / f"001_{ts}"
        snap_dir.mkdir()
        (snap_dir / "metadata.json").write_text("not json")
        with self.assertRaisesRegex(ValueError, "Invalid metadata for snapshot 001"):
            snapshot.restore_snapshot(ordinal="001")

    def test_restore_snapshot_file_not_in_snapshot(self):
        with patch('aye.model.snapshot._get_next_ordinal', return_value=1):
            snapshot.create_snapshot([self.test_files[0]])

        # The error message contains the full path. We create a regex that is
        # platform-agnostic by matching any path that ends with the expected filename.
        # re.escape is used to handle special characters (like '.') in the filename.
        file_to_check = self.test_files[1]
        expected_regex = f"File '.*{re.escape(file_to_check.name)}' not found in snapshot 001"

        with self.assertRaisesRegex(ValueError, expected_regex):
            snapshot.restore_snapshot(ordinal="001", file_name=str(file_to_check))

    def test_restore_snapshot_invalid_ordinal_format(self):
        with self.assertRaisesRegex(ValueError, "Snapshot with Id abc not found"):
            snapshot.restore_snapshot(ordinal="abc")

    @patch('builtins.print')
    def test_restore_snapshot_copy_file_not_found(self, mock_print):
        with patch('aye.model.snapshot._get_next_ordinal', return_value=1):
            batch_name = snapshot.create_snapshot([self.test_files[0]])
        snap_file = self.snap_root_val / batch_name / self.test_files[0].name
        snap_file.unlink() # Delete the backed-up file
        snapshot.restore_snapshot(ordinal="001")
        mock_print.assert_called_with(f"Warning: snapshot file missing – {snap_file}")

    @patch('builtins.print')
    def test_restore_snapshot_permission_error_logs_warning(self, mock_print):
        with patch('aye.model.snapshot._get_next_ordinal', return_value=1):
            snapshot.create_snapshot([self.test_files[0]])
        with patch('aye.model.snapshot.file_backend.shutil.copy2', side_effect=PermissionError("denied")):
            snapshot.restore_snapshot(ordinal="001")
        # On Windows, tempfile can return a short (8.3) path, but the snapshot
        # logic resolves it to a long path. We must compare against the resolved path.
        expected_message = f"Warning: failed to restore {self.test_files[0].resolve()}: denied"
        mock_print.assert_any_call(expected_message)

    def test_restore_snapshot_latest_for_file(self):
        # Create two snapshots for the same file
        self.test_files[0].write_text("version 1")
        with patch('aye.model.snapshot._get_next_ordinal', return_value=1):
            snapshot.create_snapshot([self.test_files[0]])
        
        time.sleep(0.01) # ensure timestamp is different
        self.test_files[0].write_text("version 2")
        with patch('aye.model.snapshot._get_next_ordinal', return_value=2):
            snapshot.create_snapshot([self.test_files[0]])

        # Modify file and restore latest for it
        self.test_files[0].write_text("modified")
        snapshot.restore_snapshot(file_name=str(self.test_files[0]))
        self.assertEqual(self.test_files[0].read_text(), "version 2")

    def test_prune_snapshots(self):
        # Create mock snapshots
        for i in range(5):
            ts = (datetime.now(timezone.utc) - timedelta(minutes=i)).strftime("%Y%m%dT%H%M%S")
            snap_dir = self.snap_root_val / f"{i+1:03d}_{ts}"
            snap_dir.mkdir(parents=True)

        self.assertEqual(len(list(p for p in self.snap_root_val.iterdir() if p.is_dir() and p.name != 'latest')), 5)
        
        deleted = snapshot.prune_snapshots(keep_count=2)
        self.assertEqual(deleted, 3)
        self.assertEqual(len(list(p for p in self.snap_root_val.iterdir() if p.is_dir() and p.name != 'latest')), 2)

    def test_prune_snapshots_keep_more_than_exists(self):
        # Create 2 snapshots
        for i in range(2):
            ts = (datetime.now(timezone.utc) - timedelta(minutes=i)).strftime("%Y%m%dT%H%M%S")
            (self.snap_root_val / f"{i+1:03d}_{ts}").mkdir()
        
        # Try to keep 10
        deleted_count = snapshot.prune_snapshots(keep_count=10)
        self.assertEqual(deleted_count, 0)
        self.assertEqual(len(list(p for p in self.snap_root_val.iterdir() if p.is_dir() and p.name != 'latest')), 2)

    def test_prune_snapshots_keep_zero(self):
        for i in range(3):
            ts = (datetime.now(timezone.utc) - timedelta(minutes=i)).strftime("%Y%m%dT%H%M%S")
            (self.snap_root_val / f"{i+1:03d}_{ts}").mkdir()

        deleted_count = snapshot.prune_snapshots(keep_count=0)
        self.assertEqual(deleted_count, 3)
        self.assertEqual(len(list(p for p in self.snap_root_val.iterdir() if p.is_dir() and p.name != 'latest')), 0)

    def test_cleanup_snapshots(self):
        # Create old and new snapshots
        old_ts = (datetime.now(timezone.utc) - timedelta(days=35)).strftime("%Y%m%dT%H%M%S")
        new_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        (self.snap_root_val / f"001_{old_ts}").mkdir(parents=True)
        (self.snap_root_val / f"002_{new_ts}").mkdir(parents=True)

        self.assertEqual(len(list(p for p in self.snap_root_val.iterdir() if p.is_dir() and p.name != 'latest')), 2)
        
        deleted = snapshot.cleanup_snapshots(older_than_days=30)
        self.assertEqual(deleted, 1)
        self.assertEqual(len(list(p for p in self.snap_root_val.iterdir() if p.is_dir() and p.name != 'latest')), 1)
        self.assertTrue((self.snap_root_val / f"002_{new_ts}").exists())

    def test_cleanup_snapshots_invalid_dir_name(self):
        # Create one valid old snapshot and one invalid one
        old_ts = (datetime.now(timezone.utc) - timedelta(days=35)).strftime("%Y%m%dT%H%M%S")
        (self.snap_root_val / f"001_{old_ts}").mkdir()
        (self.snap_root_val / "invalid_name").mkdir()

        with patch('builtins.print') as mock_print:
            deleted_count = snapshot.cleanup_snapshots(older_than_days=30)
            self.assertEqual(deleted_count, 1) # Only the valid one is deleted
            self.assertTrue((self.snap_root_val / "invalid_name").exists())
            mock_print.assert_any_call("Warning: Could not parse timestamp from invalid_name")

    def test_cleanup_snapshots_no_snapshots(self):
        self.assertEqual(snapshot.cleanup_snapshots(older_than_days=1), 0)

    def test_apply_updates(self):
        with patch('aye.model.snapshot.create_snapshot', return_value="001_20230101T000000") as mock_create:
            updated_files = [
                {"file_name": str(self.test_files[0]), "file_content": "new content"}
            ]
            batch_ts = snapshot.apply_updates(updated_files, prompt="apply update")

            self.assertEqual(batch_ts, "001_20230101T000000")
            mock_create.assert_called_once_with([self.test_files[0]], "apply update")

            # Verify file was written
            self.assertEqual(self.test_files[0].read_text(), "new content")

    def test_apply_updates_multiple_files(self):
        with patch('aye.model.snapshot.create_snapshot', return_value="003_20240101T000000") as mock_create:
            updated_files = [
                {"file_name": str(self.test_files[0]), "file_content": "first"},
                {"file_name": str(self.test_files[1]), "file_content": "second"}
            ]
            batch_ts = snapshot.apply_updates(updated_files)

            self.assertEqual(batch_ts, "003_20240101T000000")
            mock_create.assert_called_once_with([self.test_files[0], self.test_files[1]], None)
            self.assertEqual(self.test_files[0].read_text(), "first")
            self.assertEqual(self.test_files[1].read_text(), "second")

    def test_apply_updates_no_files(self):
        # It should raise ValueError because create_snapshot will be called with an empty list
        with self.assertRaisesRegex(ValueError, "No files supplied for snapshot"):
            snapshot.apply_updates([], prompt="empty update")

    @patch('aye.model.snapshot.list_snapshots')
    def test_driver(self, mock_list_snapshots):
        snapshot.driver()
        mock_list_snapshots.assert_called_once()
