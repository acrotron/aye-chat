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

import aye.model.snapshot as snapshot


class TestSnapshot(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.snap_root_val = Path(self.tmpdir.name) / "snapshots"
        self.snap_root_val.mkdir(parents=True, exist_ok=True)  # Ensure parent exists
        self.latest_dir_val = self.snap_root_val / "latest"
        self.test_dir = Path(self.tmpdir.name) / "src"
        self.test_dir.mkdir()

        # Patch the constants in the snapshot module
        self.snap_root_patcher = patch('aye.model.snapshot.SNAP_ROOT', self.snap_root_val)
        self.latest_dir_patcher = patch('aye.model.snapshot.LATEST_SNAP_DIR', self.latest_dir_val)
        self.snap_root_patcher.start()
        self.latest_dir_patcher.start()

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

    def test_list_snapshots(self):
        # Create mock snapshot dirs
        ts1 = (datetime.utcnow() - timedelta(minutes=2)).strftime("%Y%m%dT%H%M%S")
        ts2 = (datetime.utcnow() - timedelta(minutes=1)).strftime("%Y%m%dT%H%M%S")
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
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        (self.snap_root_val / f"001_{ts}").mkdir()
        with self.assertRaisesRegex(ValueError, "Metadata missing for snapshot 001"):
            snapshot.restore_snapshot(ordinal="001")

    def test_restore_snapshot_metadata_invalid_json(self):
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        snap_dir = self.snap_root_val / f"001_{ts}"
        snap_dir.mkdir()
        (snap_dir / "metadata.json").write_text("not json")
        with self.assertRaisesRegex(ValueError, "Invalid metadata for snapshot 001"):
            snapshot.restore_snapshot(ordinal="001")

    def test_restore_snapshot_file_not_in_snapshot(self):
        with patch('aye.model.snapshot._get_next_ordinal', return_value=1):
            snapshot.create_snapshot([self.test_files[0]])
        with self.assertRaisesRegex(ValueError, r"File '.*test2\.py' not found in snapshot 001"):
            snapshot.restore_snapshot(ordinal="001", file_name=str(self.test_files[1]))

    @patch('builtins.print')
    def test_restore_snapshot_copy_file_not_found(self, mock_print):
        with patch('aye.model.snapshot._get_next_ordinal', return_value=1):
            batch_name = snapshot.create_snapshot([self.test_files[0]])
        snap_file = self.snap_root_val / batch_name / self.test_files[0].name
        snap_file.unlink() # Delete the backed-up file
        snapshot.restore_snapshot(ordinal="001")
        mock_print.assert_called_with(f"Warning: snapshot file missing – {snap_file}")

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
            ts = (datetime.utcnow() - timedelta(minutes=i)).strftime("%Y%m%dT%H%M%S")
            snap_dir = self.snap_root_val / f"{i+1:03d}_{ts}"
            snap_dir.mkdir(parents=True)

        self.assertEqual(len(list(p for p in self.snap_root_val.iterdir() if p.is_dir() and p.name != 'latest')), 5)
        
        deleted = snapshot.prune_snapshots(keep_count=2)
        self.assertEqual(deleted, 3)
        self.assertEqual(len(list(p for p in self.snap_root_val.iterdir() if p.is_dir() and p.name != 'latest')), 2)

    def test_prune_snapshots_keep_more_than_exists(self):
        # Create 2 snapshots
        for i in range(2):
            ts = (datetime.utcnow() - timedelta(minutes=i)).strftime("%Y%m%dT%H%M%S")
            (self.snap_root_val / f"{i+1:03d}_{ts}").mkdir()
        
        # Try to keep 10
        deleted_count = snapshot.prune_snapshots(keep_count=10)
        self.assertEqual(deleted_count, 0)
        self.assertEqual(len(list(p for p in self.snap_root_val.iterdir() if p.is_dir() and p.name != 'latest')), 2)

    def test_cleanup_snapshots(self):
        # Create old and new snapshots
        old_ts = (datetime.utcnow() - timedelta(days=35)).strftime("%Y%m%dT%H%M%S")
        new_ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        (self.snap_root_val / f"001_{old_ts}").mkdir(parents=True)
        (self.snap_root_val / f"002_{new_ts}").mkdir(parents=True)

        self.assertEqual(len(list(p for p in self.snap_root_val.iterdir() if p.is_dir() and p.name != 'latest')), 2)
        
        deleted = snapshot.cleanup_snapshots(older_than_days=30)
        self.assertEqual(deleted, 1)
        self.assertEqual(len(list(p for p in self.snap_root_val.iterdir() if p.is_dir() and p.name != 'latest')), 1)
        self.assertTrue((self.snap_root_val / f"002_{new_ts}").exists())

    def test_cleanup_snapshots_invalid_dir_name(self):
        # Create one valid old snapshot and one invalid one
        old_ts = (datetime.utcnow() - timedelta(days=35)).strftime("%Y%m%dT%H%M%S")
        (self.snap_root_val / f"001_{old_ts}").mkdir()
        (self.snap_root_val / "invalid_name").mkdir()

        with patch('builtins.print') as mock_print:
            deleted_count = snapshot.cleanup_snapshots(older_than_days=30)
            self.assertEqual(deleted_count, 1) # Only the valid one is deleted
            self.assertTrue((self.snap_root_val / "invalid_name").exists())
            mock_print.assert_any_call("Warning: Could not parse timestamp from invalid_name")

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

    def test_apply_updates_no_files(self):
        # It should raise ValueError because create_snapshot will be called with an empty list
        with self.assertRaisesRegex(ValueError, "No files supplied for snapshot"):
            snapshot.apply_updates([], prompt="empty update")

    @patch('aye.model.snapshot.list_snapshots')
    def test_driver(self, mock_list_snapshots):
        snapshot.driver()
        mock_list_snapshots.assert_called_once()