import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch, MagicMock

from prompt_toolkit.document import Document

from aye.plugins.at_file_completer import AtFileCompleter, AtFileCompleterPlugin


class TestAtFileCompleter(TestCase):
    def setUp(self):
        # Create a temporary directory structure for testing
        self.temp_dir = tempfile.mkdtemp()
        self.project_root = Path(self.temp_dir)
        
        # Create test files
        (self.project_root / "main.py").write_text("# main")
        (self.project_root / "utils.py").write_text("# utils")
        (self.project_root / "config.json").write_text("{}")
        
        # Create subdirectory with files
        src_dir = self.project_root / "src"
        src_dir.mkdir()
        (src_dir / "app.py").write_text("# app")
        (src_dir / "helpers.py").write_text("# helpers")
        
        # Create ignored directory (should be skipped)
        git_dir = self.project_root / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("git config")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir)

    def test_completer_init_with_defaults(self):
        completer = AtFileCompleter()
        self.assertEqual(completer.project_root, Path.cwd())
        self.assertFalse(completer._cache_valid)

    def test_completer_init_with_project_root(self):
        completer = AtFileCompleter(project_root=self.project_root)
        self.assertEqual(completer.project_root, self.project_root)

    def test_completer_init_with_file_cache(self):
        cache = ["file1.py", "file2.py"]
        completer = AtFileCompleter(file_cache=cache)
        self.assertTrue(completer._cache_valid)
        self.assertEqual(completer._file_cache, cache)

    def test_get_project_files_builds_cache(self):
        completer = AtFileCompleter(project_root=self.project_root)
        files = completer._get_project_files()
        
        self.assertTrue(completer._cache_valid)
        self.assertIn("main.py", files)
        self.assertIn("utils.py", files)
        self.assertIn("config.json", files)
        self.assertIn(str(Path("src/app.py")), files)
        self.assertIn(str(Path("src/helpers.py")), files)
        # .git should be ignored
        self.assertNotIn(".git/config", files)

    def test_get_project_files_uses_cache(self):
        cache = ["cached.py"]
        completer = AtFileCompleter(project_root=self.project_root, file_cache=cache)
        files = completer._get_project_files()
        
        self.assertEqual(files, cache)

    def test_invalidate_cache(self):
        completer = AtFileCompleter(project_root=self.project_root, file_cache=["old.py"])
        self.assertTrue(completer._cache_valid)
        
        completer.invalidate_cache()
        
        self.assertFalse(completer._cache_valid)
        self.assertIsNone(completer._file_cache)

    def test_completions_no_at_symbol(self):
        completer = AtFileCompleter(project_root=self.project_root)
        doc = Document("some text without at")
        
        completions = list(completer.get_completions(doc, None))
        
        self.assertEqual(completions, [])

    def test_completions_at_in_middle_of_word_ignored(self):
        completer = AtFileCompleter(project_root=self.project_root)
        doc = Document("email@example.com")
        
        completions = list(completer.get_completions(doc, None))
        
        self.assertEqual(completions, [])

    def test_completions_at_start_of_line(self):
        completer = AtFileCompleter(project_root=self.project_root)
        doc = Document("@main")
        
        completions = list(completer.get_completions(doc, None))
        
        self.assertTrue(len(completions) > 0)
        completion_texts = [c.text for c in completions]
        self.assertIn("main.py", completion_texts)

    def test_completions_at_after_space(self):
        completer = AtFileCompleter(project_root=self.project_root)
        doc = Document("update @main")
        
        completions = list(completer.get_completions(doc, None))
        
        self.assertTrue(len(completions) > 0)
        completion_texts = [c.text for c in completions]
        self.assertIn("main.py", completion_texts)

    def test_completions_empty_partial_shows_files(self):
        completer = AtFileCompleter(project_root=self.project_root)
        doc = Document("update @")
        
        completions = list(completer.get_completions(doc, None))
        
        # Should show up to 20 files alphabetically
        self.assertTrue(len(completions) > 0)
        self.assertTrue(len(completions) <= 20)

    def test_completions_partial_path_matching(self):
        completer = AtFileCompleter(project_root=self.project_root)
        doc = Document("update @src/app")
        
        completions = list(completer.get_completions(doc, None))
        
        completion_texts = [c.text for c in completions]
        self.assertIn(str(Path("src/app.py")), completion_texts)

    def test_completions_filename_prefix_match(self):
        completer = AtFileCompleter(project_root=self.project_root)
        doc = Document("update @help")
        
        completions = list(completer.get_completions(doc, None))
        
        completion_texts = [c.text for c in completions]
        self.assertIn(str(Path("src/helpers.py")), completion_texts)

    def test_completions_substring_match(self):
        completer = AtFileCompleter(project_root=self.project_root)
        doc = Document("update @onfig")  # substring of config.json
        
        completions = list(completer.get_completions(doc, None))
        
        completion_texts = [c.text for c in completions]
        self.assertIn("config.json", completion_texts)

    def test_completions_stops_at_space(self):
        completer = AtFileCompleter(project_root=self.project_root)
        doc = Document("update @main.py and then")
        
        completions = list(completer.get_completions(doc, None))
        
        # Should not complete because there's a space after the partial
        self.assertEqual(completions, [])

    def test_completions_case_insensitive(self):
        completer = AtFileCompleter(project_root=self.project_root)
        doc = Document("update @MAIN")
        
        completions = list(completer.get_completions(doc, None))
        
        completion_texts = [c.text for c in completions]
        self.assertIn("main.py", completion_texts)

    def test_completions_fuzzy_fallback(self):
        """Test that fuzzy matching is attempted when no exact matches found."""
        completer = AtFileCompleter(project_root=self.project_root)
        doc = Document("update @mian")  # typo - should fuzzy match to main.py
        
        completions = list(completer.get_completions(doc, None))
        
        # If rapidfuzz is installed, should get fuzzy matches
        # If not installed, should gracefully return empty
        # Either way, should not raise an exception
        completion_texts = [c.text for c in completions]
        
        # Check if rapidfuzz is available
        try:
            import rapidfuzz
            # If rapidfuzz is installed, we should get main.py as a fuzzy match
            self.assertIn("main.py", completion_texts)
        except ImportError:
            # If rapidfuzz is not installed, no completions expected
            self.assertEqual(completions, [])

    def test_completions_fuzzy_fallback_without_rapidfuzz(self):
        """Test graceful handling when rapidfuzz is not available."""
        completer = AtFileCompleter(project_root=self.project_root)
        doc = Document("update @xyznonexistent")
        
        # Mock the import to raise ImportError
        import sys
        original_modules = sys.modules.copy()
        
        # Temporarily remove rapidfuzz from sys.modules if present
        for mod_name in list(sys.modules.keys()):
            if mod_name.startswith('rapidfuzz'):
                del sys.modules[mod_name]
        
        # Mock the import to fail
        with patch.dict('sys.modules', {'rapidfuzz': None}):
            with patch('builtins.__import__', side_effect=lambda name, *args: (_ for _ in ()).throw(ImportError()) if 'rapidfuzz' in name else original_modules.get(name)):
                # This should not raise, just return empty
                completions = list(completer.get_completions(doc, None))
                # No exact matches and no fuzzy (import fails) = empty
                self.assertEqual(completions, [])


