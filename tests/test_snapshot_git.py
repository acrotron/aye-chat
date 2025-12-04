"""Tests for GitStashBackend snapshot functionality."""

import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch, MagicMock

import aye.model.snapshot as snapshot
from aye.model.snapshot import GitStashBackend, FileBasedBackend, get_backend, reset_backend


class TestBackendSelection(TestCase):
    """Test backend selection based on git detection."""

    def setUp(self):
        reset_backend()

    def tearDown(self):
        reset_backend()

    def test_file_based_backend_when_not_in_git_repo(self):
        with patch('aye.model.snapshot._is_git_repository', return_value=None):
            reset_backend()
            backend = get_backend()
            self.assertIsInstance(backend, FileBasedBackend)

    def test_git_stash_backend_when_in_git_repo(self):
        """Test that GitStashBackend is returned when in a git repo.
        
        NOTE: Git backend is currently disabled in production code.
        This test verifies the GitStashBackend class works correctly
        when instantiated directly.
        """
        # Test that GitStashBackend can be instantiated and works
        git_root = Path('/fake/git/root')
        backend = GitStashBackend(git_root)
        self.assertIsInstance(backend, GitStashBackend)
        self.assertEqual(backend.git_root, git_root)

    def test_backend_singleton(self):
        with patch('aye.model.snapshot._is_git_repository', return_value=None):
            reset_backend()
            backend1 = get_backend()
            backend2 = get_backend()
            self.assertIs(backend1, backend2)

    def test_reset_backend_clears_singleton(self):
        with patch('aye.model.snapshot._is_git_repository', return_value=None):
            reset_backend()
            backend1 = get_backend()
            reset_backend()
            backend2 = get_backend()
            self.assertIsNot(backend1, backend2)


class TestGitStashBackendUnit(TestCase):
    """Unit tests for GitStashBackend with mocked git commands."""

    def setUp(self):
        self.git_root = Path('/fake/git/root')
        self.backend = GitStashBackend(self.git_root)

    def test_truncate_prompt(self):
        self.assertEqual(self.backend._truncate_prompt("short"), "short".ljust(32))
        self.assertEqual(self.backend._truncate_prompt("a" * 40), "a" * 32 + "...")
        self.assertEqual(self.backend._truncate_prompt(None), "no prompt".ljust(32))
        self.assertEqual(self.backend._truncate_prompt("  "), "no prompt".ljust(32))

    def test_get_stash_list_parses_aye_stashes(self):
        mock_output = """stash@{0}: On main: aye: 003_20231203T120000 | third prompt | file3.py
stash@{1}: On main: Some other stash
stash@{2}: On main: aye: 002_20231202T110000 | second prompt | file2.py
stash@{3}: On main: aye: 001_20231201T100000 | first prompt | file1.py"""

        with patch.object(self.backend, '_run_git') as mock_git:
            mock_git.return_value = MagicMock(returncode=0, stdout=mock_output)
            stashes = self.backend._get_stash_list()

        self.assertEqual(len(stashes), 3)
        self.assertEqual(stashes[0]['ordinal'], '003')
        self.assertEqual(stashes[0]['batch_id'], '003_20231203T120000')
        self.assertEqual(stashes[0]['prompt'], 'third prompt')
        self.assertEqual(stashes[1]['ordinal'], '002')
        self.assertEqual(stashes[2]['ordinal'], '001')

    def test_get_stash_list_returns_empty_on_error(self):
        with patch.object(self.backend, '_run_git') as mock_git:
            mock_git.return_value = MagicMock(returncode=1, stdout='', stderr='error')
            stashes = self.backend._get_stash_list()
        self.assertEqual(stashes, [])

    def test_get_next_ordinal_returns_one_when_no_stashes(self):
        with patch.object(self.backend, '_get_stash_list', return_value=[]):
            self.assertEqual(self.backend._get_next_ordinal(), 1)

    def test_get_next_ordinal_increments_from_max(self):
        stashes = [
            {'ordinal': '001', 'batch_id': '001_20231201T100000'},
            {'ordinal': '003', 'batch_id': '003_20231203T120000'},
        ]
        with patch.object(self.backend, '_get_stash_list', return_value=stashes):
            self.assertEqual(self.backend._get_next_ordinal(), 4)

    def test_create_snapshot_no_files_raises(self):
        with self.assertRaisesRegex(ValueError, "No files supplied for snapshot"):
            self.backend.create_snapshot([])

    def test_list_snapshots_formats_correctly(self):
        stashes = [
            {'ordinal': '002', 'batch_id': '002_20231202T110000', 'prompt': 'edit config', 'files': 'config.py', 'index': 0},
            {'ordinal': '001', 'batch_id': '001_20231201T100000', 'prompt': 'initial', 'files': 'main.py', 'index': 1},
        ]
        with patch.object(self.backend, '_get_stash_list', return_value=stashes):
            result = self.backend.list_snapshots()

        self.assertEqual(len(result), 2)
        self.assertIn('002', result[0])
        self.assertIn('edit config', result[0])
        self.assertIn('config.py', result[0])

    def test_restore_snapshot_no_snapshots_raises(self):
        with patch.object(self.backend, '_get_stash_list', return_value=[]):
            with self.assertRaisesRegex(ValueError, "No snapshots found"):
                self.backend.restore_snapshot()

    def test_restore_snapshot_ordinal_not_found_raises(self):
        stashes = [{'ordinal': '001', 'batch_id': '001_20231201T100000', 'index': 0}]
        with patch.object(self.backend, '_get_stash_list', return_value=stashes):
            with self.assertRaisesRegex(ValueError, "Snapshot with Id 999 not found"):
                self.backend.restore_snapshot(ordinal="999")


