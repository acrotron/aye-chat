"""Clipboard image loading helpers for paste-image support.

This module reads image data from the system clipboard, normalizes it
to PNG, validates size constraints, and returns attachment dicts
compatible with the existing backend payload shape used by
``model.attachments``.

Clipboard access strategy:

1. **macOS / Windows**: ``PIL.ImageGrab.grabclipboard()`` via Pillow.
2. **Linux (Wayland)**: ``wl-paste --type image/png`` subprocess.
3. **Linux (X11)**: ``xclip -selection clipboard -t image/png -o`` subprocess.

If none of the above succeed the module raises
``ClipboardImageUnavailableError`` with a platform-appropriate message.

All image data stays in memory. Nothing is written to disk.
Exception messages never include raw bytes or base64 data.

See ``ctrl-v.md`` Phase 1 for design context.
"""

from __future__ import annotations

import base64
import io
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from aye.model.attachments import IMAGE_MAX_BYTES

# ---------------------------------------------------------------------------
# PNG magic bytes for validation
# ---------------------------------------------------------------------------

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# ---------------------------------------------------------------------------
# Subprocess timeout (seconds)
# ---------------------------------------------------------------------------

_SUBPROCESS_TIMEOUT = 5

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ClipboardImageError(Exception):
    """Base exception for clipboard image operations."""


class ClipboardImageUnavailableError(ClipboardImageError):
    """Raised when clipboard image access is not supported on this system."""


class ClipboardImageNotFoundError(ClipboardImageError):
    """Raised when the clipboard does not contain an image."""


class ClipboardImageTooLargeError(ClipboardImageError):
    """Raised when the clipboard image exceeds the size limit."""


# ---------------------------------------------------------------------------
# Internal: Pillow-based clipboard reading
# ---------------------------------------------------------------------------


def _read_pillow() -> Optional[bytes]:
    """Try to read a clipboard image via Pillow and return PNG bytes.

    Returns:
        PNG bytes if an image was found, ``None`` if the clipboard is
        empty or does not contain an image, or if Pillow is not
        available / does not support clipboard on this platform.
    """
    try:
        from PIL import ImageGrab  # type: ignore[import-untyped]
    except ImportError:
        return None

    try:
        img = ImageGrab.grabclipboard()
    except Exception:  # noqa: BLE001 – Pillow can raise various OS errors
        return None

    if img is None:
        return None

    # On some platforms grabclipboard() returns a list of file paths
    # rather than an Image object (e.g. Windows file copy).  We only
    # handle actual image data.
    if isinstance(img, list):
        return None

    # Ensure we have a Pillow Image
    try:
        from PIL import Image as PILImage  # type: ignore[import-untyped]

        if not isinstance(img, PILImage.Image):
            return None
    except ImportError:
        return None

    buf = io.BytesIO()
    try:
        # Convert to RGBA first to handle palette / mode edge cases,
        # then save as PNG.
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")
        img.save(buf, format="PNG")
    except Exception:  # noqa: BLE001
        return None

    return buf.getvalue()


# ---------------------------------------------------------------------------
# Internal: Linux subprocess fallbacks
# ---------------------------------------------------------------------------


def _read_wl_paste() -> Optional[bytes]:
    """Try to read a clipboard image via ``wl-paste`` (Wayland).

    Returns:
        PNG bytes if successful, ``None`` otherwise.
    """
    try:
        result = subprocess.run(
            ["wl-paste", "--type", "image/png"],
            capture_output=True,
            timeout=_SUBPROCESS_TIMEOUT,
        )
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return None
    except OSError:
        return None

    if result.returncode != 0 or not result.stdout:
        return None

    if not result.stdout.startswith(_PNG_SIGNATURE):
        return None

    return result.stdout


def _read_xclip() -> Optional[bytes]:
    """Try to read a clipboard image via ``xclip`` (X11).

    Returns:
        PNG bytes if successful, ``None`` otherwise.
    """
    try:
        result = subprocess.run(
            ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
            capture_output=True,
            timeout=_SUBPROCESS_TIMEOUT,
        )
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return None
    except OSError:
        return None

    if result.returncode != 0 or not result.stdout:
        return None

    if not result.stdout.startswith(_PNG_SIGNATURE):
        return None

    return result.stdout


