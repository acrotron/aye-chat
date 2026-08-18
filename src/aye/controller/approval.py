"""Interactive confirmation and permission toggling for shell tool calls.

In ``default`` permission mode, ``bash``/``cmd`` calls must be approved by the
user before they run. The request is shown inside a styled panel (matching the
chat frame) and confirmed with a single keypress: Enter runs, Esc or Ctrl+C
skips. Shift+Tab (bound in the REPL) toggles between the ``default`` and
``full`` permission modes live and persists the choice.
"""

import os
from typing import Callable, Optional

from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from rich import box, print as rprint
from rich.console import Console
from rich.panel import Panel

from aye.model.auth import set_user_config
from aye.model.tools import (
    PERMISSION_DEFAULT,
    PERMISSION_FULL,
    PERMISSION_KEY,
    permission_mode,
)


def build_confirm_panel(command: str) -> Panel:
    """Build the framed panel asking the user to approve *command*."""
    body = (
        f"[yellow]Model wants to run:[/]\n"
        f"[bold white]{command}[/]\n\n"
        f"[green]Enter[/] to run  \u00b7  [red]Esc[/] to skip"
    )
    return Panel(
        body,
        title=" Shell request ",
        border_style="cyan",
        box=box.ROUNDED,
    )


def _build_confirm_bindings() -> KeyBindings:
    """Key bindings for the approval screen: Enter runs, Esc/Ctrl+C skips."""
    kb = KeyBindings()

    @kb.add("enter")
    def approve(event):
        event.app.exit(result=True)

    @kb.add("escape")
    def decline(event):
        event.app.exit(result=False)

    @kb.add("c-c")
    def cancel(event):
        event.app.exit(result=False)

    return kb


def _build_key_application() -> Application:
    """Invisible-keypress application: renders nothing, exits on a bound key."""
    layout = Layout(container=Window(FormattedTextControl(""), height=1))
    return Application(
        full_screen=False,
        layout=layout,
        key_bindings=_build_confirm_bindings(),
    )


def _default_read_key() -> bool:
    """Read a single key without rendering an input line."""
    return _build_key_application().run() is True


def confirm_command(
    command: str,
    *,
    console: Optional[Console] = None,
    read_key: Optional[Callable[[], bool]] = None,
) -> bool:
    """Ask the user to approve running *command*.

    Args:
        command: The exact command line the model wants to run.
        console: Optional rich console for the panel (defaults to a new one).
        read_key: Optional key reader override (used by tests).

    Returns:
        True if the user pressed Enter, False if they pressed Esc or Ctrl+C.
    """
    console = console if console is not None else Console()
    console.print(build_confirm_panel(command))

    reader = read_key if read_key is not None else _default_read_key
    try:
        return bool(reader())
    except KeyboardInterrupt:
        return False


def toggle_permission_mode() -> str:
    """Switch between ``default`` and ``full`` permission modes.

    Persists the new mode to the user config, and to the session environment
    as well so the change applies immediately even when the mode had been set
    through an environment variable.

    Returns:
        The new mode.
    """
    current = permission_mode()
    new_mode = PERMISSION_FULL if current == PERMISSION_DEFAULT else PERMISSION_DEFAULT
    os.environ["AYE_TOOL_PERMISSION"] = new_mode
    set_user_config(PERMISSION_KEY, new_mode)
    if new_mode == PERMISSION_FULL:
        rprint("[bold green]Permission: full[/] - shell commands run without asking")
    else:
        rprint(
            "[bold yellow]Permission: default[/] - "
            "shell commands ask first (Enter to run)"
        )
    return new_mode