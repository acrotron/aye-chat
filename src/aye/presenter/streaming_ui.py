"""Streaming UI components for displaying LLM responses progressively.

This module provides the StreamingResponseDisplay class which handles
real-time display of LLM responses in a styled Rich panel with
word-by-word animation and stall detection.
"""

import os
import time
import threading
from typing import Optional, Callable

from rich import box
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme


# Theme matching repl_ui.py for consistent styling
_STREAMING_THEME = Theme({
    "ui.response_symbol.name": "bold cornflower_blue",
    "ui.response_symbol.waves": "steel_blue",
    "ui.response_symbol.pulse": "bold pale_turquoise1",
    "ui.border": "dim slate_blue3",
    "ui.stall_spinner": "dim yellow",
})


def _get_env_float(env_var: str, default: float) -> float:
    """Get a float value from environment variable with fallback default."""
    try:
        return float(os.environ.get(env_var, str(default)) or str(default))
    except ValueError:
        return default


def _create_response_panel(content: str, use_markdown: bool = True, show_stall_indicator: bool = False) -> Panel:
    """Create a styled response panel matching the final response display."""
    # Decorative "sonar pulse" marker
    pulse = "[ui.response_symbol.waves](([/] [ui.response_symbol.pulse]●[/] [ui.response_symbol.waves]))[/]"

    # A 2-column grid: marker + content
    grid = Table.grid(padding=(0, 1))
    grid.add_column()
    grid.add_column()

    # Use Markdown for proper formatting, or Text for mid-animation
    if use_markdown and content:
        rendered_content = Markdown(content)
    else:
        rendered_content = Text(content) if content else Text("")

    # Add stall indicator if needed
    if show_stall_indicator:
        stall_text = Text("\n⋯ waiting for more", style="ui.stall_spinner")
        if isinstance(rendered_content, Markdown):
            # For markdown, we need to convert to a container that can hold both
            container = Table.grid(padding=0)
            container.add_column()
            container.add_row(rendered_content)
            container.add_row(stall_text)
            rendered_content = container
        else:
            # For Text, we can append directly
            rendered_content.append("\n⋯ waiting for more", style="ui.stall_spinner")

    grid.add_row(pulse, rendered_content)

    # Wrap in a rounded panel
    return Panel(
        grid,
        border_style="ui.border",
        box=box.ROUNDED,
        padding=(0, 1),
        expand=True,
    )


