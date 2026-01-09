import os
import sys
import threading
from prompt_toolkit.document import Document
from typing import Dict, Any, Optional, List, Set
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from .plugin_base import Plugin
from rich import print as rprint

# Windows shell built-in commands (cmd.exe internals, not files on disk)
_WINDOWS_BUILTINS: Set[str] = {
    "assoc", "break", "call", "cd", "chdir", "cls", "color", "copy", "date",
    "del", "dir", "echo", "endlocal", "erase", "exit", "for", "ftype", "goto",
    "if", "md", "mkdir", "mklink", "move", "path", "pause", "popd", "prompt",
    "pushd", "rd", "rem", "ren", "rename", "rmdir", "set", "setlocal", "shift",
    "start", "time", "title", "type", "ver", "verify", "vol",
}

# Windows executable file extensions
_WINDOWS_EXECUTABLE_EXTENSIONS: Set[str] = {
    ".exe", ".bat", ".cmd", ".com", ".ps1", ".vbs", ".js", ".msc",
}


class DynamicAutoCompleteCompleter(Completer):
    """
    A completer wrapper that enables auto-complete (complete-while-typing)
    only for specific contexts like @ file references.

    Behavior depends on completion_style:
    - 'readline': @ completions auto-trigger, other completions require TAB
    - 'multi': all completions auto-trigger (complete while typing)

    This allows @ file completion to always show in multi-column format
    while respecting the user's preference for other completions.
    """

    def __init__(self, inner_completer: Completer, completion_style: str = "readline"):
        self.inner_completer = inner_completer
        self.completion_style = completion_style.lower()

    def _is_at_file_context(self, text: str) -> bool:
        """Check if we're in an @ file reference context."""
        if "@" not in text:
            return False

        at_pos = text.rfind("@")
        # Valid file reference: @ at start or preceded by whitespace
        if at_pos == 0 or (at_pos > 0 and text[at_pos - 1] in " \t\n"):
            # Check that we haven't finished the reference (no space after partial)
            partial = text[at_pos + 1 :]
            if " " not in partial or partial.endswith("/"):
                return True

        return False

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor

        # For @ file references, always yield completions (auto-complete)
        # This ensures @ completions work the same in both modes
        if self._is_at_file_context(text):
            yield from self.inner_completer.get_completions(document, complete_event)
            return

        # For other completions, behavior depends on completion_style:
        # - 'multi': auto-complete (yield completions always)
        # - 'readline': only yield completions when TAB is pressed
        if self.completion_style == "multi":
            # Multi mode: always show completions (auto-complete)
            yield from self.inner_completer.get_completions(document, complete_event)
        else:
            # Readline mode: only show completions when TAB is pressed
            # complete_event.completion_requested is True when user pressed TAB
            if complete_event.completion_requested:
                yield from self.inner_completer.get_completions(document, complete_event)


