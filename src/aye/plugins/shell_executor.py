import subprocess
import os
from typing import Dict, Any, Optional
from .plugin_base import Plugin


class ShellExecutorPlugin(Plugin):
    name = "shell_executor"
    version = "1.0.0"
    premium = "free"

    def init(self, cfg: Dict[str, Any]) -> None:
        """Initialize the shell executor plugin."""
        pass

    def _is_valid_command(self, command: str) -> bool:
        """Check if a command exists in the system."""
        try:
            result = subprocess.run(['command', '-v', command], 
                                  capture_output=True, 
                                  text=True, 
                                  shell=False)
            return result.returncode == 0
        except Exception:
            return False

    def on_command(self, command_name: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Handle shell command execution through plugin system."""
        if command_name == "execute_shell_command":
            command = params.get("command", "")
            args = params.get("args", [])
            
            if not self._is_valid_command(command):
                return None #{"error": f"Command '{command}' is not found or not executable."}
            
            try:
                cmd = [command] + args
                result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
            except subprocess.CalledProcessError as e:
                return {"error": f"Error running {command} {' '.join(args)}: {e.stderr}", 
                       "stdout": e.stdout, "stderr": e.stderr, "returncode": e.returncode}
            except FileNotFoundError:
                return None # {"error": f"{command} is not installed or not found in PATH."}
        return None