class StreamingResponseDisplay:
    """Manages a live-updating Rich panel for streaming LLM responses."""

    def __init__(
        self,
        console: Optional[Console] = None,
        word_delay: Optional[float] = 0.20,
        stall_threshold: Optional[float] = 3.0,
        on_first_content: Optional[Callable[[], None]] = None,
    ):
        self._console = console or Console(theme=_STREAMING_THEME)
        self._live: Optional[Live] = None

        self._current_content: str = ""  # Full content received so far
        self._animated_content: str = ""  # Content that has been animated

        self._started: bool = False
        self._first_content_received: bool = False
        self._on_first_content = on_first_content

        # Configuration with env var fallbacks
        self._word_delay = word_delay if word_delay is not None else _get_env_float("AYE_STREAM_WORD_DELAY", 0.20)
        self._stall_threshold = (
            stall_threshold if stall_threshold is not None else _get_env_float("AYE_STREAM_STALL_THRESHOLD", 3.0)
        )

        # Synchronization: Live + internal state are touched by monitor thread
        # and by whichever thread calls update(). We must serialize them.
        self._lock = threading.RLock()

        # Stall detection state
        # NOTE: this must track *when we last received new content from the stream*,
        # not when we last refreshed the UI. If we update this timestamp when we draw
        # the stall indicator, the indicator will blink on/off.
        self._last_receive_time: float = 0.0
        self._is_animating: bool = False
        self._showing_stall_indicator: bool = False

        self._stall_monitor_thread: Optional[threading.Thread] = None
        self._stop_monitoring = threading.Event()

    def _refresh_display(self, use_markdown: bool = False, show_stall: bool = False) -> None:
        """Refresh the live display with current animated content."""
        with self._lock:
            if not self._live:
                return

            self._live.update(
                _create_response_panel(
                    self._animated_content,
                    use_markdown=use_markdown,
                    show_stall_indicator=show_stall,
                )
            )
            self._showing_stall_indicator = show_stall

    def start(self) -> None:
        """Start the live display."""
        with self._lock:
            if self._started:
                return

            self._console.print()  # spacing before panel
            self._live = Live(
                _create_response_panel("", use_markdown=False),
                console=self._console,
                refresh_per_second=30,
                transient=False,
            )
            self._live.start()
            self._started = True
            self._last_receive_time = time.time()

            # Start stall monitoring thread
            self._stop_monitoring.clear()
            self._stall_monitor_thread = threading.Thread(target=self._monitor_stall, daemon=True)
            self._stall_monitor_thread.start()

    def _monitor_stall(self) -> None:
        """Monitor for stalls.

        A true stall is:
        - we are not animating
        - AND the animated output has caught up to the received content
        - AND we have not received new stream content for >= stall_threshold

        Important: do NOT use a timestamp that is updated when the stall indicator is rendered,
        otherwise the stall indicator will blink (it resets its own timer).
        """
        while not self._stop_monitoring.is_set():
            if self._stop_monitoring.wait(0.5):
                break

            with self._lock:
                if not self._started or not self._animated_content:
                    continue

                caught_up = (not self._is_animating) and (self._animated_content == self._current_content)
                if not caught_up:
                    # If we were showing stall but new content is now pending/animating,
                    # the animation path will refresh with show_stall=False.
                    continue

                time_since_receive = time.time() - self._last_receive_time
                should_show_stall = time_since_receive >= self._stall_threshold

                # Only redraw when the stall state changes.
                if should_show_stall != self._showing_stall_indicator:
                    self._live.update(
                        _create_response_panel(
                            self._animated_content,
                            use_markdown=False,
                            show_stall_indicator=should_show_stall,
                        )
                    )
                    self._showing_stall_indicator = should_show_stall

    def _animate_words(self, new_text: str) -> None:
        """Animate new text word by word."""
        if not new_text:
            return

        with self._lock:
            if not self._live:
                return
            self._is_animating = True

        try:
            i = 0
            n = len(new_text)

            while i < n:
                char = new_text[i]

                if char in "\n\r":
                    with self._lock:
                        self._animated_content += char
                    i += 1
                    self._refresh_display(use_markdown=True, show_stall=False)

                elif char in " \t":
                    ws_start = i
                    while i < n and new_text[i] in " \t":
                        i += 1
                    with self._lock:
                        self._animated_content += new_text[ws_start:i]
                    self._refresh_display(use_markdown=False, show_stall=False)

                else:
                    word_start = i
                    while i < n and new_text[i] not in " \t\n\r":
                        i += 1
                    with self._lock:
                        self._animated_content += new_text[word_start:i]
                    self._refresh_display(use_markdown=False, show_stall=False)

                    if self._word_delay > 0:
                        time.sleep(self._word_delay)

        finally:
            with self._lock:
                self._is_animating = False

    def update(self, content: str, is_final: bool = False) -> None:
        """Update the displayed content.

        By default, updates are animated word-by-word.
        If `is_final=True`, animation is skipped and the content is rendered
        immediately (Markdown), so the UI snaps to the final response as soon
        as it is ready.

        Args:
            content: The full content to display (not a delta).
            is_final: If True, stop animating and render final content immediately.
        """
        with self._lock:
            # For finalization, we must still run even if content matches.
            if not is_final and content == self._current_content:
                return

            # This is the key timestamp for stall detection:
            # it should only change when new stream content arrives.
            self._last_receive_time = time.time()

            # Fire the on_first_content callback before starting the display
            if not self._first_content_received:
                self._first_content_received = True
                if self._on_first_content:
                    self._on_first_content()

        # Auto-start if not started
        if not self._started:
            self.start()

        new_text = ""

        # Decide how to update state under lock
        with self._lock:
            stall_was_showing = self._showing_stall_indicator

            if is_final:
                # Immediately snap to the final content (no word-by-word delays).
                self._current_content = content
                self._animated_content = content
            else:
                if content.startswith(self._current_content):
                    new_text = content[len(self._current_content):]
                else:
                    self._animated_content = ""
                    new_text = content

                self._current_content = content

        # If stall indicator is currently shown, hide it immediately.
        # (Otherwise it would stay visible until the first animated refresh.)
        if stall_was_showing:
            self._refresh_display(use_markdown=False, show_stall=False)

        # Final render: no animation.
        if is_final:
            self._refresh_display(use_markdown=True, show_stall=False)
            return

        # Streaming render: animate only the delta.
        if new_text:
            self._animate_words(new_text)

    def stop(self) -> None:
        """Stop the live display."""
        # Stop the monitoring thread
        self._stop_monitoring.set()
        if self._stall_monitor_thread and self._stall_monitor_thread.is_alive():
            self._stall_monitor_thread.join(timeout=1.0)
        self._stall_monitor_thread = None

        with self._lock:
            live = self._live

        if live:
            # Final update with full markdown rendering
            if self._animated_content:
                self._refresh_display(use_markdown=True, show_stall=False)

            with self._lock:
                live.stop()
                self._console.print()  # spacing after panel
                self._live = None
                self._started = False

    def is_active(self) -> bool:
        return self._started and self._live is not None

    def has_received_content(self) -> bool:
        return self._first_content_received

    @property
    def content(self) -> str:
        return self._current_content

    def __enter__(self) -> "StreamingResponseDisplay":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()


def create_streaming_callback(display: StreamingResponseDisplay):
    """Create a callback function for use with cli_invoke."""

    def callback(content: str, is_final: bool = False) -> None:
        display.update(content, is_final=is_final)

    return callback