class TestAtFileCompleterPlugin(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.project_root = Path(self.temp_dir)
        
        # Create test files
        (self.project_root / "main.py").write_text("print('hello')")
        (self.project_root / "utils.py").write_text("def helper(): pass")
        
        src_dir = self.project_root / "src"
        src_dir.mkdir()
        (src_dir / "app.py").write_text("# application code")
        
        self.plugin = AtFileCompleterPlugin()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir)

    def test_plugin_metadata(self):
        self.assertEqual(self.plugin.name, "at_file_completer")
        self.assertEqual(self.plugin.version, "1.0.0")
        self.assertEqual(self.plugin.premium, "free")

    def test_plugin_init(self):
        cfg = {"debug": False, "verbose": False}
        self.plugin.init(cfg)
        
        self.assertFalse(self.plugin.debug)
        self.assertFalse(self.plugin.verbose)

    def test_get_at_file_completer_command(self):
        self.plugin.init({})
        
        result = self.plugin.on_command("get_at_file_completer", {
            "project_root": str(self.project_root)
        })
        
        self.assertIsNotNone(result)
        self.assertIn("completer", result)
        self.assertIsInstance(result["completer"], AtFileCompleter)

    def test_get_at_file_completer_caches_instance(self):
        self.plugin.init({})
        
        result1 = self.plugin.on_command("get_at_file_completer", {
            "project_root": str(self.project_root)
        })
        result2 = self.plugin.on_command("get_at_file_completer", {
            "project_root": str(self.project_root)
        })
        
        # Same instance should be returned
        self.assertIs(result1["completer"], result2["completer"])

    def test_get_at_file_completer_new_instance_for_different_root(self):
        self.plugin.init({})
        
        result1 = self.plugin.on_command("get_at_file_completer", {
            "project_root": str(self.project_root)
        })
        result2 = self.plugin.on_command("get_at_file_completer", {
            "project_root": "/different/path"
        })
        
        # Different instances for different roots
        self.assertIsNot(result1["completer"], result2["completer"])

    def test_invalidate_file_cache_command(self):
        self.plugin.init({})
        
        # First get a completer to create the instance
        self.plugin.on_command("get_at_file_completer", {
            "project_root": str(self.project_root)
        })
        
        result = self.plugin.on_command("invalidate_file_cache", {})
        
        self.assertEqual(result, {"status": "cache_invalidated"})

    def test_invalidate_file_cache_no_completer(self):
        self.plugin.init({})
        
        # Call without creating completer first
        result = self.plugin.on_command("invalidate_file_cache", {})
        
        self.assertEqual(result, {"status": "cache_invalidated"})

    def test_has_at_references_true(self):
        self.plugin.init({})
        
        result = self.plugin.on_command("has_at_references", {
            "text": "update @main.py with new code"
        })
        
        self.assertTrue(result["has_references"])

    def test_has_at_references_at_start(self):
        self.plugin.init({})
        
        result = self.plugin.on_command("has_at_references", {
            "text": "@main.py needs updating"
        })
        
        self.assertTrue(result["has_references"])

    def test_has_at_references_false(self):
        self.plugin.init({})
        
        result = self.plugin.on_command("has_at_references", {
            "text": "just a normal prompt"
        })
        
        self.assertFalse(result["has_references"])

    def test_has_at_references_email_ignored(self):
        self.plugin.init({})
        
        result = self.plugin.on_command("has_at_references", {
            "text": "send to user@example.com"
        })
        
        self.assertFalse(result["has_references"])

    def test_parse_at_references_no_references(self):
        self.plugin.init({})
        
        result = self.plugin.on_command("parse_at_references", {
            "text": "just a normal prompt",
            "project_root": str(self.project_root)
        })
        
        self.assertIsNone(result)

    def test_parse_at_references_single_file(self):
        self.plugin.init({})
        
        result = self.plugin.on_command("parse_at_references", {
            "text": "update @main.py with logging",
            "project_root": str(self.project_root)
        })
        
        self.assertIsNotNone(result)
        self.assertIn("main.py", result["references"])
        self.assertIn("main.py", result["expanded_files"])
        self.assertIn("main.py", result["file_contents"])
        self.assertEqual(result["file_contents"]["main.py"], "print('hello')")
        self.assertIn("cleaned_prompt", result)

    def test_parse_at_references_multiple_files(self):
        self.plugin.init({})
        
        result = self.plugin.on_command("parse_at_references", {
            "text": "refactor @main.py and @utils.py",
            "project_root": str(self.project_root)
        })
        
        self.assertIsNotNone(result)
        self.assertEqual(len(result["references"]), 2)
        self.assertIn("main.py", result["file_contents"])
        self.assertIn("utils.py", result["file_contents"])

    def test_parse_at_references_with_path(self):
        self.plugin.init({})
        
        result = self.plugin.on_command("parse_at_references", {
            "text": "update @src/app.py",
            "project_root": str(self.project_root)
        })
        
        self.assertIsNotNone(result)
        self.assertIn("src/app.py", result["references"])
        self.assertIn("src/app.py", result["file_contents"])

    def test_parse_at_references_file_not_found(self):
        self.plugin.init({})
        
        result = self.plugin.on_command("parse_at_references", {
            "text": "update @nonexistent.py",
            "project_root": str(self.project_root)
        })
        
        self.assertIn("error", result)
        self.assertIn("nonexistent.py", result["references"])

    def test_parse_at_references_cleaned_prompt(self):
        self.plugin.init({})
        
        result = self.plugin.on_command("parse_at_references", {
            "text": "please update @main.py with better code",
            "project_root": str(self.project_root)
        })
        
        # The @main.py should be removed from cleaned prompt
        self.assertNotIn("@main.py", result["cleaned_prompt"])
        self.assertIn("please", result["cleaned_prompt"])
        self.assertIn("better code", result["cleaned_prompt"])

    def test_parse_at_references_at_start_of_text(self):
        self.plugin.init({})
        
        result = self.plugin.on_command("parse_at_references", {
            "text": "@main.py needs updating",
            "project_root": str(self.project_root)
        })
        
        self.assertIsNotNone(result)
        self.assertIn("main.py", result["references"])

    def test_parse_at_references_wildcard_not_supported_in_at_syntax(self):
        """Wildcards like @*.py are not supported in the @reference syntax.
        
        The regex pattern only matches word characters, dots, slashes, dashes, and underscores.
        Asterisks are not included, so @*.py won't be recognized as a reference.
        Use the 'with' command syntax for wildcards instead: 'with *.py: prompt'
        """
        self.plugin.init({})
        
        result = self.plugin.on_command("parse_at_references", {
            "text": "update @*.py",
            "project_root": str(self.project_root)
        })
        
        # @*.py is not recognized as a valid reference due to the * character
        # The regex pattern r'(?:^|\s)@([\w./\-_]+)' doesn't include *
        self.assertIsNone(result)

    def test_unknown_command_returns_none(self):
        self.plugin.init({})
        
        result = self.plugin.on_command("unknown_command", {})
        
        self.assertIsNone(result)

    def test_parse_at_references_empty_text(self):
        self.plugin.init({})
        
        result = self.plugin.on_command("parse_at_references", {
            "text": "",
            "project_root": str(self.project_root)
        })
        
        self.assertIsNone(result)

    def test_has_at_references_empty_text(self):
        self.plugin.init({})
        
        result = self.plugin.on_command("has_at_references", {
            "text": ""
        })
        
        self.assertFalse(result["has_references"])


