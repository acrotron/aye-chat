import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import os
import sys

try:
    from aye.controller.util import (
        find_project_root,
        discover_agents_file,
        _try_read_agents,
        is_truncated_json,
        AGENTS_FILENAME,
    )
except ImportError:
    project_root = Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(project_root))
    from aye.controller.util import (
        find_project_root,
        discover_agents_file,
        _try_read_agents,
        is_truncated_json,
        AGENTS_FILENAME,
    )


class TestFindProjectRoot(unittest.TestCase):

    def setUp(self):
        """Set up a temporary directory structure for testing."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()

        self.original_cwd = Path.cwd()
        self.test_cwd = self.root / "test_cwd"
        self.test_cwd.mkdir()
        os.chdir(self.test_cwd)

        # Structure 1: Project with .aye/file_index.json at its root
        self.project_a = self.root / "project_a"
        self.project_a_marker_dir = self.project_a / ".aye"
        self.project_a_marker_file = self.project_a_marker_dir / "file_index.json"
        self.project_a_deep_dir = self.project_a / "src" / "app"
        self.project_a_file = self.project_a_deep_dir / "main.py"

        self.project_a_marker_dir.mkdir(parents=True)
        self.project_a_marker_file.touch()
        self.project_a_deep_dir.mkdir(parents=True)
        self.project_a_file.touch()

        # Structure 2: No project markers
        self.project_b = self.root / "project_b"
        self.project_b_sub_dir = self.project_b / "another" / "dir"
        self.project_b_sub_dir.mkdir(parents=True)

    def tearDown(self):
        os.chdir(self.original_cwd)
        self.temp_dir.cleanup()

    def test_find_from_deep_subdirectory(self):
        found_root = find_project_root(self.project_a_deep_dir)
        self.assertEqual(found_root, self.project_a)

    def test_find_from_project_root_itself(self):
        found_root = find_project_root(self.project_a)
        self.assertEqual(found_root, self.project_a)

    def test_find_from_file_path(self):
        found_root = find_project_root(self.project_a_file)
        self.assertEqual(found_root, self.project_a)

    def test_no_marker_found_returns_cwd(self):
        found_root = find_project_root(self.project_b_sub_dir)
        self.assertEqual(found_root, self.test_cwd)

    def test_start_dir_does_not_exist(self):
        non_existent_path = self.project_a / "src" / "non_existent_file.txt"
        found_root = find_project_root(non_existent_path)
        self.assertEqual(found_root, self.project_a)

    def test_current_working_directory_is_not_changed(self):
        cwd_before = Path.cwd()
        self.assertEqual(cwd_before, self.test_cwd)
        find_project_root(self.project_a_deep_dir)
        cwd_after = Path.cwd()
        self.assertEqual(cwd_after, cwd_before)

    def test_no_start_path_uses_cwd(self):
        """When start_path is None, should search from cwd."""
        os.chdir(self.project_a_deep_dir)
        found_root = find_project_root()
        self.assertEqual(found_root, self.project_a)

    def test_no_start_path_no_marker_returns_cwd(self):
        """When start_path is None and no marker exists, should return cwd."""
        os.chdir(self.project_b_sub_dir)
        found_root = find_project_root()
        self.assertEqual(found_root, self.project_b_sub_dir)

    def test_start_path_is_non_existent_dir(self):
        """When start_path is a directory that doesn't exist, search from parent."""
        non_existent_dir = self.project_a / "src" / "does_not_exist"
        found_root = find_project_root(non_existent_dir)
        self.assertEqual(found_root, self.project_a)

    def test_start_path_string(self):
        """Should accept a string path."""
        found_root = find_project_root(str(self.project_a_deep_dir))
        self.assertEqual(found_root, self.project_a)


