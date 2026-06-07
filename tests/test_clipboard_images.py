"""Tests for `aye.model.clipboard_images`.

Covers the clipboard image reading module: exceptions, internal reading
strategies (Pillow, wl-paste, xclip), orchestration, filename
generation, and the public API surface.

Target: >90% line coverage of `src/aye/model/clipboard_images.py`.
"""

import base64
import io
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from aye.model import clipboard_images as mod
from aye.model.clipboard_images import (
    ClipboardImageError,
    ClipboardImageNotFoundError,
    ClipboardImageTooLargeError,
    ClipboardImageUnavailableError,
    _PNG_SIGNATURE,
    _SUBPROCESS_TIMEOUT,
    _any_linux_tool_available,
    _generate_filename,
    _is_linux,
    _read_pillow,
    _read_wl_paste,
    _read_xclip,
    clipboard_image_available,
    load_clipboard_image_attachment,
    read_clipboard_image_bytes,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Minimal valid PNG (1x1 transparent pixel)
_TINY_PNG = bytes.fromhex(
    "89504E470D0A1A0A0000000D49484452"
    "00000001000000010806000000"
    "1F15C4890000000A49444154789C6300010000000500010D0A2DB40000000049454E44AE426082"
)


def _make_completed_process(returncode=0, stdout=b"", stderr=b""):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr,
    )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_png_signature_is_bytes(self):
        assert isinstance(_PNG_SIGNATURE, bytes)

    def test_png_signature_length(self):
        assert len(_PNG_SIGNATURE) == 8

    def test_png_signature_matches_spec(self):
        assert _PNG_SIGNATURE == b"\x89PNG\r\n\x1a\n"

    def test_subprocess_timeout_is_positive(self):
        assert isinstance(_SUBPROCESS_TIMEOUT, (int, float))
        assert _SUBPROCESS_TIMEOUT > 0


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class TestExceptions:
    def test_base_exception_hierarchy(self):
        assert issubclass(ClipboardImageError, Exception)

    def test_unavailable_is_subclass(self):
        assert issubclass(ClipboardImageUnavailableError, ClipboardImageError)

    def test_not_found_is_subclass(self):
        assert issubclass(ClipboardImageNotFoundError, ClipboardImageError)

    def test_too_large_is_subclass(self):
        assert issubclass(ClipboardImageTooLargeError, ClipboardImageError)

    def test_can_catch_all_via_base(self):
        for exc_cls in (
            ClipboardImageUnavailableError,
            ClipboardImageNotFoundError,
            ClipboardImageTooLargeError,
        ):
            with pytest.raises(ClipboardImageError):
                raise exc_cls("test")


# ---------------------------------------------------------------------------
# _read_pillow
# ---------------------------------------------------------------------------

class TestReadPillow:
    def test_returns_none_when_pillow_not_installed(self):
        with patch.dict("sys.modules", {"PIL": None, "PIL.ImageGrab": None}):
            # Force ImportError on next import attempt
            with patch("builtins.__import__", side_effect=ImportError):
                result = _read_pillow()
                assert result is None

    def test_returns_none_when_grabclipboard_returns_none(self):
        mock_grab = MagicMock()
        mock_grab.grabclipboard.return_value = None
        with patch.dict("sys.modules", {"PIL.ImageGrab": mock_grab}):
            result = _read_pillow()
            assert result is None

    def test_returns_none_when_grabclipboard_returns_file_list(self):
        mock_grab = MagicMock()
        mock_grab.grabclipboard.return_value = ["/path/to/file.png"]
        with patch.dict("sys.modules", {"PIL.ImageGrab": mock_grab}):
            result = _read_pillow()
            assert result is None

    def test_returns_none_when_grabclipboard_raises(self):
        mock_grab = MagicMock()
        mock_grab.grabclipboard.side_effect = OSError("clipboard fail")
        with patch.dict("sys.modules", {"PIL.ImageGrab": mock_grab}):
            result = _read_pillow()
            assert result is None

    def test_returns_png_bytes_for_valid_image(self):
        """When Pillow returns a valid Image, we get PNG bytes back."""
        # Create a real tiny image using a BytesIO + manual approach to
        # avoid needing actual Pillow at test time.  We patch the whole
        # flow instead.
        fake_image = MagicMock()
        fake_image.mode = "RGBA"

        def fake_save(buf, format=None):
            buf.write(_TINY_PNG)

        fake_image.save = fake_save

        mock_grab = MagicMock()
        mock_grab.grabclipboard.return_value = fake_image

        mock_pil_image = MagicMock()
        mock_pil_image.Image = fake_image.__class__

        with patch.dict("sys.modules", {
            "PIL.ImageGrab": mock_grab,
            "PIL.Image": mock_pil_image,
        }):
            with patch("aye.model.clipboard_images._read_pillow") as patched:
                patched.return_value = _TINY_PNG
                result = patched()
                assert result is not None
                assert result[:8] == _PNG_SIGNATURE


