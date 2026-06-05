"""Pending clipboard attachment state management.

This module provides helpers to stage clipboard image attachments on the
active REPL config object so they can be accumulated across multiple
``paste-image`` calls (or future ``Ctrl+V`` key presses) and then
merged into the next normal AI prompt.

It also provides marker helpers for the optional ``Ctrl+V`` key binding
(Phase 5) where a visible placeholder is inserted into the prompt
buffer.

All pending state lives on ``conf._pending_clipboard_attachments``
(a plain list of attachment dicts).  The helpers gracefully handle the
attribute being absent so callers never need to pre-initialize it.

See ``ctrl-v.md`` Phase 2 for design context.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Marker helpers (Phase 5 prep – included now to keep later diffs small)
# ---------------------------------------------------------------------------

#: Pattern that matches clipboard image markers inserted by Ctrl+V.
#: Example marker: ``[clipboard:image-001]``
CLIPBOARD_MARKER_RE = re.compile(r"\[clipboard:image-\d{3,}\]")

_PENDING_ATTR = "_pending_clipboard_attachments"


# ---------------------------------------------------------------------------
# Pending attachment state
# ---------------------------------------------------------------------------


def get_pending_clipboard_attachments(conf: Any) -> List[Dict[str, object]]:
    """Return the current list of pending clipboard attachments.

    If the config object has no pending attachments yet an empty list
    is returned.  The returned list is a **copy** so callers cannot
    accidentally mutate the internal state.

    Args:
        conf: The active REPL config object.

    Returns:
        A (possibly empty) list of attachment dicts.
    """
    return list(getattr(conf, _PENDING_ATTR, None) or [])


def add_pending_clipboard_attachment(
    conf: Any,
    attachment: Dict[str, object],
) -> None:
    """Append a clipboard attachment to the pending list.

    Multiple calls accumulate; nothing is replaced.

    Args:
        conf: The active REPL config object.
        attachment: An attachment dict with keys ``file_name``,
            ``mime_type``, ``data_b64``, and ``bytes_size``.
    """
    pending = getattr(conf, _PENDING_ATTR, None)
    if pending is None:
        pending = []
        setattr(conf, _PENDING_ATTR, pending)
    pending.append(attachment)


def clear_pending_clipboard_attachments(conf: Any) -> None:
    """Remove all pending clipboard attachments.

    Safe to call even when the attribute does not exist.

    Args:
        conf: The active REPL config object.
    """
    if hasattr(conf, _PENDING_ATTR):
        setattr(conf, _PENDING_ATTR, [])


def pending_clipboard_attachment_count(conf: Any) -> int:
    """Return the number of currently staged clipboard attachments.

    Args:
        conf: The active REPL config object.

    Returns:
        Count of pending attachments (``0`` if none staged).
    """
    return len(getattr(conf, _PENDING_ATTR, None) or [])


# ---------------------------------------------------------------------------
# Marker helpers
# ---------------------------------------------------------------------------


def make_clipboard_marker(conf: Any) -> str:
    """Generate a safe visible marker for a clipboard image.

    The marker encodes the 1-based index of the staged attachment so the
    user can see how many images have been pasted.

    Example return value: ``[clipboard:image-001]``

    Args:
        conf: The active REPL config object (used to derive the
            current count).

    Returns:
        A marker string.
    """
    count = pending_clipboard_attachment_count(conf)
    return f"[clipboard:image-{count:03d}]"


def strip_clipboard_markers(prompt: str) -> str:
    """Remove all clipboard image markers from a prompt string.

    This should be called before sending the prompt to the LLM so that
    internal markers do not pollute the user's actual text.

    Args:
        prompt: The raw prompt text, possibly containing markers.

    Returns:
        The prompt with all ``[clipboard:image-NNN]`` markers removed
        and excess whitespace cleaned up.
    """
    cleaned = CLIPBOARD_MARKER_RE.sub("", prompt)
    # Collapse runs of whitespace left behind by marker removal,
    # but preserve leading/trailing whitespace choices.
    cleaned = re.sub(r"  +", " ", cleaned)
    return cleaned.strip()


__all__ = [
    "CLIPBOARD_MARKER_RE",
    "get_pending_clipboard_attachments",
    "add_pending_clipboard_attachment",
    "clear_pending_clipboard_attachments",
    "pending_clipboard_attachment_count",
    "make_clipboard_marker",
    "strip_clipboard_markers",
]
