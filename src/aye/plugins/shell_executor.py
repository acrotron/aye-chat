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
        if self.verbose:
            rprint(f"[bold yellow]Initializing {self.name} v{self.version}[/]")
        pass

    def _is_windows(self) -> bool:
        """Check if running on Windows."""
        return platform.system() == "Windows"

    def _is_valid_command(self, command: str) -> bool:
        """Check if a command exists in the system PATH or is a built-in.
        
        Uses a hybrid approach:
        1. First tries shutil.which() for external executables
        2. On Windows, if that fails, attempts to run the command with shell=True
           to detect built-in commands
        On Unix/Linux, uses shutil.which directly.
        """
        # First try shutil.which for external executables
        if shutil.which(command) is not None:
            return True

        if self._is_windows():
            # On Windows, try with common extensions
            for ext in ['.exe', '.cmd', '.bat']:
                if shutil.which(command + ext):
                    return True
            
            # If not found, test if it's a built-in by trying to run it with /?
            # Built-in commands will respond to /? or help, external commands that
            # don't exist will fail
            try:
                result = subprocess.run(
                    f"{command} /?",
                    shell=True,
                    capture_output=True,
                    timeout=2,
                    text=True
                )
                # If command executes without error (even if it shows help), it exists
                # Most built-ins return 0 for /?, but some may return other codes
                # We just check that it didn't fail to find the command
                return True
            except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
                return False
        else:
            # On Unix/Linux, shutil.which is sufficient
            return False

    def _build_full_cmd(self, command: str, args: list) -> str:
        """Build the full shell command string, quoting args properly.
        
        On Windows, uses different quoting strategy than Unix.
        """
        if self._is_windows():
            # On Windows, use simpler quoting (shlex.quote is for Unix shells)
            # For basic cases, just join with spaces; for complex args, wrap in quotes
            quoted_args = []
            for arg in args:
                if ' ' in arg or '"' in arg:
                    # Escape internal quotes and wrap in quotes
                    quoted_args.append('"' + arg.replace('"', '\\"') + '"')
                else:
                    quoted_args.append(arg)
            return f"{command} {' '.join(quoted_args)}"
        else:
            # On Unix/Linux, use shlex.quote for proper shell escaping
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
            
            # On Windows, os.system returns the raw exit code
            # On Unix, it returns the status as returned by wait()
            if self._is_windows():
                actual_exit_code = exit_code
            else:
                # On Unix, extract the actual exit code using WEXITSTATUS
                actual_exit_code = exit_code >> 8 if hasattr(os, 'WEXITSTATUS') else exit_code
            
            return {
                "exit_code": actual_exit_code,
                "message": f"Interactive command '{full_cmd_str}' completed (exit code: {actual_exit_code}). Press Enter to continue in REPL."
            }
        except Exception as e:
            return {"error": f"Failed to run interactive command '{full_cmd_str}': {e}"}

    def _execute_non_interactive(self, command: str, args: list) -> Dict[str, Any]:
        """Execute a non-interactive command using subprocess.run with capture.
        
        On Windows, uses shell=True for proper command resolution.
        On Unix/Linux, uses shell=False for security.
        """
        try:
            if self._is_windows():
                # On Windows, always use shell=True to resolve built-ins and commands properly
                cmd_str = self._build_full_cmd(command, args)
                result = subprocess.run(cmd_str, capture_output=True, text=True, check=True, shell=True)
            else:
                # On Unix/Linux, use shell=False for security
                cmd = [command] + args
                result = subprocess.run(cmd, capture_output=True, text=True, check=True, shell=False)
            
            return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
        except subprocess.CalledProcessError as e:
            return {
                "error": f"Error running {command} {' '.join(args)}: {e.stderr}", 
                "stdout": e.stdout, "stderr": e.stderr, "returncode": e.returncode
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
            
            # Check if interactive
            if self._is_interactive(command):
                return self._execute_interactive(full_cmd_str)
            else:
                return self._execute_non_interactive(command, args)
        return None
