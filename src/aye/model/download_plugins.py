"""Plugin download and management module.

This module handles downloading plugins from the server and managing the local
plugin manifest.
"""

import hashlib
import json
import shutil
import socket
import time
from pathlib import Path

import httpx

from aye.model.auth import get_token
from aye.model.api import fetch_plugin_manifest


def _is_network_error(exc: Exception) -> bool:
    """Check if an exception is a network-related error."""
    # httpx network errors
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.TimeoutException)):
        return True
    # Socket errors (DNS failures, connection refused, etc.)
    if isinstance(exc, (socket.gaierror, socket.timeout, OSError)):
        # Check for common network-related errno values
        if hasattr(exc, 'errno') and exc.errno in (
            11001,  # getaddrinfo failed (Windows)
            -2,     # Name or service not known (Linux)
            -3,     # Temporary failure in name resolution
            110,    # Connection timed out
            111,    # Connection refused
            113,    # No route to host
        ):
            return True
        # Check error message for network-related keywords
        err_str = str(exc).lower()
        if any(keyword in err_str for keyword in ('getaddrinfo', 'name resolution', 'network', 'connection')):
            return True
    # Check if the exception wraps a network error
    if exc.__cause__ and _is_network_error(exc.__cause__):
        return True
    return False

PLUGIN_ROOT = Path.home() / ".aye" / "plugins"
MANIFEST_FILE = PLUGIN_ROOT / "manifest.json"
MAX_AGE = 86400  # 24 hours


def _now_ts() -> int:
    """Return current Unix epoch time (seconds)."""
    return int(time.time())


def fetch_plugins(dry_run: bool = True) -> None:  # pylint: disable=too-many-locals
    """Fetch plugins from the server and update local manifest.

    Args:
        dry_run: If True, performs a dry run without making actual changes.
    """
    token = get_token()
    if not token:
        return

    # Wipeout if there are any leftovers
    shutil.rmtree(str(PLUGIN_ROOT), ignore_errors=True)

    PLUGIN_ROOT.mkdir(parents=True, exist_ok=True)

    # Load any existing manifest so we can preserve previous timestamps
    try:
        old_manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    except Exception:  # pylint: disable=broad-exception-caught
        old_manifest = {}

    manifest = {}
    try:
        # Use the dedicated API function instead of direct httpx call
        plugins = fetch_plugin_manifest(dry_run=dry_run)

        for name, entry in plugins.items():
            expected_hash = entry["sha256"]
            dest = PLUGIN_ROOT / name

            source_text = entry["content"]

            computed_hash = hashlib.sha256(source_text.encode("utf‑8")).hexdigest()

            if not (dest.is_file() and computed_hash == expected_hash):
                dest.write_text(entry["content"], encoding="utf-8")
            else:
                print(f"{name}: hash does not match")

            # Always populate manifest entry irrespective of download skip
            # Preserve previous timestamps if we already have them
            prev = old_manifest.get(name, {})
            checked = prev.get("checked", _now_ts())
            expires = prev.get("expires", checked + MAX_AGE)

            manifest[name] = {
                "sha256": expected_hash,
                "checked": checked,
                "expires": expires,
            }

        # Write manifest with all plugins
        # Sort keys so the file is deterministic – helpful for tests / diffs
        sorted_manifest = {k: manifest[k] for k in sorted(manifest)}
        MANIFEST_FILE.write_text(json.dumps(sorted_manifest, indent=4), encoding="utf-8")

    except Exception as e:
        # Translate network errors to user-friendly messages
        if _is_network_error(e):
            raise RuntimeError("Could not download plugins - Network error. Please check your internet connection.") from e
        raise RuntimeError(f"Could not download plugins - {e}") from e


def driver() -> None:
    """Driver function to call fetch_plugins."""
    try:
        fetch_plugins()
        print("Plugins fetched successfully.")
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"Error fetching plugins: {e}")


if __name__ == "__main__":
    driver()