class CompositeCompleter(Completer):
    """
    Composite completer that delegates to AtFileCompleter for '@' references
    and CmdPathCompleter for everything else.
    """

    def __init__(self, cmd_completer: Completer, at_completer: Completer):
        self.cmd_completer = cmd_completer
        self.at_completer = at_completer

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor

        # Check if we're in an '@' file reference
        if "@" in text:
            at_pos = text.rfind("@")
            # Valid file reference: @ at start or preceded by whitespace
            if at_pos == 0 or (at_pos > 0 and text[at_pos - 1] in " \t\n"):
                # Delegate to at_completer
                yield from self.at_completer.get_completions(document, complete_event)
                return

        # Otherwise use cmd_completer
        yield from self.cmd_completer.get_completions(document, complete_event)


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
        if not os.environ.get("AYE_SKIP_PATH_SCAN"):
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

    def _split_path_env(self, path_value: str) -> List[str]:
        """Split a PATH-like env var into directories.

        Normally PATH is split with os.pathsep, but unit tests (and some
        environments) may provide POSIX-style PATH strings (':') even when
        running on Windows (where os.pathsep is ';').

        This keeps behavior correct across platforms while avoiding breaking
        typical Windows paths like 'C:\\Windows\\System32'.
        """
        if not path_value:
            return []

        # Prefer the platform separator when it is actually present.
        if os.pathsep in path_value:
            return [p for p in path_value.split(os.pathsep) if p]

        # Heuristic: looks like a POSIX/WSL PATH (starts with '/' or contains /mnt/)
        # In that case, ':' is the correct separator.
        if path_value.startswith("/") or "/mnt/" in path_value:
            return [p for p in path_value.split(":") if p]

        # Fallback: no known separator found; treat as a single entry.
        return [path_value]

    def _is_windows_executable(self, entry) -> bool:
        """Check if a file is an executable on Windows based on extension."""
        if not entry.is_file():
            return False
        _, ext = os.path.splitext(entry.name)
        return ext.lower() in _WINDOWS_EXECUTABLE_EXTENSIONS

    def _get_command_name(self, filename: str) -> str:
        """Get command name from filename, stripping extension on Windows."""
        if sys.platform == "win32":
            name, ext = os.path.splitext(filename)
            if ext.lower() in _WINDOWS_EXECUTABLE_EXTENSIONS:
                return name
        return filename

    def _get_system_commands(self) -> List[str]:
        """Get list of available system commands.

        Skips directories that are slow to access (Windows paths on WSL).
        On Windows, includes shell built-in commands and uses extension-based
        executable detection.
        """
        try:
            path_value = os.environ.get("PATH", "")
            path_dirs = self._split_path_env(path_value)
            commands: Set[str] = set()

            # On Windows, add shell built-in commands (dir, mkdir, etc.)
            if sys.platform == "win32":
                commands.update(_WINDOWS_BUILTINS)

            for directory in path_dirs:
                # Skip Windows paths on WSL - they're extremely slow
                if directory.startswith("/mnt/") and len(directory) > 5 and directory[5].isalpha():
                    continue

                # Skip if directory doesn't exist or isn't accessible
                if not os.path.isdir(directory):
                    continue

                try:
                    # Use scandir for better performance
                    with os.scandir(directory) as entries:
                        for entry in entries:
                            try:
                                # On Windows, check by extension; on Unix, check executable bit
                                if sys.platform == "win32":
                                    if self._is_windows_executable(entry):
                                        commands.add(self._get_command_name(entry.name))
                                else:
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
        if text.startswith("/") and (len(words) == 0 or (len(words) == 1 and not text.endswith(" "))):
            prefix = text[1:]  # Remove the leading slash
            for cmd in current_commands:
                if cmd.startswith(prefix):
                    yield Completion(cmd, start_position=-len(prefix), display=f"/{cmd}", display_meta="Aye command")
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
    version = "1.0.7"  # Windows command completion support
    premium = "free"

    def init(self, cfg: Dict[str, Any]) -> None:
        """Initialize the completer plugin."""
        # Normalize debug from the incoming config BEFORE calling super().init,
        # because the base class may consume/mutate cfg.
        debug_val = cfg.get("debug")
        if isinstance(debug_val, str):
            debug_enabled = debug_val.lower() == "on"
        else:
            debug_enabled = bool(debug_val)

        super().init(cfg)

        # Base Plugin exposes `debug` as a read-only property; keep any internal
        # debug flag consistent if such a writable attribute exists.
        if hasattr(self, "_debug"):
            try:
                setattr(self, "_debug", debug_enabled)
            except Exception:
                pass

        # IMPORTANT: call through the module attribute so unittest.mock.patch
        # on "aye.plugins.completer.rprint" reliably intercepts it.
        if debug_enabled:
            sys.modules[__name__].rprint(f"[bold yellow]Initializing {self.name} v{self.version}[/]")

    def on_command(self, command_name: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Handle completion requests through the plugin system."""
        if command_name == "get_completer":
            commands = params.get("commands", [])
            project_root = params.get("project_root")
            completion_style = params.get("completion_style", "readline")

            # Create the command/path completer
            cmd_completer = CmdPathCompleter(commands)

            # Get the @file completer
            from .at_file_completer import AtFileCompleter
            from pathlib import Path

            at_completer = AtFileCompleter(project_root=Path(project_root) if project_root else None)

            # Create composite completer
            composite = CompositeCompleter(cmd_completer, at_completer)

            # Wrap with dynamic auto-complete behavior
            # Pass the completion_style so it knows when to auto-trigger
            dynamic_completer = DynamicAutoCompleteCompleter(composite, completion_style)

            return {"completer": dynamic_completer}
        return None
