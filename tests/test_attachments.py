"""Tests for `aye.model.attachments`.

Covers the Phase 1 foundation module: constants, the `ImageAttachment`
dataclass, pattern/path classification helpers, MIME detection, and the
`load_image_attachment` loader.

Target: >90% line coverage of `src/aye/model/attachments.py`.
"""

import base64
import dataclasses
from pathlib import Path
from unittest.mock import patch

import pytest

from aye.model import attachments as att
from aye.model.attachments import (
    IMAGE_EXTENSIONS,
    IMAGE_MAX_BYTES,
    ImageAttachment,
    _is_image_path,
    _is_image_targeted_pattern,
    _relative_name,
    detect_mime_type,
    load_image_attachment,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_image_extensions_is_frozenset(self):
        assert isinstance(IMAGE_EXTENSIONS, frozenset)

    def test_image_extensions_contains_expected_types(self):
        for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
            assert ext in IMAGE_EXTENSIONS

    def test_image_extensions_are_lowercase(self):
        for ext in IMAGE_EXTENSIONS:
            assert ext == ext.lower()
            assert ext.startswith(".")

    def test_image_max_bytes_is_positive_int(self):
        assert isinstance(IMAGE_MAX_BYTES, int)
        assert IMAGE_MAX_BYTES > 0


# ---------------------------------------------------------------------------
# ImageAttachment dataclass
# ---------------------------------------------------------------------------

class TestImageAttachment:
    def test_construction(self):
        a = ImageAttachment(
            file_name="foo.png",
            mime_type="image/png",
            data_b64="AAAA",
            bytes_size=3,
        )
        assert a.file_name == "foo.png"
        assert a.mime_type == "image/png"
        assert a.data_b64 == "AAAA"
        assert a.bytes_size == 3

    def test_is_frozen(self):
        a = ImageAttachment(
            file_name="foo.png",
            mime_type="image/png",
            data_b64="AAAA",
            bytes_size=3,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            a.file_name = "bar.png"  # type: ignore[misc]

    def test_equality_by_value(self):
        a = ImageAttachment("f.png", "image/png", "AAAA", 3)
        b = ImageAttachment("f.png", "image/png", "AAAA", 3)
        c = ImageAttachment("f.png", "image/png", "AAAB", 3)
        assert a == b
        assert a != c

    def test_hashable(self):
        a = ImageAttachment("f.png", "image/png", "AAAA", 3)
        # frozen=True dataclasses are hashable
        assert isinstance(hash(a), int)


# ---------------------------------------------------------------------------
# _is_image_targeted_pattern
# ---------------------------------------------------------------------------

class TestIsImageTargetedPattern:
    def test_empty_string(self):
        assert _is_image_targeted_pattern("") is False

    def test_trailing_forward_slash_is_not_targeted(self):
        assert _is_image_targeted_pattern("dir/") is False
        assert _is_image_targeted_pattern("some/deep/dir/") is False

    def test_trailing_backslash_is_not_targeted(self):
        assert _is_image_targeted_pattern("dir\\") is False

    def test_no_suffix(self):
        assert _is_image_targeted_pattern("README") is False
        assert _is_image_targeted_pattern("src/main") is False

    def test_wildcard_in_suffix_is_not_targeted(self):
        assert _is_image_targeted_pattern("*.*") is False
        assert _is_image_targeted_pattern("foo.*") is False
        assert _is_image_targeted_pattern("foo.?") is False

    def test_recursive_glob_without_image_ext(self):
        # Path("src/**/*").suffix is empty
        assert _is_image_targeted_pattern("src/**/*") is False

    def test_each_image_extension_targeted(self):
        for ext in IMAGE_EXTENSIONS:
            assert _is_image_targeted_pattern(f"*{ext}") is True

    def test_case_insensitive(self):
        assert _is_image_targeted_pattern("*.PNG") is True
        assert _is_image_targeted_pattern("*.Jpg") is True
        assert _is_image_targeted_pattern("screenshot.PNG") is True

    def test_nested_image_patterns(self):
        assert _is_image_targeted_pattern("screenshots/*.jpg") is True
        assert _is_image_targeted_pattern("assets/**/*.webp") is True

    def test_direct_image_filename(self):
        assert _is_image_targeted_pattern("screenshot.png") is True

    def test_non_image_extension(self):
        assert _is_image_targeted_pattern("*.py") is False
        assert _is_image_targeted_pattern("src/**/*.md") is False
        assert _is_image_targeted_pattern("main.py") is False


# ---------------------------------------------------------------------------
# _is_image_path
# ---------------------------------------------------------------------------

class TestIsImagePath:
    def test_image_path(self):
        assert _is_image_path(Path("a.png")) is True
        assert _is_image_path(Path("some/dir/a.jpg")) is True
        assert _is_image_path(Path("a.webp")) is True

    def test_non_image_path(self):
        assert _is_image_path(Path("a.py")) is False
        assert _is_image_path(Path("README.md")) is False

    def test_no_suffix(self):
        assert _is_image_path(Path("Makefile")) is False

    def test_case_insensitive(self):
        assert _is_image_path(Path("PHOTO.PNG")) is True
        assert _is_image_path(Path("Photo.JpG")) is True


# ---------------------------------------------------------------------------
# detect_mime_type
# ---------------------------------------------------------------------------

class TestDetectMimeType:
    def test_png(self):
        assert detect_mime_type(Path("foo.png")) == "image/png"

    def test_jpg(self):
        assert detect_mime_type(Path("foo.jpg")) == "image/jpeg"

    def test_jpeg(self):
        assert detect_mime_type(Path("foo.jpeg")) == "image/jpeg"

    def test_gif(self):
        assert detect_mime_type(Path("foo.gif")) == "image/gif"

    def test_webp(self):
        # webp may not be in every stdlib mimetypes db; fallback covers it.
        assert detect_mime_type(Path("foo.webp")) == "image/webp"

    def test_bmp(self):
        assert detect_mime_type(Path("foo.bmp")) == "image/bmp"

    def test_uppercase_extension(self):
        # mimetypes is case-insensitive on suffix
        assert detect_mime_type(Path("FOO.PNG")) == "image/png"

    def test_fallback_used_when_mimetypes_unresolved(self):
        """When `mimetypes.guess_type` returns None, the fallback map is used."""
        with patch("aye.model.attachments.mimetypes.guess_type",
                   return_value=(None, None)):
            assert detect_mime_type(Path("foo.png")) == "image/png"
            assert detect_mime_type(Path("foo.webp")) == "image/webp"

    def test_unknown_extension_returns_octet_stream(self):
        with patch("aye.model.attachments.mimetypes.guess_type",
                   return_value=(None, None)):
            assert detect_mime_type(Path("foo.xyz")) == "application/octet-stream"


# ---------------------------------------------------------------------------
# _relative_name
# ---------------------------------------------------------------------------

class TestRelativeName:
    def test_file_under_root(self, tmp_path):
        root = tmp_path
        sub = root / "sub" / "deep"
        sub.mkdir(parents=True)
        f = sub / "photo.png"
        f.write_bytes(b"x")

        name = _relative_name(f, root)
        # POSIX-style path expected
        assert name == "sub/deep/photo.png"

    def test_file_directly_in_root(self, tmp_path):
        f = tmp_path / "a.png"
        f.write_bytes(b"x")
        assert _relative_name(f, tmp_path) == "a.png"

    def test_file_outside_root_returns_basename(self, tmp_path):
        # `other` is not under `tmp_path`
        other_dir = tmp_path.parent / (tmp_path.name + "_sibling")
        other_dir.mkdir()
        try:
            f = other_dir / "photo.png"
            f.write_bytes(b"x")
            assert _relative_name(f, tmp_path) == "photo.png"
        finally:
            f.unlink()
            other_dir.rmdir()

    def test_resolve_failure_falls_back_to_basename(self, tmp_path):
        f = tmp_path / "a.png"
        f.write_bytes(b"x")

        # Force the resolve call inside _relative_name to raise OSError.
        original_resolve = Path.resolve

        def fake_resolve(self, *args, **kwargs):
            raise OSError("resolve failed")

        with patch.object(Path, "resolve", fake_resolve):
            assert _relative_name(f, tmp_path) == "a.png"

        # Sanity check the original still works
        _ = original_resolve


# ---------------------------------------------------------------------------
# load_image_attachment
# ---------------------------------------------------------------------------

PNG_BYTES = bytes.fromhex(
    # Minimal valid-looking PNG header plus a few bytes of payload.
    # Content correctness doesn't matter; the loader treats it as opaque bytes.
    "89504E470D0A1A0A0000000D49484452"
    "00000001000000010806000000"
    "1F15C4890000000A49444154789C6300010000000500010D0A2DB40000000049454E44AE426082"
)


class TestLoadImageAttachment:
    def test_returns_dict_with_expected_keys(self, tmp_path):
        f = tmp_path / "photo.png"
        f.write_bytes(PNG_BYTES)

        result = load_image_attachment(f, tmp_path)

        assert isinstance(result, dict)
        assert set(result.keys()) == {
            "file_name", "mime_type", "data_b64", "bytes_size"
        }

    def test_file_name_is_relative_posix(self, tmp_path):
        sub = tmp_path / "shots"
        sub.mkdir()
        f = sub / "a.png"
        f.write_bytes(b"x")

        result = load_image_attachment(f, tmp_path)
        assert result["file_name"] == "shots/a.png"

    def test_mime_type_correct(self, tmp_path):
        f = tmp_path / "a.jpg"
        f.write_bytes(b"x")
        result = load_image_attachment(f, tmp_path)
        assert result["mime_type"] == "image/jpeg"

    def test_bytes_size_matches_raw_length(self, tmp_path):
        f = tmp_path / "a.png"
        f.write_bytes(PNG_BYTES)
        result = load_image_attachment(f, tmp_path)
        assert result["bytes_size"] == len(PNG_BYTES)

    def test_base64_round_trip(self, tmp_path):
        f = tmp_path / "a.png"
        f.write_bytes(PNG_BYTES)

        result = load_image_attachment(f, tmp_path)
        decoded = base64.b64decode(result["data_b64"].encode("ascii"))
        assert decoded == PNG_BYTES

    def test_data_b64_is_ascii_str(self, tmp_path):
        f = tmp_path / "a.png"
        f.write_bytes(PNG_BYTES)

        result = load_image_attachment(f, tmp_path)
        assert isinstance(result["data_b64"], str)
        # ASCII-only check
        result["data_b64"].encode("ascii")

    def test_nonexistent_path_raises_filenotfound(self, tmp_path):
        missing = tmp_path / "does_not_exist.png"
        with pytest.raises(FileNotFoundError):
            load_image_attachment(missing, tmp_path)

    def test_directory_path_raises_filenotfound(self, tmp_path):
        d = tmp_path / "dir.png"  # name has image suffix but it's a directory
        d.mkdir()
        with pytest.raises(FileNotFoundError):
            load_image_attachment(d, tmp_path)

    def test_non_image_extension_raises_valueerror(self, tmp_path):
        f = tmp_path / "notes.txt"
        f.write_text("hi")
        with pytest.raises(ValueError) as exc_info:
            load_image_attachment(f, tmp_path)
        assert "supported image" in str(exc_info.value).lower() \
            or "not a supported image" in str(exc_info.value).lower()

    def test_oversize_image_rejected(self, tmp_path):
        f = tmp_path / "big.png"
        # Write more than the patched cap
        f.write_bytes(b"x" * 100)

        with patch("aye.model.attachments.IMAGE_MAX_BYTES", 10):
            with pytest.raises(ValueError) as exc_info:
                load_image_attachment(f, tmp_path)
            msg = str(exc_info.value)
            assert "exceeds" in msg or "limit" in msg

    def test_size_at_limit_is_allowed(self, tmp_path):
        f = tmp_path / "ok.png"
        payload = b"x" * 10
        f.write_bytes(payload)

        with patch("aye.model.attachments.IMAGE_MAX_BYTES", 10):
            result = load_image_attachment(f, tmp_path)
            assert result["bytes_size"] == 10

    def test_stat_failure_raises_oserror(self, tmp_path):
        f = tmp_path / "a.png"
        f.write_bytes(b"x")

        original_stat = Path.stat

        def fake_stat(self, *args, **kwargs):
            # Only fail for our specific test file; let other Path.stat calls
            # (e.g. those used by exists()/is_file() if any) behave normally.
            if self == f:
                raise OSError("simulated stat failure")
            return original_stat(self, *args, **kwargs)

        with patch.object(Path, "stat", fake_stat):
            with pytest.raises(OSError) as exc_info:
                load_image_attachment(f, tmp_path)
            assert "stat" in str(exc_info.value).lower() \
                or "simulated" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# __all__ exposure
# ---------------------------------------------------------------------------

class TestPublicApi:
    def test_all_symbols_exported(self):
        for name in (
            "IMAGE_EXTENSIONS",
            "IMAGE_MAX_BYTES",
            "ImageAttachment",
            "_is_image_targeted_pattern",
            "_is_image_path",
            "detect_mime_type",
            "load_image_attachment",
        ):
            assert name in att.__all__
            assert hasattr(att, name)
