from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from rich import print as rprint

class Plugin(ABC):
    name: str
    version: str = "1.0.0"
    premium: str = "free"  # one of: free, pro, team, enterprise
    verbose: bool = False
    debug: bool = False

    def init(self, cfg: Dict[str, Any]) -> None:
        self.verbose = bool(cfg.get("verbose", False))
        self.debug = bool(cfg.get("debug", False))

        if self.debug:
            rprint(f"[bold yellow]Plugin config: {cfg}[/]")
            rprint(f"[bold yellow]Plugin premium tier: {self.premium}[/]")
            rprint(f"[bold yellow]Plugin verbose mode: {self.verbose}[/]")

    def on_command(self, command_name: str, params: Dict[str, Any] = {}) -> Optional[Dict[str, Any]]:
        """
        Handle a command with generic parameters.
        
        Args:
            command_name: Name of the command being executed
            params: Dictionary containing command-specific parameters
            
        Returns:
            Dictionary with response data, or None if plugin doesn't handle this command
        """
        return None
