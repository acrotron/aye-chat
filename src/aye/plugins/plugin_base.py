from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class Plugin(ABC):
    name: str
    version: str = "1.0.0"
    premium: str = "free"  # one of: free, pro, team, enterprise

    @abstractmethod
    def init(self, cfg: Dict[str, Any]) -> None:
        ...

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