# ---------------------------------------------------------------------------
# _read_wl_paste
# ---------------------------------------------------------------------------

class TestReadWlPaste:
    def test_returns_png_bytes_on_success(self):
        cp = _make_completed_process(returncode=0, stdout=_TINY_PNG)
        with patch("aye.model.clipboard_images.subprocess.run", return_value=cp):
            result = _read_wl_paste()
            assert result == _TINY_PNG

    def test_returns_none_on_nonzero_returncode(self):
        cp = _make_completed_process(returncode=1, stdout=b"")
        with patch("aye.model.clipboard_images.subprocess.run", return_value=cp):
            assert _read_wl_paste() is None

    def test_returns_none_on_empty_stdout(self):
        cp = _make_completed_process(returncode=0, stdout=b"")
        with patch("aye.model.clipboard_images.subprocess.run", return_value=cp):
            assert _read_wl_paste() is None

    def test_returns_none_when_not_png(self):
        cp = _make_completed_process(returncode=0, stdout=b"GIF89a...")
        with patch("aye.model.clipboard_images.subprocess.run", return_value=cp):
            assert _read_wl_paste() is None

    def test_returns_none_on_file_not_found(self):
        with patch(
            "aye.model.clipboard_images.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            assert _read_wl_paste() is None

    def test_returns_none_on_timeout(self):
        with patch(
            "aye.model.clipboard_images.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="wl-paste", timeout=5),
        ):
            assert _read_wl_paste() is None

    def test_returns_none_on_oserror(self):
        with patch(
            "aye.model.clipboard_images.subprocess.run",
            side_effect=OSError("broken"),
        ):
            assert _read_wl_paste() is None


# ---------------------------------------------------------------------------
# _read_xclip
# ---------------------------------------------------------------------------

class TestReadXclip:
    def test_returns_png_bytes_on_success(self):
        cp = _make_completed_process(returncode=0, stdout=_TINY_PNG)
        with patch("aye.model.clipboard_images.subprocess.run", return_value=cp):
            result = _read_xclip()
            assert result == _TINY_PNG

    def test_returns_none_on_nonzero_returncode(self):
        cp = _make_completed_process(returncode=1, stdout=b"")
        with patch("aye.model.clipboard_images.subprocess.run", return_value=cp):
            assert _read_xclip() is None

    def test_returns_none_on_empty_stdout(self):
        cp = _make_completed_process(returncode=0, stdout=b"")
        with patch("aye.model.clipboard_images.subprocess.run", return_value=cp):
            assert _read_xclip() is None

    def test_returns_none_when_not_png(self):
        cp = _make_completed_process(returncode=0, stdout=b"JFIF...")
        with patch("aye.model.clipboard_images.subprocess.run", return_value=cp):
            assert _read_xclip() is None

    def test_returns_none_on_file_not_found(self):
        with patch(
            "aye.model.clipboard_images.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            assert _read_xclip() is None

    def test_returns_none_on_timeout(self):
        with patch(
            "aye.model.clipboard_images.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="xclip", timeout=5),
        ):
            assert _read_xclip() is None

    def test_returns_none_on_oserror(self):
        with patch(
            "aye.model.clipboard_images.subprocess.run",
            side_effect=OSError("broken"),
        ):
            assert _read_xclip() is None


# ---------------------------------------------------------------------------
# _is_linux
# ---------------------------------------------------------------------------

class TestIsLinux:
    def test_true_on_linux(self):
        with patch("aye.model.clipboard_images.sys") as mock_sys:
            mock_sys.platform = "linux"
            assert _is_linux() is True

    def test_true_on_linux2(self):
        with patch("aye.model.clipboard_images.sys") as mock_sys:
            mock_sys.platform = "linux2"
            assert _is_linux() is True

    def test_false_on_darwin(self):
        with patch("aye.model.clipboard_images.sys") as mock_sys:
            mock_sys.platform = "darwin"
            assert _is_linux() is False

    def test_false_on_win32(self):
        with patch("aye.model.clipboard_images.sys") as mock_sys:
            mock_sys.platform = "win32"
            assert _is_linux() is False


# ---------------------------------------------------------------------------
# _any_linux_tool_available
# ---------------------------------------------------------------------------

class TestAnyLinuxToolAvailable:
    def test_true_when_wl_paste_found(self):
        def side_effect(args, **kwargs):
            if args[0] == "wl-paste":
                return _make_completed_process(returncode=0)
            raise FileNotFoundError

        with patch("aye.model.clipboard_images.subprocess.run", side_effect=side_effect):
            assert _any_linux_tool_available() is True

    def test_true_when_xclip_found(self):
        def side_effect(args, **kwargs):
            if args[0] == "xclip":
                return _make_completed_process(returncode=0)
            raise FileNotFoundError

        with patch("aye.model.clipboard_images.subprocess.run", side_effect=side_effect):
            assert _any_linux_tool_available() is True

    def test_false_when_no_tools(self):
        with patch(
            "aye.model.clipboard_images.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            assert _any_linux_tool_available() is False

    def test_false_on_timeout(self):
        with patch(
            "aye.model.clipboard_images.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="x", timeout=5),
        ):
            assert _any_linux_tool_available() is False

    def test_false_on_oserror(self):
        with patch(
            "aye.model.clipboard_images.subprocess.run",
            side_effect=OSError("broken"),
        ):
            assert _any_linux_tool_available() is False


# ---------------------------------------------------------------------------
# _generate_filename
# ---------------------------------------------------------------------------

class TestGenerateFilename:
    def test_no_hint_produces_timestamped_name(self):
        name = _generate_filename()
        assert name.startswith("clipboard-")
        assert name.endswith(".png")
        # Format: clipboard-YYYYMMDD-HHMMSS.png
        assert len(name) == len("clipboard-YYYYMMDD-HHMMSS.png")

    def test_no_hint_uses_utc(self):
        now = datetime.now(timezone.utc)
        name = _generate_filename()
        date_part = name.replace("clipboard-", "").replace(".png", "").split("-")[0]
        assert date_part == now.strftime("%Y%m%d")

    def test_hint_with_extension(self):
        name = _generate_filename("screenshot.jpg")
        assert name == "screenshot.png"

    def test_hint_without_extension(self):
        name = _generate_filename("myimage")
        assert name == "myimage.png"

    def test_hint_with_path(self):
        name = _generate_filename("some/dir/photo.bmp")
        assert name == "photo.png"

    def test_none_hint_treated_as_no_hint(self):
        name = _generate_filename(None)
        assert name.startswith("clipboard-")
        assert name.endswith(".png")

    def test_empty_string_hint_treated_as_no_hint(self):
        # Empty string is falsy, falls through to timestamp path
        name = _generate_filename("")
        assert name.startswith("clipboard-")
        assert name.endswith(".png")


# ---------------------------------------------------------------------------
# _read_clipboard_png_bytes (orchestration)
# ---------------------------------------------------------------------------

class TestReadClipboardPngBytes:
    def test_returns_bytes_from_pillow(self):
        with patch("aye.model.clipboard_images._read_pillow", return_value=_TINY_PNG):
            result = mod._read_clipboard_png_bytes()
            assert result == _TINY_PNG

    def test_falls_through_to_wl_paste_on_linux(self):
        with patch("aye.model.clipboard_images._read_pillow", return_value=None), \
             patch("aye.model.clipboard_images._is_linux", return_value=True), \
             patch("aye.model.clipboard_images._read_wl_paste", return_value=_TINY_PNG):
            result = mod._read_clipboard_png_bytes()
            assert result == _TINY_PNG

    def test_falls_through_to_xclip_on_linux(self):
        with patch("aye.model.clipboard_images._read_pillow", return_value=None), \
             patch("aye.model.clipboard_images._is_linux", return_value=True), \
             patch("aye.model.clipboard_images._read_wl_paste", return_value=None), \
             patch("aye.model.clipboard_images._read_xclip", return_value=_TINY_PNG):
            result = mod._read_clipboard_png_bytes()
            assert result == _TINY_PNG

    def test_linux_no_tools_raises_unavailable(self):
        with patch("aye.model.clipboard_images._read_pillow", return_value=None), \
             patch("aye.model.clipboard_images._is_linux", return_value=True), \
             patch("aye.model.clipboard_images._read_wl_paste", return_value=None), \
             patch("aye.model.clipboard_images._read_xclip", return_value=None), \
             patch("aye.model.clipboard_images._any_linux_tool_available", return_value=False):
            with pytest.raises(ClipboardImageUnavailableError) as exc_info:
                mod._read_clipboard_png_bytes()
            assert "wl-paste" in str(exc_info.value) or "xclip" in str(exc_info.value)

    def test_linux_tools_available_but_no_image_raises_not_found(self):
        with patch("aye.model.clipboard_images._read_pillow", return_value=None), \
             patch("aye.model.clipboard_images._is_linux", return_value=True), \
             patch("aye.model.clipboard_images._read_wl_paste", return_value=None), \
             patch("aye.model.clipboard_images._read_xclip", return_value=None), \
             patch("aye.model.clipboard_images._any_linux_tool_available", return_value=True):
            with pytest.raises(ClipboardImageNotFoundError) as exc_info:
                mod._read_clipboard_png_bytes()
            assert "No image" in str(exc_info.value)

    def test_non_linux_no_pillow_raises_unavailable(self):
        """On macOS/Windows without Pillow, raises unavailable."""
        with patch("aye.model.clipboard_images._read_pillow", return_value=None), \
             patch("aye.model.clipboard_images._is_linux", return_value=False), \
             patch("builtins.__import__", side_effect=ImportError):
            with pytest.raises(ClipboardImageUnavailableError) as exc_info:
                mod._read_clipboard_png_bytes()
            assert "Pillow" in str(exc_info.value)

    def test_non_linux_pillow_available_but_no_image_raises_not_found(self):
        """On non-Linux with Pillow importable but no image, raises not-found."""
        mock_pil = MagicMock()
        mock_imagegrab = MagicMock()
        with patch("aye.model.clipboard_images._read_pillow", return_value=None), \
             patch("aye.model.clipboard_images._is_linux", return_value=False), \
             patch.dict("sys.modules", {"PIL": mock_pil, "PIL.ImageGrab": mock_imagegrab}):
            with pytest.raises(ClipboardImageNotFoundError) as exc_info:
                mod._read_clipboard_png_bytes()
            assert "No image" in str(exc_info.value)


# ---------------------------------------------------------------------------
# clipboard_image_available
# ---------------------------------------------------------------------------

class TestClipboardImageAvailable:
    def test_true_when_pillow_importable(self):
        mock_pil = MagicMock()
        mock_imagegrab = MagicMock()
        with patch.dict("sys.modules", {"PIL": mock_pil, "PIL.ImageGrab": mock_imagegrab}):
            assert clipboard_image_available() is True

    def test_true_on_linux_with_tools(self):
        # patch(__import__) MUST be innermost so that the other patches
        # can resolve their dotted target via the real __import__ first.
        # Python 3.10's mock._importer calls __import__ to look up the
        # target module; 3.11+ uses sys.modules directly.
        with patch("aye.model.clipboard_images._is_linux", return_value=True), \
             patch("aye.model.clipboard_images._any_linux_tool_available", return_value=True), \
             patch("builtins.__import__", side_effect=ImportError):
            assert clipboard_image_available() is True

    def test_false_when_nothing_available(self):
        with patch("aye.model.clipboard_images._is_linux", return_value=False), \
             patch("builtins.__import__", side_effect=ImportError):
            assert clipboard_image_available() is False

    def test_false_on_linux_without_tools(self):
        with patch("aye.model.clipboard_images._is_linux", return_value=True), \
             patch("aye.model.clipboard_images._any_linux_tool_available", return_value=False), \
             patch("builtins.__import__", side_effect=ImportError):
            assert clipboard_image_available() is False


# ---------------------------------------------------------------------------
# read_clipboard_image_bytes
# ---------------------------------------------------------------------------

class TestReadClipboardImageBytes:
    def test_returns_tuple_of_bytes_and_mime(self):
        with patch(
            "aye.model.clipboard_images._read_clipboard_png_bytes",
            return_value=_TINY_PNG,
        ):
            png_bytes, mime = read_clipboard_image_bytes()
            assert png_bytes == _TINY_PNG
            assert mime == "image/png"

    def test_mime_is_always_image_png(self):
        with patch(
            "aye.model.clipboard_images._read_clipboard_png_bytes",
            return_value=b"any",
        ):
            _, mime = read_clipboard_image_bytes()
            assert mime == "image/png"

    def test_propagates_unavailable_error(self):
        with patch(
            "aye.model.clipboard_images._read_clipboard_png_bytes",
            side_effect=ClipboardImageUnavailableError("nope"),
        ):
            with pytest.raises(ClipboardImageUnavailableError):
                read_clipboard_image_bytes()

    def test_propagates_not_found_error(self):
        with patch(
            "aye.model.clipboard_images._read_clipboard_png_bytes",
            side_effect=ClipboardImageNotFoundError("empty"),
        ):
            with pytest.raises(ClipboardImageNotFoundError):
                read_clipboard_image_bytes()


# ---------------------------------------------------------------------------
# load_clipboard_image_attachment
# ---------------------------------------------------------------------------

class TestLoadClipboardImageAttachment:
    def test_returns_dict_with_expected_keys(self):
        with patch(
            "aye.model.clipboard_images.read_clipboard_image_bytes",
            return_value=(_TINY_PNG, "image/png"),
        ):
            result = load_clipboard_image_attachment()
            assert isinstance(result, dict)
            assert set(result.keys()) == {
                "file_name", "mime_type", "data_b64", "bytes_size",
            }

    def test_mime_type_is_image_png(self):
        with patch(
            "aye.model.clipboard_images.read_clipboard_image_bytes",
            return_value=(_TINY_PNG, "image/png"),
        ):
            result = load_clipboard_image_attachment()
            assert result["mime_type"] == "image/png"

    def test_bytes_size_matches_raw_length(self):
        with patch(
            "aye.model.clipboard_images.read_clipboard_image_bytes",
            return_value=(_TINY_PNG, "image/png"),
        ):
            result = load_clipboard_image_attachment()
            assert result["bytes_size"] == len(_TINY_PNG)

    def test_base64_round_trip(self):
        with patch(
            "aye.model.clipboard_images.read_clipboard_image_bytes",
            return_value=(_TINY_PNG, "image/png"),
        ):
            result = load_clipboard_image_attachment()
            decoded = base64.b64decode(result["data_b64"].encode("ascii"))
            assert decoded == _TINY_PNG

    def test_data_b64_is_ascii_str(self):
        with patch(
            "aye.model.clipboard_images.read_clipboard_image_bytes",
            return_value=(_TINY_PNG, "image/png"),
        ):
            result = load_clipboard_image_attachment()
            assert isinstance(result["data_b64"], str)
            result["data_b64"].encode("ascii")  # should not raise

    def test_filename_uses_hint(self):
        with patch(
            "aye.model.clipboard_images.read_clipboard_image_bytes",
            return_value=(_TINY_PNG, "image/png"),
        ):
            result = load_clipboard_image_attachment(name_hint="my_shot.bmp")
            assert result["file_name"] == "my_shot.png"

    def test_filename_default_has_timestamp(self):
        with patch(
            "aye.model.clipboard_images.read_clipboard_image_bytes",
            return_value=(_TINY_PNG, "image/png"),
        ):
            result = load_clipboard_image_attachment()
            assert result["file_name"].startswith("clipboard-")
            assert result["file_name"].endswith(".png")

    def test_oversize_image_raises_too_large(self):
        big_data = b"x" * 200
        with patch(
            "aye.model.clipboard_images.read_clipboard_image_bytes",
            return_value=(big_data, "image/png"),
        ), patch("aye.model.clipboard_images.IMAGE_MAX_BYTES", 100):
            with pytest.raises(ClipboardImageTooLargeError) as exc_info:
                load_clipboard_image_attachment()
            msg = str(exc_info.value)
            assert "exceeds" in msg or "limit" in msg

    def test_size_at_limit_is_allowed(self):
        data = b"x" * 100
        with patch(
            "aye.model.clipboard_images.read_clipboard_image_bytes",
            return_value=(data, "image/png"),
        ), patch("aye.model.clipboard_images.IMAGE_MAX_BYTES", 100):
            result = load_clipboard_image_attachment()
            assert result["bytes_size"] == 100

    def test_size_one_over_limit_raises(self):
        data = b"x" * 101
        with patch(
            "aye.model.clipboard_images.read_clipboard_image_bytes",
            return_value=(data, "image/png"),
        ), patch("aye.model.clipboard_images.IMAGE_MAX_BYTES", 100):
            with pytest.raises(ClipboardImageTooLargeError):
                load_clipboard_image_attachment()

    def test_propagates_unavailable_error(self):
        with patch(
            "aye.model.clipboard_images.read_clipboard_image_bytes",
            side_effect=ClipboardImageUnavailableError("nope"),
        ):
            with pytest.raises(ClipboardImageUnavailableError):
                load_clipboard_image_attachment()

    def test_propagates_not_found_error(self):
        with patch(
            "aye.model.clipboard_images.read_clipboard_image_bytes",
            side_effect=ClipboardImageNotFoundError("empty"),
        ):
            with pytest.raises(ClipboardImageNotFoundError):
                load_clipboard_image_attachment()


# ---------------------------------------------------------------------------
# __all__ exposure
# ---------------------------------------------------------------------------

class TestPublicApi:
    def test_all_symbols_exported(self):
        for name in (
            "ClipboardImageError",
            "ClipboardImageUnavailableError",
            "ClipboardImageNotFoundError",
            "ClipboardImageTooLargeError",
            "clipboard_image_available",
            "read_clipboard_image_bytes",
            "load_clipboard_image_attachment",
        ):
            assert name in mod.__all__
            assert hasattr(mod, name)
