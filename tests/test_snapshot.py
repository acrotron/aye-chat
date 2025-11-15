import os
import json
import shutil
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
            batch_name = snapshot.create_snapshot([self.test_files[0]])
        
        # Modify the original file
        self.test_files[0].write_text("modified content")
        self.assertNotEqual(self.test_files[0].read_text(), "test content")

        # Restore
        snapshot.restore_snapshot(ordinal="001", file_name=str(self.test_files[0]))
        
        # Verify content is restored
        self.assertEqual(self.test_files[0].read_text(), "test content")

    def test_prune_snapshots(self):
        # Create mock snapshots
        for i in range(5):
            ts = (datetime.utcnow() - timedelta(minutes=i)).strftime("%Y%m%dT%H%M%S")
            snap_dir = self.snap_root_val / f"{i+1:03d}_{ts}"
            snap_dir.mkdir(parents=True)

        self.assertEqual(len(list(self.snap_root_val.iterdir())), 5)
        
        deleted = snapshot.prune_snapshots(keep_count=2)
        self.assertEqual(deleted, 3)
        self.assertEqual(len(list(self.snap_root_val.iterdir())), 2)

    def test_cleanup_snapshots(self):
        # Create old and new snapshots
        old_ts = (datetime.utcnow() - timedelta(days=35)).strftime("%Y%m%dT%H%M%S")
        new_ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        (self.snap_root_val / f"001_{old_ts}").mkdir(parents=True)
        (self.snap_root_val / f"002_{new_ts}").mkdir(parents=True)

        self.assertEqual(len(list(self.snap_root_val.iterdir())), 2)
        
        deleted = snapshot.cleanup_snapshots(older_than_days=30)
        self.assertEqual(deleted, 1)
        self.assertEqual(len(list(self.snap_root_val.iterdir())), 1)
        self.assertTrue((self.snap_root_val / f"002_{new_ts}").exists())

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
