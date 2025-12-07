import os
import threading
from prompt_toolkit.document import Document
from typing import Dict, Any, Optional, List
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from .plugin_base import Plugin
from rich import print as rprint


class CmdPathCompleter(Completer):
    """
    Completes:
    • the first token with an optional list of commands (with or without leading slash)
    • the *last* token (any argument) as a filesystem path
    
    System commands are loaded lazily in a background thread to avoid
    blocking startup on slow filesystems (e.g., WSL accessing Windows paths).
    """

    def __init__(self, commands: Optional[List[str]] = None):
        self._path_completer = PathCompleter()
        self._builtin_commands = commands or []
        self._system_commands: List[str] = []
        self._system_commands_loaded = False
        self._lock = threading.Lock()
        
        # Start background thread to load system commands
        # This prevents blocking startup on slow PATH scans (e.g., WSL)
        if not os.environ.get('AYE_SKIP_PATH_SCAN'):
            thread = threading.Thread(target=self._load_system_commands_background, daemon=True)
            thread.start()

    def _load_system_commands_background(self):
        """Load system commands in background thread."""
        try:
            system_cmds = self._get_system_commands()
            with self._lock:
                self._system_commands = system_cmds
                self._system_commands_loaded = True
        except Exception:
            # Silently fail - completions will just be limited to builtins
            with self._lock:
                self._system_commands_loaded = True

    @property
    def commands(self) -> List[str]:
        """Get combined list of builtin and system commands."""
        with self._lock:
            if self._system_commands_loaded:
                return sorted(list(set(self._system_commands + self._builtin_commands)))
            else:
                # System commands still loading, return just builtins
                return sorted(self._builtin_commands)

    def _get_system_commands(self) -> List[str]:
        """Get list of available system commands.
        
        Skips directories that are slow to access (Windows paths on WSL).
        """
        try:
            path_dirs = os.environ.get('PATH', '').split(os.pathsep)
            commands = set()
            
            for directory in path_dirs:
                # Skip Windows paths on WSL - they're extremely slow
                if directory.startswith('/mnt/') and len(directory) > 5 and directory[5].isalpha():
                    continue
                
                # Skip if directory doesn't exist or isn't accessible
                if not os.path.isdir(directory):
                    continue
                
                try:
                    # Use scandir for better performance
                    with os.scandir(directory) as entries:
                        for entry in entries:
                            try:
                                if entry.is_file() and os.access(entry.path, os.X_OK):
                                    commands.add(entry.name)
                            except (OSError, IOError):
                                continue
                except (OSError, IOError, PermissionError):
                    continue
            
            return list(commands)
        except Exception:
            return []

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        words = text.split()
        
        # Get current commands list (thread-safe)
        current_commands = self.commands

        # ----- Handle slash-prefixed commands -----
        if text.startswith('/') and (len(words) == 0 or (len(words) == 1 and not text.endswith(" "))):
            prefix = text[1:]  # Remove the leading slash
            for cmd in current_commands:
                if cmd.startswith(prefix):
                    yield Completion(
                        cmd,
                        start_position=-len(prefix),
                        display=f"/{cmd}",
                        display_meta="Aye command"
                    )
            return

        # ----- 1️⃣  First word → command completions (optional) -----
        if len(words) == 0:
            return
        if len(words) == 1 and not text.endswith(" "):
            prefix = words[0]
            for cmd in current_commands:
                if cmd.startswith(prefix):
                    yield Completion(
                        cmd + " ",
                        start_position=-len(prefix),
                        display=cmd,
                    )
            return

        # ----- 2️⃣  Anything after a space → path completion -----
        last_word = words[-1]
        sub_doc = Document(text=last_word, cursor_position=len(last_word))

        for comp in self._path_completer.get_completions(sub_doc, complete_event):
            completion_text = comp.text
            if os.path.isdir(last_word + completion_text):
                completion_text += "/"
            
            yield Completion(
                completion_text,
                start_position=comp.start_position,
                display=comp.display,
            )


class CompleterPlugin(Plugin):
    name = "completer"
    version = "1.0.1"  # Version bump for lazy loading fix
    premium = "free"

    def init(self, cfg: Dict[str, Any]) -> None:
        """Initialize the completer plugin."""
        super().init(cfg)
        if self.debug:
            rprint(f"[bold yellow]Initializing {self.name} v{self.version}[/]")

    def on_command(self, command_name: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Handle completion requests through the plugin system."""
        if command_name == "get_completer":
            commands = params.get("commands", [])
            return {"completer": CmdPathCompleter(commands)}
        return None
