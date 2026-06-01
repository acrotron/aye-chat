"""Tests for Phase 2 image handling in the @file completer plugin.

Covers:
- Image extension detection (case-insensitive) in `@` references.
- Mixed source + image references.
- Glob/directory image expansion rule from `issue.md` Section 3.1.
- Ignore-pattern handling for images.
- Oversize-image rejection surfaced via `image_errors`.
- Response-shape additions: `attachments`, `has_image_references`,
  `has_source_references`, `image_errors`.
"""

import base64
import shutil
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from aye.plugins.at_file_completer import AtFileCompleterPlugin


# A small but non-trivial byte payload used as image content. Loader doesn't
# validate image headers, only extension and size.
_IMAGE_BYTES = bytes(range(256)) * 2  # 512 bytes, deterministic


class _ImageTestBase(TestCase):
    """Common setup for image-related plugin tests."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.project_root = Path(self.temp_dir)

        # Top-level source file and image
        (self.project_root / "main.py").write_text("print('hello')")
        (self.project_root / "screenshot.png").write_bytes(_IMAGE_BYTES)

        # src/ with mixed source + image content
        src = self.project_root / "src"
        src.mkdir()
        (src / "app.py").write_text("# app")
        (src / "ui.py").write_text("# ui")
        (src / "icon.png").write_bytes(_IMAGE_BYTES)

        # src/nested/ with deeper content
        nested = src / "nested"
        nested.mkdir()
        (nested / "deep.py").write_text("# deep")
        (nested / "deep.jpg").write_bytes(_IMAGE_BYTES)

        # screenshots/ \u2014 image-only directory
        shots = self.project_root / "screenshots"
        shots.mkdir()
        (shots / "a.jpg").write_bytes(_IMAGE_BYTES)
        (shots / "b.jpg").write_bytes(_IMAGE_BYTES)

        # design/ \u2014 nested image
        design = self.project_root / "design"
        design.mkdir()
        (design / "mockup.png").write_bytes(_IMAGE_BYTES)

        # Generic-extension test dir
        dir_mix = self.project_root / "dir"
        dir_mix.mkdir()
        (dir_mix / "notes.txt").write_text("hi")
        (dir_mix / "chart.png").write_bytes(_IMAGE_BYTES)

        self.plugin = AtFileCompleterPlugin()
        self.plugin.init({})

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _parse(self, text: str):
        return self.plugin.on_command(
            "parse_at_references",
            {"text": text, "project_root": str(self.project_root)},
        )


class TestSingleImageReference(_ImageTestBase):
    def test_direct_image_produces_attachment(self):
        result = self._parse("describe @screenshot.png")

        self.assertIsNotNone(result)
        self.assertEqual(result["file_contents"], {})
        self.assertEqual(len(result["attachments"]), 1)
        self.assertTrue(result["has_image_references"])
        self.assertFalse(result["has_source_references"])
        self.assertEqual(result["image_errors"], [])

    def test_attachment_dict_has_expected_keys(self):
        result = self._parse("describe @screenshot.png")

        att = result["attachments"][0]
        self.assertEqual(
            set(att.keys()),
            {"file_name", "mime_type", "data_b64", "bytes_size"},
        )
        self.assertEqual(att["file_name"], "screenshot.png")
        self.assertEqual(att["mime_type"], "image/png")
        self.assertEqual(att["bytes_size"], len(_IMAGE_BYTES))
        # Base64 round-trip
        decoded = base64.b64decode(att["data_b64"].encode("ascii"))
        self.assertEqual(decoded, _IMAGE_BYTES)

    def test_uppercase_image_extension_detected(self):
        # Rename screenshot.png \u2192 SHOT.PNG
        upper_path = self.project_root / "SHOT.PNG"
        upper_path.write_bytes(_IMAGE_BYTES)

        result = self._parse("describe @SHOT.PNG")
        self.assertEqual(len(result["attachments"]), 1)
        self.assertTrue(result["has_image_references"])
        self.assertEqual(result["attachments"][0]["mime_type"], "image/png")

    def test_cleaned_prompt_strips_image_reference(self):
        result = self._parse("please describe @screenshot.png now")
        self.assertNotIn("@screenshot.png", result["cleaned_prompt"])
        self.assertIn("please", result["cleaned_prompt"])
        self.assertIn("now", result["cleaned_prompt"])


class TestMixedReferences(_ImageTestBase):
    def test_source_and_image_both_populated(self):
        result = self._parse("review @src/ui.py using @design/mockup.png")

        self.assertIsNotNone(result)
        self.assertIn("src/ui.py", result["file_contents"])
        self.assertEqual(len(result["attachments"]), 1)
        self.assertEqual(result["attachments"][0]["file_name"], "design/mockup.png")
        self.assertTrue(result["has_image_references"])
        self.assertTrue(result["has_source_references"])

    def test_expanded_files_contains_both(self):
        result = self._parse("review @src/ui.py using @design/mockup.png")
        expanded = result["expanded_files"]
        self.assertIn("src/ui.py", expanded)
        self.assertIn("design/mockup.png", expanded)


class TestGlobImageRules(_ImageTestBase):
    def test_image_targeted_glob_includes_images(self):
        # @*.png should include screenshot.png
        result = self._parse("update @*.png")

        self.assertIsNotNone(result)
        names = {a["file_name"] for a in result["attachments"]}
        self.assertIn("screenshot.png", names)
        self.assertTrue(result["has_image_references"])

    def test_nested_image_targeted_glob_includes_images(self):
        result = self._parse("check @screenshots/*.jpg")

        self.assertIsNotNone(result)
        names = {a["file_name"] for a in result["attachments"]}
        self.assertIn("screenshots/a.jpg", names)
        self.assertIn("screenshots/b.jpg", names)

    def test_recursive_image_glob_includes_images(self):
        result = self._parse("audit @src/**/*.jpg")

        self.assertIsNotNone(result)
        names = {a["file_name"] for a in result["attachments"]}
        self.assertIn("src/nested/deep.jpg", names)

    def test_directory_ref_excludes_images(self):
        # @src/ should pull source files but NOT src/icon.png or src/nested/deep.jpg
        result = self._parse("analyze @src/")

        self.assertIsNotNone(result)
        self.assertEqual(result["attachments"], [])
        self.assertFalse(result["has_image_references"])
        # Source files should be present
        self.assertIn("src/app.py", result["file_contents"])
        self.assertIn("src/ui.py", result["file_contents"])
        self.assertIn("src/nested/deep.py", result["file_contents"])
        # Images explicitly excluded
        self.assertNotIn("src/icon.png", result["file_contents"])

    def test_implicit_directory_ref_excludes_images(self):
        # @src (no trailing slash) resolving to a directory \u2192 same as @src/
        result = self._parse("analyze @src")

        self.assertIsNotNone(result)
        self.assertEqual(result["attachments"], [])
        self.assertFalse(result["has_image_references"])
        self.assertIn("src/app.py", result["file_contents"])

    def test_generic_star_dot_star_excludes_images(self):
        # @dir/*.* should exclude images even though chart.png is in dir/
        result = self._parse("summarize @dir/*.*")

        self.assertIsNotNone(result)
        self.assertEqual(result["attachments"], [])
        self.assertFalse(result["has_image_references"])
        # Text file under the glob should be loaded
        self.assertIn("dir/notes.txt", result["file_contents"])

    def test_generic_recursive_glob_excludes_images(self):
        # @src/**/* should exclude images
        result = self._parse("refactor @src/**/*")

        self.assertIsNotNone(result)
        self.assertEqual(result["attachments"], [])
        # Source files present
        self.assertIn("src/app.py", result["file_contents"])
        self.assertIn("src/nested/deep.py", result["file_contents"])

    def test_direct_image_ref_in_subdir_always_included(self):
        # @dir/chart.png is a direct ref \u2192 included regardless of any directory rule.
        result = self._parse("explain @dir/chart.png")

        self.assertIsNotNone(result)
        names = {a["file_name"] for a in result["attachments"]}
        self.assertIn("dir/chart.png", names)
        self.assertTrue(result["has_image_references"])


class TestIgnoreHandlingForImages(_ImageTestBase):
    def test_image_under_ayeignore_not_loaded(self):
        # Add an .ayeignore that excludes the design/ directory
        (self.project_root / ".ayeignore").write_text("design/\n")

        result = self._parse("describe @design/mockup.png")

        # Either no attachments at all, or the design image is filtered out.
        if result is not None:
            names = {a["file_name"] for a in result.get("attachments", [])}
            self.assertNotIn("design/mockup.png", names)

    def test_image_under_gitignore_not_loaded_via_glob(self):
        (self.project_root / ".gitignore").write_text("screenshots/\n")

        result = self._parse("check @screenshots/*.jpg")

        if result is not None:
            names = {a["file_name"] for a in result.get("attachments", [])}
            self.assertNotIn("screenshots/a.jpg", names)
            self.assertNotIn("screenshots/b.jpg", names)


class TestImageErrors(_ImageTestBase):
    def test_oversize_image_surfaced_in_image_errors(self):
        # Patch the max-bytes constant low enough to fail screenshot.png (512 bytes)
        with patch("aye.model.attachments.IMAGE_MAX_BYTES", 100):
            result = self._parse("describe @screenshot.png")

        self.assertIsNotNone(result)
        self.assertEqual(result["attachments"], [])
        self.assertTrue(len(result["image_errors"]) >= 1)
        # Image refs were attempted \u2014 should still be reflected
        self.assertTrue(result["has_image_references"])
        # Since nothing was loaded, an error key should be surfaced
        self.assertIn("error", result)
        self.assertIn("image", result["error"].lower())

    def test_partial_failure_keeps_successful_images(self):
        # Create two images, only one is oversize via patched limit per file
        big = self.project_root / "big.png"
        big.write_bytes(b"x" * 1000)  # bigger than the patched limit below

        with patch("aye.model.attachments.IMAGE_MAX_BYTES", 600):
            # screenshot.png (512 bytes) loads; big.png (1000 bytes) fails.
            result = self._parse("check @screenshot.png and @big.png")

        self.assertIsNotNone(result)
        names = {a["file_name"] for a in result["attachments"]}
        self.assertIn("screenshot.png", names)
        self.assertNotIn("big.png", names)
        self.assertTrue(any("big.png" in err for err in result["image_errors"]))
        # Because at least one image loaded, we do NOT surface a top-level error
        self.assertNotIn("error", result)


class TestResponseShape(_ImageTestBase):
    def test_response_has_required_keys_on_success(self):
        result = self._parse("describe @screenshot.png")

        for key in (
            "references",
            "expanded_files",
            "file_contents",
            "attachments",
            "image_errors",
            "has_image_references",
            "has_source_references",
            "cleaned_prompt",
        ):
            self.assertIn(key, result)

    def test_no_references_returns_none(self):
        result = self._parse("just talking, no refs")
        self.assertIsNone(result)

    def test_only_source_refs_flags(self):
        result = self._parse("explain @main.py")
        self.assertTrue(result["has_source_references"])
        self.assertFalse(result["has_image_references"])
        self.assertEqual(result["attachments"], [])

    def test_only_image_refs_flags(self):
        result = self._parse("describe @screenshot.png")
        self.assertFalse(result["has_source_references"])
        self.assertTrue(result["has_image_references"])
        self.assertEqual(result["file_contents"], {})
