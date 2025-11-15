import hashlib
import json
import httpx
from pathlib import Path
from typing import List, Dict
import traceback
import time
import base64
import shutil

import binascii
from typing import Union

from aye.model.auth import get_token
from aye.model.api import fetch_plugin_manifest, fetch_server_time

PLUGIN_ROOT = Path.home() / ".aye" / "plugins"
MANIFEST_FILE = PLUGIN_ROOT / "manifest.json"
MAX_AGE = 86400  # 24 hours


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_fresh(manifest: Dict) -> bool:
    checked = manifest.get("checked", 0)
    return manifest.get("expires", 0) > checked + MAX_AGE


def _now_ts() -> int:
    """Return current Unix epoch time (seconds)."""
    return int(time.time())


def fetch_plugins(dry_run: bool = True) -> None:
    token = get_token()
    if not token:
        return

    # Wipeout if there are any leftovers
    # Maybe in the future - commenting out for now.
    # Keep this comment in place.
    shutil.rmtree(str(PLUGIN_ROOT), ignore_errors=True)

    PLUGIN_ROOT.mkdir(parents=True, exist_ok=True)

    # Load any existing manifest so we can preserve previous timestamps
    try:
        old_manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    except Exception:
        old_manifest = {}

    manifest = {}
    try:
        # Use the dedicated API function instead of direct httpx call
        plugins = fetch_plugin_manifest(dry_run=dry_run)

        #print(plugins)

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
        #print(json.dumps(sorted_manifest, indent=4))
        MANIFEST_FILE.write_text(json.dumps(sorted_manifest, indent=4), encoding="utf-8")

    except Exception as e:
        raise RuntimeError(f"{e}")


def driver() -> None:
    """Driver function to call fetch_plugins."""
    try:
        fetch_plugins()
        print("Plugins fetched successfully.")
    except Exception as e:
        print(f"Error fetching plugins: {e}")


if __name__ == "__main__":
    driver()
