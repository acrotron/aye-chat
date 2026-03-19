"""Streaming UI components for progressively displaying LLM responses."""
from __future__ import annotations

import os
import sys
import shutil
import threading
import time
from typing import Callable, Optional, Tuple

from rich import box
from rich.console import Console, ConsoleDimensions
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from aye.presenter.repl_ui import deep_ocean_theme


_STREAMING_THEME = deep_ocean_theme

# Debounce delay for resize events (seconds)
_RESIZE_DEBOUNCE_SEC = 0.15


def _get_env_float(env_var: str, default: float) -> float:
    try:
        return float(os.environ.get(env_var, str(default)) or str(default))
    except ValueError:
        return default


def _is_wsl() -> bool:
    if sys.platform != "linux":
        return False
    try:
        with open("/proc/version", "r", encoding="utf-8", errors="ignore") as handle:
            return "microsoft" in handle.read().lower()
    except OSError:
        return False


def _create_response_panel(
    content: str,
    *,
    use_markdown: bool,
    show_status: Optional[str] = None,
) -> Panel:
    pulse = "[ui.response_symbol.waves](([/] [ui.response_symbol.pulse]\u25cf[/] [ui.response_symbol.waves]))[/]"
    grid = Table.grid(padding=(0, 1))
    grid.add_column()
    grid.add_column()

    if use_markdown and content.strip():
        rendered = Markdown(content)
    else:
        rendered = Text(content)

    if show_status:
        status_text = Text(f"\n{show_status}", style="ui.stall_spinner")
        if isinstance(rendered, Text):
            rendered.append(status_text.plain, style=status_text.style)
        else:
            stack = Table.grid(padding=0)
            stack.add_column()
            stack.add_row(rendered)
            stack.add_row(status_text)
            rendered = stack

    grid.add_row(pulse, rendered)

    return Panel(
        grid,
        border_style="ui.border",
        box=box.ROUNDED,
        padding=(0, 1),
        expand=True,
    )


