"""Version checking functionality to notify users of updates."""

import httpx
import re
from typing import Optional
from packaging import version as pkg_version
from rich import print as rprint


def get_current_version() -> str:
    """Get the current installed version of ayechat."""
    try:
        from importlib.metadata import version, PackageNotFoundError
        return version("ayechat")
    except (ImportError, PackageNotFoundError):
        return "0.0.0"


def get_latest_stable_version_info() -> Optional[tuple[str, Optional[str]]]:
    """
    Fetch the latest stable (non-prerelease) version from PyPI.

    Returns:
        A tuple of (version_string, python_version_support) or None if unable to fetch.
        python_version_support is a string like ">=3.8, <3.14" or None if not available.
    """
    try:
        response = httpx.get(
            "https://pypi.org/pypi/ayechat/json",
            timeout=3.0,
            follow_redirects=True
        )
        response.raise_for_status()
        data = response.json()

        # Get all releases and find the latest stable one
        releases = data.get("releases", {})
        stable_versions = []

        for version_str in releases.keys():
            try:
                parsed = pkg_version.parse(version_str)
                # Exclude prereleases (alpha, beta, rc, dev, etc.)
                if not parsed.is_prerelease:
                    stable_versions.append(parsed)
            except Exception:
                continue

        if not stable_versions:
            # Fallback to the info version if no stable versions found
            version_str = data["info"].get("version")
            python_requires = data["info"].get("requires_python")
            return (version_str, python_requires) if version_str else None

        # Sort and get the latest stable version
        latest_stable = max(stable_versions)
        latest_version_str = str(latest_stable)

        # Get Python version requirement from the latest release info
        python_requires = data["info"].get("requires_python")

        return latest_version_str, python_requires
    except Exception:
        # Silently fail - version checking is not critical
        return None


def is_newer_version_available() -> tuple[bool, Optional[str], str, Optional[str]]:
    """
    Check if a newer stable version is available.

    Returns:
        A tuple of (is_newer, latest_version, current_version, python_requires)
    """
    current = get_current_version()
    version_info = get_latest_stable_version_info()

    if version_info is None:
        return False, None, current, None

    latest, python_requires = version_info

    try:
        current_parsed = pkg_version.parse(current)
        latest_parsed = pkg_version.parse(latest)
        is_newer = latest_parsed > current_parsed
        return is_newer, latest, current, python_requires
    except Exception:
        # If version parsing fails, assume no update needed
        return False, latest, current, python_requires


def _parse_python_version_max(python_requires: Optional[str]) -> Optional[str]:
    """
    Extract the maximum Python version from requires_python string.

    Args:
        python_requires: A string like ">=3.8, <3.14" or ">=3.8"

    Returns:
        The maximum Python version (e.g., "3.13") or None if not found
    """
    if not python_requires:
        return None

    # Look for patterns like "<3.14" or "<=3.13"
    match = re.search(r'<\s*(\d+\.\d+)', python_requires)
    if match:
        max_version = match.group(1)
        # Convert "<3.14" to "3.13" (the highest supported)
        parts = max_version.split('.')
        if len(parts) == 2:
            major, minor = parts
            # Subtract 1 from minor version for exclusive upper bound
            return f"{major}.{int(minor) - 1}"

    # Look for patterns like "<=3.13"
    match = re.search(r'<=\s*(\d+\.\d+)', python_requires)
    if match:
        return match.group(1)

    return None


def check_version_and_print_warning() -> None:
    """
    Check for newer stable version and print a warning if one is available.

    This function is designed to be called at application startup.
    It performs a quick check and prints a warning if an update is available.
    Excludes prerelease versions.
    """
    is_newer, latest, current, python_requires = is_newer_version_available()

    if is_newer and latest:
        rprint(f"[[blue]notice[/]] A new release of Aye Chat available: [red]{current}[/] -> [green]{latest}[/]")

        # max_python = _parse_python_version_max(python_requires)
        # if max_python:
        #     rprint(f"   Supports Python: up to {max_python}")

        rprint(f"[[blue]notice[/]] To update, run: [green]pip install --upgrade ayechat[/]\n")
