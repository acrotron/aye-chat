import io
import subprocess
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from aye.model.snapshot.git_backend import GitStashBackend


def cp(args, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr=stderr)


class TestGitStashBackendParsing(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.git_root = Path(self.tmp.name)
        self.backend = GitStashBackend(self.git_root)

    def test_get_stash_list_parses_aye_entries(self):
        stdout = "\n".join(
            [
                "stash@{0}: On main: aye: 002_20231201T120000 | fix bug | a.py,b.py",
                "stash@{1}: On main: something else",
                "stash@{2}: On main: aye: 001_20231130T090000 |  prompt with spaces   | x.txt",
                "stash@{3}: On main: aye: not-a-match | nope | y",  # won't match regex
            ]
        )

        self.backend._run_git = MagicMock(return_value=cp(["git", "stash", "list"], 0, stdout=stdout))
        stashes = self.backend._get_stash_list()

        self.assertEqual(len(stashes), 2)
        self.assertEqual(stashes[0]["index"], 0)
        self.assertEqual(stashes[0]["batch_id"], "002_20231201T120000")
        self.assertEqual(stashes[0]["ordinal"], "002")
        self.assertEqual(stashes[0]["timestamp"], "20231201T120000")
        self.assertEqual(stashes[0]["prompt"], "fix bug")
        self.assertEqual(stashes[0]["files"], "a.py,b.py")

        self.assertEqual(stashes[1]["index"], 2)
        self.assertEqual(stashes[1]["batch_id"], "001_20231130T090000")
        self.assertEqual(stashes[1]["prompt"], "prompt with spaces")
        self.assertEqual(stashes[1]["files"], "x.txt")

    def test_get_stash_list_returns_empty_on_git_failure(self):
        self.backend._run_git = MagicMock(return_value=cp(["git"], 1, stdout="", stderr="boom"))
        self.assertEqual(self.backend._get_stash_list(), [])

    def test_get_next_ordinal_empty(self):
        self.backend._get_stash_list = MagicMock(return_value=[])
        self.assertEqual(self.backend._get_next_ordinal(), 1)

    def test_get_next_ordinal_non_empty(self):
        self.backend._get_stash_list = MagicMock(
            return_value=[
                {"ordinal": "001"},
                {"ordinal": "010"},
                {"ordinal": "003"},
            ]
        )
        self.assertEqual(self.backend._get_next_ordinal(), 11)

    def test_truncate_prompt_none_or_blank(self):
        self.assertEqual(self.backend._truncate_prompt(None, max_length=8), "no prompt")
        self.assertEqual(self.backend._truncate_prompt("   ", max_length=8), "no prompt")

    def test_truncate_prompt_short_pads(self):
        out = self.backend._truncate_prompt("hi", max_length=5)
        self.assertEqual(out, "hi".ljust(5))

    def test_truncate_prompt_long_truncates_with_ellipsis(self):
        out = self.backend._truncate_prompt("abcdefghijklmnopqrstuvwxyz", max_length=5)
        # Implementation returns prompt[:max_length] + "..."
        self.assertEqual(out, "abcde...")


class TestGitStashBackendWorkingTree(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.git_root = Path(self.tmp.name)
        self.backend = GitStashBackend(self.git_root)

    def test_check_other_uncommitted_changes_excludes_targets_and_handles_quoted(self):
        target1 = (self.git_root / "a.txt").resolve()
        target2 = (self.git_root / "dir" / "b.txt").resolve()

        stdout = "\n".join(
            [
                " M a.txt",
                "A  dir/b.txt",
                " M other.txt",
                "?? \"spaced name.txt\"",
                "",  # ignored
            ]
        )

        self.backend._run_git = MagicMock(return_value=cp(["git", "status"], 0, stdout=stdout))
        changed = self.backend._check_other_uncommitted_changes([target1, target2])

        self.assertEqual(set(changed), {(self.git_root / "other.txt").resolve(), (self.git_root / "spaced name.txt").resolve()})

    def test_get_untracked_files_filters_to_given_paths(self):
        f1 = (self.git_root / "a.txt").resolve()
        f2 = (self.git_root / "b.txt").resolve()
        stdout = "a.txt\n"  # only a.txt is untracked

        self.backend._run_git = MagicMock(return_value=cp(["git", "ls-files"], 0, stdout=stdout))
        untracked = self.backend._get_untracked_files([f1, f2])
        self.assertEqual(untracked, [f1])


class TestGitStashBackendSnapshotOps(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.git_root = Path(self.tmp.name)
        self.backend = GitStashBackend(self.git_root)

    def test_get_file_content_from_snapshot_success(self):
        self.backend._run_git = MagicMock(return_value=cp(["git", "show"], 0, stdout="content"))
        out = self.backend.get_file_content_from_snapshot("a.txt", "stash@{0}")
        self.assertEqual(out, "content")

    def test_get_file_content_from_snapshot_not_found(self):
        self.backend._run_git = MagicMock(return_value=cp(["git", "show"], 1, stdout="", stderr="bad"))
        out = self.backend.get_file_content_from_snapshot("a.txt", "stash@{0}")
        self.assertIsNone(out)

    def test_get_file_content_from_snapshot_exception(self):
        self.backend._run_git = MagicMock(side_effect=RuntimeError("boom"))
        out = self.backend.get_file_content_from_snapshot("a.txt", "stash@{0}")
        self.assertIsNone(out)

    def test_create_snapshot_raises_on_empty_file_list(self):
        with self.assertRaises(ValueError):
            self.backend.create_snapshot([])

    @patch("aye.model.snapshot.git_backend.rprint")
    def test_create_snapshot_no_changes_short_circuits(self, rprint_mock):
        f = (self.git_root / "a.txt")

        self.backend._check_other_uncommitted_changes = MagicMock(return_value=[])
        self.backend._get_untracked_files = MagicMock(return_value=[])
        self.backend._get_next_ordinal = MagicMock(return_value=1)

        fixed_now = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

        def run_side_effect(args, check=False, capture_output=True):
            if args[:2] == ["status", "--porcelain"]:
                return cp(["git"] + args, 0, stdout="")  # no changes
            return cp(["git"] + args, 0, stdout="")

        with patch("aye.model.snapshot.git_backend.datetime") as dt:
            dt.now.return_value = fixed_now
            dt.strptime = datetime.strptime
            self.backend._run_git = MagicMock(side_effect=run_side_effect)
            batch_id = self.backend.create_snapshot([f], prompt="hello")

        self.assertEqual(batch_id, "001_20240102T030405")
        rprint_mock.assert_called()

    def test_create_snapshot_stages_untracked_intent_to_add(self):
        f = (self.git_root / "a.txt")
        resolved = f.resolve()

        self.backend._check_other_uncommitted_changes = MagicMock(return_value=[])
        self.backend._get_untracked_files = MagicMock(return_value=[resolved])
        self.backend._get_next_ordinal = MagicMock(return_value=7)

        fixed_now = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

        calls = []

        def run_side_effect(args, check=False, capture_output=True):
            calls.append(args)
            if args[:2] == ["status", "--porcelain"]:
                return cp(["git"] + args, 0, stdout=" M a.txt\n")
            if args[:2] == ["stash", "push"]:
                return cp(["git"] + args, 0, stdout="Saved working directory")
            if args[:2] == ["stash", "apply"]:
                return cp(["git"] + args, 0, stdout="Applied")
            return cp(["git"] + args, 0, stdout="")

        with patch("aye.model.snapshot.git_backend.datetime") as dt:
            dt.now.return_value = fixed_now
            dt.strptime = datetime.strptime
            with patch("aye.model.snapshot.git_backend.Path.cwd", return_value=self.git_root):
                self.backend._run_git = MagicMock(side_effect=run_side_effect)
                batch_id = self.backend.create_snapshot([f], prompt="p")

        self.assertEqual(batch_id, "007_20240102T030405")
        self.assertTrue(any(c[:3] == ["add", "--intent-to-add", "--"] for c in calls))
        self.assertTrue(any(c[:2] == ["stash", "push"] for c in calls))
        self.assertTrue(any(c[:2] == ["stash", "apply"] for c in calls))

    @patch("aye.model.snapshot.git_backend.rprint")
    def test_create_snapshot_stash_push_no_local_changes_returns_batch_id(self, rprint_mock):
        f = (self.git_root / "a.txt")

        self.backend._check_other_uncommitted_changes = MagicMock(return_value=[])
        self.backend._get_untracked_files = MagicMock(return_value=[])
        self.backend._get_next_ordinal = MagicMock(return_value=2)

        fixed_now = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

        def run_side_effect(args, check=False, capture_output=True):
            if args[:2] == ["status", "--porcelain"]:
                return cp(["git"] + args, 0, stdout=" M a.txt\n")
            if args[:2] == ["stash", "push"]:
                return cp(["git"] + args, 1, stdout="No local changes to save", stderr="")
            return cp(["git"] + args, 0, stdout="")

        with patch("aye.model.snapshot.git_backend.datetime") as dt:
            dt.now.return_value = fixed_now
            dt.strptime = datetime.strptime
            self.backend._run_git = MagicMock(side_effect=run_side_effect)
            batch_id = self.backend.create_snapshot([f], prompt=None)

        self.assertEqual(batch_id, "002_20240102T030405")
        rprint_mock.assert_called()


class TestGitStashBackendListingAndRestore(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.git_root = Path(self.tmp.name)
        self.backend = GitStashBackend(self.git_root)

    def test_list_snapshots_all(self):
        self.backend._get_stash_list = MagicMock(
            return_value=[
                {"ordinal": "002", "prompt": "hello", "files": "a.py", "index": 0, "batch_id": "002_20240101T000000", "timestamp": "20240101T000000"},
                {"ordinal": "001", "prompt": "world", "files": "b.py", "index": 1, "batch_id": "001_20231231T000000", "timestamp": "20231231T000000"},
            ]
        )
        out = self.backend.list_snapshots()
        self.assertEqual(len(out), 2)
        self.assertTrue(out[0].startswith("002  ("))
        self.assertIn(")  a.py", out[0])

    def test_list_snapshots_for_specific_file(self):
        target = (self.git_root / "a.py")

        self.backend._get_stash_list = MagicMock(
            return_value=[
                {"ordinal": "002", "prompt": "p", "files": "a.py", "index": 5, "batch_id": "002_20240101T000000", "timestamp": "20240101T000000"},
                {"ordinal": "001", "prompt": "p", "files": "b.py", "index": 6, "batch_id": "001_20231231T000000", "timestamp": "20231231T000000"},
            ]
        )

        def run_side_effect(args, check=False, capture_output=True):
            # args: ["stash", "show", "stash@{5}"] etc.
            if args[:2] == ["stash", "show"] and args[2] == "stash@{5}":
                return cp(["git"] + args, 0, stdout="a.py | 1 +\n")
            if args[:2] == ["stash", "show"] and args[2] == "stash@{6}":
                return cp(["git"] + args, 0, stdout="b.py | 1 +\n")
            return cp(["git"] + args, 0, stdout="")

        self.backend._run_git = MagicMock(side_effect=run_side_effect)
        out = self.backend.list_snapshots(file=target)
        self.assertEqual(out, [("002_20240101T000000", "stash@{5}")])

    def test_restore_snapshot_raises_when_none_exist(self):
        self.backend._get_stash_list = MagicMock(return_value=[])
        with self.assertRaises(ValueError):
            self.backend.restore_snapshot()

    def test_restore_snapshot_by_file_uses_checkout_and_drop(self):
        file_name = str(self.git_root / "a.py")

        self.backend._get_stash_list = MagicMock(
            return_value=[
                {"index": 3, "ordinal": "003", "batch_id": "003_20240101T000000", "timestamp": "20240101T000000"},
                {"index": 4, "ordinal": "002", "batch_id": "002_20231231T000000", "timestamp": "20231231T000000"},
            ]
        )

        calls = []

        def run_side_effect(args, check=False, capture_output=True):
            calls.append(args)
            if args[:2] == ["stash", "show"] and args[2] == "stash@{3}":
                return cp(["git"] + args, 0, stdout="a.py | 1 +\n")
            if args[:2] == ["stash", "show"] and args[2] == "stash@{4}":
                return cp(["git"] + args, 0, stdout="b.py | 1 +\n")
            return cp(["git"] + args, 0, stdout="")

        self.backend._run_git = MagicMock(side_effect=run_side_effect)
        self.backend.restore_snapshot(ordinal=None, file_name=file_name)

        self.assertIn(["checkout", "stash@{3}", "--", file_name], calls)
        self.assertIn(["stash", "drop", "stash@{3}"], calls)

    def test_restore_snapshot_by_file_raises_when_not_found(self):
        file_name = str(self.git_root / "missing.py")

        self.backend._get_stash_list = MagicMock(return_value=[{"index": 1, "ordinal": "001", "batch_id": "001_20240101T000000", "timestamp": "20240101T000000"}])
        self.backend._run_git = MagicMock(return_value=cp(["git"], 0, stdout="a.py | 1 +\n"))

        with self.assertRaises(ValueError):
            self.backend.restore_snapshot(ordinal=None, file_name=file_name)

    def test_restore_snapshot_by_ordinal_not_found(self):
        self.backend._get_stash_list = MagicMock(return_value=[{"index": 1, "ordinal": "001", "batch_id": "001_20240101T000000", "timestamp": "20240101T000000"}])
        with self.assertRaises(ValueError):
            self.backend.restore_snapshot(ordinal="999")

    @patch("aye.model.snapshot.git_backend.rprint")
    def test_restore_snapshot_pop_conflict_warns_no_exception(self, rprint_mock):
        self.backend._get_stash_list = MagicMock(return_value=[{"index": 0, "ordinal": "001", "batch_id": "001_20240101T000000", "timestamp": "20240101T000000"}])

        def run_side_effect(args, check=False, capture_output=True):
            if args[:2] == ["stash", "pop"]:
                return cp(["git"] + args, 1, stdout="CONFLICT (content): ...", stderr="")
            return cp(["git"] + args, 0, stdout="")

        self.backend._run_git = MagicMock(side_effect=run_side_effect)
        self.backend.restore_snapshot(ordinal="001")
        self.assertTrue(rprint_mock.called)

    def test_restore_snapshot_pop_failure_raises(self):
        self.backend._get_stash_list = MagicMock(return_value=[{"index": 0, "ordinal": "001", "batch_id": "001_20240101T000000", "timestamp": "20240101T000000"}])
        self.backend._run_git = MagicMock(return_value=cp(["git", "stash", "pop"], 2, stdout="", stderr="bad"))

        with self.assertRaises(RuntimeError):
            self.backend.restore_snapshot(ordinal="001")


class TestGitStashBackendMaintenance(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.git_root = Path(self.tmp.name)
        self.backend = GitStashBackend(self.git_root)

    def test_list_all_snapshots_sorts_oldest_first_by_timestamp(self):
        self.backend._get_stash_list = MagicMock(
            return_value=[
                {"batch_id": "002_20240102T000000", "timestamp": "20240102T000000"},
                {"batch_id": "001_20240101T000000", "timestamp": "20240101T000000"},
            ]
        )
        out = self.backend.list_all_snapshots()
        self.assertEqual(out, [Path("001_20240101T000000"), Path("002_20240102T000000")])

    def test_delete_snapshot_found_drops_and_prints(self):
        self.backend._get_stash_list = MagicMock(
            return_value=[
                {"batch_id": "001_20240101T000000", "index": 2, "timestamp": "20240101T000000", "ordinal": "001"},
            ]
        )
        self.backend._run_git = MagicMock(return_value=cp(["git", "stash", "drop"], 0, stdout=""))

        buf = io.StringIO()
        with redirect_stdout(buf):
            self.backend.delete_snapshot("001_20240101T000000")
        self.assertIn("Deleted snapshot: 001_20240101T000000", buf.getvalue())

        self.backend._run_git.assert_called_with(["stash", "drop", "stash@{2}"])

    def test_delete_snapshot_not_found_prints_warning(self):
        self.backend._get_stash_list = MagicMock(return_value=[])
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.backend.delete_snapshot("001_20240101T000000")
        self.assertIn("Warning: Snapshot 001_20240101T000000 not found", buf.getvalue())

    def test_prune_snapshots_noop_when_under_keep_count(self):
        self.backend._get_stash_list = MagicMock(return_value=[{"ordinal": "001", "index": 0, "batch_id": "001_x"}])
        self.backend._run_git = MagicMock()
        self.assertEqual(self.backend.prune_snapshots(keep_count=5), 0)
        self.backend._run_git.assert_not_called()

    def test_prune_snapshots_deletes_oldest_by_ordinal_keeps_newest(self):
        # ordinals: 005 newest, keep_count=2 -> delete 003,002,001
        stashes = [
            {"ordinal": "001", "index": 10, "batch_id": "001_t", "timestamp": "t"},
            {"ordinal": "002", "index": 11, "batch_id": "002_t", "timestamp": "t"},
            {"ordinal": "003", "index": 12, "batch_id": "003_t", "timestamp": "t"},
            {"ordinal": "004", "index": 13, "batch_id": "004_t", "timestamp": "t"},
            {"ordinal": "005", "index": 14, "batch_id": "005_t", "timestamp": "t"},
        ]
        self.backend._get_stash_list = MagicMock(return_value=stashes)

        dropped = []

        def run_side_effect(args, check=True, capture_output=True):
            if args[:2] == ["stash", "drop"]:
                dropped.append(args[2])
            return cp(["git"] + args, 0, stdout="")

        self.backend._run_git = MagicMock(side_effect=run_side_effect)
        deleted = self.backend.prune_snapshots(keep_count=2)

        self.assertEqual(deleted, 3)
        # Drop highest stash indices first among to_delete to avoid shifting
        self.assertEqual(dropped, ["stash@{12}", "stash@{11}", "stash@{10}"])

    def test_cleanup_snapshots_drops_older_than_cutoff_and_skips_bad_timestamps(self):
        stashes = [
            {"batch_id": "001_20200101T000000", "timestamp": "20200101T000000", "index": 1, "ordinal": "001"},
            {"batch_id": "002_20990101T000000", "timestamp": "20990101T000000", "index": 2, "ordinal": "002"},
            {"batch_id": "003_bad", "timestamp": "bad", "index": 3, "ordinal": "003"},
        ]
        self.backend._get_stash_list = MagicMock(return_value=stashes)

        dropped = []

        def run_side_effect(args, check=True, capture_output=True):
            if args[:2] == ["stash", "drop"]:
                dropped.append(args[2])
            return cp(["git"] + args, 0, stdout="")

        # Fix 'now' so older_than_days comparison is stable.
        fixed_now = datetime(2024, 1, 10, 0, 0, 0, tzinfo=timezone.utc)
        with patch("aye.model.snapshot.git_backend.datetime") as dt:
            dt.now.return_value = fixed_now
            dt.strptime = datetime.strptime
            self.backend._run_git = MagicMock(side_effect=run_side_effect)
            deleted = self.backend.cleanup_snapshots(older_than_days=30)

        self.assertEqual(deleted, 1)
        self.assertEqual(dropped, ["stash@{1}"])


if __name__ == "__main__":
    unittest.main()