class StreamingResponseDisplay:
    """Manages a Rich Live panel for streaming LLM responses."""

    def __init__(
        self,
        *,
        console: Optional[Console] = None,
        viewport_height: Optional[int] = None,
        on_first_content: Optional[Callable[[], None]] = None,
    ) -> None:
        self._console = console or Console(theme=_STREAMING_THEME)
        self._stream_console: Optional[Console] = None
        self._live: Optional[Live] = None

        self._viewport_height = max(
            5, int(_get_env_float("AYE_STREAM_VIEWPORT_HEIGHT", viewport_height or 15))
        )
        self._on_first_content = on_first_content

        self._current_content: str = ""
        self._first_content_received = False
        self._started = False

        self._lock = threading.RLock()

        size = self._console.size
        self._last_terminal_size: Tuple[int, int] = (size.width, size.height)
        self._last_render_line_count = 0

        self._needs_manual_clear = sys.platform == "win32" or _is_wsl()

        # Resize debounce state
        self._resize_pending = False
        self._resize_pending_type: Optional[str] = None
        self._resize_last_event_time: float = 0.0
        self._pre_resize_size: Tuple[int, int] = (size.width, size.height)

        # Background monitor thread
        self._stop_monitor = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._started:
                return

            self._console.print()
            self._ensure_live()
            self._started = True

            # Start background monitor for debounced resize handling
            self._stop_monitor.clear()
            self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self._monitor_thread.start()

    def stop(self) -> None:
        # Signal monitor to stop
        self._stop_monitor.set()
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=0.5)
        self._monitor_thread = None

        with self._lock:
            if not self._started:
                return

            if self._live:
                self._live.stop()
                self._live = None

            self._started = False
            self._clear_for_final_print()

            text, use_markdown, _ = self._prepare_display_payload(prefer_markdown=True)
            panel = _create_response_panel(text, use_markdown=use_markdown)
            if text.strip():
                self._console.print(panel)
            self._console.print()

    # ------------------------------------------------------------------
    # Public API used by LLM invoker
    # ------------------------------------------------------------------
    def update(self, content: str, *, is_final: bool = False) -> None:
        if content is None:
            content = ""

        with self._lock:
            if not self._started:
                self.start()

            if content and not self._first_content_received:
                self._first_content_received = True
                if self._on_first_content:
                    self._on_first_content()

            if not is_final and content == self._current_content:
                return

            self._current_content = content
            self._refresh_display(prefer_markdown=is_final)

    def is_active(self) -> bool:
        with self._lock:
            return self._started

    def has_received_content(self) -> bool:
        with self._lock:
            return self._first_content_received

    @property
    def content(self) -> str:
        with self._lock:
            return self._current_content

    def __enter__(self) -> "StreamingResponseDisplay":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Internal rendering helpers
    # ------------------------------------------------------------------
    def _ensure_live(self) -> None:
        if self._live:
            return

        width = max(10, self._console.width - 2)
        self._stream_console = Console(theme=_STREAMING_THEME, width=width)
        placeholder = _create_response_panel("", use_markdown=False)
        self._live = Live(
            placeholder,
            console=self._stream_console,
            auto_refresh=False,
            transient=True,
            vertical_overflow="ellipsis",
        )
        self._live.start()
        self._last_render_line_count = 4

    def _refresh_display(self, *, prefer_markdown: bool) -> None:
        self._ensure_live()
        if not self._live:
            return

        # Check for resize and queue it (debounced)
        self._check_and_queue_resize()

        # If a resize is pending and not yet handled, skip expensive refresh
        if self._resize_pending:
            return

        text, use_markdown, line_count = self._prepare_display_payload(prefer_markdown)
        panel = _create_response_panel(text, use_markdown=use_markdown)
        self._live.update(panel, refresh=True)
        self._last_render_line_count = line_count

    def _prepare_display_payload(self, prefer_markdown: bool) -> Tuple[str, bool, int]:
        content = self._current_content or ""
        lines = content.splitlines() or [""]
        trimmed = False

        if len(lines) > self._viewport_height:
            trimmed = True
            lines = ["..."] + lines[-self._viewport_height :]

        text = "\n".join(lines)
        line_count = len(lines) + 4  # panel borders + padding
        line_count = max(line_count, 4)

        use_markdown = prefer_markdown and not trimmed
        return text, use_markdown, line_count

    # ------------------------------------------------------------------
    # Resize handling with debounce
    # ------------------------------------------------------------------
    def _check_and_queue_resize(self) -> None:
        """Detect resize and queue it for debounced handling."""
        if not self._console.is_terminal:
            return

        ts = shutil.get_terminal_size(fallback=self._last_terminal_size)
        width, height = ts.columns, ts.lines
        last_width, last_height = self._last_terminal_size

        if width == last_width and height == last_height:
            return

        # Capture pre-resize size if this is a new resize sequence
        if not self._resize_pending:
            self._pre_resize_size = self._last_terminal_size

        # Update current size
        self._last_terminal_size = (width, height)
        self._console._size = ConsoleDimensions(width, height)  # type: ignore[attr-defined]

        # Determine resize type based on what changed from the start of this resize sequence
        pre_width, _ = self._pre_resize_size
        if width != pre_width:
            # Width changed - this causes text reflow and ghost boxes
            resize_type = "width"
        else:
            # Height-only change - terminal handles scrolling, no ghost boxes
            resize_type = "height_only"

        # If we already have a pending "width" resize, keep it (most disruptive)
        if self._resize_pending_type == "width":
            pass  # keep width
        else:
            self._resize_pending_type = resize_type

        self._resize_pending = True
        self._resize_last_event_time = time.time()

        # Stop the live display immediately to prevent Rich from emitting
        # stale clear codes during the resize drag
        if self._live:
            self._live.stop()
            self._live = None

    def _monitor_loop(self) -> None:
        """Background thread that handles debounced resize completion."""
        while not self._stop_monitor.is_set():
            if self._stop_monitor.wait(0.05):
                break

            with self._lock:
                if not self._resize_pending:
                    continue

                elapsed = time.time() - self._resize_last_event_time
                if elapsed < _RESIZE_DEBOUNCE_SEC:
                    continue

                # Debounce period expired - handle the resize
                self._finalize_resize()

    def _finalize_resize(self) -> None:
        """Called after debounce expires to apply the appropriate clear strategy."""
        resize_type = self._resize_pending_type

        # Reset pending state
        self._resize_pending = False
        self._resize_pending_type = None
        self._pre_resize_size = self._last_terminal_size

        # Apply the appropriate clear strategy
        if resize_type == "width":
            # Width change: full clear because terminal text reflow makes
            # line positions unpredictable and causes ghost boxes
            self._clear_width_resize_artifacts()
        # Height-only changes: NO clearing needed!
        # The terminal handles vertical scrolling itself. The cursor stays
        # at the same position relative to the streaming box. Clearing here
        # would erase content above the box that was already printed.

        # Recreate the Live display and refresh
        self._ensure_live()
        if self._started and self._current_content:
            text, use_markdown, line_count = self._prepare_display_payload(prefer_markdown=False)
            panel = _create_response_panel(text, use_markdown=use_markdown)
            if self._live:
                self._live.update(panel, refresh=True)
            self._last_render_line_count = line_count

    def _clear_width_resize_artifacts(self) -> None:
        """Clear ghost box artifacts after a width change.

        Width changes cause terminal text reflow - existing lines wrap differently
        at the new width, making cursor positions unpredictable. We need to
        move up to where the old panel started and erase everything below.
        """
        if not self._console.is_terminal:
            return
        if not self._needs_manual_clear and not sys.stdout.isatty():
            return

        lines = max(self._last_render_line_count, self._viewport_height + 4)
        if lines <= 0:
            return

        sys.stdout.write(f"\033[{lines}A")  # Move cursor up
        sys.stdout.write("\033[J")           # Erase from cursor to end of screen
        sys.stdout.flush()

    def _clear_for_final_print(self) -> None:
        """Clear the transient Live region before printing the final panel.

        This is called only at stop() to ensure the final panel prints cleanly.
        """
        if not self._console.is_terminal:
            return
        if not self._needs_manual_clear and not sys.stdout.isatty():
            return

        lines = max(self._last_render_line_count, self._viewport_height + 4)
        if lines <= 0:
            return

        sys.stdout.write(f"\033[{lines}A")
        sys.stdout.write("\033[J")
        sys.stdout.flush()


def create_streaming_callback(display: StreamingResponseDisplay) -> Callable[[str, bool], None]:
    """Create a callback compatible with cli_invoke's streaming hook."""

    def callback(content: str, is_final: bool = False) -> None:
        display.update(content, is_final=is_final)

    return callback