class TestDiscoverAgentsFile(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()

        # repo_root
        self.repo_root = self.root / "repo"
        self.repo_root.mkdir()

        # nested dir inside repo
        self.sub_dir = self.repo_root / "src" / "app"
        self.sub_dir.mkdir(parents=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_highest_precedence_aye_agents(self):
        """cwd/.aye/AGENTS.md should be found first."""
        aye_dir = self.sub_dir / ".aye"
        aye_dir.mkdir(parents=True)
        agents_file = aye_dir / AGENTS_FILENAME
        agents_file.write_text("aye agents content", encoding="utf-8")

        # Also place one at repo root to confirm it is NOT chosen
        (self.repo_root / AGENTS_FILENAME).write_text("repo agents", encoding="utf-8")

        result = discover_agents_file(self.sub_dir, self.repo_root)
        self.assertIsNotNone(result)
        path, contents = result
        self.assertEqual(path, agents_file)
        self.assertEqual(contents, "aye agents content")

    def test_walk_upward_finds_agents_in_parent(self):
        """Should walk upward and find AGENTS.md in an ancestor."""
        agents_file = self.repo_root / AGENTS_FILENAME
        agents_file.write_text("repo level agents", encoding="utf-8")

        result = discover_agents_file(self.sub_dir, self.repo_root)
        self.assertIsNotNone(result)
        path, contents = result
        self.assertEqual(path, agents_file)
        self.assertEqual(contents, "repo level agents")

    def test_walk_upward_finds_agents_in_cwd(self):
        """Should find AGENTS.md directly in cwd."""
        agents_file = self.sub_dir / AGENTS_FILENAME
        agents_file.write_text("local agents", encoding="utf-8")

        result = discover_agents_file(self.sub_dir, self.repo_root)
        self.assertIsNotNone(result)
        path, contents = result
        self.assertEqual(path, agents_file)
        self.assertEqual(contents, "local agents")

    def test_no_agents_found_returns_none(self):
        """Should return None when no AGENTS.md exists."""
        result = discover_agents_file(self.sub_dir, self.repo_root)
        self.assertIsNone(result)

    def test_cwd_equals_repo_root(self):
        """When cwd == repo_root and AGENTS.md is there, should find it."""
        agents_file = self.repo_root / AGENTS_FILENAME
        agents_file.write_text("at root", encoding="utf-8")

        result = discover_agents_file(self.repo_root, self.repo_root)
        self.assertIsNotNone(result)
        path, contents = result
        self.assertEqual(path, agents_file)
        self.assertEqual(contents, "at root")

    def test_cwd_equals_repo_root_no_agents(self):
        """When cwd == repo_root and no AGENTS.md, should return None."""
        result = discover_agents_file(self.repo_root, self.repo_root)
        self.assertIsNone(result)

    def test_walk_upward_finds_intermediate(self):
        """Should find AGENTS.md in an intermediate directory between cwd and repo_root."""
        intermediate = self.repo_root / "src"
        agents_file = intermediate / AGENTS_FILENAME
        agents_file.write_text("intermediate", encoding="utf-8")

        result = discover_agents_file(self.sub_dir, self.repo_root)
        self.assertIsNotNone(result)
        path, contents = result
        self.assertEqual(path, agents_file)
        self.assertEqual(contents, "intermediate")


class TestTryReadAgents(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_file_does_not_exist(self):
        path = self.root / "nonexistent.md"
        result = _try_read_agents(path, verbose=False)
        self.assertIsNone(result)

    def test_file_exists_and_readable(self):
        path = self.root / AGENTS_FILENAME
        path.write_text("hello agents", encoding="utf-8")
        result = _try_read_agents(path, verbose=False)
        self.assertIsNotNone(result)
        self.assertEqual(result, (path, "hello agents"))

    def test_file_exists_but_unreadable_verbose_false(self):
        """When file can't be read and verbose is False, returns None silently."""
        path = self.root / AGENTS_FILENAME
        path.write_text("data", encoding="utf-8")

        with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            result = _try_read_agents(path, verbose=False)
        self.assertIsNone(result)

    @patch("aye.controller.util.rprint", create=True)
    def test_file_exists_but_unreadable_verbose_true(self, *args):
        """When file can't be read and verbose is True, returns None and prints warning."""
        path = self.root / AGENTS_FILENAME
        path.write_text("data", encoding="utf-8")

        with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            with patch("rich.print") as mock_rprint:
                result = _try_read_agents(path, verbose=True)
                self.assertIsNone(result)
                mock_rprint.assert_called_once()
                call_arg = mock_rprint.call_args[0][0]
                self.assertIn("Warning", call_arg)
                self.assertIn(str(path), call_arg)


class TestIsTruncatedJson(unittest.TestCase):

    def test_empty_string(self):
        self.assertFalse(is_truncated_json(""))

    def test_none_like_empty(self):
        # empty after strip
        self.assertFalse(is_truncated_json("   "))

    def test_valid_object(self):
        self.assertFalse(is_truncated_json('{"key": "value"}'))

    def test_valid_object_with_whitespace(self):
        self.assertFalse(is_truncated_json('  {"key": "value"}  '))

    def test_valid_array(self):
        self.assertFalse(is_truncated_json('[1, 2, 3]'))

    def test_valid_array_with_whitespace(self):
        self.assertFalse(is_truncated_json('  [1, 2, 3]  '))

    def test_truncated_object(self):
        self.assertTrue(is_truncated_json('{"key": "val'))

    def test_truncated_array(self):
        self.assertTrue(is_truncated_json('[1, 2, 3'))

    def test_truncated_object_with_whitespace(self):
        self.assertTrue(is_truncated_json('  {"key": "val  '))

    def test_truncated_array_with_whitespace(self):
        self.assertTrue(is_truncated_json('  [1, 2  '))

    def test_not_json_at_all(self):
        self.assertFalse(is_truncated_json("hello world"))

    def test_not_json_plain_number(self):
        self.assertFalse(is_truncated_json("42"))

    def test_object_starts_but_ends_with_bracket(self):
        # starts with { but ends with ] — truncated
        self.assertTrue(is_truncated_json('{"a": [1, 2]'))

    def test_array_starts_but_ends_with_brace(self):
        # starts with [ but ends with } — truncated
        self.assertTrue(is_truncated_json('[{"a": 1}'))

    def test_complete_nested_object(self):
        self.assertFalse(is_truncated_json('{"a": {"b": [1, 2]}}'))

    def test_complete_nested_array(self):
        self.assertFalse(is_truncated_json('[{"a": 1}, {"b": 2}]'))


class TestDiscoverAgentsFileEdgeCases(unittest.TestCase):
    """Edge-case tests for discover_agents_file to cover remaining branches."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_aye_agents_not_a_file(self):
        """If .aye/AGENTS.md is a directory, skip it and continue search."""
        cwd = self.root / "project"
        cwd.mkdir()
        # Make .aye/AGENTS.md a directory, not a file
        (cwd / ".aye" / AGENTS_FILENAME).mkdir(parents=True)

        # Place a real one at cwd level
        real_agents = cwd / AGENTS_FILENAME
        real_agents.write_text("real content", encoding="utf-8")

        result = discover_agents_file(cwd, cwd)
        self.assertIsNotNone(result)
        path, contents = result
        self.assertEqual(path, real_agents)
        self.assertEqual(contents, "real content")

    def test_filesystem_root_boundary(self):
        """When repo_root is /, search should stop and return None."""
        # Use a temp dir with no AGENTS.md; set repo_root far above
        cwd = self.root / "deep" / "nested"
        cwd.mkdir(parents=True)
        result = discover_agents_file(cwd, Path("/"))
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
