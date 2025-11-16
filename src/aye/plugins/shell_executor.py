import subprocess
import os
import shlex
import shutil
import platform
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
        super().init(cfg)
        if self.debug:
            rprint(f"[bold yellow]Initializing {self.name} v{self.version}[/]")
        pass

    def _is_windows(self) -> bool:
        """Check if running on Windows."""
        return platform.system() == "Windows"

    def _is_valid_command(self, command: str) -> bool:
        """Check if a command exists in the system PATH or is a built-in."""
        if shutil.which(command) is not None:
            return True

        if self._is_windows():
            for ext in ['.exe', '.cmd', '.bat']:
                if shutil.which(command + ext):
                    return True
            try:
                result = subprocess.run(
                    f"{command} /?",
                    shell=True,
                    capture_output=True,
                    timeout=2,
                    text=True
                )
                if result.stderr and "is not recognized" in result.stderr:
                    return False
                return True
            except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
                return False
        else:
            # On Unix/Linux, a simple `command -v` is effective for shell built-ins and PATH executables
            try:
                result = subprocess.run(['command', '-v', command], capture_output=True, text=True, check=True, shell=False)
                return result.returncode == 0
            except (subprocess.CalledProcessError, FileNotFoundError):
                return False

    def _build_full_cmd(self, command: str, args: list) -> str:
        """Build the full shell command string, quoting args properly."""
        if self._is_windows():
            quoted_args = []
            for arg in args:
                if ' ' in arg or '"' in arg:
                    quoted_args.append('"' + arg.replace('"', '\\"') + '"')
                else:
                    quoted_args.append(arg)
            return f"{command} {' '.join(quoted_args)}"
        else:
            quoted_args = [shlex.quote(arg) for arg in args]
            return f"{command} {' '.join(quoted_args)}"

    def _is_interactive(self, command: str) -> bool:
        """Check if the command requires interactive TTY handling."""
        return command in self.INTERACTIVE_COMMANDS

    def _execute_interactive(self, full_cmd_str: str) -> Dict[str, Any]:
        """Execute an interactive command using os.system."""
        try:
            exit_code = os.system(full_cmd_str)
            actual_exit_code = exit_code >> 8 if not self._is_windows() and hasattr(os, 'WEXITSTATUS') else exit_code
            return {
                "exit_code": actual_exit_code,
                "message": f"Interactive command '{full_cmd_str}' completed (exit code: {actual_exit_code})."
            }
        except Exception as e:
            return {"error": f"Failed to run interactive command '{full_cmd_str}': {e}"}

    def _execute_non_interactive(self, command: str, args: list) -> Dict[str, Any]:
        """Execute a non-interactive command using subprocess.run with capture."""
        try:
            use_shell = self._is_windows()
            cmd_list_or_str = self._build_full_cmd(command, args) if use_shell else [command] + args
            result = subprocess.run(cmd_list_or_str, capture_output=True, text=True, check=True, shell=use_shell)
            return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
        except subprocess.CalledProcessError as e:
            return {
                "error": f"Command '{self._build_full_cmd(command, args)}' failed with exit code {e.returncode}",
                "stdout": e.stdout,
                "stderr": e.stderr,
                "returncode": e.returncode
            }
        except FileNotFoundError:
            return None  # Command not found

    def on_command(self, command_name: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Handle shell command execution through plugin system."""
        if command_name == "execute_shell_command":
            command = params.get("command", "")
            args = params.get("args", [])
            
            if not self._is_valid_command(command):
                return None  # Command not found or not executable
            
            full_cmd_str = self._build_full_cmd(command, args)
            
            if self._is_interactive(command):
                return self._execute_interactive(full_cmd_str)
            else:
                return self._execute_non_interactive(command, args)
        return None
