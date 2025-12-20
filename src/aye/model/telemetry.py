from __future__ import annotations

from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Optional


# In-memory only telemetry (counts; no ordering).
_enabled: bool = False
_counts: Dict[str, int] = {}


def set_enabled(enabled: bool) -> None:
    global _enabled
    _enabled = bool(enabled)


def is_enabled() -> bool:
    return _enabled


def reset() -> None:
    """Clear all accumulated in-memory telemetry."""
    _counts.clear()


def _sanitize_first_token(token: str) -> str:
    """Sanitize a first token so we never include arguments or paths.

    Notes:
    - We lower-case to reduce cardinality.
    - If the token looks like a path, we keep only the basename.
    """
    if not token:
        return ""

    t = token.strip()
    if not t:
        return ""

    # Normalize slash-prefixed commands if they somehow reach here.
    if t.startswith("/") and len(t) > 1:
        t = t[1:]

    # If token looks like a path (contains separators), only keep basename.
    if "/" in t or "\\" in t:
        try:
            # On POSIX, pathlib.Path does not treat backslashes as separators.
            # Use PureWindowsPath when token contains backslashes so tests/behavior
            # are consistent across OSes.
            if "\\" in t and "/" not in t:
                t = PureWindowsPath(t).name
            else:
                t = Path(t).name
        except Exception:
            # Fall back to a conservative placeholder.
            t = "<path>"

    return t.lower()


def record_command(first_token: str, has_args: bool, prefix: Optional[str] = None) -> None:
    """Record a built-in or shell command as `<cmd>` or `<cmd> <args>`.

    Only the first token is recorded; args are collapsed to the literal `<args>`.

    Args:
        first_token: The command's first token.
        has_args: Whether arguments were present.
        prefix: Optional prefix to distinguish command sources.
            Expected values:
              - "aye:" for Aye Chat built-ins
              - "cmd:" for shell commands
            If omitted, the unprefixed command name is recorded.
    """
    if not _enabled:
        return

    cmd = _sanitize_first_token(first_token)
    if not cmd:
        return

    p = (prefix or "").strip().lower()
    name = f"{p}{cmd}" if p else cmd
    name = f"{name} <args>" if has_args else name
    _counts[name] = _counts.get(name, 0) + 1


def record_llm_prompt(kind: str = "LLM") -> None:
    """Record an LLM prompt event.

    Allowed kinds:
    - "LLM"
    - "LLM <with>"
    - "LLM @"
    """
    if not _enabled:
        return

    if kind not in {"LLM", "LLM <with>", "LLM @"}:
        kind = "LLM"

    _counts[kind] = _counts.get(kind, 0) + 1


def build_payload(top_n: int = 20) -> Optional[Dict[str, Any]]:
    """Build a bounded telemetry payload (top-N by frequency).

    Returns None if telemetry is disabled.
    """
    if not _enabled:
        return None

    # Sort by count desc, then name asc for determinism.
    items = sorted(_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    items = items[: max(0, int(top_n))]

    events = [{"name": name, "count": count} for name, count in items]
    return {"v": 1, "events": events}