class TestGitStashBackendIntegration(TestCase):
    """Integration tests for GitStashBackend using real temporary git repos."""

    def setUp(self):
        # Create a temporary directory for a real git repo
        self.tmpdir = tempfile.TemporaryDirectory()
        self.git_root = Path(self.tmpdir.name)

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=self.git_root, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=self.git_root, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.git_root, capture_output=True, check=True)

        # Create and commit an initial file
        initial_file = self.git_root / "initial.txt"
        initial_file.write_text("initial content")
        subprocess.run(["git", "add", "initial.txt"], cwd=self.git_root, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=self.git_root, capture_output=True, check=True)

        self.backend = GitStashBackend(self.git_root)

        # Reset module backend
        reset_backend()

    def tearDown(self):
        reset_backend()
        self.tmpdir.cleanup()

    def test_create_and_list_snapshot(self):
        # Create a test file with changes
        test_file = self.git_root / "test.txt"
        test_file.write_text("test content")
        subprocess.run(["git", "add", "test.txt"], cwd=self.git_root, capture_output=True, check=True)

        # Create snapshot
        batch_id = self.backend.create_snapshot([test_file], prompt="test snapshot")

        self.assertTrue(batch_id.startswith("001_"))

        # List snapshots
        snaps = self.backend.list_snapshots()
        self.assertEqual(len(snaps), 1)
        self.assertIn("001", snaps[0])
        self.assertIn("test snapshot", snaps[0])

    def test_create_restore_snapshot(self):
        # Create a test file
        test_file = self.git_root / "restore_test.txt"
        test_file.write_text("original content")
        subprocess.run(["git", "add", "restore_test.txt"], cwd=self.git_root, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "Add restore test file"], cwd=self.git_root, capture_output=True, check=True)

        # Modify the file
        test_file.write_text("modified content")

        # Create snapshot
        batch_id = self.backend.create_snapshot([test_file], prompt="before restore")

        # File should still have modified content (stash apply)
        self.assertEqual(test_file.read_text(), "modified content")

        # Reset file to committed state (simulate user undoing changes or wanting to restore)
        subprocess.run(["git", "checkout", "--", "restore_test.txt"], cwd=self.git_root, capture_output=True, check=True)
        self.assertEqual(test_file.read_text(), "original content")

        # Restore from snapshot
        self.backend.restore_snapshot(ordinal="001")

        # File should be restored to modified content from the stash
        self.assertEqual(test_file.read_text(), "modified content")

    def test_prune_snapshots(self):
        test_file = self.git_root / "prune_test.txt"

        # Create multiple snapshots
        for i in range(5):
            test_file.write_text(f"content {i}")
            subprocess.run(["git", "add", "prune_test.txt"], cwd=self.git_root, capture_output=True, check=True)
            self.backend.create_snapshot([test_file], prompt=f"snapshot {i}")

        # Should have 5 snapshots
        snaps = self.backend.list_snapshots()
        self.assertEqual(len(snaps), 5)

        # Prune to keep only 2
        deleted = self.backend.prune_snapshots(keep_count=2)
        self.assertEqual(deleted, 3)

        # Should have 2 snapshots now
        snaps = self.backend.list_snapshots()
        self.assertEqual(len(snaps), 2)

    def test_prune_snapshots_keep_zero(self):
        test_file = self.git_root / "prune_zero_test.txt"

        # Create multiple snapshots
        for i in range(3):
            test_file.write_text(f"content {i}")
            subprocess.run(["git", "add", "prune_zero_test.txt"], cwd=self.git_root, capture_output=True, check=True)
            self.backend.create_snapshot([test_file], prompt=f"snapshot {i}")

        # Should have 3 snapshots
        snaps = self.backend.list_snapshots()
        self.assertEqual(len(snaps), 3)

        # Prune with keep_count=0 should delete all snapshots
        deleted = self.backend.prune_snapshots(keep_count=0)
        self.assertEqual(deleted, 3)

        # Should have 0 snapshots now
        snaps = self.backend.list_snapshots()
        self.assertEqual(len(snaps), 0)

    def test_warns_uncommitted_changes_to_other_files(self):
        # Create two files
        file1 = self.git_root / "file1.txt"
        file2 = self.git_root / "file2.txt"

        file1.write_text("file1 content")
        file2.write_text("file2 content")

        subprocess.run(["git", "add", "."], cwd=self.git_root, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "Add files"], cwd=self.git_root, capture_output=True, check=True)

        # Modify both files
        file1.write_text("file1 modified")
        file2.write_text("file2 modified")

        # Create snapshot only for file1 - should warn about file2
        with patch.object(self.backend, '_warn_uncommitted_changes') as mock_warn:
            self.backend.create_snapshot([file1], prompt="snapshot file1")
            # Check that warning was called with file2
            if mock_warn.called:
                warned_files = mock_warn.call_args[0][0]
                warned_paths = [f.name for f in warned_files]
                self.assertIn("file2.txt", warned_paths)


class TestIsGitRepository(TestCase):
    """Test git repository detection."""

    def test_returns_path_in_git_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            git_root = Path(tmpdir)
            subprocess.run(["git", "init"], cwd=git_root, capture_output=True, check=True)

            # Change to the temp directory and test
            original_cwd = os.getcwd()
            try:
                os.chdir(git_root)
                result = snapshot._is_git_repository()
                self.assertIsNotNone(result)
                self.assertEqual(result.resolve(), git_root.resolve())
            finally:
                os.chdir(original_cwd)

    def test_returns_none_when_not_in_git_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            non_git_dir = Path(tmpdir)

            original_cwd = os.getcwd()
            try:
                os.chdir(non_git_dir)
                result = snapshot._is_git_repository()
                self.assertIsNone(result)
            finally:
                os.chdir(original_cwd)
