"""Base plugin class for the aye plugin system.

This module provides the abstract base class that all plugins must inherit from.
"""

from abc import ABC
from typing import Any, Dict, Optional
from rich import print as rprint
from aye.model.auth import get_user_config


class Plugin(ABC):
    """Abstract base class for all plugins.

    Plugins must inherit from this class and implement required methods.
    """

    name: str
    version: str = "1.0.0"
    premium: str = "free"  # one of: free, pro, team, enterprise
    verbose: bool = False

    @property
    def debug(self) -> bool:
        """Dynamically checks if debug mode is enabled."""
        return get_user_config("debug", "off").lower() == "on"

    def init(self, cfg: Dict[str, Any]) -> None:
        """Initialize the plugin with configuration.

        Args:
            cfg: Configuration dictionary for the plugin.
        """
        self.verbose = bool(cfg.get("verbose", False))

        if self.debug:
            rprint(f"[bold yellow]Plugin config: {cfg}[/]")
            rprint(f"[bold yellow]Plugin premium tier: {self.premium}[/]")
            rprint(f"[bold yellow]Plugin verbose mode: {self.verbose}[/]")

    def on_command(
        self, _command_name: str, _params: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Handle a command with generic parameters.

        Args:
            _command_name: Name of the command being executed
            _params: Dictionary containing command-specific parameters

        Returns:
            Dictionary with response data, or None if plugin doesn't handle this command
        """
        return None
