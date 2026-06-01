"""Image attachment model and helpers for multimodal `@` references.

This module provides the data structures and pure helpers needed to load
image files referenced via `@filename` in user prompts, encode them for
transport to the backend, and decide which glob patterns should expand
to include images.

The module is intentionally self-contained: it does not import from
`aye.controller` or `aye.plugins`. Consumers (REPL, `llm_invoker`,
`at_file_completer`) wire it in during later phases.

See `issue.md` for the full feature spec and `image_phases.md` for
phasing context.
"""

from __future__ import annotations

import base64
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: File extensions treated as images for `@` reference detection.
#:
#: Matched case-insensitively. Note that `.gif` is included by extension;
#: animated GIFs are passed as-is when under the size limit (no special
#: handling in v1).
IMAGE_EXTENSIONS: frozenset = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
)

#: Maximum allowed size, in bytes, for a single image attachment
#: before base64 encoding. Images exceeding this size are rejected
#: by `load_image_attachment` rather than silently dropped.
#:
#: Base64 encoding inflates payloads by ~33%, so this cap also keeps
#: the encoded request body within reasonable bounds.
IMAGE_MAX_BYTES: int = 2_000_000

#: Fallback MIME types for common image extensions, used when the
#: system `mimetypes` database does not resolve the suffix.
_MIME_FALLBACK: Dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ImageAttachment:
    """A single image attachment ready to send to the backend.

    Attributes:
        file_name: Display name or repo-relative POSIX path of the image.
            Used for logging and as the attachment name in the API payload.
        mime_type: MIME type such as ``image/png`` or ``image/jpeg``.
        data_b64: Base64-encoded image bytes (UTF-8 ASCII string).
        bytes_size: Size in bytes of the *original* (pre-base64) image data.

    Note:
        Field names are intentionally identical to the JSON keys sent
        over the wire. No aliasing or renaming should be required during
        serialization (see ``issue.md`` Section 4).
    """

    file_name: str
    mime_type: str
    data_b64: str
    bytes_size: int


# ---------------------------------------------------------------------------
# Pattern classification
# ---------------------------------------------------------------------------

def _is_image_targeted_pattern(pattern: str) -> bool:
    """Check if a glob pattern explicitly targets image files.

    A pattern is considered "image-targeted" only when its literal
    extension (the suffix after the final dot) matches a known image
    extension. Generic patterns like ``*.*``, ``dir/*``, ``dir/**/*``,
    or bare directory references are NOT image-targeted.

    Args:
        pattern: The raw glob pattern from a ``@`` reference.

    Returns:
        ``True`` if the pattern's literal suffix is in ``IMAGE_EXTENSIONS``,
        otherwise ``False``.

    Examples:
        >>> _is_image_targeted_pattern("*.png")
        True
        >>> _is_image_targeted_pattern("screenshots/*.jpg")
        True
        >>> _is_image_targeted_pattern("assets/**/*.webp")
        True
        >>> _is_image_targeted_pattern("screenshot.PNG")
        True
        >>> _is_image_targeted_pattern("*.*")
        False
        >>> _is_image_targeted_pattern("dir/")
        False
        >>> _is_image_targeted_pattern("src/**/*")
        False
        >>> _is_image_targeted_pattern("*.py")
        False
    """
    if not pattern:
        return False

    # Directory references (trailing slash) are never image-targeted.
    if pattern.endswith("/") or pattern.endswith("\\"):
        return False

    suffix = Path(pattern).suffix.lower()
    if not suffix:
        return False

    # Treat a wildcard extension like "*.*" as non-targeted.
    if "*" in suffix or "?" in suffix:
        return False

    return suffix in IMAGE_EXTENSIONS


def _is_image_path(path: Path) -> bool:
    """Return True if the path has an image extension.

    Convenience helper for callers classifying expanded file paths
    (as opposed to raw glob patterns).

    Args:
        path: A concrete filesystem path.

    Returns:
        ``True`` if the path's suffix is in ``IMAGE_EXTENSIONS``.
    """
    return path.suffix.lower() in IMAGE_EXTENSIONS


# ---------------------------------------------------------------------------
# MIME detection
# ---------------------------------------------------------------------------

def detect_mime_type(path: Path) -> str:
    """Detect the MIME type of an image file from its extension.

    Uses the standard library ``mimetypes`` module first and falls back
    to a small built-in map for common image extensions to handle systems
    where the MIME database is incomplete.

    Args:
        path: Path to the image file. Only the suffix is used; the file
            does not need to exist.

    Returns:
        A MIME type string such as ``"image/png"``. Returns
        ``"application/octet-stream"`` if the type cannot be determined.
    """
    guessed, _ = mimetypes.guess_type(path.as_posix())
    if guessed:
        return guessed

    suffix = path.suffix.lower()
    return _MIME_FALLBACK.get(suffix, "application/octet-stream")


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _relative_name(path: Path, root: Path) -> str:
    """Return a display name for an image file.

    Prefers a POSIX-style path relative to ``root`` so the backend and
    logs see stable, non-absolute filenames. Falls back to the basename
    when the file is not under ``root``.

    Args:
        path: The (resolved) image path.
        root: The project root.

    Returns:
        A POSIX-style relative path or the basename.
    """
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return path.name


def load_image_attachment(path: Path, root: Path) -> dict:
    """Load an image file and return a serialization-ready attachment dict.

    The returned dict has the same field names as ``ImageAttachment`` and
    is the wire format consumed by ``cli_invoke`` (see ``issue.md``
    Section 4). Plugins should return these dicts directly; the REPL or
    ``llm_invoker`` layer may wrap them into ``ImageAttachment`` for type
    safety.

    Args:
        path: Path to the image file. Must exist and have an extension
            in ``IMAGE_EXTENSIONS``.
        root: Project root used to compute a relative ``file_name``.

    Returns:
        A dict with keys ``file_name``, ``mime_type``, ``data_b64``,
        and ``bytes_size``.

    Raises:
        FileNotFoundError: If ``path`` does not exist or is not a file.
        ValueError: If the file's extension is not a recognized image
            type, or if its size exceeds ``IMAGE_MAX_BYTES``.
        OSError: If reading the file fails for I/O reasons.
    """
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Image file not found: {path}")

    if not _is_image_path(path):
        raise ValueError(
            f"Not a supported image type: {path.name} "
            f"(supported: {', '.join(sorted(IMAGE_EXTENSIONS))})"
        )

    # Check size before reading to avoid loading huge files into memory.
    try:
        bytes_size = path.stat().st_size
    except OSError as exc:
        raise OSError(f"Could not stat image file {path}: {exc}") from exc

    if bytes_size > IMAGE_MAX_BYTES:
        raise ValueError(
            f"Image {path.name} is {bytes_size} bytes, which exceeds the "
            f"limit of {IMAGE_MAX_BYTES} bytes. Reduce the image size and "
            f"try again."
        )

    raw_bytes = path.read_bytes()
    data_b64 = base64.b64encode(raw_bytes).decode("ascii")
    mime_type = detect_mime_type(path)
    file_name = _relative_name(path, root)

    return {
        "file_name": file_name,
        "mime_type": mime_type,
        "data_b64": data_b64,
        "bytes_size": bytes_size,
    }


__all__ = [
    "IMAGE_EXTENSIONS",
    "IMAGE_MAX_BYTES",
    "ImageAttachment",
    "_is_image_targeted_pattern",
    "_is_image_path",
    "detect_mime_type",
    "load_image_attachment",
]