class TestAtFileCompleterEdgeCases(TestCase):
    """Test edge cases and error handling."""

    def test_completer_handles_permission_error(self):
        # Test that completer handles errors gracefully
        with patch('os.walk', side_effect=PermissionError("Access denied")):
            completer = AtFileCompleter(project_root=Path("/some/path"))
            files = completer._get_project_files()
            
            # Should return empty list, not raise
            self.assertEqual(files, [])

    def test_plugin_read_file_encoding_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            
            # Create a file with binary content that can't be decoded as UTF-8
            binary_file = project_root / "binary.bin"
            binary_file.write_bytes(b'\x80\x81\x82')
            
            plugin = AtFileCompleterPlugin()
            plugin.init({"verbose": False})
            
            # This should handle the encoding error gracefully
            result = plugin.on_command("parse_at_references", {
                "text": "@binary.bin",
                "project_root": str(project_root)
            })
            
            # Should return error since file couldn't be read
            self.assertIn("error", result)

    def test_completer_relative_path_error(self):
        # Test handling of paths that can't be made relative
        completer = AtFileCompleter(project_root=Path("/project"))
        completer._file_cache = None
        completer._cache_valid = False
        
        with patch('os.walk') as mock_walk:
            mock_walk.return_value = [
                ("/project", [], ["file.py"])
            ]
            
            files = completer._get_project_files()
            self.assertIn("file.py", files)

    def test_parse_at_references_special_characters_in_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            
            # Create file with underscores and dashes
            (project_root / "my-file_v2.py").write_text("# content")
            
            plugin = AtFileCompleterPlugin()
            plugin.init({})
            
            result = plugin.on_command("parse_at_references", {
                "text": "update @my-file_v2.py",
                "project_root": str(project_root)
            })
            
            self.assertIsNotNone(result)
            self.assertIn("my-file_v2.py", result["references"])
