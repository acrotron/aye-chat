from prompt_toolkit.document import Document
from typing import Dict, Any, Optional, List
from prompt_toolkit.completion import Completer, Completion
from .plugin_base import Plugin
from rich import print as rprint


class SlashCompleter(Completer):
    """
    Completes Aye commands when user types '/' at the start of input.
    Shows all available commands immediately and filters as user types.
    """

    def __init__(self, commands: Optional[List[str]] = None):
        self.commands = sorted(commands or [])

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        
        # Only complete if we're at the start and have a slash
        if not text.startswith('/'):
            return
        
        # Get the part after the slash
        if len(text) == 1:
            # Just typed '/', show all commands
            for cmd in self.commands:
                yield Completion(
                    cmd,
                    start_position=0,
                    display=f"/{cmd}",
                    display_meta="Aye command"
                )
        else:
            # Filter commands based on what's typed after '/'
            prefix = text[1:]  # Remove the '/'
            for cmd in self.commands:
                if cmd.startswith(prefix):
                    yield Completion(
                        cmd,
                        start_position=-len(prefix),
                        display=f"/{cmd}",
                        display_meta="Aye command"
                    )


class SlashCompleterPlugin(Plugin):
    name = "slash_completer"
    version = "1.0.0"
    premium = "free"

    def init(self, cfg: Dict[str, Any]) -> None:
        """Initialize the slash completer plugin."""
        super().init(cfg)
        if self.debug:
            rprint(f"[bold yellow]Initializing {self.name} v{self.version}[/]")

    def on_command(self, command_name: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Handle slash completion requests through the plugin system."""
        if command_name == "get_slash_completer":
            commands = params.get("commands", [])
            return {"completer": SlashCompleter(commands)}
        return None