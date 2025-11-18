import os
from prompt_toolkit.document import Document
from typing import Dict, Any, Optional, List
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from .plugin_base import Plugin
from rich import print as rprint


class CmdPathCompleter(Completer):
    """
    Completes:
    • the first token with an optional list of commands
    • the *last* token (any argument) as a filesystem path
    """

    def __init__(self, commands: Optional[List[str]] = None):
        self._path_completer = PathCompleter()
        system_commands = self._get_system_commands()
        builtin_commands = commands or []
        self.commands = sorted(list(set(system_commands + builtin_commands)))

    def _get_system_commands(self):
        """Get list of available system commands"""
        try:
            # Get PATH directories
            path_dirs = os.environ.get('PATH', '').split(os.pathsep)
            commands = set()
            
            # Scan each directory for executables
            for directory in path_dirs:
                if os.path.isdir(directory):
                    for item in os.listdir(directory):
                        item_path = os.path.join(directory, item)
                        if os.path.isfile(item_path) and os.access(item_path, os.X_OK):
                            commands.add(item)
            
            return list(commands)
        except Exception:
            return []


    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        words = text.split()

        # ----- 1️⃣  First word → command completions (optional) -----
        if len(words) == 0:
            return
        if len(words) == 1 and not text.endswith(" "):
            # Still typing the command itself
            prefix = words[0]
            for cmd in self.commands:
                if cmd.startswith(prefix):
                    yield Completion(
                        cmd + " ",
                        start_position=-len(prefix),
                        display=cmd,
                    )
            return

        # ----- 2️⃣  Anything after a space → path completion -----
        # The word we are currently completing (the part after the last space)
        last_word = words[-1]

        # Create a temporary Document that contains only that word.
        sub_doc = Document(text=last_word, cursor_position=len(last_word))

        for comp in self._path_completer.get_completions(sub_doc, complete_event):
            # Append "/" if it's a folder
            completion_text = comp.text
            if os.path.isdir(last_word + completion_text):
                completion_text += "/"
            
            # Forward the inner completion unchanged – its start_position is
            # already the negative length of `last_word`.
            yield Completion(
                completion_text,
                start_position=comp.start_position,
                display=comp.display,
            )


class CompleterPlugin(Plugin):
    name = "completer"
    version = "1.0.0"
    premium = "free"

    def init(self, cfg: Dict[str, Any]) -> None:
        """Initialize the completer plugin."""
        super().init(cfg)
        if self.debug:
            rprint(f"[bold yellow]Initializing {self.name} v{self.version}[/]")
        pass

    def on_command(self, command_name: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Handle completion requests through the plugin system."""
        if command_name == "get_completer":
            commands = params.get("commands", [])
            return {"completer": CmdPathCompleter(commands)}
        return None
