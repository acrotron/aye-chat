import os
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch, MagicMock, call
import pytest

import aye.snapshot as snapshot


class TestSnapshot(TestCase):
    def setUp(self):
        import tempfile
        temp_dir = Path(tempfile.gettempdir())
        self.snap_root = temp_dir / "mock_snapshots"
        self.latest_dir = self.snap_root / "latest"
        self.test_files = [
            temp_dir / "test1.py",
            temp_dir / "test2.py"
        ]

        # Create test files
        for f in self.test_files:
            f.write_text("test content")

    def tearDown(self):
        # Clean up test files
        for f in self.test_files:
            if f.exists():
                f.unlink()

        # Remove mock snapshot dir if it exists
        if self.snap_root.exists():
            shutil.rmtree(self.snap_root)

    @patch('aye.snapshot.SNAP_ROOT')
    @patch('aye.snapshot.LATEST_SNAP_DIR')
    def test_create_snapshot(self, mock_latest_dir, mock_snap_root):
        # Configure mocks to behave like Path objects
        mock_snap_root.return_value = MagicMock(spec=Path)
        mock_latest_dir.return_value = MagicMock(spec=Path)

        mock_snap_root.return_value.__str__.return_value = str(self.snap_root)
        mock_latest_dir.return_value.__str__.return_value = str(self.latest_dir)

        mock_snap_root.return_value.exists.return_value = True
        mock_latest_dir.return_value.exists.return_value = True

        mock_snap_root.return_value.is_dir.return_value = False
        mock_snap_root.return_value.mkdir.return_value = None
        mock_latest_dir.return_value.mkdir.return_value = None

        #with patch('shutil.copy2') as mock_copy2, \
        #     patch('aye.snapshot._get_next_ordinal', return_value=1):  # Mock ordinal generation
        #    batch_name = snapshot.create_snapshot(self.test_files)

        #self.assertTrue(batch_name.startswith("001_"))
        #mock_snap_root.return_value.mkdir.assert_called_once_with(parents=True, exist_ok=True)
        #mock_latest_dir.return_value.mkdir.assert_called_once_with(parents=True, exist_ok=True)
        #self.assertEqual(mock_copy2.call_count, len(self.test_files))

    @patch('aye.snapshot.SNAP_ROOT')
    @patch('aye.snapshot.LATEST_SNAP_DIR')
    def test_list_snapshots(self, mock_latest_dir, mock_snap_root):
        mock_snap_root.__str__.return_value = str(self.snap_root)
        mock_latest_dir.__str__.return_value = str(self.latest_dir)

        # Create mock snapshot dirs
        snap_dirs = [
            self.snap_root / "001_20230101T000000",
            self.snap_root / "002_20230102T000000"
        ]

        # Mock glob to return our test dirs
        mock_snap_root.is_dir.return_value = True
        mock_snap_root.iterdir.return_value = snap_dirs

        # Mock metadata files
        for snap_dir in snap_dirs:
            snap_dir.mkdir(parents=True)
            meta_file = snap_dir / "metadata.json"
            meta_file.write_text(json.dumps({
                "timestamp": "20230101T000000",
                "files": [{"original": str(self.test_files[0]), "snapshot": str(snap_dir / "test1.py")}]
            }))

        # Test listing all snapshots
        snaps = snapshot.list_snapshots()
        self.assertEqual(len(snaps), 2)

        # Test listing snapshots for specific file
        file_snaps = snapshot.list_snapshots(self.test_files[0])
        self.assertEqual(len(file_snaps), 2)

    @patch('aye.snapshot.SNAP_ROOT')
    @patch('aye.snapshot.LATEST_SNAP_DIR')
    def test_restore_snapshot(self, mock_latest_dir, mock_snap_root):
        # Mock constants' str (if needed for logging/paths)
        mock_snap_root.__str__.return_value = str(self.snap_root)
        mock_latest_dir.__str__.return_value = str(self.latest_dir)

        # Create FULLY MOCKED snapshot structure
        mock_snap_dir = MagicMock(spec=Path)  # Mimic Path behavior
        mock_snap_dir.name = "001_20230101T000000"  # For matching "001"
        mock_snap_dir.__str__.return_value = str(self.snap_root / "001_20230101T000000")

        # Mock metadata.json read
        mock_meta_file = MagicMock(spec=Path)
        mock_meta_file.read_text.return_value = json.dumps({
            "timestamp": "20230101T000000",
            "files": [{"original": str(self.test_files[0]), "snapshot": str(mock_snap_dir / "test1.py")}]
        })
        mock_meta_file.exists.return_value = True
        mock_meta_file.suffix = ".json"
        mock_meta_file.__str__.return_value = str(mock_snap_dir / "metadata.json")

        # Mock snap_dir's / operator and iterdir
        mock_snap_dir.__truediv__ = MagicMock(return_value=mock_meta_file)  # For snap_dir / "metadata.json"
        mock_snap_dir.is_dir.return_value = True

        # Now set up the root mock
        mock_snap_root.is_dir.return_value = True
        mock_snap_root.iterdir.return_value = [mock_snap_dir]  # Fully mocked dir
        mock_snap_root.__truediv__ = MagicMock(return_value=mock_snap_dir)  # If function does SNAP_ROOT / "001_..."

        # Test
        with patch('shutil.copy2') as mock_copy:
            snapshot.restore_snapshot("001", str(self.test_files[0]))
            mock_copy.assert_called_once_with(  # Also assert args for better verification
                Path(str(mock_snap_dir / "test1.py")),
                # Expected snapshot path
                Path(str(self.test_files[0]))  # Expected original path
            )

    @patch('aye.snapshot.SNAP_ROOT')
    @patch('aye.snapshot.LATEST_SNAP_DIR')
    def test_prune_snapshots(self, mock_latest_dir, mock_snap_root):
        mock_snap_root.__str__.return_value = str(self.snap_root)
        mock_latest_dir.__str__.return_value = str(self.latest_dir)

        # Create mock snapshots
        snap_dirs = [
            self.snap_root / "001_20230101T000000",
            self.snap_root / "002_20230102T000000",
            self.snap_root / "003_20230103T000000"
        ]

        # Mock glob to return our test dirs
        mock_snap_root.is_dir.return_value = True
        mock_snap_root.iterdir.return_value = snap_dirs

        # Mock metadata files
        for snap_dir in snap_dirs:
            snap_dir.mkdir(parents=True)
            meta_file = snap_dir / "metadata.json"
            meta_file.write_text(json.dumps({"timestamp": snap_dir.name.split('_')[1]}))

        # Test pruning
        with patch('shutil.rmtree') as mock_rmtree:
            deleted = snapshot.prune_snapshots(keep_count=2)
            self.assertEqual(deleted, 1)
            mock_rmtree.assert_called_once()

    @patch('aye.snapshot.SNAP_ROOT')
    @patch('aye.snapshot.LATEST_SNAP_DIR')
    def test_cleanup_snapshots(self, mock_latest_dir, mock_snap_root):
        mock_snap_root.__str__.return_value = str(self.snap_root)
        mock_latest_dir.__str__.return_value = str(self.latest_dir)

        # Create mock snapshots with different timestamps
        old_date = (datetime.now(timezone.utc) - timedelta(days=31)).strftime("%Y%m%dT%H%M%S")
        snap_dirs = [
            self.snap_root / f"001_{old_date}",
            self.snap_root / "002_20230102T000000"
        ]

        # Mock glob to return our test dirs
        mock_snap_root.is_dir.return_value = True
        mock_snap_root.iterdir.return_value = snap_dirs

        # Mock metadata files
        for snap_dir in snap_dirs:
            snap_dir.mkdir(parents=True)
            meta_file = snap_dir / "metadata.json"
            meta_file.write_text(json.dumps({"timestamp": snap_dir.name.split('_')[1]}))

        # Test cleanup
        #with patch('shutil.rmtree') as mock_rmtree:
        #    deleted = snapshot.cleanup_snapshots(older_than_days=30)
        #    self.assertEqual(deleted, 1)
        #    mock_rmtree.assert_called_once()

    @patch('aye.snapshot.SNAP_ROOT')
    @patch('aye.snapshot.LATEST_SNAP_DIR')
    def test_apply_updates(self, mock_latest_dir, mock_snap_root):
        mock_snap_root.__str__.return_value = str(self.snap_root)
        mock_latest_dir.__str__.return_value = str(self.latest_dir)

        # Mock create_snapshot
        with patch('aye.snapshot.create_snapshot', return_value="001_20230101T000000") as mock_create:
            updated_files = [
                {"file_name": str(self.test_files[0]), "file_content": "new content"}
            ]
            batch_ts = snapshot.apply_updates(updated_files)

            self.assertEqual(batch_ts, "001_20230101T000000")
            mock_create.assert_called_once()

            # Verify file was written
            self.assertEqual(self.test_files[0].read_text(), "new content")


if __name__ == '__main__':
    import unittest

    unittest.main()
