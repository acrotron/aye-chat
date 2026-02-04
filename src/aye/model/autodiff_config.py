"""Autodiff configuration for automatic diff display after LLM changes.

This module provides functionality to check if autodiff mode is enabled.
When enabled, diffs are automatically displayed for every file modified
by an LLM response.

See: autodiff.md for the full design plan.
"""

from aye.model.auth import get_user_config


# Config key for autodiff mode
AUTODIFF_KEY = "autodiff"


def is_autodiff_enabled() -> bool:
    """Check if autodiff mode is enabled.

    When enabled, diffs are automatically displayed for every file
    modified by an LLM response, immediately after the optimistic
    write is applied.

    Can be set via:
    - Environment variable: AYE_AUTODIFF=on
    - Config file (~/.ayecfg): autodiff=on

    Returns:
        True if autodiff mode is enabled, False otherwise (default)
    """
    value = get_user_config(AUTODIFF_KEY, "off")
    return str(value).lower() in ("on", "true", "1", "yes")
