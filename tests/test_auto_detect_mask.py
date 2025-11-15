import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
import pathspec

from aye.plugins.auto_detect_mask import AutoDetectMaskPlugin

class TestAutoDetectMaskPlugin(TestCase):
    def setUp(self):
        self.plugin = AutoDetectMaskPlugin()
        self.plugin.init({})
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

        # Create a file structure
        (self.root / "main.py").write_text("# python")
        (self.root / "utils.py").write_text("# python")
        (self.root / "app.js").write_text("// javascript")
        (self.root / "style.css").write_text("/* css */")
        (self.root / "README.md").write_text("# markdown")
        (self.root / "data.json").write_text("{}")
        
        # Binary file
        self.binary_file = self.root / "binary.dat"
        self.binary_file.write_text(b"hello\0world".decode('latin-1'))
        
        # Ignored files
        (self.root / ".gitignore").write_text("*.css\nignored/\n")
        self.ignored_dir = self.root / "ignored"
        self.ignored_dir.mkdir()
        (self.ignored_dir / "ignored.py").write_text("# ignored")
        
        # Hidden dir
        self.hidden_dir = self.root / ".hidden"
        self.hidden_dir.mkdir()
        (self.hidden_dir / "hidden.py").write_text("# hidden")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_is_binary(self):
        text_file = self.root / "main.py"
        self.assertTrue(self.plugin._is_binary(self.binary_file))
        self.assertFalse(self.plugin._is_binary(text_file))

    def test_load_gitignore(self):
        spec = self.plugin._load_gitignore(self.root)
        self.assertIsInstance(spec, pathspec.PathSpec)
        self.assertTrue(spec.match_file("style.css"))
        self.assertTrue(spec.match_file("ignored/some_file.txt"))
        self.assertFalse(spec.match_file("main.py"))

    def test_process_file(self):
        # Source file
        self.assertEqual(self.plugin._process_file(self.root / "main.py"), "py")
        # Non-source file
        self.assertIsNone(self.plugin._process_file(self.root / "binary.dat"))
        # Binary file (mocked as our heuristic is simple)
        with patch.object(self.plugin, '_is_binary', return_value=True):
            self.assertIsNone(self.plugin._process_file(self.root / "main.py"))

    def test_auto_detect_mask_top_extensions(self):
        mask = self.plugin.auto_detect_mask(project_root=str(self.root))
        # Expected: py (2), js (1), md (1), json (1). css is ignored.
        # Sorted by frequency: py, then others.
        # The order of js, md, json is not guaranteed.
        self.assertIn("*.py", mask)
        self.assertTrue(mask.startswith("*.py"))
        
        parts = set(mask.split(','))
        self.assertEqual(len(parts), 4)
        self.assertIn("*.py", parts)
        self.assertIn("*.js", parts)
        self.assertIn("*.md", parts)
        self.assertIn("*.json", parts)

    def test_auto_detect_mask_with_max_exts(self):
        mask = self.plugin.auto_detect_mask(project_root=str(self.root), max_exts=2)
        # Expected: py (2), then one of js, md, json
        self.assertIn("*.py", mask)
        self.assertEqual(len(mask.split(',')), 2)

    def test_auto_detect_mask_no_source_files(self):
        empty_dir = self.root / "empty"
        empty_dir.mkdir()
        mask = self.plugin.auto_detect_mask(project_root=str(empty_dir), default_mask="*.sh")
        self.assertEqual(mask, "*.sh")

    def test_auto_detect_mask_invalid_dir(self):
        with self.assertRaises(ValueError):
            self.plugin.auto_detect_mask(project_root="non_existent_dir")

    def test_on_command_handler(self):
        params = {"project_root": str(self.root)}
        result = self.plugin.on_command("auto_detect_mask", params)
        self.assertIn("mask", result)
        self.assertIn("*.py", result["mask"])
        
        # Test non-matching command
        result_none = self.plugin.on_command("other_command", {})
        self.assertIsNone(result_none)