# ---------------------------------------------------------------------------
# Internal: orchestrate reading strategies
# ---------------------------------------------------------------------------


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def _read_clipboard_png_bytes() -> bytes:
    """Attempt all clipboard reading strategies and return PNG bytes.

    Raises:
        ClipboardImageUnavailableError: If no clipboard backend is
            available on this system.
        ClipboardImageNotFoundError: If the clipboard does not contain
            an image.
    """
    # 1. Pillow (macOS, Windows, and some Linux configurations)
    png_bytes = _read_pillow()
    if png_bytes is not None:
        return png_bytes

    # 2. Linux subprocess fallbacks
    if _is_linux():
        png_bytes = _read_wl_paste()
        if png_bytes is not None:
            return png_bytes

        png_bytes = _read_xclip()
        if png_bytes is not None:
            return png_bytes

        # All Linux backends tried and failed.  Distinguish between
        # "no clipboard tool" and "clipboard exists but has no image"
        # by checking tool availability.
        if not _any_linux_tool_available():
            raise ClipboardImageUnavailableError(
                "Clipboard image paste is not available. "
                "On Linux, install wl-paste (Wayland) or xclip (X11)."
            )

        raise ClipboardImageNotFoundError(
            "No image found in clipboard. "
            "Copy an image to the clipboard and try `paste-image` again."
        )

    # Non-Linux: Pillow was the only strategy and returned None.
    # Determine if Pillow is importable at all.
    try:
        from PIL import ImageGrab  # type: ignore[import-untyped]  # noqa: F401

        pillow_available = True
    except ImportError:
        pillow_available = False

    if not pillow_available:
        raise ClipboardImageUnavailableError(
            "Clipboard image paste is not available. "
            "Pillow is required but could not be imported."
        )

    raise ClipboardImageNotFoundError(
        "No image found in clipboard. "
        "Copy an image to the clipboard and try `paste-image` again."
    )


def _any_linux_tool_available() -> bool:
    """Check whether at least one Linux clipboard tool is installed."""
    for cmd in ("wl-paste", "xclip"):
        try:
            subprocess.run(
                [cmd, "--version"],
                capture_output=True,
                timeout=_SUBPROCESS_TIMEOUT,
            )
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    return False


# ---------------------------------------------------------------------------
# Filename generation
# ---------------------------------------------------------------------------


def _generate_filename(name_hint: Optional[str] = None) -> str:
    """Generate a synthetic filename for a clipboard image.

    Args:
        name_hint: Optional custom prefix. If ``None`` the name is
            ``clipboard-YYYYMMDD-HHMMSS.png``.

    Returns:
        A safe filename string with ``.png`` extension.
    """
    if name_hint:
        stem = Path(name_hint).stem
        return f"{stem}.png"

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"clipboard-{ts}.png"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def clipboard_image_available() -> bool:
    """Check whether clipboard image reading is likely supported.

    This is a best-effort check. It verifies that at least one
    clipboard backend (Pillow or a Linux subprocess tool) appears
    to be importable or installed.  It does **not** guarantee that
    the clipboard currently contains an image.

    Returns:
        ``True`` if at least one backend is available.
    """
    # Check Pillow
    try:
        from PIL import ImageGrab  # type: ignore[import-untyped]  # noqa: F401

        return True
    except ImportError:
        pass

    # Check Linux tools
    if _is_linux():
        return _any_linux_tool_available()

    return False


def read_clipboard_image_bytes() -> Tuple[bytes, str]:
    """Read raw PNG bytes from the system clipboard.

    Returns:
        A tuple of ``(png_bytes, mime_type)``.
        The MIME type is always ``"image/png"`` because clipboard data
        is normalized to PNG.

    Raises:
        ClipboardImageUnavailableError: No clipboard backend available.
        ClipboardImageNotFoundError: Clipboard exists but has no image.
    """
    png_bytes = _read_clipboard_png_bytes()
    return png_bytes, "image/png"


def load_clipboard_image_attachment(
    name_hint: Optional[str] = None,
) -> dict:
    """Read a clipboard image and return a serialization-ready attachment dict.

    The returned dict has the same field names as
    ``model.attachments.ImageAttachment`` and is compatible with the
    wire format consumed by ``cli_invoke`` and ``invoke_llm``.

    Args:
        name_hint: Optional custom filename prefix. Defaults to
            ``clipboard-YYYYMMDD-HHMMSS.png``.

    Returns:
        A dict with keys ``file_name``, ``mime_type``, ``data_b64``,
        and ``bytes_size``.

    Raises:
        ClipboardImageUnavailableError: No clipboard backend available.
        ClipboardImageNotFoundError: Clipboard has no image.
        ClipboardImageTooLargeError: Image exceeds ``IMAGE_MAX_BYTES``.
    """
    png_bytes, mime_type = read_clipboard_image_bytes()
    bytes_size = len(png_bytes)

    if bytes_size > IMAGE_MAX_BYTES:
        raise ClipboardImageTooLargeError(
            f"Clipboard image is {bytes_size:,} bytes, which exceeds "
            f"the limit of {IMAGE_MAX_BYTES:,} bytes. "
            f"Copy a smaller image to the clipboard and try again."
        )

    file_name = _generate_filename(name_hint)
    data_b64 = base64.b64encode(png_bytes).decode("ascii")

    return {
        "file_name": file_name,
        "mime_type": mime_type,
        "data_b64": data_b64,
        "bytes_size": bytes_size,
    }


__all__ = [
    "ClipboardImageError",
    "ClipboardImageUnavailableError",
    "ClipboardImageNotFoundError",
    "ClipboardImageTooLargeError",
    "clipboard_image_available",
    "read_clipboard_image_bytes",
    "load_clipboard_image_attachment",
]
