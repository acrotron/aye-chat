"""Streaming UI components for displaying LLM responses progressively.

This module provides the StreamingResponseDisplay class which handles
real-time display of LLM responses in a styled Rich panel with
word-by-word animation and stall detection.
"""
from aye.presenter.repl_ui import deep_ocean_theme

import os
import sys
import time
import threading
import math
from typing import Optional, Callable

from rich import box
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme


# Instead of manually trying to make the theme consistent I just directly used the theme from repl_ui.py
_STREAMING_THEME = deep_ocean_theme


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
        self._stream_console: Optional[Console] = None
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
        self._viewport_height = int(_get_env_float("AYE_STREAM_VIEWPORT_HEIGHT", 15.0))

        # Synchronization: Live + internal state are touched by monitor thread
        # and by whichever thread calls update(). We must serialize them.
        self._lock = threading.RLock()

        # Stall detection state
        self._last_receive_time: float = 0.0
        self._is_animating: bool = False
        self._showing_stall_indicator: bool = False

        # OS and Resize detection state
        self._last_console_size = self._console.size
        self._resize_old_size = self._console.size
        self._is_resizing: bool = False
        self._last_resize_time: float = 0.0

        self._stall_monitor_thread: Optional[threading.Thread] = None
        self._stop_monitoring = threading.Event()

    def _detect_resize(self) -> bool:
        """Compares console.size against last known dimensions."""
        current_size = self._console.size
        if current_size != self._last_console_size:
            if not self._is_resizing:
                # Capture the original size before the resize drag started
                self._resize_old_size = self._last_console_size
            self._last_console_size = current_size
            return True
        return False

    def _handle_resize_start(self) -> None:
        """Immediate stop - calls live.stop() to erase the transient region without printing."""
        self._is_resizing = True
        self._last_resize_time = time.time()
        if self._live:
            if self._stream_console:
                # Silence the console to prevent Rich from printing inaccurate clear codes during resize
                self._stream_console.quiet = True
            self._live.stop()
            self._live = None

    def _clear_resize_artifacts(self) -> None:
        """Calculates offset and clears ghost boxes after resize finishes."""
        if self._console.is_terminal:
            old_width = self._resize_old_size.width
            new_width = self._last_console_size.width
            estimated_wraps = 0
            ansi_sent = ""
            
            if new_width > 0 and old_width > 0 and old_width != new_width:
                # Calculate exactly how many lines the text took inside the old panel
                old_text_width = max(1, old_width - 8)
                explicit_lines = self._animated_content.split('\n')
                old_panel_content_lines = sum(
                    math.ceil(len(line) / old_text_width) if len(line) > 0 else 1 
                    for line in explicit_lines
                )
                
                if self._showing_stall_indicator:
                    old_panel_content_lines += 1
                    
                # Panel borders (top and bottom) = 2 lines
                old_panel_height = old_panel_content_lines + 2
                
                # Calculate how much the old panel wrapped when the terminal shrank
                old_panel_width = max(10, old_width - 2)
                if old_width > new_width:
                    wrap_multiplier = math.ceil(old_panel_width / new_width)
                    ghost_box_height = old_panel_height * wrap_multiplier
                else:
                    # Terminal widens. Explicit newlines prevent unwrapping.
                    ghost_box_height = old_panel_height
                
                # Move cursor UP to the top of the ghost box
                term_height = self._console.height
                estimated_wraps = min(ghost_box_height, term_height - 2)
                
                if estimated_wraps > 0:
                    ansi_sent = f"\033[{estimated_wraps}A"
                    sys.stdout.write(f"\033[{estimated_wraps}A")
                    
            sys.stdout.write("\033[J")
            ansi_sent += "\033[J"
            sys.stdout.flush()
            
            try:
                with open("resize_debug.log", "a") as f:
                    f.write(f"Resize Guess: old_w={old_width}, new_w={new_width}, estimated_wraps={estimated_wraps}, ansi={ansi_sent}\n")
            except Exception:
                pass

    def _ensure_live(self) -> None:
        """Fresh Live creation - creates a new instance after cooldown."""
        if self._live is not None:
            return

        # Set UI window to a specific size relative to the terminal window
        fixed_width = max(10, self._console.width - 2)
        self._stream_console = Console(theme=_STREAMING_THEME, width=fixed_width)

        self._live = Live(
            _create_response_panel(self._animated_content, use_markdown=False, show_stall_indicator=self._showing_stall_indicator),
            console=self._stream_console,
            auto_refresh=False,  # Disable background thread to prevent race conditions during resize
            transient=True,
            vertical_overflow="ellipsis"  # Prevent scrolling artifacts if height exceeded
        )
        self._live.start()

    def _refresh_display(self, use_markdown: bool = False, show_stall: bool = False) -> None:
        """Refresh the live display with current animated content."""
        with self._lock:
            if not self._live:
                return

            if self._console.is_terminal:
                if self._detect_resize():
                    self._handle_resize_start()

            if self._is_resizing:
                return

            content_to_display = self._animated_content
            
            # Calculate safe viewport height to prevent terminal scrolling artifacts
            max_lines = self._viewport_height
            term_height = self._console.height
            
            safe_term_lines = max(4, term_height - 6)
            if max_lines <= 0:
                max_lines = safe_term_lines
            else:
                max_lines = min(max_lines, safe_term_lines)
            
            lines = content_to_display.split('\n')
            if len(lines) > max_lines:
                content_to_display = "...\n" + "\n".join(lines[-max_lines:])
                # Disable markdown if truncated to prevent unclosed formatting tags
                use_markdown = False

            self._live.update(
                _create_response_panel(
                    content_to_display,
                    use_markdown=use_markdown,
                    show_stall_indicator=show_stall,
                ),
                refresh=True
            )
            self._showing_stall_indicator = show_stall

    def start(self) -> None:
        """Start the live display."""
        with self._lock:
            if self._started:
                return

            self._console.print()  # spacing before panel

            self._ensure_live()

            self._started = True
            self._last_receive_time = time.time()

            # Start stall monitoring thread
            self._stop_monitoring.clear()
            self._stall_monitor_thread = threading.Thread(target=self._monitor_stall, daemon=True)
            self._stall_monitor_thread.start()

    def _monitor_stall(self) -> None:
        """Monitor for stalls and terminal resizes."""
        while not self._stop_monitoring.is_set():
            if self._stop_monitoring.wait(0.1):
                break

            with self._lock:
                if not self._started:
                    continue

                if self._detect_resize():
                    self._handle_resize_start()

                if self._is_resizing:
                    if time.time() - self._last_resize_time >= 0.5:
                        self._is_resizing = False
                        self._clear_resize_artifacts()
                        self._ensure_live()
                        self._refresh_display(
                            use_markdown=False,
                            show_stall=self._showing_stall_indicator
                        )
                    else:
                        continue

                if not self._live:
                    continue

                if not self._animated_content:
                    continue

                caught_up = (not self._is_animating) and (self._animated_content == self._current_content)
                if not caught_up:
                    continue

                time_since_receive = time.time() - self._last_receive_time
                should_show_stall = time_since_receive >= self._stall_threshold

                # Only redraw when the stall state changes.
                if should_show_stall != self._showing_stall_indicator:
                    self._refresh_display(use_markdown=False, show_stall=should_show_stall)

    def _animate_words(self, new_text: str) -> None:
        """Animate new text word by word."""
        if not new_text:
            return

        with self._lock:
            if not self._started:
                return
            self._is_animating = True

        try:
            i = 0
            n = len(new_text)

            while i < n:
                with self._lock:
                    is_resizing = self._is_resizing
                
                if is_resizing:
                    time.sleep(0.1)
                    continue

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
        """Update the displayed content."""
        with self._lock:
            if not is_final and content == self._current_content:
                return

            self._last_receive_time = time.time()

            if not self._first_content_received:
                self._first_content_received = True
                if self._on_first_content:
                    self._on_first_content()

        if not self._started:
            self.start()

        new_text = ""

        with self._lock:
            stall_was_showing = self._showing_stall_indicator

            if is_final:
                self._current_content = content
                self._animated_content = content
            else:
                if content.startswith(self._current_content):
                    new_text = content[len(self._current_content):]
                else:
                    self._animated_content = ""
                    new_text = content

                self._current_content = content

        if stall_was_showing:
            self._refresh_display(use_markdown=False, show_stall=False)

        if is_final:
            self._refresh_display(use_markdown=True, show_stall=False)
            return

        if new_text:
            self._animate_words(new_text)

    def stop(self) -> None:
        """Stop the live display."""
        # Wait for any active resizing to settle before stopping
        # Add a safety timeout of 3 seconds to prevent infinite hangs
        wait_start = time.time()
        while time.time() - wait_start < 3.0:
            with self._lock:
                if not self._is_resizing:
                    break
            time.sleep(0.1)

        self._stop_monitoring.set()
        if self._stall_monitor_thread and self._stall_monitor_thread.is_alive():
            self._stall_monitor_thread.join(timeout=1.0)
        self._stall_monitor_thread = None

        with self._lock:
            live = self._live
            self._is_resizing = False
            was_started = self._started

        if was_started:
            if live and self._animated_content:
                self._refresh_display(use_markdown=True, show_stall=False)

            with self._lock:
                if self._live:
                    self._live.stop()
                    self._live = None
                self._started = False

            # 4. Print the final panel using the standard dynamic console
            if self._current_content:
                final_panel = _create_response_panel(self._current_content, use_markdown=True, show_stall_indicator=False)
                self._console.print(final_panel)

            self._console.print()  # spacing after panel

    def is_active(self) -> bool:
        return self._started

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
