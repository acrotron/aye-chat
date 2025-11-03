import subprocess
import os
import shlex
from typing import Dict, Any, Optional
from .plugin_base import Plugin
from rich import print as rprint


class ShellExecutorPlugin(Plugin):
    name = "shell_executor"
    version = "1.0.0"
    premium = "free"

    # Known interactive commands that require a TTY (add more as needed)
    INTERACTIVE_COMMANDS = {
        'vi', 'vim', 'nano', 'emacs', 'top', 'htop', 'less', 'more',
        'man', 'git-log', 'git-diff'  # git subcmds may need TTY for paging
    }

    def init(self, cfg: Dict[str, Any]) -> None:
        """Initialize the shell executor plugin."""
        if self.verbose:
            rprint(f"[bold yellow]Initializing {self.name} v{self.version}[/]")
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

    def _build_full_cmd(self, command: str, args: list) -> str:
        """Build the full shell command string, quoting args properly."""
        quoted_args = [shlex.quote(arg) for arg in args]
        return f"{command} {' '.join(quoted_args)}"

    def _is_interactive(self, command: str) -> bool:
        """Check if the command requires interactive TTY handling."""
        return command in self.INTERACTIVE_COMMANDS

    def _execute_interactive(self, full_cmd_str: str) -> Dict[str, Any]:
        """Execute an interactive command using os.system."""
        try:
            # Use os.system to run interactively in the current terminal
            # It blocks until the command exits, handles input/output directly
            exit_code = os.system(full_cmd_str)
            return {
                "exit_code": exit_code,
                "message": f"Interactive command '{full_cmd_str}' completed (exit code: {exit_code // 256 if hasattr(os, 'WEXITSTATUS') else exit_code}). Press Enter to continue in REPL."
            }
        except Exception as e:
            return {"error": f"Failed to run interactive command '{full_cmd_str}': {e}"}

    def _execute_non_interactive(self, command: str, args: list) -> Dict[str, Any]:
        """Execute a non-interactive command using subprocess.run with capture."""
        try:
            cmd = [command] + args
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
        except subprocess.CalledProcessError as e:
            return {
                "error": f"Error running {command} {' '.join(args)}: {e.stderr}", 
                "stdout": e.stdout, "stderr": e.stderr, "returncode": e.returncode
            }
        except FileNotFoundError:
            return None # {"error": f"{command} is not installed or not found in PATH."}

    def on_command(self, command_name: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Handle shell command execution through plugin system."""
        if command_name == "execute_shell_command":
            command = params.get("command", "")
            args = params.get("args", [])
            
            if not self._is_valid_command(command):
                return None # {"error": f"Command '{command}' is not found or not executable."}
            
            full_cmd_str = self._build_full_cmd(command, args)
            
            # Check if interactive
            if self._is_interactive(command):
                return self._execute_interactive(full_cmd_str)
            else:
                return self._execute_non_interactive(command, args)
        return None
